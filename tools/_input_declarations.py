#!/usr/bin/env python3
"""The one reading of a brief's INPUT declarations, shared by the brief lint's P6
(tools/check_brief_capability.py) and the delivery check's C1 (tools/check_verifier_delivery.py), so the
two can never disagree on what a brief declares.

THE PARSED DOCUMENT. A brief is Markdown, and the worker reads it through a Markdown reader, so the
declarations are judged on the document the vendored CommonMark parser builds (opf/tools/_vendor/marko,
pinned 2.2.4 with its provenance file and byte manifest, loaded as opf/tools/_commonmark_headings.py
loads it: the vendor directory derived from this file's own location, the version pinned, and the imported
modules origin-checked against the vendored tree, never an ambient install; with ONE stated difference
from that loader: importing the vendored GFM elements runs the gfm package's __init__, which builds a
module-level Markdown object and imports the GFM renderer at import time, where _commonmark_headings never
touches the extension path; marko 2.2.4 gives no import of the elements that skips its package __init__,
and loading the file directly would take an importlib exec_module call the dangerous-API lint refuses, so
the package and renderer modules the import pulls in are origin-checked here too and neither the Markdown
object nor the renderer is used), with the vendored GFM elements (tables, task-list
items, strikethrough, autolinked URLs and alerts) registered on a fresh extension-free parser per parse,
so the reading matches what a GFM reader shows. Conformance is exactly Marko 2.2.4's (about 97.5% of the
CommonMark 0.31.2 example suite; see opf/tools/selftest_commonmark_conformance.py), so on rare edge inputs
the reading diverges from a strict spec reading exactly where Marko does (a no-break space accepted after
a closing fence, for one). A parse failure, a vendored tree that cannot be imported or fails its
provenance pin, a text over MAX_TEXT_BYTES, a parse that misses PARSE_DEADLINE_SECONDS, or a document span
that does not cover the text is DeclarationError, which both callers turn into their cannot-evaluate
refusal, never a silent empty reading.

BOUNDS. The evaluation is bounded by construction, each bound decided on the text alone so no outcome
depends on unrelated content elsewhere in the brief: a text over MAX_TEXT_BYTES (512 KiB; every file in
the real-brief corpus measures under 224 KiB and every real brief under 64 KiB, so the ceiling holds each
with wide margin) is refused up front in forbidden(), whatever it holds; a text that can nest blocks more
than MAX_NESTING_DEPTH levels deep (judged by _nesting_refused, a conservative prescan over marker runs
and indentation whose running depth follows block structure, resetting where a new top-level block must
start, so a block's outcome never depends on content before its block) is refused up front too, because
the parser opens a run of nested quote markers in time that grows about twofold per level and a single
long regex match holds the interpreter where no in-process deadline can stop it; a line holding a run of
more than MAX_WHITESPACE_RUN consecutive whitespace characters (the class the parser itself reads as
whitespace, vertical tab and no-break space included, never only space and tab) is refused the same way,
because the parser reads such a run in time that grows with the square of the run, also inside matches a
deadline cannot interrupt. Each of those costs is additive across a text, so a text inside every per-item
bound could still be slow in total, answered on an idle host and refused on a busy one; three total-cost
bounds close that gap, each a sum the parser's measured cost grows with, capped where each sum's worst
measured in-bound text parses in about a second: MAX_WHITESPACE_COST over squared whitespace-run
lengths, MAX_NESTING_COST over deep marker openings, and MAX_INLINE_MARK_COST over squared per-chunk
counts of emphasis, strikethrough and code-span marks. The worst in-bound text measured on the vendored
pin, a text at the byte ceiling sitting at every cost cap at once with the rest deep steady quote
lines, parses in about 3.2 s, well under the deadline. Every prescan runs on every text, so no refusal depends on
whether the rest of the brief forces a parse. The parse itself runs in a fresh daemon thread (a full
recursion stack, whatever the caller's depth) joined under PARSE_DEADLINE_SECONDS, a backstop for shapes
outside the measured model: a pathological shape the parser reads superlinearly in many steps costs at
most about the deadline and is then refused, never trusted partial. Named residuals, each availability
only, never a wrong verdict: a thread a missed deadline abandons may keep consuming CPU until the process
exits; in a long-lived process such abandoned threads accumulate and compete with later parses, which can
push a later in-bound parse past its own deadline; process exit itself can be delayed while an abandoned
thread sits inside one long regex match, since the main thread cannot run until the match steps; and the
deadline fires only when the parser is between matches, so a single match that runs long under the bounds
above would stretch it. A brief that is pure ASCII, holds no "nput" substring in any case and none of the characters that
can make rendered text differ from source text (RENDERING_MARK_RE: a character reference's &, emphasis or
strikethrough or code marks, a link bracket, inline HTML's <, a backslash escape) is answered without
parsing: nothing in such a text can render, spell or disguise the label, and ASCII holds no format
character. Any other text takes the parse.

TEXT GATE (forbidden). Only LF ends a line here. A brief holding any other line separator anywhere (a
carriage return, bare or in CRLF, U+000C FORM FEED, which the parser reads as a line ending, U+0085 NEXT
LINE, U+2028 LINE SEPARATOR, U+2029 PARAGRAPH SEPARATOR), or any format character (Unicode category Cf: a
byte order mark, a zero-width or bidirectional control, a soft hyphen) outside a code block or code span of
the parsed document, is refused by the caller before any rule runs: a separator this reading does not split
on can hide a declaration from it, and a format character is invisible and can split or disguise a label or
a path. Nothing is removed silently. A format character inside a fenced or indented code block's span or a
code span's span is not refused here (code is literal text these rules never read); combining characters
(Mn, Me) are not refused anywhere, since the detection fold below removes them instead.

CODE (rule one). Code declares nothing and is never a look-alike: a fenced code block, an indented code
block and a code span are literal text, so a brief may quote a workflow's indented `inputs:` key, an example
INPUT line, or the label itself (`INPUT:`) in code. The one place code takes part is as the quoted form of a
declared path (below).

DECLARATIONS (read). A declaration is a paragraph at the document's top level or directly in a list item
(bullet or numbered; a task-list item, a block quote's or alert's paragraph, a heading and a table cell
never declare), judged one rendered line at a time (soft and hard line breaks split the paragraph into the
lines a reader sees, so two INPUT lines with no blank line between them are two declarations): a rendered
line declares when its text, after the parser has applied emphasis, strikethrough, backslash escapes and
character references, and after removing format (Cf) and combining (Mn, Me) characters and folding each
remaining character through NFKC and the bounded confusable table (CONFUSABLES), begins with the label
INPUT: (upper case, the colon directly after, no markup residue before it, and no part of the label in a
code span or inline HTML). So **INPUT:**, __INPUT:__, *INPUT:*, INPUT\\: and INPUT&#58; declare, and so do
a fullwidth or confusable-spelt label that renders as INPUT:, because the worker sees the same declaration
in each. After the label comes one path, taken from the rendered text: one word, one code span (the quoted
form for a path with spaces or markup characters), or one span in double quotes, then only whitespace on
that rendered line (input_shape judges the path: absolute, no `..` component, no control character and no
character of INPUT_SHAPE_CHARACTERS). Inline HTML in the label or the path, a word after the path, an
unclosed quote and a failed shape are a malformed declaration (bad), never ignored. Emphasis inside an
unquoted path is applied like any other emphasis (the reader sees the applied form); quote such a path in a
code span, where its literal characters are then judged by shape.

LOOK-ALIKES. A rendered paragraph line (wherever the paragraph sits), a heading, or a table cell whose
folded text begins, past leading whitespace, dashes (category Pd) and bare markup marks (LEAD_MARKS), with
the token INPUT or INPUTS in any case followed by optional spaces or tabs and a colon (NEAR_INPUT_RE over
the fold, which reads the listed confusable colons and the NFKC-foldable ones as the colon), and is not a
declaration above, is a malformed declaration (bad): Input:, INPUTS:, INPUT :, a fullwidth colon that does
not complete the exact label, **INPUT: with its markup unclosed, a task-list item's label, a bulleted or
dashed label, a quoted (>) label, a heading ## Inputs: and inline HTML around the label are all refused,
never read past. A table cell whose folded text is exactly INPUT or INPUTS (upper case; CELL_TOKENS) is a
look-alike too, so a header row | INPUT | path | is refused while | Input | Expected | is an ordinary
header. A line of a fenced or indented block read with the code rule patched out, and a line of an HTML
block once its tags are stripped, are scanned the same way (an HTML block or comment can hold text a reader
hides, so a label there is refused, never trusted). BOUNDS, named: the scan is anchored at the rendered
start, so prose that only mentions the word input (The tool's input: a TOML file.), a label glued after
other visible text (1.INPUT:, Step 1 - INPUT:), a mid-line table pipe in plain prose (| INPUT | x | with no
delimiter row) and inline code are not look-alikes; the confusable table is bounded, not a full confusables
list, so an unlisted look-alike letter or colon (small capitals, U+A4FD) passes; a link reference
definition ([INPUT]: /x) renders as nothing and declares nothing; and a soft-wrapped continuation line of a
declaration's paragraph is prose, as the line-based reading always had it.

This module opens no brief path itself and runs nothing beyond the vendored parser; the one filesystem
access is importing that parser. Callers: check_brief_capability (decode_brief refuses what forbidden()
finds, PreflightText reads read()), check_verifier_delivery (_declared_inputs refuses both and takes
read()'s declarations); both call forbidden() first, so read() may assume LF-only line ends.
"""
import bisect
import html
import os
import re
import sys
import threading
import unicodedata

