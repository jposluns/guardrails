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
provenance pin, a text over MAX_TEXT_BYTES, a parse that misses PARSE_DEADLINE_SECONDS, a parse child that
exits non-zero or returns a result not of the serialized document's shape, or a document span that does
not cover the text is DeclarationError, which both callers turn into their cannot-evaluate refusal, never
a silent empty reading or another exception.

BOUNDS. The one hard bound is wall clock, held by construction: the parse runs in its own child process
(python3 -I -B on this module's sibling child script tools/_input_declarations_child.py, an empty
environment, the text on stdin, one JSON result on stdout) in its own session, and a parse that has not
delivered its result within PARSE_DEADLINE_SECONDS has its whole process group killed and the text
refused, never read partially; the group is killed after a normal exit too, so nothing the child started
outlives the call, and a killed, crashed, signalled, non-zero or silent child is the same fail-closed
refusal. No in-process deadline can hold that bound, because a single regex match holds the GIL and the
re engine reads some texts inside one
match for minutes (an unclosed inline-HTML opener scans to its paragraph's end per opener; a run of
fullwidth letters backtracks quadratically in the GFM autolink lookbehind), where a thread join waits out
the match however short its timeout; and per-shape cost models over the text cannot enumerate every slow
shape, so no such model is relied on for the bound. Before any parse, two cheap structural prescans
refuse degenerate shapes up front with a named reason, each decided on the text alone: a text over
MAX_TEXT_BYTES (the dispatcher's own brief cap, so every brief that can be dispatched is read; it bounds
one parse's memory, not its time, and the constant's comment records the timings measured at it) is
refused in forbidden() whatever it holds; a text that can nest blocks more than MAX_NESTING_DEPTH levels
deep (judged by _nesting_refused, a conservative prescan over marker runs and indentation whose running depth
follows block structure, resetting where a new top-level block must start, so a block's outcome never
depends on content before its block; after a quote marker it consumes one optional non-LF whitespace
character as a column, exactly as the parser's own quote prefix does, so a no-break space or a
vertical tab after > hides no level; and it skips a fenced code block's content, which opens nothing,
placing each fence where the parser's FencedCode opens and closes it and skipping nothing from the first
fence-shaped line it cannot place) is refused up front too; and so is a line holding a run of more than
MAX_WHITESPACE_RUN consecutive whitespace characters (the class the parser itself reads as whitespace,
vertical tab and no-break space included, never only space and tab). Both prescans run on every text, so
neither refusal depends on whether the rest of the brief forces a parse. The earlier additive total-cost
prescans (summed squared whitespace-run lengths, summed deep marker openings, summed squared per-chunk
inline-mark counts) are gone: their only purpose was to keep the in-process parse under a deadline the
process could not otherwise enforce, and the child kill now holds that bound for every shape, counted or
not. Named residuals, each availability only, never a wrong verdict: near the deadline, whether a slow
in-bound text is answered or refused still depends on host load, so a text the ceiling admits can be
refused on a busy host (the bound is deterministic, the answer is not); every parse pays one child start
(an interpreter and the vendored-parser import); and a caller that ignores SIGCHLD reads a failed
child's status as 0, so a non-zero status is refused but a status of 0 is never trusted alone: the
result must also be one complete well-formed JSON document of the serialized document's shape
(_checked_tree, at most MAX_TREE_DEPTH levels deep). A brief that is pure ASCII, holds no "nput" substring in any case and none of the characters that
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

