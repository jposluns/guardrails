#!/usr/bin/env python3
"""Stale pending-claim check: a record sentence that says a named pull request is pending, open, under
review, stacked on or waiting, while that pull request has merged or closed.

  check_pending_pr_claims.py --check-inputs           offline: validate the declaration and every
                                                     declared surface, list the candidate claims;
                                                     never reads pull request state
  check_pending_pr_claims.py --all --live             read every declared surface and the live state
                                                     of every bound pull request
  check_pending_pr_claims.py --draft PATH --target T  judge the complete proposed content PATH as the
                                                     declared surface T (live)
  check_pending_pr_claims.py --staged --live          judge the staged blob of every declared surface
                                                     the index changes (live)
  check_pending_pr_claims.py --self-test              synthetic fixtures and stubbed gh processes
  check_pending_pr_claims.py --self-test --execution-report ABS_PATH
  any other argument list is refused with exit 2

Exit codes: 0 every bound claim was evaluated and none contradicts its pull request's lifecycle
(advisory warnings may remain); 1 one or more hard findings, evaluation otherwise complete; 2 the
evaluation is incomplete (pull request state could not be read, a binding is ambiguous, a declared
surface is missing or malformed, or a usage error). Definite findings are printed alongside the
unknowns. A run with no candidates means only that no supported current claim was found.

The declaration is .aiqt/pending-pr-claims.toml; the contract, grammar and residuals are in
.aiqt/pending-pr-claims.md. State is read by tools/_pr_state.py (gh api, host pinned to github.com);
a caller-supplied state map is never accepted as authority. The --check-inputs mode is the only
mode the Quality workflow runs: it is deterministic and offline and does not assess currency.

A negation, past-tense or conditional cue directly before a claim (not waiting for #17 to merge, had
been waiting, Neither #16 nor #17 is open, If #17 is still open) never exempts it: when the pull
request has merged or closed, the claim is a warning (exit 0, a WARNING line naming the sentence and
the pull request) instead of a finding. Only a claim with no such cue is a finding.

Every pull request reference of a declared surface that reaches no evaluated record unit, whatever
dropped it (a fenced block, indented code, an HTML comment, a link reference definition, or a gap in
a reader), is a WARNING naming the file, line and reference as not evaluated, and the block kind when
the classifier hid the line (no state read, exit 0 with no finding), so a dropped claim never passes
silently. The documented exemptions alone are not scanned: a fenced block whose info string starts
with `example` or `history`, a blockquote with a historical lead-in, and strikethrough, historical
spans and single-line comments inside a line read in full. The warning is keyed by pull request, by
design: one evaluated mention of a pull request suppresses it for that pull request's other dropped
occurrences in the same file.

Delimiters inside code spans are inert: each text is given one code-span reading (CommonMark
backtick-run matching) before normalization, and every exemption and link recognizer (comments,
strikethrough, historical spans, issue references, inline, reference and HTML links) runs only on the
text outside code spans, while the text inside them is still read for claims. Angle brackets around a
GitHub URL are a spelling, dropped inside code spans too, so a backticked autolink still binds. A code
span directly after a historical lead-in is its quotation whatever its backtick run length. Block
classification reads code spans across a paragraph's lines before it looks for an inline comment
opener on a paragraph or list item line, so a code span wrapped around `<!--` keeps it inert.

RESIDUALS (disclosed; the full list is in the contract): a prose grammar cannot be complete, and this
one is partial. The self-test covers only its rule and fix rows. Not caught: a claim before its
reference outside a Status cell (still pending: #17), words between a reference and its phrase (PR
#17, which fixes the parser, is still open), a Status cell whose state word is not first, a pull URL
suffix other than /files, /commits or /checks, a reference split inside a word, a current claim
added inside a blockquote that has a historical lead-in, and an issue claim (issue references are
never read). A cue further back than the words directly before a claim is not seen, so that claim
stays a finding. An imperative-looking `Open #N` at a clause start warns instead of finding.

RUNNING TIME is bounded in two layers.

First, every step that reads a document is meant to run in time linear in its size; that is shown only
for the shapes the self-test times. Its growth rows time the whole check at two sizes, eight times
apart, on seventeen shapes: unclosed comment, link bracket, anchor and quotation openers; complete
anchors on one line; an anchor tag with many href attributes; many uses of one long reference link
definition, on one line, on many lines and in many paragraphs; a line of list markers; lazy lines under
many open list items; claims, and joined references, in one clause; and four paragraph shapes (closed
comments, unclosed comment openers, code spans beside comments, comments cut across lines). A row
passes when the time grows at most sixteen times, and its red leg, with that shape's quadratic form put
back, must grow more. A shape the rows do not time is not shown to be linear. One table of a document's
link reference definitions serves all its units and scanned lines, so a definition's URL is read once.

Where a URL is still copied into the text (a definition URL holding a parenthesis, a square bracket or
a backtick, copied into each use, or a placeholder the markdown-link step leaves, as in a reference link
whose label ends in a closing parenthesis written after a link text in brackets, an opening parenthesis
and a space, which that step reads as an inline link ending at that parenthesis, given its URL back), n uses of a URL of length L read n * L characters, and the clause they land in is quoted
as copied. Those copies are bounded instead: past eight characters of URL per byte of the document (its
UTF-8 bytes) plus 65,536 the document is cannot-evaluate (exit 2), and two growth rows show that bound reached on each
copy path, with their red legs (the bound removed) growing more.

Second, hard bounds on input and report. A declared surface, a draft or a staged blob larger than
MAX_BYTES (64 KiB) is cannot-evaluate (exit 2), a defence in depth for a superlinear path no row has
found. The bound was set from the real records: the largest declared surface at any commit of this
repository was 24,834 bytes (TODO.md), and 64 KiB is 2.6 times that; the self-test fails when a
declared surface passes half the bound. Each finding, warning and cannot-evaluate line quotes its whole
clause or line, so n references bound in one clause of length L (n claims, one claim naming n joined
references, or n uses of one reference link) would give n * L characters; the lines built for one
document are bounded instead: past REPORT_MAX_CHARS (4 Mi characters; 16 MiB at four bytes a
character) the document is cannot-evaluate (exit 2), with a line naming the bound, and its later lines
are not built (a line is built only when the clause it quotes fits in what is left).
On the worst report shapes then known at 64 KiB (n uses of a long pull URL reference link, joined
claims, hidden references on one line) a whole run peaked at about 32 MiB resident on the host it was
measured on (2026-11-20; before the report bound, more than 2 GiB, and a 1 GiB limit ended the run with
an uncaught MemoryError). The report bound runs after collection, so collection is bounded too, by an
allowance of COLLECT_MAX_CHARS (4 Mi characters) per document, past which the document is
cannot-evaluate (exit 2), naming the bound, before the entry that would pass it is kept. The allowance
charges these fields and no others: a record unit (a Markdown paragraph, list item, table cell or
blockquote, or a TOML string value) 32 characters plus its location, not its text; a claim entry 32
plus its quoted clause and its phrase; an ambiguous-phrase entry 32 plus its quoted clause; an
unevaluated-reference entry 32 plus its location and its quoted line. A string the entry charged just
before also held (the same object) is not counted again, so the entries of one line or clause, which
share one copy of its quoted text and location, count it once, while a location built anew for each
unit (a long header cell over many rows, a long TOML key path over many values) is counted each time.
The allowance counts characters of those fields; it is not a measure of the memory the process uses. It
does not count the input bytes and decoded text, the parsed TOML document, the units' texts, the
reader's and tokenizer's temporaries or Python's object overhead (the 32 per entry is a nominal charge,
not a measured size), and a character can take up to four bytes. No bound is set on the memory of the
whole process: the peaks given here were observed, not bounded. With the quotes shared, 9,000 hidden
references on one line with leading white space and non-BMP text (52,907 bytes) peaked at about 52 MiB
(2026-11-20; before, a 1 GiB limit ended the run with an uncaught MemoryError); a path no one has found
can need more. Reading pull request state (tools/_pr_state.py), whose reasons can quote the response,
is outside these bounds.

NESTING is bounded too. No walk over a document recurses: Markdown blocks, blockquotes and list items
are read in loops over the lines, and the walk over a parsed TOML document keeps its own stack and builds a
value's location only at that value. A TOML table or array nested more than TOML_MAX_DEPTH (100) tables
and arrays deep is cannot-evaluate (exit 2), naming the bound, never a traceback. The bound is enforced
before the TOML reader runs, by one linear scan of the document's text (_toml_nesting) that counts the
depth the reader will build (table headers, with a level for every array of tables an earlier header
named that a later header goes through, also when the header's closing bracket is missing, since the
reader builds its table first; dotted keys; inline arrays and tables; strings and comments are stepped
over), so the reader is never given a key of more than TOML_MAX_DEPTH + 1 parts, a table or
inline nesting more than TOML_MAX_DEPTH deep: its copying of a dotted key's prefixes (quadratic in the
key's parts; a 16,000-part key held about 1 GiB) and its recursion on inline nesting stay bounded. For a
document the reader reads, the scan's depth is the parsed document's (the self-test compares them over a
generated corpus), so it refuses no document the walk would read and passes none the walk would refuse;
for a document the reader refuses, it is never lower than the depth the reader builds before refusing it
(the self-test measures that depth by instrumenting the reader over generated malformed documents); the
walk keeps its own refusal as a defence in depth.

IN-BAND MARKS. The check writes a few characters into a text as its own marks and reads them back by
their spelling (IN_BAND_MARKS: NUL and U+E000 to U+E004). A document that carries one, raw or decoded
from a TOML escape in a string value or a key, is cannot-evaluate (exit 2) before any of it is read, so
no input can spell a mark; other private-use characters are text.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: check_pending_pr_claims.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import bisect
import errno
import os
import re
import stat
import subprocess
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _pr_state  # noqa: E402  (the lifecycle reader beside this checker)

ROOT = Path(__file__).resolve().parents[1]
CONFIG_REL = ".aiqt/pending-pr-claims.toml"
# The size bound on a declared surface, a draft and a staged blob: larger is cannot-evaluate (exit 2). It
# is set from the real records, not from what the reader could take: the largest declared surface at
# any commit of this repository was 24,834 bytes (TODO.md; changelog.toml at most 2,728), and 64 KiB is
# 2.6 times that. With the report bound (REPORT_MAX_CHARS) it caps the residual superlinear paths (module
# docstring, RUNNING TIME). The self-test fails when a declared surface grows past half the bound, so the
# bound is measured again before a record outgrows its margin. tools/apply_record_draft.py reads an
# existing destination up to its own bound (EXISTING_MAX_BYTES there), so an older, larger record can
# still be replaced.
MAX_BYTES = 64 * 1024
# The nesting bound on a TOML document: a table or array nested more than TOML_MAX_DEPTH tables and arrays
# below the top-level table (itself counted, the top-level table not) is cannot-evaluate (exit 2), naming
# the bound. A scan of the text before the TOML reader (_toml_nesting) refuses the depth the reader would
# build, so the reader never parses a key of more than TOML_MAX_DEPTH + 1 parts, a table or inline nesting
# past the bound (its cost per key is quadratic in the key's parts, and it recurses on inline arrays and
# tables); the walk over the parsed document, iterative, refuses the same depth again as a defence in
# depth. The bound keeps every document that is read well inside the depth the reader parses, and it is
# below the depth at which the recursive walk this replaced ended in an uncaught RecursionError, so no
# document that walk failed on is read now. The real records nest at most a few levels.
TOML_MAX_DEPTH = 100
FORMATS =("toml-strings", "markdown")
ROLES = ("source", "generated-view")
TOP_KEYS = {"schema-version", "limits", "surface"}
LIMIT_KEYS = {"max-prs": (1, 1000), "call-timeout-seconds": (1, 60), "deadline-seconds": (1, 600)}
SURFACE_KEYS = {"path", "format", "repository", "role"}
SURFACE_OPTIONAL = {"expected-repository", "generated-from", "status-column", "note"}

HARD, WARN, AMBIGUOUS, QUOTE = "hard", "warning", "ambiguous", "quote"
# A pull request reference found only in text the Markdown block classifier hides (a warning, no read).
HIDDEN = "hidden"


class InputError(Exception):
    """A declaration or declared surface that cannot be evaluated (exit 2)."""


# ---------------------------------------------------------------------------------------------------
# The declaration.

# The characters the nesting scan stops at outside strings and comments, and where each kind of string
# ends (a backslash escape is stepped over in a basic string; a multi-line closer may take up to two more
# quotes, which are the string's).
_TOML_SCAN_RE = re.compile(r"\"\"\"|'''|[\"'#\[\]{}=,.\n]")
_TOML_BASIC_END_RE = re.compile(r"\\[^\n]|[\"\n]")
_TOML_LITERAL_END_RE = re.compile(r"['\n]")
_TOML_ML_BASIC_END_RE = re.compile(r"\\[\s\S]|\"\"\"\"{0,2}")
_TOML_ML_LITERAL_END_RE = re.compile(r"''''{0,2}")
_TOML_STRING_ENDS = {'"""': _TOML_ML_BASIC_END_RE, "'''": _TOML_ML_LITERAL_END_RE,
                     '"': _TOML_BASIC_END_RE, "'": _TOML_LITERAL_END_RE}
# A table header's key as the TOML reader reads it (TOML 1.0, tomllib): parts joined by dots, spaces and
# tabs around each; a part is a bare key, a literal string or a one-line basic string with the escapes
# TOML 1.0 defines (a control character other than a tab is in neither kind of string).
_TOML_HEADER_WS_RE = re.compile(r"[ \t]*")
_TOML_KEY_PART_RE = re.compile(
    r"([A-Za-z0-9_-]+)"
    r"|'([^'\x00-\x08\x0a-\x1f\x7f]*)'"
    r'|"((?:[^"\\\x00-\x08\x0a-\x1f\x7f]|\\(?:[btnfr"\\]|u[0-9A-Fa-f]{4}|U[0-9A-Fa-f]{8}))*)"')
_TOML_KEY_ESCAPE_RE = re.compile(r"\\(?:u([0-9A-Fa-f]{4})|U([0-9A-Fa-f]{8})|(.))")
_TOML_KEY_ESCAPES = {"b": "\b", "t": "\t", "n": "\n", "f": "\f", "r": "\r", '"': '"', "\\": "\\"}
# The nesting scan runs before the TOML reader (True). The self-test sets it False, for the red legs of
# its rows (the reader's own cost on deep dotted keys and headers, and the walk's refusal after it).
TOML_PRE_SCAN = True
# The nesting scan counts the level of each array of tables an earlier header named that a later header
# goes through (True). The self-test sets it False, for the red legs of its rows (the scan that counted
# only the array of tables its own header named).
TOML_SCAN_ARRAYS = True
# The nesting scan counts and bounds the table or array of tables an unclosed header names, as the reader
# builds it before refusing the header (True). The self-test sets it False, for the red legs of its rows
# (the scan that read an unclosed header by its syntax alone, losing the levels of earlier arrays of
# tables and the header's own array).
TOML_SCAN_UNCLOSED = True
# How many documents have been given to the TOML reader (the self-test reads it to show which ones the
# scan kept from the reader).
_TOML_READS = [0]


def _toml_key_part(found):
    """The key a match of _TOML_KEY_PART_RE names, decoded as the TOML reader decodes it, or None for a
    \\u or \\U escape that is not a Unicode scalar value (the reader refuses it)."""
    bare, literal, basic = found.groups()
    if basic is None:
        return bare if literal is None else literal
    if "\\" not in basic:
        return basic
    pieces, last = [], 0
    for escape in _TOML_KEY_ESCAPE_RE.finditer(basic):
        pieces.append(basic[last:escape.start()])
        digits = escape.group(1) or escape.group(2)
        if digits is None:
            pieces.append(_TOML_KEY_ESCAPES[escape.group(3)])
        else:
            point = int(digits, 16)
            if 0xD800 <= point <= 0xDFFF or point > 0x10FFFF:
                return None
            pieces.append(chr(point))
        last = escape.end()
    pieces.append(basic[last:])
    return "".join(pieces)


def _toml_header(text, position, array, arrays, bound):
    """Reads the table header whose key starts at position (just past its "[", or its "[[" when array, an
    array-of-tables header) as the TOML reader reads it. Returns (the depth of the table it opens, the
    position past its closing bracket or brackets); or (that depth, None) when its key is read but its
    closing bracket or brackets are missing (the reader builds the table first, then refuses the header
    and reads nothing after it); or (None, the position of the part that passes bound); or None when the
    reader refuses the header's key (it then builds nothing from the header and reads nothing after it).

    The depth follows TOML 1.0's tables and arrays of tables as the reader builds them: each part of the
    key is a table one level below the one before, except a part that names an array of tables, which is
    two levels, the array and its last element (the table the reader goes into); an array-of-tables
    header's last part always names one (the array it creates or appends an element to). Which parts name
    arrays of tables is known from the earlier headers: arrays is a tree of the names the headers have
    gone through (a name maps to [whether it names an array of tables, the names below it]). An
    array-of-tables header starts a new, empty element, so what its array named below the last element is
    forgotten. Nothing else makes an array a header can go through: an inline array is immutable, and a
    dotted key cannot go through an array of tables (the reader refuses both)."""
    depth, names, node, start = 0, arrays, None, position
    while True:
        start = _TOML_HEADER_WS_RE.match(text, position).end()
        found = _TOML_KEY_PART_RE.match(text, start)
        name = None if found is None else _toml_key_part(found)
        if name is None:
            return None
        node = names.setdefault(name, [False, {}])
        names = node[1]
        depth += 2 if node[0] and TOML_SCAN_ARRAYS else 1
        if depth > bound:
            return None, start
        position = _TOML_HEADER_WS_RE.match(text, found.end()).end()
        if not text.startswith(".", position):
            break
        position += 1
    # The reader builds the table or array of tables the key names before it looks for the closing
    # bracket or brackets (tomllib), so the last level is counted and bounded first: a header whose
    # closer is missing or partial still reaches this depth before the reader refuses it.
    closer = "]]" if array else "]"
    closed = text.startswith(closer, position)
    if not (closed or TOML_SCAN_UNCLOSED):
        return None
    if array and not (node[0] and TOML_SCAN_ARRAYS):
        depth += 1
        if depth > bound:
            return None, start
    if not closed:
        return depth, None
    if array:
        node[0], node[1] = True, {}
    return depth, position + len(closer)


def _toml_depth(text, bound):
    """(the deepest table or array the syntax of text nests, None), or (None, (position, what)) for the
    first table header, dotted key or inline array or table that puts a table or array more than bound
    deep. One pass over text, each character read once (a header's key is read once by _toml_header),
    holding a stack of the open inline arrays and tables; strings and comments are stepped over whole. The
    depth it counts is the depth the TOML reader builds (TOML 1.0): a header's tables and arrays of tables
    (_toml_header, which counts the level of every array of tables an earlier header named that it goes
    through); a key's parts before its last, below its table; one per inline array or table. For a
    document the reader reads it is the parsed document's depth, no more and no less (the self-test
    compares the two over a generated corpus), so the scan refuses exactly the documents the walk in
    toml_units would refuse after parsing; and since it stops at the first part past the bound, the reader
    is never given a key of more than bound + 1 parts, inline nesting more than bound deep or a table more
    than bound deep, which bounds the reader's work per key (it copies a key's prefixes, quadratic in its
    parts) and its recursion on inline nesting. For a document the reader refuses, the depth is never lower
    than the depth the reader builds before it refuses it (the self-test measures that depth by
    instrumenting the reader over a generated corpus of malformed documents): a header whose key the
    reader reads but whose closing bracket or brackets are missing is counted and bounded as a header that
    is closed (the reader builds its table before it looks for the closer), and the rest of its line, like
    a header whose key the reader refuses (it builds nothing from it), is counted as its syntax gives (its
    parts, plus one for an array-of-tables header); other text that is not TOML may be scanned loosely,
    never below what the reader builds from it; the reader refuses it and reads nothing after it."""
    deepest = 0
    arrays = {}           # the arrays of tables earlier headers named (_toml_header)
    stack = []            # the open inline arrays and tables: (bracket, the depth of that array or table)
    table = 0             # the depth of the table the latest header opened (the top-level table is 0)
    mode = "key"          # "key" (a key is read, or at a line start a header), "header" or "value"
    base, parts = 0, 1    # in key mode: the depth of the table holding the key, its parts so far
    header_extra = 0      # in header mode: 1 for an array-of-tables header
    level = 0             # in value mode: the depth an inline array or table opened here would have
    position, end = 0, len(text)
    while position < end:
        found = _TOML_SCAN_RE.search(text, position)
        if found is None:
            break
        token, position = found.group(0), found.end()
        if token == "#":
            newline = text.find("\n", position)
            position = end if newline < 0 else newline
            continue
        if token[0] in "\"'":
            closer = _TOML_STRING_ENDS[token]
            while True:
                stop = closer.search(text, position)
                if stop is None:
                    return deepest, None     # an unclosed string: the reader refuses the text
                if stop.group(0)[0] == "\\":
                    position = stop.end()
                    continue
                # A single-line string ends at its quote; at a line end it is unclosed (the reader
                # refuses it) and the line end is read as a line end.
                position = stop.end() if stop.group(0) != "\n" else stop.start()
                break
            continue
        if mode == "header":
            # A header the reader refuses (_toml_header gave None, or an unclosed header's depth, already
            # counted): its parts, as its syntax gives them.
            if token == ".":
                parts += 1
                if parts + header_extra > bound:
                    return None, (found.start(), "a table header")
            elif token == "]":
                if header_extra and text.startswith("]", position):
                    position += 1
                table = parts + header_extra
                deepest = max(deepest, table)
                mode, base, parts = "key", table, 1
            elif token == "\n":
                mode, base, parts = "key", table, 1
            continue
        if mode == "key":
            if token == ".":
                parts += 1
                # The key's parts before its last are tables, one level each below its table.
                if base + parts - 1 > bound:
                    return None, (found.start(), "a dotted key")
            elif token == "=":
                deepest = max(deepest, base + parts - 1)
                mode, level = "value", base + parts
            elif token == "[" and not stack:
                header_extra = 1 if text.startswith("[", position) else 0
                header = _toml_header(text, position + header_extra, header_extra == 1, arrays, bound)
                if header is not None and header[0] is None:
                    return None, (header[1], "a table header")
                if header is None or header[1] is None:
                    # The reader refuses this header (an unclosed one after building its table, counted
                    # here), and the rest of the line is read loosely by its syntax.
                    if header is not None:
                        deepest = max(deepest, header[0])
                    position += header_extra
                    mode, parts = "header", 1
                    if 1 + header_extra > bound:
                        return None, (found.start(), "a table header")
                else:
                    table, position = header
                    deepest = max(deepest, table)
                    mode, base, parts = "key", table, 1
            elif token == "}" and stack and stack[-1][0] == "{":
                stack.pop()
                mode, level = "value", (stack[-1][1] + 1) if stack else table + 1
            elif token == "\n" and not stack:
                base, parts = table, 1
            continue
        # Value mode: a dot is part of a number or a date, and a line end outside any inline array or
        # table ends the key/value pair.
        if token in "[{":
            if level > bound:
                return None, (found.start(), "an inline array or table")
            deepest = max(deepest, level)
            stack.append((token, level))
            if token == "{":
                mode, base, parts = "key", level, 1
            else:
                level += 1
        elif token in "]}":
            if stack:
                stack.pop()
            level = (stack[-1][1] + 1) if stack else table + 1
        elif token == ",":
            if stack and stack[-1][0] == "{":
                mode, base, parts = "key", stack[-1][1], 1
            elif stack:
                level = stack[-1][1] + 1
        elif token == "\n" and not stack:
            mode, base, parts = "key", table, 1
    return deepest, None


def _toml_nesting(text, label):
    """None, or the reason text is refused before the TOML reader runs: a table header, a dotted key or an
    inline array or table that puts a table or array more than TOML_MAX_DEPTH deep (_toml_depth)."""
    _deepest, refusal = _toml_depth(text, TOML_MAX_DEPTH)
    if refusal is None:
        return None
    position, what = refusal
    return ("{}: line {}: {} nests tables and arrays more than {} deep (TOML_MAX_DEPTH), a bound on "
            "nesting, found before the TOML reader ran; it was not read".format(
                label, text.count("\n", 0, position) + 1, what, TOML_MAX_DEPTH))


def _parse_toml(text, label):
    """(document, None), or (None, reason) for text that is not valid TOML or is nested too deeply to be
    read. The one tomllib call site: the nesting scan (_toml_nesting) runs first, so a document nested past
    TOML_MAX_DEPTH by its syntax never reaches the reader; a bare ValueError (an integer past the
    int-string limit), a RecursionError and a MemoryError (kept as a defence in depth) are each handled
    here, never re-raised."""
    why = _toml_nesting(text, label) if TOML_PRE_SCAN else None
    if why is not None:
        return None, why
    _TOML_READS[0] += 1
    try:
        return tomllib.loads(text), None
    except RecursionError:
        return None, ("{}: nests inline arrays and tables too deeply for the TOML reader (it passed Python's "
                      "recursion limit; this check reads at most {} nested tables and arrays, TOML_MAX_DEPTH), "
                      "a bound on nesting; it was not read".format(label, TOML_MAX_DEPTH))
    except (tomllib.TOMLDecodeError, ValueError, MemoryError) as exc:
        return None, "{}: is not valid TOML ({}: {})".format(label, type(exc).__name__, exc)


def load_config(root):
    """The validated declaration as a dict; InputError on any problem (fail closed)."""
    data, why = _parse_toml(_read_text(Path(root) / CONFIG_REL, CONFIG_REL), CONFIG_REL)
    if why is not None:
        raise InputError(why)
    return validate_config(data)


def validate_config(data):
    if set(data) != TOP_KEYS:
        raise InputError("{}: top-level keys must be exactly {} (got {})".format(
            CONFIG_REL, sorted(TOP_KEYS), sorted(data)))
    if type(data["schema-version"]) is not int or data["schema-version"] != 1:
        raise InputError("{}: schema-version must be exactly the integer 1".format(CONFIG_REL))
    limits = data["limits"]
    if not isinstance(limits, dict) or set(limits) != set(LIMIT_KEYS):
        raise InputError("{}: [limits] keys must be exactly {}".format(CONFIG_REL, sorted(LIMIT_KEYS)))
    for key, (low, high) in LIMIT_KEYS.items():
        if type(limits[key]) is not int or not low <= limits[key] <= high:
            raise InputError("{}: limits.{} must be an integer from {} to {}".format(
                CONFIG_REL, key, low, high))
    surfaces = data["surface"]
    if not isinstance(surfaces, list) or not surfaces:
        raise InputError("{}: [[surface]] must be a non-empty array of tables".format(CONFIG_REL))
    seen = set()
    for index, row in enumerate(surfaces):
        where = "{}: surface[{}]".format(CONFIG_REL, index)
        if (not isinstance(row, dict) or not SURFACE_KEYS <= set(row)
                or not set(row) <= SURFACE_KEYS | SURFACE_OPTIONAL):
            raise InputError("{}: keys must be {} (optionally {})".format(
                where, sorted(SURFACE_KEYS), sorted(SURFACE_OPTIONAL)))
        for key, value in row.items():
            if not isinstance(value, str) or not value or any(ord(ch) < 32 for ch in value):
                raise InputError("{}: {} must be a non-empty single-line string".format(where, key))
        path = row["path"]
        if os.path.isabs(path) or ".." in Path(path).parts or "\\" in path or path in seen:
            raise InputError("{}: path {!r} must be a unique repo-relative path with no '..'".format(
                where, path))
        seen.add(path)
        if row["format"] not in FORMATS:
            raise InputError("{}: format must be one of {}".format(where, list(FORMATS)))
        if row["role"] not in ROLES:
            raise InputError("{}: role must be one of {}".format(where, list(ROLES)))
        if row["repository"] != "origin" and not _pr_state.valid_repo(row["repository"]):
            raise InputError("{}: repository must be \"origin\" or OWNER/REPO".format(where))
        if "expected-repository" in row and not _pr_state.valid_repo(row["expected-repository"]):
            raise InputError("{}: expected-repository must be OWNER/REPO".format(where))
        if "status-column" in row and row["format"] != "markdown":
            raise InputError("{}: status-column applies to a markdown surface only".format(where))
        if row["role"] == "generated-view" and "generated-from" not in row:
            raise InputError("{}: a generated-view surface must name generated-from".format(where))
    return data


_READ_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0)


def _read_bytes(path, label):
    """The bytes of a regular, non-symlink file of at most MAX_BYTES; InputError otherwise. The file is
    opened once (O_NOFOLLOW: a symlink is refused; O_NONBLOCK: a FIFO is not waited on) and its type
    is checked on that descriptor, so the bytes read are the bytes of the file whose type was checked."""
    try:
        handle = os.open(path, _READ_FLAGS)
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise InputError("{}: is not a regular file (a symlink or directory is refused)".format(label))
        raise InputError("{}: cannot be read ({})".format(label, exc.strerror or type(exc).__name__))
    try:
        if not stat.S_ISREG(os.fstat(handle).st_mode):
            raise InputError("{}: is not a regular file (a symlink or directory is refused)".format(label))
        with os.fdopen(handle, "rb", closefd=False) as source:
            data = source.read(MAX_BYTES + 1)
    except OSError as exc:
        raise InputError("{}: cannot be read ({})".format(label, exc.strerror or type(exc).__name__))
    finally:
        os.close(handle)
    if len(data) > MAX_BYTES:
        raise InputError("{}: larger than {} bytes".format(label, MAX_BYTES))
    return data


# The characters this check writes into a text as its own marks: NUL (a reference link's URL placeholder,
# REFERENCE_TARGET_RE), U+E000 and U+E001 (a pull request reference's token, REF_MARK), U+E002 (an escaped
# table pipe, _cells), U+E003 (an issue number's '#', _issue) and U+E004 (code-span content as an
# exemption recognizer sees it, CODE_FILLER). A mark is read back by its spelling alone, so no input may
# spell one: a document carrying any of them, raw or decoded from a TOML escape (a key or a string value),
# is cannot-evaluate (exit 2) before any of it is read. A new mark must be added here; the self-test fails
# when the source writes a NUL or private-use character this set does not hold.
IN_BAND_MARKS = "\x00"
IN_BAND_MARK_RE = re.compile("[" + IN_BAND_MARKS + "]")


def _refuse_marks(text, where, lines=False):
    """InputError (cannot evaluate) when text carries a mark of IN_BAND_MARKS, naming where, the mark and,
    when lines, the line it is on; else nothing."""
    found = IN_BAND_MARK_RE.search(text)
    if found is not None:
        if lines:
            where = "{}: line {}".format(where, text.count("\n", 0, found.start()) + 1)
        raise InputError("{}: carries U+{:04X}, a character this check reserves for its own marks "
                         "(IN_BAND_MARKS); it was not read".format(where, ord(found.group(0))))


def _decode(data, label):
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise InputError("{}: is not UTF-8".format(label))
    if "\x00" in text:
        raise InputError("{}: carries a NUL character".format(label))
    _refuse_marks(text, label, lines=True)
    return text


def _read_text(path, label):
    return _decode(_read_bytes(path, label), label)


# ---------------------------------------------------------------------------------------------------
# Record units: complete paragraphs, list items, table cells and decoded TOML strings, with locations.

def _too_deep(label):
    return InputError("{}: nests tables and arrays more than {} deep (TOML_MAX_DEPTH), a bound on nesting; "
                      "it was not read".format(label, TOML_MAX_DEPTH))


def toml_units(text, label, budget=None):
    """[(location, prose, context)] for every decoded string value of a TOML document, in document order.
    Each unit is charged to budget (a _CollectBudget, or None) before it is kept: its location names its
    whole key path, so a long key path over many string values is counted once per value. The walk is
    iterative and builds a location only at a string value, from one shared list of key path pieces, so
    reaching a value costs its own path, not the paths of every table above it; a table or array nested
    more than TOML_MAX_DEPTH deep is InputError (cannot evaluate), naming the bound, and so is a key or a
    string value that carries a mark of IN_BAND_MARKS (a TOML escape can spell one)."""
    data, why = _parse_toml(text, label)
    if why is not None:
        raise InputError(why)
    units = []
    # The pieces of the key path to the value being visited, joined only at a string value; a key below
    # an empty path is written alone, any other key after a dot, an array index in brackets.
    pieces = []
    # One frame per open table or array, the top-level table first: (an iterator over its (key, value)
    # or (index, value) pairs, whether it is an array, how many pieces its path holds, whether that path
    # is non-empty). A frame's depth is its place on the stack, the top-level table at 0.
    stack = [(iter(data.items()), False, 0, False)]
    while stack:
        items, is_array, held, named = stack[-1]
        del pieces[held:]
        item = next(items, None)
        if item is None:
            stack.pop()
            continue
        key, value = item
        if is_array:
            pieces.append("[{}]".format(key))
        elif named:
            _refuse_marks(key, "{}: a key".format(label))
            pieces.append(".")
            pieces.append(key)
        else:
            _refuse_marks(key, "{}: a key".format(label))
            pieces.append(key)
        if isinstance(value, str):
            location = "{}: {}".format(label, "".join(pieces))
            # A TOML escape (\u0000, ) can spell a mark the raw text cannot hold.
            _refuse_marks(value, location)
            if budget is not None:
                budget.charge(location)
            units.append((location, value, "prose"))
        elif isinstance(value, (dict, list)):
            if len(stack) > TOML_MAX_DEPTH:
                raise _too_deep(label)
            child_named = named or is_array or bool(key)
            if isinstance(value, dict):
                stack.append((iter(value.items()), False, len(pieces), child_named))
            else:
                stack.append((iter(enumerate(value)), True, len(pieces), child_named))
    return units


# A fence run, matched after a line's indentation; whether it opens a fence is block_context's call.
FENCE_OPEN_RE = re.compile(r"(`{3,}|~{3,})")
# The deepest indentation, in columns past the containing block's content column (the left margin, or a
# list item's content column), at which a fence opens or closes (CommonMark); a deeper line is
# indented code, or paragraph text when a paragraph is open.
FENCE_MAX_RELATIVE_INDENT = 3
# A maximal backtick run: an inline code span opens at one and closes at the next run of the same length.
BACKTICK_RUN_RE = re.compile(r"`+")
# A link reference definition ([label]: URL "title"); it is not record prose.
LINK_DEFINITION_RE = re.compile(
    r" {0,3}\[([^\]]{1,999})\]:\s*<?([^\s<>]+)>?(?:\s+(?:\"[^\"]*\"|'[^']*'|\([^)]*\)))?\s*$")
# A full ([text][label]), collapsed ([text][]) or shortcut ([label]) reference link.
REFERENCE_LINK_RE = re.compile(r"(!?)\[([^\[\]]*)\](?:\[([^\[\]]*)\])?(?![(:])")
HEADER_EMPHASIS_RE = re.compile(r"^(\*{1,2}|_{1,2}|`)(.+)\1$")
QUOTE_RE = re.compile(r" {0,3}>[ ]?(.*)")
HEADING_RE = re.compile(r" {0,3}#{1,6}(?:\s+(.*))?$")
# A list item: indentation and the space after the marker are spaces and tabs only (CommonMark).
ITEM_RE = re.compile(r"[ \t]*(?:[-*+]|[0-9]{1,9}[.)])[ \t]+(?:\[[ xX]\][ \t]+)?(.*)")
# A list item's marker alone: its end is where ITEM_RE's text group starts.
ITEM_MARKER_RE = re.compile(r"[ \t]*(?:[-*+]|[0-9]{1,9}[.)])[ \t]+(?:\[[ xX]\][ \t]+)?")
# Markdown indentation and blank lines are made of spaces and tabs only; any other whitespace (a
# no-break space, for example) is text, so it never indents a line or makes it a fence.
MD_SPACE = " \t"
# The info strings (first word, case-insensitive) that mark a fenced block as an example: a pull
# request reference only inside such a block raises no hidden-reference warning.
EXAMPLE_FENCE_INFO = ("example", "history")
SEPARATOR_CELL_RE = re.compile(r"\s*:?-{3,}:?\s*")
HISTORY_INTRO_RE = re.compile(r"(?i)(?:earlier note|previously recorded)\s*:\s*$")
HISTORY_LEAD_RE = re.compile(r"(?i)\s*(?:earlier note|previously recorded)\s*:")


def _cells(line):
    body = line.strip()
    if body.startswith("|"):
        body = body[1:]
    if body.endswith("|") and not body.endswith("\\|"):
        body = body[:-1]
    return [cell.replace("\ue002", "\\|").strip() for cell in body.replace("\\|", "\ue002").split("|")]


def _header_name(cell):
    """A header cell's name with one wrapping emphasis or code span removed (**Status** is Status)."""
    match = HEADER_EMPHASIS_RE.match(cell)
    return match.group(2).strip() if match else cell


