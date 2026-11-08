#!/usr/bin/env python3
r"""A restricted reader for the YAML frontmatter of a skill file (SKILL.md). Offline, stdlib only, pure.

It never parses YAML in general and never imports a YAML library. It reads a small subset and decides
one of three statuses; anything it does not read is CANNOT_EVALUATE, never a pass.

  VALID (0)            every frontmatter line is inside the subset, and name and description are
                       non-empty strings
  INVALID (1)          a definite defect: a ": " or a line-final ":" inside a plain value (a YAML parser
                       reads either as a mapping indicator and refuses the document), a tab starting a
                       key line, a duplicate key, or a missing or empty name or description
  CANNOT_EVALUATE (2)  anything outside the subset, or bytes that cannot be read as the subset's text

The subset:
  - the file starts with a line that is exactly "---" and the frontmatter ends at the next line that is
    exactly "---"; the text is UTF-8 with LF line ends (a BOM, a CR or a NUL is CANNOT_EVALUATE);
  - every frontmatter character is inside YAML's printable set; a character outside it (a C0 control
    other than TAB, LF and CR, a C1 control other than NEL, DEL, a surrogate, U+FFFE or U+FFFF; the
    other non-characters, such as U+FDD0, are YAML-printable and read), a byte order mark after the
    opening fence, and the
    NEL, LINE SEPARATOR and PARAGRAPH SEPARATOR characters (line breaks to a YAML 1.1 parser, plain
    content to this reader's LF-only line split) are each CANNOT_EVALUATE;
  - each key line starts in column 0 with a plain key ([A-Za-z0-9_][A-Za-z0-9_-]*), a colon and one
    space, then exactly one value form (a value that starts with a further space is CANNOT_EVALUATE:
    the extra indentation would hide the value's first character from every form check below):
      a one-line single-quoted value ('it''s'), with nothing after the closing quote;
      a one-line double-quoted value whose escapes are only \\ \" \/ \n and \t;
      a block scalar header (|, |-, |+, >, >-, >+) followed by lines indented by at least one space;
      a safe plain value: it does not start with a YAML indicator character, it holds no " #" and no
        tab, it does not read as a YAML null, boolean, number (signed base-2, -8 and -16 forms
        included), the "=" value key or the "<<" merge key, and it may continue on following lines
        indented by at least one space (each continuation line held to the same rules);
  - a key line with nothing after the colon is an empty value: INVALID for name and description,
    CANNOT_EVALUATE for any other key;
  - a blank line between keys is allowed; a comment line, a list item, a flow collection, an anchor, an
    alias, a tag, a multi-line quoted value or an indentation indicator is CANNOT_EVALUATE.

A colon inside a plain value is a mapping indicator only when a space follows it or it ends the line.
So "see https://example.org" (a colon followed by "/") is plain content and passes, "a:b" passes, and
"in order: investigate" or a line ending in "order:" is INVALID. Inside either kind of quotes ": " is
content and passes.

The reader stops at the first line it cannot accept; that line decides the status, so a file holding
both a definite defect and an unread construct reports whichever comes first (both are non-zero).
The value text it returns for a block scalar or a multi-line plain value is a simple folding used only
for the non-empty test: it does not reproduce YAML's handling of more-indented lines inside a folded
scalar, and the gate never publishes it.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: _skill_frontmatter.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import re

VALID, INVALID, CANNOT_EVALUATE = 0, 1, 2
STATUS_NAMES = {VALID: "VALID", INVALID: "INVALID", CANNOT_EVALUATE: "CANNOT_EVALUATE"}
REQUIRED_KEYS = ("name", "description")
# A skill file larger than this is refused as CANNOT_EVALUATE before it is decoded.
MAX_BYTES = 4 * 1024 * 1024

FENCE = "---"
KEY_LINE_RE = re.compile(r"([A-Za-z0-9_][A-Za-z0-9_-]*):(?: (.*))?\Z", re.ASCII)
SINGLE_RE = re.compile(r"'((?:[^']|'')*)'\Z")
DOUBLE_RE = re.compile(r'"((?:[^"\\]|\\.)*)"\Z')
BLOCK_HEADER_RE = re.compile(r"[|>][+-]?\Z")
DOUBLE_ESCAPES = {"\\": "\\", '"': '"', "/": "/", "n": "\n", "t": "\t"}
# A plain value starting with one of these is a YAML indicator (or reserved) and is outside the subset.
INDICATORS = frozenset("-?:,[]{}#&*!|>'\"%@`")
# Plain values YAML resolves to something other than a string (YAML 1.1 and 1.2 core forms), plus
# "=" and "<<", which YAML 1.1 resolves to the value key and merge key types (PyYAML's safe loader
# refuses to construct either as a plain value). The other bare type indicators YAML 1.1 resolves
# ("!", "&" and "*") start with an indicator character and are refused before this pattern is tried.
# The base-2, -8 and -16 integer forms admit a sign (YAML 1.1 reads "+0x1F" as the integer 31), and a
# float that starts with "." admits "_" after its first digit (YAML 1.1 reads ".5_0" as 0.5).
NON_STRING_RE = re.compile(
    r"(=|<<|~|null|Null|NULL|true|True|TRUE|false|False|FALSE|yes|Yes|YES|no|No|NO|on|On|ON|off|Off|OFF"
    r"|y|Y|n|N|[-+]?(?:\.[0-9][0-9_]*|[0-9][0-9_]*(?:[.:][0-9_]*)*)(?:[eE][-+]?[0-9]+)?"
    r"|[-+]?0[xob][0-9a-fA-F_]+|[-+]?\.(?:inf|Inf|INF)|\.(?:nan|NaN|NAN)"
    r"|[0-9]{4}-[0-9]{1,2}-[0-9]{1,2}.*)\Z")

# YAML's printable set (c-printable), restricted further: TAB and LF are the only accepted controls
# (CR, NUL and a leading BOM are refused before any line is read), a byte order mark inside the text
# is refused, and NEL (U+0085), LINE SEPARATOR (U+2028) and PARAGRAPH SEPARATOR (U+2029) are refused
# because a YAML 1.1 parser reads each as a line break while this reader's LF-only split does not.
_REFUSED_PRINTABLE = frozenset((chr(0x85), chr(0x2028), chr(0x2029), chr(0xFEFF)))


def _unreadable_char(line):
    """The first character of a frontmatter line that is outside the readable set, or None."""
    for ch in line:
        if ch == "\t":
            continue
        if ch in _REFUSED_PRINTABLE:
            return ch
        code = ord(ch)
        if 0x20 <= code <= 0x7E or 0xA0 <= code <= 0xD7FF or 0xE000 <= code <= 0xFFFD \
                or 0x10000 <= code <= 0x10FFFF:
            continue
        return ch
    return None


class Result:
    """The reader's decision: status (VALID, INVALID or CANNOT_EVALUATE), a one-line detail, the
    1-based line number it concerns (0 for the whole file) and, for VALID, the {key: text} map."""

    __slots__ = ("status", "detail", "line", "fields")

    def __init__(self, status, detail, line=0, fields=None):
        self.status, self.detail, self.line, self.fields = status, detail, line, fields

    def describe(self):
        where = "line {}: ".format(self.line) if self.line else ""
        return "{} {}{}".format(STATUS_NAMES[self.status], where, self.detail)


def _plain_problem(text, first):
    """The (status, detail) of one plain-value segment, or None when it is inside the subset. first is
    True for the segment on the key line, False for a continuation line."""
    if ": " in text:
        return INVALID, "unquoted ': ' inside a plain value (quote the value or use a block scalar)"
    if text.endswith(":"):
        return INVALID, "line-final ':' inside a plain value (quote the value or use a block scalar)"
    if text[0] in INDICATORS:
        return CANNOT_EVALUATE, "plain value starts with the YAML indicator {!r}".format(text[0])
    if " #" in text or "\t" in text:
        return CANNOT_EVALUATE, "plain value holds a comment marker or a tab"
    if first and NON_STRING_RE.match(text):
        return CANNOT_EVALUATE, "plain value {!r} does not read as a string".format(text)
    return None


def _indent(line):
    return len(line) - len(line.lstrip(" "))


def _read_block(lines, start, header):
    """Read a block scalar's content from lines[start:]. Returns (next index, text) or raises
    _Stop. Content lines are blank or indented by the first content line's indentation (at least 1)."""
    i, n, width, body = start, len(lines), None, []
    while i < n:
        line = lines[i]
        if line.strip(" ") == "":
            if width is not None and len(line) > 0 and _indent(line) > width and not body:
                raise _Stop(CANNOT_EVALUATE, "blank line indented past the block content", i + 1)
            body.append("")
            i += 1
            continue
        ind = _indent(line)
        if width is None:
            if ind == 0:
                break
            if any(len(b) > ind for b in lines[start:i] if b.strip(" ") == ""):
                raise _Stop(CANNOT_EVALUATE, "leading blank line indented past the block content", i + 1)
            width = ind
        if ind < width:
            if ind == 0:
                break
            raise _Stop(CANNOT_EVALUATE, "block scalar line indented less than its content", i + 1)
        body.append(line[width:])
        i += 1
    while body and body[-1] == "":
        body.pop()
    if header[0] == "|":
        text = "\n".join(body)
    else:
        text = re.sub(r"(?<!\n)\n(?!\n)", " ", "\n".join(body))
    if header.endswith("-") or not body:
        return i, text
    return i, text + "\n"