This module opens no brief path itself and runs nothing beyond the vendored parser and the parse child
that hosts it (this interpreter, python3 -I -B, on the sibling child script); the filesystem accesses are
importing that parser and starting that child. Callers: check_brief_capability (decode_brief refuses what forbidden()
finds, PreflightText reads read()), check_verifier_delivery (_declared_inputs refuses both and takes
read()'s declarations); both call forbidden() first, so read() may assume LF-only line ends.
"""
import bisect
import html
import json
import os
import re
import signal
import subprocess
import sys
import threading
import unicodedata

MARKO_VERSION = "2.2.4"           # the exact vendored pin; any other resolved version fails closed
# the brief ceiling, enforced on every text before any other rule: the dispatcher's own brief cap, 102,400
# bytes (the dispatcher refuses a larger brief before any worker reads it), so every brief that can be
# dispatched is read here and none is refused for its size alone; real briefs run past 64 KiB (the
# largest measured is 92,342 bytes). The ceiling no longer bounds the parse's time, which the child kill
# holds for every shape at the deadline; it bounds only the memory one parse may take. Measured at this
# ceiling on the host this reading runs on (an 80-cpu machine at one-minute load averages 37 to 50, each
# text read once through read() and timed from the parent around the call, 2026-10-31): real briefs of
# 65,766 to 79,833 bytes 0.23 to 0.50 s; brief-like shapes: prose 0.42 s, headings 0.58 s, one-line
# paragraphs 0.98 s, flat bullets 1.6 s, setext pairs 2.0 s, an 8-column table 2.5 s, a 2,000-column
# table 3.3 s, backtick runs 0.89 s, asterisk marks 0.39 s, lazy continuations under a 20-deep quote
# 2.4 s and under a 20-deep bullet lead 3.4 s. Degenerate walls no real brief holds take most of the
# deadline or all of it: no-break-space runs 4.5 s, 2,000-space runs 7.2 s, blank-separated 13-deep quote
# openings 7.9 s, unclosed CDATA openers 8.1 s, a fullwidth-letter line 8.5 s and 13-deep bullet walls
# 10.2 s were answered, and unclosed <!-- and <? openers, an underscore-mark run and a
# link-reference-definition wall were refused at the deadline (10.0 s, the child killed). So no shape
# outlasts the deadline, every brief-like shape keeps over half of it as margin, and whether a degenerate
# wall near the deadline is answered or refused depends on host load
MAX_TEXT_BYTES = 102400
# the parse must deliver its result within this wall-clock bound or the parse child's process group is
# killed and the text refused (DeclarationError), the fail-closed refusal an oversized text gets; the
# bound holds by construction for every shape, because the kill needs no cooperation from the parser,
# where an in-process join cannot return while one regex match holds the GIL
PARSE_DEADLINE_SECONDS = 10.0
# the nesting ceiling, judged by the prescan in forbidden() before any parse: the parser opens a run of
# nested markers on one line in time that grows about twofold per level (measured on the vendored pin:
# about 0.06 s at 20, about 4 s at 26), so past a few levels more a single opening would burn the whole
# deadline before the kill refuses it; the prescan refuses the shape up front, instantly and with a
# reason that names the lead, where the kill could only refuse it at the deadline. A real brief nests
# far shallower. This prescan is kept for that cheap up-front refusal; it is not what holds the
# wall-clock bound (the child kill is)
MAX_NESTING_DEPTH = 20
# the block-structure resets of _nesting_refused, a patchable seam for its callers' self-tests
NESTING_RESETS = True
# the fenced-code skip of _nesting_refused, a patchable seam for its callers' self-tests
NESTING_FENCES = True
# the open-label reading of _nesting_refused (a link label kept open across empty lines until its "]"), a
# patchable seam for its callers' self-tests
NESTING_LABELS = True
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
# length inside single regex matches (measured on the vendored pin: 93.5 s at a 64 KiB space run, 52 s
# at a 64 KiB vertical-tab run; about 0.1 s at this bound for space, vertical tab and no-break space
# alike); the prescan refuses such a run up front, instantly and with a reason that names the line,
# where the child kill could only refuse it at the deadline. A real brief's longest run (alignment
# padding, indented code) is far shorter. Kept for that cheap up-front refusal; it is not what holds
# the wall-clock bound (the child kill is)
MAX_WHITESPACE_RUN = 2000
WHITESPACE_RUN_RE = re.compile(r"[^\S\n]{%d}" % (MAX_WHITESPACE_RUN + 1))
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
# the parse child, a fixed path derived from THIS file's own absolute location (never the CWD): it reads
# the text from stdin, parses it in its own process and session, and writes one JSON document to stdout
_CHILD_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_input_declarations_child.py")
# the parse child's whole environment: empty, since the parse reads nothing from it (the interpreter runs
# -I, which ignores every PYTHON* variable, the text arrives on stdin as bytes the child decodes as UTF-8,
# and the result is ASCII JSON), so no variable of the caller's, a credential included, reaches the child.
# A patchable seam for the callers' self-tests only: None hands the child the caller's whole environment
CHILD_ENVIRONMENT = {}
# the failures decoding the child's stdout that are this reading's refusal: bytes that are not UTF-8 or
# not one JSON document (ValueError), and JSON nested past the decoder's own stack bound (RecursionError)
CHILD_RESULT_ERRORS = (ValueError, RecursionError)
# the deepest serialized document read (the root is level 1): the readings below walk the tree
# recursively, one frame per level, so a deeper tree would end in RecursionError there. The vendored
# parser's own recursion bound refuses deeper nesting first: the deepest document it returned measured
# 498 levels (runs of nested emphasis, strong emphasis, images and links in emphasis, 2026-10-31), and
# each of those shapes nested further failed in the child as a recursion error, its refusal; this bound
# sits above that with room, and below what the recursive readings can walk under their callers' frames
MAX_TREE_DEPTH = 600
# the fields a serialized node may carry (_serialize) and the one type each must have
NODE_FIELDS = {"kind": str, "span": list, "text": str, "children": list, "task": bool, "body": str,
               "escape": bool}
# a patchable seam for the callers' self-tests only: False restores the abandoned in-process thread join,
# whose timeout cannot fire while one regex match holds the GIL, so a self-test can show the child kill
# is what holds the wall-clock bound; production callers never set it
PARSE_IN_CHILD = True


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


def _parse_document(text):
    """The marko Document for `text`, parsed IN THIS PROCESS with no bound of its own: a FRESH
    extension-free Parser per call with the vendored GFM elements registered (never marko's Markdown
    object, which is documented not thread-safe). Only the parse child and the in-process seam below run
    it; every caller of this module reaches it through _parse, which holds the wall-clock bound."""
    marko, gfm_elements = _load_marko()
    parser = marko.parser.Parser()
    for name in GFM_ELEMENT_NAMES:
        parser.add_element(getattr(gfm_elements, name))
    return parser.parse(text)


def _serialize(element):
    """The parsed element as a tree of plain dicts, the one shape both sides of the process boundary
    read: kind (the element's class name), span (its source span), text (its string children), children
    (its element children, a bare string child as kind "str"; written whenever the element has a child
    sequence, an empty one included, so a document always carries it), task (it carries a task-list check
    state), escape (a RawText's character-reference switch) and body (an HTML block's raw text). Every reading
    this module makes of a parsed document is made of these fields alone, so the in-process seam and the
    child return identical documents by construction."""
    node = {"kind": type(element).__name__}
    span = getattr(element, "source_span", None)
    if isinstance(span, tuple) and len(span) == 2:
        node["span"] = [span[0], span[1]]
    if hasattr(element, "checked"):
        node["task"] = True
    body = getattr(element, "body", None)
    if isinstance(body, str):
        node["body"] = body
    if node["kind"] == "RawText":
        node["escape"] = bool(getattr(element, "escape", True))
    children = getattr(element, "children", None)
    if isinstance(children, str):
        node["text"] = children
    elif children is not None:
        node["children"] = [{"kind": "str", "text": child} if isinstance(child, str)
                            else _serialize(child) for child in children]
    return node


def _checked_tree(tree):
    """`tree` when it has the serialized document's shape (_serialize), else DeclarationError: a dict of
    kind Document with a children list, and every node below it a dict whose kind is a string and whose
    other fields are only NODE_FIELDS of their types, at most MAX_TREE_DEPTH levels deep. The walk is
    iterative, so a tree of any depth is judged without recursing; a node with no kind, a number in a
    node's place, a document with no children list and a tree too deep for the recursive readings below
    are each this refusal, never another exception or an empty reading."""
    if not isinstance(tree, dict) or tree.get("kind") != "Document" \
            or not isinstance(tree.get("children"), list):
        raise DeclarationError("the parsed result is not a document with a children list; the brief is "
                               "refused, never read partially")
    pending = [(tree, 1)]
    while pending:
        node, depth = pending.pop()
        if depth > MAX_TREE_DEPTH:
            raise DeclarationError("the parsed document is nested more than {} levels deep, deeper than "
                                   "this reading evaluates; the brief is refused".format(MAX_TREE_DEPTH))
        if not isinstance(node, dict) or not isinstance(node.get("kind"), str):
            raise DeclarationError("the parsed document holds a node with no kind; the brief is refused, "
                                   "never read partially")
        for name, value in node.items():
            if name not in NODE_FIELDS or type(value) is not NODE_FIELDS[name]:
                raise DeclarationError("the parsed document holds a {} node with a malformed {!r} field; "
                                       "the brief is refused, never read partially".format(
                                           node["kind"], name))
        if "span" in node and (len(node["span"]) != 2 or any(type(end) is not int for end in node["span"])):
            raise DeclarationError("the parsed document holds a {} node with a malformed span; the brief "
                                   "is refused, never read partially".format(node["kind"]))
        pending.extend((child, depth + 1) for child in node.get("children", ()))
    return tree


def _kill_group(process):
    """Kill the parse child's whole process group (it was started with start_new_session, so its group id
    is its pid and holds only processes it started), then the child itself where the group is already
    gone; never a pid this module did not start. After the child is reaped, its group id stays reserved
    while any member of the group lives, so the kill reaches only what the child left behind; an empty
    group's id is free again, and the kill then finds no group (barring a reuse of that id in the moment
    between the reap and the kill)."""
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except OSError:
        try:
            process.kill()
        except OSError:
            pass


def _check_exit(status):
    """DeclarationError for a parse child's non-zero exit status, a negative one being the signal that
    ended it: a child that failed is refused even when it wrote a complete well-formed document first."""
    if status != 0:
        raise DeclarationError("the parse child exited with status {} (a negative status is the signal "
                               "that ended it); the brief is refused, never read partially".format(status))


def _child_parse(text):
    """The serialized document from a parse of `text` in a child process (python3 -I -B _CHILD_PATH, its
    own session, CHILD_ENVIRONMENT as its whole environment, the text on stdin as UTF-8, one JSON document
    on stdout). The wall-clock bound holds by construction: a child that has not delivered within
    PARSE_DEADLINE_SECONDS has its process group killed and the text is refused. The group is killed on
    every other path too, a normal exit included, so nothing the child started outlives the call. A child
    that cannot be started, dies, is killed, exits with a non-zero status or a signal, or writes anything
    but one well-formed JSON document is the same refusal, never a partial read; an exit status of 0 is
    never trusted alone (a caller that ignores SIGCHLD reads a failed child's status as 0), so the result
    must be complete as well."""
    try:
        process = subprocess.Popen([sys.executable, "-I", "-B", _CHILD_PATH],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL, env=CHILD_ENVIRONMENT,
                                   start_new_session=True)
    except OSError as exc:
        raise DeclarationError("cannot start the parse child {}: {}".format(_CHILD_PATH, exc)) from exc
    try:
        stdout, _ = process.communicate(text.encode("utf-8"), timeout=PARSE_DEADLINE_SECONDS)
    except subprocess.TimeoutExpired:
        _kill_group(process)
        process.communicate()   # reap the killed child and close both pipe ends
        raise DeclarationError("the CommonMark parser did not finish over the brief within {} s, this "
                               "reading's deadline; the brief is refused, never read partially".format(
                                   PARSE_DEADLINE_SECONDS))
    except BaseException:       # whatever interrupted the wait, the child never outlives the call
        _kill_group(process)
        process.communicate()
        raise
    # the child exited and is reaped, but a process it started and left running is still in its group
    _kill_group(process)
    _check_exit(process.returncode)
    try:
        result = json.loads(stdout.decode("utf-8"))
    except CHILD_RESULT_ERRORS as exc:
        raise DeclarationError("the parse child returned no readable result over the brief ({}); the "
                               "brief is refused, never read partially".format(
                                   type(exc).__name__)) from exc
    if not isinstance(result, dict):
        raise DeclarationError("the parse child returned a malformed result over the brief; the brief "
                               "is refused, never read partially")
    if "declaration_error" in result:
        raise DeclarationError(str(result["declaration_error"]))
    if "error" in result or "tree" not in result:
        raise DeclarationError("the CommonMark parser failed over the brief: {}".format(
            result.get("error", "the child returned no document")))
    return result["tree"]


def _joined_parse(text):
    """The serialized document from the in-process parse behind a daemon-thread join: the seam
    PARSE_IN_CHILD=False restores it so a self-test can show that this join cannot hold the deadline (a
    single regex match holds the GIL, so join() waits out the match however short its timeout) where the
    child kill can; no production caller takes this path."""
    outcome = []

    def parse_text():
        try:
            outcome.append(("document", _serialize(_parse_document(text))))
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
    return value


def _parse(text):
    """The serialized document (_serialize) for `text`, bounded: the byte ceiling and the structural
    prescans are enforced first (kept here as well as in forbidden(), so no caller can reach an unbounded
    parse), then the parse runs in its own killed-at-the-deadline child process (_child_parse; the seam
    PARSE_IN_CHILD=False restores the old in-process thread join for the self-tests that show the join
    cannot hold the bound). A parse that misses the deadline, a parse exception (a recursion error
    included), a child that dies or returns garbage, a document not of the serialized shape
    (_checked_tree), and a document span that does not cover `text` (the parser's buffer rewrites are
    length-preserving only over LF-only text, which forbidden() guarantees first) are DeclarationError,
    fail closed, never a partial read."""
    if len(text.encode("utf-8")) > MAX_TEXT_BYTES:
        raise DeclarationError("the brief is larger than {} bytes, over this reading's ceiling".format(
            MAX_TEXT_BYTES))
    for prescan in (_whitespace_refused, _nesting_refused):
        found = prescan(text)
        if found is not None:
            raise DeclarationError("the brief is over this reading's bounds: " + found[1])
    document = _checked_tree(_child_parse(text) if PARSE_IN_CHILD else _joined_parse(text))
    if document.get("span") != [0, len(text)]:
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


def _segments(element):
    """The rendered lines of a serialized unit's inline children: a list of [(character, kind)] lists,
    one per rendered line (LineBreak splits; each split matches one source newline, so a segment's line
    number is the unit's start line plus its index). A code span's text is literal (kind "code" while
    CODE_SPANS_ARE_CODE); inline HTML is kind "html"; raw text has character references applied with
    html.unescape, exactly as the vendored renderer applies them; a backslash escape's Literal is its
    unescaped character; emphasis, strong emphasis, strikethrough, links, images and autolinks contribute
    their rendered children."""
    lines = [[]]

    def walk(children):
        for child in children:
            kind = child["kind"]
            if kind == "LineBreak":
                lines.append([])
            elif kind == "CodeSpan":
                piece_kind = "code" if CODE_SPANS_ARE_CODE else "text"
                lines[-1].extend((character, piece_kind) for character in child.get("text", ""))
            elif kind == "InlineHTML":
                lines[-1].extend((character, "html") for character in child.get("text", ""))
            elif kind == "RawText":
                piece = child.get("text", "")
                if child.get("escape", True):
                    piece = html.unescape(piece)
                lines[-1].extend((character, "text") for character in piece)
            elif kind == "Literal":
                lines[-1].extend((character, "text") for character in child.get("text", ""))
            elif kind == "str":   # a bare string child of a list of elements renders nothing
                continue
            elif "text" in child:
                lines[-1].extend((character, "text") for character in child["text"])
            elif child.get("children"):
                walk(child["children"])

    if element.get("children"):
        walk(element["children"])
    return lines


def _units(element, declaring, line_of):
    """(kind, start line, serialized element) for every unit of the document, in order: paragraphs
    ("paragraph" in a declaring position, "task" for a task-list item, "quoted" elsewhere), headings,
    table cells, HTML blocks and, with the code rule patched out, code blocks. Quotes, alerts and every
    other unknown block recurse with declaring off (fail closed: their paragraphs are still scanned);
    lists keep it."""
    for child in element.get("children") or []:
        span = child.get("span")
        line = line_of(span[0]) if span else None
        kind = child["kind"]
        if kind in ("FencedCode", "CodeBlock"):
            if not CODE_BLOCKS_ARE_CODE:
                yield "code", line, child
        elif kind == "Paragraph":
            if child.get("task"):
                unit = "task"
            else:
                unit = "paragraph" if declaring else "quoted"
            yield unit, line, child
        elif kind in ("Heading", "SetextHeading"):
            yield "heading", line, child
        elif kind == "Table":
            for row in child.get("children") or []:
                row_span = row.get("span")
                row_line = line_of(row_span[0]) if row_span else line
                for cell in row.get("children") or []:
                    cell_span = cell.get("span")
                    yield "cell", line_of(cell_span[0]) if cell_span else row_line, cell
        elif kind == "HTMLBlock":
            yield "html", line, child
        elif kind in ("List", "ListItem"):
            yield from _units(child, declaring, line_of)
        elif kind in ("BlankLine", "ThematicBreak", "LinkRefDef"):
            continue
        else:   # Quote, Alert and every block this reading does not name: never a declaring position
            yield from _units(child, False, line_of)


def _code_regions(element):
    """The [start, end) spans of the serialized document's code: fenced and indented code blocks (their
    whole spans, marker lines included) and code spans, each honoured only while its rule says it is
    code."""
    regions = []

    def walk(node):
        for child in node.get("children") or []:
            kind = child["kind"]
            if kind in ("FencedCode", "CodeBlock"):
                if CODE_BLOCKS_ARE_CODE and child.get("span"):
                    regions.append(tuple(child["span"]))
                continue
            if kind == "CodeSpan":
                if CODE_SPANS_ARE_CODE and child.get("span"):
                    regions.append(tuple(child["span"]))
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
    the parser requires) and of the whitespace columns between and before them (a tab counts four; after
    a quote mark, one character of any whitespace but LF counts one column, exactly the one optional
    whitespace character the parser's own quote prefix consumes there). The scan stops at the first
    character that is neither, the third value (its position); it never reads the rest of the line."""
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
            # the parser's quote prefix (block.py, " {,3}>[^\n\S]?") consumes ONE optional whitespace
            # character after the marker, any whitespace but LF, so a no-break space, a vertical tab or
            # an ideographic space there is the column a space would be, never the end of the lead; a
            # space or tab is left to the loop, which already counts it
            if position < length and line[position] not in " \t" and line[position] != "\n" \
                    and line[position].isspace():
                columns += 1
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
                position = first   # digits that open no marker are the line's text, not its lead
                break
        else:
            break
    return markers, columns, position


# a line that must start a new block at the document's top level when it carries no lead markers and
# under two lead columns: an ATX heading (one to six #, then whitespace or the line's end, as the
# parser's Heading pattern requires) or a code fence (three or more backticks with no backtick in the
# info string, as FencedCode.match requires, or three or more tildes with any info string). Each breaks
# a paragraph, so it is never a lazy continuation, and under two columns it cannot continue any list
# item's content, whose indent is at least two
BLOCK_START_RE = re.compile(r" ?(?:#{1,6}(?=\s|$)|`{3,}[^`\n]*$|~{3,})")
# a line whose lead (_lead_shape) is followed by three backticks or three tildes is shaped like a fence:
# the prescan places it exactly as the parser reads it, or skips no fenced line from there on
FENCE_SHAPE_RE = re.compile(r"`{3}|~{3}")
# a fence's opening run as FencedCode.pattern and FencedCode.match read it after the container prefix:
# three or more backticks with no backtick in the rest of the line, or three or more tildes with any rest
FENCE_OPEN_RE = re.compile(r"(`{3,})[^`]*$|(~{3,})")
# a line that can close an open fence (FencedCode.parse): its leading whitespace, a run of one fence
# character, then only whitespace; it closes when its run is the opening character at least as long and
# its leading spaces fit the container
FENCE_CLOSE_RE = re.compile(r"([ \t]*)(`+|~+)[^\S\n]*$")
# a list marker and the spaces after it, read one marker at a time across a fence line's lead
LIST_MARK_RE = re.compile(r"(\d{1,9}[.)]|[-*+])( +)")
# a lead that may open an HTML block the parser reads on to an end marker, across blank lines and fence
# lines alike (HTMLBlock.match, its types 1 to 4 in its own order); the value builds the end marker
HTML_END_STARTS = ((re.compile(r"<(script|pre|style|textarea)(?:[>\s]|$)", re.I),
                    lambda found: re.compile(r"(?i)</{}>".format(found.group(1).lower()))),
                   (re.compile(r"<!--"), lambda found: re.compile(r"-->")),
                   (re.compile(r"<\?"), lambda found: re.compile(r"\?>")),
                   (re.compile(r"<!"), lambda found: re.compile(r">")))
# where a link label ends on one line, as LinkRefDef.match reads it (inline_parser._parse_link_label,
# find_next(text, "]", start + 1, disallowed="[")): the first "]" or "[" not escaped by a backslash. The
# search runs over the raw buffer with no line or empty-line limit, so a label with neither on its lines
# stays open; a backslash ending a line escapes the LF, so no escape carries into the next line
LABEL_END_RE = re.compile(r"(?:[^\\\[\]]|\\.)*([\[\]])")


def _fence_opening(line, position, markers, running, after_blank):
    """How the parser reads a line shaped like a fence (FENCE_SHAPE_RE at `position`, the end of its lead)
    while no fence is open: (mode, character, run length, column) for a fence it surely opens, None for
    a line it surely reads as no fence, and False where this prescan cannot place the line. The modes:
    "top", a fence at the document's top level (a lead of no markers and under two columns, which no list
    item or quote can hold, or under four columns where no level is open); "item", a fence opened on a
    list marker line, whose item's content column is exactly the fence's column; "range", a fence on a
    line of two or three columns under an open level, in a list item whose content column is at most its
    own or at the top level. A quote marker or a tab in the lead, a marker followed by five or more
    spaces, a lead of four or more columns under an open level, and an ordered marker other than 1 right
    after a non-blank line (a paragraph's continuation, never an item) are not placed."""
    lead = line[:position]
    run = FENCE_OPEN_RE.match(line, position)
    if run is None:   # a backtick run with a backtick after it opens no fence anywhere
        return None
    opened = run.group(1) or run.group(2)
    fence = (opened[0], len(opened), position)
    if "\t" in lead or ">" in lead:
        return False
    first = len(lead) - len(lead.lstrip(" "))
    if markers == 0:
        if first <= 1 or (running == 0 and first <= 3):
            return ("top",) + fence
        if running == 0:   # four or more columns at the top level: an indented code line or paragraph text
            return None
        return ("range",) + fence if first <= 3 else False
    if first > 3:
        return False if running else None
    at = first
    while at < position:
        mark = LIST_MARK_RE.match(lead, at)
        if mark is None or len(mark.group(2)) > 4:
            return False
        if at == first and mark.group(1)[:-1].isdigit() and int(mark.group(1)[:-1]) != 1 \
                and not after_blank:
            return False
        at = mark.end()
    return ("item",) + fence


def _fence_holds(fence, line):
    """How the parser reads `line` inside the open `fence` (_fence_opening): "content", "closed" (the line
    is the closing fence), "exit" (the line ends the fence's list item, and the fence with it, and is read
    outside any fence), or False where this prescan cannot place the line. A top-level fence holds every
    line up to a closing run indented by at most three spaces, to the text's end when none comes; an item
    fence holds the lines indented at least to its column and every line of spaces alone (the parser's
    prefix test pads a blank line), and closes on a run indented zero to three columns past it; a range
    fence holds what both of its readings hold and is unplaced where they differ."""
    mode, character, length, column = fence
    close = FENCE_CLOSE_RE.match(line)
    closes = close is not None and close.group(2)[0] == character and len(close.group(2)) >= length
    lead = close.group(1) if closes else ""
    if mode == "top":
        return "closed" if closes and "\t" not in lead and len(lead) <= 3 else "content"
    expanded = line.expandtabs(4)
    held = len(expanded) - len(expanded.lstrip(" ")) >= column or not expanded.strip(" ")
    if mode == "item":
        if closes:
            if "\t" in lead:
                return False
            if column <= len(lead) <= column + 3:
                return "closed"
        return "content" if held else "exit"
    if closes:
        if lead == " " * column:
            return "closed"
        if "\t" in lead or len(lead) <= column + 3:
            return False
    return "content" if held else False


def _opaque_start(rest):
    """The block the parser may read on from a line whose text after its lead is `rest`, where a line
    shaped like a fence is not a fence: an end-marker pattern for an HTML block of types 1 to 4
    (HTML_END_STARTS), "html" for any other < (an HTML block that ends at a blank line), and for [ (a
    link reference definition) "label" while its label is still open at the line's end (LABEL_END_RE),
    "link" once its "]" has appeared (the destination and title, read on to an empty line), or None where
    a "[" comes first (no definition: the parser's label search fails); None otherwise."""
    if rest.startswith("<"):
        for start, end in HTML_END_STARTS:
            found = start.match(rest)
            if found:
                return end(found)
        return "html"
    if not rest.startswith("["):
        return None
    found = LABEL_END_RE.match(rest, 1)
    if found is None:
        return "label"
    return "link" if found.group(1) == "]" else None


def _nesting_refused(text):
    """(line number, reason) for the first line whose lead can take the document past MAX_NESTING_DEPTH
    nested levels; None otherwise. A conservative upper bound, never a parse: a line's marker tokens each
    open at most one level, and its leading whitespace continues at most the levels already opened (every
    two columns one level, and never more than any earlier line of the same block run opened), so a
    deeply indented fence body or continuation line alone opens nothing, while a run of markers on one
    line, or marker lines that march deeper line by line, is counted in full. The running depth follows
    block structure: it resets to zero where a new top-level block must start, at a non-blank line with
    no lead markers and under two lead columns that either follows a blank line (nothing open can read it
    as item content, which needs two columns, or as a lazy continuation, which needs an open paragraph)
    or opens an ATX heading or code fence (BLOCK_START_RE, which breaks a paragraph too), so a block's
    outcome never depends on content before its block; blank is what the parser's BlankLine reads as
    blank (str.strip, every whitespace character). A fenced code block's content lines are skipped, since
    the parser reads them as code that opens nothing: a fence opens and closes where the parser's
    FencedCode does (_fence_opening, _fence_holds: backtick and tilde runs of three or more, indented zero
    to three columns in their container, an unclosed fence running to its container's end), at the top
    level and in a list item. From the first fence-shaped line it cannot place exactly (a fence in a
    quote, a tab in a fence line's lead, a fence-shaped line where an HTML block or link reference
    definition may be open, which the parser reads on as that block, a link label across any number of
    empty lines until its "]") it skips no line, so a misplaced
    fence can only count more. Over-counting only refuses more, never less."""
    running = 0
    after_blank = True
    placed = NESTING_FENCES   # False from the first fence-shaped line the prescan cannot place
    fence = None              # the open fence (_fence_opening), whose content lines are skipped
    # the blocks that may be open across a fence-shaped line (_opaque_start): a set, so it holds at most
    # nine kinds however many lines open one, and each line's update stays constant time
    opaque = set()
    for number, line in enumerate(text.split("\n"), 1):
        closing = False
        if placed and fence is not None:
            held = _fence_holds(fence, line)
            if held == "content":
                continue
            fence = None
            placed = held is not False
            closing = held == "closed"
        if not line.strip():
            after_blank = True
            # an open label survives any blank line (its "]" search has no line limit); a title survives
            # a line of whitespace alone, since the parser refuses only a title holding "\n\n"
            opaque = {kind for kind in opaque if not isinstance(kind, str)
                      or (kind == "label" and NESTING_LABELS) or (kind in ("label", "link") and line)}
            continue
        markers, columns, position = _lead_shape(line)
        if placed and not closing:
            if FENCE_SHAPE_RE.match(line, position):
                fence = False if opaque else _fence_opening(line, position, markers, running, after_blank)
                placed = fence is not False
                fence = fence or None
            else:
                opaque = {kind for kind in opaque if isinstance(kind, str) or not kind.search(line)}
                if "label" in opaque:
                    ended = LABEL_END_RE.match(line)
                    if ended is not None:   # the label closes ("]") or its search fails ("[")
                        opaque.discard("label")
                        if ended.group(1) == "]":
                            opaque.add("link")
                started = _opaque_start(line[position:])
                if started is not None and (isinstance(started, str) or not started.search(line)):
                    opaque.add(started)
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
    return None


def _whitespace_refused(text):
    """(line number, reason) for the first line holding a whitespace run of more than MAX_WHITESPACE_RUN
    characters of the parser's whitespace class; None otherwise."""
    run = WHITESPACE_RUN_RE.search(text)
    if run is not None:
        return text.count("\n", 0, run.start()) + 1, (
            "the line holds a run of more than {} consecutive whitespace characters (space, tab or any "
            "other character the parser reads as whitespace), which the parser reads in time that grows "
            "with the square of the run; break the run".format(MAX_WHITESPACE_RUN))
    return None


def forbidden(text):
    """(line number, reason) for the first line separator other than LF anywhere in `text`, for the first
    line a structural prescan refuses (_whitespace_refused for a run over MAX_WHITESPACE_RUN,
    _nesting_refused for a lead past MAX_NESTING_DEPTH), or for the first format character (category Cf)
    outside the parsed document's code blocks and code spans; None when there is none. The prescans are
    judged on every text whatever else it holds, so those refusals never depend on unrelated content.
    DeclarationError for a text over MAX_TEXT_BYTES (refused here up front, parse path or not) and when a
    Cf stands in text the vendored parser cannot read. The Cf region check is one sorted sweep: positions
    ascend, so one pointer over the sorted regions visits each region once."""
    if len(text.encode("utf-8")) > MAX_TEXT_BYTES:
        raise DeclarationError("the brief is larger than {} bytes, over this reading's ceiling".format(
            MAX_TEXT_BYTES))
    found = SEPARATOR_RE.search(text)
    if found:
        line = text.count("\n", 0, found.start()) + 1
        return line, "the brief holds a line separator other than LF ({}); only LF ends a line".format(
            SEPARATORS[found.group(0)])
    for prescan in (_whitespace_refused, _nesting_refused):
        refused = prescan(text)
        if refused is not None:
            return refused
    if text.isascii():   # ASCII holds no format character
        return None
    positions = [index for index, character in enumerate(text)
                 if unicodedata.category(character) == "Cf"]
    if not positions:
        return None
    regions = sorted(_code_regions(_parse(text)))
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
    document = _parse(text)
    starts = _line_starts(text)
    line_of = lambda position: bisect.bisect_right(starts, position)  # noqa: E731
    inputs, bad = [], []
    for kind, line, element in _units(document, True, line_of):
        if kind in ("html", "code"):
            body = element.get("body", "") if kind == "html" else "".join(
                raw.get("text", "") for raw in element.get("children") or [])
            for offset, raw_line in enumerate(body.split("\n")):
                if _line_scan(raw_line, strip_tags=(kind == "html")):
                    bad.append(((line or 1) + offset, LOOK_ALIKE_REASON))
            continue
        for offset, characters in enumerate(_segments(element)):
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
