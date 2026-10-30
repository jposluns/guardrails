#!/usr/bin/env python3
"""The one reading of a brief's INPUT declarations, shared by the brief lint's P6
(tools/check_brief_capability.py) and the delivery check's C1 (tools/check_verifier_delivery.py), so the
two can never disagree on what a brief declares.

TEXT GATE (forbidden). Only LF ends a line here. A brief holding any other line separator anywhere (a
carriage return, bare or in CRLF, U+0085 NEXT LINE, U+2028 LINE SEPARATOR, U+2029 PARAGRAPH SEPARATOR),
or any format character (Unicode category Cf: a byte order mark, a zero-width or bidirectional control,
a soft hyphen) outside a fenced code block, is refused by the caller before any rule runs: a separator
this reading does not split on can hide a declaration from it, and a format character is invisible and
can split or disguise a label or a path. Nothing is removed silently. A format character inside a
fenced block's body is not refused here (a fence is literal text this module never reads).

FENCES. A fence opens on a line of optional spaces or tabs and a run of three or more backticks or
tildes (a backtick fence whose info string holds a backtick opens nothing), and closes on a line whose
stripped text is a run of the same character at least as long with nothing after it; an unclosed fence
runs to the text's end. Inside a fence NOTHING is a declaration and NOTHING is a look-alike: a fenced
INPUT line is not checked, so a brief may quote a GitHub Actions workflow (an indented `inputs:` key) or
an example INPUT line in a fence. The fence marker lines themselves are outside text.

DECLARATIONS (read). Outside fences, a declaration is exactly one canonical shape at the start of a
line: optional spaces or tabs, at most one bullet list marker (`-`, `*` or `+` and a space), then the
label INPUT: as written (upper case; **INPUT:** and **INPUT**: are the only markup forms kept; no other
prefix, numbered markers and blockquote markers included, is skipped), then one path, quoted with " or
` when it holds a space, then only whitespace. The path must be absolute, with no `..` component, no
control character and no character of INPUT_SHAPE_CHARACTERS (a <name> placeholder, $, ~, a glob or a
brace leaves the path it names outside the text); any other shape on a canonical-labelled line is a
malformed declaration (bad), never ignored.

LOOK-ALIKES. Any other line outside fences whose text, folded to NFKC and through a BOUNDED
confusable-to-ASCII table (CONFUSABLES: Greek and Cyrillic look-alikes of the letters I N P U T, and the
colon-like characters U+A789, U+2236, U+0589, U+05C3, U+02D0 and U+02F8; NFKC already folds the
fullwidth and small forms), holds the token INPUT or INPUTS (not inside a longer word) followed, past
backticks, emphasis, spaces and tabs, by a colon or a table pipe, in any case and ANYWHERE in the line,
is a malformed declaration (bad): `- [ ] INPUT:`, a bullet of another character, `1.INPUT:`,
`<b>INPUT:</b>`, `| INPUT | path |`, `Step 1 - INPUT:`, `Input:`, `INPUTS:`, a fullwidth colon and a
Cyrillic I are all refused, never read past. BOUNDS, named: the table is bounded, not a full
confusables list, so an unlisted look-alike letter passes; a line where another word stands between
INPUT and the colon (`INPUT FILE: x`) declares nothing and is not a look-alike; and prose that holds
`INPUT:` or `INPUT |` anywhere outside a fence is refused even when it only discusses the label (quote
such prose in a fence).

This module reads text only: it opens no path and runs nothing. Callers: check_brief_capability
(decode_brief refuses what forbidden() finds, PreflightText reads read()), check_verifier_delivery
(_declared_inputs refuses both and takes read()'s declarations).
"""
import re
import unicodedata

INPUT_LABEL = "INPUT:"
# the only markup forms of the label that declare; everything else is a look-alike or plain text
INPUT_LABELS = (INPUT_LABEL, "**INPUT:**", "**INPUT**:")
# at most one bullet list marker before the label; numbered and blockquote markers are not skipped
LIST_MARKERS = ("- ", "* ", "+ ")
# a character that leaves the declared path outside the text: a <name> placeholder, a variable, a home
# path, a glob or a brace
INPUT_SHAPE_CHARACTERS = frozenset("<>$~*?[]{}")
# only LF ends a line: any other separator can hide a declaration from a line-based reading
SEPARATORS = {"\r": "U+000D CARRIAGE RETURN", "\x85": "U+0085 NEXT LINE",
              chr(0x2028): "U+2028 LINE SEPARATOR", chr(0x2029): "U+2029 PARAGRAPH SEPARATOR"}
SEPARATOR_RE = re.compile("[\r\x85" + chr(0x2028) + chr(0x2029) + "]")
# the bounded confusable fold (after NFKC): look-alikes of I N P U T and colon-like characters
CONFUSABLES = {
    "Ι": "I", "ι": "i", "І": "I", "і": "i", "Ӏ": "I", "ӏ": "i",
    "Ν": "N",
    "Ρ": "P", "ρ": "p", "Р": "P", "р": "p",
    "υ": "u",
    "Τ": "T", "τ": "t", "Т": "T", "т": "t",
    "꞉": ":", "∶": ":", "։": ":", "׃": ":", "ː": ":", "˸": ":",
}
# the look-alike scan: the token INPUT or INPUTS, then backticks, emphasis, spaces or tabs, then a colon
# or a table pipe, anywhere in the folded line
NEAR_INPUT_RE = re.compile(r"(?<![A-Za-z0-9_])INPUTS?+[`*_ \t]*+[:|]", re.I)
FENCE_OPEN_RE = re.compile(r"[ \t]*(`{3,}+|~{3,}+)(.*)\Z")

