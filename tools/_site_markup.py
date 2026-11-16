"""The element allowlist and the tokenizer decision of the site template's pages: the one definition
tools/gen_draft_site.py (its inline markup, its link guard and its code-style refusals) and tools/check_site.py
(its Page scan) read, so they cannot drift.

Why. Python's html.parser and a browser (the WHATWG tokenizer) read some constructs differently, and each
difference can hide markup that is live in a browser from every check built on html.parser: html.parser reads
"<![CDATA[" as a section up to "]]>" where a browser in HTML content ends it at the first ">"; it opens a comment
at "<!-->" and "<!--->" and keeps it open to the next "-->" where a browser ends each at once as an empty
comment; and it reads textarea, title, xmp, noembed, noframes and plaintext as raw text even inside svg or math,
where a browser reads them as foreign elements and keeps tokenizing markup. Closing them one construct at a
time cannot be shown complete, so the pages are held to what both read alike instead:

- Markup constructs (split_constructs): the only markup declarations a page may hold are ordinary comments,
  which html.parser and a browser end at the same "-->", and, on a whole page, the "<!doctype html>"
  declaration. Every other "<!", every "<?" and every "</" not followed by an ASCII letter is refused, wherever
  it sits (inside an attribute value or raw text too: fail closed).
- Elements. INLINE_ELEMENTS is the closed set of HTML element names inline page markup may use: exactly the
  names the inline values of the draft-src data files use (a, b, code, em, li, p, span, strong, ul), and td as
  well only in a table component's raw cell (CELL_ELEMENTS; the template's whole-td cell form). ELEMENTS
  is the set a page body may carry: the names of every built page's body and of the generator's own template
  literals (the plan component's small among them). Neither holds a foreign element (FOREIGN_ELEMENTS) or a
  raw-text or escapable raw-text element (RAW_TEXT_ELEMENTS).
- A whole generated page may also carry the shell's own structure, each only where the shell writes it:
  DOCUMENT_ELEMENTS (html, head, body) outside svg; HEAD_ELEMENTS (the head's meta, link, title and script)
  only while the head, written or implied, is open (ElementRule; text other than whitespace ends an implied
  head, as in a browser); the svg element (the light and dark switch's glyphs and the figure component's
  diagram) in the body, holding only SVG_ELEMENTS, and never a math element.
- An inline value must end at a token boundary (unfinished): a start tag, end tag or other construct it leaves
  open would run on into the template's markup that follows it on the page.

Residual: the rule reads element names as html.parser tokenizes them; it bounds which elements and constructs
reach a page, it does not prove the two tokenizers agree on every malformed tag.
"""
import re

INLINE_ELEMENTS = frozenset(("a", "b", "code", "em", "li", "p", "span", "strong", "ul"))
# A table component's raw cell is a whole td element (gen_draft_site._table_html): only there may inline markup
# also use td.
CELL_ELEMENTS = INLINE_ELEMENTS | {"td"}
ELEMENTS = frozenset((
    "a", "b", "blockquote", "button", "caption", "code", "col", "colgroup", "dd", "details", "div", "dl", "dt",
    "em", "figcaption", "figure", "footer", "h1", "h2", "h3", "h4", "header", "img", "li", "main", "nav", "ol",
    "p", "pre", "section", "small", "span", "strong", "summary", "table", "tbody", "td", "th", "thead", "tr",
    "ul",
))
DOCUMENT_ELEMENTS = frozenset(("html", "head", "body"))
HEAD_ELEMENTS = frozenset(("link", "meta", "script", "title"))
# The head elements whose content html.parser and a browser read as raw text: their text is not page text.
_HEAD_RAW_TEXT = frozenset(("script", "title"))
SVG_ROOT = "svg"
SVG_ELEMENTS = frozenset(("circle", "line", "path", "polygon", "rect", "text"))
FOREIGN_ELEMENTS = frozenset(("svg", "math"))
RAW_TEXT_ELEMENTS = frozenset((
    "script", "style", "textarea", "title", "xmp", "iframe", "noembed", "noframes", "noscript", "plaintext",
    "template",
))
DOCTYPE = "<!doctype html>"
# The whitespace a browser's head insertion modes keep in the head; any other text ends the head.
_SPACE = "\t\n\f\r "

if ELEMENTS & (FOREIGN_ELEMENTS | RAW_TEXT_ELEMENTS | DOCUMENT_ELEMENTS | HEAD_ELEMENTS | SVG_ELEMENTS):
    raise AssertionError("_site_markup: ELEMENTS overlaps a set it must exclude")
if not CELL_ELEMENTS <= ELEMENTS:
    raise AssertionError("_site_markup: INLINE_ELEMENTS or CELL_ELEMENTS is not within ELEMENTS")

