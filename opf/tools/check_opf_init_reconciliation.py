"""Structural gate for the OPF init later-slice obligation crosswalk (opf/spec/OPF-INIT-RECONCILIATION.md).

The opf-init-p0 gate residue in .aiqt/core/gates/manifest.toml lists the later OPF-INIT obligations it leaves
out of scope. This gate reads that list from the residue, requires it to equal the frozen roster below, and
then parses the crosswalk: every residue obligation has exactly one "## Obligation:" entry, no entry names an
obligation outside the roster, the Migrate/Move entry keeps each of its three "### Split:" children exactly
once, every entry carries an Owner and an Ownership, and an entry whose Disposition is "transferred" names as
its Successor a backlog id from the closed list in TODO.md at the repository root and an Owner other than
"none yet". It reads the crosswalk through a closed list of line shapes, each starting at the line start:
a blank line, an entry heading ("## Obligation: " or "### Split: " and a name), any other ATX heading (one to
six "#" and a space, a tab or the line end), a field line ("- Name: value"), an ordered item ("<digits>.
text"), a line indented with spaces exactly to the text of the item line above it or of the continuation
line above it, and prose. Inline text is read as CommonMark 0.31.2 reads it (the vendored marko's version):
a backslash escapes ASCII punctuation, a code span closes only on a backtick run of exactly its opening
length, and an inline comment is "<!-->", "<!--->" or "<!--" through the next "-->" on its line. A
backtick run that nothing on its line closes, on a line other than a heading, carries a code span onto the
next line when that line continues the paragraph (an indented line, prose, or an ordered item other than
"1. " with text, the one item a reader starts a list on below a paragraph), and is literal text when the next
line ends the paragraph. The text of a heading, ordered item, continuation or prose line must open with a
letter, a digit that starts no list marker, "(", a quote, a backtick or an option name such as --self-test;
it must not begin with "Obligation", "Split", "Owner", "Ownership", "Disposition" or "Successor", any
whitespace (a character str.isspace accepts: a space or tab, a line feed, carriage return or form feed a
reference decodes to, or a no-break or other Unicode space) and a colon, in any case, either once emphasis,
code and escape marks are removed or as a reader renders it (a character reference decoded as the vendored
marko decodes it: a decimal number of up to seven digits, a hexadecimal one of up to six or a name of up to
32 characters, ended by ";", read by html.unescape; an escaped character and a closed code span's content
kept literal), nor open with letters that could begin such a name (a prefix of one, or a whole one and
whitespace) followed, once rendered, by a character a reader shows with no glyph: a format character (Unicode
Cf, such as U+200B) or a default-ignorable code point (such as U+034F or U+3164). A character reference is
thus refused only where its decoded character completes such a name; "S&amp;P" and "Split&nbsp;decisions"
are read, and two spellings a reader renders alike are judged alike. A name spelled with a look-alike a
reader does show (a visible combining mark such as U+0301, a full-width colon) is read as that text, not as
the name; re.IGNORECASE also folds U+017F to "s" and U+0130 and U+0131 to "i", so a name spelled with one
of those is refused although a reader shows it differently. That line, and a field line, must hold no
raw HTML outside a code span; a field value is checked for nothing else, apart from the refusals below that
apply to every line. Any other line cannot evaluate (exit 2): a blockquote, a bullet item other than a field
line, an ordered item marked other than "<digits>. ", a table row, a setext underline or thematic break, an
HTML line, an indented line that continues no item, a fence or comment opened by a line indented with spaces
or tabs at any width (a backtick run with another backtick later on its line is inline code, not an opener),
text after the "-->" closing a comment that opens its line, a "<!--" that does not close on its line
(refused as raw HTML, although a reader shows it as literal text unless a later line of the paragraph closes
it), a code span carried into a line that does not close it, two backtick runs on one line that nothing on it
closes above a line that continues the paragraph (refused although a reader may render them, as this gate
carries one run), a backtick run left open above an ordered item that continues the paragraph (also inside a
list, where a reader may start a sibling item instead), an entry
heading whose name holds a character reference html.unescape decodes (even one a backslash escapes or a code
span holds, which a reader shows literally) or a character a reader shows with no glyph, and a carriage
return or other control character. An entry ends at the next heading that is not an entry heading
(a level 1 or 2 heading also ends the obligation a split sits under); prose and ordered items end no entry.
An Owner, Ownership, Disposition or Successor field line after that end and before the next entry, or before
the first entry, belongs to no entry and is refused. Lines inside a fenced code block, or inside an HTML
comment that opens its line, are not read, and the same holds for TODO.md table rows; such a comment ends on
the first line holding "-->", so "<!-->" and "<!--->" end it on their own line. An inline comment hides no
later line.
A field's value is read from its first line only. It checks STRUCTURE only: it never reads a cited symbol,
so a green run says nothing about whether any obligation is implemented or whether a named successor
accepted the work.

Exit 0 clean; exit 1 on a concrete finding (a crosswalk that parses to no usable entries is a finding, since
any text parses); exit 2 (cannot evaluate) when the manifest, the crosswalk or TODO.md is missing or is not
UTF-8, a crosswalk line is one this gate refuses (above), the manifest does not parse, the residue no longer
has the shape this gate reads, or TODO.md lists no backlog id (in a standalone opf/ copy TODO.md is absent,
so the successor check cannot evaluate and the gate never passes there). The --self-test is source-free
(in-memory fixtures only), so it also runs in a standalone opf/ copy with no manifest beside it; each
finding fixture must be red with every check active and green with only its own check removed, and each
refused line must exit 2 and pass with only the rules its vector names removed.
Run: python3 -I -B opf/tools/check_opf_init_reconciliation.py [--self-test].
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: check_opf_init_reconciliation.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import contextlib
import html
import io
import re
import tomllib
import unicodedata
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = Path(".aiqt") / "core" / "gates" / "manifest.toml"
CROSSWALK = Path("opf") / "spec" / "OPF-INIT-RECONCILIATION.md"
BACKLOG = Path("TODO.md")
RESIDUE_GATE = "opf-init-p0"

# The residue sentence the obligation list sits in, and the list's own end.
RESIDUE_LEAD = "remain separate obligations awaiting maintainer design review: "
RESIDUE_END = ";"

# The ten obligations the residue names, normalized (_norm). Verb wiring and the CLI verbs stay separate
# entries until a maintainer decides their overlap.
OBLIGATIONS = (
    "target and control binding",
    "adoption classifier",
    "coordination",
    "keep-apply",
    "verb wiring",
    "git staging",
    "publication",
    "finalization",
    "cli verbs",
    "migrate/move",
)
SPLIT_PARENT = "migrate/move"
SPLIT_CHILDREN = ("import disposition", "move disposition", "whole-store relocation")
DISPOSITIONS = ("open", "retained", "transferred", "superseded")
# Owner values that name nobody, compared after casefold and a trailing-dot strip.
NO_OWNER = ("", "none", "none yet")

_OBLIGATION_RE = re.compile(r"^## Obligation: (.+?)\s*$")
_SPLIT_RE = re.compile(r"^### Split: (.+?)\s*$")
_FIELD_RE = re.compile(r"^- ([A-Za-z ]+): ?(.*?)\s*$")
# The fields the checks below read; one outside an entry is refused, never attached to the entry above.
READ_FIELDS = ("owner", "ownership", "disposition", "successor")
# The crosswalk is read through a closed list of line shapes; any other line cannot evaluate. Besides the
# entry headings and field lines above: a blank line, an ATX heading at the line start, an ordered item
# "<digits>. text" at the line start, a line indented exactly to the text of the item line just above it (or
# of the line it continues), and plain prose at the line start.
_HEADING_RE = re.compile(r"^(#{1,6})(?:[ \t]+(.*?))?[ \t]*$")
_ORDERED_RE = re.compile(r"^(\d{1,9}\. )(.*)$")
# The text of a heading, item, continuation or prose line must open with a letter, a digit that does not start
# an ordered-list marker, "(", a quote, a backtick (a fence never reaches here), or one or two hyphens before a
# letter (an option name such as --self-test); every mark a reader can render as structure is outside this list.
_PLAIN_RE = re.compile(r"(?![0-9]{1,9}[.)](?:[ \t]|$))(?:[^\W_]|[(\"'`]|--?[^\W_])")
# Raw HTML, looked for outside code spans (_inline): a reader can render a tag as structure. An unclosed "<!--"
# is literal text to a reader, but it matches here too, so a comment that might run on into the next line of
# a paragraph is never read past.
_HTML_RE = re.compile(r"<[A-Za-z/!?]")
# Inline text as CommonMark 0.31.2 reads it, the version the vendored marko implements: a backslash escapes
# ASCII punctuation, a code span closes only on a backtick run of exactly its opening length, and an HTML
# comment is "<!-->", "<!--->", or "<!--" through the next "-->".
_ESCAPABLE = "!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~"
_SHORT_COMMENTS = ("<!-->", "<!--->")
# Text that names an entry or a read field, after emphasis, code and escape marks are removed: outside an entry
# heading or a field line it is a form this gate cannot read.
# Whitespace before the colon is any character str.isspace accepts (\s in a str pattern): the ASCII whitespace
# HTML shows as one space (tab, line feed, form feed, carriage return, space), which "&#10;" or "&NewLine;"
# decodes to, and the no-break and other Unicode spaces, which HTML does not collapse but a reader shows blank.
# A vertical tab is not HTML whitespace; html.unescape decodes its reference to nothing, and a literal one is a
# control character (_CONTROL_RE), so either spelling is refused.
_NAMES = ("obligation", "split", "owner", "ownership", "disposition", "successor")
_NAMES_RE = re.compile(r"(?:{})\s*:".format("|".join(_NAMES)), re.IGNORECASE)
_FORMATTING_RE = re.compile(r"[*_`\\~]")
_EMPHASIS = "*_~"
# A character reference as the vendored marko's renderer finds it (its Renderer._charref): a decimal number of up
# to seven digits, a hexadecimal one of up to six, or a name of up to 32 characters, ended by ";". It decodes
# each with html.unescape, which also reads a name that only begins with a legacy one ("&shyer;" is U+00AD and
# "er;"), and leaves an unknown name as it is.
_REFERENCE_RE = re.compile(r"&(?:#[0-9]{1,7};|#[xX][0-9a-fA-F]{1,6};|[^\t\n\f <&#;]{1,32};)")
# The Unicode Default_Ignorable_Code_Point property (Unicode Character Database, DerivedCoreProperties.txt),
# which the standard library does not expose: code points a reader shows with no glyph. Most are format
# characters (Cf); the rest include U+034F COMBINING GRAPHEME JOINER, the Mongolian free variation selectors,
# the Hangul fillers and the variation selectors. Inclusive ranges; a character in one is never read as text.
_DEFAULT_IGNORABLE = (
    (0x00AD, 0x00AD), (0x034F, 0x034F), (0x061C, 0x061C), (0x115F, 0x1160), (0x17B4, 0x17B5),
    (0x180B, 0x180F), (0x200B, 0x200F), (0x202A, 0x202E), (0x2060, 0x206F), (0x3164, 0x3164),
    (0xFE00, 0xFE0F), (0xFEFF, 0xFEFF), (0xFFA0, 0xFFA0), (0xFFF0, 0xFFF8), (0x1BCA0, 0x1BCA3),
    (0x1D173, 0x1D17A), (0xE0000, 0xE0FFF),
)
# A carriage return (a line ending to a Markdown reader, not to this gate's line split) or another control
# character other than a tab, including the Unicode line and paragraph separators.
_CONTROL_RE = re.compile("[\\x00-\\x08\\x0b-\\x1f\\x7f\\x85\\u2028\\u2029]")
_FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
# A comment hides lines only when "<!--" opens the line (an HTML block); elsewhere it is inline.
_COMMENT_OPEN_RE = re.compile(r"^ {0,3}<!--")
# A fence or comment opened by an indented line (spaces or tabs, any width) may sit inside a list item, where
# an unindented line closes it, or open nothing at all; this gate reads neither case, so the crosswalk refuses
# the indented opener. A backtick run followed by a backtick on its line is inline code, not a fence.
_INDENTED_OPENER_RE = re.compile(r"^[ \t]+(?:`{3,}(?!.*`)|~{3,}|<!--)")
# A backlog table row in TODO.md: "| ID | Status | Item | Tags |", one id per row.
_BACKLOG_ROW_RE = re.compile(r"^\| ([A-Z0-9]+(?:-[A-Z0-9]+)*) \|")


class CannotEvaluate(Exception):
    pass


def _norm(name):
    name = " ".join(name.split()).casefold()
    if name.startswith("the "):
        name = name[4:]
    return name


def _parse_manifest(manifest_text):
    try:
        return tomllib.loads(manifest_text)
    except (ValueError, RecursionError, MemoryError) as exc:
        # ValueError: tomllib.TOMLDecodeError and an over-long integer literal; RecursionError: deep
        # nesting and (CPython 3.14.8 on) a 1001-part key; MemoryError: a many-part key under a memory
        # limit. Each is an unparseable manifest, so the gate cannot evaluate.
        raise CannotEvaluate("gates manifest does not parse: {}".format(exc)) from None


def _gate_tables(doc):
    """The manifest's gate array. Any other value (an integer, float, bool, date, time, string or table) is a
    manifest this gate cannot read, never a TypeError."""
    gates = doc.get("gate")
    if not isinstance(gates, list):
        raise CannotEvaluate("gates manifest has no gate array (gate is {})".format(type(gates).__name__))
    return gates


def residue_obligations(manifest_text):
    """The obligation names the opf-init-p0 residue lists, normalized and in order."""
    doc = _parse_manifest(manifest_text)
    gates = [g for g in _gate_tables(doc) if isinstance(g, dict) and g.get("id") == RESIDUE_GATE]
    if len(gates) != 1 or not isinstance(gates[0].get("residue"), str):
        raise CannotEvaluate("gates manifest has no single {} gate with a residue".format(RESIDUE_GATE))
    residue = gates[0]["residue"]
    if residue.count(RESIDUE_LEAD) != 1:
        raise CannotEvaluate("the {} residue no longer carries the obligation list lead".format(RESIDUE_GATE))
    tail = residue.split(RESIDUE_LEAD, 1)[1]
    if RESIDUE_END not in tail:
        raise CannotEvaluate("the {} residue obligation list has no end".format(RESIDUE_GATE))
    listed = tail.split(RESIDUE_END, 1)[0].rstrip(".")
    # str.split always gives at least one part, so an empty list shows as one empty name.
    parts = [p.strip() for p in listed.split(",")]
    if parts[-1].startswith("and "):
        parts[-1] = parts[-1][4:]
    names = [_norm(p) for p in parts]
    if any(not n for n in names):
        raise CannotEvaluate("the {} residue obligation list is empty or malformed".format(RESIDUE_GATE))
    return names


def backlog_ids(todo_text):
    """The closed successor list: the first cell of each row of every TODO.md table headed "| ID |", read
    through _visible_lines, so a row inside a fenced code block or an HTML comment names no id. A TODO.md
    that lists no id cannot evaluate, so an unreadable backlog never lets a transferred entry pass."""
    ids = set()
    in_table = False
    for _number, line, _bare in _visible_lines(todo_text):
        if line.startswith("| ID |"):
            in_table = True
            continue
        if not line.startswith("|"):
            in_table = False
            continue
        m = _BACKLOG_ROW_RE.match(line)
        if in_table and m:
            ids.add(m.group(1))
    if not ids:
        raise CannotEvaluate("{} lists no backlog id, so a transferred entry's successor cannot be "
                             "checked".format(BACKLOG))
    return frozenset(ids)


def _visible_lines(text, strict=False):
    """A Markdown text's lines minus fenced code blocks and HTML comments, so a heading, field or table row a
    reader does not see as one never counts. A fence closes on a line of the same character at least as
    long, and an unclosed fence hides the rest of the file. A backtick line whose info string holds a
    backtick (```x```) is inline code, not a fence opener (CommonMark). A comment that opens its line (up to
    three spaces, then "<!--") hides that line through the whole line holding its closing "-->", as an HTML
    block does: "<!-->" and "<!--->" hold their own "-->", so they end the block on their own line. An
    unclosed one hides the rest of the file. Any other line is read as inline text (_inline): its comments are
    removed and hide no later line. Yields (line number, line without its inline comments, that line without
    its code spans). With strict set (the crosswalk), raises CannotEvaluate on a control character
    (_CONTROL_RE), on a fence or comment opened by an indented line, on text after the "-->" that closes a
    line-opening comment (that text is part of an HTML block a reader renders), and on a code span it cannot
    bound: one carried from the line above that this line does not close, two runs on one line that nothing
    on it closes above a line that continues its paragraph, or a run left open above a "<digits>. " line that
    continues the paragraph (any marker but "1. " with text after it, which a reader starts a list on). A run
    that nothing on its line closes, on a line other than a heading, carries into the next line when that
    line is indented or prose, and is literal text otherwise."""
    if strict:
        m = _CONTROL_RE.search(text)
        if m:
            number = text.count("\n", 0, m.start()) + 1
            raise _unread(number, text.split("\n")[number - 1], "a carriage return or other control character")
    fence = None
    in_comment = False
    lines = text.split("\n")
    # carried: the length of a backtick run left open by the line above, whose paragraph this line continues.
    carried = 0
    for number, line in enumerate(lines, 1):
        carry, carried = carried, 0
        if in_comment:
            rest = _block_comment_rest(line, False)
            in_comment = rest is None
            if strict and not in_comment:
                _refuse_remainder(number, line, rest)
            continue
        m = _FENCE_RE.match(line)
        if fence is not None:
            if m and m.group(1)[0] == fence[0] and len(m.group(1)) >= len(fence) and not m.group(2).strip():
                fence = None
            continue
        if strict and _INDENTED_OPENER_RE.match(line):
            raise _unread(number, line, "a fence or comment opened by an indented line")
        if m and m.group(1)[0] == "`" and "`" in m.group(2):
            m = None
        if m:
            fence = m.group(1)
            continue
        if _COMMENT_OPEN_RE.match(line):
            rest = _block_comment_rest(line, True)
            in_comment = rest is None
            if strict and not in_comment:
                _refuse_remainder(number, line, rest)
            continue
        visible, bare, unclosed = _inline(line, carry)
        if strict and unclosed is None:
            _refuse_unbounded(number, line, "a code span open from the line above does not close on this line")
        elif strict and unclosed and _holds_paragraph(line):
            following = lines[number] if number < len(lines) else ""
            ordered = _ORDERED_RE.match(following)
            if ordered and not _interrupts(ordered):
                _refuse_unbounded(number, line, "a backtick run left open above an ordered item that continues "
                                                "its paragraph")
            elif not ordered and following.strip() and (following[0] in " \t" or _PLAIN_RE.match(following)):
                if len(unclosed) > 1:
                    _refuse_runs(number, line)
                carried = unclosed[0]
        yield number, visible, bare


def _block_comment_rest(line, opener):
    """The text after the "-->" that ends an HTML comment block on this line, or None when the line does not
    end it. On the opening line the "-->" may share the opener's dashes ("<!-->", "<!--->"), as CommonMark's
    end condition is any "-->" on the line."""
    end = line.find("-->", line.find("<!--") + 2 if opener else 0)
    return None if end < 0 else line[end + 3:]


def _code_span_end(line, start, length):
    """The end of the first backtick run of exactly `length` at or after `start`, or None: the close of a code
    span opened by a run of that length. A longer or shorter run closes nothing."""
    while True:
        begin = line.find("`", start)
        if begin < 0:
            return None
        start = begin
        while start < len(line) and line[start] == "`":
            start += 1
        if start - begin == length:
            return start


def _comment_end(line, start):
    """The end of the inline HTML comment opening at `start`, or None when it does not close on this line."""
    for short in _SHORT_COMMENTS:
        if line.startswith(short, start):
            return start + len(short)
    end = line.find("-->", start + 4)
    return None if end < 0 else end + 3


def _inline(line, carried=0):
    """(visible, bare, unclosed) for a line read left to right as CommonMark inline text: a backslash before
    ASCII punctuation escapes it (an escaped backtick opens nothing); a backtick run opens a code span that the
    next run of exactly its length closes, and its content is code (a "<!--" in it is no comment); a comment
    (_comment_end) is removed; an unclosed "<!--" stays as literal text. visible is the line without its
    comments, bare is visible without its code spans, and unclosed lists the length of each backtick run no
    run on the line closes (each is read as literal text here, which leaves more of the line in bare than a
    span closing on the next line would). With carried set, the line opens inside a code span of that length
    from the line above; when no run of that length closes it here, unclosed is None."""
    visible = []
    bare = []
    unclosed = []
    i = 0
    if carried:
        i = _code_span_end(line, 0, carried)
        if i is None:
            return line, "", None
        visible.append(line[:i])
    while i < len(line):
        if line[i] == "\\" and line[i + 1:i + 2] and line[i + 1] in _ESCAPABLE:
            visible.append(line[i:i + 2])
            bare.append(line[i:i + 2])
            i += 2
            continue
        if line[i] == "`":
            run = i
            while run < len(line) and line[run] == "`":
                run += 1
            end = _code_span_end(line, run, run - i)
            if end is None:
                unclosed.append(run - i)
                end = run
                bare.append(line[i:end])
            visible.append(line[i:end])
            i = end
            continue
        if line.startswith("<!--", i):
            end = _comment_end(line, i)
            if end is not None:
                i = end
                continue
        visible.append(line[i])
        bare.append(line[i])
        i += 1
    return "".join(visible), "".join(bare), unclosed


def _unread(number, line, reason):
    return CannotEvaluate("{} line {} is not a shape this gate reads ({}): {!r}".format(
        CROSSWALK, number, reason, line))


def _refuse_remainder(number, line, remainder):
    if remainder.strip():
        raise _unread(number, line, "text after the \"-->\" that closes a comment")


def _refuse_indented(number, line):
    raise _unread(number, line, "an indented line that does not continue the item line or line above it")


def _refuse_unbounded(number, line, reason):
    raise _unread(number, line, reason)


def _refuse_runs(number, line):
    raise _unread(number, line, "more than one backtick run that no run of its length closes, above a line "
                                "that continues the paragraph")


def _holds_paragraph(line):
    """True when a code span left open on `line` can run on into the next line: any line but a heading, which
    ends on its own line."""
    return not _HEADING_RE.match(line)


def _interrupts(ordered):
    """True when the ordered item `ordered` (an _ORDERED_RE match) starts a list below a paragraph line, so the
    paragraph ends above it: outside a list, the vendored marko starts one only on the marker "1. " with text
    after it. Any other ordered item is read here as continuing the paragraph, although inside a list marko
    ends the paragraph at any list item; a backtick run left open above such an item is refused, not read."""
    return ordered.group(1) == "1. " and bool(ordered.group(2).strip())


def _decoded(text):
    """`text` with each character reference (_REFERENCE_RE) replaced as the vendored marko renders it."""
    return _REFERENCE_RE.sub(lambda m: html.unescape(m.group()), text)


def _invisible(ch):
    """True when a reader shows `ch` with no glyph: a format character (Unicode category Cf, such as U+200B) or
    a default-ignorable code point (_DEFAULT_IGNORABLE, such as U+034F)."""
    if unicodedata.category(ch) == "Cf":
        return True
    point = ord(ch)
    return any(low <= point <= high for low, high in _DEFAULT_IGNORABLE)


def _hidden(text):
    """True when `text` holds a character reference html.unescape decodes (_decoded changes it, even where a
    backslash or a code span keeps it literal to a reader) or a character a reader shows with no glyph
    (_invisible): a reader may see a name this gate does not."""
    return _decoded(text) != text or any(_invisible(ch) for ch in text)


def _rendered(text):
    """`text` as a reader shows it, without its emphasis marks: character references decoded (_decoded), while
    a backslash-escaped character and the content of a code span that closes in `text` stay literal. An
    unclosed backtick run is dropped as a mark. Two spellings a reader shows alike render alike here."""
    out = []
    raw = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "\\" and text[i + 1:i + 2] and text[i + 1] in _ESCAPABLE:
            literal = text[i + 1]
            i += 2
        elif ch == "`":
            run = i
            while run < len(text) and text[run] == "`":
                run += 1
            end = _code_span_end(text, run, run - i)
            literal = "" if end is None else text[run:end - (run - i)]
            i = run if end is None else end
        else:
            if ch not in _EMPHASIS:
                raw.append(ch)
            i += 1
            continue
        out.append(_decoded("".join(raw)))
        out.append(literal)
        raw = []
    out.append(_decoded("".join(raw)))
    return "".join(out)


def _hidden_name(text):
    """True when `text` opens with ASCII letters and whitespace (str.isspace) that could begin an entry or read
    field name and its colon (a prefix of a name, or a whole name and whitespace), and then a character a
    reader shows with no glyph (_invisible). The caller passes the rendered text (_rendered), so such a
    character spelled as a character reference is judged as the character itself."""
    for i, ch in enumerate(text):
        if _invisible(ch):
            prefix = text[:i].casefold()
            return any(name.startswith(prefix) or prefix.rstrip() == name for name in _NAMES)
        if not ((ch.isascii() and ch.isalpha()) or ch.isspace()):
            return False
    return False


def _read_text(number, line, text, bare):
    """Refuses the text of a heading, item, continuation or prose line unless it is plain: it opens as
    _PLAIN_RE allows, its line holds no raw HTML outside a code span (bare, from _inline), and it does not name
    an entry or a read field, either once emphasis, code and escape marks are removed or as a reader renders
    it (_rendered), nor open with a name a character a reader shows with no glyph splits or follows. A
    character reference is judged by what it decodes to, so one that decodes to a letter, whitespace, a colon
    or such a character can complete a name, and others, such as "S&amp;P" or "Split&nbsp;decisions", are
    read."""
    if not _PLAIN_RE.match(text):
        raise _unread(number, line, "text that opens with a mark a reader can render as structure")
    if _HTML_RE.search(bare):
        raise _unread(number, line, "raw HTML, or a \"<!--\" that does not close on its line")
    rendered = _rendered(text).lstrip()
    if _NAMES_RE.match(_FORMATTING_RE.sub("", text).lstrip()) or _NAMES_RE.match(rendered):
        raise _unread(number, line, "an entry or read field named outside an entry heading or field line")
    if _hidden_name(rendered):
        raise _unread(number, line, "a character a reader shows with no glyph where a name would be")


def parse_crosswalk(text):
    """[(level, name, fields, parent)]: level is "obligation" or "split", fields a dict of the entry's
    "- Field: value" lines (a repeated field keeps every value), parent the enclosing obligation. Every
    visible line must be one of the closed list of shapes this gate reads (see _HEADING_RE); any other line
    raises CannotEvaluate. Any heading other than an entry heading ends the entry above it (a level 1 or 2
    heading also ends the obligation a split sits under), so a field below it attaches to no entry: a field
    this gate reads (READ_FIELDS) there, or before the first entry, is kept as a ("stray", field, {}, None)
    record for _check_stray to refuse. Prose and ordered items end no entry."""
    entries = []
    parent = None
    fields = None
    width = None
    for number, line, bare in _visible_lines(text, strict=True):
        # width: the indent a line needs to continue the item or continuation line just above it.
        item, width = width, None
        if not line.strip():
            continue
        m = _OBLIGATION_RE.match(line) or _SPLIT_RE.match(line)
        if m and _hidden(m.group(1)):
            raise _unread(number, line, "an entry name holding a character reference or a character a reader "
                                        "shows with no glyph")
        m = _OBLIGATION_RE.match(line)
        if m:
            parent = _norm(m.group(1))
            fields = {}
            entries.append(("obligation", parent, fields, None))
            continue
        m = _SPLIT_RE.match(line)
        if m:
            fields = {}
            entries.append(("split", _norm(m.group(1)), fields, parent))
            continue
        m = _HEADING_RE.match(line)
        if m:
            if m.group(2):
                _read_text(number, line, m.group(2), bare)
            fields = None
            if len(m.group(1)) <= 2:
                parent = None
            continue
        m = _FIELD_RE.match(line)
        if m:
            if _HTML_RE.search(bare):
                raise _unread(number, line, "raw HTML, or a \"<!--\" that does not close on its line")
            field = m.group(1).strip().casefold()
            if fields is not None:
                fields.setdefault(field, []).append(m.group(2))
            elif field in READ_FIELDS:
                entries.append(("stray", field, {}, None))
            width = 2
            continue
        m = _ORDERED_RE.match(line)
        if m:
            _read_text(number, line, m.group(2), bare)
            width = len(m.group(1))
            continue
        if line[0] in " \t":
            if item and line[:item] == " " * item and line[item] not in " \t":
                _read_text(number, line, line[item:], bare)
                width = item
            else:
                _refuse_indented(number, line)
            continue
        _read_text(number, line, line, bare)
    return entries


def _fielded(entries):
    """The obligation and split entries, without the stray records. A stray record has no fields, so only the
    checks that refuse an absent field need this filter."""
    return [e for e in entries if e[0] != "stray"]


def _check_missing(entries, roster, backlog):
    names = [e[1] for e in entries if e[0] == "obligation"]
    return ["missing: residue obligation {!r} has no entry".format(n) for n in roster if n not in names]


def _check_duplicate(entries, roster, backlog):
    names = [e[1] for e in entries if e[0] == "obligation"]
    return ["duplicate: obligation {!r} has {} entries".format(n, names.count(n))
            for n in sorted(set(names)) if names.count(n) > 1]


def _check_unknown(entries, roster, backlog):
    return ["unknown: obligation {!r} is not in the residue".format(e[1])
            for e in entries if e[0] == "obligation" and e[1] not in roster]


def _check_split(entries, roster, backlog):
    children = [e[1] for e in entries if e[0] == "split" and e[3] == SPLIT_PARENT]
    found = ["split-child-missing: {!r} has no {!r} split".format(SPLIT_PARENT, c)
             for c in SPLIT_CHILDREN if c not in children]
    found += ["split-child-duplicate: {!r} split {!r} appears {} times".format(SPLIT_PARENT, c, children.count(c))
              for c in SPLIT_CHILDREN if children.count(c) > 1]
    found += ["split-unknown: split {!r} under {!r} is not a declared child".format(e[1], e[3])
              for e in entries if e[0] == "split" and (e[3] != SPLIT_PARENT or e[1] not in SPLIT_CHILDREN)]
    return found


def _check_disposition(entries, roster, backlog):
    found = []
    for level, name, fields, _parent in _fielded(entries):
        values = fields.get("disposition", [])
        if len(values) != 1 or values[0] not in DISPOSITIONS:
            found.append("disposition: {} {!r} needs exactly one Disposition from {}".format(
                level, name, "/".join(DISPOSITIONS)))
    return found


def _check_successor(entries, roster, backlog):
    found = []
    for level, name, fields, _parent in entries:
        if fields.get("disposition") == ["transferred"]:
            successors = fields.get("successor", [])
            if len(successors) != 1 or successors[0] not in backlog:
                found.append("transferred-successor: {} {!r} is transferred but its Successor is not exactly "
                             "one backlog id from {}: {!r}".format(level, name, BACKLOG, successors))
    return found


def _check_owner(entries, roster, backlog):
    found = []
    for level, name, fields, _parent in _fielded(entries):
        for field in ("owner", "ownership"):
            values = fields.get(field, [])
            if len(values) != 1 or not values[0]:
                found.append("owner: {} {!r} needs exactly one nonempty {}".format(level, name, field.capitalize()))
    return found


def _check_transferred_owner(entries, roster, backlog):
    found = []
    for level, name, fields, _parent in entries:
        owners = fields.get("owner", [])
        if fields.get("disposition") == ["transferred"] and any(
                o.casefold().rstrip(".").strip() in NO_OWNER for o in owners):
            found.append("transferred-owner: {} {!r} is transferred but its Owner names nobody".format(level, name))
    return found


def _check_stray(entries, roster, backlog):
    return ["stray: field {} sits under a heading that is not an entry, or before the first entry, so it "
            "belongs to no entry".format(e[1].capitalize())
            for e in entries if e[0] == "stray"]


CHECKS = {
    "missing": _check_missing,
    "duplicate": _check_duplicate,
    "unknown": _check_unknown,
    "split": _check_split,
    "disposition": _check_disposition,
    "successor": _check_successor,
    "owner": _check_owner,
    "transferred-owner": _check_transferred_owner,
    "stray": _check_stray,
}


def findings(text, backlog, roster=OBLIGATIONS, disabled=frozenset()):
    entries = parse_crosswalk(text)
    out = []
    for name, check in CHECKS.items():
        if name not in disabled:
            out.extend(check(entries, roster, backlog))
    return out


def _read(rel):
    try:
        return (ROOT / rel).read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise CannotEvaluate("cannot read {}: {}".format(rel, exc))


def run_gate():
    try:
        listed = residue_obligations(_read(MANIFEST))
        text = _read(CROSSWALK)
        backlog = backlog_ids(_read(BACKLOG))
        crosswalk_found = findings(text, backlog)
    except CannotEvaluate as exc:
        sys.stderr.write("CANNOT EVALUATE check_opf_init_reconciliation: {}\n".format(exc))
        return 2
    found = []
    if tuple(listed) != OBLIGATIONS:
        found.append("residue drift: {} lists {!r}, the frozen roster is {!r}".format(
            MANIFEST, listed, list(OBLIGATIONS)))
    found.extend(crosswalk_found)
    for line in found:
        sys.stdout.write("FAIL check_opf_init_reconciliation: {}\n".format(line))
    if found:
        return 1
    sys.stdout.write("PASS check_opf_init_reconciliation: {} obligations, {} split children\n".format(
        len(OBLIGATIONS), len(SPLIT_CHILDREN)))
    return 0


# --- self-test ---------------------------------------------------------------------------------------

def _entry(name, disposition="open", successor="none", header="## Obligation: ", owner="none yet.",
           ownership="open."):
    return "{}{}\n\n- Owner: {}\n- Ownership: {}\n- Disposition: {}\n- Successor: {}\n\n".format(
        header, name, owner, ownership, disposition, successor)


def _transferred(name, successor="OPF-ADOPT-ORCH", header="## Obligation: ", owner="adoption work"):
    return _entry(name, "transferred", successor, header, owner, "transferred.")


_DISPLAY = {
    "keep-apply": "Keep-apply", "cli verbs": "CLI verbs", "migrate/move": "Migrate/Move",
}


def _good(drop=None, extra="", children=SPLIT_CHILDREN, override=None):
    parts = ["# Crosswalk\n\n"]
    for name in OBLIGATIONS:
        if name == drop:
            continue
        if override and name in override:
            parts.append(override[name])
        else:
            parts.append(_entry(_DISPLAY.get(name, name)))
        if name == SPLIT_PARENT:
            parts.extend(_entry(c, header="### Split: ") for c in children)
    parts.append(extra)
    return "".join(parts)


_MANIFEST_FIXTURE = (
    '[[gate]]\nid = "opf-init-p0"\nresidue = "Class c: the later OPF-INIT slices (P1 and beyond) are out of scope '
    'and remain separate obligations awaiting maintainer design review: target and control binding, the adoption '
    'classifier, coordination, Keep-apply, verb wiring, git staging, publication, finalization, the CLI verbs, and '
    'Migrate/Move; the contract fixtures drive no live adopter product repository."\n')

# Two backlog tables; prose and a row outside an "| ID |" table name no id.
_BACKLOG_FIXTURE = (
    "# TODO\n\n| ID | Status | Item | Tags |\n| --- | --- | --- | --- |\n"
    "| OPF-ADOPT-ORCH | Partly done | Adoption | `[public]` |\n\nOPF-ADOPTORCH in prose is not a row.\n\n"
    "| ID | Status | Item | Tags |\n| --- | --- | --- | --- |\n| OPF-IMPORT-COMPLETION | Planned | Import | `[x]` |\n\n"
    "| Name | Note |\n| XYZZY-NOT-REAL | a table with no ID header |\n")

# Two unparseable manifests: one tomllib refuses with RecursionError, one with TOMLDecodeError.
_DEEP_MANIFEST = "deep = " + "[" * 5000 + "]" * 5000 + "\n"
_INVALID_MANIFEST = "[[gate]\n"

# A gate value of each TOML type that is not an array of tables; the first eight raised TypeError when iterated.
_NON_LIST_GATES = (
    ("integer", "gate = 1\n"), ("float", "gate = 1.5\n"), ("bool", "gate = true\n"), ("date", "gate = 1979-05-27\n"),
    ("time", "gate = 07:32:00\n"), ("datetime", "gate = 1979-05-27T07:32:00Z\n"), ("inf", "gate = inf\n"),
    ("nan", "gate = nan\n"), ("string", 'gate = "x"\n'), ("table", "[gate]\nid = 1\n"), ("absent", "other = 1\n"),
)
_TYPEERROR_GATES = 8

# Successor values an open rule (anything but empty or "none") accepted; each must be refused.
_OPEN_RULE_SUCCESSORS = ("XYZZY-NOT-REAL", "none yet", "TBD", "-", "OPF-ADOPTORCH", "opf-adopt-orch")


def _unhandled_parse(manifest_text):
    """The manifest parse with its handler removed: the flip each unparseable-manifest vector turns red under."""
    return tomllib.loads(manifest_text)


def _unguarded_gate_tables(doc):
    """The gate lookup with its type check removed: the flip each non-list gate vector turns red under."""
    return doc.get("gate", [])


def _open_successor_rule(entries, roster, backlog):
    """The successor check as an open rule, any value but empty or "none" passing: the flip each closed-list
    vector turns red under."""
    return ["open-rule: {!r}".format(name) for _level, name, fields, _parent in entries
            if fields.get("disposition") == ["transferred"] and (
                len(fields.get("successor", [])) != 1
                or fields["successor"][0].strip().casefold() in ("", "none"))]


def _unguarded_backlog_ids(todo_text):
    """The backlog read with its empty-list refusal removed: the flip the no-id vector turns red under."""
    return frozenset(m.group(1) for m in map(_BACKLOG_ROW_RE.match, todo_text.split("\n")) if m)


def _files(manifest=_MANIFEST_FIXTURE, crosswalk=None, backlog=_BACKLOG_FIXTURE):
    texts = {MANIFEST: manifest, CROSSWALK: _good() if crosswalk is None else crosswalk}
    if backlog is not None:
        texts[BACKLOG] = backlog
    return texts


def _run(texts):
    """(exit code, stdout, stderr) of run_gate over in-memory files, an absent file reading as unreadable; an
    exception that escapes run_gate gives a None exit code."""
    def read(rel):
        if rel not in texts:
            raise CannotEvaluate("cannot read {}: absent".format(rel))
        return texts[rel]
    out, err = io.StringIO(), io.StringIO()
    try:
        with mock.patch.object(sys.modules[__name__], "_read", read), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = run_gate()
    except Exception:
        code = None
    return code, out.getvalue(), err.getvalue()


def _gate_cannot_evaluate(manifest_text, message="gates manifest does not parse: "):
    """True when run_gate over this manifest text and a clean crosswalk exits 2 with the named message; an
    exception that escapes run_gate is False."""
    code, _out, err = _run(_files(manifest=manifest_text))
    return code == 2 and err.startswith("CANNOT EVALUATE check_opf_init_reconciliation: " + message)


# Manifests whose residue this gate cannot read, each with the start of the refusal it must give. Without the
# guard each names, the first and third raise AttributeError and the fourth IndexError, the second and fifth
# pass on the first match (exit 0), and the last two read a wrong list (residue drift, exit 1).
_RESIDUE_SHAPES = (
    ("gate-entry-not-a-table", "gate = [1]\n", "gates manifest has no single "),
    ("two-residue-gates", _MANIFEST_FIXTURE * 2, "gates manifest has no single "),
    ("residue-not-a-string", '[[gate]]\nid = "opf-init-p0"\nresidue = 1\n', "gates manifest has no single "),
    ("no-lead", _MANIFEST_FIXTURE.replace("review: ", "review "), "the opf-init-p0 residue no longer carries "),
    ("two-leads", _MANIFEST_FIXTURE.replace("Migrate/Move;", "Migrate/Move; and " + RESIDUE_LEAD + "x;"),
     "the opf-init-p0 residue no longer carries "),
    ("no-end", _MANIFEST_FIXTURE.replace("Migrate/Move;", "Migrate/Move."),
     "the opf-init-p0 residue obligation list has no end"),
    ("empty-name", _MANIFEST_FIXTURE.replace("coordination, ", "coordination, , "),
     "the opf-init-p0 residue obligation list is empty or malformed"),
)

# TODO.md rows a reader does not see: a fenced block and an HTML comment, each holding an "| ID |" table.
_HIDDEN_BACKLOG = ("```\n| ID | Status |\n| OPF-FENCED | x |\n```\n"
                   "<!--\n| ID | Status |\n| OPF-COMMENTED | x |\n-->\n")


class _StubRoot:
    """A ROOT stand-in under which every file reads as `data` (bytes), or raises `data` (an exception)."""

    def __init__(self, data):
        self.data = data

    def __truediv__(self, rel):
        return self

    def read_bytes(self):
        if isinstance(self.data, BaseException):
            raise self.data
        return self.data


def _run_over_root(root):
    """(exit code, stderr) of run_gate reading every file through the real _read under `root`; an exception
    that escapes run_gate gives a None exit code."""
    out, err = io.StringIO(), io.StringIO()
    try:
        with mock.patch.object(sys.modules[__name__], "ROOT", root), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = run_gate()
    except Exception:
        code = None
    return code, err.getvalue()


def _findings_or_none(text, backlog, disabled=frozenset()):
    """findings(), or None when it raises, so a parser that crashes fails its vector instead of the run."""
    try:
        return findings(text, backlog, disabled=disabled)
    except Exception:
        return None


def _owner_after(separator):
    """The clean crosswalk with its last entry's Owner moved below `separator`: green only when the gate lets
    that Owner attach to the entry above the separator."""
    last = _entry(SPLIT_CHILDREN[-1], header="### Split: ")
    kept = "".join(line for line in last.splitlines(True) if not line.startswith("- Owner:"))
    return _good().replace(last, kept) + separator + "- Owner: none yet.\n"


def _with_line(name, line):
    """The clean crosswalk with `line` placed in entry `name` above its fields."""
    return _good(override={name: _entry(name).replace("\n\n- Owner:", "\n\n" + line + "\n\n- Owner:", 1)})


def _ignore(*_args):
    return None


def _never(*_args):
    return False


def _broad_hidden(text):
    """An entry name refused for any "&...;", decoded or not: the flip the undecoded-reference vector turns red
    under."""
    return bool(re.search(r"&(?:#[0-9]+|#[xX][0-9A-Fa-f]+|[A-Za-z][A-Za-z0-9]*);", text)) or any(
        _invisible(ch) for ch in text)


def _comment_to_line_end(line, start):
    """An inline comment read as running to the line end when it does not close, with no short form."""
    end = line.find("-->", start + 4)
    return len(line) if end < 0 else end + 3


def _code_span_end_any(line, start, length):
    """A code span read as closing on the next `length` backticks, even inside a longer run."""
    end = line.find("`" * length, start)
    return None if end < 0 else end + length


def _block_comment_rest_after_opener(line, opener):
    """A comment block read as ending only on a "-->" after the opener's own four characters."""
    end = line.find("-->", line.find("<!--") + 4 if opener else 0)
    return None if end < 0 else line[end + 3:]