LOOK_ALIKE_REASON = ("the line holds a look-alike of the INPUT: label (another case or script, another "
                     "marker or markup, a colon-like character or a table cell) and is never ignored; "
                     "write INPUT: and one absolute path at the line's start, or quote the line in a fence")


def _fence_closes(line, opening):
    """Whether `line` closes a fence opened by the run `opening` (the same character, at least as many)."""
    stripped = line.lstrip(" \t")
    run = len(stripped) - len(stripped.lstrip(opening[0]))
    return run >= len(opening) and not stripped[run:].strip()


def _fence_opens(line):
    """The fence run `line` opens, or None (a backtick fence's info string holds no backtick)."""
    opened = FENCE_OPEN_RE.match(line)
    if opened and not (opened.group(1)[0] == "`" and "`" in opened.group(2)):
        return opened.group(1)
    return None


def outside_lines(text):
    """(line number, line) for each line of `text` outside a fenced block's body, the fence marker
    lines included, split at LF alone (forbidden() refuses every other separator first)."""
    fence = None
    for number, line in enumerate(text.split("\n"), 1):
        if fence is not None:
            if _fence_closes(line, fence):
                fence = None
                yield number, line
            continue
        opening = _fence_opens(line)
        if opening is not None:
            fence = opening
        yield number, line


def forbidden(text):
    """(line number, reason) for the first line separator other than LF anywhere in `text`, or the
    first format character (category Cf) outside a fenced block's body; None when there is neither."""
    found = SEPARATOR_RE.search(text)
    if found:
        line = text.count("\n", 0, found.start()) + 1
        return line, "the brief holds a line separator other than LF ({}); only LF ends a line".format(
            SEPARATORS[found.group(0)])
    for number, line in outside_lines(text):
        for character in line:
            if unicodedata.category(character) == "Cf":
                return number, ("the brief holds a format character (U+{:04X}) outside a fenced block; "
                                "format characters are invisible and are refused, never removed "
                                "silently".format(ord(character)))
    return None


def fold(line):
    """`line` folded for the look-alike scan: NFKC, then CONFUSABLES read as their ASCII forms."""
    if line.isascii():   # NFKC and the table leave ASCII as it is
        return line
    line = unicodedata.normalize("NFKC", line)
    return "".join(CONFUSABLES.get(character, character) for character in line)


def label_rest(line):
    """The text after the canonical INPUT label opening `line`, or None: optional spaces or tabs, at
    most one bullet list marker (LIST_MARKERS), then one label of INPUT_LABELS."""
    rest = line.lstrip(" \t")
    for marker in LIST_MARKERS:
        if rest.startswith(marker):
            rest = rest[len(marker):].lstrip(" \t")
            break
    for label in INPUT_LABELS:
        if rest.startswith(label):
            return rest[len(label):]
    return None


def input_shape(path):
    """(path, None) when a declared input names one absolute path, else (None, reason): no path, a
    character of INPUT_SHAPE_CHARACTERS, a control character, a relative path, or a `..` component."""
    if not path:
        return None, "an INPUT line with no path; write one absolute path after INPUT:"
    if any(character in INPUT_SHAPE_CHARACTERS for character in path):
        return None, ("the declared input {} holds a placeholder, variable, home, glob or brace character ({}), so "
                      "the path it names is not in the text; write the path itself".format(
                          path, " ".join(sorted(INPUT_SHAPE_CHARACTERS))))
    if any(unicodedata.category(character) == "Cc" for character in path):
        return None, "the declared input {!r} holds a control character".format(path)
    if not path.startswith("/"):
        return None, "the declared input {} is not an absolute path".format(path)
    if ".." in path.split("/"):
        return None, "the declared input {} holds a `..` component; write the path itself".format(path)
    return path, None


def declaration(line):
    """None when `line` opens with no canonical INPUT label (label_rest), else (path, None) for a
    declared path or (None, reason) for a malformed declaration. After the label comes one path, quoted
    with " or ` when it holds a space, then only whitespace (input_shape judges the path)."""
    rest = label_rest(line)
    if rest is None:
        return None
    rest = rest.lstrip()
    if rest[:1] in ('"', "`"):
        end = rest.find(rest[0], 1)
        if end < 0:
            return None, "the declared input's quote does not close on its line"
        path, tail = rest[1:end], rest[end + 1:]
    else:
        words = rest.split(None, 1)
        path, tail = (words[0] if words else ""), (words[1] if len(words) > 1 else "")
    if tail.strip():
        return None, ("a word follows the declared input {} (quote a path that holds a space, and write nothing "
                      "after it)".format(path))
    return input_shape(path)


def look_alike(line):
    """Whether a line that is no declaration holds a look-alike of the INPUT label anywhere: the line
    folded (fold), then NEAR_INPUT_RE searched over the whole of it."""
    return bool(NEAR_INPUT_RE.search(fold(line)))


def read(text):
    """(inputs, bad): [(line number, path)] for the declarations outside fences, in order, and
    [(line number, reason)] for every malformed declaration or look-alike outside fences. Inside a
    fenced block's body nothing is read."""
    inputs, bad = [], []
    for number, line in outside_lines(text):
        declared = declaration(line)
        if declared is None:
            if look_alike(line):
                bad.append((number, LOOK_ALIKE_REASON))
        elif declared[1] is not None:
            bad.append((number, declared[1]))
        else:
            inputs.append((number, declared[0]))
    return inputs, bad