MARKO_VERSION = "2.2.4"           # the exact vendored pin; any other resolved version fails closed
# the brief ceiling, enforced on every text before any other rule: 512 KiB holds every file of the
# real-brief corpus (largest file under 224 KiB, largest real brief under 64 KiB) with wide margin
MAX_TEXT_BYTES = 1 << 19
# the parse must finish within this wall-clock bound or the text is refused (DeclarationError): a plain
# text exactly at the ceiling parses in about 0.7 s on the vendored pin, and the worst measured in-bound
# text (at the byte ceiling and at every cost cap below at once) in about 3.2 s, so an in-bound text
# stays well under the deadline and the deadline is only a backstop for shapes outside the measured model
PARSE_DEADLINE_SECONDS = 10.0
# the nesting ceiling, judged by the prescan in forbidden() before any parse: the parser opens a run of
# quote markers on one line in time that grows about twofold per level (measured on the vendored pin:
# about 0.06 s at 20, about 4 s at 26), and a single long regex match holds the interpreter so no
# deadline can stop it mid-match, so the depth is capped where the worst single opening stays near a
# tenth of a second; a real brief nests far shallower
MAX_NESTING_DEPTH = 20
# the total parse cost of a text's nested openings: re-opening nested levels (a marker line after a
# blank line, or a line deeper than the one before it) costs the parser about two to the depth times
# 1e-7 s, additively across openings (measured on the vendored pin with only the depth ceiling applied:
# 0.10 s for one 20-deep quote opening, 2.1 s for twenty of them blank-separated), so a text inside the
# per-line depth ceiling could still be slow in total (16,000 blank-separated 13-marker quote openings
# stay inside every per-line bound and measured past the 10 s deadline), where whether it answered would
# depend on host load, not text. _nesting_refused charges each line that opens levels beyond those
# still open two to the power of its depth and caps the sum at ten full-depth openings' worth, about
# one second of measured opening cost; a line that only continues already-open levels costs the parser
# little at any in-bound depth (a text at the ceiling made of 20-deep quote lines measures about 1.2 s)
# and is charged nothing
MAX_NESTING_COST = 10 * (1 << MAX_NESTING_DEPTH)
# the block-structure resets of _nesting_refused, a patchable seam for its callers' self-tests
NESTING_RESETS = True
# the longest run of consecutive whitespace characters a line may hold, judged in forbidden() before any
# parse. The run class is the parser's own whitespace by construction, never a remembered list: every
# whitespace test in the vendored tree is Python's \s or the isspace/strip predicate behind it
# (marko/patterns.py whitespace r"\s+"; marko/inline_parser.py whitespace_re r"\s", the emphasis
# flanking test; marko/helpers.py normalize_label r"\s+"; marko/block.py SetextHeading str.isspace,
# ThematicBreak r"\s+", BlankLine str.strip, and the [^\n\S] classes of the Heading and HTML patterns;
# marko/ext/gfm/elements.py task-list r"\s", the autolink lookbehinds, the table splitter r"\s*" and the
# alert pattern r"\s*"), so the class here is [^\S\n], exactly \s minus the LF line end: space, tab,
# U+000B VERTICAL TAB, the information separators U+001C..U+001F, U+00A0 NO-BREAK SPACE, U+1680 OGHAM
# SPACE MARK, U+2000..U+200A, U+202F, U+205F and U+3000, plus the five SEPARATORS, which the caller
# refuses first as line separators. The parser reads one run in time that grows with the square of its
# length, inside single regex matches the deadline cannot interrupt (measured on the vendored pin:
# 93.5 s at a 64 KiB space run, 52 s at a 64 KiB vertical-tab run; about 0.1 s at this bound for space,
# vertical tab and no-break space alike); a real brief's longest run (alignment padding, indented code)
# is far shorter
MAX_WHITESPACE_RUN = 2000
WHITESPACE_RUN_RE = re.compile(r"[^\S\n]{%d}" % (MAX_WHITESPACE_RUN + 1))
# the total parse cost of a text's whitespace runs: a run costs the parser about its length squared times
# 3e-8 s at worst (a no-break-space run in its own paragraph), additively across runs, so a text inside
# the per-run bound could still be slow in total (260 runs of 2000 spaces measured 11 s, an answer or a
# deadline refusal depending on host load, not text). _whitespace_refused charges each maximal run of two
# or more class characters its length squared and caps the sum at eight full-length runs, which parse in
# about one second
MAX_WHITESPACE_COST = 8 * MAX_WHITESPACE_RUN ** 2
WHITESPACE_COST_RE = re.compile(r"[^\S\n]{2,}")
# the total parse cost of a text's inline marks: the parser's inline delimiter pass costs about the
# square of one paragraph's count of emphasis, strikethrough and code-span marks times 5e-10 s (measured
# on the vendored pin: 0.5 s at 32,768 asterisks in one paragraph, 14.6 s at 131,071, where the deadline
# would decide by host load, not text; backtick, tilde and underscore shapes measure cheaper), additively
# across paragraphs. _inline_marks_refused counts marks per chunk of lines between blank lines, which
# covers every paragraph the chunk can hold (marks in code are counted too, over-counting only, so a
# fenced example never widens what is read), charges each chunk its count squared and caps the sum at one
# full chunk of 32,768 marks, which parses in about half a second
MAX_INLINE_MARK_COST = 1 << 30
INLINE_MARK_CHARACTERS = "*_~`"
# the characters that can make rendered text differ from source text: a character reference (&#78;),
# emphasis, strikethrough or code marks, a link bracket, inline HTML and a backslash escape; a pure-ASCII
# text with none of them and no "nput" substring cannot render or disguise the label and skips the parse
RENDERING_MARK_RE = re.compile(r"[&*_~\[<\\`]")
INPUT_LABEL = "INPUT:"
# a table cell that is exactly this token (upper case, after the fold) is a look-alike header cell
CELL_TOKENS = ("INPUT", "INPUTS")
# removed before the label fold: format characters are invisible, combining marks draw over the letter
IGNORED_CATEGORIES = ("Cf", "Mn", "Me")
# bare marks skipped before the look-alike token: emphasis and strikethrough residue, a code backtick,
# U+2800 BRAILLE PATTERN BLANK (renders blank) and bullets; dashes (U+2043 HYPHEN BULLET among them) are
# skipped by category (Pd)
LEAD_MARKS = frozenset("*_~`\N{BULLET}\N{TRIANGULAR BULLET}\N{MIDDLE DOT}\N{BULLET OPERATOR}" + chr(0x2800))
# a character that leaves the declared path outside the text: a <name> placeholder, a variable, a home
# path, a glob or a brace
INPUT_SHAPE_CHARACTERS = frozenset("<>$~*?[]{}")
# only LF ends a line: any other separator can hide a declaration from this reading or split a line for
# the parser where this reading's line numbers do not split
SEPARATORS = {"\r": "U+000D CARRIAGE RETURN", "\x0c": "U+000C FORM FEED", "\x85": "U+0085 NEXT LINE",
              chr(0x2028): "U+2028 LINE SEPARATOR", chr(0x2029): "U+2029 PARAGRAPH SEPARATOR"}
