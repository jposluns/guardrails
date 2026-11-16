"""The element allowlist of the site template's pages: the one definition tools/gen_draft_site.py (its inline
markup and its link guard) and tools/check_site.py (its Page scan of the generated pages) read, so the three
cannot drift.

Why an allowlist. Python's html.parser and a browser tokenize some constructs differently: html.parser reads
"<![CDATA[" as a section up to "]]>" where a browser in HTML content ends it at the first ">", and it reads
textarea, title, xmp, noembed, noframes and plaintext as raw text even inside svg or math, where a browser
reads them as foreign elements and keeps tokenizing markup. Each difference hides markup that is live in a
browser from every check built on html.parser. Closing them one construct at a time cannot be shown
complete, so the pages are held to the elements they need instead:

- ELEMENTS is the closed set of HTML element names inline page markup may use. It is derived from every inline
  value in the draft-src data files (a, b, code, em, li, p, span, strong, ul) and from the elements the
  generator's own templates write in a page body, which an inline value may also carry (a raw table cell is a
  whole td element). It holds no foreign element (FOREIGN_ELEMENTS) and no raw-text or escapable raw-text
  element (RAW_TEXT_ELEMENTS).
- A whole generated page may also carry the shell's own structure, each only where the shell writes it:
  DOCUMENT_ELEMENTS (html, head, body) outside svg; HEAD_ELEMENTS (the head's meta, link, title and script)
  only while the head, written or implied, is open; the svg element (the light and dark switch's glyphs and the figure
  component's diagram) in the body, holding only SVG_ELEMENTS, and never a math element.

Inline markup is also refused when it holds a markup declaration or processing instruction other than an
ordinary comment (comment_problem). Residual: the rule reads element names as html.parser tokenizes them;
it bounds which elements reach a page, it does not prove the two tokenizers agree on every malformed tag.
"""
import re

ELEMENTS = frozenset((
    "a", "b", "blockquote", "button", "caption", "code", "col", "colgroup", "dd", "details", "div", "dl", "dt",
    "em", "figcaption", "figure", "footer", "h1", "h2", "h3", "h4", "header", "img", "li", "main", "nav", "ol",
    "p", "pre", "section", "small", "span", "strong", "summary", "table", "tbody", "td", "th", "thead", "tr",
    "ul",
))
DOCUMENT_ELEMENTS = frozenset(("html", "head", "body"))
HEAD_ELEMENTS = frozenset(("link", "meta", "script", "title"))
SVG_ROOT = "svg"
SVG_ELEMENTS = frozenset(("circle", "line", "path", "polygon", "rect", "text"))
FOREIGN_ELEMENTS = frozenset(("svg", "math"))
RAW_TEXT_ELEMENTS = frozenset((
    "script", "style", "textarea", "title", "xmp", "iframe", "noembed", "noframes", "noscript", "plaintext",
    "template",
))

if ELEMENTS & (FOREIGN_ELEMENTS | RAW_TEXT_ELEMENTS | DOCUMENT_ELEMENTS | HEAD_ELEMENTS | SVG_ELEMENTS):
    raise AssertionError("_site_markup: ELEMENTS overlaps a set it must exclude")

_TAG_NAME_RE = re.compile(r"</?([^\t\n\f\r />]*)")


def _raw_name(text):
    """The tag name as written at the start of a tag's source text, or None when there is none."""
    match = _TAG_NAME_RE.match(text) if isinstance(text, str) else None
    return match.group(1) if match else None


class ElementRule:
    """Judges each tag of 1 inline value (page false) or 1 whole page (page true) against the allowlist, in
    document order. start() and end() return a finding (a string) for a tag outside the allowlist, else None.
    `written` is the tag's source text when the caller has it; a name that is not ASCII is a finding, since
    html.parser folds it with str.lower and a browser does not."""

    def __init__(self, page):
        self.page = page
        # A page starts in its head, written or implied (its start tag is optional, as in a browser): the head
        # ends at "</head>", at "<body>" and at the first other start tag that is not a head element.
        self.head = page
        self.svg = 0

    def _context(self):
        if not self.page:
            return ELEMENTS, ""
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
        return problem

    def end(self, tag):
        if self.page and tag == SVG_ROOT and self.svg:
            self.svg -= 1
            return None
        if self.page and tag == "head" and self.head:
            self.head = False
            return None
        return self._judge("</{}>".format(tag), tag, None)


def comment_problem(value):
    """The first markup declaration or processing instruction in `value` (inline markup) that is not an ordinary
    comment, as "<offset>: <text>", or None. Every "<!" must open "<!--" and close at the first "-->" with text
    that does not start with ">" or "->", end with "-", or hold "--" or "<!-" (html.parser and a browser end
    such a comment at the same "-->"); every "<?" and every "</" not followed by an ASCII letter (a bogus
    comment to a browser) is refused. The scan is lexical, so such text inside an attribute value is refused
    too (fail closed)."""
    at = 0
    while True:
        found = re.compile(r"<[!?]|</(?![A-Za-z])").search(value, at)
        if found is None:
            return None
        start = found.start()
        if not value.startswith("<!--", start):
            return "{}: {!r}".format(start, value[start:start + 9])
        end = value.find("-->", start + 4)
        body = value[start + 4:end] if end >= 0 else ""
        if end < 0 or body.startswith((">", "->")) or body.endswith("-") or "--" in body or "<!-" in body:
            return "{}: {!r}".format(start, value[start:start + 9])
        at = end + 3