# Each rule a crosswalk line is refused under, with the module attribute and the stand-in that removes it. The
# last five are readings rather than refusals: each stand-in reads a comment, a code span or a name the way a
# reader does not, and the vectors under them pass because raw HTML or a name is then hidden from this gate.
_RULE_FLIPS = {
    "plain": ("_PLAIN_RE", re.compile("")),
    "html": ("_HTML_RE", re.compile(r"(?!)")),
    "names": ("_NAMES_RE", re.compile(r"(?!)")),
    "indent": ("_refuse_indented", _ignore),
    "control": ("_CONTROL_RE", re.compile(r"(?!)")),
    "remainder": ("_refuse_remainder", _ignore),
    "opener": ("_INDENTED_OPENER_RE", re.compile(r"(?!)")),
    "unbounded": ("_refuse_unbounded", _ignore),
    "two-runs": ("_refuse_runs", _ignore),
    "hidden-name": ("_hidden_name", _never),
    "comment-eol": ("_comment_end", _comment_to_line_end),
    "short-comment": ("_SHORT_COMMENTS", ()),
    "span-length": ("_code_span_end", _code_span_end_any),
    "escape": ("_ESCAPABLE", ""),
}

# Crosswalks holding a line outside the shapes this gate reads: (label, text, the rules that refuse it). Each
# cannot evaluate, and each passes (exit 0) with exactly those rules removed. An _owner_after vector passes
# because its Owner then attaches to the entry above; a _good(extra=...) vector because its lines read as prose.
_UNREAD_SHAPES = (
    ("setext-dash", _owner_after("Appendix\n---\n\n"), {"plain"}),
    ("setext-equals", _owner_after("Appendix\n===\n\n"), {"plain"}),
    ("thematic-break", _owner_after("***\n\n"), {"plain"}),
    ("html-heading", _owner_after("<h2>Appendix</h2>\n\n"), {"plain", "html"}),
    ("blockquote-heading", _owner_after("> ## Appendix\n\n"), {"plain"}),
    ("indented-code", _owner_after("    Appendix\n\n"), {"indent"}),
    ("indented-heading", _owner_after("   ## Appendix\n\n"), {"indent"}),
    ("indented-heading-in-split", _good().replace("### Split: move", "    ### Notes\n\n### Split: move"),
     {"indent"}),
    ("table-row", _owner_after("| Appendix |\n\n"), {"plain"}),
    ("table-delimiter-row", _owner_after("Name | Note\n--- | ---\n\n"), {"plain"}),
    ("table-row-field", _good(extra="| Note |\n| --- |\n| Owner: someone |\n"), {"plain"}),
    ("table-row-entry", _good(extra="| Note |\n| --- |\n| Obligation: coordination |\n"), {"plain"}),
    ("list-item-heading", _owner_after("- ## Appendix\n\n"), {"plain"}),
    ("list-item-html-heading", _owner_after("- <h2>Appendix</h2>\n\n"), {"plain", "html"}),
    ("list-item-blockquote", _owner_after("- > Appendix\n\n"), {"plain"}),
    ("list-item-entry-heading", _good(extra="- ## Obligation: coordination\n"), {"plain"}),
    ("continued-item-heading", _owner_after("- Note: x\n  ## Appendix\n\n"), {"plain"}),
    ("star-field", _good(extra="## Appendix\n\n* Owner: none yet.\n"), {"plain", "names"}),
    ("plus-field", _good(extra="## Appendix\n\n+ Owner: none yet.\n"), {"plain"}),
    ("ordered-field", _good(extra="## Appendix\n\n1. Owner: none yet.\n"), {"names"}),
    ("paren-ordered-field", _good(extra="## Appendix\n\n1) Owner: none yet.\n"), {"plain"}),
    ("tab-dash-field", _good(extra="## Appendix\n\n-\tOwner: none yet.\n"), {"plain"}),
    ("nested-dash-field", _good(extra="## Appendix\n\n  - Owner: none yet.\n"), {"indent"}),
    ("prose-field", _good(extra="Owner: none yet.\n"), {"names"}),
    ("raw-html-in-prose", _owner_after("A note <h2>Appendix</h2>\n\n"), {"html"}),
    ("raw-html-in-field", _good(extra="- Note: x <h2>Appendix</h2>\n"), {"html"}),
    ("comment-remainder", _owner_after("<!-- x --><h2>Appendix</h2>\n\n"), {"remainder"}),
    ("comment-remainder-closing-line", _owner_after("<!-- x\n--><h2>Appendix</h2>\n\n"), {"remainder"}),
    ("indented-fence-opener", _good(extra="- Note: x\n  ```\n## Obligation: coordination\n"), {"opener"}),
    ("indented-comment-opener", _good(extra="- Note: x\n  <!-- x\n## Obligation: coordination\n-->\n"),
     {"opener"}),
    ("crlf-setext", _owner_after("Appendix\r\n---\r\n\r\n"), {"control", "plain"}),
    ("crlf-bare-heading", _owner_after("##\r\n\r\n"), {"control", "plain"}),
    ("lone-carriage-return", _owner_after("A note\r## Appendix\n\n"), {"control"}),
    ("hash-prose", _with_line("publication", "#5 is tracked elsewhere."), {"plain"}),
    ("seven-hashes", _with_line("publication", "####### not a heading"), {"plain"}),
    ("entry-setext", _good(extra="Obligation: coordination\n===\n"), {"names", "plain"}),
    ("entry-setext-indented-continuation", _good(extra="Obligation: coordination\n    continued\n===\n"),
     {"names", "indent", "plain"}),
    ("entry-html", _good(extra="<h2>Obligation: coordination</h2>\n"), {"plain", "html"}),
    ("entry-blockquote", _good(extra="> ## Obligation: coordination\n"), {"plain"}),
    ("entry-indented-heading", _good(extra=" ## Obligation: coordination\n"), {"indent"}),
    ("entry-indented-code", _good(extra="    ## Obligation: coordination\n"), {"indent"}),
    ("entry-star-item", _good(extra="* Obligation: coordination\n"), {"plain", "names"}),
    ("entry-other-case", _good(extra="## obligation: coordination\n"), {"names"}),
    ("entry-emphasis", _good(extra="## **Obligation:** coordination\n"), {"plain", "names"}),
    ("entry-emphasis-setext", _good(extra="**Obligation:** coordination\n===\n"), {"plain", "names"}),
    ("entry-escaped", _good(extra="## Obligation\\: coordination\n"), {"names"}),
    ("unclosed-comment-in-prose", _owner_after("A note <!-- <h2>Appendix</h2>\n\n"), {"comment-eol"}),
    ("unclosed-comment-in-field", _owner_after("- Note: x <!-- <h2>Appendix</h2>\n\n"), {"comment-eol"}),
    ("short-comment-in-prose", _owner_after("A note <!--> <h2>Appendix</h2>\n\n"), {"comment-eol"}),
    ("short-comment-ends-first", _owner_after("A note <!--> <h2>Appendix</h2> -->\n\n"), {"short-comment"}),
    ("dash-short-comment-ends-first", _owner_after("A note <!---> <h2>Appendix</h2> -->\n\n"), {"short-comment"}),
    ("span-longer-closer", _owner_after("A note ` <h2>Appendix</h2> ``\n\n"), {"span-length"}),
    ("span-longer-closer-in-field", _owner_after("- Note: `` <h2>Appendix</h2> ```\n\n"), {"span-length"}),
    ("two-runs-above-prose", _owner_after("A note ` x `` y\nz ` w\n\n"), {"two-runs"}),
    ("span-exact-closer-later", _owner_after("A note `` a ``` b `` <h2>Appendix</h2> `` c ``\n\n"), {"span-length"}),
    ("span-escaped-opener", _owner_after("A note \\` <h2>Appendix</h2> `\n\n"), {"escape"}),
    ("span-across-lines", _owner_after("A note `x\ny `<h2>Appendix</h2>` z\n\n"), {"html"}),
    ("span-open-past-next-line", _owner_after("A note `x\ny\nz `<h2>Appendix</h2>` w\n\n"), {"unbounded"}),
    ("span-open-above-ordered-item", _good(extra="A note `x\n10. y `<h2>Appendix</h2>` z\n"), {"unbounded"}),
    ("name-decimal-reference", _good(extra="## Obligation&#58; coordination\n"), {"names"}),
    ("name-named-reference", _good(extra="## Obligation&colon; coordination\n"), {"names"}),
    ("name-zero-width", _good(extra="## Obli\u200bgation: coordination\n"), {"hidden-name"}),
    ("field-name-reference", _good(extra="Owner&#58; none yet.\n"), {"names"}),
    ("field-name-soft-hyphen", _good(extra="Own\u00ader: none yet.\n"), {"hidden-name"}),
    ("field-name-soft-hyphen-reference", _good(extra="Own&shy;er: none yet.\n"), {"hidden-name"}),
    ("field-name-legacy-prefix-reference", _good(extra="Own&shyer; notes are open.\n"), {"hidden-name"}),
    ("field-name-inner-reference", _good(extra="Own&#101;r: none yet.\n"), {"names"}),
    ("field-name-reference-after-space", _good(extra="Owner &#58; none yet.\n"), {"names"}),
    ("field-name-reference-after-tab", _good(extra="Owner\t&#58; none yet.\n"), {"names"}),
    ("field-name-format-character-after-tab", _good(extra="Owner\t\u200b: none yet.\n"), {"hidden-name"}),
    # Whitespace a reference decodes to before the colon: HTML shows each as a space, as it does "Owner :".
    ("field-name-line-feed-reference", _good(extra="Owner&#10;: none yet.\n"), {"names"}),
    ("field-name-hex-line-feed-reference", _good(extra="Owner&#xA;: none yet.\n"), {"names"}),
    ("field-name-named-line-feed-reference", _good(extra="Owner&NewLine;: none yet.\n"), {"names"}),
    ("field-name-carriage-return-reference", _good(extra="Owner&#13;: none yet.\n"), {"names"}),
    ("field-name-hex-carriage-return-reference", _good(extra="Owner&#x0D;: none yet.\n"), {"names"}),
    ("field-name-form-feed-reference", _good(extra="Owner&#12;: none yet.\n"), {"names"}),
    ("field-name-hex-form-feed-reference", _good(extra="Owner&#xC;: none yet.\n"), {"names"}),
    ("field-name-no-break-space-reference", _good(extra="Owner&nbsp;: none yet.\n"), {"names"}),
    ("field-name-no-break-space", _good(extra="Owner\u00a0: none yet.\n"), {"names"}),
    ("field-name-em-space-reference", _good(extra="Owner&#x2003;: none yet.\n"), {"names"}),
    ("successor-line-feed-reference", _good(extra="Successor&#10;: none.\n"), {"names"}),
    ("disposition-line-feed-reference", _good(extra="Disposition&#10;: transferred\n"), {"names"}),
    ("ownership-named-line-feed-reference", _good(extra="Ownership&NewLine;: open.\n"), {"names"}),
    ("obligation-carriage-return-reference", _good(extra="Obligation&#13;: coordination\n"), {"names"}),
    ("split-form-feed-reference", _good(extra="Split&#12;: import disposition\n"), {"names"}),
    ("heading-line-feed-reference", _good(extra="## Obligation&#10;: coordination\n"), {"names"}),
    ("field-name-line-feed-then-zero-width", _good(extra="Owner&#10;&#x200B;: none yet.\n"), {"hidden-name"}),
    # Default-ignorable characters that are not format characters: a reader shows "Owner:".
    ("field-name-grapheme-joiner-reference", _good(extra="Own&#847;er: none yet.\n"), {"hidden-name"}),
    ("field-name-grapheme-joiner", _good(extra="Own\u034fer: none yet.\n"), {"hidden-name"}),
    ("field-name-variation-selector-reference", _good(extra="Own&#xFE0F;er: none yet.\n"), {"hidden-name"}),
    ("field-name-mongolian-selector-reference", _good(extra="Own&#x180B;er: none yet.\n"), {"hidden-name"}),
    ("field-name-hangul-filler-reference", _good(extra="Own&#x3164;er: none yet.\n"), {"hidden-name"}),
    ("field-name-hangul-filler-after-name", _good(extra="Owner&#x3164;: none yet.\n"), {"hidden-name"}),
    ("tab-indented-comment-opener", _good(extra="- Note: x\n\t<!-- x\n"), {"opener", "indent", "html"}),
    ("ordered-item-indented-fence", _good(extra="10. x\n    ```\n    y\n"), {"opener", "unbounded"}),
)