SEPARATOR_RE = re.compile("[\r\x0c\x85" + chr(0x2028) + chr(0x2029) + "]")
# the bounded confusable fold (after NFKC): look-alikes of I N P U T and colon-like characters
CONFUSABLES = {
    "Ι": "I", "ι": "i", "І": "I", "і": "i", "Ӏ": "I", "ӏ": "i",
    "Ν": "N",
    "Ρ": "P", "ρ": "p", "Р": "P", "р": "p",
    "υ": "u",
    "Τ": "T", "τ": "t", "Т": "T", "т": "t",
    "꞉": ":", "∶": ":", "։": ":", "׃": ":", "ː": ":", "˸": ":",
}
# the look-alike scan: the token INPUT or INPUTS, spaces or tabs, then the colon, matched at a rendered
# line's start once leading whitespace, Pd dashes and LEAD_MARKS are skipped
NEAR_INPUT_RE = re.compile(r"INPUTS?+[ \t]*+:", re.I)
# the two code rules, patchable seams: a fenced or indented code block is outside every rule, and a code
# span's text is literal (never a label, never a look-alike; the quoted form of a declared path)
CODE_BLOCKS_ARE_CODE = True
CODE_SPANS_ARE_CODE = True
# the vendored GFM elements registered on each fresh parser, so the reading matches a GFM reader
GFM_ELEMENT_NAMES = ("Paragraph", "Strikethrough", "Url", "Table", "TableRow", "TableCell", "Alert")
# an HTML tag of an HTML block, stripped before that block's lines are scanned for the label
HTML_TAG_RE = re.compile(r"<[^>]*>")