# A definition URL in a resolved reference link is written as a placeholder, NUL, the URL's index in
# the document's targets (_Definitions), NUL, so each use costs its own length, not the URL's (no
# document text holds a NUL: _decode refuses one, and toml_units one decoded from an escape; IN_BAND_MARKS). Only a URL of characters the steps before
# the markdown-link step and the code-span reading treat alike (PLACEHOLDER_URL_RE) is written so; any
# other URL is copied (and charged to the URL copy bound, below).
REFERENCE_TARGET_RE = re.compile("\x00([0-9]+)\x00")
PLACEHOLDER_URL_RE = re.compile(r"[^\s()<>\[\]`\x00]+")
# Definition URLs are written as placeholders (True). The self-test sets it False, copying each URL into
# every use, for a growth row's red leg.
REFERENCE_PLACEHOLDERS = True


class _LinkedProse(str):
    """Prose whose reference links _resolve_reference_links rewrote with placeholders: targets[k] is
    (URL, stand-in) for the placeholder with index k (_normalize reads them back)."""
    targets = ()


def _link_stand_in(url):
    """What the markdown-link step is given for url, worked out once per definition, so a long URL is not
    read again at each use: a pull request or issue URL without its fragment, and any other URL as the
    empty string; _link_target reads each as it reads url (the same target, or the link text)."""
    if PULL_URL_RE.fullmatch(url) or ISSUE_URL_RE.fullmatch(url):
        return url.split("#")[0]
    return ""


# The URL copy bound: a Markdown document's reference links may copy at most URL_COPY_FACTOR characters
# of definition URL per byte of the document, plus URL_COPY_FLOOR, into the text read; past it the
# document is cannot-evaluate (exit 2). A URL is copied where no placeholder can stand for it: a URL
# PLACEHOLDER_URL_RE refuses is copied into each use, and a placeholder the markdown-link step does not
# consume is given its URL back (_with_targets). Later steps read the copied text, and the clause it
# lands in is quoted, so n uses of a URL of length L read n * L characters; the bound keeps that work
# linear in the document. The real records never come near it (they hold no reference link definition).
URL_COPY_FACTOR = 8
URL_COPY_FLOOR = 65536
# The copy bound is enforced (True). The self-test sets it False, copying without a bound, for a growth
# row's red leg.
URL_COPY_BOUNDED = True


class _CopyBudget:
    """The characters of definition URL a document's reference links may still copy: URL_COPY_FACTOR per
    byte of the document (size, its UTF-8 bytes, not its characters) plus URL_COPY_FLOOR."""

    def __init__(self, label, size):
        self.label, self.limit = label, URL_COPY_FACTOR * size + URL_COPY_FLOOR
        self.left = self.limit

    def charge(self, count):
        """Spend count characters; InputError (cannot evaluate) once more than the bound is spent."""
        if not URL_COPY_BOUNDED or not count:
            return
        self.left -= count
        if self.left < 0:
            raise InputError("{}: its reference links would copy more than {} characters of definition URLs "
                             "into the text read ({} per byte of the document plus {}), a bound on running "
                             "time".format(self.label, self.limit, URL_COPY_FACTOR, URL_COPY_FLOOR))


class _Targets(list):
    """A document's (URL, stand-in) pairs, placeholder k naming item k, with its copy budget (or None)."""
    budget = None


class _Definitions(dict):
    """A document's link reference definitions (a label's key -> its URL), with what a use of each is
    written as worked out once per definition: whether its URL is written as a placeholder (it is read
    whole for PLACEHOLDER_URL_RE once) and its stand-in (_link_stand_in, also once). One table serves
    every record unit and every scanned raw line of the document, so n units or lines using one long
    definition read its URL once, not n times. targets is the document's one list of (URL, stand-in)
    pairs, placeholder k naming targets[k]; a _LinkedProse carries that list, never a copy of it. budget
    is the document's _CopyBudget (None: unbounded), charged for every URL copied in."""

    def __init__(self, pairs=(), budget=None):
        super().__init__(pairs)
        # A definition's key -> (its placeholder, True) or (its URL, False) when it cannot be one.
        self.written = {}
        self.targets = _Targets()
        self.targets.budget = self.budget = budget

    def copied(self, url):
        """url, charged to the budget: a use that copies it into the text."""
        if self.budget is not None:
            self.budget.charge(len(url))
        return url

    def use(self, key):
        """(what a use of key's definition is written as, True when that is a placeholder)."""
        written = self.written.get(key)
        if written is None:
            url = self[key]
            if PLACEHOLDER_URL_RE.fullmatch(url):
                self.targets.append((url, _link_stand_in(url)))
                written = ("\x00{}\x00".format(len(self.targets) - 1), True)
            else:
                written = (url, False)
            self.written[key] = written
        return written


# One _Definitions table is shared by a document's units and its scanned lines (True). The self-test sets
# it False, working each definition out again for every unit and line, for a growth row's red leg.
SHARED_DEFINITIONS = True


def _resolve_reference_links(prose, definitions):
    """prose with each reference link whose label is defined in the document rewritten as the inline
    link it stands for, so the markdown-link step reads its URL; an undefined one is left as text.
    Brackets inside a code span are inert (code_spans), as to the markdown-link step. The URL is written
    as a placeholder where it can be (REFERENCE_TARGET_RE; a _LinkedProse carries the URLs), and _normalize
    gives the markdown-link step a stand-in it reads as the URL and puts the URL back in any placeholder
    that step leaves, so the text read is the text the copied URL would give. definitions is the
    document's _Definitions table (a plain dict is given a table of its own, with no copy bound); a URL
    copied into a use is charged to its copy budget."""
    if not definitions:
        return prose
    if not isinstance(definitions, _Definitions) or not SHARED_DEFINITIONS:
        definitions = _Definitions(definitions, getattr(definitions, "budget", None))
    placeholders = REFERENCE_PLACEHOLDERS and "\x00" not in prose
    linked = False

    def replace(match):
        nonlocal linked
        bang, text, label = match.group(1), match.group(2), match.group(3)
        key = " ".join((label if label else text).split()).casefold()
        if key not in definitions:
            return match.group(0)
        if not placeholders:
            return "{}[{}]({})".format(bang, text, definitions.copied(definitions[key]))
        written, placeheld = definitions.use(key)
        linked = linked or placeheld
        return "{}[{}]({})".format(bang, text, written if placeheld else definitions.copied(written))
    flags = _code_flags(prose)
    if flags is None:
        resolved = REFERENCE_LINK_RE.sub(replace, prose)
    else:
        resolved = _masked_sub(REFERENCE_LINK_RE, replace, prose, flags, True)[0]
    if not linked:
        return resolved
    resolved = _LinkedProse(resolved)
    resolved.targets = definitions.targets
    return resolved


# The kinds block_context gives a Markdown line. The first five are never read: a blank line, a line
# of a fenced block, an indented code line, a line of an HTML comment block, and a line wholly inside
# an inline HTML comment (which, unlike the others, does not end the paragraph around it). The rest
# are the prose kinds, the only lines any reader (record units, link definitions, Status cells and so
# the references they resolve) consumes.
BLANK, FENCE, CODE, COMMENT, INLINE_COMMENT = "blank", "fence", "code", "comment", "inline-comment"
# A line of a fenced block whose info string is one of EXAMPLE_FENCE_INFO: not read, and not scanned for
# hidden references either.
EXAMPLE_FENCE = "example-fence"
DEFINITION, QUOTE_LINE, TABLE_HEADER, TABLE_DELIMITER, TABLE_ROW, HEADING, ITEM, PARA = (
    "definition", "quote", "table-header", "table-delimiter", "table-row", "heading", "item", "para")
PROSE_KINDS = (DEFINITION, QUOTE_LINE, TABLE_HEADER, TABLE_DELIMITER, TABLE_ROW, HEADING, ITEM, PARA)
# What a hidden-reference warning calls the block a line was hidden in.
HIDDEN_KIND_NAMES = {FENCE: "fenced block", CODE: "indented code block", COMMENT: "HTML comment block",
                     INLINE_COMMENT: "inline HTML comment", DEFINITION: "link reference definition"}


def _blank(text):
    """True for a Markdown blank line: spaces and tabs only (a no-break space is text)."""
    return not text.strip(MD_SPACE)


def _lead(text):
    """The index of a line's first character that is not a space or a tab."""
    return len(text) - len(text.lstrip(MD_SPACE))


def _fence_opener(text, start):
    """The FENCE_OPEN_RE match when text opens a fence at index start, else None. A backtick fence's info
    string cannot hold a backtick, so ```x``` is inline code, not a fence."""
    opener = FENCE_OPEN_RE.match(text, start)
    if opener is None or (opener.group(1)[0] == "`" and "`" in text[opener.end():]):
        return None
    return opener


def _example_fence(text, opener):
    """True when the fence opener's info string starts with a word in EXAMPLE_FENCE_INFO."""
    words = text[opener.end():].split()
    return bool(words) and words[0].casefold() in EXAMPLE_FENCE_INFO


def _indent(text):
    """The columns of leading whitespace (a tab advances to the next multiple of four)."""
    expanded = text.expandtabs(4)
    return len(expanded) - len(expanded.lstrip(" "))


def _content_column(text, match):
    """A list item line's content column: the column its text starts at (after the marker, the spaces and
    any task box). CommonMark puts it at the marker's end plus one when five or more spaces follow the
    marker, which would make text wrapped under the item's first word indented code; the later column
    is kept because it reads more (a deeper code threshold, and a fence left of it is outside the item)."""
    return len(text[:match.start(1)].expandtabs(4))


def _expanded_past(length, column, piece):
    """(length, column) after piece, continuing str.expandtabs(4) from an expansion of that length that
    ends at that column (a tab advances to the next multiple of four; a carriage return or line feed
    starts the column again), so len(text[:end].expandtabs(4)) is read in pieces, each read once."""
    pad = column % 4
    expanded = ("x" * pad + piece).expandtabs(4) if "\t" in piece else "x" * pad + piece
    cut = max(expanded.rfind("\r"), expanded.rfind("\n"))
    return (length + len(expanded) - pad,
            column - pad + len(expanded) if cut < 0 else len(expanded) - cut - 1)


def _backtick_runs(text):
    """[(start, end)] for the maximal backtick runs of text: the one place text is read for them."""
    return [(match.start(), match.end()) for match in BACKTICK_RUN_RE.finditer(text)]


def _next_same(lengths):
    """For each run length in lengths, the index of the next run of the same length, or -1."""
    following, seen = [-1] * len(lengths), {}
    for index in range(len(lengths) - 1, -1, -1):
        following[index] = seen.get(lengths[index], -1)
        seen[lengths[index]] = index
    return following


def code_spans(text):
    """[(start, end, inner_start, inner_end)] for the inline code spans of text, read as CommonMark reads
    them: a backtick run opens a span that the next run of exactly the same length closes; a run with no
    such closer is literal backticks, and the scan goes on after it. start and end bound the span with
    its backticks, inner_start and inner_end its content. This is the one code-span reading: every
    exemption recognizer (comments, strikethrough, historical spans, issue references, links) uses it,
    so a delimiter inside a code span never opens or closes anything. Backslash escapes are not read."""
    runs = _backtick_runs(text)
    by_length = {}
    for index, (start, end) in enumerate(runs):
        by_length.setdefault(end - start, []).append(index)
    spans, index = [], 0
    while index < len(runs):
        start, end = runs[index]
        same = by_length[end - start]
        later = bisect.bisect_right(same, index)
        if later < len(same):
            closer = runs[same[later]]
            spans.append((start, closer[1], end, closer[0]))
            index = same[later] + 1
        else:
            index += 1
    return spans


def _blank_code_spans(text, spans):
    """text with each code span (backticks included) replaced by spaces of the same length."""
    pieces, last = [], 0
    for start, end, _inner_start, _inner_end in spans:
        pieces.extend((text[last:start], " " * (end - start)))
        last = end
    pieces.append(text[last:])
    return "".join(pieces)


def _comment_start(text):
    """The index in text of the first `<!--` outside a code span whose `-->` is not on the same line, or
    None (a comment that ends on its own line is left to the html-comment normalization)."""
    return _open_comment(_blank_code_spans(text, code_spans(text)))


# An inline comment opener on a paragraph line is looked for outside the code spans of the paragraph
# read across its lines, so a code span that wraps a `<!--` onto later lines keeps it inert (True). The
# self-test sets it False, reading code spans on the line alone, for its red-when-reverted leg.
PARAGRAPH_CODE_SPANS = True


def _ends_comment_search(line):
    """True for a line no inline comment runs past: a blank line or a line that starts a fence, heading,
    blockquote or list item (_interrupts_paragraph). The closer search stops there."""
    return _blank(line) or _interrupts_paragraph(line)


def _paragraph_ends(line):
    """True for a line that ends the paragraph read for code spans: a blank line, a line that starts a
    fence, heading, blockquote or list item, or a line that opens an HTML comment block."""
    return _blank(line) or _interrupts_paragraph(line) or line.lstrip(MD_SPACE).startswith("<!--")


def _single_line_block(text, lines, index, table):
    """True for a heading, a table row or a blockquote line: its code spans are read on the line alone
    when an inline comment opener is looked for (a code span wrapped across blockquote lines is not
    followed)."""
    following = lines[index + 1] if index + 1 < len(lines) else ""
    return (HEADING_RE.match(text) is not None or QUOTE_RE.match(text) is not None
            or text.lstrip().startswith("|") or ("|" in text and (table or _delimiter_row(following))))


def _paragraph_comment_start(scan, first, index, text):
    """_comment_start for text, the prose of line index (the line or the part of it after a `-->`), with
    code spans read across the paragraph: its earlier lines are the text the lines of block_context's
    kinds from index first keep (what the record unit joins; none when first is None), and its later
    lines are read raw up to its end (_paragraph_ends). A `<!--` inside a code span that opens on an
    earlier line or closes on a later one is inert. scan is the document's _ParagraphScan."""
    if not PARAGRAPH_CODE_SPANS:
        return _comment_start(text)
    return scan.comment_start(first, index, text)


def _rescanning_comment_start(scan, first, index, text):
    """_paragraph_comment_start as it was read before _ParagraphScan: the whole paragraph rebuilt and
    read for code spans again at each opener, quadratic in a paragraph's comment lines (a red leg, and
    the reference the self-test compares the classification with)."""
    if not PARAGRAPH_CODE_SPANS:
        return _comment_start(text)
    lines = scan.lines
    prefix = [] if first is None else [line for kind, line in scan.kinds[first:index] if kind in (PARA, ITEM)]
    end = index + 1
    while end < len(lines) and not _paragraph_ends(lines[end]):
        end += 1
    head = "\n".join(prefix + [text])
    window = "\n".join([head] + lines[index + 1:end])
    base = len(head) - len(text)
    return _open_comment(_blank_code_spans(window, code_spans(window))[base:base + len(text)])


class _ParagraphScan:
    """The paragraph-wide readings block_context makes, each built in one pass, so the inline comment
    openers of a paragraph together cost work in proportion to the paragraph: a paragraph of n such
    lines is read in O(n), never O(n^2). The cost is linear in total, not per opener: an opener that
    follows a change of paragraph or of raw run pays for reading the earlier kept lines (_follow) or
    for finding the first literal again (_first_alive). The self-test's growth rows time the whole
    check on four paragraph shapes of such lines.

    closer(index): the line whose `-->` closes an inline comment opened on line index (the first later
    line holding one before a blank line or a line that starts a fence, heading, blockquote or list
    item), or -1.

    comment_start(first, index, text): the code-span reading of one window, prefix + text + later,
    where prefix is the text the paragraph's earlier lines keep and later is its raw lines after index
    up to _paragraph_ends, without rebuilding the window. CommonMark matching reads left to right: a
    run opens a span when a run of its length follows anywhere later, and the first such run closes it.
    The prefix is read once, line by line as its lines are kept, and only its literal runs (those with
    no run of their length later in the prefix) are remembered, in reading order; each has a distinct
    length. A span runs from the prefix into text exactly when one of those literal runs has a run of
    its length in text or later, and the first such literal in reading order is the one that opens it
    (everything after it in the prefix is inside its span). A kept line that holds a run of a literal's
    length turns that literal into a span closed in the line, so the literals after it are dropped. The
    later lines are summarized once per raw run of lines between paragraph ends, as the last line
    holding a backtick run of each length."""

    def __init__(self, lines, kinds):
        self.lines, self.kinds = lines, kinds
        self._segment = None    # per line: the first line of its raw run, or -1 for a _paragraph_ends line
        self._last = None       # per raw run (by first line): {backtick run length: the last line with one}
        self._closer = None     # per line: closer(line)
        self._first = None      # the kinds index of the paragraph the prefix reading follows
        self._upto = 0          # the kinds entries read into the prefix so far
        self._literal = []      # the lengths of the prefix's literal runs, in reading order
        self._where = {}        # length -> its index in _literal
        self._alive = 0         # no literal before this index has a run of its length in the later lines
        self._alive_run = None  # the raw run _alive was found for

    def _prepare(self):
        if self._segment is not None:
            return
        lines, count = self.lines, len(self.lines)
        self._segment, self._last, start = [-1] * count, {}, -1
        for index, line in enumerate(lines):
            if _paragraph_ends(line):
                start = -1
                continue
            if start < 0:
                start, self._last[index] = index, {}
            self._segment[index] = start
            for run_start, run_end in _backtick_runs(line):
                self._last[start][run_end - run_start] = index
        self._closer, after = [-1] * count, -1
        for index in range(count - 1, -1, -1):
            self._closer[index] = after
            line = lines[index]
            if _ends_comment_search(line):
                after = -1
            elif "-->" in line:
                after = index

    def closer(self, index):
        self._prepare()
        return self._closer[index]

    def _later_has(self, length, index):
        """True when the raw lines after index, up to the paragraph's end, hold a run of length."""
        start = index + 1
        if start >= len(self.lines) or self._segment[start] < 0:
            return False
        return self._last[self._segment[start]].get(length, -1) >= start

    def _follow(self, first, index):
        """Bring the prefix reading up to the kept text of kinds[first:index]."""
        if first != self._first:
            self._first, self._upto, self._literal, self._where = first, first, [], {}
            self._alive, self._alive_run = 0, None
        for kind, text in self.kinds[self._upto:index]:
            if kind in (PARA, ITEM):
                self._keep(text)
        self._upto = max(self._upto, index)

    def _keep(self, text):
        """Read one more kept line into the prefix."""
        runs = _backtick_runs(text)
        if not runs:
            return
        lengths = [end - start for start, end in runs]
        at = 0
        hits = [self._where[length] for length in set(lengths) if length in self._where]
        if hits:
            opened = min(hits)
            length = self._literal[opened]
            for dropped in self._literal[opened:]:
                del self._where[dropped]
            del self._literal[opened:]
            self._alive = min(self._alive, opened)
            at = lengths.index(length) + 1
        following = _next_same(lengths)
        while at < len(lengths):
            if following[at] >= 0:
                at = following[at] + 1
            else:
                self._where[lengths[at]] = len(self._literal)
                self._literal.append(lengths[at])
                at += 1

    def _first_alive(self, index):
        """The index of the first literal with a run of its length in the later lines, or None."""
        start = index + 1
        if start >= len(self.lines) or self._segment[start] < 0:
            return None
        run = self._segment[start]
        last = self._last[run]
        if run != self._alive_run:
            self._alive_run = run
            if len(self._literal) <= len(last):
                self._alive = 0
            else:
                self._alive = min((self._where[length] for length, line in last.items()
                                   if line >= start and length in self._where), default=len(self._literal))
        while self._alive < len(self._literal) and last.get(self._literal[self._alive], -1) < start:
            self._alive += 1
        return self._alive if self._alive < len(self._literal) else None

    def comment_start(self, first, index, text):
        self._prepare()
        opener = None   # the length of a span the prefix opens that runs into text
        if first is not None:
            self._follow(first, index)
        runs = _backtick_runs(text)
        lengths = [end - start for start, end in runs]
        if first is not None and self._literal:
            best = self._first_alive(index)
            for length in set(lengths):
                found = self._where.get(length)
                if found is not None and (best is None or found < best):
                    best = found
            if best is not None:
                opener = self._literal[best]
        if opener is None and not runs:
            return _open_comment(text)
        spans, at = [], 0
        if opener is not None:
            if opener not in lengths:
                return None   # the span covers all of text
            at = lengths.index(opener)
            spans.append((0, runs[at][1], 0, 0))
            at += 1
        following = _next_same(lengths)
        while at < len(runs):
            if following[at] >= 0:
                spans.append((runs[at][0], runs[following[at]][1], 0, 0))
                at = following[at] + 1
            elif self._later_has(lengths[at], index):
                spans.append((runs[at][0], len(text), 0, 0))
                break
            else:
                at += 1
        return _open_comment(_blank_code_spans(text, spans))


def _open_comment(masked):
    """The index in masked (text with its code spans blanked) of the first `<!--` whose `-->` is not
    later on the same text, or None."""
    position = 0
    while True:
        start = masked.find("<!--", position)
        if start < 0:
            return None
        end = masked.find("-->", start + 4)
        if end < 0:
            return start
        position = end + 3


def _delimiter_row(line):
    return "|" in line and all(SEPARATOR_CELL_RE.fullmatch(cell) for cell in _cells(line))


def _interrupts_paragraph(line):
    """True for a line that starts a block and so ends a paragraph: an inline HTML comment does not run
    past it."""
    body = line.lstrip(MD_SPACE)
    return (FENCE_OPEN_RE.match(body) is not None or HEADING_RE.match(body) is not None
            or QUOTE_RE.match(body) is not None or ITEM_RE.match(body) is not None)


def _open_items(items, indent):
    """The content columns of items (ascending) at most indent: the open items a line so indented stays
    inside."""
    return items[:bisect.bisect_right(items, indent)]


def _list_block_opener(text, nested):
    """(item content columns, block) when text is a list item line (or a run of markers: `- 1. `) whose
    content opens a fence or an HTML comment block, else None: the block opens inside the innermost
    item, at its content column; nested holds the content columns of the enclosing open items."""
    # Each marker is matched without the rest of the line (ITEM_MARKER_RE) and its content column is
    # carried on from the previous one (_expanded_past), so a line of n markers is read in O(n).
    item = ITEM_MARKER_RE.match(text)
    if item is None:
        return None
    chain, at, length, column = list(nested), 0, 0, 0
    while item is not None and item.end() > at:
        length, column = _expanded_past(length, column, text[at:item.end()])
        chain.append(length)
        at = item.end()
        item = ITEM_MARKER_RE.match(text, at)
    opener = _fence_opener(text, at)
    if opener is not None:
        return chain, (FENCE, opener.group(1), chain[-1], _example_fence(text, opener))
    if text.startswith("<!--", at) and _comment_start(text) == at:
        return chain, (COMMENT, None, chain[-1], False)
    return None


# The text after the `-->` that closes a comment is paragraph text as it stands (True). The self-test
# sets it False, re-reading that text as a fresh line, for its red-when-reverted leg.
COMMENT_TAIL_IS_PARAGRAPH = True
# That paragraph text is still scanned for a new comment it opens (`--> <!-- second`), as any prose
# line is (True). The self-test sets it False for its red-when-reverted leg.
COMMENT_TAIL_SCANNED = True
# A table header cell names its column and is also read as a prose unit (True). The self-test sets it
# False, reading header cells as column names only, for its red-when-reverted leg.
HEADER_CELLS_READ = True


def _inline_comment(scan, first, index, text):
    """(start, end) when text, the prose of line index, opens an inline comment at index start of text
    whose `-->` is on line end, the first later line holding one before the paragraph ends (a blank line
    or a line that starts a fence, heading, blockquote or list item); None otherwise (with no such line,
    `<!--` is text). first gives the paragraph's earlier lines (_paragraph_comment_start)."""
    start = _paragraph_comment_start(scan, first, index, text)
    if start is None:
        return None
    later = scan.closer(index)
    return None if later < 0 else (start, later)


def _comment_tail(scan, first, index, text):
    """(text, hidden_until) for the paragraph text after the `-->` that closes a comment on line index:
    text cut where a new inline comment it opens starts, and the index of the line that ends that
    comment (-1 when it opens none), so `--> <!-- second` hides the lines up to the next `-->`. first
    gives the paragraph's earlier lines (_paragraph_comment_start)."""
    if COMMENT_TAIL_SCANNED:
        opened = _inline_comment(scan, first, index, text)
        if opened is not None:
            return text[:opened[0]], opened[1]
    return text, -1


def block_context(lines):
    """[(kind, text)], one per line of a Markdown document: the one place its block context is decided,
    before any line's text is read. A fence opens or closes only at most FENCE_MAX_RELATIVE_INDENT
    columns past its container's content column (the left margin, or the innermost list item's content
    column); four or more columns past it, outside a paragraph, is indented code. A line indented less
    than a list item's content column ends that item (a paragraph line aside: it may be a lazy
    continuation), and with it any fenced block or HTML comment block opened inside it. An HTML comment
    block starts at a line opening `<!--` (after at most three columns past the container) and runs to
    the line holding `-->`, across blank lines; an inline comment that `<!--` opens later in a line runs
    to the first later line holding `-->` before the paragraph ends (with none, `<!--` is text); on a
    paragraph or list item line that `<!--` is looked for outside the code spans read across the
    paragraph (_paragraph_comment_start), so a code span wrapped around it keeps it inert. The text
    after the `-->` that closes either kind of comment is paragraph text as it stands: it is not
    indented afresh, so it never becomes indented code, a fence or a list item, but a comment it opens
    (`--> <!-- second`) is found as on any prose line and hides the lines to its `-->`. A fence or an HTML
    comment block may open right after a list marker (`- ```` or `1. <!--`); it opens inside that item,
    at the item's content column. Indentation and blank lines count spaces and tabs only. text is the
    line for a prose kind, cut at an inline comment's start or after a comment's end; "" otherwise.
    InputError for a fenced block or HTML comment block that is never closed."""
    kinds = []
    items = []            # the content columns of the open list items, innermost last
    block = None          # (FENCE, fence run, container column, example) or (COMMENT, None, column, False)
    hidden_until = -1     # the index of the line that ends the open inline comment
    para = quote = table = False
    para_first = 0        # the index of the open paragraph's first line
    # The paragraph-wide readings, each made once (_ParagraphScan). The open paragraph's earlier lines,
    # whose kept text (what its record unit joins) precedes a line read for code spans, are the PARA and
    # ITEM entries of kinds from para_first; with no open paragraph there are none (first None).
    scan = _ParagraphScan(lines, kinds)

    for index, text in enumerate(lines):
        if index < hidden_until:
            kinds.append((INLINE_COMMENT, ""))
            continue
        if index == hidden_until:
            hidden_until = -1
            text = text[text.find("-->") + 3:]
            if _blank(text):
                kinds.append((INLINE_COMMENT, ""))
                continue
            if COMMENT_TAIL_IS_PARAGRAPH:
                first = para_first if para else None
                if not para:
                    para_first = index
                para, quote, table = True, False, False
                text, hidden_until = _comment_tail(scan, first, index, text)
                kinds.append((INLINE_COMMENT, "") if _blank(text) else (PARA, text))
                continue
        indent = _indent(text)
        if block is not None:
            name, run, column, example = block
            if _blank(text) or indent >= column:
                if name == FENCE:
                    body = text.strip(MD_SPACE)
                    if (indent - column <= FENCE_MAX_RELATIVE_INDENT and body.startswith(run)
                            and set(body) == {run[0]}):
                        block = None
                    kinds.append((EXAMPLE_FENCE if example else FENCE, ""))
                    continue
                end = text.find("-->")
                if end < 0:
                    kinds.append((COMMENT, ""))
                    continue
                block, text = None, text[end + 3:]
                if _blank(text):
                    kinds.append((COMMENT, ""))
                    continue
                if COMMENT_TAIL_IS_PARAGRAPH:
                    para, quote, table, para_first = True, False, False, index
                    text, hidden_until = _comment_tail(scan, None, index, text)
                    kinds.append((COMMENT, "") if _blank(text) else (PARA, text))
                    continue
                indent = _indent(text)
            block = None  # the list item holding the block has ended (or the comment closed)
        if _blank(text):
            para = quote = table = False
            kinds.append((BLANK, ""))
            continue
        lead = _lead(text)
        # items ascends (an item's content column is past every column it nests in), so the items this
        # line stays inside are a prefix, found by bisection: a lazy line under n open items costs
        # O(log n) plus the items it keeps (fewer than its indent), not O(n).
        nested = _open_items(items, indent)
        column = nested[-1] if nested else 0
        if not para:
            items = nested
        opener = _fence_opener(text, lead)
        if opener and indent - column <= FENCE_MAX_RELATIVE_INDENT:
            items, block = nested, (FENCE, opener.group(1), column, _example_fence(text, opener))
            para = quote = table = False
            kinds.append((EXAMPLE_FENCE if block[3] else FENCE, ""))
            continue
        if not para and not quote and indent - column >= 4:
            kinds.append((CODE, ""))
            continue
        opened = _list_block_opener(text, nested)
        if opened is not None:
            items, block = opened
            para = quote = table = False
            kinds.append((EXAMPLE_FENCE if block[3] else block[0], ""))
            continue
        start = _comment_start(text)
        html_block = text.startswith("<!--", lead) and indent - column <= 3
        if html_block and start == lead:
            items, block = nested, (COMMENT, None, column, False)
            para = quote = table = False
            kinds.append((COMMENT, ""))
            continue
        if not html_block and "<!--" in text and not _single_line_block(text, lines, index, table):
            # A paragraph or list item line: its code spans are read across the paragraph (a list item
            # line starts a new one), so a code span that wraps around `<!--` keeps it inert.
            first = None if ITEM_RE.match(text) or not para else para_first
            start = _paragraph_comment_start(scan, first, index, text)
        if start is not None and not html_block:
            hidden_until = scan.closer(index)
            if hidden_until > index:
                text = text[:start]
                if _blank(text):
                    kinds.append((INLINE_COMMENT, ""))
                    continue
        following = lines[index + 1] if index + 1 < len(lines) and hidden_until <= index else ""
        if not para and LINK_DEFINITION_RE.match(text):
            items, quote, table = nested, False, False
            kinds.append((DEFINITION, text))
            continue
        if QUOTE_RE.match(text):
            items, para, quote = nested, False, True
            kinds.append((QUOTE_LINE, text))
            continue
        quote = False
        # A table row: a line with a leading pipe, a row of the current table, or a header whose next
        # line is a delimiter row; the outer pipes are optional (GFM).
        if text.lstrip().startswith("|") or ("|" in text and (table or _delimiter_row(following))):
            items, para = nested, False
            if _delimiter_row(text):
                kinds.append((TABLE_DELIMITER, text))
            elif not table and _delimiter_row(following):
                table = True
                kinds.append((TABLE_HEADER, text))
            else:
                kinds.append((TABLE_ROW, text))
            continue
        table = False
        if HEADING_RE.match(text):
            items, para = nested, False
            kinds.append((HEADING, text))
            continue
        match = ITEM_RE.match(text)
        if match:
            items, para, para_first = nested + [_content_column(text, match)], True, index
            kinds.append((ITEM, text))
            continue
        if not para:
            para_first = index
        para = True
        kinds.append((PARA, text))
    if block is not None:
        raise InputError("a fenced block opened is never closed" if block[0] == FENCE
                         else "an HTML comment opened is never closed")
    return kinds


def markdown_units(text, label, status_column=None, hidden=None, budget=None):
    """[(location, prose, context)] for a Markdown document, read only from the lines block_context
    gives a prose kind (fenced blocks, indented code and HTML comments are never read, in a list item
    as outside one); a blockquote whose lead-in (the paragraph just before it, or its own first words)
    is `Earlier note:` or `Previously recorded:` is historical and skipped; any other blockquote is
    context "quote"; a cell of the declared status column is context "status"; everything else is
    "prose". Wrapped lines of one paragraph or list item are joined into one unit. A link reference
    definition (a definition line, so never one inside code or a comment) is not a unit; a reference
    link to it reads as an inline link. A leading byte order mark is dropped, and a line's trailing
    carriage return is a line ending, not text. When hidden is a list, the unevaluated_references
    entries of the document are appended to it. Each unit and entry is charged to budget (a
    _CollectBudget, or None) before it is kept: a table cell's location names its header cell, so a long
    header over many rows is counted once per row."""
    units = []

    def unit(location, prose, context):
        if budget is not None:
            budget.charge(location)
        units.append((location, prose, context))
    size = len(text.encode("utf-8"))   # the document's bytes, as the copy bound counts them
    if text.startswith("\ufeff"):
        text = text[1:]
    lines = [line[:-1] if line.endswith("\r") else line for line in text.split("\n")]
    try:
        blocks = block_context(lines)
    except InputError as exc:
        raise InputError("{}: {}".format(label, exc))
    definitions = _Definitions(budget=_CopyBudget(label, size))
    para, para_line = [], 0
    quote, quote_line = [], 0
    quote_numbers = []    # the line numbers of the open blockquote
    exempt = set()        # the line numbers of blockquotes with a historical lead-in (never scanned)
    last_prose = ""
    header = None

    def flush_para():
        nonlocal para, last_prose
        if para:
            joined = " ".join(para)
            unit("{}:{}".format(label, para_line), joined, "prose")
            last_prose = joined
        para = []

    def flush_quote():
        nonlocal quote, last_prose
        if quote:
            joined = " ".join(quote)
            historical = (HISTORY_INTRO_RE.search(last_prose) is not None
                          or HISTORY_LEAD_RE.match(joined) is not None)
            if not historical:
                unit("{}:{} (blockquote)".format(label, quote_line), joined, "quote")
            else:
                exempt.update(quote_numbers)
            last_prose = ""
        quote = []
        quote_numbers.clear()

    for number, (kind, line) in enumerate(blocks, 1):
        if kind == INLINE_COMMENT:
            continue
        if kind == BLANK:
            flush_para()
            flush_quote()
            header = None
            continue
        if kind == CODE:
            last_prose = ""
            continue
        if kind not in PROSE_KINDS:  # a fence or HTML comment block line
            flush_para()
            flush_quote()
            header = None
            last_prose = ""
            continue
        if kind == DEFINITION:
            flush_quote()
            header = None
            match = LINK_DEFINITION_RE.match(line)
            definitions.setdefault(" ".join(match.group(1).split()).casefold(), match.group(2))
            continue
        if kind == QUOTE_LINE:
            flush_para()
            if not quote:
                quote_line = number
            quote.append(QUOTE_RE.match(line).group(1).strip())
            quote_numbers.append(number)
            continue
        flush_quote()
        if kind in (TABLE_HEADER, TABLE_DELIMITER, TABLE_ROW):
            flush_para()
            last_prose = ""
            cells = _cells(line)
            if kind == TABLE_DELIMITER:
                continue
            if kind == TABLE_HEADER:
                header = [_header_name(cell) for cell in cells]
                if HEADER_CELLS_READ:
                    # A header cell names its column and is also read as prose (never as a Status cell).
                    for column, cell in enumerate(cells):
                        unit("{}:{} (header cell {})".format(label, number, column + 1), cell, "prose")
                continue
            for column, cell in enumerate(cells):
                name = header[column] if header is not None and column < len(header) else None
                context = "status" if status_column is not None and name == status_column else "prose"
                unit("{}:{} ({} cell)".format(label, number, name or "column {}".format(column + 1)), cell,
                     context)
            continue
        header = None
        if kind == HEADING:
            flush_para()
            unit("{}:{}".format(label, number), (HEADING_RE.match(line).group(1) or "").strip(), "prose")
            last_prose = units[-1][1]
            continue
        if kind == ITEM:
            flush_para()
            para, para_line = [ITEM_RE.match(line).group(1).strip()], number
            continue
        if not para:
            para_line = number
        para.append(line.strip())
    flush_para()
    flush_quote()
    units = [(where, _resolve_reference_links(prose, definitions), context) for where, prose, context in units]
    if hidden is not None:
        hidden.extend(unevaluated_references(label, lines, blocks, units, exempt, definitions, budget))
    return units


# The NORMALIZE steps that only exempt text: the scan of a hidden line skips them, so a reference that
# is struck through, commented or quoted inside a hidden line is still found.
HIDDEN_SCAN_SKIPS = ("html-comment", "history-span", "strikethrough")
# The reason every unevaluated-reference warning gives, and the location tag of one found in a line that
# was read in full (any drop other than a hidden block: the warning does not depend on what dropped it).
NOT_EVALUATED = "not evaluated"
# A line read in full is scanned too, so the scan is the set difference of the whole document's
# references and the references of the units evaluated (True). The self-test sets it False, scanning
# hidden lines only, for its red-when-reverted leg.
SCAN_READ_LINES = True


def _references(text, skips=HIDDEN_SCAN_SKIPS):
    """[(key, ref)] for the pull request references in text, found as tokenize finds them (links, issue
    references and the other spelling steps applied) but with the skips steps left out (none: exactly as
    a record unit is read); key is (repository casefolded or None for the surface default, number), ref
    is (repository or None, number, original text)."""
    body = _normalize(" ".join(text.split()), skips, getattr(text, "targets", ()))
    found = []
    for match in REF_RE.finditer(body):
        if match.group("un"):
            repo, number = "{}/{}".format(match.group("uo"), match.group("ur")), match.group("un")
        elif match.group("qn"):
            repo, number = "{}/{}".format(match.group("qo"), match.group("qr")), match.group("qn")
        else:
            repo, number = None, match.group("pn") or match.group("hn")
        number = int(number)
        if number < 1 or (repo is not None and not _pr_state.valid_repo(repo)):
            continue
        found.append(((repo.casefold() if repo else None, number), (repo, number, match.group(0))))
    return found