class _Stop(Exception):
    def __init__(self, status, detail, line):
        super().__init__(detail)
        self.status, self.detail, self.line = status, detail, line


def evaluate_text(text):
    """Decide the frontmatter of one decoded skill file. Returns a Result."""
    if "\r" in text or "\x00" in text or text.startswith("\ufeff"):
        return Result(CANNOT_EVALUATE, "a CR, a NUL or a BOM is outside the subset")
    lines = text.split("\n")
    if lines[0] != FENCE:
        return Result(CANNOT_EVALUATE, "the file does not open with a '---' frontmatter line", 1)
    try:
        end = lines.index(FENCE, 1)
    except ValueError:
        return Result(CANNOT_EVALUATE, "no closing '---' frontmatter line", 1)
    body = lines[1:end]
    for j, raw in enumerate(body):
        bad = _unreadable_char(raw)
        if bad is not None:
            return Result(CANNOT_EVALUATE,
                          "character {!r} in the frontmatter is outside the readable set "
                          "(not YAML-printable, a byte order mark, or a non-LF line break)".format(bad),
                          j + 2)
    fields, i = {}, 0
    try:
        while i < len(body):
            line, lineno = body[i], i + 2
            if line.strip(" ") == "":
                i += 1
                continue
            if line.startswith("\t"):
                raise _Stop(INVALID, "a tab starts a key line (YAML forbids tab indentation)", lineno)
            if line.startswith(" "):
                raise _Stop(CANNOT_EVALUATE, "indented line outside any value", lineno)
            m = KEY_LINE_RE.match(line)
            if m is None:
                raise _Stop(CANNOT_EVALUATE, "line is not a 'key: value' line of the subset", lineno)
            key, value = m.group(1), m.group(2)
            if key in fields:
                raise _Stop(INVALID, "duplicate key {!r}".format(key), lineno)
            i += 1
            if value is None or value.strip(" ") == "":
                if i < len(body) and body[i].startswith(" ") and body[i].strip(" "):
                    raise _Stop(CANNOT_EVALUATE, "value of {!r} starts on the next line".format(key), lineno)
                if key not in REQUIRED_KEYS:
                    raise _Stop(CANNOT_EVALUATE, "empty value for {!r}".format(key), lineno)
                fields[key] = ""
                continue
            if value[0] == " ":
                raise _Stop(CANNOT_EVALUATE,
                            "leading space before the value of {!r} (the value-form checks would read "
                            "only the space)".format(key), lineno)
            if value != value.rstrip(" "):
                raise _Stop(CANNOT_EVALUATE, "trailing space after the value of {!r}".format(key), lineno)
            if value[0] == "'":
                q = SINGLE_RE.match(value)
                if q is None:
                    raise _Stop(CANNOT_EVALUATE, "single-quoted value is not one closed line", lineno)
                fields[key] = q.group(1).replace("''", "'")
            elif value[0] == '"':
                q = DOUBLE_RE.match(value)
                if q is None:
                    raise _Stop(CANNOT_EVALUATE, "double-quoted value is not one closed line", lineno)
                raw, out, j = q.group(1), [], 0
                while j < len(raw):
                    if raw[j] == "\\":
                        if raw[j + 1] not in DOUBLE_ESCAPES:
                            raise _Stop(CANNOT_EVALUATE, "escape \\{} is outside the subset".format(
                                raw[j + 1]), lineno)
                        out.append(DOUBLE_ESCAPES[raw[j + 1]])
                        j += 2
                    else:
                        out.append(raw[j])
                        j += 1
                fields[key] = "".join(out)
            elif value[0] in "|>":
                if BLOCK_HEADER_RE.match(value) is None:
                    raise _Stop(CANNOT_EVALUATE, "block scalar header {!r} is outside the subset".format(
                        value), lineno)
                try:
                    i, fields[key] = _read_block(body, i, value)
                except _Stop as stop:
                    raise _Stop(stop.status, stop.detail, stop.line + 1)
            else:
                problem = _plain_problem(value, True)
                if problem:
                    raise _Stop(problem[0], problem[1], lineno)
                parts = [value]
                while i < len(body) and body[i].startswith(" ") and body[i].strip(" "):
                    seg = body[i].strip(" ")
                    problem = _plain_problem(seg, False)
                    if problem:
                        raise _Stop(problem[0], problem[1], i + 2)
                    if body[i].rstrip(" ") != body[i]:
                        raise _Stop(CANNOT_EVALUATE, "trailing space on a plain continuation line", i + 2)
                    parts.append(seg)
                    i += 1
                if i < len(body) and body[i].startswith("\t"):
                    raise _Stop(INVALID, "a tab starts a key line (YAML forbids tab indentation)", i + 2)
                fields[key] = " ".join(parts)
    except _Stop as stop:
        return Result(stop.status, stop.detail, stop.line)
    for key in REQUIRED_KEYS:
        if key not in fields:
            return Result(INVALID, "missing {!r}".format(key))
        if fields[key].strip() == "":
            return Result(INVALID, "empty {!r}".format(key))
    return Result(VALID, "name {!r}".format(fields["name"]), 0, fields)


def evaluate_bytes(data):
    """Decide the frontmatter of one skill file's raw bytes. Returns a Result."""
    if not isinstance(data, (bytes, bytearray)):
        return Result(CANNOT_EVALUATE, "input is not bytes")
    if len(data) > MAX_BYTES:
        return Result(CANNOT_EVALUATE, "file is larger than {} bytes".format(MAX_BYTES))
    try:
        text = bytes(data).decode("utf-8")
    except UnicodeDecodeError as exc:
        return Result(CANNOT_EVALUATE, "not UTF-8 ({})".format(exc.reason))
    return evaluate_text(text)


if __name__ == "__main__":
    # This module is a library with no command line: running it directly (with or without
    # arguments) decides nothing, so it refuses rather than exit 0 having evaluated nothing.
    sys.stderr.write("error: _skill_frontmatter.py is a library with no command line; run "
                     "tools/check_skill_frontmatter.py [--self-test] instead. Nothing was "
                     "evaluated (cannot evaluate).\n")
    sys.exit(2)