LOOK_ALIKE_REASON = ("the line opens with a look-alike of the INPUT: label (another case, a colon-like "
                     "character, markup or HTML around the label, a quoted or task-list or heading or "
                     "table-cell label) and is never ignored; write INPUT: and one absolute path as its own "
                     "paragraph line, or quote the text in a code span or fence")

# opf/tools/_vendor, derived from THIS file's own absolute location (never the CWD), the same anchoring
# opf/tools/_commonmark_headings.py uses
_VENDOR_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "opf", "tools", "_vendor")
_LOADED = None                    # (marko module, gfm elements module) after a successful pinned load


class DeclarationError(Exception):
    """A fail-closed cannot-evaluate outcome from this reading (a vendored-parser import or provenance
    failure, an oversized text, a parse exception, or a document span that does not cover the text). A
    caller turns it into its own cannot-evaluate refusal; it is never an empty reading."""


def _verify_module_origin(module):
    """The module must resolve from a file physically under the vendored tree (an absolute realpath
    containment check, as opf/tools/_commonmark_headings.py makes it); DeclarationError otherwise. A forged
    __file__ or an import hook supplying an unchecked submodule is interpreter compromise, outside scope."""
    module_file = getattr(module, "__file__", None)
    if not module_file:
        raise DeclarationError("vendored module {!r} exposes no file path".format(
            getattr(module, "__name__", module)))
    resolved = os.path.realpath(module_file)
    anchor = os.path.realpath(_VENDOR_DIR) + os.sep
    if not resolved.startswith(anchor):
        raise DeclarationError("module {!r} resolved from {!r}, outside the vendored tree {!r}".format(
            getattr(module, "__name__", module), resolved, _VENDOR_DIR))


def _load_marko():
    """Import the vendored marko once, verify version and origin, and cache it. Coverage is BOUNDED to the
    modules imported here (marko, marko.block, marko.parser, marko.inline, marko.ext.gfm.elements, and the
    gfm package and renderer modules that import pulls in); the other parse-path submodules resolve from
    the verified package's own __path__. Importing marko.ext.gfm.elements necessarily runs the gfm
    package's __init__, which builds a module-level Markdown object and imports the GFM renderer (the one
    stated difference from opf/tools/_commonmark_headings.py, whose load never touches the extension
    path): both resolve from the vendored tree and are origin-checked below, and neither the Markdown
    object nor the renderer is ever used here. Never an ambient install: a mismatch is DeclarationError,
    not a fallback."""
    global _LOADED
    if _LOADED is not None:
        return _LOADED
    if _VENDOR_DIR not in sys.path:
        sys.path.insert(0, _VENDOR_DIR)
    try:
        import marko            # noqa: E402  vendored, provenance-checked below
        import marko.block      # noqa: E402,F401
        import marko.inline     # noqa: E402,F401
        import marko.parser     # noqa: E402,F401
        import marko.ext.gfm.elements as gfm_elements  # noqa: E402
    except DeclarationError:
        raise
    except Exception as exc:    # an unreadable or broken vendored tree is fail-closed, not a silent skip
        raise DeclarationError("cannot import the vendored marko parser from {}: {}".format(
            _VENDOR_DIR, exc)) from exc
    version = getattr(marko, "__version__", None)
    if version != MARKO_VERSION:
        raise DeclarationError("vendored marko version is {!r}, expected {!r}".format(
            version, MARKO_VERSION))
    pulled_in = tuple(sys.modules[name] for name in ("marko.ext.gfm", "marko.ext.gfm.renderer")
                      if name in sys.modules)
    for module in (marko, marko.block, marko.inline, marko.parser, gfm_elements) + pulled_in:
        _verify_module_origin(module)
    _LOADED = (marko, gfm_elements)
    return _LOADED