def evaluated_keys(units):
    """The keys of the pull request references that reach the claim matcher: every reference tokenize
    finds in a record unit (the units find_candidates evaluates)."""
    keys = set()
    for _where, prose, context in units:
        keys.update((repo.casefold() if repo else None, number)
                    for repo, number, _text in tokenize(prose, context)[1])
    return keys


def unevaluated_references(label, lines, blocks, units, exempt=(), definitions=None, budget=None):
    """[(location, reason, ref, line)] for every pull request reference of a Markdown document that
    reached no evaluated record unit: the set difference of the references of the whole document and
    evaluated_keys(units), whatever dropped them. A line read in full is scanned as record text is read
    (reference links resolved, strikethrough, historical spans and single-line comments exempt); a line
    the block classifier hides or cuts (a fenced block, indented code, an HTML comment, a link reference
    definition) is scanned whole, with those exemptions left out, and its entry names the block kind. Not
    scanned: the documented exemptions only, a fenced block whose info string is in EXAMPLE_FENCE_INFO
    and the lines of a blockquote with a historical lead-in (exempt, their line numbers). A reference
    is keyed by repository (or the surface default) and number, by design: one evaluated mention of a
    pull request suppresses the entry for its other dropped occurrences in the same document. Each entry
    is reported as a warning, without reading pull request state. Exemption delimiters inside code spans
    are inert in the scan as in the matcher (one shared reading, code_spans). The entries of one line
    share its location and quoted text, and each is charged to budget (_line_entries)."""
    evaluated = evaluated_keys(units)
    found = []
    for number, (raw, (kind, text)) in enumerate(zip(lines, blocks), 1):
        if kind in (BLANK, EXAMPLE_FENCE) or number in exempt:
            continue
        if kind in PROSE_KINDS and kind != DEFINITION and text == raw:
            if not SCAN_READ_LINES:
                continue
            name, refs = NOT_EVALUATED, _references(_resolve_reference_links(raw, definitions or {}), ())
        else:
            # A hidden line; a prose line a comment cut is named for the part the comment cut away.
            name = "HTML comment" if kind in PROSE_KINDS and kind != DEFINITION else HIDDEN_KIND_NAMES[kind]
            refs = _references(raw)
        _line_entries(found, refs, evaluated, ("{}:{} ({})", (label, number, name)), name, raw, budget)
    return found


def _line_entries(found, refs, evaluated, location, name, line, budget):
    """Append to found one (location, name, ref, quoted line) entry for each reference of refs, first per
    key, whose key is not in evaluated. The location (location: a format and its arguments) and the
    quoted line (line stripped) are built once, at the line's first entry, and shared by all its entries
    (SHARED_QUOTES), so n references on one line hold one copy of the line, not n; each entry is charged
    to budget (a _CollectBudget, or None) before it is kept."""
    seen, where, quoted = set(), None, None
    for key, ref in refs:
        if key in evaluated or key in seen:
            continue
        seen.add(key)
        if where is None or not SHARED_QUOTES:
            where, quoted = location[0].format(*location[1]), line.strip()
        if budget is not None:
            budget.charge(where, quoted)
        found.append((where, name, ref, quoted))


def toml_unevaluated_references(units, budget=None):
    """The unevaluated_references entries of a toml-strings document: each line of each decoded string
    is scanned as record text is read, against the references of all its units (comments are not read
    or scanned). The entries of one line share its location and quoted text (_line_entries)."""
    evaluated = evaluated_keys(units)
    found = []
    for where, prose, _context in units:
        for offset, line in enumerate(prose.split("\n"), 1):
            _line_entries(found, _references(line, ()), evaluated,
                          ("{} (string line {}, {})", (where, offset, NOT_EVALUATED)), NOT_EVALUATED, line, budget)
    return found


def surface_units(surface, data, hidden=None, budget=None):
    """The record units of one declared surface given its complete bytes; InputError when malformed.
    When hidden is a list, the unevaluated-reference entries of the surface are appended to it. Each
    unit and entry is charged to budget (a _CollectBudget, or None) before it is kept."""
    label = surface["path"]
    text = _decode(data, label)
    if surface["format"] == "toml-strings":
        units = toml_units(text, label, budget)
        if hidden is not None and SCAN_READ_LINES:
            hidden.extend(toml_unevaluated_references(units, budget))
        return units
    return markdown_units(text, label, surface.get("status-column"), hidden, budget)


# ---------------------------------------------------------------------------------------------------
# The grammar: pull request references, clause boundaries, and bound claim phrases.

_O = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?"
_R = r"[A-Za-z0-9._-]{1,100}"
REF_RE = re.compile(
    r"(?:\b(?:pull\s+requests?|PRs?)\s+)?"
    r"(?:https?://github\.com/(?P<uo>" + _O + r")/(?P<ur>" + _R + r")/pull/(?P<un>[0-9]{1,9})"
    r"(?:/(?:files|commits|checks))?/?(?![\w/])"
    r"|(?<![\w./-])(?P<qo>" + _O + r")/(?P<qr>" + _R + r")#(?P<qn>[0-9]{1,9})(?![\w])"
    r"|\bpr:#?(?P<pn>[0-9]{1,9})(?![\w])"
    r"|(?<![\w&#/])#(?P<hn>[0-9]{1,9})(?![\w]))",
    re.IGNORECASE)
# The placeholder a pull request reference is replaced with before the phrase rules run (private-use
# characters in IN_BAND_MARKS, so no record text can spell it: a document carrying one is refused).
REF_MARK = "\ue000[0-9]+\ue001"
REF_MARK_RE = re.compile("\ue000([0-9]+)\ue001")
LIST = r"(?P<l>{0}(?:\s*,\s*(?:and\s+|or\s+)?{0}|\s+(?:and|or|&)\s+{0})*)".format(REF_MARK)
# A verb, or `still` or `currently` standing for one, then up to two modifiers (#17 still currently open).
VERB = (r"(?:(?:is|are|remains?|stays?|(?:has|have)\s+been|still|currently)"
        r"(?:\s+(?:still|currently|now|also|both)){0,2})")
# A historical lead-in followed by a quoted span: only that span is exempt. A straight single-quoted
# span ends at a quote that no letter follows, so an apostrophe inside it (isn't) does not end it.
STRAIGHT_SPAN = r"'(?:[^']|'(?=\w))*'(?!\w)"
# A backticked span after a lead-in: a whole backtick run of any length closed by the next run of the
# same length. The history-span step reads code-span content as CODE_FILLER (no backtick), so this
# matches exactly a code span as code_spans bounds it, embedded shorter runs included; a run with no
# such closer quotes nothing.
BACKTICK_SPAN = r"(?P<tick>`+)(?!`)[^`]*(?<!`)(?P=tick)(?!`)"


# A historical lead-in before a quoted span, up to where the span must start.
HISTORY_SPAN_LEAD = (r"(?i)\b(?:previously recorded|earlier note|the old note read|old note read|was recorded as|"
                     r"previously read)\s*:?\s*")


def _history_span_re(straight, backtick=BACKTICK_SPAN):
    return re.compile(
        HISTORY_SPAN_LEAD + r"(?:\u201c[^\u201d]*\u201d|\"[^\"]*\"|" + backtick + r"|\u2018[^\u2019]*\u2019"
        r"|" + straight + r")")


HISTORY_SPAN_RE = _history_span_re(STRAIGHT_SPAN)
HISTORY_SPAN_LEAD_RE = re.compile(HISTORY_SPAN_LEAD)
# Where a straight single-quoted span after a lead-in ends (STRAIGHT_SPAN): the first quote no word
# character follows.
STRAIGHT_SPAN_END_RE = re.compile(r"'(?!\w)")
CLAUSE_SPLIT_RE = re.compile(r"[;:!?\u00a6]|\.(?=\s|$)|\s[\u2013\u2014]\s|\s-\s")
# A status cell keeps its colon (In progress: #17) inside one clause.
STATUS_CLAUSE_SPLIT_RE = re.compile(r"[;!?\u00a6]|\.(?=\s|$)|\s[\u2013\u2014]\s|\s-\s")
FORMATTING_RE = re.compile("[`\u201c\u201d\u2018\u2019\"*]|~~")
INSTRUCTION_WORDS = {"leave", "leaves", "left", "keep", "keeps", "kept", "to", "will", "should",
                     "must", "please", "re"}
# Cue words: one of these among the words directly before a claim (read back over CUE_PASSES words
# only) makes the claim negated, past, conditional or a question, so a merged or closed pull request
# gives a warning, never a finding and never a silent pass. "we'd" reads as "we d" (had or would).
CUES = {"not", "never", "longer", "no", "t", "isnt", "arent", "wasnt", "werent", "hasnt", "havent",
        "hadnt", "dont", "doesnt", "didnt", "cannot", "neither", "nor", "none", "was", "were", "had", "d",
        "did", "if", "whether", "unless", "previously", "formerly", "once", "merged", "closed", "landed"}
# Words read past while looking back for a cue: adverbs, auxiliaries, contraction stems and a few
# determiners ("have not only been waiting", "haven't been", "no pending PRs", "all pending PRs").
CUE_PASSES = {"still", "also", "then", "currently", "yet", "just", "only", "now", "already", "even",
              "been", "be", "being", "is", "are", "am", "have", "has", "s", "re", "ve", "do", "does",
              "isn", "aren", "wasn", "weren", "hasn", "haven", "hadn", "don", "doesn", "didn", "can",
              "won", "wouldn", "couldn", "shouldn", "the", "all", "any", "of"}
# An issue number, bare or qualified by its repository (acme/widgets#17). Only the plural `issues`
# takes a list: after `issue #12,` a following `#17` is read as a pull request.
_ISSUE_NUMBER = r"(?:" + _O + r"/" + _R + r")?#[0-9]{1,9}"
ISSUE_LIST_RE = re.compile(
    r"\bissue\s*(?P<one>" + _ISSUE_NUMBER + r")"
    r"|\bissues\s*(?P<l>" + _ISSUE_NUMBER + r"(?:\s*(?:,|/|&|\band\b|\bor\b)\s*(?:and\s+|or\s+)?"
    + _ISSUE_NUMBER + r")*)", re.I)
PULL_URL_RE = re.compile(r"https?://github\.com/" + _O + r"/" + _R + r"/pull/[0-9]{1,9}"
                         r"(?:/(?:files|commits|checks))?/?(?:#[\w-]*)?", re.ASCII)
ISSUE_URL_RE = re.compile(r"https?://github\.com/" + _O + r"/" + _R + r"/issues/(?P<n>[0-9]{1,9})/?(?:#[\w-]*)?",
                          re.ASCII)
# An autolink: a GitHub URL between angle brackets.
AUTOLINK_RE = re.compile(r"<(https?://github\.com/[^\s<>]+)>")


def _link_target(text, url):
    """A link's replacement: a pull request URL stands for its target (it carries the repository); an
    issue URL becomes an issue reference; any other link keeps only its text."""
    if PULL_URL_RE.fullmatch(url):
        return " " + url.split("#")[0].rstrip("/") + " "
    issue = ISSUE_URL_RE.fullmatch(url)
    if issue:
        return " issue #{} ".format(issue.group("n"))
    return text


def _link(match):
    """A Markdown inline link or image."""
    return _link_target(match.group(1), match.group(2))


def _html_link(match):
    """An HTML anchor on one unit: <a href="URL">text</a>."""
    return _link_target(match.group(2), match.group(1))


def _issue(match):
    """An issue reference list: its numbers are not pull requests, so their '#' is replaced by a
    private-use mark no pull request reference matches."""
    return match.group(0).replace("#", "\ue003")


HTML_COMMENT_RE = re.compile(r"<!--.*?-->")
MARKDOWN_LINK_RE = re.compile(r"!?\[([^\]]*)\]\(\s*<?([^\s()<>]+)>?(?:\s+(?:\"[^\"]*\"|'[^']*'))?\s*\)")
# An HTML anchor's opening tag; HTML_LINK_RE is it, then the anchor text up to `</a>` on the same line.
HTML_ANCHOR_TAG = r"(?i)<a\s+[^>]*?\bhref\s*=\s*[\"']([^\"'\s<>]+)[\"'][^>]*>"
HTML_ANCHOR_TAG_RE = re.compile(HTML_ANCHOR_TAG)
HTML_LINK_RE = re.compile(HTML_ANCHOR_TAG + r"(.*?)</a\s*>")
HTML_ANCHOR_OPEN_RE = re.compile(r"(?i)<a\s")
# The href attribute HTML_ANCHOR_TAG reads, and the closing tag HTML_LINK_RE ends at, each sought alone.
HTML_HREF_RE = re.compile(r"(?i)\bhref\s*=\s*[\"']([^\"'\s<>]+)[\"']")
HTML_ANCHOR_CLOSE_RE = re.compile(r"(?i)</a\s*>")


# Each step regex below can fail at many starts that all scan the same stretch of text (n unclosed `<!--`
# on one line each scan to the line's end), quadratic in the text when the regex is run as it stands.
# Its finder yields exactly the matches regex.finditer yields, in order, as the real Match objects
# (regex.match at each start it finds), but tries the regex only at a start where it can match or
# where no earlier start has scanned the same stretch: a failed start shows every start it covers fails
# too. Each finder's docstring argues that it reads each character of the text a bounded number of
# times; the growth rows time that on their shapes only. The other steps have no finder and are not
# argued here.

def _html_comment_matches(text):
    """HTML_COMMENT_RE.finditer(text): the first `-->` after a `<!--`, on its line. A `<!--` with none
    shows every later `<!--` on that line has none either (its search is a part of the first one's)."""
    position, line_end = 0, -1
    while True:
        start = text.find("<!--", position)
        if start < 0:
            return
        if line_end < start:
            line_end = text.find("\n", start)
            line_end = len(text) if line_end < 0 else line_end
        end = text.find("-->", start + 4, line_end)
        if end < 0:
            position = line_end + 1
            continue
        found = HTML_COMMENT_RE.match(text, start)
        yield found
        position = found.end()


def _markdown_link_matches(text):
    """MARKDOWN_LINK_RE.finditer(text). A `[` can only close at the first `]` after it, so when the link
    after that `]` does not match, no `[` before it opens one either; the search goes on after it."""
    position = 0
    while True:
        bracket = text.find("[", position)
        if bracket < 0:
            return
        close = text.find("]", bracket + 1)
        if close < 0:
            return
        start = bracket - 1 if bracket > position and text[bracket - 1] == "!" else bracket
        found = MARKDOWN_LINK_RE.match(text, start)
        if found is None:
            position = close + 1
            continue
        yield found
        position = found.end()


# The HTML link finder keeps the next line feed it found until the search passes it (True). The self-test
# sets it False, seeking the line end again after each tag, for a growth row's red leg.
KEPT_LINE_ENDS = True


def _html_link_matches(text):
    """HTML_LINK_RE.finditer(text). An anchor tag ends at the first `>` after its `<a` (no part of the tag
    pattern can hold a `>`), so a start matches exactly when an href attribute (HTML_HREF_RE) lies between
    its `<a ` and that `>` and a `</a>` (HTML_ANCHOR_CLOSE_RE) starts after the `>` on the same line.
    The two are sought apart, the href inside the tag and the closer after it, and HTML_LINK_RE is run
    only at a start where it matches, so it never retries each href of a tag against a missing closer.
    A failed start shows every `<a` before that `>` fails too; a tag whose line has no `</a>` shows every
    later tag ending on that line has none either; and the next closer found, and the next line feed
    found, are each kept until the search passes them, so the text after the tags is read for a closer
    once and for a line end once (n complete anchors on one line do not each read the rest of it)."""
    position, no_close_before = 0, -1
    closer, closer_from = None, len(text) + 1   # the first closer at or after closer_from
    line_end = -1   # the first line feed after the last tag end it was sought from, or len(text)
    while True:
        opener = HTML_ANCHOR_OPEN_RE.search(text, position)
        if opener is None:
            return
        start = opener.start()
        tag_end = text.find(">", start)
        if tag_end < 0:
            return
        if tag_end >= no_close_before and HTML_HREF_RE.search(text, start + 3, tag_end) is not None:
            if closer_from > tag_end + 1 or (closer is not None and closer.start() <= tag_end):
                closer, closer_from = HTML_ANCHOR_CLOSE_RE.search(text, tag_end + 1), tag_end + 1
            if line_end <= tag_end or not KEPT_LINE_ENDS:
                # No line feed lies between the tag end the kept one was sought from and it, so it is
                # still the first after this tag end while it lies past this tag end.
                line_end = text.find("\n", tag_end + 1)
                line_end = len(text) if line_end < 0 else line_end
            found = (HTML_LINK_RE.match(text, start) if closer is not None and closer.start() < line_end
                     else None)
            if found is not None:
                yield found
                position = found.end()
                continue
            no_close_before = line_end
        position = tag_end + 1


def _history_span_matches(text):
    """HISTORY_SPAN_RE.finditer(text). A span starts right after its lead-in, and the quotation mark
    there decides where it can end; that end is found once and reused for every later lead-in before it,
    so n lead-ins before one unclosed quotation mark read it once, not n times."""
    ends = {}   # the closing mark sought -> (searched from, found at or -1)

    def end_after(key, start):
        searched, found = ends.get(key, (len(text) + 1, -1))
        if not (searched <= start and (found < 0 or found >= start)):
            if key == "'":
                hit = STRAIGHT_SPAN_END_RE.search(text, start)
                found = -1 if hit is None else hit.start()
            else:
                found = text.find(key, start)
            ends[key] = (start, found)
        return found

    position = 0
    while True:
        lead = HISTORY_SPAN_LEAD_RE.search(text, position)
        if lead is None:
            return
        at = lead.end()
        mark = text[at:at + 1]
        closer = {"\u201c": "\u201d", "\"": "\"", "\u2018": "\u2019", "'": "'"}.get(mark)
        if mark == "`" or (closer is not None and end_after(closer, at + 1) >= 0):
            found = HISTORY_SPAN_RE.match(text, lead.start())
            if found is not None:
                yield found
                position = found.end()
                continue
        position = max(at, lead.start() + 1)


# The step regexes read through a finder (above) rather than run as they stand (True). The self-test
# sets it False, running each regex as it stands, for the growth rows' red legs.
LINEAR_FINDERS = True
STEP_FINDERS = {HTML_COMMENT_RE: _html_comment_matches, MARKDOWN_LINK_RE: _markdown_link_matches,
                HTML_LINK_RE: _html_link_matches, HISTORY_SPAN_RE: _history_span_matches}


def _step_matches(regex, text):
    """regex.finditer(text), through its finder when it has one (STEP_FINDERS)."""
    finder = STEP_FINDERS.get(regex) if LINEAR_FINDERS else None
    return regex.finditer(text) if finder is None else finder(text)


def _step_sub(regex, replacement, text):
    """regex.sub(replacement, text), with the matches _step_matches gives."""
    if not LINEAR_FINDERS or regex not in STEP_FINDERS:
        return regex.sub(replacement, text)
    pieces, last = [], 0
    for found in _step_matches(regex, text):
        pieces.extend((text[last:found.start()],
                       replacement(found) if callable(replacement) else found.expand(replacement)))
        last = found.end()
    pieces.append(text[last:])
    return "".join(pieces)


# The normalization steps tokenize applies, in order, before pull request references are found:
# (name, regex, replacement). The self-test removes one at a time by name (red-when-reverted).
NORMALIZE = (
    ("html-comment", HTML_COMMENT_RE, " "),
    ("markdown-link", MARKDOWN_LINK_RE, _link),
    ("html-link", HTML_LINK_RE, _html_link),
    # A bracket that is not a link ([#17] with no definition) is text: its brackets are dropped.
    ("bracket", re.compile(r"\[([^\[\]]*)\]"), r" \1 "),
    # A curly apostrophe inside a word (isn\u2019t, we\u2019d) is an apostrophe, not a closing quotation mark.
    ("apostrophe", re.compile(r"(?<=[A-Za-z])\u2019(?=[A-Za-z])"), "'"),
    ("history-span", HISTORY_SPAN_RE, " \u00a6 "),
    # GFM strikethrough: ~~text~~, or ~text~ with flanking tildes (so ~5 min and ~10 is not struck).
    ("strikethrough", re.compile(r"~~[^~]*~~|(?<![~\w])~(?![\s~])[^~]*(?<![\s~])~(?![~\w])"), " "),
    ("formatting", FORMATTING_RE, ""),
    # Angle brackets around a GitHub URL are dropped, keeping the URL: a spelling, never an exemption,
    # so it reads code-span text too, and it runs after the formatting marks are gone, so backticks
    # inside or around the brackets (`<`URL`>`) do not keep them between the reference and its predicate.
    ("autolink", AUTOLINK_RE, r" \1 "),
    ("underscore-emphasis", re.compile(r"(?<!\w)_{1,2}(?=#[0-9])|(?<=[0-9])_{1,2}(?!\w)"), ""),
    ("issue-reference", ISSUE_LIST_RE, _issue),
    ("glued-pr", re.compile(r"\b(PRs?)#(?=[0-9])", re.I), r"\1 #"),
    ("slash-list", re.compile(r"(?<=[0-9])\s*/\s*(?=#[0-9])"), " and "),
)
# The NORMALIZE steps that recognize an exemption or a link by its delimiters: each runs only on text
# outside code spans (code_spans, read once per text before the first step), so a delimiter inside a
# code span is inert. The other steps are spellings and run on all text, code-span text included, so a
# claim inside a code span is still read. The autolink step is a spelling (it drops only the angle
# brackets and keeps the URL), so a backticked `<URL>` still binds to the predicate after it.
CODE_SPAN_INERT_STEPS = ("html-comment", "markdown-link", "html-link", "history-span", "strikethrough",
                         "issue-reference")
# Delimiters inside code spans are inert to those steps (True). The self-test sets it False, letting
# them see code-span text, for its red-when-reverted leg.
CODE_SPANS_INERT = True
# What a code span's content reads as to an exemption recognizer: a private-use character no delimiter,
# space or word pattern matches (its backticks stay, so a backticked span after a historical lead-in is
# still that span).
CODE_FILLER = "\ue004"


class _RealMatch:
    """A match found in a code-masked view, read back from the real text at the same positions."""

    def __init__(self, match, text):
        self._match, self._text = match, text

    def start(self, group=0):
        return self._match.start(group)

    def end(self, group=0):
        return self._match.end(group)

    def group(self, group=0):
        start = self._match.start(group)
        return None if start < 0 else self._text[start:self._match.end(group)]


_TEMPLATE_GROUP_RE = re.compile(r"\\([0-9])")


def _replacement(match, replacement, flags):
    """(text, flags) for one match: the replacement text, and per character whether it is code-span
    content (a group copied from the match keeps its flags; any other text is not code)."""
    if callable(replacement):
        text = replacement(match)
        for group in range(1, (match._match.re.groups or 0) + 1):
            if match.start(group) >= 0 and text == match.group(group):
                return text, flags[match.start(group):match.end(group)]
        return text, bytearray(len(text))
    pieces, out_flags, last = [], bytearray(), 0
    for ref in _TEMPLATE_GROUP_RE.finditer(replacement):
        literal, group = replacement[last:ref.start()], int(ref.group(1))
        pieces.append(literal)
        out_flags += bytearray(len(literal))
        if match.start(group) >= 0:
            pieces.append(match.group(group))
            out_flags += flags[match.start(group):match.end(group)]
        last = ref.end()
    pieces.append(replacement[last:])
    out_flags += bytearray(len(replacement) - last)
    return "".join(pieces), out_flags


def _masked_sub(regex, replacement, text, flags, outside_code):
    """(text, flags) after regex.sub(replacement): when outside_code, the regex sees the code-span
    content as CODE_FILLER, so it matches only by delimiters outside code spans; the replacement is
    built from the real text either way, and flags follow every character that is kept."""
    view = text
    if outside_code:
        view = "".join(CODE_FILLER if flag else char for char, flag in zip(text, flags))
    pieces, out_flags, last = [], bytearray(), 0
    for found in _step_matches(regex, view):
        new_text, new_flags = _replacement(_RealMatch(found, text), replacement, flags)
        pieces.extend((text[last:found.start()], new_text))
        out_flags += flags[last:found.start()] + new_flags
        last = found.end()
    pieces.append(text[last:])
    out_flags += flags[last:]
    return "".join(pieces), out_flags


def _code_flags(text):
    """A bytearray, one entry per character of text: 1 for code-span content, else 0; None when text
    holds no code-span content (every step then runs on the text as it stands)."""
    spans = code_spans(text) if CODE_SPANS_INERT and "`" in text else []
    if not any(inner_end > inner_start for _start, _end, inner_start, inner_end in spans):
        return None
    flags = bytearray(len(text))
    for _start, _end, inner_start, inner_end in spans:
        flags[inner_start:inner_end] = b"\x01" * (inner_end - inner_start)
    return flags


class _TargetMatch:
    """A markdown-link match whose URL group, when it is a placeholder, reads as its URL's stand-in
    (_link_stand_in), which the link replacement reads as it reads the URL."""

    def __init__(self, match, targets):
        self._match, self._targets = match, targets

    def group(self, group=0):
        value = self._match.group(group)
        placeholder = None if value is None else REFERENCE_TARGET_RE.fullmatch(value)
        return value if placeholder is None else self._targets[int(placeholder.group(1))][1]


# The steps a placeholder (REFERENCE_TARGET_RE) reaches: they read it as they read its URL (no character
# of either opens or closes a comment or a link, or splits the URL group); before any other step, each
# placeholder the markdown-link step left is replaced by its URL.
PLACEHOLDER_STEPS = ("html-comment", "markdown-link")


def _reading_targets(link, targets):
    """The markdown-link replacement link, given each match with a placeholder URL read as its stand-in."""
    return lambda match: link(_TargetMatch(match, targets))


def _with_targets(text, flags, targets):
    """(text, flags) with each placeholder replaced by the URL it stands for, every character of it
    flagged as the placeholder is (no code span starts or ends inside one: it holds no backtick; one can
    still lie inside a code span when a URL copied into the text brought a backtick). The URLs given
    back are charged to the targets' copy budget (_CopyBudget) before any of them is copied."""
    placeholders = list(REFERENCE_TARGET_RE.finditer(text))
    budget = getattr(targets, "budget", None)
    if budget is not None:
        budget.charge(sum(len(targets[int(found.group(1))][0]) for found in placeholders))
    pieces, out_flags, last = [], bytearray(), 0
    for found in placeholders:
        url = targets[int(found.group(1))][0]
        pieces.extend((text[last:found.start()], url))
        if flags is not None:
            out_flags += flags[last:found.start()] + bytearray(flags[found.start():found.start() + 1] * len(url))
        last = found.end()
    pieces.append(text[last:])
    if flags is not None:
        out_flags += flags[last:]
    return "".join(pieces), (None if flags is None else out_flags)


def _normalize(text, skips=(), targets=()):
    """text with the NORMALIZE steps applied in order, except those named in skips. The code spans are
    read once, before the first step; a CODE_SPAN_INERT_STEPS step never sees their content. targets are
    the (URL, stand-in) pairs of the placeholders in text (_resolve_reference_links): the markdown-link
    step reads a placeholder that is a link's URL as its stand-in, and any placeholder left is given back
    its URL before the first step outside PLACEHOLDER_STEPS."""
    flags = _code_flags(text)
    for name, regex, replacement in NORMALIZE:
        if targets and name not in PLACEHOLDER_STEPS:
            text, flags = _with_targets(text, flags, targets)
            targets = ()
        if name in skips:
            continue
        if targets and name == "markdown-link" and callable(replacement):
            replacement = _reading_targets(replacement, targets)
        if flags is None:
            text = _step_sub(regex, replacement, text)
        else:
            text, flags = _masked_sub(regex, replacement, text, flags, name in CODE_SPAN_INERT_STEPS)
    if targets:
        text = _with_targets(text, flags, targets)[0]
    return text

PATTERNS = (
    # (rule id, kind, phrase, regex, contexts) in priority order; a token bound HARD is not also a
    # warning. The self-test exercises every rule id on both sides and with the rule removed.
    ("status-in-progress", HARD, "in progress (an open pull request under review)",
     re.compile(r"^\s*in\s+progress\s*[(:]?\s*" + LIST, re.I), ("status",)),
    ("status-open", HARD, "open (status)",
     re.compile(r"^\s*(?:open|pending|under\s+review|in\s+review)\s*[(:]?\s*" + LIST, re.I), ("status",)),
    ("pending-verb", HARD, "pending", re.compile(LIST + r"\s+" + VERB + r"\s+pending\b", re.I), None),
    ("pending-paren", HARD, "pending", re.compile(LIST + r"\s*\(\s*pending\s*\)", re.I), None),
    ("open-paren", HARD, "open", re.compile(
        LIST + r"\s*\(\s*(?:open|in\s+progress|under\s+review|in\s+review)\s*\)", re.I), None),
    ("pending-batch", HARD, "a pending batch member",
     re.compile(r"\bpending\s+batch\s*\(\s*" + LIST + r"\s*\)", re.I), None),
    ("pending-lead", HARD, "pending", re.compile(r"\bpending\s+(?:merge\s+of\s+)?" + LIST, re.I), None),
    ("open-verb", HARD, "open", re.compile(LIST + r"\s+" + VERB + r"\s+open\b", re.I), None),
    ("open-lead", HARD, "open", re.compile(r"(?<![\w-])open\s+" + LIST, re.I), None),
    ("under-review", HARD, "under review", re.compile(
        LIST + r"\s+" + VERB + r"\s+(?:under|in)\s+review\b", re.I), None),
    ("awaiting-review", HARD, "under review", re.compile(
        LIST + r"\s+" + VERB + r"\s+(?:awaiting|waiting\s+(?:for|on))\s+(?:review|approval)\b", re.I), None),
    ("in-progress-verb", HARD, "in progress", re.compile(LIST + r"\s+" + VERB + r"\s+in\s+progress\b", re.I),
     None),
    ("waiting-to-merge", HARD, "yet to merge", re.compile(
        r"\b(?:waiting|wait|waits)\s+(?:for|on)\s+" + LIST + r"\s+to\s+(?:merge|land|be\s+merged)\b",
        re.I), None),
    ("awaiting-merge", HARD, "yet to merge", re.compile(r"\bawaiting\s+(?:the\s+)?merge\s+of\s+" + LIST, re.I),
     None),
    ("until-merge", HARD, "yet to merge", re.compile(
        r"\buntil\s+" + LIST + r"\s+(?:merges?|lands?|(?:is|are)\s+merged)\b", re.I), None),
    ("once-merge", HARD, "yet to merge", re.compile(
        r"\b(?:once|after)\s+" + LIST + r"\s+(?:merges?|lands?|(?:is|are)\s+merged)\b", re.I), None),
    ("has-yet-to-merge", HARD, "yet to merge", re.compile(
        LIST + r"\s+(?:has|have)\s+yet\s+to\s+(?:merge|land|be\s+merged)\b", re.I), None),
    ("yet-to-be-merged", HARD, "yet to merge", re.compile(
        LIST + r"\s+(?:is|are)\s+yet\s+to\s+be\s+(?:merged|landed)\b", re.I), None),
    ("not-yet-merged", HARD, "not yet merged", re.compile(LIST + r"\s+(?:is|are)\s+not\s+yet\s+merged\b",
                                                          re.I), None),
    ("has-not-merged", HARD, "not yet merged", re.compile(
        LIST + r"\s+(?:has|have)(?:\s+not|n'?t)\s+(?:yet\s+)?(?:been\s+)?(?:merged|landed)\b", re.I), None),
    ("waiting-dependency", WARN, "a dependency",
     re.compile(r"\b(?:waiting|wait|waits)\s+(?:for|on)\s+" + LIST, re.I), None),
    ("awaiting-dependency", WARN, "a dependency", re.compile(r"\bawaiting\s+" + LIST, re.I), None),
    ("blocked-dependency", WARN, "a dependency", re.compile(r"\bblocked\s+(?:on|by)\s+" + LIST, re.I), None),
    ("stacked", WARN, "a stack base", re.compile(r"\b(?:stacked|stacks)\s+(?:on|atop)\s+" + LIST, re.I), None),
)
AMBIGUOUS_RES = (
    re.compile(r"\bpending\s+(?:PRs?|pull\s+requests?)\b(?!\s*\ue000)", re.I),
    re.compile(r"\b(?:the|this|that)\s+(?:PR|pull\s+request)\s+" + VERB + r"\s+(?:pending|open)\b", re.I),
)
IMPERATIVE_OPEN = (WARN, "open (an instruction or a claim; review it)")
# An unnamed pending or open phrase after a cue (There are no pending PRs): a warning, not ambiguous.
CUED_AMBIGUOUS = "cued-ambiguous"


def tokenize(prose, context="prose"):
    """(clauses, refs): the prose normalized (NORMALIZE), each pull request reference replaced by a
    token, split into clauses; refs[k] = (repo or None for the surface default, number, original
    text)."""
    text = " ".join(_normalize(" ".join(prose.split()), (), getattr(prose, "targets", ())).split())
    refs = []

    def replace(match):
        if match.group("un"):
            repo, number = "{}/{}".format(match.group("uo"), match.group("ur")), match.group("un")
        elif match.group("qn"):
            repo, number = "{}/{}".format(match.group("qo"), match.group("qr")), match.group("qn")
        else:
            repo, number = None, match.group("pn") or match.group("hn")
        number = int(number)
        if number < 1 or (repo is not None and not _pr_state.valid_repo(repo)):
            return match.group(0)
        refs.append((repo, number, match.group(0)))
        return "\ue000{}\ue001".format(len(refs) - 1)

    text = REF_RE.sub(replace, text)
    splitter = STATUS_CLAUSE_SPLIT_RE if context == "status" else CLAUSE_SPLIT_RE
    clauses = [clause.strip() for clause in splitter.split(text)]
    return [clause for clause in clauses if clause], refs


WORD_RE = re.compile(r"[A-Za-z]+")


class _ClauseWords:
    """The words of one clause (runs of ASCII letters, lowercased), read once: backwards(start) gives the
    words of clause[:start], last first, without reading the clause again, so a clause with n claims is
    read once, not n times."""

    def __init__(self, clause):
        self.clause = clause
        found = list(WORD_RE.finditer(clause))
        self.starts = [match.start() for match in found]
        self.ends = [match.end() for match in found]
        self.words = [match.group(0).lower() for match in found]

    def backwards(self, start):
        for index in range(bisect.bisect_left(self.starts, start) - 1, -1, -1):
            if self.ends[index] > start:   # a word that start cuts: its part before start
                yield self.clause[self.starts[index]:start].lower()
            else:
                yield self.words[index]


_CLAUSE_WORDS = [None]


def _clause_words(clause):
    """The _ClauseWords of clause, kept for the clause find_candidates is reading."""
    held = _CLAUSE_WORDS[0]
    if held is None or held.clause is not clause:
        held = _CLAUSE_WORDS[0] = _ClauseWords(clause)
    return held


def _instruction_before(clause, start):
    last = next(_clause_words(clause).backwards(start), None)
    return last is not None and last in INSTRUCTION_WORDS


def _imperative_open(clause, start):
    """True when `open #N` starts its clause or follows a comma (Open #17 to see it; To review it,
    open #17): an instruction as likely as a claim, so it warns rather than finds. Only the white space
    directly before start is read."""
    at = start
    while at > 0 and clause[at - 1].isspace():
        at -= 1
    return at == 0 or clause[at - 1] == ","


def _cue_before(clause, start):
    """The cue word (CUES) among the words directly before a claim, read back over CUE_PASSES words
    only, or None: "not waiting", "have not only been waiting", "we'd been waiting", "were not only
    waiting", "Neither #16 nor #17 is open", "If #17 is still open", "no pending PRs". The cue never
    exempts a claim; it makes it a warning. Present perfect with no cue ("have been waiting", "it's
    been waiting") has none, so it stays a finding."""
    for word in _clause_words(clause).backwards(start):
        if word in CUES:
            return word
        if word not in CUE_PASSES:
            return None
    return None


# The separator between two references of one LIST, as LIST spells it.
LIST_SEPARATOR_RE = re.compile(r"\s*,\s*(?:and\s+|or\s+)?|\s+(?:and|or|&)\s+", re.I)
# A rule whose regex starts with LIST is tried only at the first reference of each run of joined
# references (True). The self-test sets it False, running the regex as it stands, for a growth row's red
# leg.
LIST_RUN_STARTS = True


def _claim_matches(regex, clause):
    """regex.finditer(clause). A regex that starts with LIST is tried at each reference, and fails at a
    reference in a run of n joined ones (#1, #2 and #3) only after reading the run to its end, so a
    clause holding such a run would cost O(n^2). Whatever LIST can match from a later reference of the
    run it can match from an earlier one too, so when the regex fails at a reference it fails at every
    reference joined after it; those are passed over, and each run is read once."""
    list_first = _LIST_FIRST.get(regex)
    if list_first is None:
        list_first = _LIST_FIRST[regex] = regex.pattern.startswith(LIST) and bool(regex.flags & re.I)
    if not LIST_RUN_STARTS or not list_first:
        return regex.finditer(clause)
    return _list_run_matches(regex, clause)