_TAG_NAME_RE = re.compile(r"</?([^\t\n\f\r />]*)")
_CONSTRUCT_RE = re.compile(r"<[!?]|</(?![A-Za-z])")


def _raw_name(text):
    """The tag name as written at the start of a tag's source text, or None when there is none."""
    match = _TAG_NAME_RE.match(text) if isinstance(text, str) else None
    return match.group(1) if match else None


class ElementRule:
    """Judges each tag of 1 inline value (page false) or 1 whole page (page true) against the allowlist, in
    document order. start() and end() return a finding (a string) for a tag outside the allowlist, else None;
    text() takes the page's character data, as html.parser hands it over. `written` is the tag's source text
    when the caller has it; a name that is not ASCII is a finding, since html.parser folds it with str.lower
    and a browser does not. `cell` true (inline only) judges a table's raw cell against CELL_ELEMENTS."""

    def __init__(self, page, cell=False):
        self.page = page
        self.inline = CELL_ELEMENTS if cell else INLINE_ELEMENTS
        # A page starts in its head, written or implied (its start tag is optional, as in a browser): the head
        # ends at "</head>", at "<body>", at the first other start tag that is not a head element, and at the
        # first text other than whitespace outside a title or script (text()).
        self.head = page
        self.raw = None
        self.svg = 0

    def _context(self):
        if not self.page:
            return self.inline, ""
        if self.svg:
            return SVG_ELEMENTS, " inside <svg>"
        if self.head:
            return HEAD_ELEMENTS | {"html", "head"}, " inside <head>"
        return ELEMENTS | DOCUMENT_ELEMENTS | {SVG_ROOT}, " on the page"

    def _judge(self, shown, tag, written):
        allowed, where = self._context()
        name = _raw_name(written)
        if name is not None and not name.isascii():
            return "{}{} has a non-ASCII element name {!r}".format(shown, where, name)
        if tag not in allowed:
            return "{}{}, an element outside the template's element allowlist".format(shown, where)
        return None

    def start(self, tag, written=None, closed=False):
        if self.page and self.head and tag not in HEAD_ELEMENTS and tag not in ("html", "head"):
            self.head = False
        problem = self._judge("<{}>".format(tag), tag, written)
        if self.page and not closed and tag == SVG_ROOT:
            self.svg += 1
        if self.page and self.head and not closed and tag in _HEAD_RAW_TEXT:
            self.raw = tag
        return problem

    def end(self, tag):
        if tag == self.raw:
            self.raw = None
        if self.page and tag == SVG_ROOT and self.svg:
            self.svg -= 1
            return None
        if self.page and tag == "head" and self.head:
            self.head = False
            return None
        return self._judge("</{}>".format(tag), tag, None)

    def text(self, data):
        """Character data outside a tag. Text other than whitespace (TAB, LF, FF, CR, SPACE) outside the head's
        title or script ends the head, written or implied, as a browser's head insertion modes do."""
        if self.page and self.head and self.raw is None and data.strip(_SPACE):
            self.head = False


def split_constructs(source, page=False):
    """Each markup construct in `source` that html.parser and a browser may end at different places, as
    (offset, its first 9 characters), in order. Every "<!" must open an ordinary comment, "<!--" closing at
    the first "-->" with text that does not start with ">" or "->", end with "-", or hold "--" or "<!-"
    (html.parser and a browser end such a comment at the same "-->"), except that on a whole page (`page`)
    "<!doctype html>" in any letter case is also read alike; every "<?" and every "</" not followed by an
    ASCII letter (a bogus comment to a browser) is such a construct. The scan is lexical, so such text inside
    an attribute value or raw text is reported too (fail closed)."""
    found, at = [], 0
    while True:
        match = _CONSTRUCT_RE.search(source, at)
        if match is None:
            return found
        start = match.start()
        if page and source[start:start + len(DOCTYPE)].lower() == DOCTYPE:
            at = start + len(DOCTYPE)
            continue
        end = source.find("-->", start + 4) if source.startswith("<!--", start) else -1
        body = source[start + 4:end] if end >= 0 else ""
        if end < 0 or body.startswith((">", "->")) or body.endswith("-") or "--" in body or "<!-" in body:
            found.append((start, source[start:start + 9]))
            at = start + 2
            continue
        at = end + 3


def construct_finding(offset, text):
    """The finding for 1 construct split_constructs reports, the wording every consumer shares."""
    return "{!r} at offset {}, a markup construct html.parser and a browser can end at different places".format(
        text, offset)


def unfinished(parser):
    """The text an html.parser.HTMLParser that has been fed 1 inline value (and not closed) still holds back
    because a construct in it is not complete ('<span title="x', "</p", a lone "<"), or None. On a page such a
    construct runs on into the template's markup that follows the value."""
    rest = parser.rawdata
    at = rest.find("<")
    return rest[at:] if at >= 0 else None