def _parse(text):
    """The marko Document for `text`: a FRESH extension-free Parser per call with the vendored GFM
    elements registered (never marko's Markdown object, which is documented not thread-safe). The byte
    ceiling is enforced before parsing. The parse runs in its own daemon thread, which starts with a full
    recursion stack whatever the caller's depth, and must finish within PARSE_DEADLINE_SECONDS: a parse
    that misses the deadline, a parse exception (a recursion error included) and a document span that does
    not cover `text` (the parser's buffer rewrites are length-preserving only over LF-only text, which
    forbidden() guarantees first) are DeclarationError, fail closed. A thread a missed deadline abandons
    may keep consuming CPU until the process exits; the refusal itself is prompt, and the residuals (the
    module docstring names each: abandoned threads accumulating in a long-lived process and competing
    with later parses, and process exit waiting out an abandoned thread's current match) are
    availability, never a wrong verdict."""
    if len(text.encode("utf-8")) > MAX_TEXT_BYTES:
        raise DeclarationError("the brief is larger than {} bytes, over this reading's ceiling".format(
            MAX_TEXT_BYTES))
    # the same prescans forbidden() runs, kept here too so no caller can reach an unbounded parse: these
    # shapes cost the parser time the deadline below cannot soundly bound, inside single matches it
    # cannot interrupt or in sums of steps whose refusal would otherwise depend on host load
    for prescan in (_whitespace_refused, _nesting_refused, _inline_marks_refused):
        found = prescan(text)
        if found is not None:
            raise DeclarationError("the brief is over this reading's bounds: " + found[1])
    marko, gfm_elements = _load_marko()
    outcome = []

    def parse_text():
        try:
            parser = marko.parser.Parser()
            for name in GFM_ELEMENT_NAMES:
                parser.add_element(getattr(gfm_elements, name))
            outcome.append(("document", parser.parse(text)))
        except BaseException as exc:    # held for the joining thread; a worker thread must not die silent
            outcome.append(("error", exc))

    worker = threading.Thread(target=parse_text, daemon=True, name="input-declarations-parse")
    worker.start()
    worker.join(PARSE_DEADLINE_SECONDS)
    if worker.is_alive() or not outcome:
        raise DeclarationError("the CommonMark parser did not finish over the brief within {} s, this "
                               "reading's deadline; the brief is refused, never read partially".format(
                                   PARSE_DEADLINE_SECONDS))
    kind, value = outcome[0]
    if kind == "error":
        if isinstance(value, DeclarationError):
            raise value
        raise DeclarationError("the CommonMark parser failed over the brief: {}".format(value)) from value
    document = value
    if getattr(document, "source_span", None) != (0, len(text)):
        raise DeclarationError("the parsed document span does not cover the brief's text")
    return document


def fold(text):
    """`text` folded for label detection: format and combining characters (IGNORED_CATEGORIES) removed,
    then each character folded through NFKC, then CONFUSABLES read as their ASCII forms."""
    if text.isascii():   # the removal, NFKC and the table leave ASCII as it is
        return text
    pieces = []
    for character in text:
        if unicodedata.category(character) in IGNORED_CATEGORIES:
            continue
        for folded in unicodedata.normalize("NFKC", character):
            pieces.append(CONFUSABLES.get(folded, folded))
    return "".join(pieces)


def unit_declares(kind):
    """Only a paragraph at the document's top level or directly in a list item declares; a quoted or
    task-list paragraph, a heading, a table cell, an HTML block and code never do."""
    return kind == "paragraph"


def _lead_skip(folded, start=0):
    """The index of the first folded character past leading whitespace, Pd dashes and LEAD_MARKS: where
    the look-alike token scan begins."""
    position = start
    while position < len(folded):
        character = folded[position]
        if not (character.isspace() or character in LEAD_MARKS
                or unicodedata.category(character) == "Pd"):
            break
        position += 1
    return position


def _line_scan(line, strip_tags=False):
    """Whether a raw text line (an HTML block's line, tags stripped; a code block's line when the code
    rule is patched out) opens with the look-alike token after the fold and the lead skip."""
    if strip_tags:
        line = HTML_TAG_RE.sub("", line)
    folded = fold(line)
    return NEAR_INPUT_RE.match(folded, _lead_skip(folded)) is not None


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


HTML_IN_INPUT_REASON = ("the declared input holds inline HTML (a <name> placeholder or a tag), so the "
                        "path it names is not in the text; write the path itself, or quote it in a code "
                        "span")


def _declaration_tail(characters):
    """(path, None) or (None, reason) for the rendered characters after the label: one word, one code
    span, or one double-quoted span, then only whitespace. `characters` is [(character, kind)] with kind
    "text", "code" or "html"."""
    position = 0
    while position < len(characters) and characters[position][1] == "text" \
            and characters[position][0].isspace():
        position += 1
    rest = characters[position:]
    if not rest:
        return input_shape("")
    if rest[0][1] == "code":
        end = 0
        while end < len(rest) and rest[end][1] == "code":
            end += 1
        path, tail = "".join(character for character, _ in rest[:end]), rest[end:]
    elif rest[0] == ('"', "text"):
        end = 1
        while end < len(rest) and rest[end][0] != '"':
            end += 1
        if end == len(rest):
            return None, "the declared input's quote does not close on its line"
        span = rest[1:end]
        if any(kind == "html" for _, kind in span):
            return None, HTML_IN_INPUT_REASON
        path, tail = "".join(character for character, _ in span), rest[end + 1:]
    else:
        end = 0
        while end < len(rest) and not rest[end][0].isspace():
            if rest[end][1] == "html":
                return None, HTML_IN_INPUT_REASON
            end += 1
        path, tail = "".join(character for character, _ in rest[:end]), rest[end:]
    if any(not character.isspace() for character, _ in tail):
        return None, ("a word follows the declared input {} (quote a path that holds a space in a code "
                      "span or double quotes, and write nothing after it)".format(path))
    return input_shape(path)