_LIST_FIRST = {}   # regex -> whether it starts with LIST (and, as LIST_SEPARATOR_RE, ignores case)


def _list_run_matches(regex, clause):
    """_claim_matches for a regex that starts with LIST."""
    position, failed_end = 0, -1   # failed_end: the end of the last reference the regex failed at
    for mark in REF_MARK_RE.finditer(clause):
        if mark.start() < position:
            continue
        if failed_end >= 0 and LIST_SEPARATOR_RE.fullmatch(clause, failed_end, mark.start()):
            failed_end = mark.end()
            continue
        found = regex.match(clause, mark.start())
        if found is None:
            failed_end = mark.end()
            continue
        failed_end = -1
        yield found
        position = found.end()


def find_candidates(prose, context, budget=None):
    """[(kind, phrase, ref, clause text)] for one unit. A quote context reports any claim as QUOTE (it
    needs a historical lead-in or a move out of the quote); an ambiguous phrase is AMBIGUOUS, whether
    or not a named claim also binds in the same clause, unless every match of it follows a cue
    (CUED_AMBIGUOUS, a warning). A claim after a cue binds as a warning naming the cue. Every entry of
    one clause shares its clause text, built once; each entry is charged to budget (a _CollectBudget, or
    None) before it is kept."""
    clauses, refs = tokenize(prose, context)
    found = []
    for clause in clauses:
        bound = {}
        for rule, kind, phrase, regex, contexts in PATTERNS:
            if contexts is not None and context not in contexts:
                continue
            for match in _claim_matches(regex, clause):
                hit_kind, hit_phrase = kind, phrase
                if rule == "open-lead" and _instruction_before(clause, match.start()):
                    continue
                cue = _cue_before(clause, match.start())
                if cue is not None:
                    hit_kind, hit_phrase = WARN, "{} (after the cue {!r}: negated, past, conditional or a " \
                                                 "question; review it)".format(phrase, cue)
                elif rule == "open-lead" and context != "status" and _imperative_open(clause, match.start()):
                    hit_kind, hit_phrase = IMPERATIVE_OPEN
                for index in REF_MARK_RE.findall(match.group("l")):
                    index = int(index)
                    if index not in bound or (bound[index][0] == WARN and hit_kind == HARD):
                        bound[index] = (hit_kind, hit_phrase)
        shown = REF_MARK_RE.sub(lambda m: refs[int(m.group(1))][2], clause)
        for index in sorted(bound):
            kind, phrase = bound[index]
            if context == "quote":
                kind = QUOTE
            if budget is not None:
                budget.charge(shown, phrase)
            found.append((kind, phrase, refs[index], shown))
        cues = [_cue_before(clause, match.start()) for regex in AMBIGUOUS_RES for match in regex.finditer(clause)]
        if cues and budget is not None:
            budget.charge(shown)
        if None in cues:
            found.append((AMBIGUOUS, "pending or open", None, shown))
        elif cues:
            found.append((CUED_AMBIGUOUS, cues[0], None, shown))
    return found


# ---------------------------------------------------------------------------------------------------
# Evaluation.

class Result:
    def __init__(self):
        self.hard, self.warnings, self.unknown, self.candidates = [], [], [], 0

    def exit_code(self):
        return 2 if self.unknown else (1 if self.hard else 0)


# The report bound: the finding, warning and cannot-evaluate lines built for one document (a declared
# surface, draft or staged blob) hold at most REPORT_MAX_CHARS characters in all. Each line quotes its
# whole clause or line, so n references bound in one clause of length L would need n * L characters; a
# line that would pass the bound is not built (it is refused before building when its clause alone would
# pass it), the document's later lines are not built either, and one cannot-evaluate line naming the
# bound is added in their place (exit 2). The real records never come near it.
REPORT_MAX_CHARS = 4 * 1024 * 1024
# The report bound is enforced (True). The self-test sets it False, building every line, for a red leg.
REPORT_BOUNDED = True


class _ReportBudget:
    """The characters of report lines built so far for each document (by its path), at most
    REPORT_MAX_CHARS each; past that, result.unknown gets one cannot-evaluate line naming the bound."""

    def __init__(self, result):
        self.result, self.used, self.over = result, {}, set()

    def room(self, path, quoted):
        """False when no more lines are built for path: it is past the bound, or a line quoting quoted
        (at least as long as quoted) would pass it."""
        if not REPORT_BOUNDED:
            return True
        if path not in self.over and self.used.get(path, 0) + len(quoted) > REPORT_MAX_CHARS:
            self._refuse(path)
        return path not in self.over

    def add(self, lines, path, line):
        """Append line to lines, unless it passes path's bound."""
        if REPORT_BOUNDED:
            used = self.used.get(path, 0) + len(line)
            if used > REPORT_MAX_CHARS:
                self._refuse(path)
                return
            self.used[path] = used
        lines.append(line)

    def _refuse(self, path):
        self.over.add(path)
        self.result.unknown.append(
            "{}: its report would pass {} characters (REPORT_MAX_CHARS), a bound on memory; the lines "
            "past it were not built, so this document's findings and warnings are incomplete".format(
                path, REPORT_MAX_CHARS))


# The collection bound: an allowance of COLLECT_MAX_CHARS characters per document; past it the document
# is cannot-evaluate (exit 2), naming the bound, before the entry that would pass it is kept. Each entry
# is charged COLLECT_ENTRY_CHARS for itself plus the length of these fields only: a record unit its
# location (not its text); a claim entry its quoted clause and its phrase; an ambiguous-phrase entry its
# quoted clause; an unevaluated-reference entry its location and its quoted line. A field the entry
# charged just before also held (the same object) is not counted again: a clause or line is built once
# and shared by every entry that quotes it, so it is counted once, while a string built anew for each
# entry (a table cell's location naming a long header cell, a TOML string's location naming a long key
# path) is counted each time. It is a count of characters in those fields, not of memory: the input, the
# parsed TOML document, the units' texts, temporaries and object overhead are not counted (the entry
# charge is nominal). The report bound (REPORT_MAX_CHARS) runs after collection and only on the lines it
# builds; this bound keeps collection itself from growing past it first. The real records never come
# near it.
COLLECT_MAX_CHARS = REPORT_MAX_CHARS
COLLECT_ENTRY_CHARS = 32
# The collection bound is enforced (True). The self-test sets it False, collecting without a bound, for a
# red leg.
COLLECT_BOUNDED = True
# Every entry found on one line shares that line's quoted text and location, each built once (True). The
# self-test sets it False, building them again for each entry as before, for a red leg.
SHARED_QUOTES = True


class _CollectBudget:
    """The characters one document's candidate collection may still hold (COLLECT_MAX_CHARS)."""

    def __init__(self, label):
        self.label, self.left, self.held = label, COLLECT_MAX_CHARS, ()

    def charge(self, *held):
        """Charge one entry holding the strings held; InputError (cannot evaluate) once the bound is
        passed. A string the entry charged just before also held (the same object) is not counted again."""
        if not COLLECT_BOUNDED:
            return
        cost = COLLECT_ENTRY_CHARS
        for text in held:
            if text is not None and not any(text is earlier for earlier in self.held):
                cost += len(text)
        self.held = held
        self.left -= cost
        if self.left < 0:
            raise InputError("{}: collecting its candidates would hold more than {} characters "
                             "(COLLECT_MAX_CHARS), a bound on memory".format(self.label, COLLECT_MAX_CHARS))


def collect(surfaces_with_bytes):
    """[(surface, location, kind, phrase, ref, clause)] over (surface, bytes) pairs, with a HIDDEN entry
    (phrase: the block kind or not evaluated; clause: the line) for each unevaluated-reference entry; InputError
    propagates, including a document past the collection bound (COLLECT_MAX_CHARS)."""
    out = []
    for surface, data in surfaces_with_bytes:
        hidden = []
        budget = _CollectBudget(surface["path"])
        for location, prose, context in surface_units(surface, data, hidden, budget):
            for kind, phrase, ref, clause in find_candidates(prose, context, budget):
                out.append((surface, location, kind, phrase, ref, clause))
        for location, name, ref, line in hidden:
            out.append((surface, location, HIDDEN, name, ref, line))
    return out


def evaluate(candidates, config, root, transport=None, git_runner=_pr_state.run_git_origin):
    """The Result of judging the candidates against live pull request state."""
    result = Result()
    result.candidates = sum(1 for candidate in candidates if candidate[2] != HIDDEN)
    budget = _ReportBudget(result)
    origin_cache = {}
    keyed = []
    for surface, location, kind, phrase, ref, clause in candidates:
        path = surface["path"]
        if kind == HIDDEN:
            if budget.room(path, clause):
                budget.add(result.warnings, path, _hidden_warning(location, phrase, ref, clause))
            continue
        if kind == AMBIGUOUS:
            if budget.room(path, clause):
                budget.add(result.unknown, path, "{}: {!r}: a pending or open phrase whose pull request is "
                           "not named; the binding is ambiguous".format(location, clause))
            continue
        if kind == CUED_AMBIGUOUS:
            if budget.room(path, clause):
                budget.add(result.warnings, path, "{}: {!r}: a pending or open phrase whose pull request is "
                           "not named, after the cue {!r} (negated, past, conditional or a question); no "
                           "pull request was read; review it".format(location, clause, phrase))
            continue
        if kind == QUOTE:
            if budget.room(path, clause):
                budget.add(result.unknown, path, "{}: {!r}: a claim inside a blockquote with no historical "
                           "lead-in (Earlier note: or Previously recorded:); review it".format(
                               location, clause))
            continue
        repo, number, text = ref
        if repo is None:
            repo, why = _surface_repo(surface, root, origin_cache, git_runner)
            if repo is None:
                if budget.room(path, clause):
                    budget.add(result.unknown, path, "{}: {!r}: {} cannot be bound to a repository: {}".format(
                        location, clause, text, why))
                continue
        keyed.append(((repo, number), location, kind, phrase, clause, path))
    limits = config["limits"]
    states = _pr_state.read_states([key for key, *_rest in keyed], transport=transport,
                                   max_prs=limits["max-prs"],
                                   call_timeout=limits["call-timeout-seconds"],
                                   deadline=limits["deadline-seconds"]) if keyed else {}
    for (repo, number), location, kind, phrase, clause, path in keyed:
        state, why, when = states[(repo, number)]
        name = "{}#{}".format(repo, number)
        read = " (read {})".format(when) if when else ""
        if state == _pr_state.OPEN or not budget.room(path, clause):
            continue
        if state == _pr_state.UNKNOWN:
            budget.add(result.unknown, path, "{}: {!r}: {} state cannot be evaluated: {}{}".format(
                location, clause, name, why, read))
        else:
            line = "{}: {!r}: says {} is {}, but it is {}{}".format(location, clause, name, phrase,
                                                                     state, read)
            budget.add(result.hard if kind == HARD else result.warnings, path, line)
    return result


def _hidden_warning(location, name, ref, line):
    """The warning for a pull request reference that reached no evaluated record unit: the file and line
    (location), the reference and the reason, not evaluated, with the block kind (name) when the block
    classifier hid the line; no pull request state is read for it."""
    if name == NOT_EVALUATED:
        why = "no record unit the check read carries it (a claim there was dropped before it was read)"
    else:
        why = "it appears only inside {} {}, which is not read as a current claim".format(
            "an" if name[0] in "aeiouH" else "a", name)
    return ("{}: {!r}: the pull request reference {} was {}: {}; its state was not read; review it (or "
            "mark an example fence with the info string {})".format(
                location, line, ref[2], NOT_EVALUATED, why,
                " or ".join("`" + word + "`" for word in EXAMPLE_FENCE_INFO)))


def _surface_repo(surface, root, cache, git_runner):
    if surface["repository"] != "origin":
        return surface["repository"], None
    if "origin" not in cache:
        cache["origin"] = _pr_state.origin_repo(root, git_runner)
    repo, why = cache["origin"]
    expected = surface.get("expected-repository")
    if repo is not None and expected is not None and repo.casefold() != expected.casefold():
        return None, "the origin names {}, not the declared expected repository {}".format(repo, expected)
    return repo, why


def report(result, stream=None):
    stream = stream or sys.stdout
    for line in result.hard:
        print("FINDING: " + line, file=stream)
    for line in result.warnings:
        print("WARNING: " + line, file=stream)
    for line in result.unknown:
        print("CANNOT EVALUATE: " + line, file=stream)
    code = result.exit_code()
    if code == 0:
        if result.candidates:
            print("PASS: {} candidate claim(s) evaluated; none contradicts its pull request's lifecycle"
                  "{}".format(result.candidates, " (advisory warnings above)" if result.warnings else ""),
                  file=stream)
        else:
            print("PASS: no supported current pending-claim found (this is not proof that none exists)"
                  "{}".format(" (advisory warnings above)" if result.warnings else ""), file=stream)
    elif code == 2:
        print("INCOMPLETE: the evaluation did not complete; this is not a pass", file=stream)
    return code


# ---------------------------------------------------------------------------------------------------
# Modes.

def _declared(config, rel):
    for surface in config["surface"]:
        if surface["path"] == rel:
            return surface
    return None


def check_inputs(root):
    """--check-inputs: offline validation; returns the exit code."""
    try:
        config = load_config(root)
        pairs = [(s, _read_bytes(Path(root) / s["path"], s["path"])) for s in config["surface"]]
        candidates = collect(pairs)
    except InputError as exc:
        print("CANNOT EVALUATE: {}".format(exc), file=sys.stderr)
        return 2
    built = Result()
    budget = _ReportBudget(built)
    for surface, location, kind, phrase, ref, clause in candidates:
        if kind == HIDDEN and budget.room(surface["path"], clause):
            budget.add(built.warnings, surface["path"], _hidden_warning(location, phrase, ref, clause))
    if built.unknown:
        for line in built.unknown:
            print("CANNOT EVALUATE: {}".format(line), file=sys.stderr)
        return 2
    hidden = built.warnings
    for line in hidden:
        print("WARNING: " + line)
    print("PASS: {} declared surface(s) parse; {} candidate claim(s) found; {} unevaluated-reference "
          "warning(s). Offline input validation only: pull request currency was NOT assessed".format(
              len(pairs), len(candidates) - len(hidden), len(hidden)))
    return 0


def run_all(root, transport=None, git_runner=_pr_state.run_git_origin):
    try:
        config = load_config(root)
        pairs = [(s, _read_bytes(Path(root) / s["path"], s["path"])) for s in config["surface"]]
        candidates = collect(pairs)
    except InputError as exc:
        print("CANNOT EVALUATE: {}".format(exc), file=sys.stderr)
        return 2
    return report(evaluate(candidates, config, root, transport, git_runner))


def judge_draft_bytes(root, target, data, transport=None, git_runner=_pr_state.run_git_origin):
    """The exit code for complete proposed bytes of the declared surface target (a repo-relative path),
    with the findings printed. Used by --draft and by tools/apply_record_draft.py."""
    try:
        config = load_config(root)
        surface = _declared(config, target)
        if surface is None:
            raise InputError("{}: is not a declared surface in {}".format(target, CONFIG_REL))
        if len(data) > MAX_BYTES:
            raise InputError("{}: draft larger than {} bytes".format(target, MAX_BYTES))
        candidates = collect([(surface, data)])
    except InputError as exc:
        print("CANNOT EVALUATE: {}".format(exc), file=sys.stderr)
        return 2
    return report(evaluate(candidates, config, root, transport, git_runner))


def run_draft(root, draft, target, transport=None):
    try:
        data = _read_bytes(Path(draft), draft)
    except InputError as exc:
        print("CANNOT EVALUATE: {}".format(exc), file=sys.stderr)
        return 2
    return judge_draft_bytes(root, target, data, transport)


STAGED_NAMES = ["diff", "--cached", "--name-only", "-z", "--no-renames"]
OBJECT_ID_RE = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")
# Index modes of a regular file; a symlink (120000), a gitlink (160000) or anything else is refused.
REGULAR_INDEX_MODES = ("100644", "100755")


def _git(root, args):
    """(returncode, stdout bytes) of one of the three read-only index reads run_staged makes: the staged
    path list (args == STAGED_NAMES), one path's index entry (args == ["ls-files", "--stage", "-z",
    "--", PATH], pathspecs read literally) or one blob by object id (args == ["cat-file", "blob",
    OID]). Each launch is a literal argument list; any other args is a ValueError."""
    if args == STAGED_NAMES:
        child = subprocess.run(["git", "-C", str(root), "diff", "--cached", "--name-only", "-z",
                                "--no-renames"], capture_output=True, stdin=subprocess.DEVNULL, timeout=60)
    elif len(args) == 5 and args[:4] == ["ls-files", "--stage", "-z", "--"]:
        child = subprocess.run(["git", "--literal-pathspecs", "-C", str(root), "ls-files", "--stage", "-z",
                                "--", args[4]], capture_output=True, stdin=subprocess.DEVNULL, timeout=60)
    elif len(args) == 3 and args[:2] == ["cat-file", "blob"] and OBJECT_ID_RE.fullmatch(args[2]):
        child = subprocess.run(["git", "-C", str(root), "cat-file", "blob", args[2]], capture_output=True,
                               stdin=subprocess.DEVNULL, timeout=60)
    else:
        raise ValueError("unsupported git read {!r}".format(args))
    return child.returncode, child.stdout


def _staged_object(path, listing):
    """The object id of path's single stage-0 index entry given `git ls-files --stage -z` output;
    InputError when the entry is missing, unmerged, or not a regular file (a symlink, gitlink or other
    mode is refused as cannot-evaluate, as a non-regular declared surface is in the other modes)."""
    entries = []
    for record in listing.decode("utf-8", "surrogateescape").split("\0"):
        if not record:
            continue
        meta, _tab, name = record.partition("\t")
        fields = meta.split(" ")
        if name == path and len(fields) == 3:
            entries.append(fields)
    if not entries:
        raise InputError("{}: has no staged index entry (deleted or unreadable)".format(path))
    if len(entries) != 1 or entries[0][2] != "0":
        raise InputError("{}: the index entry is unmerged".format(path))
    mode, oid, _stage = entries[0]
    if mode not in REGULAR_INDEX_MODES:
        raise InputError("{}: is staged as mode {}, not a regular file (a symlink or other non-regular "
                         "entry is refused)".format(path, mode))
    if not OBJECT_ID_RE.fullmatch(oid):
        raise InputError("{}: the index entry names no valid object id".format(path))
    return oid


def run_staged(root, transport=None, git_runner=_pr_state.run_git_origin, git=_git):
    """--staged: judge the staged blob (complete content, unchanged context included) of every declared
    surface the index changes."""
    try:
        config = load_config(root)
        code, out = git(root, STAGED_NAMES)
        if code != 0:
            raise InputError("git diff --cached exited {}".format(code))
        staged = set(out.decode("utf-8", "surrogateescape").split("\0")) - {""}
        pairs = []
        for surface in config["surface"]:
            if surface["path"] not in staged:
                continue
            code, listing = git(root, ["ls-files", "--stage", "-z", "--", surface["path"]])
            if code != 0:
                raise InputError("{}: the index entry cannot be read (git exited {})".format(
                    surface["path"], code))
            code, blob = git(root, ["cat-file", "blob", _staged_object(surface["path"], listing)])
            if code != 0:
                raise InputError("{}: the staged blob cannot be read (git exited {})".format(
                    surface["path"], code))
            if len(blob) > MAX_BYTES:
                raise InputError("{}: staged blob larger than {} bytes".format(surface["path"], MAX_BYTES))
            pairs.append((surface, blob))
        candidates = collect(pairs)
    except (InputError, OSError, subprocess.SubprocessError) as exc:
        print("CANNOT EVALUATE: {}".format(exc), file=sys.stderr)
        return 2
    if not pairs:
        print("PASS: no declared record surface is staged; nothing to judge")
        return 0
    return report(evaluate(candidates, config, root, transport, git_runner))


# ---------------------------------------------------------------------------------------------------
# Self-test (synthetic lifecycle responses only; no network, no real gh).

SUITE_ID = "pending-pr-claims-selftest"
EXECUTED = []
FAILURES = []


def check(name, ok, detail=""):
    if name in EXECUTED:
        print("SELF-TEST HARNESS ERROR: duplicate check id {!r}".format(name), file=sys.stderr)
        sys.exit(2)
    EXECUTED.append(name)
    if not ok:
        FAILURES.append("{}: {!r}".format(name, detail))


REPO = "acme/widgets"


def _response(number, state, merged_at=None, repo=REPO, **extra):
    import json
    obj = {"number": number, "state": state, "merged_at": merged_at,
           "html_url": "https://github.com/{}/pull/{}".format(repo, number),
           "base": {"repo": {"full_name": repo}}}
    obj.update(extra)
    return json.dumps(obj).encode("utf-8")


MERGED_AT = "2026-11-01T10:00:00Z"
LIFECYCLE = {"open": ("open", None), "merged": ("closed", MERGED_AT), "closed": ("closed", None)}


class FakeTransport:
    """In-process stand-in for gh: {(repo, number): lifecycle name or (code, stdout, stderr)}."""

    def __init__(self, table):
        self.table, self.calls = table, []

    def __call__(self, repo, number, timeout):
        self.calls.append((repo, number))
        entry = self.table.get((repo, number), (1, b"", b"gh: Not Found (HTTP 404)"))
        if isinstance(entry, str):
            state, merged_at = LIFECYCLE[entry]
            return 0, _response(number, state, merged_at, repo), b""
        if isinstance(entry, BaseException):
            raise entry
        return entry


def _origin_ok(_root):
    return 0, ("https://github.com/" + REPO + ".git\n").encode()


MD_SURFACE = {"path": "NOTES.md", "format": "markdown", "repository": "origin", "role": "source",
              "status-column": "Status"}
TOML_SURFACE = {"path": "changes.toml", "format": "toml-strings", "repository": "origin", "role": "source"}
CONFIG = {"schema-version": 1, "limits": {"max-prs": 100, "call-timeout-seconds": 10,
                                          "deadline-seconds": 120}, "surface": [MD_SURFACE, TOML_SURFACE]}


def _judge(text, table, surface=MD_SURFACE, config=CONFIG, git_runner=_origin_ok):
    """(exit code, Result, transport) for one synthetic surface body."""
    transport = FakeTransport(table)
    candidates = collect([(surface, text.encode("utf-8"))])
    result = evaluate(candidates, config, "/nonexistent", transport, git_runner)
    return result.exit_code(), result, transport


def _numbers(lines):
    return sorted(int(n) for line in lines for n in re.findall(r"#([0-9]+)", line.split(": says ")[-1]
                                                                 .split(" is ")[0]))


def _with_rules_removed(rules, fn):
    """Run fn() with the PATTERNS rows of the given rule ids removed (the red-when-reverted leg)."""
    global PATTERNS
    saved = PATTERNS
    unknown = set(rules) - {row[0] for row in saved}
    if unknown:
        raise ValueError("no such rule {}".format(sorted(unknown)))
    PATTERNS = tuple(row for row in saved if row[0] not in rules)
    try:
        return fn()
    finally:
        PATTERNS = saved


def _with_steps_removed(steps, fn):
    """Run fn() with the NORMALIZE steps of the given names removed (the red-when-reverted leg)."""
    global NORMALIZE
    saved = NORMALIZE
    unknown = set(steps) - {row[0] for row in saved}
    if unknown:
        raise ValueError("no such step {}".format(sorted(unknown)))
    NORMALIZE = tuple(row for row in saved if row[0] not in steps)
    try:
        return fn()
    finally:
        NORMALIZE = saved


def _without_imperative_open(fn):
    """Run fn() with the imperative `open #N` downgrade disabled (the red-when-reverted leg)."""
    global _imperative_open
    saved = _imperative_open
    _imperative_open = lambda _clause, _start: False  # noqa: E731
    try:
        return fn()
    finally:
        _imperative_open = saved


def _without_negation(fn):
    """Run fn() with the cue reading disabled, so no claim is downgraded (the red-when-reverted leg)."""
    return _with_globals({"_cue_before": lambda _clause, _start: None}, fn)


def _with_globals(replacements, fn):
    """Run fn() with the named module globals replaced, restoring them afterwards (a red leg)."""
    names = globals()
    saved = {name: names[name] for name in replacements}
    names.update(replacements)
    try:
        return fn()
    finally:
        names.update(saved)


def _limit_address_space():
    """Limit the calling process (a self-test child, before it runs the checker) to 512 MiB of address
    space, so collection that would grow past it ends in a MemoryError instead of exhausting the host."""
    import resource
    limit = 512 * 1024 * 1024
    resource.setrlimit(resource.RLIMIT_AS, (limit, limit))


def _status_table(cell):
    return "| ID | Status |\n| --- | --- |\n| X-1 | {} |\n".format(cell)


# Every grammar rule has one row in _rule_rows: (rule id, a text the rule alone binds to #17, its
# no-flag twin, then the five literal check ids). Each is checked merged, open and unreadable, its twin
# merged, and the text again with the rule removed.


def _rule_rows(m, o):
    kinds = {row[0]: row[1] for row in PATTERNS}
    seen = []
    for (rule, text, twin, merged_id, red_id, open_id, unknown_id, twin_id) in (
            ("status-in-progress", _status_table("In progress: #17"), _status_table("Done: #17"),
             "rule/status-in-progress/merged", "rule/status-in-progress/merged/red-when-reverted", "rule/status-in-progress/open-passes", "rule/status-in-progress/unknown-cannot-evaluate", "rule/status-in-progress/no-flag-twin"),
            ("status-open", _status_table("Open (#17)"), _status_table("Merged (#17)"),
             "rule/status-open/merged", "rule/status-open/merged/red-when-reverted", "rule/status-open/open-passes", "rule/status-open/unknown-cannot-evaluate", "rule/status-open/no-flag-twin"),
            ("pending-verb", "PR #17 remains pending.\n", "PR #17 was pending.\n",
             "rule/pending-verb/merged", "rule/pending-verb/merged/red-when-reverted", "rule/pending-verb/open-passes", "rule/pending-verb/unknown-cannot-evaluate", "rule/pending-verb/no-flag-twin"),
            ("pending-paren", "Fix in PR #17 (pending).\n", "Fix in PR #17 (merged).\n",
             "rule/pending-paren/merged", "rule/pending-paren/merged/red-when-reverted", "rule/pending-paren/open-passes", "rule/pending-paren/unknown-cannot-evaluate", "rule/pending-paren/no-flag-twin"),
            ("open-paren", "Fix in PR #17 (open).\n", "Fix in PR #17 (closed).\n",
             "rule/open-paren/merged", "rule/open-paren/merged/red-when-reverted", "rule/open-paren/open-passes", "rule/open-paren/unknown-cannot-evaluate", "rule/open-paren/no-flag-twin"),
            ("pending-batch", "This ships in the pending batch (#17).\n", "This shipped in the merged batch (#17).\n",
             "rule/pending-batch/merged", "rule/pending-batch/merged/red-when-reverted", "rule/pending-batch/open-passes", "rule/pending-batch/unknown-cannot-evaluate", "rule/pending-batch/no-flag-twin"),
            ("pending-lead", "The pending PR #17 needs a rebase.\n", "The merged PR #17 needed a rebase.\n",
             "rule/pending-lead/merged", "rule/pending-lead/merged/red-when-reverted", "rule/pending-lead/open-passes", "rule/pending-lead/unknown-cannot-evaluate", "rule/pending-lead/no-flag-twin"),
            ("open-verb", "PR #17 is still open.\n", "PR #17 is no longer open.\n",
             "rule/open-verb/merged", "rule/open-verb/merged/red-when-reverted", "rule/open-verb/open-passes", "rule/open-verb/unknown-cannot-evaluate", "rule/open-verb/no-flag-twin"),
            ("open-lead", "The open PR #17 needs a rebase.\n", "The closed PR #17 needed a rebase.\n",
             "rule/open-lead/merged", "rule/open-lead/merged/red-when-reverted", "rule/open-lead/open-passes", "rule/open-lead/unknown-cannot-evaluate", "rule/open-lead/no-flag-twin"),
            ("under-review", "PR #17 remains under review.\n", "PR #17 was under review.\n",
             "rule/under-review/merged", "rule/under-review/merged/red-when-reverted", "rule/under-review/open-passes", "rule/under-review/unknown-cannot-evaluate", "rule/under-review/no-flag-twin"),
            ("awaiting-review", "PR #17 is awaiting review.\n", "PR #17 was awaiting review.\n",
             "rule/awaiting-review/merged", "rule/awaiting-review/merged/red-when-reverted", "rule/awaiting-review/open-passes", "rule/awaiting-review/unknown-cannot-evaluate", "rule/awaiting-review/no-flag-twin"),
            ("in-progress-verb", "PR #17 is in progress.\n", "PR #17 was in progress.\n",
             "rule/in-progress-verb/merged", "rule/in-progress-verb/merged/red-when-reverted", "rule/in-progress-verb/open-passes", "rule/in-progress-verb/unknown-cannot-evaluate", "rule/in-progress-verb/no-flag-twin"),
            ("waiting-to-merge", "Waiting for #17 to merge.\n", "We waited for #17 to merge.\n",
             "rule/waiting-to-merge/merged", "rule/waiting-to-merge/merged/red-when-reverted", "rule/waiting-to-merge/open-passes", "rule/waiting-to-merge/unknown-cannot-evaluate", "rule/waiting-to-merge/no-flag-twin"),
            ("awaiting-merge", "Awaiting merge of #17.\n", "Awaiting deployment of #17.\n",
             "rule/awaiting-merge/merged", "rule/awaiting-merge/merged/red-when-reverted", "rule/awaiting-merge/open-passes", "rule/awaiting-merge/unknown-cannot-evaluate", "rule/awaiting-merge/no-flag-twin"),
            ("until-merge", "Hold the release until #17 merges.\n", "Held the release until #17 merged.\n",
             "rule/until-merge/merged", "rule/until-merge/merged/red-when-reverted", "rule/until-merge/open-passes", "rule/until-merge/unknown-cannot-evaluate", "rule/until-merge/no-flag-twin"),
            ("once-merge", "Rebase once #17 lands.\n", "Rebased once #17 landed.\n",
             "rule/once-merge/merged", "rule/once-merge/merged/red-when-reverted", "rule/once-merge/open-passes", "rule/once-merge/unknown-cannot-evaluate", "rule/once-merge/no-flag-twin"),
            ("has-yet-to-merge", "PR #17 has yet to merge.\n", "PR #17 has merged.\n",
             "rule/has-yet-to-merge/merged", "rule/has-yet-to-merge/merged/red-when-reverted", "rule/has-yet-to-merge/open-passes", "rule/has-yet-to-merge/unknown-cannot-evaluate", "rule/has-yet-to-merge/no-flag-twin"),
            ("yet-to-be-merged", "PR #17 is yet to be merged.\n", "PR #17 is merged.\n",
             "rule/yet-to-be-merged/merged", "rule/yet-to-be-merged/merged/red-when-reverted", "rule/yet-to-be-merged/open-passes", "rule/yet-to-be-merged/unknown-cannot-evaluate", "rule/yet-to-be-merged/no-flag-twin"),
            ("not-yet-merged", "#17 is not yet merged.\n", "#17 is merged now.\n",
             "rule/not-yet-merged/merged", "rule/not-yet-merged/merged/red-when-reverted", "rule/not-yet-merged/open-passes", "rule/not-yet-merged/unknown-cannot-evaluate", "rule/not-yet-merged/no-flag-twin"),
            ("has-not-merged", "PR #17 hasn't merged yet.\n", "PR #17 has merged since.\n",
             "rule/has-not-merged/merged", "rule/has-not-merged/merged/red-when-reverted", "rule/has-not-merged/open-passes", "rule/has-not-merged/unknown-cannot-evaluate", "rule/has-not-merged/no-flag-twin"),
            ("waiting-dependency", "Still waiting on #17.\n", "Waited on #17 last week.\n",
             "rule/waiting-dependency/merged", "rule/waiting-dependency/merged/red-when-reverted", "rule/waiting-dependency/open-passes", "rule/waiting-dependency/unknown-cannot-evaluate", "rule/waiting-dependency/no-flag-twin"),
            ("awaiting-dependency", "Awaiting #17.\n", "Awaited #17 last week.\n",
             "rule/awaiting-dependency/merged", "rule/awaiting-dependency/merged/red-when-reverted", "rule/awaiting-dependency/open-passes", "rule/awaiting-dependency/unknown-cannot-evaluate", "rule/awaiting-dependency/no-flag-twin"),
            ("blocked-dependency", "Blocked on #17.\n", "Unblocked by #17.\n",
             "rule/blocked-dependency/merged", "rule/blocked-dependency/merged/red-when-reverted", "rule/blocked-dependency/open-passes", "rule/blocked-dependency/unknown-cannot-evaluate", "rule/blocked-dependency/no-flag-twin"),
            ("stacked", "This branch is stacked on #17.\n", "This branch was unstacked from #17.\n",
             "rule/stacked/merged", "rule/stacked/merged/red-when-reverted", "rule/stacked/open-passes", "rule/stacked/unknown-cannot-evaluate", "rule/stacked/no-flag-twin"),
    ):
        seen.append((rule, merged_id, red_id, open_id, unknown_id, twin_id))
        hard = kinds.get(rule) == HARD
        code, res, t = _judge(text, m)
        flagged = (code == 1 and len(res.hard) == 1) if hard else (
            code == 0 and len(res.warnings) == 1 and not res.hard)
        check(merged_id, flagged and t.calls == [(REPO, 17)], (code, res.hard, res.warnings))
        red = _with_rules_removed({rule}, lambda: _judge(text, m)[1]) if rule in kinds else None
        check(red_id, red is not None and (not red.hard if hard else not red.warnings),
              None if red is None else (red.hard, red.warnings))
        code, res, _t = _judge(text, o)
        check(open_id, code == 0 and not res.hard and not res.warnings, (code, res.hard, res.warnings))
        code, res, _t = _judge(text, {})
        check(unknown_id, code == 2 and not res.hard, res.unknown)
        code, res, _t = _judge(twin, m)
        check(twin_id, code == 0 and not res.hard and not res.warnings, (code, res.hard, res.warnings))
    ids_match = all(all(cid.startswith("rule/{}/".format(row[0])) for cid in row[1:]) for row in seen)
    check("rule/every-rule-has-a-row", ids_match and sorted(row[0] for row in seen) == sorted(kinds),
          sorted(set(kinds) ^ {row[0] for row in seen}))


# A definition inside a fenced block or an HTML comment, met first, must not shadow the live one after
# the claim; backtick lines six columns deep in a list item (four past its content column) are indented
# code, not a fence, so the list prose between them is read.
FENCED_DEFINITION = ("```md\n[pr17]: https://github.com/acme/widgets/issues/17\n```\n\n[#17][pr17] is pending.\n\n"
                     "[pr17]: https://github.com/acme/widgets/pull/17\n")
COMMENT_DEFINITION = ("<!--\n\n[pr17]: https://github.com/acme/widgets/issues/17\n\n-->\n\n[#17][pr17] is pending.\n\n"
                      "[pr17]: https://github.com/acme/widgets/pull/17\n")
OVERINDENTED_BACKTICKS = "- Item\n\n      ```\n\n  PR #17 is pending.\n\n      ```\n"
# A fence opened right after a list marker closes inside that item, so the item text after it is read
# (read as a paragraph, the closing fence would open a fence that `outside` closes, hiding the claim).
FENCE_AFTER_BULLET = "- ```\n  example\n  ```\n\n  PR #17 is pending.\n\noutside\n"
FENCE_AFTER_ORDERED = "1. ```\n   example\n   ```\n\n   PR #17 is pending.\n\noutside\n"
# A no-break space is not indentation: the backticks after it are text, not a fence.
NBSP_BACKTICKS = " ```\nPR #17 is pending.\n ```\n"
# The text after a closing `-->` is paragraph text as it stands, not a line indented four columns.
COMMENT_TAIL = "<!-- a\n-->    PR #17 is pending.\n"
INLINE_COMMENT_TAIL = "Intro <!-- a\nb --> ```\nPR #17 is pending.\n"
# A second comment opened after a closing `-->` (of a comment block, or of an inline comment) hides the
# lines up to its own `-->`; its contents were joined into one paragraph and erased unseen.
REOPENED_BLOCK_COMMENT = "<!-- first\n--> <!-- second\nPR #17 is pending.\n-->\n"
REOPENED_INLINE_COMMENT = "Intro <!-- first\n--> <!-- second\nPR #17 is pending.\n-->\n"
# A table header cell is read as prose as well as naming its column.
HEADER_CLAIM = "| PR #17 is pending | Status |\n| --- | --- |\n| release | queued |\n"
# A toml-strings value whose comment spans its lines: normalization erases the claim inside it.
TOML_MULTILINE_COMMENT = "note = \"Release notes <!-- a\\nPR #17 is pending --> follow.\"\n"
# The verb alternation before the modifier suffix applied to `still` and `currently` (a red leg).
VERB_BEFORE_STILL_MODIFIERS = (r"(?:(?:is|are|remains?|stays?|(?:has|have)\s+been)"
                               r"(?:\s+(?:still|currently|now|also|both)){0,2}|still|currently)")


