"""Find markup that Python's html.parser tokenizes differently from a browser, in the raw page source.

A gate that judges a page through html.parser sees the page as that parser tokenizes it. Where a browser
(the WHATWG HTML parsing rules) reads the same bytes another way, content can reach the rendered page that
the gate never saw. This module gives such a gate 2 checks of the page source, standard library only:

  differential_constructs(raw)  every construct below found in the page source, as a named reason
  raw_vs_parsed(raw, parsed, names)
                                for each tag name, the raw "<name" start-tag count (case-insensitive) against
                                the count of start tags the gate's parser reported; any difference is a
                                reason, so a tag the parser did not see fails closed

The constructs, each with the way the 2 parsers part (html.parser of the Python floor, 3.14):
  - a srcdoc attribute: its value is a whole HTML document the browser renders in the iframe, which the
    gate reads as one attribute string.
  - a noscript start tag: html.parser reads its content as markup; a browser with scripting on reads it as
    text up to </noscript>, so an attribute value holding "</noscript>" ends it early in the browser.
  - a "<![" marked section: html.parser drops a "<![CDATA[" section up to "]]>"; a browser outside SVG
    and MathML reads it as a bogus comment ending at the first ">", and inside them keeps its text.
  - "<!-->" and "<!--->": where a "-->" follows later, html.parser runs the comment on to it (where none
    follows, it closes the comment at once too); a browser always closes an empty comment at once, so the
    markup up to that later "-->" is live.
  - a template start tag: a browser parses its content into an inert document fragment, while html.parser
    reads it as ordinary markup that the gate judges like any other, so a template hides nothing from the
    gate; it is refused to keep pages off a construct the 2 parsers build differently.
  - after the first svg or math start tag, a start tag of an element html.parser reads as raw text or
    escapable text (RAW_TEXT_TAGS): inside SVG and MathML a browser parses that content as markup.
  - a select start tag: html.parser reads a RAW_TEXT_TAGS start tag inside a select as raw text, while the
    in-select parsing rules of a browser ignore such a tag (all but script, which both read as raw text,
    and textarea, which closes the select), so the markup after it is live. Newer select parsing in
    browsers can differ again; the select is refused whichever rules apply.
  - a RAW_TEXT_TAGS start tag written self-closing ("<xmp/>"): html.parser reads it as a start and an end
    tag and stays in markup, while a browser ignores the "/" on a non-void HTML element and reads what
    follows as raw text up to the matching end tag, so markup after that end tag which html.parser read
    as the raw text of a later element is live. Only this check reads the page through html.parser itself
    (its defaults, as the gate's parsers use them), since the parser's own reading is what makes a tag
    self-closing; a void element (<br/>) or an element outside RAW_TEXT_TAGS (<path/>) is not reported.

The pattern checks can only over-report against a browser: a "<name" or a construct inside a comment, an
attribute value or element text that both parsers agree on is reported too, as is a non-ASCII letter that
Python's case folding matches to an ASCII one (U+017F long s matches s). The svg or math rule does not
track where the svg or math element ends, and it refuses an svg title written for accessibility. Residual:
a differential this list does not name (for example a malformed attribute or end tag a browser repairs
differently) is outside it, and raw_vs_parsed covers only the tag names a caller passes.
"""
import re
from html.parser import HTMLParser

# A browser ends a tag name at whitespace (a CR is read as LF), "/" or ">", and html.parser does the same.
_NAME_END = r"(?=[\t\n\f\r />]|\Z)"

# Elements whose content html.parser (3.14) does not read as markup, joined with the browser's own raw text
# and escapable raw text elements, plaintext and noscript, so the set does not shrink with the parser.
RAW_TEXT_TAGS = tuple(sorted(
    set(HTMLParser.CDATA_CONTENT_ELEMENTS) | set(HTMLParser.RCDATA_CONTENT_ELEMENTS)
    | {"script", "style", "xmp", "iframe", "noembed", "noframes", "textarea", "title", "plaintext", "noscript"}))
FOREIGN_TAGS = ("svg", "math")
# The element whose content a browser parses by its own rules, ignoring a raw text start tag inside it.
SELECT_TAG = "select"

# An attribute name starts after whitespace, "/" or a quoted value's closing quote, and ends at whitespace,
# "/", ">" or "=". A browser folds ASCII case in attribute names; IGNORECASE folds at least that much.
_SRCDOC_RE = re.compile(r"""[\t\n\f\r /"']srcdoc(?=[\t\n\f\r />=]|\Z)""", re.IGNORECASE)
_MARKED_SECTION_RE = re.compile(r"<!\[")
_EMPTY_COMMENT_RE = re.compile(r"<!---?>")


def start_tag_re(name):
    """The pattern for a raw start tag of `name`: "<", the name in any case, then a name end or the end."""
    return re.compile("<" + re.escape(name) + _NAME_END, re.IGNORECASE)


def raw_start_tags(raw, name):
    """The offsets of every raw start tag of `name` in `raw`."""
    return [match.start() for match in start_tag_re(name).finditer(raw)]


class _SelfClosing(HTMLParser):
    """Records the name of every start tag html.parser reads as self-closing, after which it stays in
    markup instead of entering raw text."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.names = set()

    def handle_startendtag(self, tag, attrs):
        self.names.add(tag)


def self_closed_raw_text(raw):
    """The sorted RAW_TEXT_TAGS names that html.parser, with its defaults, reads as self-closing in `raw`
    (outside SVG and MathML a browser enters raw text at each of them instead)."""
    parser = _SelfClosing()
    parser.feed(raw)
    parser.close()
    return sorted(parser.names & set(RAW_TEXT_TAGS))


def differential_constructs(raw):
    """Every construct of the module docstring found in `raw`, as a named reason (a list, empty when none)."""
    found = []
    if _SRCDOC_RE.search(raw):
        found.append("srcdoc attribute (a nested document the parser reads as text)")
    if raw_start_tags(raw, "noscript"):
        found.append("<noscript> element (a browser reads its content as text)")
    if _MARKED_SECTION_RE.search(raw):
        found.append('"<![" CDATA or marked section (a browser ends it elsewhere)')
    if _EMPTY_COMMENT_RE.search(raw):
        found.append('"<!-->" or "<!--->" comment (a browser closes it at once)')
    if raw_start_tags(raw, "template"):
        found.append("<template> element (a browser parses its content as an inert fragment)")
    foreign = [offset for name in FOREIGN_TAGS for offset in raw_start_tags(raw, name)]
    if foreign:
        first = min(foreign)
        for name in RAW_TEXT_TAGS:
            if any(offset > first for offset in raw_start_tags(raw, name)):
                found.append("<{}> after an <svg> or <math> start tag (a browser parses it as markup)"
                             .format(name))
    if raw_start_tags(raw, SELECT_TAG):
        found.append("<select> element (a browser ignores most raw text start tags inside it)")
    for name in self_closed_raw_text(raw):
        found.append("<{}/> written self-closing (a browser reads what follows as raw text)".format(name))
    return found


def raw_vs_parsed(raw, parsed, names):
    """A reason for each tag name in `names` whose raw start-tag count in `raw` differs from `parsed`, the
    mapping of lower-case tag name to the number of start tags the gate's parser reported (a missing name
    counts 0). Names are compared lower-case and each is checked once, in sorted order."""
    found = []
    for name in sorted({name.lower() for name in names}):
        raw_count = len(raw_start_tags(raw, name))
        seen = parsed.get(name, 0)
        if raw_count != seen:
            found.append("<{}> start tags: {} in the source, {} parsed".format(name, raw_count, seen))
    return found