def _scan_segment(kind, characters):
    """None, ("input", path) or ("bad", reason) for one rendered line of a unit. `characters` is
    [(character, kind)] with kind "text", "code" or "html"; html characters are invisible to a reader and
    take no part in the fold, but one in or before the label, or in the path, is bad."""
    folded = []   # (folded character, index into `characters`, kind) for text and code characters
    html_positions = []
    for index, (character, piece_kind) in enumerate(characters):
        if piece_kind == "html":
            html_positions.append(index)
        elif piece_kind == "code":
            folded.append((character, index, "code"))
        else:
            if unicodedata.category(character) in IGNORED_CATEGORIES:
                continue
            for mapped in unicodedata.normalize("NFKC", character):
                folded.append((CONFUSABLES.get(mapped, mapped), index, "text"))
    folded_text = "".join(entry[0] for entry in folded)
    label_length = len(INPUT_LABEL)
    if unit_declares(kind) and folded_text.startswith(INPUT_LABEL) \
            and all(entry[2] == "text" for entry in folded[:label_length]):
        label_end = folded[label_length - 1][1]
        if not any(position <= label_end for position in html_positions):
            path, reason = _declaration_tail(characters[label_end + 1:])
            return ("bad", reason) if reason is not None else ("input", path)
    start = _lead_skip(folded_text)
    near = NEAR_INPUT_RE.match(folded_text, start)
    if near and any(entry[2] == "text" for entry in folded[start:near.end()]):
        return "bad", LOOK_ALIKE_REASON
    if kind == "cell" and folded_text.strip() in CELL_TOKENS \
            and any(entry[2] == "text" for entry in folded):
        return "bad", LOOK_ALIKE_REASON
    return None


def _segments(classes, element):
    """The rendered lines of a unit's inline children: a list of [(character, kind)] lists, one per
    rendered line (LineBreak splits; each split matches one source newline, so a segment's line number is
    the unit's start line plus its index). A code span's text is literal (kind "code" while
    CODE_SPANS_ARE_CODE); inline HTML is kind "html"; raw text has character references applied with
    html.unescape, exactly as the vendored renderer applies them; a backslash escape's Literal is its
    unescaped character; emphasis, strong emphasis, strikethrough, links, images and autolinks contribute
    their rendered children."""
    lines = [[]]

    def walk(children):
        for child in children:
            if isinstance(child, classes["LineBreak"]):
                lines.append([])
            elif isinstance(child, classes["CodeSpan"]):
                kind = "code" if CODE_SPANS_ARE_CODE else "text"
                lines[-1].extend((character, kind) for character in str(child.children))
            elif isinstance(child, classes["InlineHTML"]):
                lines[-1].extend((character, "html") for character in str(child.children))
            elif isinstance(child, classes["RawText"]):
                piece = str(child.children)
                if getattr(child, "escape", True):
                    piece = html.unescape(piece)
                lines[-1].extend((character, "text") for character in piece)
            elif isinstance(child, classes["Literal"]):
                lines[-1].extend((character, "text") for character in str(child.children))
            else:
                grand = getattr(child, "children", None)
                if isinstance(grand, str):
                    lines[-1].extend((character, "text") for character in grand)
                elif grand:
                    walk(grand)

    children = getattr(element, "children", None)
    if children and not isinstance(children, str):
        walk(children)
    return lines


def _units(classes, element, declaring, line_of):
    """(kind, start line, element) for every unit of the document, in order: paragraphs ("paragraph" in a
    declaring position, "task" for a task-list item, "quoted" elsewhere), headings, table cells, HTML
    blocks and, with the code rule patched out, code blocks. Quotes and alerts recurse with declaring off;
    lists keep it; an unknown block with element children recurses with it off (fail closed: its
    paragraphs are still scanned)."""
    for child in getattr(element, "children", []) or []:
        span = getattr(child, "source_span", None)
        line = line_of(span[0]) if span else None
        if isinstance(child, (classes["FencedCode"], classes["CodeBlock"])):
            if not CODE_BLOCKS_ARE_CODE:
                yield "code", line, child
        elif isinstance(child, classes["Paragraph"]):
            if hasattr(child, "checked"):
                kind = "task"
            else:
                kind = "paragraph" if declaring else "quoted"
            yield kind, line, child
        elif isinstance(child, (classes["Heading"], classes["SetextHeading"])):
            yield "heading", line, child
        elif isinstance(child, classes["Table"]):
            for row in child.children:
                row_span = getattr(row, "source_span", None)
                row_line = line_of(row_span[0]) if row_span else line
                for cell in row.children:
                    cell_span = getattr(cell, "source_span", None)
                    yield "cell", line_of(cell_span[0]) if cell_span else row_line, cell
        elif isinstance(child, classes["HTMLBlock"]):
            yield "html", line, child
        elif isinstance(child, classes["Quote"]):
            yield from _units(classes, child, False, line_of)
        elif isinstance(child, (classes["List"], classes["ListItem"])):
            yield from _units(classes, child, declaring, line_of)
        elif isinstance(child, (classes["BlankLine"], classes["ThematicBreak"], classes["LinkRefDef"])):
            continue
        else:
            yield from _units(classes, child, False, line_of)


def _classes():
    """The parsed-element classes of the verified vendored load, by name."""
    marko, gfm_elements = _load_marko()
    return {
        "FencedCode": marko.block.FencedCode, "CodeBlock": marko.block.CodeBlock,
        "Paragraph": marko.block.Paragraph, "Heading": marko.block.Heading,
        "SetextHeading": marko.block.SetextHeading, "Quote": marko.block.Quote,
        "List": marko.block.List, "ListItem": marko.block.ListItem,
        "BlankLine": marko.block.BlankLine, "ThematicBreak": marko.block.ThematicBreak,
        "LinkRefDef": marko.block.LinkRefDef, "HTMLBlock": marko.block.HTMLBlock,
        "Table": gfm_elements.Table,
        "LineBreak": marko.inline.LineBreak, "CodeSpan": marko.inline.CodeSpan,
        "RawText": marko.inline.RawText, "Literal": marko.inline.Literal,
        "InlineHTML": marko.inline.InlineHTML,
    }