def _no_block_context(lines):
    """block_context as it was before it existed for definitions: every non-blank line is read, and one
    shaped like a definition is collected wherever it stands (a red-when-reverted leg)."""
    return [(BLANK, "") if not line.strip() else (DEFINITION if LINK_DEFINITION_RE.match(line) else PARA, line)
            for line in lines]


def _block_context_rows(m):
    """The red-when-reverted legs of the block context rows and the verb modifier row, and an HTML
    comment that is never closed (cannot-evaluate)."""
    for name, replacements, text in (
            ("markdown/fenced-definition-not-collected/red-when-reverted", {"block_context": _no_block_context},
             FENCED_DEFINITION),
            ("markdown/comment-definition-not-collected/red-when-reverted",
             {"_comment_start": lambda _text: None}, COMMENT_DEFINITION),
            ("markdown/overindented-backticks-in-list-not-a-fence/red-when-reverted",
             {"FENCE_MAX_RELATIVE_INDENT": 99}, OVERINDENTED_BACKTICKS),
            ("verb/still-currently-open/red-when-reverted",
             {"PATTERNS": tuple((rule, kind, phrase, re.compile(regex.pattern.replace(
                 VERB, VERB_BEFORE_STILL_MODIFIERS), regex.flags), contexts)
                 for rule, kind, phrase, regex, contexts in PATTERNS)}, "#17 still currently open.\n")):
        try:
            code, res, t = _with_globals(replacements, lambda: _judge(text, m))
            ok, detail = code == 0 and t.calls == [] and not res.hard, (code, t.calls, res.hard)
        except InputError as exc:
            ok, detail = False, str(exc)
        check(name, ok, detail)
    try:
        _judge("Intro.\n\n<!-- PR #17 is pending\n\nnever closed\n", m)
        got = None
    except InputError as exc:
        got = str(exc)
    check("markdown/unclosed-comment-cannot-evaluate", got is not None and "never closed" in got, got)


def _fix_rows(m, o):
    """Reference spellings, Markdown structure, negation and binding fixes, each with a twin on the
    other side and, where a step or function carries the fix, its red-when-reverted leg."""
    two = {(REPO, 17): "merged", (REPO, 18): "merged"}
    link = "[#17](https://github.com/acme/widgets/pull/17) is pending.\n"
    for name, text, table, want, calls in (
            ("ref/markdown-link-hash", link, m, 1, [(REPO, 17)]),
            ("ref/markdown-link-words", "[PR #17](https://github.com/acme/widgets/pull/17) remains open.\n",
             m, 1, [(REPO, 17)]),
            ("ref/markdown-link-target-repository", "[#17](https://github.com/peer/repo/pull/17) is pending.\n",
             {("peer/repo", 17): "merged"}, 1, [("peer/repo", 17)]),
            ("ref/markdown-link-no-claim", "[#17](https://github.com/acme/widgets/pull/17) merged.\n", m, 0, []),
            ("ref/autolink", "<https://github.com/acme/widgets/pull/17> is pending.\n", m, 1, [(REPO, 17)]),
            ("ref/glued-pr", "PR#17 is pending.\n", m, 1, [(REPO, 17)]),
            ("ref/glued-pr-no-claim", "PR#17 merged.\n", m, 0, []),
            ("ref/underscore-emphasis", "_#17_ is pending.\n", m, 1, [(REPO, 17)]),
            ("ref/underscore-emphasis-no-claim", "_#17_ merged.\n", m, 0, []),
            ("ref/slash-list", "#17/#18 are pending.\n", two, 1, [(REPO, 17), (REPO, 18)]),
            ("ref/slash-list-no-claim", "#17/#18 merged.\n", two, 0, []),
            ("ref/url-files-suffix", "https://github.com/acme/widgets/pull/17/files is pending.\n", m, 1,
             [(REPO, 17)]),
            ("ref/url-files-suffix-no-claim", "https://github.com/acme/widgets/pull/17/files merged.\n", m, 0, []),
            ("ref/issue-not-a-pull-request", "Issue #17 remains open.\n", {}, 0, []),
            ("ref/issue-list-not-pull-requests", "Issues #17 and #18 remain open.\n", {}, 0, []),
            ("ref/issue-link-not-a-pull-request",
             "[#17](https://github.com/acme/widgets/issues/17) remains open.\n", {}, 0, []),
            ("ref/pull-request-beside-issue", "Issue #18 is fixed; PR #17 remains open.\n", m, 1, [(REPO, 17)]),
            ("format/strikethrough-not-current", "~~PR #17 is pending~~ merged.\n", m, 0, []),
            ("format/strikethrough-word-removed", "PR #17 is ~~open~~ pending.\n", m, 1, [(REPO, 17)]),
            ("format/html-comment-not-current", "<!-- PR #17 is pending -->\n", m, 0, []),
            ("format/text-beside-html-comment", "<!-- note --> PR #17 is pending.\n", m, 1, [(REPO, 17)]),
            ("history/straight-single-quoted-span", "Previously recorded: 'PR #17 is pending.'\n", m, 0, []),
            ("history/straight-quote-then-current-claim",
             "Previously recorded: 'PR #17 isn't pending.' Now PR #18 remains open.\n",
             {(REPO, 18): "merged"}, 1, [(REPO, 18)]),
            ("markdown/indented-code-not-read", "Example:\n\n    PR #17 is pending\n", m, 0, []),
            ("markdown/indented-list-continuation-read", "- Item one\n\n    PR #17 is pending\n", m, 1,
             [(REPO, 17)]),
            ("markdown/inline-triple-backticks-not-a-fence", "```x``` is inline code.\n\nPR #17 is pending.\n",
             m, 1, [(REPO, 17)]),
            ("markdown/info-string-fence-skipped", "```python\nPR #17 is pending\n```\n", m, 0, []),
            ("markdown/table-without-outer-pipes", "ID | Status\n--- | ---\nX-1 | In progress (#17)\n", m, 1,
             [(REPO, 17)]),
            ("markdown/table-without-pipes-other-column",
             "ID | Status | Item\n--- | --- | ---\nX-1 | Planned | In progress (#17)\n", m, 0, []),
            ("negation/still-waiting-binds", "We are still waiting for #17 to merge.\n", m, 1, [(REPO, 17)]),
            ("negation/do-not-merge-until-binds", "Do not merge until #17 merges.\n", m, 1, [(REPO, 17)]),
            ("negation/present-perfect-binds", "We have been waiting for #17 to merge.\n", m, 1, [(REPO, 17)]),
            ("cue/contracted-present-perfect-binds", "It\u2019s been waiting for #17 to merge.\n", m, 1,
             [(REPO, 17)]),
            ("cue/cue-in-earlier-words-not-read", "We merged the fix and #17 remains open.\n", m, 1, [(REPO, 17)]),
            ("ref/reference-style-link", "[#17][pr17] is pending.\n\n[pr17]: https://github.com/peer/repo/pull/17\n",
             {("peer/repo", 17): "merged"}, 1, [("peer/repo", 17)]),
            ("ref/reference-style-link-no-claim",
             "[#17][pr17] merged.\n\n[pr17]: https://github.com/peer/repo/pull/17\n", m, 0, []),
            ("ref/shortcut-reference-link", "[#17] is pending.\n\n[#17]: https://github.com/peer/repo/pull/17\n",
             {("peer/repo", 17): "merged"}, 1, [("peer/repo", 17)]),
            ("ref/bracketed-reference-binds", "[#17] is pending.\n", m, 1, [(REPO, 17)]),
            ("ref/autolink-trailing-slash", "<https://github.com/acme/widgets/pull/17/> is pending.\n", m, 1,
             [(REPO, 17)]),
            ("ref/url-trailing-slash", "https://github.com/acme/widgets/pull/17/ is pending.\n", m, 1, [(REPO, 17)]),
            ("ref/http-url", "http://github.com/acme/widgets/pull/17 is pending.\n", m, 1, [(REPO, 17)]),
            ("ref/html-link", "<a href=\"https://github.com/peer/repo/pull/17\">#17</a> is pending.\n",
             {("peer/repo", 17): "merged"}, 1, [("peer/repo", 17)]),
            ("ref/html-link-no-claim", "<a href=\"https://github.com/peer/repo/pull/17\">#17</a> merged.\n", m, 0,
             []),
            ("markdown/fence-in-list-item-skipped", "- Item\n\n    ```\n    PR #17 is pending\n    ```\n", m,
             0, []),
            ("markdown/indented-code-in-list-item-skipped", "- Item\n\n      PR #17 is pending\n", m, 0, []),
            ("markdown/list-text-after-fence-read", "- Item\n\n  ```\n  code\n  ```\n\n  PR #17 is pending.\n", m,
             1, [(REPO, 17)]),
            ("status/no-separator-binds", _status_table("In progress #17"), m, 1, [(REPO, 17)]),
            ("status/emphasised-header-binds", "| ID | **Status** |\n| --- | --- |\n| X-1 | In progress (#17) |\n",
             m, 1, [(REPO, 17)]),
            ("status/byte-order-mark-binds", "\ufeff" + _status_table("In progress (#17)"), m, 1, [(REPO, 17)]),
            ("bind/ampersand-list-binds-both", "#17 & #18 are pending.\n", two, 1, [(REPO, 17), (REPO, 18)]),
            ("bind/singular-issue-then-pull-request", "Closes issue #12, and #17 is still open.\n", m, 1,
             [(REPO, 17)]),
            ("verb/present-perfect-open", "PR #17 has been open since March.\n", m, 1, [(REPO, 17)]),
            ("verb/both-still-open", "PRs #17 and #18 are both still open.\n", two, 1, [(REPO, 17), (REPO, 18)]),
            ("verb/also-pending", "PR #17 is also pending.\n", m, 1, [(REPO, 17)]),
            ("verb/past-perfect-open-no-claim", "PR #17 had been open since March.\n", m, 0, []),
            ("history/curly-span-apostrophe-inside",
             "Previously recorded: \u2018PR #18 isn\u2019t merged; PR #17 is pending\u2019\n", two, 0, []),
            ("history/straight-span-apostrophe-inside",
             "Previously recorded: 'PR #18 isn't merged; PR #17 is pending.'\n", two, 0, []),
            ("format/single-tilde-strikethrough-not-current", "~PR #17 is pending~ merged.\n", m, 0, []),
            ("format/approximate-tildes-not-struck", "Takes ~5 min; PR #17 is pending, ~10 min left.\n", m, 1,
             [(REPO, 17)]),
            ("ref/qualified-issue-not-a-pull-request", "Issue acme/widgets#17 remains open.\n", {}, 0, []),
            ("ref/mixed-issue-list-not-pull-requests", "Issues #18 and acme/widgets#17 remain open.\n", {}, 0,
             []),
            ("ref/pull-request-beside-qualified-issue",
             "Issue acme/widgets#18 is fixed; acme/widgets#17 remains open.\n", m, 1, [(REPO, 17)]),
            ("ref/markdown-link-ascii-fragment",
             "[#17](https://github.com/peer/repo/pull/17#issuecomment-1) is pending.\n",
             {("peer/repo", 17): "merged"}, 1, [("peer/repo", 17)]),
            ("ref/markdown-link-non-ascii-fragment-is-text",
             "[#17](https://github.com/peer/repo/pull/17#" + chr(0x661) + ") is pending.\n",
             {(REPO, 17): "open", ("peer/repo", 17): "merged"}, 0, [(REPO, 17)]),
            ("markdown/fenced-definition-not-collected", FENCED_DEFINITION, m, 1, [(REPO, 17)]),
            ("markdown/fenced-definition-not-collected-closed", FENCED_DEFINITION, {(REPO, 17): "closed"}, 1,
             [(REPO, 17)]),
            ("markdown/comment-definition-not-collected", COMMENT_DEFINITION, m, 1, [(REPO, 17)]),
            ("markdown/overindented-backticks-in-list-not-a-fence", OVERINDENTED_BACKTICKS, m, 1, [(REPO, 17)]),
            ("markdown/overindented-backticks-in-list-not-a-fence-closed", OVERINDENTED_BACKTICKS,
             {(REPO, 17): "closed"}, 1, [(REPO, 17)]),
            ("markdown/fence-three-past-item-column-skipped", "- Item\n\n     ```\n     PR #17 is pending\n     ```\n",
             m, 0, []),
            ("markdown/comment-spanning-blank-line-not-read", "<!--\nPR #17 is pending.\n\nPR #17 is open.\n-->\n", m,
             0, []),
            ("markdown/inline-comment-across-lines-read", "PR #17 <!-- a note\nthat wraps --> is pending.\n", m, 1,
             [(REPO, 17)]),
            ("markdown/unclosed-inline-comment-is-text", "PR #17 is pending <!-- no end\n\nNext.\n", m, 1,
             [(REPO, 17)]),
            ("markdown/fence-after-bullet-marker-read", FENCE_AFTER_BULLET, m, 1, [(REPO, 17)]),
            ("markdown/fence-after-bullet-marker-read-closed", FENCE_AFTER_BULLET, {(REPO, 17): "closed"}, 1,
             [(REPO, 17)]),
            ("markdown/fence-after-ordered-marker-read", FENCE_AFTER_ORDERED, m, 1, [(REPO, 17)]),
            ("markdown/fence-after-nested-markers-read",
             "- 1. ```\n     example\n     ```\n\n     PR #17 is pending.\n\noutside\n", m, 1, [(REPO, 17)]),
            ("markdown/fence-after-marker-in-item-skipped", "- ```\n  PR #17 is pending.\n  ```\n\noutside\n", m,
             0, []),
            ("markdown/nbsp-before-backticks-is-text", NBSP_BACKTICKS, m, 1, [(REPO, 17)]),
            ("markdown/nbsp-before-backticks-is-text-closed", NBSP_BACKTICKS, {(REPO, 17): "closed"}, 1,
             [(REPO, 17)]),
            ("markdown/em-space-before-backticks-is-text", " ```\nPR #17 is pending.\n ```\n", m, 1,
             [(REPO, 17)]),
            ("markdown/nbsp-indent-is-not-code", "Intro.\n\n    PR #17 is pending.\n", m, 1,
             [(REPO, 17)]),
            ("markdown/text-after-comment-close-read/0-spaces", "<!-- a\n-->PR #17 is pending.\n", m, 1,
             [(REPO, 17)]),
            ("markdown/text-after-comment-close-read/1-space", "<!-- a\n--> PR #17 is pending.\n", m, 1,
             [(REPO, 17)]),
            ("markdown/text-after-comment-close-read/2-spaces", "<!-- a\n-->  PR #17 is pending.\n", m, 1,
             [(REPO, 17)]),
            ("markdown/text-after-comment-close-read/3-spaces", "<!-- a\n-->   PR #17 is pending.\n", m, 1,
             [(REPO, 17)]),
            ("markdown/text-after-comment-close-read/4-spaces", COMMENT_TAIL, m, 1, [(REPO, 17)]),
            ("markdown/text-after-comment-close-read/4-spaces-closed", COMMENT_TAIL, {(REPO, 17): "closed"}, 1,
             [(REPO, 17)]),
            ("markdown/text-after-comment-close-read/tab", "<!-- a\n-->\tPR #17 is pending.\n", m, 1,
             [(REPO, 17)]),
            ("markdown/text-after-comment-close-read/in-list-item",
             "- Item\n\n  <!-- a\n  -->      PR #17 is pending.\n", m, 1, [(REPO, 17)]),
            ("markdown/text-after-comment-close-read/backticks", "<!-- a\n--> ```\nPR #17 is pending.\n", m, 1,
             [(REPO, 17)]),
            ("markdown/text-after-inline-comment-close-read", INLINE_COMMENT_TAIL, m, 1, [(REPO, 17)]),
            ("verb/still-currently-open", "#17 still currently open.\n", m, 1, [(REPO, 17)]),
            ("verb/still-currently-merged-no-claim", "#17 still currently merged.\n", m, 0, []),
            ("format/currently-pending", "PR #17 is currently pending.\n", m, 1, [(REPO, 17)]),
            ("format/now-open", "PR #17 is now open.\n", m, 1, [(REPO, 17)]),
            ("format/currently-merged-no-claim", "PR #17 is currently merged.\n", m, 0, [])):
        try:
            code, res, t = _judge(text, table)
            ok, detail = code == want and sorted(t.calls) == calls, (code, t.calls, res.hard, res.unknown)
        except InputError as exc:
            ok, detail = False, str(exc)
        check(name, ok, detail)
    _cue_rows(m)
    code, res, _t = _judge("Earlier note:\n\n| A | B |\n| --- | --- |\n| x | y |\n\n> PR #17 is pending\n", m)
    check("markdown/lead-in-not-across-table", code == 2 and "blockquote" in " ".join(res.unknown), res.unknown)
    for name, red_name, text in (
            ("bind/imperative-open-warns", "bind/imperative-open-warns/red-when-reverted",
             "Open #17 to see the discussion.\n"),
            ("bind/imperative-open-after-comma-warns", "bind/imperative-open-after-comma-warns/red-when-reverted",
             "To review it, open #17 in a browser.\n")):
        code, res, _t = _judge(text, m)
        check(name, code == 0 and not res.hard and len(res.warnings) == 1, (code, res.hard, res.warnings))
        red = _without_imperative_open(lambda: _judge(text, m)[0])
        check(red_name, red == 1, red)
    code, res, _t = _judge(_status_table("Open #17"), m)
    check("bind/status-cell-open-still-hard", code == 1 and len(res.hard) == 1, res.hard)
    code, res, t = _judge("#17 remains open and the pending PR needs review.\n", o)
    check("ambiguous/beside-named-claim", code == 2 and not res.hard and t.calls == [(REPO, 17)]
          and "ambiguous" in " ".join(res.unknown), res.unknown)
    code, res, _t = _judge("#17 remains open and pending PR #18 needs review.\n",
                           {(REPO, 17): "open", (REPO, 18): "open"})
    check("ambiguous/named-only-passes", code == 0 and not res.unknown, res.unknown)
    for name, text, steps, table in (
            ("ref/markdown-link-hash/red-when-reverted", link, {"markdown-link"}, m),
            ("ref/glued-pr/red-when-reverted", "PR#17 is pending.\n", {"glued-pr"}, m),
            ("ref/underscore-emphasis/red-when-reverted", "_#17_ is pending.\n", {"underscore-emphasis"}, m),
            ("ref/slash-list/red-when-reverted", "#17/#18 are pending.\n", {"slash-list"}, two),
            ("format/strikethrough-not-current/red-when-reverted", "~~PR #17 is pending~~ merged.\n",
             {"strikethrough"}, m),
            ("format/html-comment-not-current/red-when-reverted", "<!-- PR #17 is pending -->\n",
             {"html-comment"}, m),
            ("history/straight-single-quoted-span/red-when-reverted",
             "Previously recorded: 'PR #17 is pending.'\n", {"history-span"}, m),
            ("ref/bracketed-reference-binds/red-when-reverted", "[#17] is pending.\n", {"bracket"}, m),
            ("ref/html-link/red-when-reverted",
             "<a href=\"https://github.com/peer/repo/pull/17\">#17</a> is pending.\n", {"html-link"},
             {("peer/repo", 17): "merged", (REPO, 17): "open"}),
            ("history/curly-span-apostrophe-inside/red-when-reverted",
             "Previously recorded: \u2018PR #18 isn\u2019t merged; PR #17 is pending\u2019\n", {"apostrophe"}, two),
            ("format/single-tilde-strikethrough-not-current/red-when-reverted", "~PR #17 is pending~ merged.\n",
             {"strikethrough"}, m)):
        code, res, _t = _with_steps_removed(steps, lambda: _judge(text, table))
        # Reverted, a flag row stops flagging (0) and a no-flag row starts flagging (1).
        want = 1 if name.split("/red-when-reverted")[0] in (
            "format/strikethrough-not-current", "format/html-comment-not-current",
            "history/straight-single-quoted-span", "history/curly-span-apostrophe-inside",
            "format/single-tilde-strikethrough-not-current") else 0
        check(name, code == want and (not res.hard if want == 0 else len(res.hard) >= 1), (code, res.hard))
    code, res, t = _with_steps_removed({"issue-reference"}, lambda: _judge("Issue #17 remains open.\n", {}))
    check("ref/issue-not-a-pull-request/red-when-reverted", code == 2 and t.calls == [(REPO, 17)], res.unknown)
    # The straight-quote alternative of the history span: the naive '[^']*' form ends the span at the
    # apostrophe of isn't, so the claim after it is read (red); the shipped form keeps it inside.
    code, res, _t = _with_globals(
        {"NORMALIZE": tuple((n, _history_span_re(r"'[^']*'") if n == "history-span" else r, s)
                            for n, r, s in NORMALIZE)},
        lambda: _judge("Previously recorded: 'PR #18 isn't merged; PR #17 is pending.'\n", two))
    check("history/straight-span-apostrophe-inside/red-when-reverted", code == 1 and len(res.hard) >= 1,
          (code, res.hard))
    code, _res, t = _with_globals({"_resolve_reference_links": lambda prose, _definitions: prose},
                                  lambda: _judge("[#17][pr17] is pending.\n\n"
                                                 "[pr17]: https://github.com/peer/repo/pull/17\n",
                                                 {("peer/repo", 17): "merged"}))
    check("ref/reference-style-link/red-when-reverted", code == 0 and t.calls == [], (code, t.calls))
    _block_context_rows(m)
    _hidden_rows(m)
    _code_span_rows(m)


def _code_span_leg(replacements, text, table, surface):
    """(code, Result, transport) for text judged with the module globals replaced; InputError is
    returned as (None, message, None)."""
    try:
        return _with_globals(replacements, lambda: _judge(text, table, surface))
    except InputError as exc:
        return None, str(exc), None


def _code_span_found(judged):
    """(ok, detail): ok when the judged text gave exactly one finding for #17, one read and no warning."""
    code, res, t = judged
    if code is None:
        return False, res
    return (code == 1 and t.calls == [(REPO, 17)] and len(res.hard) == 1 and not res.warnings,
            (code, t.calls, res.hard, res.warnings))


def _code_span_rows(m):
    """Exemption delimiters written inside separate code spans are inert, so the claim between them is
    read: a finding under both terminal states. With the code-span reading removed, the matcher and the
    unevaluated-reference scan both erase it and the document passes silently (the red leg). Real
    exemptions beside or around code spans still exempt, a claim inside a code span is still read, and
    the hidden-line scan reads link delimiters inside code spans as inert too."""
    closed = {(REPO, 17): "closed"}
    for name, closed_name, red_name, text, surface in (
            ("code-span/comment-delimiters-inert", "code-span/comment-delimiters-inert-closed",
             "code-span/comment-delimiters-inert/red-when-reverted", "`<!--` PR #17 is pending `-->`\n",
             MD_SURFACE),
            ("code-span/double-tilde-inert", "code-span/double-tilde-inert-closed",
             "code-span/double-tilde-inert/red-when-reverted", "`~~` PR #17 is pending `~~`\n", MD_SURFACE),
            ("code-span/single-tilde-inert", "code-span/single-tilde-inert-closed",
             "code-span/single-tilde-inert/red-when-reverted", "`~` PR #17 is pending `~`\n", MD_SURFACE),
            ("code-span/history-lead-in-inert", "code-span/history-lead-in-inert-closed",
             "code-span/history-lead-in-inert/red-when-reverted",
             "`Previously recorded: '` PR #17 is pending `'`\n", MD_SURFACE),
            ("code-span/issue-word-inert", "code-span/issue-word-inert-closed",
             "code-span/issue-word-inert/red-when-reverted", "`issue` #17 is pending.\n", MD_SURFACE),
            ("code-span/markdown-link-inert", "code-span/markdown-link-inert-closed",
             "code-span/markdown-link-inert/red-when-reverted",
             "`[` PR #17 is pending `](https://github.com/acme/widgets/issues/1)`\n", MD_SURFACE),
            ("code-span/html-link-inert", "code-span/html-link-inert-closed",
             "code-span/html-link-inert/red-when-reverted",
             "`<a href=\"https://github.com/acme/widgets/issues/1\">` PR #17 is pending `</a>`\n", MD_SURFACE),
            ("code-span/reference-link-inert", "code-span/reference-link-inert-closed",
             "code-span/reference-link-inert/red-when-reverted",
             "`[` PR #17 is pending `][i1]`\n\n[i1]: https://github.com/acme/widgets/issues/1\n", MD_SURFACE),
            ("code-span/double-backtick-run-inert", "code-span/double-backtick-run-inert-closed",
             "code-span/double-backtick-run-inert/red-when-reverted", "``<!--`` PR #17 is pending ``-->``\n",
             MD_SURFACE),
            ("code-span/toml-comment-delimiters-inert", "code-span/toml-comment-delimiters-inert-closed",
             "code-span/toml-comment-delimiters-inert/red-when-reverted",
             "note = \"`<!--` PR #17 is pending `-->`\"\n", TOML_SURFACE)):
        ok, detail = _code_span_found(_code_span_leg({}, text, m, surface))
        check(name, ok, detail)
        ok, detail = _code_span_found(_code_span_leg({}, text, closed, surface))
        check(closed_name, ok, detail)
        code, res, t = _code_span_leg({"CODE_SPANS_INERT": False}, text, m, surface)
        check(red_name, code == 0 and t.calls == [] and not res.hard and not res.warnings,
              res if code is None else (code, t.calls, res.hard, res.warnings))
    for name, text, want, calls in (
            ("code-span/real-comment-beside-code-still-exempt", "`x` <!-- PR #17 is pending --> `y`\n", 0, []),
            ("code-span/comment-around-code-span-still-exempt", "<!-- `PR #17 is pending` -->\n", 0, []),
            ("code-span/real-strikethrough-beside-code-still-exempt", "`x` ~~PR #17 is pending~~ merged `y`.\n",
             0, []),
            ("code-span/strikethrough-around-code-span-still-exempt", "~~`PR #17 is pending`~~ merged.\n", 0, []),
            ("code-span/real-single-tilde-beside-code-still-exempt", "`x` ~PR #17 is pending~ merged.\n", 0, []),
            ("code-span/history-code-span-quote-still-exempt", "Previously recorded: `PR #17 is pending`\n", 0,
             []),
            ("code-span/issue-beside-code-still-exempt", "`x` Issue #17 remains open.\n", 0, []),
            ("code-span/claim-inside-code-span-read", "`PR #17 is pending`\n", 1, [(REPO, 17)]),
            ("code-span/unmatched-backtick-run-is-text", "`` <!-- PR #17 is pending --> `\n", 0, [])):
        try:
            code, res, t = _judge(text, m)
            ok = code == want and t.calls == calls and len(res.hard) == want and not res.warnings
            detail = (code, t.calls, res.hard, res.warnings)
        except InputError as exc:
            ok, detail = False, str(exc)
        check(name, ok, detail)
    # A fenced line is scanned whole: link delimiters inside its code spans are inert, so the reference
    # between them warns; with the reading removed, the issue link erases it and the fence is silent.
    fenced = "```\n`[` PR #17 is pending `](https://github.com/acme/widgets/issues/1)`\n```\n"
    for name, replacements in (("code-span/fenced-link-delimiters-warn", {}),
                               ("code-span/fenced-link-delimiters-warn/red-when-reverted",
                                {"CODE_SPANS_INERT": False})):
        code, res, t = _with_globals(replacements, lambda: _judge(fenced, m))
        warned = len(res.warnings) == 1 and "NOTES.md:2 (fenced block)" in res.warnings[0]
        ok = code == 0 and t.calls == [] and not res.hard and (warned if not replacements else not res.warnings)
        check(name, ok, (code, t.calls, res.hard, res.warnings))
    _code_span_binding_rows(m)
    _growth_rows(m)
    _closer_search_rows()


def _autolink_steps_before_formatting():
    """NORMALIZE with the autolink step back at its earlier place, before the bracket step (a red leg)."""
    steps = [step for step in NORMALIZE if step[0] != "autolink"]
    at = [step[0] for step in steps].index("bracket")
    return tuple(steps[:at] + [("autolink", AUTOLINK_RE, r" \1 ")] + steps[at:])


def _single_backtick_history_steps():
    """NORMALIZE with the history span's backtick alternative read as single backticks only (a red leg)."""
    return tuple((name, _history_span_re(STRAIGHT_SPAN, r"`[^`]*`") if name == "history-span" else regex,
                  replacement) for name, regex, replacement in NORMALIZE)


def _judged_as(judged, want, calls, warnings):
    """(ok, detail): ok when the judged text gave exit want, the reads calls, want findings and the
    number of warnings given."""
    code, res, t = judged
    if code is None:
        return False, res
    return (code == want and t.calls == calls and len(res.hard) == want and len(res.warnings) == warnings,
            (code, t.calls, res.hard, res.warnings))


def _code_span_binding_rows(m):
    """A reference inside or between code spans still binds: a backticked autolink, an autolink whose
    angle brackets are backticked apart from its URL, and a code span that wraps an HTML comment opener
    across the lines of a paragraph each give a finding under both terminal states, and each is shown
    silent or warning-only with its fix removed. A code span directly after a historical lead-in is that
    lead-in's quotation whatever its backtick run length (exempt), and is shown read with the
    single-backtick reading. Controls: link exemptions stay off inside code spans, a real inline comment
    across lines still hides, an unmatched run quotes nothing, and a claim after the quotation is read."""
    closed = {(REPO, 17): "closed"}
    found, wrapped_warn, silent = (1, [(REPO, 17)], 0), (0, [], 1), (0, [], 0)
    inert_autolink = {"CODE_SPAN_INERT_STEPS": CODE_SPAN_INERT_STEPS + ("autolink",)}
    single_backtick = {"NORMALIZE": _single_backtick_history_steps()}
    for name, closed_name, red_name, text, want, red, red_want in (
            ("code-span/autolink-whole-backticked-binds", "code-span/autolink-whole-backticked-binds-closed",
             "code-span/autolink-whole-backticked-binds/red-when-reverted",
             "`<https://github.com/acme/widgets/pull/17>` is pending.\n", found, inert_autolink, silent),
            ("code-span/autolink-delimiters-backticked-binds",
             "code-span/autolink-delimiters-backticked-binds-closed",
             "code-span/autolink-delimiters-backticked-binds/red-when-reverted",
             "`<`https://github.com/acme/widgets/pull/17`>` is pending.\n", found,
             {"NORMALIZE": _autolink_steps_before_formatting()}, silent),
            ("code-span/autolink-link-exemptions-still-inert", "code-span/autolink-link-exemptions-still-inert-closed",
             "code-span/autolink-link-exemptions-still-inert/red-when-reverted",
             "`<a href=\"https://github.com/acme/widgets/issues/1\">` <https://github.com/acme/widgets/pull/17>"
             " is pending `</a>`\n", found, {"CODE_SPANS_INERT": False}, silent),
            ("code-span/wrapped-comment-opener-read", "code-span/wrapped-comment-opener-read-closed",
             "code-span/wrapped-comment-opener-read/red-when-reverted", "`<!--\nPR #17 is pending\n-->`\n", found,
             {"PARAGRAPH_CODE_SPANS": False}, wrapped_warn),
            ("code-span/wrapped-comment-opener-beside-evaluated-mention-read",
             "code-span/wrapped-comment-opener-beside-evaluated-mention-read-closed",
             "code-span/wrapped-comment-opener-beside-evaluated-mention-read/red-when-reverted",
             "See #17.\n\n`<!--\nPR #17 is pending\n-->`\n", found, {"PARAGRAPH_CODE_SPANS": False}, silent),
            ("code-span/wrapped-comment-opener-span-opened-earlier-read",
             "code-span/wrapped-comment-opener-span-opened-earlier-read-closed",
             "code-span/wrapped-comment-opener-span-opened-earlier-read/red-when-reverted",
             "`a\nb <!-- PR #17 is pending\n-->`\n", found, {"PARAGRAPH_CODE_SPANS": False}, wrapped_warn),
            ("code-span/wrapped-comment-opener-in-list-item-read",
             "code-span/wrapped-comment-opener-in-list-item-read-closed",
             "code-span/wrapped-comment-opener-in-list-item-read/red-when-reverted",
             "- `<!--\n  PR #17 is pending\n  -->`\n", found, {"PARAGRAPH_CODE_SPANS": False}, wrapped_warn),
            ("history/double-backtick-span-exempt", "history/double-backtick-span-exempt-closed",
             "history/double-backtick-span-exempt/red-when-reverted",
             "Previously recorded: ``PR #17 is pending``\n", silent, single_backtick, found),
            ("history/triple-backtick-span-exempt", "history/triple-backtick-span-exempt-closed",
             "history/triple-backtick-span-exempt/red-when-reverted",
             "Previously recorded: ```PR #17 is pending```\n", silent, single_backtick, found),
            ("history/backtick-span-embedded-run-exempt", "history/backtick-span-embedded-run-exempt-closed",
             "history/backtick-span-embedded-run-exempt/red-when-reverted",
             "Previously recorded: ``PR #17 is `still` pending``\n", silent, single_backtick, found)):
        ok, detail = _judged_as(_code_span_leg({}, text, m, MD_SURFACE), *want)
        check(name, ok, detail)
        ok, detail = _judged_as(_code_span_leg({}, text, closed, MD_SURFACE), *want)
        check(closed_name, ok, detail)
        ok, detail = _judged_as(_code_span_leg(red, text, m, MD_SURFACE), *red_want)
        check(red_name, ok, detail)
    for name, closed_name, text, want in (
            ("code-span/real-inline-comment-across-lines-still-hides",
             "code-span/real-inline-comment-across-lines-still-hides-closed",
             "a `x` <!-- PR #17 is pending\n--> b\n", wrapped_warn),
            ("code-span/unmatched-backtick-before-comment-still-hides",
             "code-span/unmatched-backtick-before-comment-still-hides-closed",
             "a ` b <!-- PR #17 is pending\n--> c\n", wrapped_warn),
            ("history/unmatched-backtick-run-quotes-nothing", "history/unmatched-backtick-run-quotes-nothing-closed",
             "Previously recorded: ``PR #17 is pending\n", found),
            ("history/claim-after-double-backtick-span-read", "history/claim-after-double-backtick-span-read-closed",
             "Previously recorded: ``x`` but PR #17 is pending.\n", found)):
        ok, detail = _judged_as(_code_span_leg({}, text, m, MD_SURFACE), *want)
        check(name, ok, detail)
        ok, detail = _judged_as(_code_span_leg({}, text, closed, MD_SURFACE), *want)
        check(closed_name, ok, detail)


# Classification inputs read both ways: deterministic mixes of these pieces, plus the self-test's own
# wrapped-opener rows.
SCAN_PIECES = ("`", "``", "```", "<!--", "-->", "a ", "\n", "\n", "\n    > ", "\n<!-- x --> a", "x <!-- y",
               "--> z", "`a", "b`", "``c", " <!-- q --> ", "\n| a |", "\n- ", "\n\n", "PR #17 is pending")
# The growth rows time the whole check (_judge) on each shape at two sizes, GROWTH_FACTOR apart. Linear
# work grows about GROWTH_FACTOR times, quadratic work about its square; a row passes when the time grows
# at most GROWTH_LIMIT times (twice the linear growth), and its red leg, the quadratic form put back,
# must grow more. Each time is the fastest of GROWTH_REPEATS runs, so a pause in one run does not count.
# The rows time real runs: on a machine busy enough to slow every run of one size and none of the
# other, a row can still fail without a quadratic step (the self-test reports its times).
GROWTH_FACTOR = 8
GROWTH_LIMIT = 16
GROWTH_REPEATS = 5
GROWTH_CLAIM = "PR #17 is pending\n"


def _rescanning_list_block_opener(text, nested):
    """_list_block_opener as it was: each marker matched with the rest of its line and its content column
    read from the line's start, quadratic in a line's markers (a growth row's red leg)."""
    item = ITEM_RE.match(text)
    if item is None:
        return None
    chain, at = nested + [_content_column(text, item)], item.start(1)
    deeper = ITEM_RE.match(text, at)
    while deeper is not None and deeper.start(1) > at:
        chain, at = chain + [_content_column(text, deeper)], deeper.start(1)
        deeper = ITEM_RE.match(text, at)
    opener = _fence_opener(text, at)
    if opener is not None:
        return chain, (FENCE, opener.group(1), chain[-1], _example_fence(text, opener))
    if text.startswith("<!--", at) and _comment_start(text) == at:
        return chain, (COMMENT, None, chain[-1], False)
    return None


def _every_open_item(items, indent):
    """_open_items as it was: every open item read at every line (a growth row's red leg)."""
    return [column for column in items if column <= indent]


def _tag_retrying_html_link_matches(text):
    """_html_link_matches as it was: HTML_LINK_RE run at each tag that can start a match, so a tag with n
    href attributes and no `</a>` on its line retries each href against the rest of the line, quadratic
    in the tag (a growth row's red leg)."""
    position, no_close_before = 0, -1
    while True:
        opener = HTML_ANCHOR_OPEN_RE.search(text, position)
        if opener is None:
            return
        start = opener.start()
        tag_end = text.find(">", start)
        if tag_end < 0:
            return
        if tag_end >= no_close_before:
            found = HTML_LINK_RE.match(text, start)
            if found is not None:
                yield found
                position = found.end()
                continue
            if HTML_ANCHOR_TAG_RE.match(text, start) is not None:
                line_end = text.find("\n", tag_end + 1)
                no_close_before = len(text) if line_end < 0 else line_end
        position = tag_end + 1