# Entry headings whose name holds a character reference or a format character: each cannot evaluate, and
# without that refusal each reads as an obligation or split outside the roster (exit 1), never a pass.
_HIDDEN_ENTRY_NAMES = (
    ("obligation-reference", _good(extra=_entry("coordi&#110;ation"))),
    ("obligation-hex-reference", _good(extra=_entry("coordi&#x6E;ation"))),
    ("obligation-zero-width", _good(extra=_entry("coordination\u200b"))),
    ("obligation-grapheme-joiner", _good(extra=_entry("coordi\u034fnation"))),
    ("split-reference", _good(children=SPLIT_CHILDREN + ("move disposition&#8203;",))),
)

# Headings that end the entry above them: the Owner below each is a stray field (exit 1).
_ENDING_HEADINGS = (
    ("space-separator", "## Appendix\n\n"),
    ("tab-separator", "##\tAppendix\n\n"),
    ("bare", "##\n\n"),
    ("six-hashes", "###### Appendix\n\n"),
    ("level-1", "# Appendix\n\n"),
)


def self_test():
    failures = []
    total = 0

    def expect(cond, label):
        nonlocal total
        total += 1
        if not cond:
            failures.append(label)

    module = sys.modules[__name__]
    expect(residue_obligations(_MANIFEST_FIXTURE) == list(OBLIGATIONS), "residue/parse-roster")
    expect(residue_obligations(_MANIFEST_FIXTURE.replace("Migrate/Move;", "Migrate/Move.;")) == list(OBLIGATIONS),
           "residue/trailing-dot-stripped")
    for bad, label in ((_MANIFEST_FIXTURE.replace("review: ", "review "), "residue/no-lead"),
                       ('[[gate]]\nid = "other"\nresidue = "x"\n', "residue/no-gate"),
                       ("[[gate]\n", "residue/unparseable")):
        try:
            residue_obligations(bad)
        except CannotEvaluate:
            expect(True, label)
        except Exception:
            expect(False, label)
        else:
            expect(False, label)
    dropped = _MANIFEST_FIXTURE.replace("git staging, ", "")
    expect(residue_obligations(dropped) != list(OBLIGATIONS), "residue/drop-detected")
    code, out, _err = _run(_files(manifest=dropped))
    expect(code == 1 and "FAIL check_opf_init_reconciliation: residue drift: " in out, "residue/drift-fails-gate")
    for bad, label in ((_DEEP_MANIFEST, "deeply-nested"), (_INVALID_MANIFEST, "invalid")):
        expect(_gate_cannot_evaluate(bad), "unparseable-manifest/{}".format(label))
        with mock.patch.object(module, "_parse_manifest", _unhandled_parse):
            expect(not _gate_cannot_evaluate(bad), "unparseable-manifest/{}-red-without-handler".format(label))
    for index, (label, bad) in enumerate(_NON_LIST_GATES):
        expect(_gate_cannot_evaluate(bad, "gates manifest has no gate array"), "non-list-gate/{}".format(label))
        if index < _TYPEERROR_GATES:
            with mock.patch.object(module, "_gate_tables", _unguarded_gate_tables):
                expect(_run(_files(manifest=bad))[0] is None, "non-list-gate/{}-red-without-guard".format(label))
    for label, bad, message in _RESIDUE_SHAPES:
        expect(_gate_cannot_evaluate(bad, message), "residue-shape/{}".format(label))
    for label, data in (("not-utf8", b"\xff"), ("os-error", FileNotFoundError("absent"))):
        code, err = _run_over_root(_StubRoot(data))
        expect(code == 2 and "cannot read " in err, "read/{}-cannot-evaluate".format(label))

    backlog = backlog_ids(_BACKLOG_FIXTURE)
    expect(backlog == {"OPF-ADOPT-ORCH", "OPF-IMPORT-COMPLETION"}, "backlog/ids-from-id-tables-only")
    expect(backlog_ids(_BACKLOG_FIXTURE + _HIDDEN_BACKLOG) == backlog, "backlog/hidden-rows-name-no-id")
    code, _out, err = _run(_files(backlog="# TODO\n\n" + _HIDDEN_BACKLOG))
    expect(code == 2 and "lists no backlog id" in err, "backlog/only-hidden-ids-cannot-evaluate")
    expect(_run(_files())[0] == 0, "gate/clean-passes")
    code, _out, err = _run(_files(backlog=None))
    expect(code == 2 and "cannot read TODO.md" in err, "backlog/absent-cannot-evaluate")
    no_ids = "# TODO\n\nNo table here; OPF-ADOPT-ORCH in prose.\n"
    code, _out, err = _run(_files(backlog=no_ids))
    expect(code == 2 and "lists no backlog id" in err, "backlog/no-id-cannot-evaluate")
    with mock.patch.object(module, "backlog_ids", _unguarded_backlog_ids):
        expect(_run(_files(backlog=no_ids))[0] == 0, "backlog/no-id-passes-without-refusal")

    expect(findings(_good(), backlog) == [], "clean/no-findings")
    transferred = _good(override={"verb wiring": _transferred("verb wiring")})
    expect(findings(transferred, backlog) == [], "clean/transferred-with-backlog-successor")
    expect(bool(findings(transferred, frozenset({"OPF-IMPORT-COMPLETION"}))),
           "flip/transferred-red-when-successor-leaves-backlog")
    for hidden, label in (("```\n{}```\n", "backtick-fence"), ("~~~~ text\n{}~~~~\n", "tilde-fence"),
                          ("<!--\n{}-->\n", "comment")):
        expect(findings(_good(extra=hidden.format(_entry("coordination"))), backlog) == [],
               "clean/hidden-duplicate-ignored/{}".format(label))
    expect(findings(_good(drop="git staging", extra="```\nx\n```\n" + _entry("git staging")), backlog) == [],
           "clean/entry-after-closed-fence-counts")
    expect(findings(_good(override={"coordination": _entry("coordination") + "<!-- a note -->\n"}), backlog) == [],
           "clean/entry-after-closed-comment-counts")
    expect(_findings_or_none(_good(drop="git staging", extra="```x```\n\n" + _entry("git staging")), backlog) == [],
           "clean/inline-code-line-opens-no-fence")
    expect(_findings_or_none(_good().replace("### Split: move", "### Notes\n\nA note.\n\n### Split: move"),
                             backlog) == [], "clean/level-3-heading-keeps-split-parent")
    expect(_findings_or_none(_good(extra="## Appendix\n\n- Note: unread.\n"), backlog) == [],
           "clean/unread-field-outside-entry-ignored")
    # The reported shape: the last entry lacks its Owner and a field under "## Appendix" supplied it.
    expect(_run(_files(crosswalk=_owner_after("## Appendix\n\n")))[0] == 1, "stray/appendix-owner-fails-gate")
    # The same shape after each other heading: red, and green once the heading reads as prose.
    for label, heading in _ENDING_HEADINGS:
        text = _owner_after(heading)
        expect(_run(_files(crosswalk=text))[0] == 1, "stray/{}-heading-owner-fails-gate".format(label))
        with mock.patch.object(module, "_HEADING_RE", re.compile(r"(?!)")), \
                mock.patch.object(module, *_RULE_FLIPS["plain"]):
            expect(_findings_or_none(text, backlog) == [], "stray/{}-heading-green-as-prose".format(label))
    # Each line outside the shapes this gate reads cannot evaluate, and passes once the rules that refuse it are
    # removed; every rule is the only one refusing at least one vector.
    for label, text, rules in _UNREAD_SHAPES:
        code, _out, err = _run(_files(crosswalk=text))
        expect(code == 2 and " is not a shape this gate reads " in err, "unread/{}-cannot-evaluate".format(label))
        with contextlib.ExitStack() as stack:
            for rule in rules:
                stack.enter_context(mock.patch.object(module, *_RULE_FLIPS[rule]))
            expect(_run(_files(crosswalk=text))[0] == 0, "unread/{}-passes-without-its-rules".format(label))
    expect({min(r) for _label, _text, r in _UNREAD_SHAPES if len(r) == 1} == set(_RULE_FLIPS),
           "unread/every-rule-alone-refuses-a-vector")
    for label, text in _HIDDEN_ENTRY_NAMES:
        code, _out, err = _run(_files(crosswalk=text))
        expect(code == 2 and " is not a shape this gate reads " in err, "hidden-entry-name/{}-cannot-evaluate".format(label))
        with mock.patch.object(module, "_hidden", _never):
            expect(_run(_files(crosswalk=text))[0] == 1, "hidden-entry-name/{}-unknown-without-refusal".format(label))
    # A reference html.unescape leaves as it is ("&foo;" names no entity) is part of the entry name, so the entry
    # is read and is unknown (exit 1); refused for any "&...;", as a broad pattern would, it cannot evaluate.
    undecoded = _good(extra=_entry("coordination&foo;"))
    expect(_run(_files(crosswalk=undecoded))[0] == 1, "hidden-entry-name/undecoded-reference-read-as-unknown")
    with mock.patch.object(module, "_hidden", _broad_hidden):
        expect(_run(_files(crosswalk=undecoded))[0] == 2, "flip/undecoded-reference-refused-by-broad-pattern")
    # A "<!--" inside a line that does not close there is literal text to a reader, or a comment the paragraph's
    # next line closes; either way this gate cannot read past it. Read as running to the line end, it hid no
    # later line: the heading below it ended the entry (exit 1), and an entry below it counted.
    inline_heading = _owner_after("A note <!-- x\n## Appendix -->\n")
    inline_entry = _good(drop="git staging", extra="A note <!-- x\n\n" + _entry("git staging"))
    expect(_run(_files(crosswalk=inline_heading))[0] == 2, "inline-comment/unclosed-above-heading-cannot-evaluate")
    expect(_run(_files(crosswalk=inline_entry))[0] == 2, "inline-comment/unclosed-above-entry-cannot-evaluate")
    with mock.patch.object(module, *_RULE_FLIPS["comment-eol"]):
        expect(_run(_files(crosswalk=inline_heading))[0] == 1, "inline-comment/heading-below-ends-entry-at-line-end")
        expect(_findings_or_none(inline_entry, backlog) == [], "inline-comment/entry-below-counts-at-line-end")
    # A closed inline comment and a code span opened on one line and closed on the next are read.
    expect(_findings_or_none(_with_line("publication", "A note <!-- x --> here, `a\nb` and ``c ` d``."),
                             backlog) == [], "clean/closed-comment-and-wrapped-code-span")
    expect(_findings_or_none(_good(override={"publication": _entry("publication").replace(
        "- Owner: none yet.", "- Owner: none yet `x\n  y`.")}), backlog) == [], "clean/field-code-span-wraps")
    # A reference after letters that cannot begin an entry or field name is read, and so is one after a name's
    # first letters that decodes to a character no name continues with.
    for label, line in (("ampersand", "AT&amp;T ships it."), ("space", "Keep&nbsp;this mid-sentence."),
                        ("after-space", "Keep x&nbsp;y mid-sentence."), ("name-letter-ampersand", "S&amp;P index."),
                        ("name-letter-ampersand-other", "O&amp;M work."),
                        ("whole-name-ampersand", "Owner &amp; operator notes are open."),
                        ("whole-name-no-break-space", "Split&nbsp;decisions are open."),
                        ("letter-completing-no-name", "Own&#101;rship notes are open."),
                        ("visible-combining-mark", "Own&#769;er: none yet."),
                        ("escaped-reference", "Own\\&#101;r: none yet."),
                        ("reference-in-code", "Own`&#101;`r: none yet."),
                        ("number-without-semicolon", "Owner&#58 none yet."),
                        ("name-without-semicolon", "Own&shyer: none yet."),
                        ("unknown-entity-name", "Owner&colonx; none yet."),
                        ("number-over-seven-digits", "Owner&#00000058; none yet.")):
        expect(_findings_or_none(_with_line("publication", line), backlog) == [],
               "clean/reference-after-other-letters/{}".format(label))
    # Two spellings a reader renders alike get the same exit: a reference and the character it decodes to.
    for label, spelled, literal in (("colon", "Owner&#58; none yet.", "Owner: none yet."),
                                    ("letter", "Own&#101;r: none yet.", "Owner: none yet."),
                                    ("soft-hyphen", "Own&#173;er: none yet.", "Own\u00ader: none yet."),
                                    ("legacy-prefix", "Own&shyer; notes.", "Own\u00ader; notes."),
                                    ("no-break-space", "Owner&nbsp;: none yet.", "Owner\u00a0: none yet."),
                                    ("combining-mark", "Own&#769;er: none yet.", "Own\u0301er: none yet."),
                                    ("grapheme-joiner", "Own&#847;er: none yet.", "Own\u034fer: none yet."),
                                    ("line-feed", "Owner&#10;: none yet.", "Owner : none yet."),
                                    ("letters", "Own&#101;rship notes.", "Ownership notes."),
                                    ("code", "Own`&#101;`r: none yet.", "Own&amp;#101;r: none yet.")):
        expect(_run(_files(crosswalk=_good(extra=spelled + "\n")))[0]
               == _run(_files(crosswalk=_good(extra=literal + "\n")))[0], "rendered-alike/{}".format(label))
    # A backtick run that a reader shows as literal text, because the paragraph ends below it, is read.
    for label, extra in (("two-runs-above-blank", "Two ` runs `` open\n\nA note.\n"),
                         ("run-above-list-start", "A ` run\n1. item\n"),
                         ("two-runs-at-end", "1. x\n   ``` x `\n")):
        expect(_findings_or_none(_good(extra=extra), backlog) == [], "clean/literal-run/{}".format(label))
    # The list a reader starts on "1. " with text ends the paragraph; read as continuing it, the run is refused.
    with mock.patch.object(module, "_interrupts", _never):
        expect(_run(_files(crosswalk=_good(extra="A ` run\n1. item\n")))[0] == 2,
               "flip/run-above-list-start-refused-as-continuation")
    # A heading ends on its own line, so a run left open on it carries nothing; carried, the prose below it,
    # which closes no span, is refused.
    heading_run = _good(extra="## Notes `x\nA note here.\n")
    expect(_findings_or_none(heading_run, backlog) == [], "clean/heading-run-carries-nothing")
    with mock.patch.object(module, "_holds_paragraph", lambda _line: True):
        expect(_run(_files(crosswalk=heading_run))[0] == 2, "flip/heading-run-carried-refused")
    # An indented backtick run with another backtick later on its line is inline code, not a fence opener;
    # without that lookahead it is refused as one.
    inline_code = _good(extra="- Note: x\n  ```y``` z\n")
    expect(_findings_or_none(inline_code, backlog) == [], "clean/indented-inline-code-opens-nothing")
    with mock.patch.object(module, "_INDENTED_OPENER_RE", re.compile(r"^[ \t]+(?:`{3,}|~{3,}|<!--)")):
        expect(_run(_files(crosswalk=inline_code))[0] == 2, "flip/indented-inline-code-refused-as-opener")
    # "<!-->" and "<!--->" end the comment block they open on their own line, so the entry below is read.
    for form in ("<!-->", "<!--->"):
        for label, entry, check in (("duplicate", _entry("coordination"), "duplicate"),
                                    ("unknown", _entry("store upgrade"), "unknown")):
            text = _good(extra=form + "\n\n" + entry)
            found = _findings_or_none(text, backlog)
            expect(bool(found) and _findings_or_none(text, backlog, disabled=frozenset({check})) == [],
                   "comment-block/{}-then-{}-read".format(form, label))
            with mock.patch.object(module, "_block_comment_rest", _block_comment_rest_after_opener):
                expect(_findings_or_none(text, backlog) == [],
                       "comment-block/{}-then-{}-hidden-when-end-sought-after-opener".format(form, label))
    ownership = _good(extra="## Appendix\n\n- Ownership: open.\n")
    expect(bool(_findings_or_none(ownership, backlog)), "stray/ownership-refused")
    with mock.patch.object(module, "READ_FIELDS", tuple(f for f in READ_FIELDS if f != "ownership")):
        expect(_findings_or_none(ownership, backlog) == [], "stray/ownership-green-without-member")

    # Each fixture: (label, crosswalk text, the one check that must hold it red).
    fixtures = [("missing", _good(drop="git staging"), "missing")]
    for hidden, label in (("```\n{}```\n", "backtick-fence"), ("~~~\n{}```\n", "unclosed-fence"),
                          ("<!--\n{}-->\n", "comment"), ("<!--\n{}", "unclosed-comment")):
        fixtures.append(("missing-hidden-" + label,
                         _good(drop="git staging", extra=hidden.format(_entry("git staging"))), "missing"))
    # A line that does not close the open fence: another character, a shorter run, or an info string; the
    # entry after it stays hidden until the real close. A tilde fence may carry a backtick in its info string.
    for hidden, label in (("~~~\n```\n{}~~~\n", "other-character"), ("````\n```\n{}````\n", "shorter-run"),
                          ("```\n``` x\n{}```\n", "info-string"), ("~~~ `x`\n{}~~~\n", "tilde-backtick-info")):
        fixtures.append(("missing-fence-not-closed-" + label,
                         _good(drop="git staging", extra=hidden.format(_entry("git staging"))), "missing"))
    fixtures.append(("duplicate", _good(extra=_entry("coordination")), "duplicate"))
    fixtures.append(("unknown", _good(extra=_entry("store upgrade")), "unknown"))
    fixtures += [("split-missing-" + c.replace(" ", "-"),
                  _good(children=tuple(x for x in SPLIT_CHILDREN if x != c)), "split") for c in SPLIT_CHILDREN]
    fixtures.append(("split-duplicate", _good(children=SPLIT_CHILDREN + (SPLIT_CHILDREN[0],)), "split"))
    fixtures.append(("split-under-other-parent", _good(override={
        "coordination": _entry("coordination") + _entry(SPLIT_CHILDREN[0], header="### Split: ")}), "split"))
    fixtures.append(("split-after-unrelated-heading", _good(
        children=SPLIT_CHILDREN[:2], extra="## Appendix\n\n" + _entry(SPLIT_CHILDREN[2], header="### Split: ")),
        "split"))
    fixtures.append(("disposition-absent", _good(override={
        "publication": "## Obligation: publication\n\n- Owner: none yet.\n- Ownership: open.\n\n"}), "disposition"))
    fixtures.append(("disposition-unknown-value", _good(override={"publication": _entry("publication", "closed")}),
                     "disposition"))
    fixtures.append(("disposition-duplicate", _good(override={
        "publication": _entry("publication") + "- Disposition: open\n\n"}), "disposition"))
    for successor in _OPEN_RULE_SUCCESSORS + ("none", "", "None"):
        fixtures.append(("transferred-successor-" + (successor or "empty"),
                         _good(override={"finalization": _transferred("finalization", successor)}), "successor"))
    fixtures.append(("transferred-successor-duplicate", _good(override={
        "finalization": _transferred("finalization") + "- Successor: OPF-ADOPT-ORCH\n\n"}), "successor"))
    fixtures.append(("transferred-split-child", _good().replace(
        _entry("move disposition", header="### Split: "),
        _transferred("move disposition", "none", header="### Split: ")), "successor"))
    for field in ("Owner", "Ownership"):
        fixtures.append((field.casefold() + "-absent", _good(override={"publication": "".join(
            line for line in _entry("publication").splitlines(True) if not line.startswith("- " + field + ":"))}),
            "owner"))
    fixtures.append(("owner-empty", _good(override={"publication": _entry("publication", owner="")}), "owner"))
    fixtures.append(("ownership-duplicate", _good(override={
        "publication": _entry("publication") + "- Ownership: open.\n\n"}), "owner"))
    fixtures.append(("split-unknown-child", _good(children=SPLIT_CHILDREN + ("store upgrade",)), "split"))
    # " none" (two spaces after the colon) needs the strip; the comment hides an owner a reader never sees.
    for label, owner in (("none-yet.", "none yet."), ("None-yet", "None yet"), ("none", "none"),
                         ("space-none", " none"), ("commented", "none yet. <!-- adoption work -->")):
        fixtures.append(("transferred-owner-" + label, _good(override={
            "finalization": _transferred("finalization", owner=owner)}), "transferred-owner"))
    fixtures.append(("stray-under-appendix", _good(extra="## Appendix\n\n- Successor: OPF-ADOPT-ORCH\n"), "stray"))
    fixtures.append(("stray-before-first-entry", _good().replace("# Crosswalk\n\n", "# Crosswalk\n\n- Owner: x\n\n"),
                     "stray"))
    fixtures.append(("stray-under-level-3-heading", _good(override={
        "publication": _entry("publication") + "### Notes\n\n- Disposition: open\n\n"}), "stray"))
    fixtures += [("stray-after-" + label + "-heading", _good(extra=heading + "- Successor: OPF-ADOPT-ORCH\n"), "stray")
                 for label, heading in _ENDING_HEADINGS]
    fixtures.append(("stray-after-inline-comment", _good(
        extra="A note <!-- x -->\n## Appendix -->\n- Successor: OPF-ADOPT-ORCH\n"), "stray"))
    fixtures.append(("stray-ownership", _good(extra="## Appendix\n\n- Ownership: open.\n"), "stray"))
    for label, text, check in fixtures:
        red = _findings_or_none(text, backlog)
        expect(bool(red), "red/{}".format(label))
        expect(_findings_or_none(text, backlog, disabled=frozenset({check})) == [],
               "check-removed-green/{}".format(label))
    expect({f[2] for f in fixtures} == set(CHECKS), "every-check-has-a-fixture")
    with mock.patch.dict(CHECKS, {"successor": _open_successor_rule}):
        for successor in _OPEN_RULE_SUCCESSORS:
            text = _good(override={"finalization": _transferred("finalization", successor)})
            expect(findings(text, backlog) == [], "flip/open-successor-rule-accepts/{}".format(successor))

    for label in failures:
        sys.stdout.write("SELF-TEST FAIL check_opf_init_reconciliation: {}\n".format(label))
    if failures:
        return 1
    sys.stdout.write("PASS check_opf_init_reconciliation self-test ({} checks)\n".format(total))
    return 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv == ["--self-test"]:
        return self_test()
    if argv:
        sys.stderr.write("usage: check_opf_init_reconciliation.py [--self-test]\n")
        return 2
    return run_gate()


if __name__ == "__main__":
    if sys.argv[1:] == ["--self-test"]:
        sys.exit(self_test())
    sys.exit(main())
