#!/usr/bin/env python3
"""The one reading of a brief's INPUT declarations, shared by the brief lint's P6
(tools/check_brief_capability.py) and the delivery check's C1 (tools/check_verifier_delivery.py), so the
two can never disagree on what a brief declares.

THE PARSED DOCUMENT. A brief is Markdown, and the worker reads it through a Markdown reader, so the
declarations are judged on the document the vendored CommonMark parser builds (opf/tools/_vendor/marko,
pinned 2.2.4 with its provenance file and byte manifest, loaded exactly as opf/tools/_commonmark_headings.py
loads it: the vendor directory derived from this file's own location, the version pinned, and the imported
modules origin-checked against the vendored tree, never an ambient install), with the vendored GFM elements
(tables, task-list items, strikethrough, autolinked URLs and alerts) registered on a fresh extension-free
parser per parse, so the reading matches what a GFM reader shows. Conformance is exactly Marko 2.2.4's
(about 97.5% of the CommonMark 0.31.2 example suite; see opf/tools/selftest_commonmark_conformance.py), so
on rare edge inputs the reading diverges from a strict spec reading exactly where Marko does (a no-break
space accepted after a closing fence, for one). A parse failure, a vendored tree that cannot be imported or
fails its provenance pin, a text over MAX_TEXT_BYTES, or a document span that does not cover the text is
DeclarationError, which both callers turn into their cannot-evaluate refusal, never a silent empty reading.
The parse is worst-case superlinear on crafted pathological input under the byte ceiling; the residual is
availability (a slow lint), never a wrong verdict, and a brief that is pure ASCII with no "nput" substring
is answered without parsing (nothing in it can declare, look alike, or hold a format character).

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
import html
import os
import re
import sys
import unicodedata

MARKO_VERSION = "2.2.4"           # the exact vendored pin; any other resolved version fails closed
MAX_TEXT_BYTES = 1 << 20          # the brief lint's own brief ceiling; a larger text is DeclarationError
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
    modules imported here (marko, marko.block, marko.parser, marko.inline, marko.ext.gfm.elements); the
    other parse-path submodules resolve from the verified package's own __path__. Never an ambient
    install: a mismatch is DeclarationError, not a fallback."""
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
    for module in (marko, marko.block, marko.inline, marko.parser, gfm_elements):
        _verify_module_origin(module)
    _LOADED = (marko, gfm_elements)
    return _LOADED


def _parse(text):
    """The marko Document for `text`: a FRESH extension-free Parser per call with the vendored GFM
    elements registered (never marko's Markdown object, which is documented not thread-safe). The byte
    ceiling is enforced before parsing; a parse exception and a document span that does not cover `text`
    (the parser's buffer rewrites are length-preserving only over LF-only text, which forbidden()
    guarantees first) are DeclarationError."""
    if len(text.encode("utf-8")) > MAX_TEXT_BYTES:
        raise DeclarationError("the brief is larger than {} bytes, over this reading's ceiling".format(
            MAX_TEXT_BYTES))
    marko, gfm_elements = _load_marko()
    try:
        parser = marko.parser.Parser()
        for name in GFM_ELEMENT_NAMES:
            parser.add_element(getattr(gfm_elements, name))
        document = parser.parse(text)
    except DeclarationError:
        raise
    except Exception as exc:
        raise DeclarationError("the CommonMark parser failed over the brief: {}".format(exc)) from exc
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


def forbidden(text):
    """(line number, reason) for the first line separator other than LF anywhere in `text`, or the first
    format character (category Cf) outside the parsed document's code blocks and code spans; None when
    there is neither. DeclarationError when a Cf stands in text the vendored parser cannot read."""
    found = SEPARATOR_RE.search(text)
    if found:
        line = text.count("\n", 0, found.start()) + 1
        return line, "the brief holds a line separator other than LF ({}); only LF ends a line".format(
            SEPARATORS[found.group(0)])
    if text.isascii():   # ASCII holds no format character
        return None
    positions = [index for index, character in enumerate(text)
                 if unicodedata.category(character) == "Cf"]
    if not positions:
        return None
    regions = _code_regions(_classes(), _parse(text))
    for index in positions:
        if not any(start <= index < end for start, end in regions):
            return text.count("\n", 0, index) + 1, (
                "the brief holds a format character (U+{:04X}) outside a code block or code span; format "
                "characters are invisible and are refused, never removed silently".format(ord(text[index])))
    return None


def read(text):
    """(inputs, bad): [(line number, path)] for the declarations of the parsed document, in order, and
    [(line number, reason)] for every malformed declaration or look-alike. Inside code nothing is read.
    The caller refuses forbidden() first, so `text` ends its lines with LF alone here. DeclarationError
    when the vendored parser cannot read the text."""
    if text.isascii() and "nput" not in text.lower():
        return [], []   # nothing in it can spell the token INPUT, even folded
    classes = _classes()
    document = _parse(text)
    line_of = lambda position: text.count("\n", 0, position) + 1  # noqa: E731
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