def _growth(text_of, n, state, refused=False):
    """(growth, [seconds at n, seconds at GROWTH_FACTOR * n]) for the whole check on text_of's shape. When
    refused, a run the check ends with InputError (cannot evaluate) is timed to that end."""
    import time
    table = {(REPO, 17): state}
    texts = [text_of(n), text_of(GROWTH_FACTOR * n)]
    seconds = [None, None]
    # The two sizes are timed in turn, so a pause that lasts a while slows runs of both.
    for _ in range(GROWTH_REPEATS):
        for index, text in enumerate(texts):
            start = time.perf_counter()
            try:
                _judge(text, table)
            except InputError:
                if not refused:
                    raise
            took = time.perf_counter() - start
            seconds[index] = took if seconds[index] is None else min(seconds[index], took)
    return seconds[1] / max(seconds[0], 1e-9), [round(value, 4) for value in seconds]


def _classified(text):
    try:
        return block_context(text.split("\n"))
    except InputError as exc:
        return str(exc)


def _growth_rows(m):
    """The whole check runs in time linear in its input on each shape below: its time grows at most
    GROWTH_LIMIT times when the input grows GROWTH_FACTOR times; with that shape's quadratic form put
    back it grows more (the red leg). The classification equals the rescanning reading's on a fixed set
    of inputs, and the input of the quadratic report still gives its one finding."""
    import random
    # (row id, its red leg's id, text of size n, the pull request's state, n, the quadratic form the red
    # leg puts back). The first four are the shapes of the review that found them, the next four the
    # paragraph shapes the comment reader was made linear for, the rest the other steps found growing
    # faster than their input. A shape whose claims bind to an open pull request prints no line per claim
    # (each line quotes its clause, so that output alone grows as claims times clause length).
    for name, red_name, text_of, state, n, quadratic in (
            ("growth/unclosed-comments-linear", "growth/unclosed-comments-linear/red-when-reverted",
             lambda n: "a <!-- b\n" * n + GROWTH_CLAIM, "merged", 400, {"LINEAR_FINDERS": False}),
            ("growth/unclosed-link-brackets-linear", "growth/unclosed-link-brackets-linear/red-when-reverted",
             lambda n: "[a " * n + "\n" + GROWTH_CLAIM, "merged", 300, {"LINEAR_FINDERS": False}),
            ("growth/list-markers-linear", "growth/list-markers-linear/red-when-reverted",
             lambda n: "+ " * n + "x\n" + GROWTH_CLAIM, "merged", 1500,
             {"_list_block_opener": _rescanning_list_block_opener}),
            ("growth/claims-in-one-clause-linear", "growth/claims-in-one-clause-linear/red-when-reverted",
             lambda n: "PR #17 is pending " * n + "\n", "open", 60, {"_clause_words": _ClauseWords}),
            ("growth/closed-comments-linear", "growth/closed-comments-linear/red-when-reverted",
             lambda n: "text <!-- ordinary comment -->\n" * n + GROWTH_CLAIM, "merged", 60,
             {"_paragraph_comment_start": _rescanning_comment_start}),
            ("growth/unclosed-openers-linear", "growth/unclosed-openers-linear/red-when-reverted",
             lambda n: "text <!-- unclosed\n" * n + GROWTH_CLAIM, "merged", 80,
             {"_paragraph_comment_start": _rescanning_comment_start}),
            ("growth/code-spans-beside-linear", "growth/code-spans-beside-linear/red-when-reverted",
             lambda n: "`a` text `` <!-- c --> ``b\n" * n + GROWTH_CLAIM, "merged", 40,
             {"_paragraph_comment_start": _rescanning_comment_start}),
            ("growth/cut-lines-linear", "growth/cut-lines-linear/red-when-reverted",
             lambda n: "a ` b <!-- x\n--> c ``\n" * n + GROWTH_CLAIM, "merged", 40,
             {"_paragraph_comment_start": _rescanning_comment_start}),
            ("growth/unclosed-anchors-linear", "growth/unclosed-anchors-linear/red-when-reverted",
             lambda n: "<a href=\"x\">y " * n + "\n" + GROWTH_CLAIM, "merged", 150, {"LINEAR_FINDERS": False}),
            ("growth/unclosed-history-quotes-linear", "growth/unclosed-history-quotes-linear/red-when-reverted",
             lambda n: "previously read \u201c" * n + "\n" + GROWTH_CLAIM, "merged", 200,
             {"LINEAR_FINDERS": False}),
            ("growth/joined-references-linear", "growth/joined-references-linear/red-when-reverted",
             lambda n: "PR #17" + ", #17" * n + "\n", "merged", 50, {"LIST_RUN_STARTS": False}),
            ("growth/lazy-lines-under-items-linear", "growth/lazy-lines-under-items-linear/red-when-reverted",
             lambda n: "- " * n + "```\n" + " " * (2 * n) + "```\n" + " " * (2 * n) + "t\n" + "t\n" * n
             + GROWTH_CLAIM, "merged", 600, {"_open_items": _every_open_item}),
            ("growth/anchor-attributes-linear", "growth/anchor-attributes-linear/red-when-reverted",
             lambda n: "<a " + "href=\"x\" " * n + "> " + GROWTH_CLAIM, "merged", 1000,
             {"STEP_FINDERS": {**STEP_FINDERS, HTML_LINK_RE: _tag_retrying_html_link_matches}}),
            ("growth/reference-uses-linear", "growth/reference-uses-linear/red-when-reverted",
             lambda n: "[x]: https://example.com/" + "a" * n + "\n\n" + "[x] " * n + "\n" + GROWTH_CLAIM,
             "merged", 300, {"REFERENCE_PLACEHOLDERS": False, "URL_COPY_BOUNDED": False}),
            # Complete anchors on one line, each followed by a search for the line's end (in a fenced
            # block, scanned for hidden references, so the line's other steps stay small beside it).
            ("growth/complete-anchors-linear", "growth/complete-anchors-linear/red-when-reverted",
             lambda n: "```\n" + "<a href=\"x\">y</a> " * n + "\n```\n" + GROWTH_CLAIM, "merged", 8000,
             {"KEPT_LINE_ENDS": False}),
            # One long definition used on n lines, and in n paragraphs: its URL is read once per document.
            ("growth/reference-uses-on-lines-linear", "growth/reference-uses-on-lines-linear/red-when-reverted",
             lambda n: "[x]: https://example.com/" + "a" * (8 * n) + "\n\n" + "[x]\n" * n + GROWTH_CLAIM,
             "merged", 400, {"SHARED_DEFINITIONS": False}),
            ("growth/reference-uses-in-paragraphs-linear",
             "growth/reference-uses-in-paragraphs-linear/red-when-reverted",
             lambda n: "[x]: https://example.com/" + "a" * (8 * n) + "\n\n" + "[x]\n\n" * n + GROWTH_CLAIM,
             "merged", 300, {"SHARED_DEFINITIONS": False})):
        growth, seconds = _growth(text_of, n, state)
        check(name, growth <= GROWTH_LIMIT, (growth, seconds))
        growth, seconds = _with_globals(quadratic, lambda: _growth(text_of, n, state))
        check(red_name, growth > GROWTH_LIMIT, (growth, seconds))
    # The two URL copy paths no placeholder covers: a definition URL PLACEHOLDER_URL_RE refuses (copied
    # into each use), and a placeholder the markdown-link step leaves ([a]( [x)] reads `[a]( [x)` as the
    # link, so the URL is given back). Their text is read and quoted as copied, so n uses of a URL of
    # length L are n * L characters of work; the copy bound (URL_COPY_FACTOR) makes that cannot-evaluate
    # once it passes a multiple of the document's size. Each row's small input is evaluated (its one
    # finding) and its large one refused naming the bound, and the time grows at most GROWTH_LIMIT times;
    # with the bound removed, the large input is evaluated in time growing more (the red leg).
    for name, red_name, text_of, n in (
            ("growth/copied-reference-urls-bounded", "growth/copied-reference-urls-bounded/red-when-reverted",
             lambda n: "[x]: https://example.com/" + "a" * n + "(b)\n\n" + "[x] " * n + "\n" + GROWTH_CLAIM, 100),
            ("growth/given-back-reference-urls-bounded",
             "growth/given-back-reference-urls-bounded/red-when-reverted",
             lambda n: "[x)]: https://example.com/" + "a" * n + "\n\n" + "[a]( [x)] " * n + "\n" + GROWTH_CLAIM,
             100)):
        small = _judged_as(_code_span_leg({}, text_of(n), m, MD_SURFACE), 1, [(REPO, 17)], 0)
        try:
            _judge(text_of(GROWTH_FACTOR * n), m)
            large = "evaluated"
        except InputError as exc:
            large = str(exc)
        growth, seconds = _growth(text_of, n, "merged", refused=True)
        check(name, small[0] and "a bound on running time" in large and growth <= GROWTH_LIMIT,
              (small[1], large, growth, seconds))
        growth, seconds = _with_globals({"URL_COPY_BOUNDED": False}, lambda: _growth(text_of, n, "merged"))
        check(red_name, growth > GROWTH_LIMIT, (growth, seconds))
    pick = random.Random(17)
    texts = ["`<!--\nPR #17 is pending\n-->`\n", "`a\nb <!-- PR #17 is pending\n-->`\n",
             "- `<!--\n  PR #17 is pending\n  -->`\n", "a ` b <!-- PR #17 is pending\n--> c\n"]
    texts += ["".join(pick.choice(SCAN_PIECES) for _ in range(pick.randint(1, 60))) for _ in range(400)]
    differ = [text for text in texts if _classified(text) != _with_globals(
        {"_paragraph_comment_start": _rescanning_comment_start}, lambda: _classified(text))]
    check("scale/classification-matches-rescanning-reading", not differ, differ[:3])
    # A reference link reads the same with its URL written as a placeholder as with the URL copied in,
    # a link the markdown-link step does not read whole ([a]( [x)]) and a URL it cannot stand for
    # included; and the HTML link finder gives exactly the regex's matches.
    pieces = ("[x]", "[t][x]", "[x][]", "![x]", "[x)]", "[a](", " [y]", ")", "`", "<!--", "-->", " ", "\n",
              "PR #17 is pending", " is pending", "#17", " still open", "<a href=\"q\">", "</a>", "[")
    urls = ("https://github.com/acme/widgets/pull/17", "https://github.com/acme/widgets/pull/17#top",
            "https://github.com/acme/widgets/issues/17", "https://x.org/a(b)", "x/#17", "u`v", "a]b")
    texts = ["[x]: https://github.com/acme/widgets/pull/17\n\n[x] is pending\n",
             "[x)]: https://github.com/acme/widgets/pull/17\n\n[a]( [x)] is pending\n",
             "[x]: https://x.org/a(b)\n\n[x] PR #17 is pending\n", "[x]: x/#17\n\n`[x]` [x] is pending <!-- [x] -->\n"]
    texts += ["[x]: {}\n[x)]: {}\n[y]: {}\n\n".format(*(pick.choice(urls) for _ in range(3)))
              + "".join(pick.choice(pieces) for _ in range(pick.randint(1, 30))) for _ in range(200)]

    def judged(text):
        try:
            code, res, t = _judge(text, {(REPO, 17): "merged"})
        except InputError as exc:
            return str(exc)
        return code, re.sub(r"\(read [^)]*\)", "", repr((res.hard, res.warnings, res.unknown))), t.calls
    differ = [text for text in texts
              if judged(text) != _with_globals({"REFERENCE_PLACEHOLDERS": False}, lambda: judged(text))]
    check("scale/reference-placeholders-read-as-urls", not differ, differ[:3])
    alphabet = ("<a ", "<A\t", "href=\"x\" ", "href='y'", "href=z ", ">", "</a>", "</a  >", "</A\n>", "\n", "x",
                " ", "\"", "'", "<", "</a")
    texts = ["".join(pick.choice(alphabet) for _ in range(pick.randint(1, 30))) for _ in range(2000)]
    differ = [text for text in texts if [(found.span(), found.groups()) for found in _html_link_matches(text)]
              != [(found.span(), found.groups()) for found in HTML_LINK_RE.finditer(text)]]
    check("scale/html-link-finder-matches-regex", not differ, differ[:3])
    code, res, t = _judge("text <!-- ordinary comment -->\n" * 4000 + "PR #17 is pending\n", m)
    check("scale/report-input-still-finds", code == 1 and t.calls == [(REPO, 17)] and len(res.hard) == 1
          and not res.warnings, (code, t.calls, res.hard, res.warnings))


def _closer_search_rows():
    """An inline comment opened on a paragraph line runs only to a `-->` before the next line that starts
    a heading, list item, blockquote or fence: that line is read as its own block (the classification
    reading and its comparison both use the one closer search, so neither shows this). Each red leg puts
    back a closer search that steps over that one kind of line, which hides it in the comment."""
    starts = {"heading": HEADING_RE, "list-item": ITEM_RE, "blockquote": QUOTE_RE, "fence": FENCE_OPEN_RE}
    for name, red_name, start, text, want in (
            ("comment/closer-search-stops-at-heading", "comment/closer-search-stops-at-heading/red-when-reverted",
             "heading", "x <!-- y\n# PR #17 is pending\n-->\n", HEADING),
            ("comment/closer-search-stops-at-list-item",
             "comment/closer-search-stops-at-list-item/red-when-reverted",
             "list-item", "x <!-- y\n- PR #17 is pending\n-->\n", ITEM),
            ("comment/closer-search-stops-at-blockquote",
             "comment/closer-search-stops-at-blockquote/red-when-reverted",
             "blockquote", "x <!-- y\n> PR #17 is pending\n-->\n", QUOTE_LINE),
            ("comment/closer-search-stops-at-fence", "comment/closer-search-stops-at-fence/red-when-reverted",
             "fence", "x <!-- y\n```\nPR #17 is pending\n```\n-->\n", FENCE)):
        kinds = _classified(text)
        check(name, kinds[0] == (PARA, "x <!-- y") and kinds[1][0] == want, kinds)
        kept = [regex for kind, regex in starts.items() if kind != start]

        def stepping_over(line, kept=kept):
            return _blank(line) or any(regex.match(line.lstrip(MD_SPACE)) for regex in kept)

        kinds = _with_globals({"_ends_comment_search": stepping_over}, lambda: _classified(text))
        check(red_name, kinds[1][0] != want, kinds)


def _no_unevaluated_references(_label, _lines, _blocks, _units, _exempt=(), _definitions=None, _budget=None):
    """unevaluated_references as it was before the scan existed: nothing is reported (a red leg)."""
    return []


def _hidden_rows(m):
    """A pull request reference only in text the classifier hides warns, naming the file, line, block
    kind and reference, with no read and exit 0, whatever the pull request's state; an example fence and
    a reference also read elsewhere stay quiet. Each classifier fix, reverted, loses its finding and is
    left with that warning; with the scan removed as well it passes silently."""
    for name, text, table, line, kind in (
            ("hidden/fenced-reference-warns", "```\nPR #17 is pending\n```\n", m, 2, "fenced block"),
            ("hidden/fenced-reference-warns/unreadable-state-still-warns", "```\nPR #17 is pending\n```\n", {}, 2,
             "fenced block"),
            ("hidden/info-string-fence-reference-warns", "```python\nPR #17 is pending\n```\n", m, 2,
             "fenced block"),
            ("hidden/indented-code-reference-warns", "Example:\n\n    PR #17 is pending\n", m, 3,
             "indented code block"),
            ("hidden/comment-block-reference-warns", "<!--\nPR #17 is pending.\n-->\n", m, 2,
             "HTML comment block"),
            ("hidden/inline-comment-reference-warns", "Intro <!-- a\nPR #17 is pending -->\n", m, 2,
             "inline HTML comment"),
            ("hidden/comment-cut-line-reference-warns", "Intro <!-- PR #17 is pending\nstill -->\n", m, 1,
             "HTML comment"),
            ("hidden/definition-reference-warns", "Notes.\n\n[old]: https://github.com/acme/widgets/pull/17\n", m,
             3, "link reference definition"),
            ("hidden/fence-in-list-item-reference-warns", "- ```\n  PR #17 is pending.\n  ```\n", m, 2,
             "fenced block")):
        try:
            code, res, t = _judge(text, table)
            warned = (len(res.warnings) == 1 and "NOTES.md:{} ({})".format(line, kind) in res.warnings[0]
                      and ("reference PR #17 " in res.warnings[0]
                           or "reference https://github.com/acme/widgets/pull/17 " in res.warnings[0])
                      and "appears only inside" in res.warnings[0])
            ok, detail = code == 0 and t.calls == [] and not res.hard and warned, (code, t.calls, res.warnings)
        except InputError as exc:
            ok, detail = False, str(exc)
        check(name, ok, detail)
    code, res, t = _with_globals({"unevaluated_references": _no_unevaluated_references},
                                 lambda: _judge("```\nPR #17 is pending\n```\n", m))
    check("hidden/fenced-reference-warns/red-when-reverted", code == 0 and not res.warnings and t.calls == [],
          (code, res.warnings))
    for name, text in (("hidden/example-fence-quiet", "```example\nPR #17 is pending\n```\n"),
                       ("hidden/history-fence-quiet", "~~~ History\nPR #17 is pending\n~~~\n"),
                       ("hidden/reference-also-read-quiet", "PR #17 merged.\n\n```\nPR #17 is pending\n```\n"),
                       ("hidden/used-definition-quiet",
                        "[#17][pr17] merged.\n\n[pr17]: https://github.com/acme/widgets/pull/17\n")):
        code, res, t = _judge(text, m)
        check(name, code == 0 and not res.warnings and not res.hard and t.calls == [], (code, res.warnings))
    code, res, _t = _with_globals({"EXAMPLE_FENCE_INFO": ()},
                                  lambda: _judge("```example\nPR #17 is pending\n```\n", m))
    check("hidden/example-fence-quiet/red-when-reverted", code == 0 and len(res.warnings) == 1, res.warnings)
    # Each classifier fix reverted: the claim's finding is lost (no read) but the hidden-reference warning
    # remains; with the scan also removed, the claim passes silently, as it did before both.
    for name, replacements, text in (
            ("markdown/fence-after-bullet-marker-read/red-when-reverted",
             {"_list_block_opener": lambda _text, _nested: None}, FENCE_AFTER_BULLET),
            ("markdown/fence-after-ordered-marker-read/red-when-reverted",
             {"_list_block_opener": lambda _text, _nested: None}, FENCE_AFTER_ORDERED),
            ("markdown/nbsp-before-backticks-is-text/red-when-reverted", {"MD_SPACE": None}, NBSP_BACKTICKS),
            ("markdown/text-after-comment-close-read/4-spaces/red-when-reverted",
             {"COMMENT_TAIL_IS_PARAGRAPH": False}, COMMENT_TAIL)):
        try:
            code, res, t = _with_globals(replacements, lambda: _judge(text, m))
            ok = code == 0 and t.calls == [] and not res.hard and len(res.warnings) == 1 \
                and "appears only inside" in res.warnings[0]
            detail = (code, t.calls, res.hard, res.warnings)
            code, res, t = _with_globals(dict(replacements, unevaluated_references=_no_unevaluated_references),
                                         lambda: _judge(text, m))
            ok = ok and code == 0 and t.calls == [] and not res.hard and not res.warnings
        except InputError as exc:
            ok, detail = False, str(exc)
        check(name, ok, detail)
    _unevaluated_rows(m)


def _one_warning(res, location, needle):
    """True when res holds exactly one warning, at location, naming PR #17 and holding needle."""
    return (len(res.warnings) == 1 and location in res.warnings[0] and "reference PR #17 " in res.warnings[0]
            and "was not evaluated" in res.warnings[0] and needle in res.warnings[0])


def _unevaluated_leg(replacements, text, table, surface, location):
    """(ok, silent, detail): ok when, with the replacements, text gives exactly the one not-evaluated
    warning at location with no read and no finding; silent when, with the scan of read lines also
    removed, it gives no warning, no read and no finding (it passes silently)."""
    try:
        code, res, t = _with_globals(replacements, lambda: _judge(text, table, surface))
        ok = code == 0 and t.calls == [] and not res.hard and _one_warning(
            res, location, "no record unit the check read carries it")
        detail = (code, t.calls, res.hard, res.warnings)
        code, res, t = _with_globals(dict(replacements, SCAN_READ_LINES=False),
                                     lambda: _judge(text, table, surface))
        return ok, code == 0 and t.calls == [] and not res.hard and not res.warnings, detail
    except InputError as exc:
        return False, False, str(exc)


def _unevaluated_rows(m):
    """Every pull request reference of a document that reached no evaluated unit warns, whatever dropped
    it: a second comment opened after a closing `-->` hides its lines (a warning naming the inline
    comment), a table header cell is read as prose (a finding), each under both terminal states. With
    either fix reverted, the set difference still warns (not evaluated); with the set difference also
    reverted to the hidden-line scan, the claim passes silently. The documented exemptions stay quiet."""
    closed = {(REPO, 17): "closed"}
    for name, text, table in (
            ("markdown/comment-reopened-after-block-close-warns", REOPENED_BLOCK_COMMENT, m),
            ("markdown/comment-reopened-after-block-close-warns-closed", REOPENED_BLOCK_COMMENT, closed),
            ("markdown/comment-reopened-after-inline-close-warns", REOPENED_INLINE_COMMENT, m),
            ("markdown/comment-reopened-after-inline-close-warns-closed", REOPENED_INLINE_COMMENT, closed)):
        try:
            code, res, t = _judge(text, table)
            ok = code == 0 and t.calls == [] and not res.hard and _one_warning(
                res, "NOTES.md:3 (inline HTML comment)", "appears only inside")
            detail = (code, t.calls, res.hard, res.warnings)
        except InputError as exc:
            ok, detail = False, str(exc)
        check(name, ok, detail)
    for name, text, table, calls in (
            ("markdown/text-before-reopened-comment-read", "<!-- a\n-->PR #17 is pending <!-- b\nc -->\n", m,
             [(REPO, 17)]),
            ("markdown/table-header-claim-read", HEADER_CLAIM, m, [(REPO, 17)]),
            ("markdown/table-header-claim-read-closed", HEADER_CLAIM, closed, [(REPO, 17)]),
            ("markdown/table-header-without-pipes-read", "PR #17 is pending | Status\n--- | ---\nx | y\n", m,
             [(REPO, 17)]),
            ("markdown/table-header-read-keeps-status-column",
             "| PR #18 is pending | Status |\n| --- | --- |\n| X-1 | In progress (#17) |\n",
             {(REPO, 17): "merged", (REPO, 18): "closed"}, [(REPO, 17), (REPO, 18)])):
        try:
            code, res, t = _judge(text, table)
            ok = code == 1 and sorted(t.calls) == calls and len(res.hard) == len(calls) and not res.warnings
            detail = (code, t.calls, res.hard, res.warnings)
        except InputError as exc:
            ok, detail = False, str(exc)
        check(name, ok, detail)
    # Each fix reverted: the set difference still warns (not evaluated), with no read; with the scan of
    # read lines also removed (the hidden-line scan alone), the claim passes silently.
    for name, closed_name, red_name, fix_red_name, replacements, text, location in (
            ("unevaluated/reopened-block-comment-warns", "unevaluated/reopened-block-comment-warns-closed",
             "unevaluated/reopened-block-comment-warns/red-when-reverted",
             "markdown/comment-reopened-after-block-close-warns/red-when-reverted",
             {"COMMENT_TAIL_SCANNED": False}, REOPENED_BLOCK_COMMENT, "NOTES.md:3 (not evaluated)"),
            ("unevaluated/reopened-inline-comment-warns", "unevaluated/reopened-inline-comment-warns-closed",
             "unevaluated/reopened-inline-comment-warns/red-when-reverted",
             "markdown/comment-reopened-after-inline-close-warns/red-when-reverted",
             {"COMMENT_TAIL_SCANNED": False}, REOPENED_INLINE_COMMENT, "NOTES.md:3 (not evaluated)"),
            ("unevaluated/table-header-warns", "unevaluated/table-header-warns-closed",
             "unevaluated/table-header-warns/red-when-reverted", "markdown/table-header-claim-read/red-when-reverted",
             {"HEADER_CELLS_READ": False}, HEADER_CLAIM, "NOTES.md:1 (not evaluated)")):
        ok, silent, detail = _unevaluated_leg(replacements, text, m, MD_SURFACE, location)
        check(name, ok, detail)
        check(red_name, ok and silent, detail)
        # The fix reverted: its own result (a hidden-comment warning, or a finding) is gone, and only the
        # not-evaluated warning remains.
        check(fix_red_name, ok, detail)
        ok, silent, detail = _unevaluated_leg(replacements, text, {(REPO, 17): "closed"}, MD_SURFACE, location)
        check(closed_name, ok and silent, detail)
    toml_location = "changes.toml: note (string line 2, not evaluated)"
    ok, silent, detail = _unevaluated_leg({}, TOML_MULTILINE_COMMENT, m, TOML_SURFACE, toml_location)
    check("unevaluated/toml-multiline-comment-warns", ok, detail)
    check("unevaluated/toml-multiline-comment-warns/red-when-reverted", ok and silent, detail)
    ok, silent, detail = _unevaluated_leg({}, TOML_MULTILINE_COMMENT, {(REPO, 17): "closed"}, TOML_SURFACE,
                                          toml_location)
    check("unevaluated/toml-multiline-comment-warns-closed", ok and silent, detail)
    for name, text in (("unevaluated/historical-blockquote-quiet", "Earlier note:\n\n> PR #17 is pending.\n"),
                       ("unevaluated/struck-reference-quiet", "~~PR #17 is pending~~ merged.\n"),
                       ("unevaluated/single-line-comment-quiet", "<!-- PR #17 is pending -->\n"),
                       ("unevaluated/historical-span-quiet", "Previously recorded: 'PR #17 is pending.'\n"),
                       ("unevaluated/issue-reference-quiet", "Issue #17 remains open.\n")):
        code, res, t = _judge(text, m)
        check(name, code == 0 and not res.warnings and not res.hard and t.calls == [], (code, res.warnings))


def _cue_rows(m):
    """A claim after a negation, past-tense, conditional or question cue: a warning naming the clause
    and the pull request when it has merged (never a silent pass), a clean pass when it is open, and
    cannot-evaluate when its state is unreadable. With the cue reading removed, each is a finding."""
    three = {(REPO, n): "merged" for n in (16, 17, 18)}
    opened = {key: "open" for key in three}
    open_bad, unknown_bad = [], []
    for name, text, count in (
        ("negation/not-waiting", "We are not waiting for #17 to merge.\n", 1),
        ("negation/past-waiting", "We were waiting for #17 to merge.\n", 1),
        ("negation/contracted-not-waiting", "It isn't waiting on #17.\n", 1),
        ("negation/past-perfect-waiting", "We had been waiting for #17 to merge.\n", 1),
        ("negation/not-been-waiting", "We haven't been waiting for #17 to merge.\n", 1),
        ("negation/not-only-binds", "We are not only waiting for #17 to merge.\n", 1),
        ("negation/past-only-waiting", "We were only waiting for #17 to merge.\n", 1),
        ("negation/not-until-binds", "Not until #17 merges can we ship.\n", 1),
        ("negation/not-waiting-to-merge", "Not waiting for #17 to merge.\n", 1),
        ("negation/present-perfect-not-only-warns", "We have not only been waiting for #17 to merge.\n", 1),
        ("negation/curly-past-perfect-warns", "We\u2019d been waiting for #17 to merge.\n", 1),
        ("negation/straight-past-perfect-warns", "We'd been waiting for #17 to merge.\n", 1),
        ("negation/past-not-only-warns", "We were not only waiting for #17 to merge.\n", 1),
        ("negation/past-perfect-not-only-warns", "We had been not only waiting for #17 to merge.\n", 1),
        ("negation/cannot-wait-warns", "We cannot wait for #17 to merge.\n", 1),
        ("negation/contracted-cannot-wait-warns", "We can't wait for #17 to merge.\n", 1),
        ("cue/previously-pending-warns", "The previously pending PR #17 has merged.\n", 1),
        ("cue/formerly-open-warns", "The formerly open PR #17 was closed without merging.\n", 1),
        ("cue/once-pending-warns", "The once-pending PR #17 merged on Monday.\n", 1),
        ("cue/neither-nor-warns", "Neither #16 nor #17 is still open.\n", 1),
        ("cue/none-of-warns", "None of #16, #17 and #18 remain open.\n", 3),
        ("cue/question-warns", "Was #17 still open at the freeze?\n", 1),
        ("cue/conditional-warns", "If #17 is still open, rebase it.\n", 1),
        ("cue/whether-warns", "Check whether #17 is still open.\n", 1)):
        code, res, _t = _judge(text, three)
        named = all("acme/widgets#1" in line and "after the cue" in line for line in res.warnings)
        check(name, code == 0 and not res.hard and len(res.warnings) == count and named,
              (code, res.hard, res.warnings))
        code, res, _t = _judge(text, opened)
        if code != 0 or res.hard or res.warnings:
            open_bad.append((name, code, res.hard, res.warnings))
        code, res, _t = _judge(text, {})
        if code != 2 or res.hard:
            unknown_bad.append((name, code, res.hard))
    check("cue/every-cue-row-open-passes", not open_bad, open_bad)
    check("cue/every-cue-row-unknown-cannot-evaluate", not unknown_bad, unknown_bad)
    code, res, _t = _judge("We are not waiting for #17 to merge.\n", three)
    check("cue/warning-names-sentence-and-pull-request", len(res.warnings) == 1
          and "We are not waiting for #17 to merge" in res.warnings[0]
          and "acme/widgets#17" in res.warnings[0] and "MERGED" in res.warnings[0], res.warnings)
    for name, text in (("cue/no-pending-prs-warns", "There are no pending PRs.\n"),
                       ("cue/merged-all-pending-prs-warns", "Merged all pending pull requests for 1.2.\n")):
        code, res, t = _judge(text, three)
        check(name, code == 0 and not res.unknown and len(res.warnings) == 1 and t.calls == []
              and "not named" in res.warnings[0], (code, res.unknown, res.warnings))
    for name, text, want in (
            ("negation/not-waiting/red-when-reverted", "We are not waiting for #17 to merge.\n", 1),
            ("negation/past-waiting/red-when-reverted", "We were waiting for #17 to merge.\n", 1),
            ("cue/neither-nor-warns/red-when-reverted", "Neither #16 nor #17 is still open.\n", 1),
            ("negation/present-perfect-not-only-warns/red-when-reverted",
             "We have not only been waiting for #17 to merge.\n", 1),
            ("cue/no-pending-prs-warns/red-when-reverted", "There are no pending PRs.\n", 2)):
        red = _without_negation(lambda: _judge(text, three)[0])
        check(name, red == want, red)
    code, res, _t = _with_steps_removed({"apostrophe"}, lambda: _judge("We\u2019d been waiting for #17 to merge.\n",
                                                                         three))
    check("negation/curly-past-perfect-warns/red-when-reverted", code == 1 and len(res.hard) == 1,
          (code, res.hard))