def _code_regions(classes, element):
    """The [start, end) spans of the document's code: fenced and indented code blocks (their whole spans,
    marker lines included) and code spans, each honoured only while its rule says it is code."""
    regions = []

    def walk(node):
        for child in getattr(node, "children", []) or []:
            if isinstance(child, str):
                continue
            if isinstance(child, (classes["FencedCode"], classes["CodeBlock"])):
                if CODE_BLOCKS_ARE_CODE and getattr(child, "source_span", None):
                    regions.append(child.source_span)
                continue
            if isinstance(child, classes["CodeSpan"]):
                if CODE_SPANS_ARE_CODE and getattr(child, "source_span", None):
                    regions.append(child.source_span)
                continue
            walk(child)

    walk(element)
    return regions


def _line_starts(text):
    """The offset of each line's first character, line 1 first, computed once per text:
    bisect.bisect_right over the list is the one-based line number of any offset."""
    starts = [0]
    position = text.find("\n")
    while position != -1:
        starts.append(position + 1)
        position = text.find("\n", position + 1)
    return starts


def _lead_shape(line):
    """(markers, columns) of one line's lead: the count of its leading marker tokens (a quote mark; a
    bullet or an ordered mark of at most nine digits, each followed by whitespace or the line's end, as
    the parser requires) and of the whitespace columns between and before them (a tab counts four). The
    scan stops at the first character that is neither; it never reads the rest of the line."""
    markers = columns = position = 0
    length = len(line)
    while position < length:
        character = line[position]
        if character == " ":
            columns += 1
            position += 1
        elif character == "\t":
            columns += 4
            position += 1
        elif character == ">":
            markers += 1
            position += 1
        elif character in "-*+" and (position + 1 == length or line[position + 1] in " \t"):
            markers += 1
            position += 1
        elif character.isdigit():
            first = position
            while position < length and line[position].isdigit() and position - first < 9:
                position += 1
            if position < length and line[position] in ".)" and (
                    position + 1 == length or line[position + 1] in " \t"):
                markers += 1
                position += 1
            else:
                break
        else:
            break
    return markers, columns


# a line that must start a new block at the document's top level when it carries no lead markers and
# under two lead columns: an ATX heading (one to six #, then whitespace or the line's end, as the
# parser's Heading pattern requires) or a code fence (three or more backticks with no backtick in the
# info string, as FencedCode.match requires, or three or more tildes with any info string). Each breaks
# a paragraph, so it is never a lazy continuation, and under two columns it cannot continue any list
# item's content, whose indent is at least two
BLOCK_START_RE = re.compile(r" ?(?:#{1,6}(?=\s|$)|`{3,}[^`\n]*$|~{3,})")


