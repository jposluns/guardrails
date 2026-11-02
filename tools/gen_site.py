#!/usr/bin/env python3
"""Generate site/<name>.html pages from docs/<name>.md sources via the shared themed shell.

Single source of truth for each documentation page: a Markdown file under docs/ with a TOML frontmatter
block (delimited by `+++` fences) carries the page's per-page metadata and content, and this generator
composes it into site/<name>.html by substituting into docs/_shell.html. Every page shares the same
themed head, topbar, sidebar, footer, and theme.js bootstrap; the frontmatter's `sidebar-active` slug
marks the current-page link in the shared sidebar. This lets the docs and the site never diverge and
lets a page migrate from hand-authored HTML to a Markdown source without changing what the site renders.

Pilot scope (OPF-1 Phase 2 first-page milestone): docs/rule1.md -> site/rule1.html, byte-parity round
trip against the pre-migration hand-authored rule1. Later PRs migrate rule2..5, then the non-rule
pages, then the landing page hand-authored escape hatch (frontmatter `hand-authored = true`), then CI
turns on the drift gate for the full site.

Body conversion is a bounded stdlib converter, deliberately NOT a full Markdown implementation: an
external dependency (markdown, mistune) would enter through the project's dependency-provenance gate.
The subset supported today is CommonMark-consistent HTML block pass-through plus enough Markdown
constructs to keep the rule pages' content authorable in prose:
  - HTML blocks: any line at column 0 starting with `<` starts an HTML block that runs until the next
    blank line, passed through verbatim. Type-1 HTML blocks whose opening tag is `<script>`, `<pre>`,
    `<style>`, or `<textarea>` are an exception (CommonMark §4.6): they continue THROUGH blank lines
    until a line contains the matching close tag, so an embedded blank line does not corrupt them.
    This is how the rule pages carry their `<div class="wrap">` and `<section id="...">` scaffolding
    today, so the pilot round-trips byte-exact.
  - ATX headings (# H1, ## H2, ### H3), paragraphs (blank-line-separated blocks of text), and hr
    (a line of exactly `---`). Enough to migrate simpler pages later without an HTML fallback.
  - Fenced code blocks and inline formatting stay a next-slice concern; the corpus we are migrating in
    this PR does not use them.

Frontmatter tokens are context-escaped for their HTML sink: `title` for text position (inside
`<title>...</title>`) and every other field for quoted-attribute position (inside `content="..."` or
`href="..."`). Substitution is a SINGLE `_TOKEN_RE.sub` pass over the original shell that inserts the
body `{{content}}` fragment and every frontmatter token SIMULTANEOUSLY, so a substituted value is
NEVER re-scanned for a further token (defeats the cascading-token vector) and a body carrying literal
`{{name}}` prose is inert. At shell load, `_validate_token_sinks` confirms each field token's actual
parsed position (attribute value vs element text) matches its declared sink, so a later shell edit
that moves a text-sink token into an attribute context is rejected rather than silently under-escaped.

Residuals (disclosed per `10-ACCUR-disclose-guard-residuals`):
  - Frontmatter escaping covers the text and quoted-attribute HTML sinks the shell places tokens in.
    The sink validator (`_validate_token_sinks`) confirms only a COARSE attribute-vs-text position; it
    does NOT distinguish a quoted from an unquoted attribute, nor a `<script>`/`<style>` raw-text
    context from ordinary element text. So a future shell edit that moves a token into an UNQUOTED
    attribute, or a text-sink token into a `<script>`/`<style>` body, is NOT detected and would render
    under-escaped; `{{content}}` (the body fragment) is not sink-validated at all. There is no live
    exposure at HEAD: the checked-in `_shell.html` places every token in a quoted attribute or element
    text. This is a disclosed residual on FUTURE shell edits, consistent with the maintainer-authored-
    shell threat model, not a live defect; closing it fully would need a quoting/raw-text-aware
    collector (a future slice). The URL fields (canonical, og-url) are attribute-escaped, NOT
    URL-scheme-validated; a maintainer-authored `javascript:` value in the frontmatter would render
    literally into an href/content attribute.
  - md_to_html's HTML-block pass-through preserves the block bytes exactly for the type-1 subset and
    for lines beginning with `<`; heading/paragraph/hr blocks have their trailing whole-block
    whitespace normalized before classification, and paragraphs fold internal per-line ASCII
    space/tab into single spaces on rewrap (non-ASCII whitespace is preserved as content, and the
    whole-block trailing-whitespace normalization strips only ASCII space/tab/newline). A block whose
    first line is text but a later continuation line begins with a tag-like `<` (`<` followed by a
    letter, `/`, `!`, or `?`) is an unsupported mixed block (CommonMark type-6 paragraph interruption):
    it is REJECTED with a fail-closed SchemaError directing the author to separate the two with a blank
    line, never silently escaped into the paragraph. A bare `<` followed by a space or other non-tag
    character is treated as ordinary paragraph text and escaped, not an opener. This is conservative
    (it also rejects CommonMark type-7 tags), a fail-loud restriction, never a silent-wrong render.
    Disallowed control characters (C0 except tab/newline/CR, plus DEL) in a frontmatter field value or
    the body are rejected at the field/body boundary rather than passed through raw. That control
    rejection covers only LITERAL such characters at the field/body boundary; it does NOT decode
    numeric character references (e.g. `&#x01;`) inside pass-through HTML blocks, and does not scrub
    the maintainer-authored `_shell.html` template. Both are within the trusted-shell threat model.
  - Discovery enumerates docs/ RECURSIVELY via an explicit `os.scandir` walk (NOT `rglob`, which
    silently skips a symlinked directory and swallows a permission error on an unreadable subtree);
    every discovered `docs/**/*.md` must be declared in GENSRC_OUTPUTS or `--check`/generation fails
    closed (exit 2), and any `.md` that is a symbolic link is refused. An unreadable subtree or a
    symlinked directory fails closed (exit 2) rather than vanishing: the walk refuses to follow either.
    There is no filename-prefix exemption: the only shell template (`_shell.html`) is `.html`, never a
    `.md` discovery candidate, so an underscore-prefixed, nested, or symlinked stray `.md` fails loud
    instead of vanishing.
  - Source and target symlink protection is a pre-check (`is_symlink()` + `resolve()` bounding); a
    committed symlink is refused. The check/use gap between the pre-check and the read/write syscall
    is NOT race-free (no openat/O_NOFOLLOW/beneath containment). This is an accepted residual for a
    maintainer-run LOCAL build tool operating on repo-relative paths under docs/ and site/ in a
    committed git tree: the closed vector is a committed symlink; the residual is a same-host actor
    with write access to the working tree swapping a path component between the pre-check and the
    syscall, which is outside this tool's threat model (matching the L11/L-GS1 disclosed-residual
    precedent). A hardened deployment isolates the build at the OS level (namespaces/bind-mounts).

  gen_site.py           regenerate every site/<name>.html declared in GENSRC_OUTPUTS
  gen_site.py --check   fail (exit 1) if any target is out of date; exit 2 on a bad source/shell
  gen_site.py --self-test  adversarial corpus over the bounded subset and the fail-closed paths
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: gen_site.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import html
import html.entities
import os
import re
from html.parser import HTMLParser
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # not a version problem: every Python 3.14 ships tomllib
    import os
    try:
        sys.stderr.write(
            "error: gen_site.py cannot import tomllib, part of the Python standard library; "
            "this installation is incomplete. Nothing was run (cannot evaluate).\n")
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _gen_common import repo_root, reconcile  # noqa: E402

# The gensrc registry declaration. Only the pilot page is generated today; later PRs add the other
# docs/*.md -> site/*.html pairs, each as its own row, so the registry lists every generated page
# individually. Consistent with how gen_skill declares its per-artefact outputs. This tuple is the
# AUTHORITATIVE list of pages this generator produces: the `--check` path enforces every declared
# source is present as a regular non-symlink file (so a deleted or renamed docs/*.md fails closed
# rather than silently reading as an absent source that trips no check).
GENSRC_OUTPUTS = (
    {"target": "site/rules.html", "kind": "file",
     "sources": ("docs/_shell.html", "docs/rules.md"),
     "regenerate": "python3 tools/gen_site.py"},
    {"target": "site/about.html", "kind": "file",
     "sources": ("docs/_shell.html", "docs/about.md"),
     "regenerate": "python3 tools/gen_site.py"},
    {"target": "site/development.html", "kind": "file",
     "sources": ("docs/_shell.html", "docs/development.md"),
     "regenerate": "python3 tools/gen_site.py"},
    {"target": "site/evidence.html", "kind": "file",
     "sources": ("docs/_shell.html", "docs/evidence.md"),
     "regenerate": "python3 tools/gen_site.py"},
    {"target": "site/learn.html", "kind": "file",
     "sources": ("docs/_shell.html", "docs/learn.md"),
     "regenerate": "python3 tools/gen_site.py"},
    {"target": "site/standard.html", "kind": "file",
     "sources": ("docs/_shell.html", "docs/standard.md"),
     "regenerate": "python3 tools/gen_site.py"},
    {"target": "site/examples.html", "kind": "file",
     "sources": ("docs/_shell.html", "docs/examples.md"),
     "regenerate": "python3 tools/gen_site.py"},
    {"target": "site/teams.html", "kind": "file",
     "sources": ("docs/_shell.html", "docs/teams.md"),
     "regenerate": "python3 tools/gen_site.py"},
)

DOCS_DIR = "docs"
SITE_DIR = "site"
SHELL_REL = "docs/_shell.html"
CONTENT_TOKEN = "{{content}}"
FENCE = "+++"

# The frontmatter fields the shell template consumes, mapped to (token, sink). A required field
# missing from a page's frontmatter is a SchemaError (fail-closed exit 2); an unexpected key is also
# a SchemaError so a typo cannot silently drop metadata. The sink names the HTML context the token
# sits in: 'text' is content position (inside `<title>...</title>` or visible text); 'attr' is
# quoted-attribute position (inside `content="..."` or `href="..."`). Each value is HTML-escaped for
# its sink before substitution; the text sink escapes `&<>` and the attr sink additionally escapes
# `"` and `'` so a value cannot escape its surrounding attribute quotes.
REQUIRED_FIELDS = ("title", "description", "canonical",
                   "og-url",
                   "sidebar-active")
OPTIONAL_FIELDS = ("hand-authored",)
FIELD_TO_TOKEN = {
    "title": ("{{title}}", "text"),
    "description": ("{{description}}", "attr"),
    "canonical": ("{{canonical}}", "attr"),
    "og-url": ("{{og_url}}", "attr"),
}

# The link-preview tag block every published page's head carries except site/404.html (the pages
# tools/check_site.py's HEAD_TAG_EXEMPT names, which are not checked), in order, exactly once each. The
# og:title/og:description/twitter text and the card image are SITE-WIDE (the shell hardcodes them);
# only the canonical href and og:url vary, and both must be the page's OWN URL. `{url}` marks those
# two slots. _validate_head_tags checks every page this generator renders, the page
# gen_enforcement_register composes, and (through tools/check_site.py) every published site/*.html page
# except site/404.html, hand-maintained ones included, so an edit that drops a tag, duplicates one, moves
# one out of the head, reorders the block, or points a page at another page's canonical fails closed.
SITE_BASE_URL = "https://aiqt.ai/"
HEAD_TAG_BLOCK = (
    '<link rel="canonical" href="{url}">',
    '<meta property="og:type" content="website">',
    '<meta property="og:site_name" content="AIQT.ai">',
    '<meta property="og:url" content="{url}">',
    '<meta property="og:title" content="AIQT.ai - Rules your AI follows">',
    '<meta property="og:description" content="Rules for your chat assistant, enforcement controls '
    'for your codebase.">',
    '<meta property="og:image" content="https://aiqt.ai/images/aiqt-card.png">',
    '<meta property="og:image:type" content="image/png">',
    '<meta property="og:image:width" content="1200">',
    '<meta property="og:image:height" content="630">',
    '<meta property="og:image:alt" content="AIQT.ai: Rules your AI follows. Controls that catch '
    'what slips.">',
    '<meta name="twitter:card" content="summary_large_image">',
    '<meta name="twitter:title" content="AIQT.ai - Rules your AI follows">',
    '<meta name="twitter:description" content="Rules for your chat assistant, enforcement controls '
    'for your codebase.">',
    '<meta name="twitter:image" content="https://aiqt.ai/images/aiqt-card.png">',
    '<meta name="twitter:image:alt" content="AIQT.ai: Rules your AI follows. Controls that catch '
    'what slips.">',
)


def page_url(target_rel):
    """The page's own canonical URL, derived from its site/ target name: site/<name>.html publishes at
    SITE_BASE_URL + <name> (the home page, index.html, publishes at the bare site root)."""
    name = Path(target_rel).name
    stem = name[:-len(".html")] if name.endswith(".html") else name
    return SITE_BASE_URL if stem == "index" else SITE_BASE_URL + stem


# The elements a browser's tree builder keeps in the head; any other start tag inside the head ends it.
HEAD_ELEMENTS = frozenset(("base", "link", "meta", "noscript", "script", "style", "template", "title"))
# Head elements whose contents are raw text, not markup.
HEAD_RAW_TEXT = frozenset(("script", "style", "title"))
# Elements whose contents both html.parser and a browser's tokenizer read as text up to the first end tag
# of the same name (the specification's raw-text and RCDATA elements, plaintext aside).
RAW_TEXT_ELEMENTS = HEAD_RAW_TEXT | frozenset(("iframe", "noembed", "noframes", "textarea", "xmp"))
# Elements whose contents a browser's tokenizer reads as text, not markup (noscript when scripts run,
# plaintext to the end of the page).
TEXT_CONTENT = RAW_TEXT_ELEMENTS | frozenset(("noscript", "plaintext"))
# The elements that start svg or math content, and the elements inside it where HTML rules apply again.
FOREIGN_ROOTS = frozenset(("math", "svg"))
INTEGRATION_POINTS = frozenset(("annotation-xml", "desc", "foreignobject", "mi", "mn", "mo", "ms", "mtext"))
# HTML's whitespace characters; any other text inside the head ends it.
HTML_WHITESPACE = " \t\n\f\r"
_ATTR = (r"[^\t\n\f\r />\"'<=\x00][^\t\n\f\r />\"'<=\x00]*"
         r"(?:[\t\n\f\r ]*=[\t\n\f\r ]*(?:\"[^\"]*\"|'[^']*'|[^\t\n\f\r \"'=<>`\x00]+))?")
# Group 2 is the slash that makes the tag self-closing: only text left after the last attribute counts, so
# the "/" in `<svg x=a/>` belongs to the unquoted value and the svg stays open, as both readers take it.
_START_TAG_RE = re.compile(r"<([A-Za-z][A-Za-z0-9-]*)(?:[\t\n\f\r ]+" + _ATTR + r")*[\t\n\f\r ]*(/?)>")
_END_TAG_RE = re.compile(r"</([A-Za-z][A-Za-z0-9-]*)[\t\n\f\r ]*>")
_DOCTYPE_RE = re.compile(r"<!doctype[\t\n\f\r ]+html(?:[\t\n\f\r ]+system[\t\n\f\r ]+"
                         r"(?-i:\"about:legacy-compat\"|'about:legacy-compat'))?[\t\n\f\r ]*>", re.IGNORECASE)
_TEXT_END_RE = re.compile("</(?:" + "|".join(sorted(TEXT_CONTENT)) + ")", re.IGNORECASE)
_RAW_END_RE = {name: re.compile("</" + name + "(?=[\t\n\f\r />])", re.IGNORECASE)
               for name in RAW_TEXT_ELEMENTS | {"noscript"}}
_SCRIPT_START_RE = re.compile("<script[\t\n\f\r />]", re.IGNORECASE)
_MARKUP_START_RE = re.compile("<[/!?]")
_TAG_START_RE = re.compile("<[A-Za-z/!?]")
# A character reference as html.unescape finds one; the accepted ones are `&name;` with "name;" in the
# specification's table, and `&#<digits>;` or `&#x<hex digits>;` naming a CHARREF_OK code point.
_CHARREF_RE = re.compile(r"&(#[0-9]+;?|#[xX][0-9a-fA-F]+;?|[^\t\n\f <&#;]{1,32};?)")
CHARREF_OK = ((0x09, 0x0A), (0x20, 0x7E), (0xA0, 0xD7FF), (0xE000, 0xFDCF), (0xFDF0, 0xFFFD))


def _charref_error(text):
    """The first character reference in `text` outside the accepted set, or None. The accepted set is a
    conservative closed one, not the set where html.unescape and the tokenizer decode a reference
    differently: it refuses those and some they decode alike. A numeric reference longer than ten
    characters after the "&" (more than eight decimal or seven hex digits, leading zeros counted) fails
    before anything decodes it, so `&#00000065;` passes and `&#000000065;` fails although both name "A",
    and a run of digits too long for int() fails as this refusal, not as a ValueError. A reference
    html.unescape leaves as written passes (no table name is a prefix of it, so a browser leaves it too).
    Of the rest only a `;`-terminated reference passes: a named one whose full "name;" is in the table, or a
    numeric one naming a code point in CHARREF_OK, for which html.unescape and the tokenizer yield that code
    point. So these fail: a reference to a control code point or a noncharacter (html.unescape yields ''
    for most, a browser keeps them), to U+0000 or U+0080 to U+009F (remapped), a legacy name without ";"
    (a browser leaves it as written inside an attribute value before "=" or an ASCII letter or digit,
    html.unescape decodes it), and references both readers decode alike, such as `&#12;`, `&#128512;` and
    `&#65` without ";"."""
    for ref in _CHARREF_RE.finditer(text):
        body = ref.group(1)
        if body.startswith("#") and len(body) > 10:
            return ref.group(0)
        if html.unescape(ref.group(0)) == ref.group(0):
            continue
        if body.endswith(";") and not body.startswith("#") and body in html.entities.html5:
            continue
        if body.endswith(";") and body.startswith("#"):
            point = int(body[2:-1], 16) if body[1:2] in "xX" else int(body[1:-1])
            if any(low <= point <= high for low, high in CHARREF_OK):
                continue
        return ref.group(0)
    return None


def _page_syntax_error(page):
    """The accepted page subset: the first place `page` steps outside a syntax on which html.parser (as
    shipped with the Python floor, 3.14) and the HTML specification's tokenizer read the same tags, comments
    and text runs, as a message, or None. One pass over the source text, linear in its length (the line is
    counted only for the message), run before html.parser so its verdict does not rest on html.parser's own
    error recovery. Refused anywhere in the page:
      - any "<!" construct but a comment and a leading doctype: a comment must open with exactly "<!--",
        must not begin "<!-->" or "<!--->", and must close at its first later "-->" with no "--!>" and no
        nested "<!--" before it (html.parser ends some of these elsewhere than the tokenizer does), so a
        CDATA section, a bogus comment and an abrupt or unclosed comment fail; the doctypes accepted are
        `<!doctype html>` and `<!doctype html system "about:legacy-compat">` (keywords in any case, the
        quoted string exact), before all other markup; a "<?" construct fails too;
      - a start or end tag outside a plain shape: a start tag is a name of ASCII letters, digits and
        hyphens, then whitespace-separated attributes (a name free of quotes, "<", "=" and "/", with an
        optional double-quoted, single-quoted or unquoted value; an unquoted value holds no quote, "=",
        "<", ">" or backtick), then an optional "/" and ">"; an end tag is a name, optional whitespace and
        ">". A start tag is self-closing only when the "/" follows its last attribute: in `<svg x=a/>` the
        "/" ends the unquoted value, so the element stays open for both readers;
      - a self-closing start tag of an element whose contents are text (TEXT_CONTENT: `<script/>`,
        `<style/>`, `<title/>`, `<textarea/>`, `<noscript/>` and the rest), since the tokenizer ignores
        the slash and reads on as text while html.parser reads on as markup; a TEXT_CONTENT start tag whose
        last attribute is an unquoted value ending in "/" right before the ">" (`<iframe src=/embed/>`),
        which both readers leave open but which reads as self-closing, refused more broadly than the two
        readers need so the page cannot be taken either way at a glance (with whitespace before the ">",
        as in `<iframe src=/embed/ >`, the tag does not read as self-closing and passes); and any
        `<plaintext>`;
      - "</" followed by the name of any TEXT_CONTENT element (any case) inside an attribute value or the
        text of a RAW_TEXT_ELEMENTS element, so no end tag that one reader takes as text can end an element
        for the other (inside a comment it can end nothing: a comment inside a `<noscript>`, the one place
        a browser reads it as text, is refused below);
      - a RAW_TEXT_ELEMENTS element or a `<noscript>` that never ends, or whose first end tag (its name
        followed by whitespace, "/" or ">") carries more than its name and whitespace;
      - in the text of a `<noscript>`, in the head or the body, "<" followed by an ASCII letter, "/", "!"
        or "?": a browser running scripts reads that text as text, html.parser as markup, so markup there
        could add, hide or move a keyed tag for one reader only;
      - in the text of a `<script>`, "<!--" followed later by "<script" and then whitespace, "/" or ">"
        (any case), the only way into the tokenizer's double-escaped script state, which html.parser does
        not follow; "<!--" alone, as in a legacy `<script><!-- a() //--></script>`, passes this scan (in
        the head it fails in _HeadTagCollector);
      - in the text of a RAW_TEXT_ELEMENTS element other than script and style, "<" followed by "/", "!"
        or "?": broader than the specification needs (html.parser 3.14 reads that text as text too), kept
        so the check does not rest on how html.parser treats such text;
      - inside svg or math content (from an `<svg>` or `<math>` start tag that is not self-closing to its
        end tag), a start tag of a TEXT_CONTENT element other than `<style>` and `<title>`, or of an HTML
        integration point (INTEGRATION_POINTS), and a `<style>` or `<title>` whose text holds "<": there the
        tokenizer reads those elements' contents as markup and html.parser as text, so a keyed tag inside
        one is an element to a browser and text to html.parser; text without "<" reads alike. The
        integration points, MathML's `<mi>`, `<mo>` and `<mn>` among them, stay refused whatever they hold,
        because inside them a browser switches back to HTML rules this check does not track;
      - outside comments and the text of RAW_TEXT_ELEMENTS elements (in text, in a `<noscript>`, and in
        every start tag), a character reference outside the conservative closed set _charref_error
        accepts, which also refuses some references both readers decode alike (among the refused, a
        reference to a control code point or a noncharacter, which html.unescape drops and a browser
        keeps, could end the head for a browser only, or hide a key's spelling from one reader).
    _HeadTagCollector adds the refusals that need the head's place (see there). Inside the accepted subset
    html.parser 3.14 and the tokenizer read the same tags, comments and text runs as far as this check has
    found; the differences it knows of in where the head ends are listed in _HeadTagCollector and each ends
    the head sooner here, not later. That this list is complete is not established."""
    found = _page_syntax_scan(page)
    if found is None:
        return None
    return "line {}: {}".format(page.count("\n", 0, found[0]) + 1, found[1])


def _page_syntax_scan(page, foreign=None):
    """_page_syntax_error's pass: (offset, message) for the first refusal, or None. Each character is read a
    bounded number of times; the svg/math stack keeps a count per name so an end tag finds its element in
    constant time, and each element is popped at most once. `foreign` is the empty list to use as that
    stack (None for a new one); the self-test passes one that counts the work done on it."""
    lead = len(page) - len(page.lstrip(HTML_WHITESPACE))
    doctype = _DOCTYPE_RE.match(page, lead)
    pos = doctype.end() if doctype else 0
    foreign = [] if foreign is None else foreign
    open_count = dict.fromkeys(FOREIGN_ROOTS, 0)
    while True:
        at = page.find("<", pos)
        ref = _charref_error(page[pos:] if at < 0 else page[pos:at])
        if ref is not None:
            return page.find(ref, pos), "a character reference {!r} this check does not accept".format(ref)
        if at < 0:
            return None
        if page.startswith("<!--", at):
            close = page.find("-->", at + 4)
            if page.startswith("<!-->", at) or page.startswith("<!--->", at):
                return at, "an abrupt comment (\"<!-->\" or \"<!--->\")"
            if close < 0:
                return at, "a comment that never closes"
            if page.find("--!>", at + 2, close + 3) >= 0 or page.find("<!--", at + 4, close + 2) >= 0:
                return at, "a comment holding \"--!>\" or a nested \"<!--\" before its \"-->\""
            pos = close + 3
            continue
        if page.startswith("<!", at) or page.startswith("<?", at):
            return at, "a \"{}\" construct other than a comment (a \"<![\" section, a bogus comment or a " \
                       "doctype after other markup or outside the accepted forms)".format(page[at:at + 2])
        if page.startswith("</", at):
            end = _END_TAG_RE.match(page, at)
            if not end:
                return at, "an end tag that is not a name, optional whitespace and \">\""
            name = end.group(1).lower()
            if open_count.get(name):
                while True:
                    top = foreign.pop()
                    open_count[top] -= 1
                    if top == name:
                        break
            pos = end.end()
            continue
        if not (page[at + 1:at + 2].isascii() and page[at + 1:at + 2].isalpha()):
            pos = at + 1
            continue
        start = _START_TAG_RE.match(page, at)
        if not start:
            return at, "a start tag outside the plain shape this check reads"
        name = start.group(1).lower()
        self_closing = start.group(2) == "/"
        if _TEXT_END_RE.search(start.group(0)):
            return at, "an attribute value holding the end tag of a text element"
        ref = _charref_error(start.group(0))
        if ref is not None:
            return at, "a character reference {!r} this check does not accept".format(ref)
        if foreign and (name in INTEGRATION_POINTS or name in TEXT_CONTENT and name not in ("style", "title")):
            return at, "a <{}> inside svg or math content".format(name)
        if name in TEXT_CONTENT and self_closing:
            return at, "a self-closing <{}/>, whose contents a browser reads on as text".format(name)
        if name in TEXT_CONTENT and start.group(0).endswith("/>"):
            return at, "a <{}> whose last unquoted attribute value ends in \"/\" right before the \">\", " \
                       "which reads as self-closing but leaves the element open".format(name)
        if name == "plaintext":
            return at, "a <plaintext>"
        pos = start.end()
        if name in FOREIGN_ROOTS and not self_closing:
            foreign.append(name)
            open_count[name] += 1
        if name not in RAW_TEXT_ELEMENTS and name != "noscript":
            continue
        end = _RAW_END_RE[name].search(page, pos)
        closing = _END_TAG_RE.match(page, end.start()) if end else None
        if not closing:
            return at, "a <{}> whose text has no plain </{}> end tag".format(name, name)
        if name == "noscript":
            if _TAG_START_RE.search(page, pos, end.start()):
                return at, "a <noscript> whose text holds markup, which a browser running scripts reads as text"
            ref = _charref_error(page[pos:end.start()])
            if ref is not None:
                return at, "a character reference {!r} this check does not accept".format(ref)
        elif _TEXT_END_RE.search(page, pos, end.start()):
            return at, "the text of a <{}> holding the end tag of a text element".format(name)
        elif foreign and "<" in page[pos:end.start()]:
            return at, "a <{}> inside svg or math content whose text holds \"<\"".format(name)
        elif name == "script":
            opener = page.find("<!--", pos, end.start())
            if opener >= 0 and _SCRIPT_START_RE.search(page, opener + 4, end.start()):
                return at, "a <script> whose text holds \"<!--\" and then \"<script\", which a browser " \
                           "reads as double-escaped script text"
        elif name != "style" and _MARKUP_START_RE.search(page, pos, end.start()):
            return at, "the text of a <{}> holding markup-like \"<\"".format(name)
        pos = closing.end()


class _HeadTagCollector(HTMLParser):
    """Record the page's `<head>` start tags, where its head ends, and every `<link>`/`<meta>` element
    carrying a link-preview key, in document order, with that key, its value attribute (a link's href, a
    meta's content), where it sits, its line, and the key exactly as written. The head ends where a
    browser's tree builder ends it: at `</head>`, at a `<body>` start or a `</body>`, `</html>` or
    `</br>` end tag, at the first start tag that is not one of HEAD_ELEMENTS, or at the first text that
    is not HTML whitespace (comments and the raw-text contents of `<script>`, `<style>` and `<title>` do
    not end it; neither does anything else inside a `<template>`). It is meant for pages inside the
    accepted subset _page_syntax_error defines, and adds the refusals that need the head's place, each
    recorded as an error: any start tag but `<html>`, any end tag, or text that is not whitespace before the
    head starts (a browser starts the head at such a start tag or text; it ignores end tags there other
    than `</head>`, `</body>`, `</html>` and `</br>`, so refusing every end tag is a conservative choice,
    broader than needed); a `<noscript>` in the head holding anything but whitespace (a browser running
    scripts reads its contents as text, html.parser as markup, and without scripts a browser ends the head
    at some contents this collector would keep); a `<script>` in the head whose text holds "<!--" (a
    browser's escaped script states can keep it open past its end tag; refused whatever follows the
    "<!--", more broadly than the double-escaped state needs); and a start tag of any TEXT_CONTENT element,
    `<script>` and `<style>` included, inside a `<template>` in the head. That last refusal is broader than
    html.parser 3.14 needs for most of those elements; it is kept because this collector does not follow
    the tree builder's template and svg/math rules, so it does not track whether an svg or math element
    is open there: a template whose svg or math is closed (`<svg></svg>`) or self-closed (`<math/>`)
    before a `<style>` or `<title>` fails the same way as one whose svg or math is still open around it,
    and whether the two readers differ on each such form is not established. Inside the accepted subset
    the differences this check knows of in where the head ends each end it sooner here than in a
    browser's tree builder, none later, though that the list is complete is not established (so a block
    after that point fails as outside the head where a browser would still read it in the head): the
    start tags `<noframes>`, `<basefont>`, `<bgsound>` and `<html>` end it here and not in a
    browser, and `</head>` or `<body>` ends it here even inside a `<template>`. Outside the subset the page
    fails before this collector's places count. Where an element sits is None inside the head, otherwise
    "outside <head>", "inside the raw text of <script> in <head>" (or of `<style>` or `<title>`) for one
    read while such raw text is open (only a self-closing `<script/>`, `<style/>` or `<title/>` leaves it
    open with tags inside, which the accepted subset refuses), or "inside <template> in <head>" for one
    wrapped in a template in the head (a template's contents are not in the document). A link's key is
    ("link", "canonical") when one of its whitespace-separated rel tokens is `canonical`; a meta's keys
    are ("meta", <value>) for its `property` value and its `name` value, so a `name="og:title"` meta still
    collides with the block's `property="og:title"`. Key values compare stripped and lowercased, so a case
    variant of a block tag counts as a second occurrence; the written forms behind each key are kept so the
    check can require every spelling of the key on the tag that counts to be the block's exact one. `keys`
    limits recording to those keys (None records every key). An element carrying a duplicate attribute is
    an error: a browser keeps the first and this parser the last, so they would disagree on the tag's
    value."""

    def __init__(self, keys):
        super().__init__(convert_charrefs=True)
        self.keys = keys
        self.heads = 0
        self.head_open = False  # between the head's start and its explicit end (`</head>` or `<body>`)
        self.in_head = False    # inside the head as a browser builds it (may end before the explicit end)
        self.head_ended = False
        self.body_seen = False
        self.raw_text = None    # the open raw-text head element, while inside one
        self.script_text = []   # the text of the open head <script>, while inside one
        self.wrappers = []      # the open <template>/<noscript> elements inside the head
        self.open_wrappers = dict.fromkeys(("template", "noscript"), 0)  # how many of each are in wrappers
        self.found = []    # (key, value, place, line, written keys)
        self.errors = []

    def _before_head(self, line, what):
        """Refuse markup before the head starts: a start tag or text there starts a browser's head (or
        body) itself, so the page's own <head> would not be the head a browser builds; end tags are
        refused too, more broadly than needed, as a conservative choice."""
        if not self.heads and not self.body_seen:
            self.errors.append("line {}: {} before <head>, which this check refuses".format(line, what))

    def _in_head_noscript(self, line, what):
        """Refuse anything but whitespace inside a <noscript> in the head: a browser running scripts reads
        it as text, html.parser as markup."""
        if self.in_head and self.wrappers and self.wrappers[-1] == "noscript":
            self.errors.append("line {}: {} inside a <noscript> in <head>, which must hold only whitespace"
                               .format(line, what))

    def _end_head(self):
        self.in_head = False
        self.raw_text = None
        self.wrappers = []
        self.open_wrappers = dict.fromkeys(("template", "noscript"), 0)

    def handle_starttag(self, tag, attrs):
        line = self.getpos()[0]
        if tag not in ("html", "head", "body"):
            self._before_head(line, "a <{}> start tag".format(tag))
        self._in_head_noscript(line, "a <{}> start tag".format(tag))
        if tag == "head":
            self.heads += 1
            if self.body_seen:
                self.errors.append("line {}: <head> starts after <body>".format(line))
            elif self.heads == 1:
                self.head_open = self.in_head = True
            return
        if tag == "body":
            self.body_seen = True
            if self.head_open:
                self.head_open = False
                self.head_ended = True
                self._end_head()
            return
        if self.in_head and not self.raw_text:
            if not self.open_wrappers["template"]:
                if tag not in HEAD_ELEMENTS:
                    self._end_head()
                    return
                if tag in HEAD_RAW_TEXT:
                    self.raw_text = tag
                    self.script_text = []
            elif tag in TEXT_CONTENT:
                # Every element whose text a browser reads as text fails here, script and style included:
                # this check does not follow the tree builder's template and svg/math rules, so it does not
                # track which svg or math element is open, closed or self-closed before such an element.
                self.errors.append("line {}: a <{}> inside a <template> in <head>, whose text this check "
                                   "does not follow".format(line, tag))
            if tag in ("template", "noscript"):
                self.wrappers.append(tag)
                self.open_wrappers[tag] += 1
        if tag not in ("link", "meta"):
            return
        names = [k.lower() for k, _v in attrs]
        values = {k.lower(): (v or "") for k, v in attrs}
        keys = []
        written = {}
        if tag == "link":
            for token in values.get("rel", "").split():
                if token.lower() == "canonical":
                    if ("link", "canonical") not in keys:
                        keys.append(("link", "canonical"))
                    written.setdefault(("link", "canonical"), []).append(token)
            value = values.get("href")
        else:
            for attr in ("property", "name"):
                if attr in values:
                    key = ("meta", values[attr].strip().lower())
                    if key not in keys:
                        keys.append(key)
                    written.setdefault(key, []).append(values[attr])
            value = values.get("content")
        keys = [k for k in keys if self.keys is None or k in self.keys]
        if not keys:
            return
        if len(set(names)) != len(names):
            dup = next(n for n in names if names.count(n) > 1)
            self.errors.append(
                "line {}: a <{}> carrying a link-preview key has a duplicate {!r} attribute"
                .format(line, tag, dup))
            return
        if not self.in_head:
            place = "outside <head>"
        elif self.raw_text:
            # Only a self-closing <script/>, <style/> or <title/> lets a tag through here: a browser ignores
            # the slash and reads everything up to the matching end tag as text, so this is not an element.
            place = "inside the raw text of <{}> in <head>".format(self.raw_text)
        elif self.wrappers:
            place = "inside <{}> in <head>".format("template" if self.open_wrappers["template"] else "noscript")
        else:
            place = None
        for key in keys:
            self.found.append((key, value, place, line, tuple(written[key])))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        self._before_head(self.getpos()[0], "a </{}> end tag".format(tag))
        if tag != "noscript":
            self._in_head_noscript(self.getpos()[0], "a </{}> end tag".format(tag))
        if tag == "head" and self.head_open:
            self.head_open = False
            self.head_ended = True
            self._end_head()
            return
        if not self.in_head:
            return
        if self.raw_text:
            if tag == self.raw_text:
                # "<!--" in a head script's text can start a browser's escaped script states, which this
                # parser does not follow, so such a script fails here whatever follows the "<!--".
                if tag == "script" and "<!--" in "".join(self.script_text):
                    self.errors.append("line {}: a <script> in <head> holds \"<!--\", whose escaped script "
                                       "text this check does not follow".format(self.getpos()[0]))
                self.raw_text = None
                self.script_text = []
            return
        if tag in ("template", "noscript") and self.open_wrappers[tag]:
            # One end tag can pop several wrappers, but each is popped once: linear in total, amortized
            # constant per tag.
            while True:
                top = self.wrappers.pop()
                self.open_wrappers[top] -= 1
                if top == tag:
                    break
            return
        if self.open_wrappers["template"]:
            return
        if tag in ("body", "html", "br"):
            self._end_head()

    def handle_data(self, data):
        if data.strip(HTML_WHITESPACE):
            self._before_head(self.getpos()[0], "text")
            self._in_head_noscript(self.getpos()[0], "text")
        if self.raw_text == "script":
            self.script_text.append(data)
        if self.in_head and not self.raw_text and not self.open_wrappers["template"] \
                and data.strip(HTML_WHITESPACE):
            self._end_head()

    def handle_comment(self, data):
        self._in_head_noscript(self.getpos()[0], "a comment")


def _key_text(key):
    """A block key as the attribute that names it, for messages: rel="canonical" or "og:title"."""
    return 'rel="canonical"' if key == ("link", "canonical") else '"{}"'.format(key[1])


def _head_block_spec(url):
    """(key, exact key, value, markup) for each HEAD_TAG_BLOCK tag, in block order, with `url` in the
    `{url}` slots; the exact key is the key as the block writes it. Read from the block's own markup by
    the same collector the page check uses, so the block constant stays the single statement of what a
    page must carry."""
    spec = []
    for tag in HEAD_TAG_BLOCK:
        own = tag.format(url=url)
        collector = _HeadTagCollector(None)
        collector.feed("<head>" + own)
        collector.close()
        if len(collector.found) != 1 or collector.errors or len(collector.found[0][4]) != 1:
            raise SchemaError("internal: HEAD_TAG_BLOCK entry {!r} does not carry exactly one key"
                              .format(own))
        key, value, _place, _line, written = collector.found[0]
        spec.append((key, written[0], value, own))
    if len({s[0] for s in spec}) != len(spec):
        raise SchemaError("internal: HEAD_TAG_BLOCK names one key twice")
    return spec


def _validate_head_tags(page, url, target_rel):
    """Validate that a page's head carries the link-preview tag block exactly, by parsing the page
    (html.parser, character references decoded). The page must have exactly one `<head>` start tag, and
    that head must end (at `</head>` or a `<body>` start) before the document does. For each tag of
    HEAD_TAG_BLOCK, keyed by a link's rel="canonical" or a meta's property or name value (compared
    stripped and lowercased): exactly one `<link>`/`<meta>` element in the whole page, as html.parser
    reads it, carries that key, whatever its value; that element sits directly in the head, as
    _HeadTagCollector places it, not inside a `<template>` there (such a tag still counts toward "exactly
    one", so a hidden copy beside the real one fails as a duplicate, which is stricter than a browser).
    These hold as a browser's tokenizer reads the page only inside the accepted subset _page_syntax_error
    defines; a page outside it fails before anything is counted, and inside it each difference known in
    where the head ends makes it end here sooner than in a browser (see _HeadTagCollector); no known one
    ends it later, and that none exists is not established. Every spelling of its key on it is
    exactly the block's (no case change, no surrounding whitespace, so a meta's property and name, when
    both carry the key, are both written that way; for a link, each rel token that names canonical is
    written `canonical` in lower case, which is stricter than HTML, where rel tokens compare ASCII
    case-insensitively; other rel tokens may sit beside it); its value (a link's
    href, a meta's content) equals the block's value exactly, with `url` (the page's own URL) in the
    canonical href and og:url slots; and the block's tags appear in HEAD_TAG_BLOCK's order. So a second
    element with a block tag's key fails whatever its value and wherever it sits, a canonical or og:url
    naming another page fails as a wrong value, and a block tag after the point where the collector ends
    the head (`</head>`, `<body>`, a start tag that is not a head element, or text that is not whitespace)
    fails as outside the head. A page the parser rejects, a page with no head or more than one, a head
    that never ends, a keyed element carrying a duplicate attribute, a page outside the accepted subset
    (_page_syntax_error) or a refusal of _HeadTagCollector fails too; every failure names
    `target_rel`. Not established: that the block's tags are adjacent (other head elements may sit
    between them), anything about elements carrying none of the block's keys, whether a meta's key sits
    in `property` or `name`, attributes beyond the key and the value, and tag-like text inside comments,
    `<script>`, `<style>` or `<title>` (not elements, so neither counted nor flagged, except where the
    accepted subset or _HeadTagCollector refuses it: a self-closing `<script/>`, `<style/>` or `<title/>`
    anywhere, a `<script>` whose text holds "<!--" and then "<script", a `<script>` in the head whose text
    holds "<!--", and an svg or math `<style>` or `<title>` whose text holds "<", fail)."""
    spec = _head_block_spec(url)
    outside = _page_syntax_error(page)
    if outside is not None:
        raise SchemaError("{}: {}, outside the page syntax this check accepts".format(target_rel, outside))
    collector = _HeadTagCollector({s[0] for s in spec})
    try:
        collector.feed(page)
        collector.close()
    except (ValueError, AssertionError) as exc:
        raise SchemaError("{}: cannot parse the page to check its head tags: {}".format(target_rel, exc))
    if collector.heads != 1:
        raise SchemaError("{}: page must have exactly one <head>, found {}"
                          .format(target_rel, collector.heads))
    if collector.errors:
        raise SchemaError("{}: {}".format(target_rel, collector.errors[0]))
    if not collector.head_ended:
        raise SchemaError("{}: <head> never ends (no </head> or <body>)".format(target_rel))
    positions = {}
    for index, (key, value, place, line, written) in enumerate(collector.found):
        positions.setdefault(key, []).append((index, value, place, line, written))
    previous = None
    for key, exact, value, own in spec:
        hits = positions.get(key, [])
        if len(hits) != 1:
            raise SchemaError(
                "{}: page must carry exactly one element keyed {} (block tag {!r}), found {}{}"
                .format(target_rel, _key_text(key), own, len(hits),
                        " at lines " + ", ".join(str(h[3]) for h in hits) if hits else ""))
        index, got, place, line, written = hits[0]
        if place is not None:
            raise SchemaError("{}:{}: block tag keyed {} sits {}".format(target_rel, line, _key_text(key),
                                                                         place))
        other = next((w for w in written if w != exact), None)
        if other is not None:
            raise SchemaError("{}:{}: block tag keyed {} writes its key as {!r}, expected exactly {!r}"
                              .format(target_rel, line, _key_text(key), other, exact))
        if got != value:
            raise SchemaError(
                "{}:{}: block tag keyed {} carries {!r}, expected exactly {!r} (its own URL is {})"
                .format(target_rel, line, _key_text(key), got, value, url))
        if previous is not None and index < previous[0]:
            raise SchemaError(
                "{}:{}: block tag keyed {} comes before {} (line {}); the block's order is "
                "HEAD_TAG_BLOCK's".format(target_rel, line, _key_text(key), _key_text(previous[1]),
                                          previous[2]))
        previous = (index, key, line)

# Sidebar active-link map: slug -> the exact href the shell's `<a>` carries. A frontmatter
# `sidebar-active` naming a slug outside this map is a SchemaError, so a typo cannot silently produce
# a page with no active link. The map covers every INTERNAL page slug the shell's sidebar carries;
# external navlinks (e.g. the shell's `View on GitHub` link) are exempt from this map and reconciled
# separately by _validate_shell_sidebar. Extending the sidebar with a new page is a shell edit plus a
# matching entry here; the shell-load-time reconciliation surfaces the drift both ways.
SIDEBAR_SLUG_TO_HREF = {
    "home": "/",
    "install": "/install",
    "install-claude": "/install#claude",
    "install-chatgpt": "/install#chatgpt",
    "install-gemini": "/install#gemini",
    "install-copilot": "/install#copilot",
    "install-copilotstudio": "/install#copilotstudio",
    "install-other": "/install#other",
    "development": "/development",
    "tech-details": "/tech-details",
    "teams": "/teams",
    "learn": "/learn",
    "standard": "/standard",
    "mappings": "/mappings",
    "examples": "/examples",
    "roadmap": "/roadmap",
    "evidence": "/evidence",
    "rules": "/rules",
    "about": "/about",
    "disclosure": "/disclosure",
}


# Disallowed control characters: the C0 controls (U+0000-U+001F) except tab (U+0009), line feed
# (U+000A), and carriage return (U+000D), plus DEL (U+007F). These are anomalous in maintainer-authored
# frontmatter and body text; they pass through HTML parsing as NULL-substitution or parse errors
# (per the HTML/CommonMark specs), so they are rejected at the field/body boundary rather than emitted
# raw into the page. Tab/LF/CR are legitimate whitespace and are left to the normal escaping path.
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class SchemaError(Exception):
    """A source frontmatter or body that this generator cannot render; reported at exit 2."""


def _reject_controls(value, where, source_rel):
    """Raise SchemaError if `value` carries a disallowed control character. `where` names the boundary
    (a frontmatter field name or 'body') for the error message."""
    m = _CONTROL_RE.search(value)
    if m:
        raise SchemaError(
            "{}: {} contains a disallowed control character (U+{:04X})"
            .format(source_rel, where, ord(m.group(0))))


def split_frontmatter(source_text, source_rel):
    """Split a `+++`-fenced TOML frontmatter from the body of a source .md.

    The source MUST open with a line consisting of exactly `+++` (no trailing whitespace, no leading
    BOM), followed by TOML lines, followed by another `+++` line. Everything after the closing fence
    is the body. Missing fences, an unclosed frontmatter, or a fence not on its own line all raise
    SchemaError (fail-closed exit 2): a source that looks like it has frontmatter but does not close
    it is rejected rather than silently promoted to an all-body file with no metadata.
    """
    lines = source_text.split("\n")
    if not lines or lines[0] != FENCE:
        raise SchemaError("{}: expected frontmatter to open with '{}'".format(source_rel, FENCE))
    for idx in range(1, len(lines)):
        if lines[idx] == FENCE:
            fm_text = "\n".join(lines[1:idx])
            # Body: everything after the closing fence's newline. If the closing fence is followed by a
            # blank line (typical Markdown convention), strip that one leading blank line so the body
            # sits flush.
            body_lines = lines[idx + 1:]
            while body_lines and body_lines[0] == "":
                body_lines.pop(0)
            # Trailing blank lines are stripped so the substitution into `\n\n{{content}}\n\n` in the
            # shell composes to exactly one blank line on each side, not two.
            while body_lines and body_lines[-1] == "":
                body_lines.pop()
            return fm_text, "\n".join(body_lines)
    raise SchemaError("{}: frontmatter was opened but never closed with '{}'".format(source_rel, FENCE))


def parse_frontmatter(fm_text, source_rel):
    """Parse the frontmatter TOML string; validate required and optional keys.

    An unparseable TOML, a missing required key, a wrong-type value, or an unexpected key is a
    SchemaError (fail-closed exit 2), so a typo in a frontmatter key never silently substitutes an
    empty value into the shell.
    """
    try:
        data = tomllib.loads(fm_text)
    # ValueError and RecursionError too: tomllib raises a BARE ValueError (not TOMLDecodeError) on an
    # integer literal past CPython's 4300-digit int-string limit, and a RecursionError (a RuntimeError)
    # on a deeply nested array or inline table; neither is a TOMLDecodeError.
    except (tomllib.TOMLDecodeError, ValueError, RecursionError, MemoryError) as exc:
        raise SchemaError("{}: frontmatter is not valid TOML: {}".format(source_rel, exc))
    known = set(REQUIRED_FIELDS) | set(OPTIONAL_FIELDS)
    unknown = sorted(k for k in data if k not in known)
    if unknown:
        raise SchemaError("{}: frontmatter has unknown key(s): {}".format(source_rel, ", ".join(unknown)))
    for field in REQUIRED_FIELDS:
        if field not in data:
            raise SchemaError("{}: frontmatter is missing required key '{}'".format(source_rel, field))
        if not isinstance(data[field], str):
            raise SchemaError("{}: frontmatter key '{}' must be a string".format(source_rel, field))
        _reject_controls(data[field], "frontmatter key '{}'".format(field), source_rel)
    if "hand-authored" in data and not isinstance(data["hand-authored"], bool):
        raise SchemaError("{}: frontmatter key 'hand-authored' must be a bool".format(source_rel))
    slug = data["sidebar-active"]
    if slug and slug not in SIDEBAR_SLUG_TO_HREF:
        raise SchemaError("{}: frontmatter 'sidebar-active' = {!r} is not a known sidebar slug"
                          .format(source_rel, slug))
    return data


def _escape_field(value, sink):
    """HTML-escape a frontmatter string for its shell sink. `text` is content position (inside a
    `<title>...</title>` or visible text node); `attr` is quoted-attribute position (inside
    `content="..."` or `href="..."`), which additionally escapes `"` and `'` so a value cannot
    escape its surrounding attribute quotes. Unrecognized sinks are a programming error, not a
    runtime input, so they fail loudly rather than default-open."""
    if sink == "text":
        return html.escape(value, quote=False)
    if sink == "attr":
        return html.escape(value, quote=True)
    raise SchemaError("internal: unknown escape sink {!r}".format(sink))


# ATX heading: `# ` up to `### `. The count of leading `#` is the heading level. A hash-only line is
# NOT a heading; a trailing space between the hashes and the text is required (CommonMark).
_ATX_HEADING = re.compile(r"^(#{1,3})[ \t]+(.+?)[ \t]*#*[ \t]*$")

# An HTML block opener at column 0: `<` immediately followed by a tag-like character (a letter, `/`,
# `!`, or `?`). Used to reject a continuation line that would interrupt a paragraph with an HTML block
# (CommonMark type-6). A bare `<` followed by a space or other non-tag character is ordinary paragraph
# text, not an opener, so `text\n< 5` folds into the paragraph rather than failing loud.
_HTML_BLOCK_OPENER = re.compile(r"^<[A-Za-z/!?]")

# CommonMark §4.6 type-1 HTML block opener: a line whose first characters at column 0 are one of the
# `<script>`, `<pre>`, `<style>`, or `<textarea>` open-tag forms. The tag name is followed by a
# whitespace character, `>`, or end of line. Once entered, the block continues through blank lines
# until any line contains the matching close tag (case-insensitive).
_TYPE1_OPEN = re.compile(r"^<(script|pre|style|textarea)(?:[\s>]|$)", re.IGNORECASE)

# CommonMark §2.1: a blank line is a line containing no characters, or a line containing only spaces
# (U+0020) or tabs (U+0009). This is stricter than `line == ""` and matches the paragraph-separator
# definition CommonMark uses in §4.9.
_BLANK = re.compile(r"^[ \t]*$")

# Placeholder token: `{{name}}` where `name` is `[\w-]+`. Frontmatter substitution runs a single-pass
# `re.sub` over the shell so a substituted value is never re-scanned for another token.
_TOKEN_RE = re.compile(r"\{\{([\w-]+)\}\}", re.ASCII)

class _NavlinkCollector(HTMLParser):
    """Collect every `<a>` whose class list contains the `navlink` token, independent of attribute
    order, quote style, whitespace, and case. A navlink MUST carry exactly one non-empty `href`;
    anything else (zero, multiple, or empty) is recorded as an error so a malformed navlink cannot
    slip past the reconciliation the way a single fixed byte-layout regex allowed."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.hrefs = []
        self.errors = []

    def handle_starttag(self, tag, attrs):
        if tag != "a":
            return
        seen = set()
        for key, _value in attrs:
            key_l = key.lower()
            if key_l in seen:
                self.errors.append(
                    "an anchor carries a duplicate {!r} attribute; a browser keeps the first and this "
                    "parser the last, so they would disagree on the rendered link".format(key_l))
                return
            seen.add(key_l)
        classes = None
        hrefs = []
        for key, value in attrs:
            key_l = key.lower()
            if key_l == "class":
                classes = value or ""
            elif key_l == "href":
                hrefs.append(value)
        if classes is None or "navlink" not in classes.split():
            return
        if len(hrefs) != 1 or not hrefs[0]:
            self.errors.append(
                "a navlink carries {} href attribute(s), expected exactly one non-empty href"
                .format(len(hrefs)))
            return
        self.hrefs.append(hrefs[0])


class _TokenContextCollector(HTMLParser):
    """Record, for each `{{name}}` token in the shell, the HTML context(s) it parses into: `attr` when
    it sits inside an attribute value, `text` when it sits in element text. Used to validate a field
    token's ACTUAL position against its declared sink so a shell edit cannot relocate a text-sink
    token into an attribute (under-escaped) context unnoticed."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.contexts = {}

    def _record(self, source, ctx):
        for m in _TOKEN_RE.finditer(source):
            self.contexts.setdefault(m.group(1), set()).add(ctx)

    def handle_starttag(self, tag, attrs):
        for _key, value in attrs:
            if value:
                self._record(value, "attr")

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_data(self, data):
        self._record(data, "text")


def _escape_text(value):
    """Escape a Markdown paragraph or heading's inline text for HTML content position. Bounded subset:
    this is the SAFE encoding for text nodes, not a full CommonMark inline transformer. Angle brackets
    are escaped so the reader sees them; ampersand is escaped so `Foo & Bar` becomes `Foo &amp; Bar`.
    """
    return html.escape(value, quote=False)


def _split_into_blocks(body):
    """Split body into blocks, respecting CommonMark type-1 HTML block continuation through blank
    lines. A type-1 block (`<script>`, `<pre>`, `<style>`, `<textarea>`) at column 0 opens a block
    that runs until a line contains its matching close tag, preserving its internal blank lines
    byte-exact. Every other block is delimited by a blank line (space/tab-only per §2.1).
    """
    lines = body.split("\n")
    blocks = []
    buf = []
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        m = _TYPE1_OPEN.match(line)
        if m:
            if buf:
                blocks.append("\n".join(buf))
                buf = []
            tag = m.group(1).lower()
            close_re = re.compile(r"</" + re.escape(tag) + r"\s*>", re.IGNORECASE)
            block_lines = [line]
            if close_re.search(line):
                blocks.append("\n".join(block_lines))
                i += 1
                continue
            i += 1
            while i < n:
                block_lines.append(lines[i])
                if close_re.search(lines[i]):
                    i += 1
                    break
                i += 1
            blocks.append("\n".join(block_lines))
            continue
        if _BLANK.match(line):
            if buf:
                blocks.append("\n".join(buf))
                buf = []
            i += 1
            continue
        buf.append(line)
        i += 1
    if buf:
        blocks.append("\n".join(buf))
    return blocks


def md_to_html(body, source_rel="<source>"):
    """Convert the bounded Markdown subset to an HTML content fragment.

    Blocks are separated by blank lines (`[ \\t]*$` per CommonMark §2.1). Each block is either an
    HTML block (starts at column 0 with `<`), an ATX heading (`#` `##` `###`), an hr (`---` on its
    own line), or a paragraph (all other text lines). An HTML block is passed through verbatim,
    preserving every byte so a page authored as raw HTML today round-trips byte-exact. Type-1 HTML
    blocks (script, pre, style, textarea) continue through blank lines until the matching close tag.

    Residuals: fenced code, inline formatting (`*em*`, `**strong**`, `[link](url)`, backticks), lists,
    blockquotes, and setext headings are NOT parsed today. A page that uses them will render literal
    text through _escape_text rather than rich HTML. The rule pages, the pilot's target corpus, do
    not use them; adding them is the next slice of this generator, gated by that page-set landing.
    Non-HTML blocks (heading, hr, paragraph) have their trailing whole-block whitespace stripped
    before classification.
    """
    if body == "":
        return ""
    _reject_controls(body, "body", source_rel)
    raw_blocks = _split_into_blocks(body)
    out_blocks = []
    for block in raw_blocks:
        # HTML block: pass through verbatim, preserving internal blank lines and trailing whitespace.
        if block.startswith("<"):
            out_blocks.append(block)
            continue
        # Non-HTML block: normalize whole-block trailing whitespace before classification. Only ASCII
        # space/tab/newline is stripped so non-ASCII whitespace (e.g. a trailing U+00A0) stays content
        # rather than being silently deleted.
        block = block.rstrip(" \t\n")
        if block == "":
            continue
        if block == "---":
            out_blocks.append("<hr>")
            continue
        match = _ATX_HEADING.match(block)
        if match and "\n" not in block:
            level = len(match.group(1))
            text = _escape_text(match.group(2))
            out_blocks.append("<h{level}>{text}</h{level}>".format(level=level, text=text))
            continue
        # A non-HTML block whose FIRST line is text but a later line opens an HTML block at column 0
        # is a CommonMark type-6 paragraph interruption. This bounded converter does not merge the two
        # (that would need full type-1..6 start/end handling); rather than silently escape the HTML
        # opener into the paragraph text (a silent-wrong render), it fails closed and directs the
        # author to separate the blocks with a blank line.
        for ln in block.split("\n"):
            if _HTML_BLOCK_OPENER.match(ln):
                raise SchemaError(
                    "{}: a continuation line beginning with an HTML tag ({!r}) cannot interrupt a "
                    "paragraph in this bounded converter; separate the HTML block from the preceding "
                    "text with a blank line".format(source_rel, ln[:40]))
        # Paragraph: fold internal newlines into single spaces, escape, wrap in <p>. Only ASCII
        # space/tab is stripped per line so non-ASCII whitespace (e.g. U+00A0, U+2028) stays content
        # rather than being silently deleted.
        text = _escape_text(" ".join(part.strip(" \t") for part in block.split("\n")))
        out_blocks.append("<p>{}</p>".format(text))
    return "\n\n".join(out_blocks)


def _mark_active(shell, slug, source_rel):
    """Rewrite the sidebar's `<a class="navlink" href="/<slug>">` link to carry ` active` in its
    class and an `aria-current="page"` attribute, matching how the pre-migration hand-authored rule
    pages marked the current-page link. Relies on the shell having been sidebar-reconciled
    (_validate_shell_sidebar) so the pattern is guaranteed present exactly once; the local check
    below is defence-in-depth for a shell reused outside main() (e.g. in the self-test).
    """
    if not slug:
        return shell
    href = SIDEBAR_SLUG_TO_HREF[slug]  # verified in parse_frontmatter
    pattern = '<a class="navlink" href="{}">'.format(href)
    replacement = '<a class="navlink active" href="{}" aria-current="page">'.format(href)
    if pattern not in shell:
        raise SchemaError(
            "{}: cannot mark sidebar-active {!r}: shell has no `{}` link"
            .format(source_rel, slug, pattern))
    return shell.replace(pattern, replacement, 1)


def _validate_shell_placeholders(shell, shell_rel):
    """Validate that the shell carries each expected placeholder token exactly once and no unknown
    token.

    Expected tokens = every entry in FIELD_TO_TOKEN plus `{{content}}`. A missing token would silently
    omit its content; a duplicate would duplicate the substitution; an unknown token would ship a
    literal `{{foo}}` to the site. All three are SchemaErrors so a shell edit that drops, duplicates,
    or misnames a token surfaces at load time rather than during rendering, and the render step no
    longer needs a post-hoc leftover-placeholder guard (which false-triggered on body prose that
    legitimately carried braces).
    """
    expected = set(t for (t, _) in FIELD_TO_TOKEN.values()) | {CONTENT_TOKEN}
    # Reject any moustache opener `{{` that is not the exact start of a well-formed `{{name}}` token.
    # _TOKEN_RE (and the substitution pass) only recognize `{{[\w-]+}}`, so a malformed shape such as
    # `{{ foo }}`, `{{foo.bar}}`, `{{}}`, `{{title!}}`, or an unclosed `{{` is otherwise neither
    # substituted nor rejected and ships literally to the site. Keying off the opener (not `}}`) keeps
    # legitimate non-token braces (e.g. `{}` in inline script) from false-triggering.
    for m in re.finditer(r"\{\{", shell):
        if _TOKEN_RE.match(shell, m.start()) is None:
            near = shell[m.start():m.start() + 24].split("\n", 1)[0]
            raise SchemaError(
                "{}: shell carries a malformed placeholder near {!r} (expected `{{{{name}}}}` with "
                "name matching [A-Za-z0-9_-]+)".format(shell_rel, near))
    counts = {}
    for m in _TOKEN_RE.finditer(shell):
        tok = m.group(0)
        counts[tok] = counts.get(tok, 0) + 1
    for token in sorted(expected):
        c = counts.get(token, 0)
        if c == 0:
            raise SchemaError(
                "{}: shell is missing required placeholder {}".format(shell_rel, token))
        if c > 1:
            raise SchemaError(
                "{}: shell carries {} occurrences of {}, expected exactly one"
                .format(shell_rel, c, token))
    for token in sorted(counts):
        if token not in expected:
            raise SchemaError(
                "{}: shell carries unknown placeholder {}".format(shell_rel, token))


def _validate_token_sinks(shell, shell_rel):
    """Validate that each field token sits in the HTML context its declared sink assumes.

    `_escape_field` escapes a value for its declared sink (text escapes `&<>`; attr additionally
    escapes `"'`). If a shell edit relocates a text-sink token (e.g. `{{title}}`) into an attribute
    value, the value would be under-escaped and could break out of its attribute. This parses the
    shell and confirms each field token's ACTUAL parsed context matches its declared sink; a mismatch,
    or a token whose context cannot be observed (e.g. it sits in a comment or an unhandled position),
    is a SchemaError (fail-closed). `{{content}}` has no sink (body HTML inserted verbatim) and is not
    checked here.
    """
    collector = _TokenContextCollector()
    collector.feed(shell)
    collector.close()
    for _field, (token, sink) in FIELD_TO_TOKEN.items():
        name = token[2:-2]
        seen = collector.contexts.get(name, set())
        if seen != {sink}:
            raise SchemaError(
                "{}: token {} is declared for the {!r} sink but parses in context {} in the shell; "
                "its escaping would not match its actual HTML position"
                .format(shell_rel, token, sink, sorted(seen) or "none observed"))


def _validate_shell_sidebar(shell, shell_rel):
    """Reconcile the shell's sidebar navlink set against SIDEBAR_SLUG_TO_HREF.

    Every `<a>` whose class list contains `navlink` is parsed (via HTMLParser, so attribute order,
    quote style, whitespace, and case do not matter and a malformed navlink cannot hide behind an
    alternate byte layout). Each navlink MUST carry exactly one non-empty href. Every mapped href
    MUST appear exactly once as a navlink (drift in an unselected slug never surfaces during rendering
    otherwise). Every INTERNAL navlink href (starting with `/`) MUST be in the map (a shell edit that
    adds an internal link cannot silently produce a page whose slug list fell out of sync). EXTERNAL
    navlinks (starting with `http://` or `https://`) are exempt: the shell's `View on GitHub` link is
    external and not a page slug.
    """
    collector = _NavlinkCollector()
    collector.feed(shell)
    collector.close()
    if collector.errors:
        raise SchemaError("{}: {}".format(shell_rel, collector.errors[0]))
    hrefs = collector.hrefs
    counts = {}
    for h in hrefs:
        counts[h] = counts.get(h, 0) + 1
    mapped_hrefs = set(SIDEBAR_SLUG_TO_HREF.values())
    for slug, href in SIDEBAR_SLUG_TO_HREF.items():
        c = counts.get(href, 0)
        if c == 0:
            raise SchemaError(
                "{}: shell has no navlink for mapped slug {!r} (href {})"
                .format(shell_rel, slug, href))
        if c > 1:
            raise SchemaError(
                "{}: shell carries {} navlinks for slug {!r} (href {}), expected exactly one"
                .format(shell_rel, c, slug, href))
    for href in sorted(counts):
        if href.startswith(("http://", "https://")):
            continue
        if href not in mapped_hrefs:
            raise SchemaError(
                "{}: shell has internal navlink {} not in SIDEBAR_SLUG_TO_HREF"
                .format(shell_rel, href))


def render_page(shell, source_text, source_rel):
    """Render one page: split frontmatter, convert body, substitute into the shell, mark active nav.

    The shell is expected to have been validated by _validate_shell_placeholders and
    _validate_shell_sidebar in main(); render_page assumes those checks passed. All substitutions
    (both frontmatter tokens and `{{content}}`) run in ONE `_TOKEN_RE.sub` pass over the shell so a
    substituted value cannot be re-scanned for another token: `re.sub` walks the ORIGINAL shell
    left-to-right and appends each replacement to the output without re-scanning it, so a title of
    `See {{og_title}}` stays literal after substitution, a body carrying `{{name}}` prose is inert,
    and a description value carrying a literal `{{content}}` is not re-inflated by body content.
    """
    fm_text, body = split_frontmatter(source_text, source_rel)
    data = parse_frontmatter(fm_text, source_rel)
    body_html = md_to_html(body, source_rel)
    # Substitution map keyed by token NAME (the `{{name}}` capture group). Frontmatter values are
    # escaped for the sink their token sits in; `content` inserts the body HTML fragment verbatim
    # (md_to_html has already escaped its inline text). Both go through the same single-pass sub.
    substitutions = {"content": body_html}
    for field, (token, sink) in FIELD_TO_TOKEN.items():
        name = token[2:-2]  # strip `{{` and `}}`
        substitutions[name] = _escape_field(data[field], sink)

    def repl(m):
        name = m.group(1)
        if name in substitutions:
            return substitutions[name]
        # Shell was validated upstream; an unknown token at this point is a bug in the validator.
        raise SchemaError(
            "{}: shell carries unknown placeholder {} at render time"
            .format(source_rel, m.group(0)))

    page = _TOKEN_RE.sub(repl, shell)
    page = _mark_active(page, data["sidebar-active"], source_rel)
    return page


def _bound_under(path, root, label, root_label):
    """Verify `path.resolve()` stays under `root.resolve()`. Raise SchemaError if it escapes. The
    `strict=False` on resolve tolerates a missing final component (the pre-write target), while any
    ancestor symlink is resolved so a symlinked ancestor cannot silently redirect the check."""
    resolved = path.resolve(strict=False)
    root_resolved = root.resolve()
    try:
        resolved.relative_to(root_resolved)
    except ValueError:
        raise SchemaError(
            "{} {} resolves outside {} (resolved: {})".format(label, path, root_label, resolved))


def _validate_source_path(src, source_rel, docs_root):
    """Reject a declared source that is missing, non-regular, symlinked, or resolves outside docs/.

    A committed symlink under docs/ is refused because reading it would pull an out-of-tree file into
    site/*.html. `..` in the source path is caught by the `_bound_under` check on the resolved path;
    that check runs FIRST so a path that resolves outside docs/ surfaces its escape rather than
    misreading as a missing regular file.
    """
    _bound_under(src, docs_root, "source", DOCS_DIR + "/")
    if src.is_symlink():
        raise SchemaError(
            "declared source {} is a symbolic link; refusing to follow".format(source_rel))
    if not src.exists():
        raise SchemaError("declared source {} is missing".format(source_rel))
    if not src.is_file():
        raise SchemaError("declared source {} is not a regular file".format(source_rel))


def _validate_target_path(target, target_rel, site_root):
    """Reject a target that is a symlink or resolves outside site/. A symlinked target would redirect
    the write outside the pinned site/ root; a target whose resolved path escapes site/ is refused
    even if the direct filename is fine (an ancestor could be symlinked)."""
    if target.is_symlink():
        raise SchemaError(
            "target {} is a symbolic link; refusing to overwrite".format(target_rel))
    _bound_under(target, site_root, "target", SITE_DIR + "/")


def _declared_md_sources():
    """Return the per-entry (docs/*.md source, site/*.html target) declared in GENSRC_OUTPUTS.

    Each entry MUST declare exactly one docs/*.md source; a structural bug in the declaration (zero,
    two, or a non-docs source) fails closed here rather than being resolved by heuristic at run time.
    """
    result = []
    for entry in GENSRC_OUTPUTS:
        md = [s for s in entry["sources"]
              if s.endswith(".md") and s.startswith(DOCS_DIR + "/")]
        if len(md) != 1:
            raise SchemaError(
                "GENSRC_OUTPUTS entry for {} must declare exactly one {}/*.md source, has {}"
                .format(entry["target"], DOCS_DIR, len(md)))
        result.append((md[0], entry["target"]))
    return result


def _read(path):
    """UTF-8 read; fail-closed exit 2 on any read error (matches reconcile's fail-closed shape)."""
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        print("error: cannot read {} ({}); fail-closed".format(path, exc), file=sys.stderr)
        raise SystemExit(2)


def _discover_md(docs_root, root):
    """Recursively enumerate every docs/**/*.md, failing CLOSED on any subtree it cannot read.

    Uses an explicit os.scandir walk rather than Path.rglob: rglob silently skips a symlinked directory
    and swallows a permission error on an unreadable subtree, so a stray under either would vanish
    instead of failing loud (a check-fails-closed violation). A symlinked directory is refused outright
    (following it would pull an out-of-tree subtree into discovery); a listing or stat error is raised as
    a SchemaError so main() maps it to exit 2. A .md that is itself a symlink is returned for the
    caller's existing symlink refusal. The result is sorted for deterministic output.
    """
    found = []
    stack = [docs_root]
    while stack:
        d = stack.pop()
        try:
            entries = list(os.scandir(d))
        except OSError as exc:
            raise SchemaError("cannot list docs subtree {}: {}".format(
                d.relative_to(root).as_posix(), exc.strerror or exc))
        for entry in entries:
            p = Path(entry.path)
            try:
                if entry.is_symlink():
                    if p.is_dir():
                        raise SchemaError(
                            "discovered docs subtree {} is a symbolic link; refusing to follow"
                            .format(p.relative_to(root).as_posix()))
                    if entry.name.endswith(".md"):
                        found.append(p)
                    continue
                if entry.is_dir(follow_symlinks=False):
                    stack.append(p)
                elif entry.name.endswith(".md"):
                    found.append(p)
            except OSError as exc:
                raise SchemaError("cannot stat docs entry {}: {}".format(
                    p.relative_to(root).as_posix(), exc.strerror or exc))
    return sorted(found)


def main(argv):
    check = "--check" in argv
    root = repo_root()
    docs_root = root / DOCS_DIR
    site_root = root / SITE_DIR
    shell_path = root / SHELL_REL
    # Reject a root directory that is itself a symlink; the resolved-path bounding rests on the pinned
    # roots being real directories.
    for name, d in ((DOCS_DIR, docs_root), (SITE_DIR, site_root)):
        if d.is_symlink():
            print("error: {}/ is a symbolic link; refusing".format(name), file=sys.stderr)
            return 2
        if not d.is_dir():
            print("error: {}/ is missing or not a directory".format(name), file=sys.stderr)
            return 2
    if shell_path.is_symlink() or not shell_path.is_file():
        print("error: {} is missing, not a regular file, or a symbolic link".format(SHELL_REL),
              file=sys.stderr)
        return 2
    shell = _read(shell_path)
    try:
        _validate_shell_placeholders(shell, SHELL_REL)
        _validate_token_sinks(shell, SHELL_REL)
        _validate_shell_sidebar(shell, SHELL_REL)
        entries = _declared_md_sources()
    except SchemaError as exc:
        print("error: {}".format(exc), file=sys.stderr)
        return 2
    # Discovery reconciliation: any docs/**/*.md not in the declared set is a silent-drop hazard (a
    # maintainer adds docs/rule2.md but forgets to declare it, and the target is never generated).
    # Discovery is RECURSIVE (an explicit os.scandir walk, which unlike rglob fails closed on an
    # unreadable subtree or a symlinked directory) and has NO filename-prefix exemption, so an
    # underscore-prefixed, nested-subdirectory, or symlinked stray .md fails loud rather than silently
    # vanishing. The only shell template (_shell.html) is .html, never a .md discovery candidate. A .md
    # that is a symbolic link is refused outright (reading it would pull an out-of-tree file into the
    # pipeline).
    declared_sources = {src_rel for src_rel, _ in entries}
    try:
        discovered = _discover_md(docs_root, root)
    except SchemaError as exc:
        print("error: {}".format(exc), file=sys.stderr)
        return 2
    for p in discovered:
        rel = p.relative_to(root).as_posix()
        if p.is_symlink():
            print("error: discovered docs source {} is a symbolic link; refusing to follow"
                  .format(rel), file=sys.stderr)
            return 2
        if rel not in declared_sources:
            print("error: discovered docs source {} is not declared in GENSRC_OUTPUTS"
                  .format(rel), file=sys.stderr)
            return 2
    drift = False
    for source_rel, target_rel in entries:
        src = root / source_rel
        target = root / target_rel
        try:
            _validate_source_path(src, source_rel, docs_root)
            _validate_target_path(target, target_rel, site_root)
            page = render_page(shell, _read(src), source_rel)
            _validate_head_tags(page, page_url(target_rel), target_rel)
        except SchemaError as exc:
            print("error: {}".format(exc), file=sys.stderr)
            return 2
        if reconcile(target, page, check, root):
            print("drift: {}".format(target_rel))
            drift = True
    if check and drift:
        print("run tools/gen_site.py to regenerate")
        return 1
    return 0


def _self_test():
    """Adversarial self-test: fail-closed on malformed inputs, correct on the bounded subset.

    Each case is an assertion the pilot's design MUST hold; a regression that breaks one of these
    would ship a silently-wrong page. Most cases build inputs in memory; the symlink / declared-source
    cases build a small filesystem fixture under a tempdir so no host state is touched.
    """
    import stat
    import tempfile

    failures = []
    case_count = 0
    # A minimal well-formed shell (no sidebar) for content-substitution cases.
    tiny_shell = ("<!doctype html><html><head><title>{{title}}</title>"
                  "<meta name=\"description\" content=\"{{description}}\">"
                  "<link rel=\"canonical\" href=\"{{canonical}}\">"
                  "<meta property=\"og:url\" content=\"{{og_url}}\">"
                  "</head><body>{{content}}</body></html>")
    frontmatter = (
        '+++\n'
        'title = "T"\n'
        'description = "D"\n'
        'canonical = "https://aiqt.ai/x"\n'
        'og-url = "https://aiqt.ai/x"\n'
        'sidebar-active = ""\n'
        '+++\n'
        '\n'
    )

    def _expect_error(label, src, needle):
        nonlocal case_count
        case_count += 1
        try:
            render_page(tiny_shell, src, "test.md")
        except SchemaError as exc:
            if needle not in str(exc):
                failures.append("{}: expected error containing {!r}, got {!r}".format(
                    label, needle, str(exc)))
            return
        failures.append("{}: expected SchemaError, got clean render".format(label))

    def _expect_render(label, src, needle):
        nonlocal case_count
        case_count += 1
        try:
            rendered = render_page(tiny_shell, src, "test.md")
        except SchemaError as exc:
            failures.append("{}: expected clean render, got SchemaError: {}".format(label, exc))
            return
        if needle not in rendered:
            failures.append("{}: expected rendered to contain {!r}".format(label, needle))

    def _expect_absent(label, src, needle):
        nonlocal case_count
        case_count += 1
        try:
            rendered = render_page(tiny_shell, src, "test.md")
        except SchemaError as exc:
            failures.append("{}: expected clean render, got SchemaError: {}".format(label, exc))
            return
        if needle in rendered:
            failures.append("{}: expected rendered NOT to contain {!r}".format(label, needle))

    def _expect_raises(label, fn, exc_type, needle):
        nonlocal case_count
        case_count += 1
        try:
            fn()
        except exc_type as exc:
            if needle not in str(exc):
                failures.append("{}: expected {} containing {!r}, got {!r}".format(
                    label, exc_type.__name__, needle, str(exc)))
            return
        except Exception as exc:  # noqa: BLE001
            failures.append("{}: expected {}, got {}: {}".format(
                label, exc_type.__name__, type(exc).__name__, exc))
            return
        failures.append("{}: expected {}, got no exception".format(label, exc_type.__name__))

    # 1. Missing frontmatter fences fail closed.
    _expect_error("no frontmatter open", "hello world\n", "expected frontmatter to open")
    # 2. Unclosed frontmatter fails closed.
    _expect_error("no frontmatter close", "+++\ntitle = \"T\"\nhello\n", "never closed")
    # 3. Malformed TOML fails closed.
    _expect_error("bad toml", "+++\ntitle = not valid\n+++\n", "not valid TOML")
    # 3b. An integer literal past CPython's 4300-digit int-string limit makes tomllib raise a BARE ValueError
    #     (not TOMLDecodeError); it must still fail closed as SchemaError. The
    #     digit limit is pinned to the default 4300 (test-hermeticity) and restored in finally. A parser
    #     overflow (tomllib raises RecursionError, not a ValueError) must fail closed the same way, with the
    #     overflow in the refusal.
    #     The overflow is INJECTED (tomllib.loads and tomllib.load raise RecursionError on a marked, otherwise
    #     valid input) rather than provoked by a deeply nested body: the depth at which tomllib overflows is an
    #     interpreter limit, so a fixed body overflows under one recursion limit and parses (or trips an
    #     unrelated refusal) under another.
    _prev_digits = sys.get_int_max_str_digits()
    sys.set_int_max_str_digits(4300)
    real_loads, real_load = tomllib.loads, tomllib.load

    def overflowing_loads(text, **kwargs):
        if "injected-overflow" in text:
            raise RecursionError("injected parser overflow")
        return real_loads(text, **kwargs)

    tomllib.loads = overflowing_loads
    tomllib.load = lambda fp, **kwargs: overflowing_loads(fp.read().decode("utf-8"), **kwargs)
    try:
        for label, value, needle in (("over-long integer", "9" * 4400, "not valid TOML"),
                                     ("parser overflow", "1  # injected-overflow",
                                      "not valid TOML: injected parser overflow")):
            try:
                _expect_error(label, "+++\nx = " + value + "\n+++\n", needle)
            except (ValueError, RecursionError) as exc:
                failures.append("{}: a bare {} escaped the frontmatter parse".format(label, type(exc).__name__))
    finally:
        tomllib.loads, tomllib.load = real_loads, real_load
        sys.set_int_max_str_digits(_prev_digits)
    # 4. Missing required key fails closed.
    _expect_error("missing key", '+++\ntitle = "T"\n+++\n', "missing required key")
    # 5. Unknown key fails closed.
    _expect_error("unknown key", frontmatter.replace('+++\n\n', 'foo = "bar"\n+++\n\n'),
                  "unknown key")
    # 6. Wrong-type key fails closed.
    _expect_error("wrong type", frontmatter.replace('title = "T"\n', 'title = 42\n'),
                  "must be a string")
    # 7. Unknown sidebar-active slug fails closed.
    _expect_error("bad slug", frontmatter.replace('sidebar-active = ""', 'sidebar-active = "nope"'),
                  "not a known sidebar slug")
    # 8. HTML block passes through verbatim (byte-exact).
    _expect_render("html block passthrough",
                   frontmatter + '<div id="x">hello</div>\n',
                   '<body><div id="x">hello</div></body>')
    # 9. ATX heading converts.
    _expect_render("atx h1", frontmatter + '# Hello world\n', '<h1>Hello world</h1>')
    _expect_render("atx h2", frontmatter + '## Two\n', '<h2>Two</h2>')
    _expect_render("atx h3", frontmatter + '### Three\n', '<h3>Three</h3>')
    # 10. Paragraph folds internal newlines and escapes ampersand/lt/gt.
    _expect_render("paragraph escape", frontmatter + 'A & B\n', '<p>A &amp; B</p>')
    _expect_render("paragraph fold", frontmatter + 'One\ntwo\n', '<p>One two</p>')
    # 11. hr converts.
    _expect_render("hr", frontmatter + '---\n', '<hr>')
    # 12. Blank body renders empty content (not an error).
    _expect_render("empty body", frontmatter, '<body></body>')
    # 13. sidebar_active slug marks the shell's link active.
    slug_shell = tiny_shell.replace(
        '<body>{{content}}</body>',
        '<body><a class="navlink" href="/rules">Rules</a>{{content}}</body>')
    fm_slug = frontmatter.replace('sidebar-active = ""', 'sidebar-active = "rules"')
    case_count += 1
    try:
        rendered = render_page(slug_shell, fm_slug + '<p>ok</p>\n', "test.md")
        needle = '<a class="navlink active" href="/rules" aria-current="page">Rules</a>'
        if needle not in rendered:
            failures.append("sidebar active: expected {!r} in rendered".format(needle))
    except SchemaError as exc:
        failures.append("sidebar active: unexpected SchemaError: {}".format(exc))
    # 14. sidebar_active slug whose href is not in the shell fails closed.
    _expect_error(
        "sidebar link absent",
        frontmatter.replace('sidebar-active = ""', 'sidebar-active = "rules"'),
        "no `<a class=\"navlink\" href=\"/rules\">` link")

    # ---- Frontmatter escaping (BLOCKER-1) --------------------------------------------------------
    # 15. Title containing `&<>` is text-escaped in <title>, not injected as markup.
    tricky_title_src = frontmatter.replace('title = "T"', 'title = "A & B <c> D"')
    _expect_render("title text-escape", tricky_title_src, "<title>A &amp; B &lt;c&gt; D</title>")
    # 16. Attribute-context value with `"` is quote-escaped; no attribute breakout.
    breakout = 'D">' + '<script>bad()</script>'
    tricky_desc_src = frontmatter.replace(
        'description = "D"', 'description = "' + breakout.replace('"', '\\"') + '"')
    _expect_absent("description attr-escape", tricky_desc_src, "<script>bad()</script>")
    _expect_render("description attr-escape retained",
                   tricky_desc_src, "&quot;&gt;&lt;script&gt;bad()&lt;/script&gt;")
    # 17. canonical URL with `<>&"` is attribute-escaped (BLOCKER-1 case j).
    tricky_canon_src = frontmatter.replace(
        'canonical = "https://aiqt.ai/x"',
        'canonical = "https://aiqt.ai/x?a=1&b=2\\"><c>"')
    _expect_render("canonical attr-escape",
                   tricky_canon_src,
                   'href="https://aiqt.ai/x?a=1&amp;b=2&quot;&gt;&lt;c&gt;"')

    # ---- Single-pass substitution (BLOCKER-5) ----------------------------------------------------
    # 18. A title containing another field's token is NOT re-scanned; the substituted value renders
    # literally (attribute-escaping the braces so they cannot be re-parsed downstream).
    cascade_src = frontmatter.replace(
        'title = "T"', 'title = "See {{og_title}} here"')
    _expect_render("cascade title literal", cascade_src, "<title>See {{og_title}} here</title>")
    _expect_absent("cascade title no re-scan", cascade_src, "<title>See OT here</title>")
    # 19. A description containing `{{content}}` does NOT swallow the body.
    swallow_src = frontmatter.replace(
        'description = "D"', 'description = "before {{content}} after"') + "BODY-TEXT\n"
    _expect_absent("cascade content no swallow", swallow_src,
                   'content="before BODY-TEXT after"')
    _expect_render("cascade content literal desc", swallow_src,
                   "before {{content}} after")

    # ---- Body braces inert (MAJOR-9) -------------------------------------------------------------
    # 20. Body prose with `{{ prose }}` renders literally; the guard does not false-trigger.
    prose_src = frontmatter + "Use the {{ username }} placeholder here.\n"
    _expect_render("body prose braces",
                   prose_src, "<p>Use the {{ username }} placeholder here.</p>")
    # 21. Body prose with `{{x}}` (tight braces) is inert after shell substitution.
    tight_src = frontmatter + "Literal {{x}} and {{content}} tokens in body.\n"
    _expect_render("body tight braces literal", tight_src,
                   "<p>Literal {{x}} and {{content}} tokens in body.</p>")

    # ---- Shell validation (BLOCKER-2) ----------------------------------------------------------
    # 22. A shell missing `{{content}}` fails closed at load time.
    _expect_raises(
        "shell missing content",
        lambda: _validate_shell_placeholders(
            tiny_shell.replace("{{content}}", ""), "shell.html"),
        SchemaError, "missing required placeholder {{content}}")
    # 23. A shell with duplicated `{{content}}` fails closed at load time.
    _expect_raises(
        "shell duplicated content",
        lambda: _validate_shell_placeholders(
            tiny_shell.replace("<body>{{content}}</body>",
                               "<body>{{content}}{{content}}</body>"),
            "shell.html"),
        SchemaError, "2 occurrences of {{content}}")
    # 24. A shell with an unknown `{{...}}` token fails closed at load time.
    _expect_raises(
        "shell unknown token",
        lambda: _validate_shell_placeholders(
            tiny_shell.replace("<body>{{content}}</body>",
                               "<body>{{content}}{{stray}}</body>"),
            "shell.html"),
        SchemaError, "unknown placeholder {{stray}}")
    # 25. A shell missing `{{title}}` fails closed at load time.
    _expect_raises(
        "shell missing title",
        lambda: _validate_shell_placeholders(
            tiny_shell.replace("<title>{{title}}</title>", "<title></title>"),
            "shell.html"),
        SchemaError, "missing required placeholder {{title}}")

    # ---- CommonMark type-1 blocks (MAJOR-6) ----------------------------------------------------
    # 26. A `<script>` block with an internal blank line is preserved as one block, not split.
    type1_body = "<script>\nconst a = 1;\n\nconst b = 2;\n</script>\n"
    type1_src = frontmatter + type1_body
    _expect_render("type1 script blank-line",
                   type1_src, "<script>\nconst a = 1;\n\nconst b = 2;\n</script>")
    _expect_absent("type1 script not paragraph-split",
                   type1_src, "<p>const b = 2;")
    # 27. `<pre>`, `<style>`, `<textarea>` also continue through blank lines.
    _expect_render("type1 pre blank-line",
                   frontmatter + "<pre>\nline1\n\nline2\n</pre>\n",
                   "<pre>\nline1\n\nline2\n</pre>")
    _expect_render("type1 style blank-line",
                   frontmatter + "<style>\n.a{}\n\n.b{}\n</style>\n",
                   "<style>\n.a{}\n\n.b{}\n</style>")
    _expect_render("type1 textarea blank-line",
                   frontmatter + "<textarea>\nfoo\n\nbar\n</textarea>\n",
                   "<textarea>\nfoo\n\nbar\n</textarea>")

    # ---- Space/tab-only blank lines split paragraphs (MAJOR-7) ---------------------------------
    # 28. `One\n   \nTwo` renders as two paragraphs (space-only blank line).
    space_blank_src = frontmatter + "One\n   \nTwo\n"
    _expect_render("space-only blank splits para 1", space_blank_src, "<p>One</p>")
    _expect_render("space-only blank splits para 2", space_blank_src, "<p>Two</p>")
    _expect_absent("space-only blank NOT one paragraph", space_blank_src, "<p>One  Two</p>")
    # 29. Tab-only blank line splits paragraphs too.
    tab_blank_src = frontmatter + "One\n\t\nTwo\n"
    _expect_render("tab-only blank splits para", tab_blank_src, "<p>Two</p>")

    # ---- Sidebar reconciliation (MAJOR-8) ------------------------------------------------------
    # 30. A shell with duplicated internal navlink (two `/rules`) fails closed at load time.
    dup_nav_shell = tiny_shell.replace(
        '<body>{{content}}</body>',
        '<body>'
        '<a class="navlink" href="/rules">A</a>'
        '<a class="navlink" href="/rules">B</a>'
        + ''.join('<a class="navlink" href="{}">L</a>'.format(h)
                  for slug, h in SIDEBAR_SLUG_TO_HREF.items() if slug != "rules")
        + '{{content}}</body>')
    _expect_raises(
        "shell duplicated navlink",
        lambda: _validate_shell_sidebar(dup_nav_shell, "shell.html"),
        SchemaError, "2 navlinks for slug 'rules'")
    # 31. A shell missing a mapped navlink fails closed at load time.
    missing_about = tiny_shell.replace(
        '<body>{{content}}</body>',
        '<body>'
        + ''.join('<a class="navlink" href="{}">L</a>'.format(h)
                  for slug, h in SIDEBAR_SLUG_TO_HREF.items() if slug != "about")
        + '{{content}}</body>')
    _expect_raises(
        "shell missing mapped navlink",
        lambda: _validate_shell_sidebar(missing_about, "shell.html"),
        SchemaError, "no navlink for mapped slug 'about'")
    # 32. A shell with an unknown INTERNAL navlink fails closed at load time.
    extra_internal = tiny_shell.replace(
        '<body>{{content}}</body>',
        '<body>'
        + ''.join('<a class="navlink" href="{}">L</a>'.format(h)
                  for h in SIDEBAR_SLUG_TO_HREF.values())
        + '<a class="navlink" href="/unmapped">X</a>'
        + '{{content}}</body>')
    _expect_raises(
        "shell unmapped internal navlink",
        lambda: _validate_shell_sidebar(extra_internal, "shell.html"),
        SchemaError, "not in SIDEBAR_SLUG_TO_HREF")
    # 33. External navlinks (http://, https://) are exempt from the map reconciliation.
    with_external = tiny_shell.replace(
        '<body>{{content}}</body>',
        '<body>'
        + ''.join('<a class="navlink" href="{}">L</a>'.format(h)
                  for h in SIDEBAR_SLUG_TO_HREF.values())
        + '<a class="navlink" href="https://github.com/x">GH</a>'
        + '{{content}}</body>')
    case_count += 1
    try:
        _validate_shell_sidebar(with_external, "shell.html")
    except SchemaError as exc:
        failures.append("external navlink exempt: unexpected SchemaError: {}".format(exc))

    # ---- Filesystem fail-closed cases (BLOCKER-3, BLOCKER-4) -----------------------------------
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        docs = td / "docs"
        docs.mkdir()
        site = td / "site"
        site.mkdir()
        # 34. A symlinked source is refused.
        good_body = frontmatter + "hello\n"
        (docs / "real.md").write_text(good_body, encoding="utf-8")
        link = docs / "link.md"
        try:
            os.symlink("real.md", link)
            symlink_ok = True
        except (OSError, NotImplementedError):
            symlink_ok = False  # platform without symlink support
        if symlink_ok:
            _expect_raises(
                "symlink source refused",
                lambda: _validate_source_path(link, "docs/link.md", docs),
                SchemaError, "symbolic link")
        # 35. A missing declared source is refused.
        _expect_raises(
            "missing declared source refused",
            lambda: _validate_source_path(docs / "nope.md", "docs/nope.md", docs),
            SchemaError, "missing")
        # 36. A non-regular (directory) source is refused.
        (docs / "adir").mkdir()
        _expect_raises(
            "non-regular source refused",
            lambda: _validate_source_path(docs / "adir", "docs/adir", docs),
            SchemaError, "not a regular file")
        # 37. A symlinked target is refused.
        if symlink_ok:
            outside = td / "victim.html"
            outside.write_text("keep-me\n", encoding="utf-8")
            target_link = site / "rule1.html"
            os.symlink("../victim.html", target_link)
            _expect_raises(
                "symlink target refused",
                lambda: _validate_target_path(target_link, "site/rule1.html", site),
                SchemaError, "symbolic link")
        # 38. A source resolving outside docs/ is refused (via `..`).
        _expect_raises(
            "source escape refused",
            lambda: _validate_source_path(docs / ".." / "site" / "x.md",
                                          "docs/../site/x.md", docs),
            SchemaError, "resolves outside")

    # ---- Preserve rstrip semantics for hr trailing whitespace (MINOR-11 residual) --------------
    # 39. hr with trailing whitespace still renders (documented residual: non-HTML block rstrip).
    _expect_render("hr trailing ws", frontmatter + "---   \n", "<hr>")

    # ---- Malformed placeholder tokens rejected (finding 4) -------------------------------------
    # A moustache opener that is not the exact start of a well-formed {{name}} token is rejected at
    # load time, so a malformed token can never ship literally to the site.
    for label, bad in (
        ("ws-inside", "<div>{{ stray }}</div>"),
        ("dotted", "<div>{{foo.bar}}</div>"),
        ("empty", "<div>{{}}</div>"),
        ("bang", "<div>{{title!}}</div>"),
        ("unclosed", "<div>{{unclosed</div>"),
    ):
        _expect_raises(
            "malformed placeholder " + label,
            lambda b=bad: _validate_shell_placeholders(
                tiny_shell.replace("</body>", b + "</body>"), "shell.html"),
            SchemaError, "malformed placeholder")
    # A well-formed but unknown token still reports as an unknown (not malformed) placeholder.
    _expect_raises(
        "wellformed unknown placeholder",
        lambda: _validate_shell_placeholders(
            tiny_shell.replace("</body>", "{{stray}}</body>"), "shell.html"),
        SchemaError, "unknown placeholder {{stray}}")

    # ---- Sidebar navlink parse robust to layout (finding 3) ------------------------------------
    nav_base = tiny_shell.replace(
        '<body>{{content}}</body>',
        '<body>'
        + ''.join('<a class="navlink" href="{}">L</a>'.format(h)
                  for h in SIDEBAR_SLUG_TO_HREF.values())
        + '{{content}}</body>')
    # A duplicate navlink in an ALTERNATE attribute layout (href before class) is caught.
    _expect_raises(
        "navlink alt-layout duplicate",
        lambda: _validate_shell_sidebar(
            nav_base.replace('{{content}}',
                             '<a href="/rules" class="navlink">Dup</a>{{content}}'), "shell.html"),
        SchemaError, "2 navlinks for slug 'rules'")
    # A duplicate navlink using single-quoted attributes is caught.
    _expect_raises(
        "navlink single-quote duplicate",
        lambda: _validate_shell_sidebar(
            nav_base.replace('{{content}}',
                             "<a class='navlink' href='/about'>Dup</a>{{content}}"), "shell.html"),
        SchemaError, "2 navlinks for slug 'about'")
    # A navlink with no href is caught (not silently ignored).
    _expect_raises(
        "navlink missing href",
        lambda: _validate_shell_sidebar(
            nav_base.replace('{{content}}',
                             '<a class="navlink">NoHref</a>{{content}}'), "shell.html"),
        SchemaError, "0 href attribute")

    # ---- Token sink matches actual HTML context (finding 5) ------------------------------------
    # Relocating a text-sink token ({{title}}) into an attribute value is rejected at load time.
    _expect_raises(
        "token sink relocation",
        lambda: _validate_token_sinks(
            tiny_shell.replace("<title>{{title}}</title>",
                               '<div data-title="{{title}}"></div><title>x</title>'), "shell.html"),
        SchemaError, "declared for the 'text' sink")
    # The well-formed tiny shell passes sink validation.
    case_count += 1
    try:
        _validate_token_sinks(tiny_shell, "shell.html")
    except SchemaError as exc:
        failures.append("token sink ok shell: unexpected SchemaError: {}".format(exc))

    # ---- Link-preview head block validation ------------------------------------------------------
    # The page guard parses the head: every tag of HEAD_TAG_BLOCK keyed exactly once in the whole page,
    # inside <head>, with exactly the block's value (the page's own URL in the canonical and og:url
    # slots), in the block's order; a second element with a block key fails whatever its value.
    _own_url = SITE_BASE_URL + "rules"
    ok_tags = [t.format(url=_own_url) for t in HEAD_TAG_BLOCK]
    ok_head = "<head>" + "\n".join(ok_tags) + "</head>"
    for label, page in (("head block baseline", ok_head),
                        ("head block in a full page", "<!doctype html><html><head><title>t</title>"
                         + "\n".join(ok_tags) + "</head><body><p>x</p></body></html>"),
                        ("head block closed by body", "<head>" + "\n".join(ok_tags) + "<body></body>"),
                        ("head block other head links", ok_head.replace(
                            "<head>", '<head><link rel="stylesheet" href="/s.css"><meta name="viewport" '
                            'content="w">'))):
        case_count += 1
        try:
            _validate_head_tags(page, _own_url, "site/rules.html")
        except SchemaError as exc:
            failures.append("{}: unexpected SchemaError: {}".format(label, exc))
    # A page missing any one tag of the block fails (every tag exercised).
    for tag in HEAD_TAG_BLOCK:
        _expect_raises(
            "head missing tag " + tag[:40],
            lambda t=tag: _validate_head_tags(
                ok_head.replace(t.format(url=_own_url), "", 1), _own_url, "site/rules.html"),
            SchemaError, "exactly one")
    # A page carrying a duplicate of a block tag fails: an identical second copy, and a second copy
    # under a DIFFERENT value (a leftover old tag), after the head or inside it beside the required tag,
    # in either attribute order, under a case variant of the key, or under name= instead of property=.
    _og_title = '<meta property="og:title" content="AIQT.ai - Rules your AI follows">'
    for label, page in (
            ("head duplicate tag same value",
             ok_head + '<meta name="twitter:card" content="summary_large_image">'),
            ("head duplicate tag different value",
             ok_head + '<meta property="og:title" content="another title">'),
            ("head duplicate tag different value in head",
             ok_head.replace(_og_title, _og_title + '<meta content="another title" property="og:title">')),
            ("head duplicate tag before the required one",
             ok_head.replace(_og_title, '<meta property="og:title" content="another title">' + _og_title)),
            ("head duplicate tag case variant",
             ok_head.replace(_og_title, _og_title + '<META PROPERTY="OG:TITLE" CONTENT="another">')),
            ("head duplicate tag under name",
             ok_head.replace(_og_title, _og_title + '<meta name="og:title" content="another">')),
            ("head duplicate canonical other rel tokens",
             ok_head.replace("</head>", '<link rel="Canonical alternate" href="https://x.test/"></head>'))):
        _expect_raises(label, lambda p=page: _validate_head_tags(p, _own_url, "site/rules.html"),
                       SchemaError, "exactly one element keyed")
    # A page carrying another page's canonical (its own og:url intact) fails as a wrong value.
    _expect_raises(
        "head foreign canonical",
        lambda: _validate_head_tags(
            ok_head.replace('<link rel="canonical" href="{}">'.format(_own_url),
                            '<link rel="canonical" href="{}about">'.format(SITE_BASE_URL), 1),
            _own_url, "site/rules.html"),
        SchemaError, "expected exactly")
    # A block tag with the right key but a changed value fails.
    _expect_raises(
        "head changed value",
        lambda: _validate_head_tags(ok_head.replace('content="1200"', 'content="1201"'),
                                    _own_url, "site/rules.html"),
        SchemaError, "expected exactly")
    # A block tag moved out of the head into the body fails (the last tag, so order alone cannot see it).
    _last = ok_tags[-1]
    _expect_raises(
        "head tag outside head",
        lambda: _validate_head_tags(
            ok_head.replace("\n" + _last, "", 1) + "<body>" + _last + "</body>",
            _own_url, "site/rules.html"),
        SchemaError, "outside <head>")
    # The block reordered (og:url and og:title swapped) fails.
    _expect_raises(
        "head block reordered",
        lambda: _validate_head_tags(
            ok_head.replace(ok_tags[3] + "\n" + ok_tags[4], ok_tags[4] + "\n" + ok_tags[3], 1),
            _own_url, "site/rules.html"),
        SchemaError, "the block's order")
    # A head-less page, a page with two heads, a head that never ends, and a keyed element with a
    # duplicate attribute each fail, naming the page.
    for label, page, needle in (
            ("head absent", "\n".join(ok_tags), "site/rules.html: page must have exactly one <head>, found 0"),
            ("head twice", ok_head + "<head></head>", "site/rules.html: page must have exactly one <head>, found 2"),
            ("head never ends", "<head>" + "\n".join(ok_tags), "site/rules.html: <head> never ends"),
            ("head after body", "<body></body>" + ok_head, "site/rules.html: line 1: <head> starts after <body>"),
            ("head keyed duplicate attribute",
             ok_head.replace(_og_title, '<meta property="og:title" content="x" content="'
                             'AIQT.ai - Rules your AI follows">'), "duplicate 'content' attribute")):
        _expect_raises(label, lambda p=page: _validate_head_tags(p, _own_url, "site/rules.html"),
                       SchemaError, needle)
    # A commented-out copy of a block tag is not an element, so it neither counts nor fails.
    case_count += 1
    try:
        _validate_head_tags(ok_head.replace("</head>", "<!-- " + _og_title + " --></head>"),
                            _own_url, "site/rules.html")
    except SchemaError as exc:
        failures.append("head commented copy: unexpected SchemaError: {}".format(exc))
    # The head ends where a browser ends it: at a start tag that is not a head element, or at text that
    # is not whitespace, so a block tag after either (or wrapped in svg) fails as outside the head.
    for label, page in (
            ("head ended by a div", ok_head.replace(_og_title, "<div></div>" + _og_title)),
            ("head ended by stray text", ok_head.replace(_og_title, "stray text " + _og_title)),
            ("head ended by an img", ok_head.replace(_og_title, '<img src="/x.png" alt="x">' + _og_title)),
            ("head ended by svg around og:title", ok_head.replace(_og_title, "<svg>" + _og_title + "</svg>")),
            ("head ended by an end body tag", ok_head.replace(_og_title, "</body>" + _og_title)),
            ("head ended by an end html tag", ok_head.replace(_og_title, "</html>" + _og_title)),
            ("head ended by an end br tag", ok_head.replace(_og_title, "</br>" + _og_title))):
        _expect_raises(label, lambda p=page: _validate_head_tags(p, _own_url, "site/rules.html"),
                       SchemaError, "sits outside <head>")
    # Whitespace, comments, and the raw-text contents of title, script and style do not end the head,
    # nor does anything inside a template.
    case_count += 1
    try:
        _validate_head_tags(ok_head.replace(
            _og_title, " \t\n<!-- <div>x</div> --><title>a <b> title</title><script>if (a < b) {}</script>"
            "<style>p > a {}</style><template><div>x</div></template>\n" + _og_title),
            _own_url, "site/rules.html")
    except SchemaError as exc:
        failures.append("head whitespace and comments: unexpected SchemaError: {}".format(exc))
    # The raw-text contents of title, script and style alone, each holding tag-like text, do not end it.
    case_count += 1
    try:
        _validate_head_tags(ok_head.replace(
            _og_title, "<title>a <div> title</title><script>if (a < b) {}</script><style>p > a {}</style>"
            + _og_title), _own_url, "site/rules.html")
    except SchemaError as exc:
        failures.append("head raw-text contents: unexpected SchemaError: {}".format(exc))
    # A browser ignores the slash on <script/>, <style/> and <title/>, so everything up to the matching
    # end tag is raw text: a block hidden there is not in the head, and the self-closing tag fails.
    for label, opener in (("head block hidden after <script/>", '<script src="/theme.js"/>'),
                          ("head block hidden after <style/>", "<style/>"),
                          ("head block hidden after <title/>", "<title/>")):
        raw_tag = opener[1:].split()[0].rstrip("/>")
        _expect_raises(
            label,
            lambda o=opener, t=raw_tag: _validate_head_tags(
                ok_head.replace("<head>", "<head>" + o, 1).replace("</head>", "</{}></head>".format(t), 1),
                _own_url, "site/rules.html"),
            SchemaError, "a self-closing <{}/>".format(raw_tag))
    # The tag that counts must write the block's key exactly: a case variant or a padded key fails.
    for label, page, needle in (
            ("head key case variant", ok_head.replace('property="og:title"', 'property="OG:Title"'),
             "expected exactly 'og:title'"),
            ("head key padded", ok_head.replace('property="og:title"', 'property=" og:title "'),
             "expected exactly 'og:title'"),
            ("head canonical rel case variant", ok_head.replace('rel="canonical"', 'rel="Canonical"'),
             "expected exactly 'canonical'"),
            ("head key second spelling", ok_head.replace('property="og:title"',
                                                         'property="og:title" name="OG:Title"'),
             "as 'OG:Title', expected exactly 'og:title'"),
            ("head canonical rel second spelling", ok_head.replace('rel="canonical"',
                                                                   'rel="canonical Canonical"'),
             "as 'Canonical', expected exactly 'canonical'")):
        _expect_raises(label, lambda p=page: _validate_head_tags(p, _own_url, "site/rules.html"),
                       SchemaError, needle)
    # The exact key repeated, or another rel token beside canonical, still passes.
    for label, page in (
            ("head key exact twice", ok_head.replace('property="og:title"', 'property="og:title" name="og:title"')),
            ("head canonical beside another rel token", ok_head.replace('rel="canonical"',
                                                                        'rel="alternate canonical"'))):
        case_count += 1
        try:
            _validate_head_tags(page, _own_url, "site/rules.html")
        except SchemaError as exc:
            failures.append("{}: unexpected SchemaError: {}".format(label, exc))
    # Markup html.parser cannot follow as a browser's tokenizer would fails rather than pass: "<!--" and then
    # "<script" in a head script (the double-escaped state can hide a later block as script text), a "<!["
    # section in or before the head (a browser ends it at the first ">"), anything but whitespace, comments,
    # a doctype and <html> before the head (a browser starts the head there), an element with text contents
    # inside a template in the head (a "</template>" in that text could end the template here), and an svg
    # or math <style> or <title> inside a template in the head, whatever its text.
    for label, page, needle in (
            ("head script double escape", ok_head.replace(
                "<head>", "<head><script><!--<script></script>", 1).replace("</head>", "</script></head>", 1),
             'holds "<!--"'),
            ("head self-closed script double escape", ok_head.replace(
                "<head>", "<head><script/><!--<script>--!></script>", 1).replace("</head>", "</script></head>", 1),
             "a self-closing <script/>"),
            ("head self-closed script escape in an attribute", ok_head.replace(
                "<head>", '<head><script/><meta content="<!--"><script></script>', 1).replace(
                "</head>", "</script></head>", 1), "a self-closing <script/>"),
            ("head script double escape upper case", ok_head.replace(
                "<head>", "<head><script><!-- a <SCRIPT\n></script>", 1).replace("</head>", "</script></head>", 1),
             'holds "<!--"'),
            ("head cdata section", ok_head.replace("<head>", "<head><![CDATA[x>y]]>", 1), 'a "<![" section'),
            ("cdata section before head", ok_head.replace("<head>", "<![CDATA[x>y]]><head>", 1),
             'a "<![" section'),
            ("text before head", ok_head.replace("<head>", "x<head>", 1), "text before <head>"),
            ("start tag before head", ok_head.replace("<head>", "<div></div><head>", 1),
             "a <div> start tag before <head>"),
            ("end tag before head", ok_head.replace("<head>", "</html><head>", 1),
             "a </html> end tag before <head>"),
            ("template style hides its end", ok_head.replace(
                "<head>", "<head><template><style/></template>", 1).replace("</head>", "</style></head>", 1),
             "a self-closing <style/>"),
            ("template title hides its end", ok_head.replace(
                "<head>", "<head><template><title>x</title></template>", 1), "a <title> inside a <template>"),
            ("template noscript hides its end", ok_head.replace(
                "<head>", "<head><template><noscript></template>", 1).replace(
                "</head>", "</noscript></head>", 1), "a <noscript> whose text holds markup"),
            ("template svg style", ok_head.replace(
                "<head>", "<head><template><svg><style>p{}</style></svg></template>", 1),
             "a <style> inside a <template>"),
            ("template svg slash value style", ok_head.replace(
                "<head>", "<head><template><svg x=a/><style>p{}</style></svg></template>", 1),
             "a <style> inside a <template>"),
            ("template math style", ok_head.replace(
                "<head>", "<head><template><math><style>p{}</style></math></template>", 1),
             "a <style> inside a <template>"),
            ("template math slash value style", ok_head.replace(
                "<head>", "<head><template><math x=a/><style>p{}</style></math></template>", 1),
             "a <style> inside a <template>"),
            ("template svg title", ok_head.replace(
                "<head>", "<head><template><svg><title>Icon</title></svg></template>", 1),
             "a <title> inside a <template>"),
            ("template math slash value title", ok_head.replace(
                "<head>", "<head><template><math x=a/><title>x</title></math></template>", 1),
             "a <title> inside a <template>"),
            ("template svg nested style", ok_head.replace(
                "<head>", "<head><template><svg><g><style></style></g></svg></template>", 1),
             "a <style> inside a <template>"),
            ("template script and style", ok_head.replace(
                "<head>", "<head><template><script>a()</script><style>p{}</style></template>", 1),
             "a <script> inside a <template>"),
            ("template style", ok_head.replace("<head>", "<head><template><style>p{}</style></template>", 1),
             "a <style> inside a <template>"),
            ("head legacy script comment", ok_head.replace("<head>", "<head><script><!-- a() //--></script>", 1),
             'holds "<!--"'),
            ("head script comment then a script-like word",
             ok_head.replace("<head>", "<head><script><!-- a <scripts --></script>", 1), 'holds "<!--"'),
            ("head script less-than comparison",
             ok_head.replace("<head>", "<head><script>if(a<!--b){}</script>", 1), 'holds "<!--"')):
        _expect_raises(label, lambda p=page: _validate_head_tags(p, _own_url, "site/rules.html"),
                       SchemaError, needle)
    # A template whose svg or math is closed or self-closed before a <style> or <title> fails as well: the
    # collector does not track which svg or math element is open there, so every such form fails alike.
    for svg_or_math in ("<svg></svg>", "<math></math>", "<svg/>", "<math/>", "<svg x=a />", "<svg></svg><math/>",
                        "<math></math><svg/>", "<svg><g/></svg>"):
        for text_tag in ("style", "title"):
            _expect_raises(
                "template {} then a {}".format(svg_or_math, text_tag),
                lambda s=svg_or_math, t=text_tag: _validate_head_tags(ok_head.replace(
                    "<head>", "<head><template>{}<{t}>p</{t}></template>".format(s, t=t), 1),
                    _own_url, "site/rules.html"),
                SchemaError, "a <{}> inside a <template> in <head>".format(text_tag))
    # A doctype, <html>, whitespace and comments before the head, and a head script without "<!--", pass.
    case_count += 1
    try:
        _validate_head_tags(ok_head.replace("<head>", "<!doctype html> <!-- c -->\n<html lang=\"en\">\n<head>"
                                            "<script>a--></script>", 1), _own_url, "site/rules.html")
    except SchemaError as exc:
        failures.append("head prologue and script: unexpected SchemaError: {}".format(exc))
    # The head ends sooner than a browser's at </head> or <body> inside a template (a browser ignores both
    # there) and at </head> inside raw text a self-closing script left open; </body>, </html> and </br>
    # there do not end it, so a block after them still fails inside that raw text.
    for label, page, needle in (
            ("head ended by </head> in a template", ok_head.replace(
                "<head>", "<head><template></head></template>", 1), "sits outside <head>"),
            ("head ended by <body> in a template", ok_head.replace(
                "<head>", "<head><template><body></template>", 1), "sits outside <head>"),
            ("raw text kept past </body>", ok_head.replace("<head>", "<head><script/></body>", 1).replace(
                "</head>", "</script></head>", 1), "a self-closing <script/>")):
        _expect_raises(label, lambda p=page: _validate_head_tags(p, _own_url, "site/rules.html"),
                       SchemaError, needle)
    # A block tag wrapped in a template in the head is not a head element a reader sees; an extra copy
    # there still counts as a duplicate. A noscript in the head holding anything but whitespace fails.
    for label, page, needle in (
            ("head tag inside template", ok_head.replace(_og_title, "<template>" + _og_title + "</template>"),
             "sits inside <template> in <head>"),
            ("head tag inside noscript", ok_head.replace(_og_title, "<noscript>" + _og_title + "</noscript>"),
             "a <noscript> whose text holds markup"),
            ("head extra copy inside template", ok_head.replace(
                _og_title, _og_title + "<template>" + _og_title + "</template>"), "exactly one element keyed"),
            ("head extra copy inside noscript", ok_head.replace(
                _og_title, _og_title + "<noscript>" + _og_title + "</noscript>"),
             "a <noscript> whose text holds markup"),
            ("head noscript end hidden in a comment", ok_head.replace(
                "<head>", "<head><noscript><!--x--></noscript>", 1), "a <noscript> whose text holds markup"),
            ("head noscript text", ok_head.replace("<head>", "<head><noscript>x</noscript>", 1),
             "text inside a <noscript> in <head>")):
        _expect_raises(label, lambda p=page: _validate_head_tags(p, _own_url, "site/rules.html"),
                       SchemaError, needle)
    # Outside the accepted page syntax a page fails wherever the construct sits, head or body: each shape
    # html.parser and a browser's tokenizer read differently (an abrupt or nested comment, a "<![" section,
    # a self-closing text element, a text element's end tag inside an attribute value, a comment or other
    # raw text, a double-escaped script, svg content hiding a keyed tag) and each tag outside the plain shape.
    _in_body = "<body>{}</body>"
    _syntax_cases = []
    for where, wrap in (("head", lambda s: ok_head.replace("<head>", "<head>" + s, 1)),
                        ("body", lambda s: ok_head + _in_body.format(s))):
        for label, src, needle in (
                ("abrupt comment", "<!--><div>-->", 'an abrupt comment'),
                ("abrupt dash comment", "<!--->x<div>-->", 'an abrupt comment'),
                ("comment closed by --!>", "<!-- a --!> b -->", 'holding "--!>"'),
                ("nested comment", "<!-- <!-- x -->", 'a nested "<!--"'),
                ("cdata hiding a tag", '<![CDATA[x><meta property="og:title" content="evil">]]>',
                 'a "<![" section'),
                ("abrupt comment hiding a tag", '<!--><meta property="og:title" content="evil">-->',
                 'an abrupt comment'),
                ("bogus comment", "<!x>", 'a "<!" construct'),
                ("processing instruction", "<?x>", 'a "<?" construct'),
                ("self-closed script with end tag in an attribute",
                 '<script/><meta content="</script>x"></script>', "a self-closing <script/>"),
                ("self-closed style with end tag in an attribute",
                 '<style/><meta content="</style>x"></style>', "a self-closing <style/>"),
                ("self-closed title with end tag in an attribute",
                 '<title/><meta content="</title>x"></title>', "a self-closing <title/>"),
                ("self-closed textarea", "<textarea/></textarea>", "a self-closing <textarea/>"),
                ("self-closed noscript", "<noscript/>", "a self-closing <noscript/>"),
                ("self-closed script before a comment", "<script/><!--x-->", "a self-closing <script/>"),
                ("script end tag in an attribute", '<link title="</SCRIPT>" href="/x">',
                 "an attribute value holding the end tag"),
                ("noscript end tag in script text", "<script></noscript></script>",
                 "the text of a <script> holding the end tag"),
                ("double-escaped script", "<script><!--<script></script>x</script>", 'and then "<script"'),
                ("svg style hiding a tag", '<svg><style><meta property="og:title" content="e"></style></svg>',
                 "a <style> inside svg or math content"),
                ("math integration point", "<math><mi>x</mi></math>", "a <mi> inside svg or math content"),
                ("plaintext", "<plaintext>", "a <plaintext>"),
                ("unterminated script", "<script>x", "has no plain </script> end tag"),
                ("raw text end tag with an attribute", "<style>x</style a>", "has no plain </style> end tag"),
                ("textarea text with a comment", "<textarea><!--x--></textarea>", 'markup-like "<"'),
                ("start tag outside the plain shape", '<a b="1"c="2">', "a start tag outside the plain shape"),
                ("end tag outside the plain shape", "</a b>", "an end tag that is not a name"),
                ("comment never closes", "<!-- x", "a comment that never closes"),
                ("svg slash value hiding a tag",
                 '<svg x=a/><style><meta property="og:title" content="e"></style></svg>',
                 'a <style> inside svg or math content whose text holds "<"'),
                ("math slash value hiding a tag", '<math x=a/><style><meta property="og:title" content="e"></style>'
                 "</math>", 'a <style> inside svg or math content whose text holds "<"'),
                ("svg slash value before a script", "<svg x=/x/><script>a()</script></svg>",
                 "a <script> inside svg or math content"),
                ("script slash value", "<script x=a/></script>", 'last unquoted attribute value ends in "/"'),
                ("style slash value", "<style x=a/></style>", 'last unquoted attribute value ends in "/"'),
                ("iframe slash value", "<iframe src=/embed/></iframe>", 'last unquoted attribute value ends in "/"'),
                ("svg title holding markup", "<svg><title>a<b>c</b></title></svg>",
                 'a <title> inside svg or math content whose text holds "<"'),
                ("noscript holding markup", "<noscript><b>x</b></noscript>", "a <noscript> whose text holds markup"),
                ("noscript holding a comment", "<noscript><!-- x --></noscript>",
                 "a <noscript> whose text holds markup"),
                ("noscript holding a control reference", "<noscript>&#1;</noscript>", "a character reference"),
                ("legacy reference name before = in an attribute", '<a title="&copy=x">x</a>', "a character reference"),
                ("reference without a semicolon", "&#65", "a character reference")):
            _syntax_cases.append(("page syntax {} in {}".format(label, where), wrap(src), needle))
        # A reference outside the accepted set, in text and in a key.
        for ref in ("&#1;", "&#11;", "&#x7f;", "&#xfdd0;", "&#0;", "&#x80;", "&#xFFFE;"):
            _syntax_cases.append(("page syntax reference {} in {}".format(ref, where), wrap(ref),
                                  "a character reference"))
    for ref in ("&#1;", "&#11;", "&#x7f;", "&#xfdd0;"):
        _syntax_cases.append(("page syntax reference {} in a key".format(ref),
                              ok_head.replace('property="og:title"', 'property="og:t{}itle"'.format(ref)),
                              "a character reference"))
    _syntax_cases.append(("page syntax template svg slash value hiding its end", ok_head.replace(
        "<head>", "<head><template><svg x=a/><style></template>x</style></svg></template>", 1),
        'a <style> inside svg or math content whose text holds "<"'))
    _syntax_cases.append(("page syntax legacy doctype after <html>",
                          '<html><!DOCTYPE html SYSTEM "about:legacy-compat">' + ok_head, 'a "<!" construct'))
    _syntax_cases.append(("page syntax legacy doctype string case", '<!DOCTYPE html SYSTEM "about:Legacy-compat">'
                          + ok_head, 'a "<!" construct'))
    _syntax_cases.append(("page syntax late doctype", ok_head + "<!doctype html>", 'a "<!" construct'))
    # CHARREF_OK's ends: the code point just outside each accepted range fails, the one at its edge passes.
    for ref in ("&#x8;", "&#xB;", "&#x1F;", "&#x7F;", "&#x9F;", "&#xD800;", "&#xFDD0;", "&#xFDEF;", "&#xFFFE;",
                "&#x110000;"):
        _syntax_cases.append(("page syntax reference bound {}".format(ref), ok_head + _in_body.format(ref),
                              "a character reference"))
    for ref in ("&#x9;", "&#xA;", "&#x20;", "&#x7E;", "&#xA0;", "&#xD7FF;", "&#xE000;", "&#xFDCF;", "&#xFDF0;",
                "&#xFFFD;"):
        case_count += 1
        try:
            _validate_head_tags(ok_head + _in_body.format(ref), _own_url, "site/rules.html")
        except SchemaError as exc:
            failures.append("reference bound {}: unexpected SchemaError: {}".format(ref, exc))
    for label, page, needle in _syntax_cases:
        _expect_raises(label, lambda p=page: _validate_head_tags(p, _own_url, "site/rules.html"),
                       SchemaError, needle)
    # Inside the accepted page syntax these pass, each on the accepted side of a refusal's line: a legacy
    # script comment and "<!--" then "<scripts" in the body, "<script" with no "<!--" before it, a style
    # after a template, a text element's name in an attribute without "</", "<" text in a title, an svg
    # closed before a script, and a self-closed svg.
    for label, page in (
            ("body legacy script comment", ok_head + "<body><script><!-- a() //--></script></body>"),
            ("body script comment then a script-like word", ok_head + "<body><script><!-- a <scripts --></script>"
             "</body>"),
            ("head script start text without a comment",
             ok_head.replace("<head>", '<head><script>a("<script>")</script>', 1)),
            ("head style after a template holding an open svg", ok_head.replace(
                "<head>", "<head><template><svg></template><style>p{}</style>", 1)),
            ("head noscript whitespace", ok_head.replace("<head>", "<head><noscript> \n</noscript>", 1)),
            ("body text element name without an end tag", ok_head + '<body><a title="/script">x</a></body>'),
            ("body svg closed before a script", ok_head + "<body><svg><path d=\"M0\"/></svg><script>a()</script>"
             "</body>"),
            ("body self-closed svg before a style", ok_head + "<body><svg/><style>p{}</style></body>"),
            ("body unquoted slash value", ok_head + "<body><a href=/x/>x</a></body>"),
            ("body svg slash value closed", ok_head + "<body><svg x=a/></svg><p>x</p></body>"),
            ("body svg slash after a space self-closes", ok_head + "<body><svg x=a /><style>p{}</style></body>"),
            ("body iframe slash value then a space", ok_head + "<body><iframe src=/embed/ ></iframe></body>"),
            ("body svg title and style without <", ok_head + "<body><svg><title>Icon</title><style>p{}</style></svg>"
             "</body>"),
            ("head comment holding text element end tags", ok_head.replace(
                "<head>", "<head><!-- </script> </noscript> --><!--[if lt IE 9]><script src=\"/h.js\"></script>"
                "<![endif]-->", 1)),
            ("body noscript text", ok_head + "<body><noscript>x &amp; y</noscript></body>"),
            ("head whitespace references", ok_head.replace("<head>", "<head>&#32;&#10;&Tab;", 1)),
            ("key with a printable reference", ok_head.replace('property="og:title"', 'property="og:t&#105;tle"')),
            ("body references", ok_head + "<body>a&nbsp;b&#9776;&#xFFFD;&#126;&#x20;&middot;</body>"),
            ("legacy doctype first", '<!DOCTYPE html SYSTEM "about:legacy-compat">\n' + ok_head),
            ("legacy doctype single quotes", "<!doctype html system 'about:legacy-compat'>" + ok_head)):
        case_count += 1
        try:
            _validate_head_tags(page, _own_url, "site/rules.html")
        except SchemaError as exc:
            failures.append("{}: unexpected SchemaError: {}".format(label, exc))
    # A numeric reference is at most ten characters after the "&": eight decimal or seven hex digits pass, one
    # more fails; thousands of digits fail as the same refusal before html.unescape could raise a ValueError.
    for label, ref, refused in (("eight decimal digits", "&#00000065;", False),
                                ("nine decimal digits", "&#000000065;", True),
                                ("seven hex digits", "&#x0000041;", False),
                                ("eight hex digits", "&#x00000041;", True),
                                ("5000 decimal digits", "&#" + "0" * 5000 + "65;", True),
                                ("5000 hex digits", "&#x" + "0" * 5000 + "41;", True)):
        page = ok_head + _in_body.format(ref)
        if refused:
            _expect_raises("reference length " + label, lambda p=page: _validate_head_tags(
                p, _own_url, "site/rules.html"), SchemaError, "site/rules.html: line")
            continue
        case_count += 1
        try:
            _validate_head_tags(page, _own_url, "site/rules.html")
        except SchemaError as exc:
            failures.append("reference length {}: unexpected SchemaError: {}".format(label, exc))
    # The page scan stays linear: on 600 tags with 200 svg elements left open below 200 math elements opened
    # and closed, it counts lines once and does a bounded amount of work on its svg/math stack (counted on
    # the stack itself, not timed).
    class _CountingPage(str):
        count_calls = 0

        def count(self, *args):
            type(self).count_calls += 1
            return str.count(self, *args)

    class _CountingStack(list):
        work = 0

        def _add(self, n):
            type(self).work += max(n, 1)

        def __contains__(self, item):
            self._add(len(self))
            return list.__contains__(self, item)

        def __getitem__(self, key):
            got = list.__getitem__(self, key)
            self._add(len(got) if isinstance(key, slice) else 1)
            return got

        def __delitem__(self, key):
            self._add(len(range(*key.indices(len(self)))) if isinstance(key, slice) else 1)
            list.__delitem__(self, key)

        def index(self, *args):
            self._add(len(self))
            return list.index(self, *args)

        def pop(self, *args):
            self._add(1)
            return list.pop(self, *args)

        def append(self, item):
            self._add(1)
            list.append(self, item)

    _deep = _CountingPage("<p>\n" + "<svg>\n" * 200 + "<math></math>\n" * 200 + "&#1;")
    case_count += 1
    found = _page_syntax_scan(_deep, _CountingStack())
    _CountingPage.count_calls = 0
    message = _page_syntax_error(_deep)
    if found is None or "line 402:" not in (message or "") or _CountingPage.count_calls > 1 \
            or _CountingStack.work > 4 * 600:
        failures.append("page scan work: expected a refusal on line 402 after at most one line count and at "
                        "most 2400 stack operations, got {!r}, {} line counts, {} stack operations"
                        .format(message, _CountingPage.count_calls, _CountingStack.work))
    # The head collector stays linear too: every list it keeps in an attribute, whenever it is assigned,
    # counts the work done on it, so a list search over the open wrappers, or a stack of the svg and math
    # elements open in a head template, fails here. Each input holds at most about 600 tags: wrapper end tags
    # after 200 open templates, 400 svg and math elements in one template, and text or keyed tags inside 200
    # open templates.
    class _CountingCollector(_HeadTagCollector):
        def __setattr__(self, name, value):
            super().__setattr__(name, _CountingStack(value) if type(value) is list else value)

    for label, source in (
            ("wrapper end tags", "<template>" * 200 + "</noscript>" * 200 + "</template>" * 200),
            ("svg and math in a template", "<template>" + "<svg>" * 200 + "<math></math>" * 200 + "</template>"),
            ("text in wrappers", "<template>" * 200 + "<b>x</b>" * 200),
            ("keyed tags in wrappers", "<template>" * 200 + '<meta property="og:title" content="x">' * 200)):
        case_count += 1
        _CountingStack.work = 0
        collector = _CountingCollector(None)
        collector.feed("<head>" + source)
        collector.close()
        if _CountingStack.work > 4 * 600:
            failures.append("head collector work, {}: expected at most 2400 list operations, got {}"
                            .format(label, _CountingStack.work))
    # The repository's own published pages pass (the check site.py runs over them too).
    for _page_path in sorted((Path(__file__).resolve().parents[1] / SITE_DIR).glob("*.html")):
        if _page_path.name == "404.html":
            continue
        case_count += 1
        try:
            _validate_head_tags(_page_path.read_text(encoding="utf-8"), page_url(SITE_DIR + "/" + _page_path.name),
                                SITE_DIR + "/" + _page_path.name)
        except SchemaError as exc:
            failures.append("repository page {}: unexpected SchemaError: {}".format(_page_path.name, exc))
    # page_url derivation: the home page is the bare site root; every other page is its stem.
    case_count += 1
    if page_url("site/index.html") != SITE_BASE_URL:
        failures.append("page_url(index) expected the bare site root")
    case_count += 1
    if page_url("site/rules.html") != SITE_BASE_URL + "rules":
        failures.append("page_url(rules) expected {}rules".format(SITE_BASE_URL))

    # ---- Type-6 HTML-block paragraph interruption fails loud (finding 6) -----------------------
    # A text line immediately followed by a column-0 HTML block opener is an unsupported mixed block:
    # it fails closed rather than silently escaping the opener into the paragraph text.
    _expect_error("type6 interruption fails loud",
                  frontmatter + "text\n<div>x</div>\n", "cannot interrupt a paragraph")
    # Separated by a blank line, the same input renders a paragraph then a raw HTML block.
    _expect_render("type6 blank-separated paragraph",
                   frontmatter + "text\n\n<div>x</div>\n", "<p>text</p>")
    _expect_render("type6 blank-separated html",
                   frontmatter + "text\n\n<div>x</div>\n", "<div>x</div>")

    # ---- Control characters rejected at the boundary (finding 7) -------------------------------
    # A DEL (U+007F) in a frontmatter field value is rejected (TOML accepts it; the field guard does not).
    _expect_error("frontmatter control char",
                  frontmatter.replace('title = "T"', 'title = "T\\u007f"'),
                  "disallowed control character")
    # A C0 control (U+0001) in the body is rejected.
    _expect_error("body control char", frontmatter + "a\x01b\n", "disallowed control character")

    # ---- Unicode whitespace preserved, not deleted (finding 8) ---------------------------------
    # A line of only non-ASCII whitespace stays content (it is not an ASCII blank line), so the
    # paragraph fold preserves it rather than silently erasing the character.
    _expect_render("nbsp preserved", frontmatter + "One\n\u00a0\nTwo\n", "One \u00a0 Two")
    _expect_render("u2028 preserved", frontmatter + "One\n\u2028\nTwo\n", "One \u2028 Two")
    _expect_render("u2029 preserved", frontmatter + "One\n\u2029\nTwo\n", "One \u2029 Two")

    # ---- This round's behavioural fixes (fail-to-pass) -----------------------------------------
    # Change 5: the ATX delimiter must be ASCII space/tab. A hash followed by a non-ASCII space
    # (U+00A0) is NOT a heading; it renders as an escaped paragraph, not <h1>.
    _expect_render("atx nbsp delimiter not heading", frontmatter + "#\u00a0Heading\n",
                   "<p>#\u00a0Heading</p>")
    _expect_absent("atx nbsp delimiter no h1", frontmatter + "#\u00a0Heading\n", "<h1>")
    # Change 4: whole-block trailing normalization strips only ASCII space/tab/newline. A trailing
    # U+00A0 stays content, and a `---` line trailed by U+00A0 is no longer bare `---`, so it renders
    # as a paragraph rather than <hr>.
    _expect_render("trailing nbsp preserved", frontmatter + "One\u00a0\n", "<p>One\u00a0</p>")
    _expect_render("hr plus nbsp is paragraph", frontmatter + "---\u00a0\n", "<p>---\u00a0</p>")
    _expect_absent("hr plus nbsp not hr", frontmatter + "---\u00a0\n", "<hr>")
    # Change 3: a continuation line beginning with a bare `<` + non-tag char is ordinary paragraph
    # text (not an HTML-block opener), so `text\n< 5` folds into one escaped paragraph; a tag-like `<`
    # continuation line still fails loud.
    _expect_render("non-opener < folds into paragraph",
                   frontmatter + "text\n< 5\n", "<p>text &lt; 5</p>")
    _expect_error("tag-like < still interrupts",
                  frontmatter + "text\n<div>x\n", "cannot interrupt a paragraph")
    # Change 2: an anchor carrying a duplicate attribute (here `class`) fails closed at shell-load
    # sidebar reconciliation; a browser keeps the first and this parser the last, so they would
    # disagree on the rendered link.
    _expect_raises(
        "anchor duplicate class attribute",
        lambda: _validate_shell_sidebar(
            nav_base.replace('{{content}}',
                             '<a class="navlink" class="dup" href="/rule1">Dup</a>{{content}}'),
            "shell.html"),
        SchemaError, "duplicate 'class' attribute")

    # ---- CLI discovery reconciliation edge forms (finding 2) -----------------------------------
    # Underscore-prefixed, nested-subdirectory, and symlinked stray .md sources fail loud (exit 2)
    # via main(), rather than being silently skipped by a non-recursive/prefix-excluding discovery.
    import contextlib
    import io as _io
    mod = sys.modules[__name__]

    def _run_main(tmp_repo, argv):
        orig = mod.repo_root
        mod.repo_root = lambda: tmp_repo
        buf = _io.StringIO()
        try:
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
                return main(argv)
        finally:
            mod.repo_root = orig

    with tempfile.TemporaryDirectory() as td2:
        td2 = Path(td2)
        (td2 / "docs").mkdir()
        (td2 / "site").mkdir()
        # The fixture shell carries the full link-preview tag block in its order (the shell's canonical
        # and og:url placeholder lines moved into their block slots), so main()'s per-page head
        # validation has a complete head to accept.
        head_block = "".join(t.format(url="{{canonical}}" if 'rel="canonical"' in t else "{{og_url}}")
                             for t in HEAD_TAG_BLOCK)
        shell_no_block = (nav_base
                          .replace('<link rel="canonical" href="{{canonical}}">', "", 1)
                          .replace('<meta property="og:url" content="{{og_url}}">', "", 1))
        (td2 / "docs" / "_shell.html").write_text(
            shell_no_block.replace("</head>", head_block + "</head>"), encoding="utf-8")
        # Populate every docs/*.md source the live GENSRC_OUTPUTS declares, so this baseline exercises
        # the real registry and stays valid as pages are migrated in (a hardcoded single source would
        # fail closed on the next declared-but-absent page). Each source's canonical and og-url carry
        # that page's OWN URL, the invariant main()'s head validation enforces.
        def _fm_for(tgt):
            url = page_url(tgt)
            return (frontmatter
                    .replace('canonical = "https://aiqt.ai/x"', 'canonical = "{}"'.format(url))
                    .replace('og-url = "https://aiqt.ai/x"', 'og-url = "{}"'.format(url)))

        for src_rel, tgt in _declared_md_sources():
            src_path = td2 / src_rel
            src_path.parent.mkdir(parents=True, exist_ok=True)
            src_path.write_text(_fm_for(tgt) + "hello\n", encoding="utf-8")
        case_count += 1
        if _run_main(td2, []) != 0:
            failures.append("discovery baseline generate: expected exit 0")
        case_count += 1
        if _run_main(td2, ["--check"]) != 0:
            failures.append("discovery baseline check: expected exit 0")
        # A page whose frontmatter canonical carries ANOTHER page's URL fails closed end-to-end
        # (exit 2): its rendered canonical is not its own URL.
        first_src, first_tgt = _declared_md_sources()[0]
        _other_url = SITE_BASE_URL + "somewhere-else"
        (td2 / first_src).write_text(
            _fm_for(first_tgt).replace('canonical = "{}"'.format(page_url(first_tgt)),
                                       'canonical = "{}"'.format(_other_url)) + "hello\n",
            encoding="utf-8")
        case_count += 1
        if _run_main(td2, ["--check"]) != 2:
            failures.append("foreign canonical: expected exit 2")
        (td2 / first_src).write_text(_fm_for(first_tgt) + "hello\n", encoding="utf-8")
        # A shell missing one tag of the block fails closed end-to-end (exit 2) for every page.
        (td2 / "docs" / "_shell.html").write_text(
            shell_no_block.replace("</head>", head_block.replace(
                '<meta name="twitter:card" content="summary_large_image">', '', 1) + "</head>"),
            encoding="utf-8")
        case_count += 1
        if _run_main(td2, ["--check"]) != 2:
            failures.append("shell missing block tag: expected exit 2")
        # A shell carrying a duplicate block tag fails closed end-to-end (exit 2).
        (td2 / "docs" / "_shell.html").write_text(
            shell_no_block.replace("</head>", head_block
                             + '<meta property="og:type" content="website">' + "</head>"),
            encoding="utf-8")
        case_count += 1
        if _run_main(td2, ["--check"]) != 2:
            failures.append("shell duplicate block tag: expected exit 2")
        (td2 / "docs" / "_shell.html").write_text(
            shell_no_block.replace("</head>", head_block + "</head>"), encoding="utf-8")
        # Underscore-prefixed stray (no prefix exemption for .md).
        stray_u = td2 / "docs" / "_stray.md"
        stray_u.write_text(frontmatter + "x\n", encoding="utf-8")
        case_count += 1
        if _run_main(td2, ["--check"]) != 2:
            failures.append("underscore stray .md: expected exit 2")
        stray_u.unlink()
        # Nested-subdirectory stray.
        (td2 / "docs" / "sub").mkdir()
        stray_n = td2 / "docs" / "sub" / "stray.md"
        stray_n.write_text(frontmatter + "x\n", encoding="utf-8")
        case_count += 1
        if _run_main(td2, ["--check"]) != 2:
            failures.append("nested stray .md: expected exit 2")
        stray_n.unlink()
        (td2 / "docs" / "sub").rmdir()
        # Symlinked stray .md is refused outright.
        if symlink_ok:
            stray_l = td2 / "docs" / "link.md"
            os.symlink("rule1.md", stray_l)
            case_count += 1
            if _run_main(td2, ["--check"]) != 2:
                failures.append("symlink stray .md: expected exit 2")
            stray_l.unlink()
        # A symlinked DIRECTORY under docs/ (containing a stray .md) fails closed: the os.scandir walk
        # refuses to follow it, where rglob would have silently skipped the subtree.
        if symlink_ok:
            realdir = td2 / "realdir"
            realdir.mkdir()
            (realdir / "stray.md").write_text(frontmatter + "x\n", encoding="utf-8")
            linkdir = td2 / "docs" / "linkdir"
            os.symlink(realdir, linkdir)
            case_count += 1
            if _run_main(td2, ["--check"]) != 2:
                failures.append("symlinked docs subdir: expected exit 2")
            linkdir.unlink()
        # An unreadable (mode 0o000) directory under docs/ fails closed: the os.scandir walk raises
        # rather than silently skipping the subtree. Skipped under root (os.geteuid()==0), where mode
        # 0o000 does not deny the owner and the walk would read it clean.
        if not (hasattr(os, "geteuid") and os.geteuid() == 0):
            secret = td2 / "docs" / "secret"
            secret.mkdir()
            os.chmod(secret, 0o000)
            try:
                case_count += 1
                if _run_main(td2, ["--check"]) != 2:
                    failures.append("unreadable docs subdir: expected exit 2")
            finally:
                os.chmod(secret, 0o755)
                secret.rmdir()
        # site/ symlinked outside the root, or to a directory inside it, fails closed (exit 2) in --check and
        # write mode with the page there untouched and nothing under it read: main() refuses a site/ that is a
        # symlink at all, a stricter rule than reconcile's directory probe, which follows a link inside the root.
        if symlink_ok:
            import shutil

            def _run_main_out(tmp_repo, argv):
                """_run_main that also returns the output, reading reconcile's SystemExit as its exit code."""
                orig = mod.repo_root
                mod.repo_root = lambda: tmp_repo
                buf = _io.StringIO()
                try:
                    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
                        try:
                            code = main(argv)
                        except SystemExit as exc:
                            code = exc.code
                finally:
                    mod.repo_root = orig
                return code, buf.getvalue()

            target_rel = _declared_md_sources()[0][1]
            refusal = "{}/ is a symbolic link; refusing".format(SITE_DIR)
            # These legs need the page an earlier leg generated; when generation failed (a failure already
            # recorded above), say so rather than crash before the failure list is printed.
            legs = ("outside", "in-root")
            if not (td2 / target_rel).is_file():
                failures.append("site/ symlinked: {} was not generated, so its legs could not run"
                                .format(target_rel))
                legs = ()
            with tempfile.TemporaryDirectory() as outside_td:
                for where in legs:
                    held = Path(outside_td) / SITE_DIR if where == "outside" else td2 / "shared" / SITE_DIR
                    held.parent.mkdir(exist_ok=True)
                    shutil.move(str(td2 / SITE_DIR), str(held))
                    os.symlink(held if where == "outside" else os.path.relpath(held, td2), td2 / SITE_DIR)
                    page = held / Path(target_rel).relative_to(SITE_DIR)
                    os.utime(page, ns=(10 ** 9, 10 ** 9))
                    try:
                        for argv in (["--check"], []):
                            case_count += 1
                            code, out = _run_main_out(td2, argv)
                            written = os.stat(page).st_mtime_ns != 10 ** 9
                            if code != 2 or refusal not in out or written:
                                failures.append("site/ symlinked {} the root ({}): expected exit 2 with {!r} and {} "
                                                "untouched, got exit {}, written {}, output {!r}".format(
                                                    "outside" if where == "outside" else "inside",
                                                    " ".join(argv) or "write", refusal, target_rel, code, written,
                                                    out))
                    finally:
                        (td2 / SITE_DIR).unlink()
                        shutil.move(str(held), str(td2 / SITE_DIR))

    _ = stat  # `stat` imported for potential platform check; retained if the fixture path grows.

    if failures:
        print("SELF-TEST FAIL:")
        for line in failures:
            print("  - " + line)
        return 1
    print("PASS: gen_site self-test ({} cases)".format(case_count))
    return 0


if __name__ == "__main__":
    if "--self-test" in sys.argv[1:]:
        sys.exit(_self_test())
    sys.exit(main(sys.argv[1:]))