def _self_test_nesting(m, tempfile, contextlib, io):
    """Nesting: no walk over a nested document recurses, so deep nesting within the size bound ends in
    exit 0, 1 or 2 with no traceback. A TOML document nested past TOML_MAX_DEPTH, deep table headers,
    array-of-tables headers and dotted keys (which the TOML reader parses without recursing; the walk
    over them used to recurse and ended in an uncaught RecursionError) or inline arrays and tables
    (which the reader parses recursively), is cannot-evaluate, naming the bound; within the bound it is
    read as before. Markdown blockquotes and lists are read line by line, so deep nesting gives the
    result of one level."""
    import itertools
    import random

    def named(text):
        return "TOML_MAX_DEPTH" in text and "a bound on nesting" in text

    def drafted(target, text):
        """(exit code, output) of the draft entry point in process, or (None, the exception's name)."""
        sink = io.StringIO()
        try:
            with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
                code = judge_draft_bytes(ROOT, target, text.encode("utf-8"), FakeTransport(m), _origin_ok)
        except Exception as exc:
            return None, type(exc).__name__
        return code, sink.getvalue()

    # The reported document: 10,007 bytes, a table header 5,000 tables deep.
    deep_headers = "[" + ".".join(["a"] * 5000) + ']\nx=""\n'
    # Deep enough to pass the bound, shallow enough for the reader to parse (it was read before).
    parsed_arrays = "x = " + "[" * 150 + '""' + "]" * 150 + "\n"
    parsed_tables = "x = " + "{a=" * 150 + '""' + "}" * 150 + "\n"
    # Past what the reader parses: it ends at Python's recursion limit.
    unparsed_tables = "x = " + "{a=" * 5000 + '""' + "}" * 5000 + "\n"
    check("nesting/reported-toml-document-size", len(deep_headers.encode("utf-8")) == 10007, len(deep_headers))
    for name, text in (
            ("nesting/toml-deep-table-headers-cannot-evaluate", deep_headers),
            ("nesting/toml-deep-array-of-tables-headers-cannot-evaluate",
             "[[" + ".".join(["a"] * 5000) + ']]\nx=""\n'),
            ("nesting/toml-deep-dotted-keys-cannot-evaluate", ".".join(["a"] * 5000) + ' = ""\n'),
            ("nesting/toml-deep-inline-arrays-parsed-cannot-evaluate", parsed_arrays),
            ("nesting/toml-deep-inline-tables-parsed-cannot-evaluate", parsed_tables),
            ("nesting/toml-deep-inline-arrays-past-the-reader-cannot-evaluate",
             "x = " + "[" * 5000 + '""' + "]" * 5000 + "\n"),
            ("nesting/toml-deep-inline-tables-past-the-reader-cannot-evaluate", unparsed_tables)):
        code, out = drafted("changelog.toml", text)
        check(name, code == 2 and named(out), (code, out[-300:]))

    # Within the bound nothing changes: a value TOML_MAX_DEPTH tables and arrays deep is read, with its
    # whole key path as its location; one level deeper is refused, naming the bound.
    def header_at(depth):
        return "[" + ".".join(["a"] * depth) + ']\nx = "PR #17 remains pending."\n', ".".join(["a"] * depth) + ".x"

    def inline_at(depth):
        pairs, extra = divmod(depth, 2)
        return ("x = " + "[" * extra + "[{a=" * pairs + '"PR #17 remains pending."' + "}]" * pairs + "]" * extra
                + "\n", "x" + "[0]" * extra + "[0].a" * pairs)
    for name, (text, path) in (("nesting/toml-header-at-the-bound-read", header_at(TOML_MAX_DEPTH)),
                               ("nesting/toml-inline-at-the-bound-read", inline_at(TOML_MAX_DEPTH))):
        try:
            code, res, _t = _judge(text, m, TOML_SURFACE)
            got = (code, len(res.hard), len(res.hard) == 1 and "changes.toml: {}:".format(path) in res.hard[0])
        except Exception as exc:
            got = (type(exc).__name__, str(exc)[:300])
        check(name, got == (1, 1, True), got)
    for name, (text, _path) in (("nesting/toml-header-past-the-bound-refused", header_at(TOML_MAX_DEPTH + 1)),
                                ("nesting/toml-inline-past-the-bound-refused", inline_at(TOML_MAX_DEPTH + 1))):
        try:
            got = ("collected", len(collect([(TOML_SURFACE, text.encode("utf-8"))])))
        except InputError as exc:
            got = named(str(exc))
        except Exception as exc:
            got = (type(exc).__name__, str(exc)[:300])
        check(name, got is True, got)
    # The iterative walk gives the locations, and their order, the recursive walk gave: a key under an
    # empty path is written alone, any other key after a dot (an empty key too), an index in brackets.
    fixture = ('"" = {f = "e0", "" = "e1"}\na = ["a0", ["a1", "a2"], {b = "a3", "" = "a4"}]\n'
               '[t]\n"" = {c = "t0"}\n"d.e" = "t1"\n[[arr]]\nx = "r0"\n[[arr]]\ny = ["r1"]\n')
    want = [("x.toml: f", "e0"), ("x.toml: ", "e1"), ("x.toml: a[0]", "a0"), ("x.toml: a[1][0]", "a1"),
            ("x.toml: a[1][1]", "a2"), ("x.toml: a[2].b", "a3"), ("x.toml: a[2].", "a4"), ("x.toml: t..c", "t0"),
            ("x.toml: t.d.e", "t1"), ("x.toml: arr[0].x", "r0"), ("x.toml: arr[1].y[0]", "r1")]
    got = [(location, prose) for location, prose, _context in toml_units(fixture, "x.toml")]
    check("nesting/toml-locations-unchanged", got == want, got)

    # Markdown: blockquotes and list items nested deep within the size bound give the result of one level.
    claim = "PR #17 remains pending.\n"
    for name, shallow, nested in (
            ("nesting/markdown-deep-blockquotes-read-as-one-level", "> " + claim, ">" * 60000 + " " + claim),
            ("nesting/markdown-deep-spaced-blockquotes-read-as-one-level", "> " + claim, "> " * 30000 + claim),
            ("nesting/markdown-deep-list-markers-on-one-line-read-as-one-level", "- " + claim, "- " * 30000 + claim),
            ("nesting/markdown-deep-indented-list-items-read-as-one-level", "- " + claim,
             "".join(" " * (2 * i) + "- item {}\n".format(i) for i in range(200)) + " " * 400 + "- " + claim),
            ("nesting/markdown-deep-lists-in-blockquotes-read-as-one-level", "> - " + claim, "> - " * 15000 + claim)):
        results = []
        for text in (shallow, nested):
            try:
                code, res, t = _judge(text, m)
                results.append((code, len(res.hard), len(res.warnings), len(res.unknown), t.calls))
            except Exception as exc:
                results.append((type(exc).__name__, str(exc)[:300]))
        check(name, len(nested.encode("utf-8")) <= MAX_BYTES and results[0] == results[1]
              and isinstance(results[0][0], int), results)

    # End to end, each in its own process through --draft: no traceback, and the bound named where it is
    # reached. The documents name no pull request, so no state is read.
    with tempfile.TemporaryDirectory(prefix="pending-pr-claims-nesting-") as tmp:
        for name, target, text, want in (
                ("nesting/process-toml-deep-table-headers", "changelog.toml", deep_headers, 2),
                ("nesting/process-toml-deep-inline-arrays", "changelog.toml", parsed_arrays, 2),
                ("nesting/process-toml-deep-inline-tables", "changelog.toml", unparsed_tables, 2),
                ("nesting/process-markdown-deep-lists", "TODO.md", "- " * 30000 + "item\n", 0),
                ("nesting/process-markdown-deep-blockquotes", "TODO.md", "> " * 30000 + "text\n", 0)):
            draft = Path(tmp) / ("draft" + Path(target).suffix)
            draft.write_bytes(text.encode("utf-8"))
            child = subprocess.run([sys.executable, "-I", str(Path(__file__).resolve()), "--draft", str(draft),
                                    "--target", target], capture_output=True, stdin=subprocess.DEVNULL, timeout=120)
            err = child.stderr.decode("utf-8", "replace")
            check(name, child.returncode == want and "Traceback" not in err and (want != 2 or named(err)),
                  (child.returncode, err[-300:]))

    # Before the reader: the nesting scan refuses a document whose syntax nests past the bound before
    # tomllib is given it (the reader copies a dotted key's prefixes, so a 12,000-part key held about
    # 590 MiB before it was read), naming the bound. The red leg, the scan off, hands each to the reader.
    early = "found before the TOML reader ran"

    def scanned(text):
        """(the reason _parse_toml gives, or None; whether the reader was given text)."""
        before = _TOML_READS[0]
        why = _parse_toml(text, "x.toml")[1]
        return why, _TOML_READS[0] != before
    small_past = (".".join(["a"] * (TOML_MAX_DEPTH + 2)) + ' = ""\n',
                  "[[" + ".".join(["a"] * TOML_MAX_DEPTH) + "]]\n",
                  "[" + ".".join(["a"] * 60) + "]\n" + ".".join(["b"] * 42) + ' = ""\n',
                  "x = {" + ".".join(["a"] * 150) + ' = ""}\n', parsed_arrays, parsed_tables)
    for name, text in (
            ("nesting/toml-reported-dotted-key-refused-before-the-reader", ".".join(["a"] * 12000) + '=""\n'),
            ("nesting/toml-dotted-key-without-a-value-refused-before-the-reader", ".".join(["a"] * 12000) + "\n"),
            ("nesting/toml-dotted-key-past-the-bound-refused-before-the-reader", small_past[0]),
            ("nesting/toml-array-of-tables-header-past-the-bound-refused-before-the-reader", small_past[1]),
            ("nesting/toml-header-then-dotted-key-past-the-bound-refused-before-the-reader", small_past[2]),
            ("nesting/toml-inline-dotted-key-past-the-bound-refused-before-the-reader", small_past[3]),
            ("nesting/toml-deep-header-refused-before-the-reader", deep_headers),
            ("nesting/toml-deep-inline-arrays-refused-before-the-reader", parsed_arrays),
            ("nesting/toml-deep-inline-tables-refused-before-the-reader", unparsed_tables)):
        why, reached = scanned(text)
        check(name, why is not None and named(why) and early in why and not reached, (why, reached))
    red = [_with_globals({"TOML_PRE_SCAN": False}, lambda: scanned(text)) for text in small_past]
    check("nesting/toml-refused-before-the-reader/red-when-reverted",
          all(reached and (why is None or early not in why) for why, reached in red), red)
    # The scan counts only what the syntax nests: brackets, braces, dots and quotes inside strings and
    # comments are stepped over, so such a document is read exactly as with the scan off.
    stepped = ('a = "' + "[{." * 200 + '\\" ]" # [[[[ {{{{ ' + "." * 200 + "\n"
               "b = '" + "[" * 200 + "'\n"
               'c = """ "" ' + "[" * 200 + ' \\""" ' + "." * 200 + ' """"\n'
               "d = '''" + "{" * 200 + " '' ''''\n"
               '"' + "e." * 200 + '" = "PR #17 remains pending."\n'
               "f = [ # [[[[\n  1.5, 1979-05-27T07:32:00.5Z, 'x[', \"y{\",\n]\n"
               "[t] # [[[[ " + "." * 200 + "\n" + "g." * 90 + 'h = "#17 is open"\n')
    on = _with_globals({"TOML_PRE_SCAN": True}, lambda: toml_units(stepped, "x.toml"))
    off = _with_globals({"TOML_PRE_SCAN": False}, lambda: toml_units(stepped, "x.toml"))
    check("nesting/toml-scan-steps-over-strings-and-comments", _toml_nesting(stepped, "x.toml") is None
          and on == off and len(on) == 8, (len(on), len(off)))
    # At the bound the scan lets the document through and it is read as before: a dotted key of
    # TOML_MAX_DEPTH + 1 parts, a header and a dotted key that together reach the bound, and an
    # array-of-tables header whose element is at the bound.
    for name, text, path in (
            ("nesting/toml-dotted-key-at-the-bound-read",
             ".".join(["a"] * (TOML_MAX_DEPTH + 1)) + ' = "PR #17 remains pending."\n',
             ".".join(["a"] * (TOML_MAX_DEPTH + 1))),
            ("nesting/toml-header-and-dotted-key-at-the-bound-read",
             "[" + ".".join(["a"] * 60) + "]\n" + ".".join(["b"] * 41) + ' = "PR #17 remains pending."\n',
             ".".join(["a"] * 60 + ["b"] * 41)),
            ("nesting/toml-array-of-tables-at-the-bound-read",
             "[[" + ".".join(["a"] * (TOML_MAX_DEPTH - 1)) + ']]\nx = "PR #17 remains pending."\n',
             ".".join(["a"] * (TOML_MAX_DEPTH - 1)) + "[0].x")):
        try:
            code, res, _t = _judge(text, m, TOML_SURFACE)
            got = (code, len(res.hard), len(res.hard) == 1 and "changes.toml: {}:".format(path) in res.hard[0])
        except Exception as exc:
            got = (type(exc).__name__, str(exc)[:300])
        check(name, got == (1, 1, True), got)
    # An array of tables an earlier header named is a level under every later header that goes through
    # it (the array and its last element), and the scan counts it: the reported document, 2,878 bytes of
    # array-of-tables headers each one part longer than the one before, reaches 102 in the parsed
    # document, and a table header under one array of tables reaches 101; both are refused before the
    # reader. The red leg, the scan counting only the array of tables its own header names, hands both to
    # the reader. The walk after the reader still refuses them with the scan off (a defence in depth).
    prefixed = "[[a]]\n[" + ".".join(["a"] * TOML_MAX_DEPTH) + ']\nx = ""\n'

    def chain(last):
        return ("".join("[[" + ".".join(["a"] * n) + "]]\n" for n in range(1, last + 1))
                + 'x = "PR #17 remains pending."\n')
    reported_chain = "".join("[[" + ".".join(["a"] * n) + "]]\n" for n in range(1, 52)) + 'x="PR #17 is pending"\n'
    check("nesting/reported-array-of-tables-chain-size", len(reported_chain.encode("utf-8")) == 2878,
          len(reported_chain.encode("utf-8")))
    for name, text in (("nesting/toml-reported-array-of-tables-chain-refused-before-the-reader", reported_chain),
                       ("nesting/toml-array-of-tables-prefix-refused-before-the-reader", prefixed)):
        why, reached = scanned(text)
        check(name, why is not None and named(why) and early in why and not reached, (why, reached))
    red = [_with_globals({"TOML_SCAN_ARRAYS": False}, lambda: scanned(text)) for text in (reported_chain, prefixed)]
    check("nesting/toml-array-of-tables-levels/red-when-reverted", all(reached and why is None for why, reached in red),
          red)
    walked = []
    for text in (reported_chain, prefixed):
        try:
            walked.append(("collected", len(_with_globals(
                {"TOML_PRE_SCAN": False}, lambda: collect([(TOML_SURFACE, text.encode("utf-8"))])))))
        except InputError as exc:
            walked.append(named(str(exc)) and early not in str(exc))
    check("nesting/toml-array-of-tables-levels-refused-by-the-walk-with-the-scan-off", walked == [True, True],
          walked)
    # One header shorter, the chain's last element is at the bound and its value is read.
    try:
        code, res, _t = _judge(chain(TOML_MAX_DEPTH // 2), m, TOML_SURFACE)
        got = (code, len(res.hard), len(res.hard) == 1 and "changes.toml: {}:".format(
            "[0].".join(["a"] * (TOML_MAX_DEPTH // 2)) + "[0].x") in res.hard[0])
    except Exception as exc:
        got = (type(exc).__name__, str(exc)[:300])
    check("nesting/toml-array-of-tables-chain-at-the-bound-read", got == (1, 1, True), got)

    # The depth the reader builds, measured by instrumenting it (never read from its error): its rules
    # and value readers are wrapped for one call, and the depth is the deepest of the document it built
    # (headers and dotted keys, kept when it refuses the text later), each inline array or table as the
    # reader opens it, and each dotted key's tables as the reader reads the key and its value, each at the
    # depth it has or would have in the document.
    def parsed_depth(data):
        deepest, pending = 0, [(data, 0)]
        while pending:
            value, depth = pending.pop()
            deepest = max(deepest, depth)
            pending.extend((child, depth + 1) for child in (value.values() if isinstance(value, dict) else value)
                           if isinstance(child, (dict, list)))
        return deepest

    def reader_depth(text):
        """(the deepest table or array the TOML reader built from text before it returned or refused it,
        the parsed document or None when it refused text)."""
        import tomllib._parser
        space = tomllib._parser.__dict__      # the reader's module globals, which its functions call through
        names = ("create_dict_rule", "create_list_rule", "key_value_rule", "parse_key_value_pair", "parse_key",
                 "parse_array", "parse_inline_table")
        saved = {name: space[name] for name in names}
        seen, frames, outputs = [0], [], []     # frames: ["kv", its table's depth, its key's parts] or
                                                # ["array", its depth], the open key/value pairs and arrays

        def path_depth(data, key):
            depth, value = 0, data
            for part in key:
                value = value[part]
                depth += 1
                if isinstance(value, list):
                    value, depth = value[-1], depth + 1
            return depth

        def opened():
            top = frames[-1]
            return top[1] + (top[2] if top[0] == "kv" else 1)

        def header_rule(name):
            def rule(src, pos, out, *rest):
                outputs.append(out)
                return saved[name](src, pos, out, *rest)
            return rule

        def key_value_rule(src, pos, out, header, *rest):
            outputs.append(out)
            frames.append(["kv", path_depth(out.data.dict, header), 0])
            try:
                return saved["key_value_rule"](src, pos, out, header, *rest)
            finally:
                frames.pop()

        def parse_key_value_pair(*args):
            frames[-1][2] = 0
            got = saved["parse_key_value_pair"](*args)
            seen[0] = max(seen[0], frames[-1][1] + len(got[1]) - 1)
            return got

        def parse_key(*args):
            got = saved["parse_key"](*args)
            if frames and frames[-1][0] == "kv" and frames[-1][2] == 0:
                frames[-1][2] = len(got[1])
            return got

        def inline(name, kind):
            def read(*args):
                depth = opened()
                seen[0] = max(seen[0], depth)
                frames.append([kind, depth, 0])
                try:
                    return saved[name](*args)
                finally:
                    frames.pop()
            return read
        wrapped = {"create_dict_rule": header_rule("create_dict_rule"),
                   "create_list_rule": header_rule("create_list_rule"), "key_value_rule": key_value_rule,
                   "parse_key_value_pair": parse_key_value_pair, "parse_key": parse_key,
                   "parse_array": inline("parse_array", "array"),
                   "parse_inline_table": inline("parse_inline_table", "kv")}
        space.update(wrapped)
        try:
            try:
                data = tomllib.loads(text)
            except (ValueError, RecursionError, MemoryError):
                data = None     # not TOML the reader reads (TOMLDecodeError is a ValueError)
        finally:
            space.update(saved)
        return max([seen[0]] + [parsed_depth(out.data.dict) for out in outputs[:1]]), data

    # A header whose closing bracket or brackets are missing: the reader builds the table or array of
    # tables its key names before it looks for the closer, so the scan counts and bounds that level as for
    # a closed header. The reported document, 207 bytes, an array-of-tables header with one closing bracket
    # under an earlier array of tables, passed the scan (read by its syntax alone, 100 deep) and the reader
    # built it 101 deep before refusing it; now it is refused before the reader, as are the same header
    # with no closer, at the end of the text and with its closer split by a space, and (as before, its
    # key passes the bound) a table header with no closer under the array. The red leg, the scan reading
    # an unclosed header by its syntax alone, hands each array-of-tables header to the reader. One level
    # shorter, the header passes the scan and the reader refuses it.
    unclosed_key = ".".join(["a"] * (TOML_MAX_DEPTH - 1))
    reported_unclosed = "[[a]]\n[[" + unclosed_key + "]\n"
    check("nesting/reported-unclosed-array-of-tables-header-size", len(reported_unclosed.encode("utf-8")) == 207,
          len(reported_unclosed.encode("utf-8")))
    built = reader_depth(reported_unclosed)
    check("nesting/toml-reported-unclosed-header-built-past-the-bound-by-the-reader",
          built[0] == TOML_MAX_DEPTH + 1 and built[1] is None, built[0])
    unclosed = (reported_unclosed, "[[a]]\n[[" + unclosed_key + "\n", "[[a]]\n[[" + unclosed_key,
                "[[a]]\n[[" + unclosed_key + "] ]\n", "[[a]]\n[" + ".".join(["a"] * TOML_MAX_DEPTH) + "\n")
    why, reached = scanned(reported_unclosed)
    check("nesting/toml-reported-unclosed-header-refused-before-the-reader",
          why is not None and named(why) and early in why and "line 2:" in why and not reached, (why, reached))
    got = [scanned(text) for text in unclosed[1:]]
    check("nesting/toml-unclosed-headers-refused-before-the-reader",
          all(why is not None and named(why) and early in why and not reached for why, reached in got), got)
    red = [_with_globals({"TOML_SCAN_UNCLOSED": False}, lambda: scanned(text)) for text in unclosed[:4]]
    check("nesting/toml-unclosed-headers/red-when-reverted",
          all(reached and (why is None or early not in why) for why, reached in red), red)
    at_bound = "[[a]]\n[[" + ".".join(["a"] * (TOML_MAX_DEPTH - 2)) + "]\n"
    why, reached = scanned(at_bound)
    check("nesting/toml-unclosed-header-at-the-bound-given-to-the-reader",
          reached and why is not None and "is not valid TOML" in why
          and _toml_depth(at_bound, TOML_MAX_DEPTH) == (TOML_MAX_DEPTH, None)
          and reader_depth(at_bound) == (TOML_MAX_DEPTH, None), (why, reached))

    # The scan's depth is the parsed document's, never lower (the reader is never given a document past
    # the bound) and never higher (no document within the bound is refused), over a generated corpus:
    # every sequence of one to three table and array-of-tables headers whose keys are one to three parts
    # from two names, each followed by a dotted key holding an inline array of inline tables, with every
    # name spelled bare, literal, basic, escaped and spaced in turn (the same key, however spelled); and
    # seeded documents mixing headers, dotted keys, inline tables with dotted keys, nested inline arrays
    # and multi-line arrays. For a document the reader refuses, the scan's depth is never lower than the
    # depth the reader built before refusing it (reader_depth), over a generated corpus of malformed
    # documents: every sequence of one or two of those headers whose last header has no closer or (an
    # array-of-tables header) one closing bracket, and the seeded documents each cut short, with one
    # character dropped, or with one character inserted or replaced. For a document the reader reads, the
    # instrumented depth is the parsed document's (the instrument itself is checked).
    def compared(texts):
        """[read, lower, higher, an example of each difference, refused, lower than the reader built
        before refusing, instrumented depth not the parsed depth] over texts."""
        tally = [0, 0, 0, [], 0, 0, 0]
        for text in texts:
            got = _toml_depth(text, 10 ** 9)[0]
            built, data = reader_depth(text)
            if data is None:
                tally[4] += 1
                if got < built:
                    tally[5] += 1
                    tally[3].append((text, got, built))
                continue
            want = parsed_depth(data)
            tally[0] += 1
            tally[6] += built != want
            if got != want:
                tally[1 if got < want else 2] += 1
                tally[3].append((text, got, want))
        tally[3] = tally[3][:2]
        return tally
    spellings = {"a": ("a", '"a"', "'a'", '"\\u0061"', " a "), "b": ("b", "'b'", '"\\U00000062"', '"b"', "b\t")}
    turn = [0]

    def spelled(parts):
        pieces = []
        for part in parts:
            pieces.append(spellings[part][turn[0] % 5])
            turn[0] += 1
        return ".".join(pieces)
    heads = [(kind, parts) for kind in ("[", "[[") for size in (1, 2, 3)
             for parts in itertools.product("ab", repeat=size)]
    headed = []
    for count in (1, 2, 3):
        for sequence in itertools.product(heads, repeat=count):
            headed.append("".join(kind + spelled(parts) + ("]]" if kind == "[[" else "]") + "\nk.v = [{w = 1}]\n"
                                  for kind, parts in sequence))
    pick = random.Random(18)
    values = ("1", "'s'", "[1, [2, [3]]]", "{p.q = 1, r = [{s = 1}]}", "[{t.u = [1]}]", "[\n 1,\n [2],\n]",
              '"[{."', "{}", "[]", "{ a = { b = [ { c = 1 } ] } }")
    mixed = []
    for _ in range(4000):
        lines = []
        for _line in range(pick.randrange(1, 7)):
            kind = pick.random()
            key = ".".join(pick.choice((q, '"' + q + '"', "'" + q + "'"))
                           for q in pick.choices("abc", k=pick.randrange(1, 5)))
            if kind < 0.55:
                lines.append(("[[" + key + "]]") if kind < 0.3 else ("[" + key + "]"))
            else:
                lines.append(".".join(pick.choices("xyz", k=pick.randrange(1, 4))) + " = " + pick.choice(values))
        mixed.append("\n".join(lines) + "\n")
    malformed = []
    for count in (1, 2):
        for sequence in itertools.product(heads, repeat=count):
            before = "".join(kind + spelled(parts) + ("]]" if kind == "[[" else "]") + "\nk.v = [{w = 1}]\n"
                             for kind, parts in sequence[:-1])
            kind, parts = sequence[-1]
            key = spelled(parts)
            malformed.extend(before + kind + key + closer + "\nk.v = [{w = 1}]\n"
                             for closer in (("", "]") if kind == "[[" else ("",)))
    noise = "[]{}=.,\"'#\n \tab1"
    for text in mixed:
        for _ in range(2):
            at, how = pick.randrange(len(text) + 1), pick.randrange(4)
            malformed.append(text[:at] if how == 0 else text[:at] + text[at + 1:] if how == 1
                             else text[:at] + pick.choice(noise) + text[at + (how == 3):])
    for name, texts in (("nesting/toml-scan-depth-is-the-parsed-depth-over-generated-headers", headed),
                        ("nesting/toml-scan-depth-is-the-parsed-depth-over-generated-documents", mixed)):
        tally = compared(texts)
        check(name, tally[0] >= len(texts) // 2 and tally[1:3] == [0, 0] and tally[5:] == [0, 0], tally)
    tally = compared(malformed)
    check("nesting/toml-scan-depth-never-below-the-reader-over-generated-malformed-documents",
          tally[4] >= len(malformed) // 2 and tally[0] > 0 and tally[1:3] == [0, 0] and tally[5:] == [0, 0],
          tally)
    tally = _with_globals({"TOML_SCAN_ARRAYS": False}, lambda: compared(headed))
    check("nesting/toml-scan-depth-is-the-parsed-depth/red-when-reverted", tally[1] > 0, tally[:3])
    tally = _with_globals({"TOML_SCAN_UNCLOSED": False}, lambda: compared(malformed))
    check("nesting/toml-scan-depth-never-below-the-reader/red-when-reverted", tally[5] > 0, tally[4:6])
    # End to end under a memory limit: the reported dotted key at 16,000 parts (32,003 bytes; before,
    # about 1 GiB resident, and a 512 MiB limit gave a MemoryError) through --draft in a child limited
    # to 512 MiB of address space ends naming the bound, before the reader.
    with tempfile.TemporaryDirectory(prefix="pending-pr-claims-nesting-") as tmp:
        draft = Path(tmp) / "draft.toml"
        draft.write_bytes((".".join(["a"] * 16000) + '=""\n').encode("utf-8"))
        child = subprocess.run([sys.executable, "-I", str(Path(__file__).resolve()), "--draft", str(draft),
                                "--target", "changelog.toml"], capture_output=True, stdin=subprocess.DEVNULL,
                               timeout=120, preexec_fn=_limit_address_space)
        err = child.stderr.decode("utf-8", "replace")
        check("nesting/process-memory-limited-toml-deep-dotted-key", draft.stat().st_size == 32003
              and child.returncode == 2 and named(err) and early in err and "MemoryError" not in err
              and "Traceback" not in err, (child.returncode, err[-300:]))
        # The reported array-of-tables chain through --draft ends naming the bound, before the reader
        # (before, the reader parsed it 102 deep and the walk refused it after).
        draft.write_bytes(reported_chain.encode("utf-8"))
        child = subprocess.run([sys.executable, "-I", str(Path(__file__).resolve()), "--draft", str(draft),
                                "--target", "changelog.toml"], capture_output=True, stdin=subprocess.DEVNULL,
                               timeout=120)
        err = child.stderr.decode("utf-8", "replace")
        check("nesting/process-toml-reported-array-of-tables-chain", draft.stat().st_size == 2878
              and child.returncode == 2 and named(err) and early in err and "Traceback" not in err,
              (child.returncode, err[-300:]))
        # The reported unclosed array-of-tables header through --draft ends naming the bound, before the
        # reader (before, the reader built it 101 deep and refused it as TOML that does not parse).
        draft.write_bytes(reported_unclosed.encode("utf-8"))
        child = subprocess.run([sys.executable, "-I", str(Path(__file__).resolve()), "--draft", str(draft),
                                "--target", "changelog.toml"], capture_output=True, stdin=subprocess.DEVNULL,
                               timeout=120)
        err = child.stderr.decode("utf-8", "replace")
        check("nesting/process-toml-reported-unclosed-array-of-tables-header", draft.stat().st_size == 207
              and child.returncode == 2 and named(err) and early in err and "is not valid TOML" not in err
              and "Traceback" not in err, (child.returncode, err[-300:]))


def _self_test_marks(m, tempfile):
    """In-band marks: the characters this check writes into a text as its own marks (IN_BAND_MARKS) are
    refused in input, raw or spelled by a TOML escape in a string value or a key, so no input can spell a
    mark (before, a spelled pull request token ended in an uncaught IndexError, or a ValueError past the
    int-string limit, and the other marks changed what was read). Other private-use characters are text."""
    def refused(surface, text):
        try:
            got = collect([(surface, text.encode("utf-8"))])
        except InputError as exc:
            return "IN_BAND_MARKS" in str(exc) and "reserves for its own marks" in str(exc)
        except Exception as exc:
            return type(exc).__name__
        return ("collected", len(got))
    for name, surface, text in (
            ("marks/markdown-reference-token-refused", MD_SURFACE, "\ue0000\ue001\n"),
            ("marks/markdown-oversized-token-index-refused", MD_SURFACE, "\ue000" + "9" * 5000 + "\ue001\n"),
            ("marks/markdown-escaped-pipe-mark-refused", MD_SURFACE, _status_table("In progress (#17) \ue002")),
            ("marks/markdown-issue-mark-refused", MD_SURFACE, "PR \ue00317 remains pending.\n"),
            ("marks/markdown-code-filler-refused", MD_SURFACE, "PR #17 \ue004 remains pending.\n"),
            ("marks/toml-escaped-reference-token-refused", TOML_SURFACE, 'x="\\uE0000\\uE001"\n'),
            ("marks/toml-escaped-oversized-token-index-refused", TOML_SURFACE,
             'x="\\uE000' + "9" * 5000 + '\\uE001"\n'),
            ("marks/toml-escaped-nul-placeholder-refused", TOML_SURFACE, 'x = "[a][b] \\u00000\\u0000"\n'),
            ("marks/toml-escaped-pipe-mark-refused", TOML_SURFACE, 'x = "a \\uE002 b"\n'),
            ("marks/toml-escaped-issue-mark-refused", TOML_SURFACE, 'x = "PR \\uE00317 remains pending."\n'),
            ("marks/toml-escaped-code-filler-refused", TOML_SURFACE, 'x = "PR #17 \\ue004 remains pending."\n'),
            ("marks/toml-literal-mark-refused", TOML_SURFACE, "x = 'PR \ue00017\ue001 remains pending.'\n"),
            ("marks/toml-escaped-key-mark-refused", TOML_SURFACE, '"k\\uE000" = "PR #17 remains pending."\n'),
            ("marks/toml-escaped-table-key-mark-refused", TOML_SURFACE, '[t."\\uE001"]\nx = "PR #17 is open."\n')):
        check(name, refused(surface, text) is True, refused(surface, text))
    # Every NUL or private-use character the checker's code (before the self-test) writes or reads as a
    # mark, spelled raw or as an escape, is one IN_BAND_MARKS holds, so a new mark cannot go unrefused.
    source = Path(__file__).read_text(encoding="utf-8")
    code = source[:source.index("\n# Self-test (synthetic")]
    spelled = {int(found, 16) for found in re.findall(r"\\u([eEfF][0-9a-fA-F]{3})", code)
               if 0xE000 <= int(found, 16) <= 0xF8FF}
    spelled |= {0} if re.search(r"\\(?:x00|u0000)", code) else set()
    spelled |= {ord(ch) for ch in code if ch == "\x00" or 0xE000 <= ord(ch) <= 0xF8FF}
    check("marks/every-source-mark-reserved", spelled == {ord(ch) for ch in IN_BAND_MARKS},
          sorted(hex(point) for point in spelled))
    # Other private-use characters are text: the claim beside them is read as without them.
    for name, text in (("marks/other-private-use-is-text", "PR #17 remains pending. " + chr(0xE005) + "\n"),
                       ("marks/supplementary-private-use-is-text", chr(0xF0000) + " PR #17 remains pending.\n")):
        try:
            code_, res, _t = _judge(text, m)
            got = (code_, len(res.hard))
        except Exception as exc:
            got = (type(exc).__name__, str(exc)[:200])
        check(name, got == (1, 1), got)
    # End to end, each in its own process through --draft: exit 2 naming the reservation, no traceback
    # (before: exit 1 and an uncaught IndexError or ValueError).
    with tempfile.TemporaryDirectory(prefix="pending-pr-claims-marks-") as tmp:
        for name, target, text in (
                ("marks/process-markdown-reference-token", "TODO.md", "\ue0000\ue001\n"),
                ("marks/process-markdown-oversized-token-index", "TODO.md", "\ue000" + "9" * 5000 + "\ue001\n"),
                ("marks/process-toml-escaped-reference-token", "changelog.toml", 'x="\\uE0000\\uE001"\n'),
                ("marks/process-toml-escaped-oversized-token-index", "changelog.toml",
                 'x="\\uE000' + "9" * 5000 + '\\uE001"\n')):
            draft = Path(tmp) / ("draft" + Path(target).suffix)
            draft.write_bytes(text.encode("utf-8"))
            child = subprocess.run([sys.executable, "-I", str(Path(__file__).resolve()), "--draft", str(draft),
                                    "--target", target], capture_output=True, stdin=subprocess.DEVNULL, timeout=120)
            err = child.stderr.decode("utf-8", "replace")
            check(name, child.returncode == 2 and "IN_BAND_MARKS" in err and "Traceback" not in err,
                  (child.returncode, err[-300:]))


def _self_test():
    import contextlib
    import io
    import tempfile
    import time
    m = {(REPO, 17): "merged"}
    c = {(REPO, 17): "closed"}
    o = {(REPO, 17): "open"}

    # --- lifecycle outcomes for one hard claim ---
    code, res, _t = _judge("PR #17 remains pending.\n", m)
    check("state/merged-hard-finding", code == 1 and len(res.hard) == 1 and "MERGED" in res.hard[0], res.hard)
    red = _with_rules_removed({"pending-verb"}, lambda: _judge("PR #17 remains pending.\n", m)[0])
    check("state/merged-hard-finding/red-when-reverted", red == 0, red)
    code, res, _t = _judge("PR #17 remains pending.\n", c)
    check("state/closed-hard-finding-names-closed", code == 1 and "CLOSED" in res.hard[0]
          and "MERGED" not in res.hard[0], res.hard)
    code, res, _t = _judge("PR #17 remains pending.\n", o)
    check("state/open-passes", code == 0 and not res.hard and not res.unknown, (code, res.hard))
    code, res, _t = _judge("PR #17 remains pending.\n", {})
    check("state/unknown-404-cannot-evaluate", code == 2 and not res.hard and "404" in res.unknown[0],
          res.unknown)

    # --- reference forms ---
    for name, text in (("ref/pr-colon", "Fix is pending pr:17.\n"), ("ref/pr-colon-hash", "Fix is pending pr:#17.\n"),
                       ("ref/pull-request-words", "pull request #17 is still pending\n"),
                       ("ref/github-url", "https://github.com/acme/widgets/pull/17 is pending\n")):
        code, _res, t = _judge(text, m)
        check(name, code == 1 and t.calls == [(REPO, 17)], (code, t.calls))
    code, _res, t = _judge("PR 17 remains pending\n", m)
    check("ref/bare-number-not-a-reference", code == 0 and t.calls == [], (code, t.calls))
    code, _res, t = _judge("peer/repo#17 is pending\n", {("peer/repo", 17): "merged", (REPO, 17): "open"})
    check("ref/explicit-repository-never-local", code == 1 and t.calls == [("peer/repo", 17)], t.calls)

    # --- batch and lists ---
    text = "This is stacked on the pending batch (#17, #18).\n"
    code, res, _t = _judge(text, {(REPO, 17): "closed", (REPO, 18): "closed"})
    check("batch/both-closed-two-findings", code == 1 and _numbers(res.hard) == [17, 18], res.hard)
    red = _with_rules_removed({"pending-batch"},
                              lambda: _judge(text, {(REPO, 17): "closed", (REPO, 18): "closed"})[1])
    check("batch/both-closed-two-findings/red-when-reverted", not red.hard, red.hard)
    code, res, _t = _judge(text, {(REPO, 17): "open", (REPO, 18): "merged"})
    check("batch/only-stale-member", code == 1 and _numbers(res.hard) == [18], res.hard)
    code, res, t = _judge("#17 merged; #18 remains pending\n", {(REPO, 17): "merged", (REPO, 18): "merged"})
    check("bind/semicolon-binds-only-second", code == 1 and t.calls == [(REPO, 18)], t.calls)
    code, res, t = _judge("#17 and #18 remain open\n", {(REPO, 17): "merged", (REPO, 18): "closed"})
    check("bind/coordinated-list-binds-both", code == 1 and _numbers(res.hard) == [17, 18], res.hard)
    red = _with_rules_removed({"open-verb"}, lambda: _judge("#17 and #18 remain open\n",
                                                       {(REPO, 17): "merged", (REPO, 18): "closed"})[0])
    check("bind/coordinated-list-binds-both/red-when-reverted", red == 0, red)
    code, res, _t = _judge("The open pull requests #17 and #18 carry it.\n",
                           {(REPO, 17): "merged", (REPO, 18): "open"})
    check("bind/open-noun-phrase", code == 1 and _numbers(res.hard) == [17], res.hard)
    code, _res, t = _judge("Leave open #17 for the maintainer.\n", m)
    check("bind/instruction-not-a-claim", code == 0 and t.calls == [], t.calls)

    # --- warnings versus hard findings ---
    code, res, _t = _judge("This branch is stacked on #17.\n", m)
    check("warn/stacked-merged-is-warning", code == 0 and len(res.warnings) == 1 and not res.hard,
          (code, res.warnings))
    red = _with_rules_removed({"stacked"}, lambda: _judge("This branch is stacked on #17.\n", m)[1])
    check("warn/stacked-merged-is-warning/red-when-reverted", not red.warnings, red.warnings)
    code, res, _t = _judge("Still waiting on #17.\n", c)
    check("warn/waiting-on-closed-is-warning", code == 0 and len(res.warnings) == 1, res.warnings)
    code, res, _t = _judge("Waiting for #17 to merge.\n", c)
    check("hard/waiting-to-merge-closed", code == 1 and len(res.hard) == 1 and not res.warnings,
          (res.hard, res.warnings))
    red = _with_rules_removed({"waiting-to-merge"}, lambda: _judge("Waiting for #17 to merge.\n", c)[1])
    check("hard/waiting-to-merge-closed/red-when-reverted", not red.hard and len(red.warnings) == 1,
          (red.hard, red.warnings))
    code, res, _t = _judge("#17 is not yet merged\n", m)
    check("hard/not-yet-merged", code == 1, res.hard)
    code, res, _t = _judge("PR #17 is under review\n", c)
    check("hard/under-review", code == 1, res.hard)

    # --- the TODO Status cell ---
    table = "| ID | Status | Item |\n| --- | --- | --- |\n| X-1 | In progress (#17, #18) | Do it |\n"
    code, res, t = _judge(table, {(REPO, 17): "open", (REPO, 18): "merged"})
    check("status/in-progress-binds-both", code == 1 and sorted(t.calls) == [(REPO, 17), (REPO, 18)]
          and _numbers(res.hard) == [18], (t.calls, res.hard))
    red = _with_rules_removed({"status-in-progress"},
                              lambda: _judge(table, {(REPO, 17): "open", (REPO, 18): "merged"})[0])
    check("status/in-progress-binds-both/red-when-reverted", red == 0, red)
    code, _res, t = _judge("| ID | Status | Item |\n| --- | --- | --- |\n| X-1 | Planned | In progress (#17) |\n", m)
    check("status/only-the-status-column", code == 0 and t.calls == [], t.calls)
    code, _res, t = _judge("- [ ] Tidy the widget (fixed by #17)\n", m)
    check("checkbox/open-checkbox-no-claim", code == 0 and t.calls == [], t.calls)

    # --- what must not trigger ---
    for name, text in (
            ("quiet/deployment-pending-after-merge", "Deployment is pending after #17 merged.\n"),
            ("quiet/waiting-for-deployment", "Waiting for deployment of #17.\n"),
            ("quiet/no-longer-pending", "#17 is no longer pending.\n"),
            ("quiet/unmerged-closed", "#17 is unmerged.\n"),
            ("quiet/past-tense", "#17 was pending before the merge.\n"),
            ("quiet/earlier-attempt", "An earlier attempt, closed pull request #17, was not merged.\n"),
            ("quiet/no-cross-sentence", "The release is pending. #17 merged the fix.\n"),
            ("quiet/history-quoted-span", "Previously recorded: \u201cPR #17 is pending.\u201d\n"),
            ("quiet/history-backtick-span", "The old note read `waiting on #17`.\n"),
            ("quiet/history-blockquote", "Earlier note:\n\n> waiting for #17 to merge\n"),
            ("quiet/fenced-example", "```example\nPR #17 is pending\n```\n")):
        code, res, t = _judge(text, {(REPO, 17): "closed"})
        check(name, code == 0 and t.calls == [] and not res.hard and not res.warnings, (code, t.calls))
    red = _with_steps_removed({"history-span"},
                              lambda: _judge("Previously recorded: \u201cPR #17 is pending.\u201d\n", c)[0])
    check("quiet/history-quoted-span/red-when-reverted", red == 1, red)

    code, res, _t = _judge("Previously recorded: \u201cPR #17 is pending.\u201d Now PR #18 remains open.\n",
                           {(REPO, 18): "merged"})
    check("history/current-assertion-after-quote", code == 1 and _numbers(res.hard) == [18], res.hard)
    code, res, _t = _judge("#17 was pending; #18 remains pending\n", {(REPO, 18): "merged"})
    check("history/only-current-clause", code == 1 and _numbers(res.hard) == [18], res.hard)
    code, res, _t = _judge("PR `#17` is \u201cpending\u201d.\n", m)
    check("format/inline-formatting-does-not-hide", code == 1, res.hard)
    code, res, _t = _judge("Previously blocked; #17 remains pending\n", m)
    check("format/previously-does-not-exempt-row", code == 1, res.hard)
    code, res, _t = _judge("> PR #17 is pending\n", m)
    check("quote/bare-blockquote-incomplete", code == 2 and not res.hard and "blockquote" in res.unknown[0],
          res.unknown)
    code, res, _t = _judge("The pending PR still needs review.\n", m)
    check("ambiguous/unnamed-target-incomplete", code == 2 and "ambiguous" in res.unknown[0], res.unknown)

    # --- every grammar rule on both sides, and the reference, structure and negation fixes ---
    _rule_rows(m, o)
    _fix_rows(m, o)

    # --- TOML decoding and wrapped prose ---
    toml_text = 'items = ["""Fix is\\u0020waiting for #17\n   to merge."""]\n'
    code, res, _t = _judge(toml_text, c, surface=TOML_SURFACE)
    check("toml/escaped-wrapped-string-decoded", code == 1, (code, res.hard))
    code, res, _t = _judge("Fix is waiting for\n#17 to merge, as noted.\n", c)
    check("markdown/wrapped-paragraph-joined", code == 1, (code, res.hard))
    try:
        _judge("items = [\n", c, surface=TOML_SURFACE)
        got = None
    except InputError as exc:
        got = str(exc)
    check("toml/malformed-input-error", got is not None and "not valid TOML" in got, got)

    # --- deduplication ---
    code, res, t = _judge("PR #17 remains pending. PR #17 is still open.\n", m)
    check("dedupe/one-read-shared-result", t.calls == [(REPO, 17)] and len(res.hard) == 2, (t.calls, res.hard))

    # --- the reader's validation ---
    for name, body in (
        ("reader/wrong-number", _response(18, "open")),
        ("reader/wrong-repository", _response(17, "open", repo="peer/repo")),
        ("reader/invalid-timestamp", _response(17, "closed", "2026-11-01 10:00:00")),
        ("reader/open-with-merge-time", _response(17, "open", MERGED_AT)),
        ("reader/missing-merged-at", b'{"number": 17, "state": "closed", "html_url": '
                                     b'"https://github.com/acme/widgets/pull/17", '
                                     b'"base": {"repo": {"full_name": "acme/widgets"}}}'),
        ("reader/invalid-json", b"{not json"),
        ("reader/boolean-number", _response(17, "open").replace(b'"number": 17', b'"number": true')),
        ("reader/merged-flag-contradiction", _response(17, "closed", MERGED_AT, merged=False)),
        ("reader/unknown-state", _response(17, "draft")),
        ("reader/two-objects", _response(17, "open") + _response(17, "open")),
    ):
        code, res, _t = _judge("PR #17 remains pending\n", {(REPO, 17): (0, body, b"")})
        check(name, code == 2 and not res.hard, (code, res.unknown))
    for name, entry in (("reader/denied-403", (1, b"", b"gh: Forbidden (HTTP 403)")),
                        ("reader/timeout", subprocess.TimeoutExpired(["gh"], 1)),
                        ("reader/launch-failure", FileNotFoundError(2, "gh"))):
        code, res, _t = _judge("PR #17 remains pending\n", {(REPO, 17): entry})
        check(name, code == 2 and not res.hard, (code, res.unknown))
    code, res, t = _judge("PR #17 remains pending\n", {(REPO, 17): (1, b"", b"gh: HTTP 502")})
    check("reader/transient-retried-once", code == 2 and len(t.calls) == 2, t.calls)
    many = " ".join("#{} is pending.".format(n) for n in range(1, 4)) + "\n"
    small = dict(CONFIG, limits={"max-prs": 2, "call-timeout-seconds": 10, "deadline-seconds": 120})
    code, res, t = _judge(many, {(REPO, n): "open" for n in range(1, 4)}, config=small)
    check("bound/request-limit-names-unread", code == 2 and len(t.calls) == 2
          and "request limit" in " ".join(res.unknown), res.unknown)
    ticks = iter([0.0, 0.0, 500.0, 500.0, 500.0])
    states = _pr_state.read_states([(REPO, 1), (REPO, 2)], FakeTransport({(REPO, 1): "open", (REPO, 2): "open"}),
                                   deadline=120, clock=lambda: next(ticks))
    check("bound/deadline-names-unread", states[(REPO, 1)][0] == "OPEN"
          and states[(REPO, 2)][0] == "UNKNOWN" and "deadline" in states[(REPO, 2)][1], states)
    code, res, t = _judge("Train subject (#17) shipped. PR #17 remains pending.\n", o)
    check("reader/direct-state-not-subject", code == 0, (code, res.hard))
    code, res, _t = _judge("PR #17 remains pending. PR #18 remains pending.\n",
                           {(REPO, 17): "merged", (REPO, 18): (1, b"", b"HTTP 404")})
    check("result/hard-and-unknown-both-reported", code == 2 and len(res.hard) == 1 and len(res.unknown) == 1,
          (res.hard, res.unknown))

    # --- origin binding ---
    for name, runner in (("origin/missing", lambda _r: (2, b"")),
                         ("origin/unsupported-host", lambda _r: (0, b"https://gitlab.com/acme/widgets\n")),
                         ("origin/two-urls", lambda _r: (0, b"https://github.com/a/b\nhttps://github.com/c/d\n")),
                         ("origin/credential-url", lambda _r: (0, b"https://tok@github.com/acme/widgets\n"))):
        code, res, t = _judge("PR #17 remains pending\n", m, git_runner=runner)
        check(name, code == 2 and t.calls == [] and "tok" not in " ".join(res.unknown), res.unknown)
    pinned = dict(MD_SURFACE, **{"expected-repository": "other/widgets"})
    code, res, t = _judge("PR #17 remains pending\n", m, surface=pinned)
    check("origin/expected-repository-mismatch", code == 2 and t.calls == [], res.unknown)
    for name, url in (("origin/accepted-form/scp", "git@github.com:acme/widgets.git"),
                      ("origin/accepted-form/ssh", "ssh://git@github.com/acme/widgets"),
                      ("origin/accepted-form/https", "https://github.com/acme/widgets")):
        check(name, _pr_state.repo_from_url(url) == REPO, url)

    # --- declaration validation and declared files ---
    for name, mutate in (("config/unknown-top-key", lambda d: d.update(extra=1)),
                         ("config/bad-format", lambda d: d["surface"][0].update(format="html")),
                         ("config/escaping-path", lambda d: d["surface"][0].update(path="../x.md")),
                         ("config/limit-out-of-range", lambda d: d["limits"].update({"max-prs": 0}))):
        data = {"schema-version": 1, "limits": dict(CONFIG["limits"]),
                "surface": [dict(MD_SURFACE), dict(TOML_SURFACE)]}
        mutate(data)
        try:
            validate_config(data)
            got = None
        except InputError as exc:
            got = str(exc)
        check(name, got is not None, got)
    with tempfile.TemporaryDirectory(prefix="pending-pr-claims-") as tmp:
        try:
            _read_bytes(Path(tmp) / "absent.md", "absent.md")
            got = None
        except InputError as exc:
            got = str(exc)
        check("input/unreadable-declared-file", got is not None and "cannot be read" in got, got)
        # The size bound at its ends: one byte under and exactly MAX_BYTES are read, one byte over is not.
        for name, size, refused in (("input/size-under-bound-read", MAX_BYTES - 1, False),
                                    ("input/size-at-bound-read", MAX_BYTES, False),
                                    ("input/size-over-bound-refused", MAX_BYTES + 1, True)):
            sized = Path(tmp) / "sized.md"
            sized.write_bytes(b"a" * size)
            try:
                got = len(_read_bytes(sized, "sized.md"))
            except InputError as exc:
                got = str(exc)
            check(name, got == ("sized.md: larger than {} bytes".format(MAX_BYTES) if refused else size), got)
    # A draft given as bytes (tools/apply_record_draft.py) is held to the same bound.
    err = io.StringIO()
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
        got = judge_draft_bytes(ROOT, "TODO.md", b"a" * (MAX_BYTES + 1))
    check("input/draft-over-bound-refused", got == 2 and "draft larger than {} bytes".format(MAX_BYTES)
          in err.getvalue(), (got, err.getvalue()))

    # The report bound (REPORT_MAX_CHARS), end to end through the draft entry point: one reference link
    # to a long pull URL used n times in one claim gives n findings, each quoting the clause with every
    # use expanded. At 3,000 uses (15,145 bytes) that report would need about 1.1 billion characters; it
    # is cannot-evaluate naming the bound, with what was built held under it. With the bound removed,
    # 200 uses already build more than the bound (the red leg); 20 uses are within it and unchanged.
    long_repo = "a/" + "b" * 100

    def expanded(n):
        return ("[x]: https://github.com/" + long_repo + "/pull/17\n\npending " + "[x], " * n + "\n").encode()

    def judged_draft(data):
        sink = io.StringIO()
        with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            code = judge_draft_bytes(ROOT, "TODO.md", data, FakeTransport({(long_repo, 17): "merged"}), _origin_ok)
        return code, sink.getvalue()
    bound_named = "{} characters (REPORT_MAX_CHARS)".format(REPORT_MAX_CHARS)
    code, out = judged_draft(expanded(3000))
    check("report/bound-reached-on-expanded-references", code == 2 and bound_named in out
          and len(out) <= REPORT_MAX_CHARS + 4096, (code, len(out), out[-300:]))
    code, out = _with_globals({"REPORT_BOUNDED": False}, lambda: judged_draft(expanded(200)))
    check("report/bound-reached-on-expanded-references/red-when-reverted", code == 1 and len(out) > REPORT_MAX_CHARS,
          (code, len(out)))
    code, out = judged_draft(expanded(20))
    check("report/within-bound-unchanged", code == 1 and out.count("FINDING: ") == 20 and bound_named not in out,
          (code, out[-300:]))
    # The same bound on the lines no pull request state is read for: n distinct references on one line
    # in a fence give n hidden-reference warnings, each quoting the line.
    code, res, t = _judge("```\n" + " ".join("#{}".format(i) for i in range(1, 9001)) + "\n```\n", m)
    check("report/bound-reached-on-hidden-references", code == 2 and t.calls == []
          and any(bound_named in line for line in res.unknown)
          and sum(len(line) for line in res.warnings + res.unknown) <= REPORT_MAX_CHARS + 4096,
          (code, len(res.warnings), res.unknown[-1:]))

    # The collection bound (COLLECT_MAX_CHARS) and the shared quotes. The entries of one line share one
    # quoted copy of it, even when stripping leading white space makes a new string: n references on a
    # line with leading white space and non-BMP text hold one copy, not n (with each copy built again,
    # the red leg, they hold n). A copy built for each entry is counted against the bound, so collection
    # fails closed, naming the bound, before it holds more (the red leg, unbounded, collects every copy).
    collect_named = "{} characters (COLLECT_MAX_CHARS)".format(COLLECT_MAX_CHARS)

    def spaced(n):
        return " " + " ".join("#{}".format(i) for i in range(1, n + 1)) + " \U0001f600"

    def collected(surface, text):
        try:
            return collect([(surface, text.encode("utf-8"))])
        except InputError as exc:
            return str(exc)

    markdown_spaced = (MD_SURFACE, "```\n" + spaced(1000) + "\n```\n")
    toml_spaced = (TOML_SURFACE, 'a = """<!--\n' + spaced(1000) + '\n-->"""\n')
    unshared = {"SHARED_QUOTES": False, "COLLECT_BOUNDED": False}
    for name, (surface, text), replacements, want in (
            ("collect/markdown-line-quoted-once", markdown_spaced, {}, 1),
            ("collect/markdown-line-quoted-once/red-when-reverted", markdown_spaced, unshared, 1000),
            ("collect/toml-line-quoted-once", toml_spaced, {}, 1),
            ("collect/toml-line-quoted-once/red-when-reverted", toml_spaced, unshared, 1000)):
        got = _with_globals(replacements, lambda: collected(surface, text))
        quotes = None if isinstance(got, str) else [entry[5] for entry in got if entry[2] == HIDDEN]
        check(name, quotes is not None and len(quotes) == 1000 and len({id(quote) for quote in quotes}) == want,
              got if quotes is None else len({id(quote) for quote in quotes}))
    for name, (surface, text), replacements in (
            ("collect/markdown-copied-quotes-bound-reached", markdown_spaced, {"SHARED_QUOTES": False}),
            ("collect/markdown-copied-quotes-bound-reached/red-when-reverted", markdown_spaced, unshared),
            ("collect/toml-copied-quotes-bound-reached", toml_spaced, {"SHARED_QUOTES": False}),
            ("collect/toml-copied-quotes-bound-reached/red-when-reverted", toml_spaced, unshared)):
        got = _with_globals(replacements, lambda: collected(surface, text))
        want_refused = not name.endswith("/red-when-reverted")
        check(name, (isinstance(got, str) and collect_named in got) if want_refused
              else (not isinstance(got, str) and len(got) == 1000), got if isinstance(got, str) else len(got))
    # A location built anew for each unit is counted each time: a long header cell over many table rows
    # (each cell's location names it), a long TOML key path over many string values.
    long_header = (MD_SURFACE, "| " + "h" * 1000 + " |\n| --- |\n" + "| a |\n" * 4500)
    long_key = (TOML_SURFACE, '["' + "k" * 1000 + '"]\n' + "".join("v{} = ''\n".format(i) for i in range(4500)))
    for name, (surface, text), replacements in (
            ("collect/table-header-cell-bound-reached", long_header, {}),
            ("collect/table-header-cell-bound-reached/red-when-reverted", long_header, {"COLLECT_BOUNDED": False}),
            ("collect/toml-key-path-bound-reached", long_key, {}),
            ("collect/toml-key-path-bound-reached/red-when-reverted", long_key, {"COLLECT_BOUNDED": False})):
        got = _with_globals(replacements, lambda: collected(surface, text))
        want_refused = not name.endswith("/red-when-reverted")
        check(name, (isinstance(got, str) and collect_named in got) if want_refused else not isinstance(got, str),
              got if isinstance(got, str) else len(got))
    # Within the bound nothing changes: the declared surfaces collect the same entries with it and without.
    real = [(surface, _read_bytes(ROOT / surface["path"], surface["path"])) for surface in load_config(ROOT)["surface"]]
    got = collect(real)
    check("collect/within-bound-unchanged", got == _with_globals({"COLLECT_BOUNDED": False}, lambda: collect(real))
          and got == _with_globals({"SHARED_QUOTES": False}, lambda: collect(real)), len(got))
    # End to end under a memory limit: the draft entry point, in its own process limited to 512 MiB of
    # address space, given the 52,907-byte document of 9,000 references on one fenced line with leading
    # white space and non-BMP text. Before the quotes were shared, collection held a copy of the line per
    # reference (about 950 MB) and ended in an uncaught MemoryError; it ends with the report bound named.
    with tempfile.TemporaryDirectory(prefix="pending-pr-claims-memory-") as tmp:
        draft = Path(tmp) / "draft.md"
        draft.write_bytes(("```\n" + spaced(9000) + "\n```\n").encode("utf-8"))
        child = subprocess.run([sys.executable, "-I", str(Path(__file__).resolve()), "--draft", str(draft),
                                "--target", "TODO.md"], capture_output=True, stdin=subprocess.DEVNULL, timeout=120,
                               preexec_fn=_limit_address_space)
        out = child.stdout.decode("utf-8", "replace")
        err = child.stderr.decode("utf-8", "replace")
        check("collect/memory-limited-draft-whitespace-non-bmp", draft.stat().st_size == 52907
              and child.returncode == 2 and bound_named in out and "MemoryError" not in err,
              (child.returncode, err[-300:], out[-300:]))

    _self_test_nesting(m, tempfile, contextlib, io)
    _self_test_marks(m, tempfile)

    # The copy bound counts the document's UTF-8 bytes, not its characters: a document whose bytes allow
    # its copies is evaluated, as is its twin of the same byte count in ASCII; the twin of the same
    # character count (fewer bytes) is refused.
    def copying(tail):
        return "[x]: " + "a" * 1000 + "(b)\n\n" + "[x] " * 60 + "\n\n" + tail + "\n"
    for name, tail, want in (("copy-bound/multibyte-counted-in-bytes", "\U0001f600" * 5000, 0),
                             ("copy-bound/same-bytes-ascii-evaluated", "aaaa" * 5000, 0),
                             ("copy-bound/same-characters-fewer-bytes-refused", "a" * 5000, "refused")):
        try:
            got = _judge(copying(tail), m)[0]
        except InputError as exc:
            got = "refused" if "a bound on running time" in str(exc) else str(exc)
        check(name, got == want, got)

    # --- real processes: a stub gh executable behind the production transport ---
    with tempfile.TemporaryDirectory(prefix="pending-pr-claims-gh-") as tmp:
        stub = Path(tmp) / "gh"
        stub.write_text(
            "#!{} -I\n"
            "import os, sys, signal, time\n"
            "mode = open(os.path.join(os.path.dirname(sys.argv[0]), 'mode')).read()\n"
            "assert sys.argv[1:5] == ['api', '--hostname', 'github.com', '-H'], sys.argv\n"
            "assert 'GH_HOST' not in os.environ\n"
            "if mode == 'merged':\n"
            "    sys.stdout.write({!r})\n"
            "elif mode == 'signal':\n"
            "    os.kill(os.getpid(), signal.SIGTERM)\n"
            "elif mode == 'exit3':\n"
            "    sys.exit(3)\n"
            "elif mode == 'slow':\n"
            "    time.sleep(30)\n".format(sys.executable, _response(17, "closed", MERGED_AT).decode()))
        os.chmod(stub, 0o700)
        transport = _pr_state.GhTransport(search_path=tmp)
        os.environ["GH_HOST"] = "evil.example"
        try:
            for name, mode, want in (("process/stub-gh-merged", "merged", 1),
                                     ("process/signal-termination", "signal", 2),
                                     ("process/unexpected-exit", "exit3", 2)):
                (Path(tmp) / "mode").write_text(mode)
                candidates = collect([(MD_SURFACE, b"PR #17 remains pending\n")])
                got = evaluate(candidates, CONFIG, "/nonexistent", transport, _origin_ok).exit_code()
                check(name, got == want, got)
            (Path(tmp) / "mode").write_text("slow")
            quick = dict(CONFIG, limits={"max-prs": 100, "call-timeout-seconds": 1, "deadline-seconds": 3})
            began = time.monotonic()
            candidates = collect([(MD_SURFACE, b"PR #17 remains pending\n")])
            got = evaluate(candidates, quick, "/nonexistent", transport, _origin_ok)
            check("process/timeout-bounded", got.exit_code() == 2 and time.monotonic() - began < 15,
                  got.unknown)
        finally:
            del os.environ["GH_HOST"]
        missing = _pr_state.GhTransport(search_path=str(Path(tmp) / "absent-dir"))
        candidates = collect([(MD_SURFACE, b"PR #17 remains pending\n")])
        got = evaluate(candidates, CONFIG, "/nonexistent", missing, _origin_ok).exit_code()
        check("process/launch-failure", got == 2, got)

    # --- staged content, draft application, commit adapter ---
    _self_test_adapters(tempfile, contextlib, io)

    got = [_main_exit(argv) for argv in (["--bogus"], [], ["--all"], ["--draft", "x"],
                                         ["--execution-report", "relative/report"])]
    check("cli/unrecognized-arguments-exit-2", got == [2, 2, 2, 2, 2], got)
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        got = check_inputs(ROOT)
    check("live/real-tree-inputs-valid", got == 0, got)
    # The size bound was set from the real records with a margin (MAX_BYTES): each declared surface of
    # the tree stays within half of it, or the bound is to be measured again.
    sizes = {surface["path"]: (Path(ROOT) / surface["path"]).stat().st_size
             for surface in load_config(ROOT)["surface"]}
    check("live/real-records-within-half-the-size-bound", all(size <= MAX_BYTES // 2 for size in sizes.values()),
          sizes)

    if FAILURES:
        print("SELF-TEST FAIL: check_pending_pr_claims {} of {} check(s) failed".format(
            len(FAILURES), len(EXECUTED)))
        for failure in FAILURES:
            print("  - " + failure)
        return 1
    print("SELF-TEST PASS: check_pending_pr_claims {} checks over synthetic responses: each of the {} "
          "grammar rules binds its example to a merged pull request (finding or warning), "
          "passes it when open, is cannot-evaluate when unreadable and leaves its no-flag twin alone, and "
          "with that rule removed its example loses that finding or warning (another rule may still "
          "warn on it); the listed reference spellings, Markdown structures, cue rows, historical spans "
          "and ambiguous phrases are exercised by the named rows, not all of them with a twin; a prose "
          "grammar cannot be complete, and wording outside those rows is not claimed to be covered (see "
          "the contract's residuals)".format(len(EXECUTED), len(PATTERNS)))
    return 0


def _self_test_adapters(tempfile, contextlib, io):
    import errno
    import signal
    import apply_record_draft
    with tempfile.TemporaryDirectory(prefix="pending-pr-claims-stage-") as tmp:
        root = Path(tmp)
        os.makedirs(root / ".aiqt")
        (root / CONFIG_REL).write_text(
            'schema-version = 1\n[limits]\nmax-prs = 100\ncall-timeout-seconds = 10\ndeadline-seconds = 120\n'
            '[[surface]]\npath = "NOTES.md"\nformat = "markdown"\nrepository = "origin"\nrole = "source"\n'
            '[[surface]]\npath = "sub/LOG.md"\nformat = "markdown"\nrepository = "origin"\nrole = "source"\n')
        (root / "NOTES.md").write_text("PR #17 remains pending.\n")
        oid = "a" * 40
        blobs = {oid: b"Nothing current here.\n"}
        index = {"NOTES.md": "100644 {} 0\tNOTES.md\0".format(oid)}

        def fake_git(_root, args):
            if args[:2] == ["diff", "--cached"]:
                return 0, b"NOTES.md\0"
            if args[0] == "ls-files":
                return 0, index.get(args[-1], "").encode()
            return (0, blobs[args[2]]) if args[2] in blobs else (128, b"")

        sink = io.StringIO()
        with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            got = run_staged(root, FakeTransport({(REPO, 17): "merged"}), _origin_ok, fake_git)
        check("staged/uses-staged-bytes", got == 0, (got, sink.getvalue()))
        blobs[oid] = b"Fix is waiting for\n#17 to merge.\n"
        with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            got = run_staged(root, FakeTransport({(REPO, 17): "merged"}), _origin_ok, fake_git)
        check("staged/complete-unit-evaluated", got == 1, got)

        # A declared surface staged as a symlink (mode 120000; its blob is the link target's path) or
        # as an unmerged entry is cannot-evaluate, as a non-regular declared file is in --all.
        link_oid = "b" * 40
        blobs[link_oid] = b"REAL.md"
        index["NOTES.md"] = "120000 {} 0\tNOTES.md\0".format(link_oid)
        sink = io.StringIO()
        with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            got = run_staged(root, FakeTransport({(REPO, 17): "merged"}), _origin_ok, fake_git)
        check("staged/symlink-entry-refused", got == 2 and "120000" in sink.getvalue(), (got, sink.getvalue()))
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            got = _with_globals({"REGULAR_INDEX_MODES": REGULAR_INDEX_MODES + ("120000",)},
                                lambda: run_staged(root, FakeTransport({(REPO, 17): "merged"}), _origin_ok, fake_git))
        check("staged/symlink-entry-refused/red-when-reverted", got == 0, got)
        index["NOTES.md"] = "".join("100644 {} {}\tNOTES.md\0".format(oid, stage) for stage in (1, 2, 3))
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            got = run_staged(root, FakeTransport({(REPO, 17): "merged"}), _origin_ok, fake_git)
        check("staged/unmerged-entry-refused", got == 2, got)
        index["NOTES.md"] = "100755 {} 0\tNOTES.md\0".format(oid)
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            got = run_staged(root, FakeTransport({(REPO, 17): "merged"}), _origin_ok, fake_git)
        check("staged/executable-regular-entry-read", got == 1, got)

        # The draft is opened once and its type checked on that descriptor: a draft swapped for a
        # symlink just after a path-based type check (simulated by an os.lstat that swaps it) is not
        # followed, because no path-based check precedes the open.
        draft_path, secret = root / "swap-draft.md", root / "swap-secret.md"
        draft_path.write_bytes(b"draft bytes\n")
        secret.write_bytes(b"secret bytes\n")
        real_lstat = os.lstat
        swapped = []

        def swapping_lstat(path, *args, **kwargs):
            result = real_lstat(path, *args, **kwargs)
            if not swapped and Path(path) == draft_path:
                swapped.append(path)
                os.unlink(draft_path)
                os.symlink(secret, draft_path)
            return result
        os.lstat = swapping_lstat
        try:
            got = _read_bytes(draft_path, "swap-draft.md")
        except InputError as exc:
            got = str(exc)
        finally:
            os.lstat = real_lstat
        check("input/draft-type-checked-on-descriptor", got == b"draft bytes\n", got)
        os.unlink(draft_path)
        os.symlink(secret, draft_path)
        try:
            got = _read_bytes(draft_path, "swap-draft.md")
        except InputError as exc:
            got = str(exc)
        check("input/draft-symlink-refused", isinstance(got, str) and "not a regular file" in got, got)
        os.unlink(draft_path)
        os.mkfifo(draft_path)
        try:
            got = _read_bytes(draft_path, "swap-draft.md")
        except InputError as exc:
            got = str(exc)
        check("input/draft-fifo-refused-without-blocking", isinstance(got, str) and "not a regular file" in got,
              got)
        os.unlink(draft_path)

        target = root / "NOTES.md"
        before = target.read_bytes()
        for name, body, table, want in (
                ("apply/withheld-on-finding", b"PR #17 remains pending.\n", {(REPO, 17): "merged"}, 1),
                ("apply/withheld-on-unknown", b"PR #17 remains pending.\n", {}, 2)):
            draft = root / "draft.md"
            draft.write_bytes(body)
            with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
                got = apply_record_draft.apply(root, str(draft), "NOTES.md", FakeTransport(table), _origin_ok)
            check(name, got == want and target.read_bytes() == before, got)
        draft = root / "draft.md"
        draft.write_bytes(b"PR #17 remains pending.\n")

        def mutate():
            target.write_bytes(b"changed underneath\n")
        with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            got = apply_record_draft.apply(root, str(draft), "NOTES.md", FakeTransport({(REPO, 17): "open"}),
                                           _origin_ok, after_judge=mutate)
        check("apply/destination-changed-withheld", got == 2
              and target.read_bytes() == b"changed underneath\n", got)
        with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            got = apply_record_draft.apply(root, str(draft), "NOTES.md", FakeTransport({(REPO, 17): "open"}),
                                           _origin_ok)
        check("apply/clean-draft-written", got == 0 and target.read_bytes() == b"PR #17 remains pending.\n", got)
        with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            got = apply_record_draft.apply(root, str(draft), "OTHER.md", FakeTransport({}), _origin_ok)
        check("apply/undeclared-target-withheld", got == 2 and not (root / "OTHER.md").exists(), got)

        # A FIFO target with no writer is refused at once, never waited on: an alarm turns a blocked
        # open into a failed row, so the row is bounded. A regular target is written (the twin).
        class _Blocked(Exception):
            pass

        def _alarm(_signum, _frame):
            raise _Blocked()
        os.unlink(target)
        os.mkfifo(target)
        previous = signal.signal(signal.SIGALRM, _alarm)
        signal.setitimer(signal.ITIMER_REAL, 5)
        try:
            with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
                got = apply_record_draft.apply(root, str(draft), "NOTES.md", FakeTransport({(REPO, 17): "open"}),
                                               _origin_ok)
        except _Blocked:
            got = "still blocked in open() after 5 seconds"
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, previous)
        check("apply/fifo-target-refused", got == 2 and stat.S_ISFIFO(os.lstat(target).st_mode), got)
        os.unlink(target)
        target.write_bytes(b"old\n")
        with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            got = apply_record_draft.apply(root, str(draft), "NOTES.md", FakeTransport({(REPO, 17): "open"}),
                                           _origin_ok)
        check("apply/regular-target-written", got == 0 and target.read_bytes() == b"PR #17 remains pending.\n",
              got)

        # A read error after the target is opened is cannot-evaluate at the first preimage read and at
        # the second (after the judgement), and the target is left as it was; with reads restored the
        # draft is written (the twin).
        real_read = apply_record_draft._read_descriptor

        def failing_read(_fd, _limit):
            raise OSError(errno.EIO, os.strerror(errno.EIO))

        def fail_reads():
            apply_record_draft._read_descriptor = failing_read
        for name, early in (("apply/read-error-first-preimage-cannot-evaluate", True),
                            ("apply/read-error-second-preimage-cannot-evaluate", False)):
            target.write_bytes(b"old\n")
            if early:
                fail_reads()
            try:
                with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
                    got = apply_record_draft.apply(root, str(draft), "NOTES.md",
                                                   FakeTransport({(REPO, 17): "open"}), _origin_ok,
                                                   after_judge=None if early else fail_reads)
            except OSError as exc:
                got = "escaped {}".format(type(exc).__name__)
            finally:
                apply_record_draft._read_descriptor = real_read
            check(name, got == 2 and target.read_bytes() == b"old\n", got)
        with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            got = apply_record_draft.apply(root, str(draft), "NOTES.md", FakeTransport({(REPO, 17): "open"}),
                                           _origin_ok)
        check("apply/reads-restored-written", got == 0 and target.read_bytes() == b"PR #17 remains pending.\n",
              got)

        # The existing destination is read up to its own bound (apply_record_draft.EXISTING_MAX_BYTES,
        # 4 MiB), not the check's MAX_BYTES: a record past MAX_BYTES is replaced by a smaller draft, and
        # one past EXISTING_MAX_BYTES is refused and left as it was.
        for name, size, want in (("apply/oversized-existing-record-replaced", MAX_BYTES + 1, 0),
                                 ("apply/existing-record-over-own-bound-refused",
                                  apply_record_draft.EXISTING_MAX_BYTES + 1, 2)):
            old = b"a" * (size - 1) + b"\n"
            target.write_bytes(old)
            with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
                got = apply_record_draft.apply(root, str(draft), "NOTES.md", FakeTransport({(REPO, 17): "open"}),
                                               _origin_ok)
            check(name, got == want and target.read_bytes() == (b"PR #17 remains pending.\n" if want == 0 else old),
                  got)

        # The target's permission bits survive the replace (0644 and 0640 both kept).
        for name, mode in (("apply/mode-kept-0644", 0o644), ("apply/mode-kept-0640", 0o640)):
            target.write_bytes(b"old\n")
            os.chmod(target, mode)
            with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
                got = apply_record_draft.apply(root, str(draft), "NOTES.md", FakeTransport({(REPO, 17): "open"}),
                                               _origin_ok)
            check(name, got == 0 and stat.S_IMODE(os.stat(target).st_mode) == mode,
                  (got, oct(os.stat(target).st_mode)))

        # A symlinked parent directory is refused; a real one is written (the twin).
        outside = Path(tmp) / "outside"
        os.makedirs(outside)
        os.symlink(outside, root / "sub")
        with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            got = apply_record_draft.apply(root, str(draft), "sub/LOG.md", FakeTransport({(REPO, 17): "open"}),
                                           _origin_ok)
        check("apply/symlinked-parent-refused", got == 2 and not (outside / "LOG.md").exists(), got)
        os.unlink(root / "sub")
        os.makedirs(root / "sub")
        with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            got = apply_record_draft.apply(root, str(draft), "sub/LOG.md", FakeTransport({(REPO, 17): "open"}),
                                           _origin_ok)
        check("apply/real-parent-written", got == 0 and (root / "sub" / "LOG.md").read_bytes()
              == b"PR #17 remains pending.\n" and not (outside / "LOG.md").exists(), got)

        # A parent swapped for a symlink after it was opened withholds the write.
        def swap():
            os.rename(root / "sub", root / "sub-held")
            os.symlink(outside, root / "sub")
        os.unlink(root / "sub" / "LOG.md")
        with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            got = apply_record_draft.apply(root, str(draft), "sub/LOG.md", FakeTransport({(REPO, 17): "open"}),
                                           _origin_ok, after_judge=swap)
        check("apply/parent-swapped-after-open-withheld", got == 2 and not (outside / "LOG.md").exists()
              and not (root / "sub-held" / "LOG.md").exists(), got)

    adapter = ROOT / "tools" / "check_pending_pr_commit.sh"
    with tempfile.TemporaryDirectory(prefix="pending-pr-claims-hook-") as tmp:
        bindir = Path(tmp)
        (bindir / "git").write_text("#!/bin/sh\nprintf '%s\\n' \"" + tmp + "\"\n")
        os.makedirs(bindir / "tools")
        (bindir / "tools" / "check_pending_pr_claims.py").write_text("")
        for name, code, needle in (("commit/advisory-continues-on-incomplete", 2, "INCOMPLETE"),
                                   ("commit/advisory-continues-on-finding", 1, "ADVISORY")):
            (bindir / "python3").write_text("#!/bin/sh\nexit {}\n".format(code))
            for exe in ("git", "python3"):
                os.chmod(bindir / exe, 0o700)
            child = subprocess.run(["/bin/sh", str(adapter)], capture_output=True, stdin=subprocess.DEVNULL,
                                    env={"PATH": str(bindir)}, timeout=60)
            check(name, child.returncode == 0 and needle in child.stderr.decode("utf-8", "replace"),
                  (child.returncode, child.stderr))


def _main_exit(argv):
    """main()'s exit code for the argument list argv, its output captured."""
    import contextlib
    import io
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return main(list(argv))


def main(argv=None):
    if argv is None:
        # The process entry: the command line, ended through the execution-report finalizer when the
        # self-test armed it.
        _code = main(sys.argv[1:])
        _finalizer = sys.modules.get("_selftest_exit_report")
        (sys.exit if _finalizer is None else _finalizer.exit_with)(_code)
    report_path = None
    if len(argv) >= 2 and argv[-2] == "--execution-report" and os.path.isabs(argv[-1]) \
            and argv[:-2] in ([], ["--self-test"]):
        report_path, argv = argv[-1], ["--self-test"]
    if argv == ["--self-test"]:
        if report_path is not None:
            import _selftest_exit_report
            _selftest_exit_report.arm(report_path, SUITE_ID, EXECUTED)
        return _self_test()
    if argv == ["--check-inputs"]:
        return check_inputs(ROOT)
    if argv == ["--all", "--live"]:
        return run_all(ROOT)
    if argv == ["--staged", "--live"]:
        return run_staged(ROOT)
    if len(argv) == 4 and argv[0] == "--draft" and argv[2] == "--target":
        return run_draft(ROOT, argv[1], argv[3])
    print("error: unrecognized arguments {!r}; expected --check-inputs, --all --live, --staged --live, "
          "--draft PATH --target PATH or --self-test; fail closed".format(argv), file=sys.stderr)
    return 2


# The child-status guard: an inline copy of the canonical guard in opf/tools/_child_status.py, which
# tools/check_child_status_coverage.py pins this copy to by AST. The live modes start gh and git and read
# their exit status (subprocess), so under an ignored SIGCHLD a failing read would read as a pass; the
# guard runs at the entry line, before main() dispatches.
# The status the probe child exits with; any value other than 0 and the refusal exit would do.
_CHILD_STATUS_PROBE_CODE = 77


def _child_status_refusal():
    """None when this process can read a child's exit status, otherwise the reason it cannot."""
    import os
    import signal
    if not hasattr(signal, "SIGCHLD"):
        return None
    disposition = signal.getsignal(signal.SIGCHLD)
    if disposition is not signal.SIG_DFL:
        return ("SIGCHLD is ignored (SIG_IGN) in this process" if disposition is signal.SIG_IGN
                else "SIGCHLD is not at its default disposition in this process")
    if not hasattr(os, "fork"):
        return None
    try:
        pid = os.fork()
    except OSError as exc:
        return "the child-status probe could not fork ({})".format(type(exc).__name__)
    if pid == 0:
        os._exit(_CHILD_STATUS_PROBE_CODE)
    try:
        waited, status = os.waitpid(pid, 0)
    except ChildProcessError:
        return "the probe child was reaped before its exit status could be read (ECHILD)"
    except OSError as exc:
        return "the probe child's exit status could not be read ({})".format(type(exc).__name__)
    if waited != pid or os.waitstatus_to_exitcode(status) != _CHILD_STATUS_PROBE_CODE:
        return "a probe child that exited {} was read back as wait status {}".format(
            _CHILD_STATUS_PROBE_CODE, status)
    return None


def _child_status_refusal_exit(name):
    """0 when a child's exit status is observable; otherwise write the named refusal and exit 2 at once
    (os._exit, so no exit-time stream flush can replace the status)."""
    import os
    reason = _child_status_refusal()
    if reason is None:
        return 0
    try:
        sys.stderr.write("error: {} refuses to run: {}, so a failed child would read as a success. Nothing "
                         "was run (cannot evaluate). Start it from a parent that leaves SIGCHLD at "
                         "SIG_DFL.\n".format(name, reason))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)


if __name__ == "__main__":
    sys.exit(_child_status_refusal_exit("check_pending_pr_claims.py") or main())