def _nesting_refused(text):
    """(line number, reason) for the first line whose lead can take the document past MAX_NESTING_DEPTH
    nested levels, or whose opening takes the text's summed opening cost over MAX_NESTING_COST; None when
    neither. A conservative upper bound, never a parse: a line's marker tokens each open at most one
    level, and its leading whitespace continues at most the levels already opened (every two columns one
    level, and never more than any earlier line of the same block run opened), so a deeply indented fence
    body or continuation line alone opens nothing, while a run of markers on one line, or marker lines
    that march deeper line by line, is counted in full. The running depth follows block structure: it
    resets to zero where a new top-level block must start, at a non-blank line with no lead markers and
    under two lead columns that either follows a blank line (nothing open can read it as item content,
    which needs two columns, or as a lazy continuation, which needs an open paragraph) or opens an ATX
    heading or code fence (BLOCK_START_RE, which breaks a paragraph too), so a block's outcome never
    depends on content before its block; blank is what the parser's BlankLine reads as blank (str.strip,
    every whitespace character). Opening cost: a line whose depth exceeds the levels still open from the
    lines before it is charged two to the power of its depth, the growth the parser's opening time was
    measured to follow, and a blank line closes every open level for this charge, as it closes an open
    quote (for an open list it over-charges the next marker line, never under). Over-counting only
    refuses more (a fenced example shaped like a deeper-than-the-ceiling list is refused with the real
    thing), never less."""
    running = 0
    open_levels = 0
    cost = 0
    after_blank = True
    for number, line in enumerate(text.split("\n"), 1):
        if not line.strip():
            after_blank = True
            open_levels = 0
            continue
        markers, columns = _lead_shape(line)
        if NESTING_RESETS and markers == 0 and columns < 2 \
                and (after_blank or BLOCK_START_RE.match(line)):
            running = 0
        after_blank = False
        depth = min(running, columns // 2) + markers
        if depth > MAX_NESTING_DEPTH:
            return number, (
                "the line's leading list or quote markers and indentation open more than {} nested "
                "levels, deeper than this reading evaluates; flatten the nesting".format(
                    MAX_NESTING_DEPTH))
        if depth > running:
            running = depth
        if depth > open_levels:
            cost += 1 << depth
            if cost > MAX_NESTING_COST:
                return number, (
                    "the brief's nested list and quote openings, counted together, cost the parser more "
                    "than this reading evaluates (the summed opening cost is over {}); flatten the "
                    "nesting or open deep levels fewer times".format(MAX_NESTING_COST))
        open_levels = depth
    return None


def _whitespace_refused(text):
    """(line number, reason) for the first line holding a whitespace run of more than MAX_WHITESPACE_RUN
    characters of the parser's whitespace class, or the line whose run takes the text's summed squared
    run length over MAX_WHITESPACE_COST (the growth the parser's reading time was measured to follow);
    None when neither."""
    run = WHITESPACE_RUN_RE.search(text)
    if run is not None:
        return text.count("\n", 0, run.start()) + 1, (
            "the line holds a run of more than {} consecutive whitespace characters (space, tab or any "
            "other character the parser reads as whitespace), which the parser reads in time that grows "
            "with the square of the run; break the run".format(MAX_WHITESPACE_RUN))
    total = 0
    for match in WHITESPACE_COST_RE.finditer(text):
        length = match.end() - match.start()
        total += length * length
        if total > MAX_WHITESPACE_COST:
            return text.count("\n", 0, match.start()) + 1, (
                "the brief's whitespace runs, counted together, cost the parser more than this reading "
                "evaluates (the summed squared run length is over {}); shorten or break the runs".format(
                    MAX_WHITESPACE_COST))
    return None


def _inline_marks_refused(text):
    """(line number, reason) for the line that takes the summed squared count of inline marks (the
    INLINE_MARK_CHARACTERS: emphasis, strikethrough and code-span marks, whose delimiter pass the parser
    runs per paragraph in time that grows with the square of the marks) over MAX_INLINE_MARK_COST; None
    otherwise. Marks are counted per chunk of lines between blank lines (blank as the parser's BlankLine
    reads it), which covers every paragraph the chunk can hold; marks inside code are counted with the
    rest, over-counting only."""
    total = 0
    count = 0
    for number, line in enumerate(text.split("\n"), 1):
        if not line.strip():
            total += count * count
            count = 0
            continue
        marks = sum(line.count(character) for character in INLINE_MARK_CHARACTERS)
        if marks:
            count += marks
            if total + count * count > MAX_INLINE_MARK_COST:
                return number, (
                    "the brief's emphasis, strikethrough and code marks, counted together, cost the "
                    "parser more than this reading evaluates (the summed squared per-paragraph count is "
                    "over {}); split the marks across paragraphs or quote them in fewer runs".format(
                        MAX_INLINE_MARK_COST))
    return None


def forbidden(text):
    """(line number, reason) for the first line separator other than LF anywhere in `text`, for the first
    line a cost prescan refuses (_whitespace_refused for a run over MAX_WHITESPACE_RUN or a summed run
    cost over MAX_WHITESPACE_COST, _nesting_refused for a lead past MAX_NESTING_DEPTH or a summed opening
    cost over MAX_NESTING_COST, _inline_marks_refused for a summed mark cost over MAX_INLINE_MARK_COST),
    or for the first format character (category Cf) outside the parsed document's code blocks and code
    spans; None when there is none. The cost prescans are judged on every text whatever else it holds, so
    those refusals never depend on unrelated content. DeclarationError for a text over MAX_TEXT_BYTES
    (refused here up front, parse path or not) and when a Cf stands in text the vendored parser cannot
    read. The Cf region check is one sorted sweep: positions ascend, so one pointer over the sorted
    regions visits each region once."""
    if len(text.encode("utf-8")) > MAX_TEXT_BYTES:
        raise DeclarationError("the brief is larger than {} bytes, over this reading's ceiling".format(
            MAX_TEXT_BYTES))
    found = SEPARATOR_RE.search(text)
    if found:
        line = text.count("\n", 0, found.start()) + 1
        return line, "the brief holds a line separator other than LF ({}); only LF ends a line".format(
            SEPARATORS[found.group(0)])
    for prescan in (_whitespace_refused, _nesting_refused, _inline_marks_refused):
        refused = prescan(text)
        if refused is not None:
            return refused
    if text.isascii():   # ASCII holds no format character
        return None
    positions = [index for index, character in enumerate(text)
                 if unicodedata.category(character) == "Cf"]
    if not positions:
        return None
    regions = sorted(_code_regions(_classes(), _parse(text)))
    starts = _line_starts(text)
    pointer = 0
    for index in positions:
        while pointer < len(regions) and regions[pointer][1] <= index:
            pointer += 1
        if pointer < len(regions) and regions[pointer][0] <= index:
            continue
        return bisect.bisect_right(starts, index), (
            "the brief holds a format character (U+{:04X}) outside a code block or code span; format "
            "characters are invisible and are refused, never removed silently".format(ord(text[index])))
    return None


def read(text):
    """(inputs, bad): [(line number, path)] for the declarations of the parsed document, in order, and
    [(line number, reason)] for every malformed declaration or look-alike. Inside code nothing is read.
    The caller refuses forbidden() first, so `text` ends its lines with LF alone here. DeclarationError
    when the vendored parser cannot read the text."""
    if text.isascii() and "nput" not in text.lower() and RENDERING_MARK_RE.search(text) is None:
        # nothing in it can spell the token INPUT, even folded, and no character reference, markup,
        # escape or inline HTML can make the rendered text differ from this source text
        return [], []
    classes = _classes()
    document = _parse(text)
    starts = _line_starts(text)
    line_of = lambda position: bisect.bisect_right(starts, position)  # noqa: E731
    inputs, bad = [], []
    for kind, line, element in _units(classes, document, True, line_of):
        if kind in ("html", "code"):
            body = element.body if kind == "html" else "".join(
                str(raw.children) for raw in element.children)
            for offset, raw_line in enumerate(body.split("\n")):
                if _line_scan(raw_line, strip_tags=(kind == "html")):
                    bad.append(((line or 1) + offset, LOOK_ALIKE_REASON))
            continue
        for offset, characters in enumerate(_segments(classes, element)):
            found = _scan_segment(kind, characters)
            if found is None:
                continue
            at = (line or 1) + offset
            if found[0] == "input":
                inputs.append((at, found[1]))
            else:
                bad.append((at, found[1]))
    inputs.sort(key=lambda pair: pair[0])
    bad.sort(key=lambda pair: pair[0])
    return inputs, bad
