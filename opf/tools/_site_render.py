#!/usr/bin/env python3
"""The OPF site renderer: the opfiles.ai page rendering of tools/gen_draft_site.py, copied so the opf/ tree
renders its own pages from its own inputs, with no import from the parent repository.

  _site_render.py --check      render every page of site/draft-src/ and compare it with the committed page
                               under site/: exit 0 when all 9 match byte for byte, 1 on drift, 2 when a page
                               is refused or a page or input cannot be read
  _site_render.py --self-test  assert the 9-page render, the AIQT-only refusals and the parent-free import

Scope. This module renders; it writes nothing. tools/gen_draft_site.py in the parent repository stays the
one owner (the writer and the drift gate) of the 9 pages under opf/site/ and of the status lines, so a page
that differs here and not there is a defect in this copy, and --check reports it. Every path is named from
the OPF root (the parent of this file's directory): the page data under site/draft-src/, the committed
pages under site/, the kept live paths under site/, and VERB_STATUS in tools/opf.py for the $OPF_STATUS
token. A page data directory that resolves outside the OPF root (a symbolic link out of it) is refused, and
every page data file, committed page and tools/opf.py must be a regular file, not a symbolic link; a
directory above a committed page or tools/opf.py is followed as the file system resolves it, and a kept
live path is checked only for existence. Only the standard library is imported.

Copied unchanged from tools/gen_draft_site.py: the page shell (render_page: head, top bar, light and dark
switch, family footer, live region), the About region, every component that an opfiles.ai page can use,
the page data model and its refusals, the code-style refusals (refusals_for) and the structural link
guard (_LinkGuard). Left out, because they belong to aiqt.ai alone: the document control strip (control,
which reads the AIQT VERSION and changelog.toml), the mappings bodies (mappings, which read the AIQT rule
corpus and standards manifests through gen_mappings, gen_rules and gen_agents) and the $HOOK_COUNT token
(which reads .aiqt/core/hooks/manifest.toml). A page data file naming either component, or carrying the
$HOOK_COUNT token in any string value (a TOML escape included, since the check runs on parsed values), is
refused (exit 2) with a message naming it as AIQT-only, never rendered with a guess. Refused here and not
by the parent: a component name that is not a string, a page data directory outside the OPF root, and a
tools/opf.py that is not a regular file or is larger than MAX_STATUS_SOURCE_BYTES (each exit 2).
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: _site_render.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import ast
import html as _html
import os
import re
import shutil
import signal
import stat
import subprocess
import tempfile
import tomllib
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote

# The OPF root: every input path below resolves from it, never from the parent repository.
ROOT = Path(__file__).resolve().parents[1]

# The staging base path; each site's own base (SITES) is this while staging and "/" at its promotion.
DRAFT_BASE = "/draft/"
ASSETS = "assets/baseline/"
# The draft folder of a promoted site: a page under it (the /draft/ placeholder) keeps the robots noindex
# meta that every page carried while staging.
DRAFT_DIR = "draft/"
MAX_PAGE_BYTES = 1 << 20
# The link-preview card each site's pages name as og:image and twitter:image: a PNG under the site's live root
# (rendered by tools/render_og_cards.py from tools/og-cards/), at the size the link previews ask for.
CARD = "assets/{}-og.png"
CARD_WIDTH, CARD_HEIGHT = 1200, 627
# The light and dark switch (template specification section 7.33): scheme.js loads in the head on every page,
# before the stylesheets, with data-cfasync="false" before src and no defer or async, so a stored scheme
# applies before the first paint; the button is the last child of header.mast, after nav.nav, and stays
# hidden until the script shows it. The markup is the reference header's, byte for byte.
SCHEME_SCRIPT = "scheme.js"
_SCHEME_GLYPH = ('viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" stroke-width="1.8" '
                 'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"')
SCHEME_BUTTON = (
    '<button class="scheme" type="button" data-scheme-toggle aria-pressed="false" hidden>'
    '<svg class="moon" ' + _SCHEME_GLYPH + '><path d="M20.5 14.2A8.5 8.5 0 0 1 9.8 3.5a8.5 8.5 0 1 0 10.7 10.7z"/></svg>'
    '<svg class="sun" ' + _SCHEME_GLYPH + '><circle cx="12" cy="12" r="4.2"/><path d="M12 2.5v2.2M12 19.3v2.2M2.5 '
    '12h2.2M19.3 12h2.2M5.3 5.3l1.6 1.6M17.1 17.1l1.6 1.6M5.3 18.7l1.6-1.6M17.1 6.9l1.6-1.6"/></svg>'
    '<span class="vh">Dark mode</span></button>')


# The 6 project sites in rainbow order (specification section 1): key, name word, domain, footer slice.
FAMILY = (
    ("secureconfig", "SecureConfig", "secureconfig.ai", 1),
    ("opfiles", "OPFiles", "opfiles.ai", 2),
    ("auditthis", "AuditThis", "auditthis.ai", 3),
    ("cleanlanguage", "CleanLanguage", "cleanlanguage.ai", 4),
    ("aiqt", "AIQT", "aiqt.ai", 5),
    ("grclibrary", "GRCLibrary", "grclibrary.ai", 6),
)

# The opfiles.ai kept live paths: its machine-readable entry point and its adoption guide, each of which
# exists under opf/site/ today and keeps its path at promotion.
OPF_KEPT_LIVE_PATHS = (
    "/adopt.md",
    "/llms.txt",
)

# The opfiles.ai site context, as tools/gen_draft_site.py declares it in SITES, with its paths rebased from
# the parent repository's opf/ prefix to the OPF root ("opf/site/draft-src" is "site/draft-src" here). The
# "control" flag stays false: the document control strip belongs to aiqt.ai only.
SITE = {"key": "opfiles", "name": "OPFiles", "src": "site/draft-src", "root": "site",
        "live": "site", "base": "/", "assets": DRAFT_BASE, "control": False,
        "logo-alt": "A machine-written source record and its generated view, joined by a drift gate",
        "github": "https://github.com/jposluns/guardrails/tree/main/opf",
        "kept": OPF_KEPT_LIVE_PATHS,
        "nav": (("model/", "The model"), ("standard/", "The standard"), ("core-files/", "Technical details"),
                ("adopt/", "Adopt"))}

# The 9 pages the site renders, relative to the OPF root; the parent declares the same 9 under opf/ in
# tools/gen_draft_site.py GENSRC_OUTPUTS. A page that lands or vanishes changes this tuple with it.
OPF_PAGES = (
    "site/404.html",
    "site/adopt/index.html",
    "site/core-files/index.html",
    "site/disclosure/index.html",
    "site/draft/index.html",
    "site/index.html",
    "site/license/index.html",
    "site/model/index.html",
    "site/standard/index.html",
)

# The page components and the inline token that read AIQT inputs: refused by name, never rendered.
AIQT_ONLY_COMPONENTS = ("control", "mappings")
AIQT_ONLY_TOKEN = "$HOOK_COUNT"


class RefusedPage(Exception):
    """A built page or a page data file breaks a rule; nothing is written."""


def _url(site, path=""):
    return site["base"] + path


def _asset(site, path):
    return site["assets"] + ASSETS + path


def _asset_folder(site):
    """The site's template asset folder under its live root, the one its asset URLs name."""
    return "{}/{}{}".format(site["live"], site["assets"].lstrip("/"), ASSETS)


def _page_url(site, rel):
    """The served URL of a page path under the site's draft root: index.html is its folder."""
    if rel == "index.html":
        return _url(site)
    if rel.endswith("/index.html"):
        return _url(site, rel[:-len("index.html")])
    return _url(site, rel)


def _origin(site):
    """The site's https origin, from its FAMILY domain."""
    return "https://" + next(domain for key, _word, domain, _n in FAMILY if key == site["key"])


def _social_metas(site, page):
    """The Open Graph and Twitter card metas: the page's absolute URL and full title, the site name, and the
    site's card image with its size and alt text."""
    image = "{}/{}".format(_origin(site), CARD.format(site["key"]))
    values = (("property", "og:type", "website"),
              ("property", "og:title", "{} | {}.ai".format(page["title"], site["name"])),
              ("property", "og:url", _origin(site) + _page_url(site, page["rel"])),
              ("property", "og:site_name", site["name"] + ".ai"),
              ("property", "og:image", image),
              ("property", "og:image:width", str(CARD_WIDTH)),
              ("property", "og:image:height", str(CARD_HEIGHT)),
              ("property", "og:image:alt", "{}.ai: {}".format(site["name"], site["logo-alt"])),
              ("name", "twitter:card", "summary_large_image"),
              ("name", "twitter:image", image))
    return ['<meta {}="{}" content="{}">'.format(kind, key, _attr(value)) for kind, key, value in values]


def _t(value):
    """A data value that is text, not markup, escaped as element text."""
    return _html.escape(value, quote=False)


def _attr(value):
    """A data value escaped as a double-quoted attribute value."""
    return _html.escape(value, quote=True)


def _external(href, label):
    """A new-tab anchor: href is a raw URL, escaped here as an attribute value; label is markup."""
    return ('<a href="{}" target="_blank" rel="noopener noreferrer">{}<span class="vh"> (opens in a new tab)'
            '</span></a>').format(_attr(href), label)


def _brand(site):
    return ('<a class="brand" href="{}"><span class="name">{}<span class="tld">.ai</span></span>'
            '<span class="rule" aria-hidden="true"></span></a>').format(_url(site), site["name"])


def _nav(site, pages, current):
    registered = {_page_url(site, page["rel"]) for page in pages}
    items = []
    for path, label in site["nav"]:
        href = _url(site, path)
        if href not in registered:
            continue
        mark = ' aria-current="page"' if href == current else ""
        items.append('<a href="{}"{}>{}</a>'.format(href, mark, label))
    items.append(_external(site["github"], "GitHub"))
    return '<nav class="nav" aria-label="Site">{}</nav>'.format("".join(items))


def _footer(site):
    order = [entry for entry in FAMILY]
    start = [entry[0] for entry in order].index(site["key"])
    cards = []
    for key, word, domain, number in order[start:] + order[:start]:
        image = '<img src="{}" alt="">'.format(_asset(site, "logos/{}.svg".format(key)))
        name = '<span class="cname">{}<span class="tld">.ai</span></span>'.format(word)
        if key == site["key"]:
            link = '<a class="card" href="{}" aria-current="page">{}{}<span class="vh"> (this site)</span></a>'.format(
                _url(site), image, name)
        else:
            link = ('<a class="card" href="https://{}/" target="_blank" rel="noopener noreferrer">{}{}'
                    '<span class="vh"> (opens in a new tab)</span></a>').format(domain, image, name)
        cards.append('<li class="slice slice-{}">{}</li>'.format(number, link))
    newtab = '<span class="vh"> (opens in a new tab)</span>'
    tail = (
        '<li class="tile tile-1"><a class="card" href="https://github.com/jposluns" target="_blank" '
        'rel="noopener noreferrer"><img class="only-light" src="{}" alt=""><img class="only-dark" src="{}" '
        'alt=""><span class="cname vh">GitHub</span>{}</a></li>'
        '<li class="tile tile-2"><a class="card" href="https://www.linkedin.com/in/jposluns/" target="_blank" '
        'rel="noopener noreferrer"><img src="{}" alt=""><span class="cname vh">LinkedIn</span>{}</a></li>'
        '<li class="tile tile-3"><a class="card" href="https://orcid.org/0009-0000-7775-2233" target="_blank" '
        'rel="noopener noreferrer"><img src="{}" alt=""><span class="cname vh">ORCID</span>{}</a></li>'
    ).format(_asset(site, "marks/github-invertocat-black.svg"), _asset(site, "marks/github-invertocat-white.svg"),
             newtab, _asset(site, "marks/linkedin-in-bug.png"), newtab, _asset(site, "marks/orcid-id.svg"), newtab)
    sig = ('<div class="tile sigtile"><a class="sig" href="https://posluns.ca/" target="_blank" '
           'rel="noopener noreferrer" aria-label="A Jeff at Posluns dot ca project: posluns.ca (opens in a new '
           'tab)"><span class="pre">A </span>Jeff<span class="gold">@</span>Posluns<span class="gold">.ca</span>'
           '<span class="post"> Project</span></a></div>')
    return ('<footer class="family wrap"><div class="bar"><ul class="cards rainbow" aria-label="The 6 project '
            'sites">{}</ul><ul class="tail" aria-label="Jeff Posluns elsewhere">{}</ul>{}</div></footer>').format(
                "".join(cards), tail, sig)


def _about(site):
    """The "About this project" region (specification section 7.24, reference/landing.html). One ordinary
    space follows each dot and none precedes it, so the line breaks only after a dot."""
    dot = '<span class="dot" aria-hidden="true">&middot;</span> '
    return ('<div class="region"><p class="lbl">About this project</p><div><p class="about">'
            '<a href="{}">Disclosure</a>{}<a href="{}">Licence</a>{}{}{}{}</p></div></div>').format(
                _url(site, "disclosure/"), dot, _url(site, "license/"), dot,
                _external("https://posluns.ca/privacy/", "Privacy"), dot,
                _external("https://posluns.ca/security/", "Security"))


# ---------------------------------------------------------------------------------------------------
# The page data model: validation.

INTERNAL_HREF_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9/_.-]*/?(#[A-Za-z0-9_-]+)?|#[A-Za-z0-9_-]+|")
REL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*(/[A-Za-z0-9][A-Za-z0-9_-]*)*\.html")
ID_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]*")
KINDS = ("landing", "document")
CODE_VARIANTS = ("plain", "prompt-block", "prompt-flow")
PLAN_VARIANTS = ("", "one", "three")


def _need(doc, where, required, optional=()):
    """The table as a dict after its key checks: every required key present and no unknown key. Value types
    are not checked here; each caller checks its own (_text, _flag, _items and the component renderers)."""
    unknown = sorted(set(doc) - set(required) - set(optional))
    if unknown:
        raise RefusedPage("{}: unknown key(s) {}".format(where, ", ".join(unknown)))
    missing = sorted(set(required) - set(doc))
    if missing:
        raise RefusedPage("{}: missing key(s) {}".format(where, ", ".join(missing)))
    return dict(doc)


def _text(doc, where, key, default=None):
    value = doc.get(key, default)
    if not isinstance(value, str) or (value == "" and default is None):
        raise RefusedPage("{}: {} must be a non-empty string".format(where, key))
    return value


def _flag(doc, where, key):
    value = doc.get(key, False)
    if not isinstance(value, bool):
        raise RefusedPage("{}: {} must be a boolean".format(where, key))
    return value


def _items(doc, where, key, required, optional=()):
    value = doc.get(key)
    if not isinstance(value, list) or not value or not all(isinstance(item, dict) for item in value):
        raise RefusedPage("{}: {} must be a non-empty array of tables".format(where, key))
    out = []
    for position, item in enumerate(value, 1):
        item_where = "{}.{}[{}]".format(where, key, position)
        checked = _need(item, item_where, required, optional)
        for name in list(required) + list(optional):
            if name in checked and not isinstance(checked[name], str):
                raise RefusedPage("{}: {} must be a string".format(item_where, name))
        out.append(checked)
    return out


def _kept_live(site, target):
    """True only for a root-relative target whose path (before an optional #fragment) is one of the
    site's kept live paths; both ends of the shape are checked, so "/evidence2" and a malformed fragment
    stay refused."""
    if not isinstance(target, str) or not target.startswith("/"):
        return False
    path, sep, frag = target.partition("#")
    if path not in site["kept"]:
        return False
    return not sep or ID_RE.fullmatch(frag) is not None


def _internal_href(site, href, where):
    """The site's base + href for a site-relative target ("" is the home page), or one of the site's kept
    live paths unchanged; refused outside those shapes."""
    if isinstance(href, str) and href.startswith("/"):
        if _kept_live(site, href):
            return href
        raise RefusedPage("{}: {!r} is not a site-relative href or a kept live path".format(where, href))
    if not isinstance(href, str) or href.lower().startswith("http") \
            or ".." in href or INTERNAL_HREF_RE.fullmatch(href) is None:
        raise RefusedPage("{}: {!r} is not a site-relative href".format(where, href))
    return _url(site, href)


def _any_link(site, href, text, where):
    """An anchor for an internal site-relative href, or a new-tab anchor for an https URL."""
    if isinstance(href, str) and href.startswith("https://"):
        return _external(href, text)
    return '<a href="{}">{}</a>'.format(_attr(_internal_href(site, href, where)), text)


def _inline(site, value):
    """An inline HTML value with the $BASE/ token rendered as the site's base path and the $OPF_STATUS
    token rendered as the reference CLI status note (status_note) of VERB_STATUS in the site's "tree" (the
    tree build() is given; ROOT when the site names none). The $HOOK_COUNT token is AIQT-only and refused
    here as well as at page load (_aiqt_token_where)."""
    if AIQT_ONLY_TOKEN in value:
        raise RefusedPage("the {} token is AIQT-only (it counts the AIQT hooks manifest); an OPF page "
                          "cannot carry it".format(AIQT_ONLY_TOKEN))
    if "$OPF_STATUS" in value:
        value = value.replace("$OPF_STATUS", status_note(verb_status(site.get("tree", ROOT))))
    return value.replace("$BASE/", site["base"])


def _aiqt_token_where(doc, where):
    """The location of the first string value (a table key included) anywhere in a parsed page that
    carries the AIQT-only $HOOK_COUNT token, or None. The walk keeps its own stack, so a deeply nested
    page cannot exhaust the interpreter's recursion limit."""
    stack = [(doc, where)]
    while stack:
        value, at = stack.pop()
        if isinstance(value, str):
            if AIQT_ONLY_TOKEN in value:
                return at
        elif isinstance(value, dict):
            for key, item in value.items():
                if AIQT_ONLY_TOKEN in key:
                    return "{}.{}".format(at, key)
                stack.append((item, "{}.{}".format(at, key)))
        elif isinstance(value, list):
            for position, item in enumerate(value, 1):
                stack.append((item, "{}[{}]".format(at, position)))
    return None


# The reference CLI status note: generated from VERB_STATUS in tools/opf.py, read as a literal from the
# file's syntax tree (the module is never imported or run), and rendered wherever the $OPF_STATUS token
# appears. The status lines outside the pages (opf/README.md, opf/spec/OPF-SPEC.md, opf/site/llms.txt)
# stay with the parent generator, tools/gen_draft_site.py.
STATUS_SOURCE = "tools/opf.py"
STATUS_PREFIX = "Reference CLI status. "
_STATUS_KINDS = ("available", "partial", "in progress")
# The largest tools/opf.py read for VERB_STATUS (the file is about 1.4 MB today).
MAX_STATUS_SOURCE_BYTES = 1 << 23


def verb_status(root=ROOT):
    """VERB_STATUS from tools/opf.py under root, read as a literal: a tuple of (verb, status, scope)
    rows. RefusedPage when the file is not a regular file (a symbolic link, a FIFO or any other entry,
    checked with lstat before it is opened, so a FIFO cannot block the read), is larger than
    MAX_STATUS_SOURCE_BYTES, cannot be read or parsed, binds it other than once at top level, or holds a
    row that is not 3 strings with a known status."""
    path = Path(root) / STATUS_SOURCE
    import ast
    try:
        info = os.lstat(path)
        if not stat.S_ISREG(info.st_mode):
            raise OSError("{} is not a regular file (a symbolic link, a FIFO or another entry)".format(path))
        if info.st_size > MAX_STATUS_SOURCE_BYTES:
            raise ValueError("{} is larger than {} bytes".format(path, MAX_STATUS_SOURCE_BYTES))
        with open(path, "rb") as handle:
            data = handle.read(MAX_STATUS_SOURCE_BYTES + 1)
        if len(data) > MAX_STATUS_SOURCE_BYTES:
            raise ValueError("{} is larger than {} bytes".format(path, MAX_STATUS_SOURCE_BYTES))
        source = data.decode("utf-8")
        tree = ast.parse(source, str(path))
        nodes = [node.value for node in tree.body if isinstance(node, ast.Assign)
                 and any(isinstance(t, ast.Name) and t.id == "VERB_STATUS" for t in node.targets)]
        if len(nodes) != 1:
            raise ValueError("VERB_STATUS is bound {} times at top level".format(len(nodes)))
        table = ast.literal_eval(nodes[0])
    except (OSError, UnicodeDecodeError, SyntaxError, ValueError, TypeError, RecursionError,
            MemoryError) as exc:
        raise RefusedPage("{}: cannot read VERB_STATUS ({}: {})".format(STATUS_SOURCE, type(exc).__name__, exc))
    if not isinstance(table, tuple) or not table or not all(
            isinstance(row, tuple) and len(row) == 3 and all(isinstance(cell, str) for cell in row)
            and row[1] in _STATUS_KINDS for row in table):
        raise RefusedPage("{}: VERB_STATUS is not a tuple of (verb, status, scope) rows with a known "
                          "status".format(STATUS_SOURCE))
    return table


def status_note(table=None):
    """The 1-line status note for `table` (default verb_status()): the available verbs, each partial verb
    with its scope, and the in progress verbs, each group in table order; an empty group is left out."""
    table = verb_status() if table is None else table
    parts = [STATUS_PREFIX.rstrip()]
    available = [verb for verb, status, _scope in table if status == "available"]
    partial = ["{} ({})".format(verb, scope) for verb, status, scope in table if status == "partial"]
    pending = [verb for verb, status, _scope in table if status == "in progress"]
    if available:
        parts.append("Available: {}.".format(", ".join(available)))
    if partial:
        parts.append("Partial: {}.".format(", ".join(partial)))
    if pending:
        parts.append("In progress: {}; {} in this build.".format(
            ", ".join(pending), "each exits 2" if len(pending) > 1 else "it exits 2"))
    return " ".join(parts)


# ---------------------------------------------------------------------------------------------------
# Components (specification section 7). Each renderer returns the component's exact markup.

def _c_hero(site, doc, where):
    spec = _need(doc, where, ("component", "kicker", "statement", "intro", "start_text", "start_href"),
                 ("two",))
    classes = "statement two" if _flag(spec, where, "two") else "statement"
    return ('<section class="hero" aria-label="Introduction"><div class="hero-text">'
            '<p class="lbl gold">{}</p><h1 class="{}">{}</h1><p class="intro">{}</p>'
            '<a class="start" href="{}">{} <span class="arrow" aria-hidden="true">&rarr;</span></a></div>'
            '<div class="hero-art"><img src="{}" alt="{}"></div></section>').format(
                _inline(site, _text(spec, where, "kicker")), classes,
                _inline(site, _text(spec, where, "statement")), _inline(site, _text(spec, where, "intro")),
                _attr(_internal_href(site, spec["start_href"], where)), _t(_text(spec, where, "start_text")),
                _asset(site, "logos/{}.svg".format(site["key"])),
                site["logo-alt"])


def _c_entries(site, doc, where):
    spec = _need(doc, where, ("component", "items"), ("label",))
    label = _t(_text(spec, where, "label", "Start here"))
    tiles = []
    for item in _items(spec, where, "items", ("href", "title", "n", "d")):
        tiles.append('<li><a href="{}"><span class="t">{}</span><span class="n">{}</span>'
                     '<span class="d">{}</span></a></li>'.format(
                         _attr(_internal_href(site, item["href"], where)), _inline(site, item["title"]),
                         _inline(site, item["n"]), _inline(site, item["d"])))
    return ('<div class="region"><p class="lbl">{}</p><div><ol class="entries">{}</ol></div></div>').format(
        label, "".join(tiles))


def _c_region(site, doc, where):
    spec = _need(doc, where, ("component", "label", "html"))
    return '<div class="region"><p class="lbl">{}</p><div>{}</div></div>'.format(
        _t(_text(spec, where, "label")), _inline(site, _text(spec, where, "html")))


def _c_value(site, doc, where):
    spec = _need(doc, where, ("component", "html"), ("label",))
    return ('<div class="region"><p class="lbl">{}</p><div><p class="value">{}</p></div></div>').format(
        _t(_text(spec, where, "label", "What it gives you")), _inline(site, _text(spec, where, "html")))


def _c_about(site, doc, where):
    _need(doc, where, ("component",))
    return _about(site)


def _c_clause(site, doc, where):
    spec = _need(doc, where, ("component", "id", "num", "title", "lbl"), ("rail",))
    return ('<p class="lbl sec">{}</p>\n<h2 class="clause" id="{}"><span class="num">{}</span>'
            '<span>{}</span></h2>').format(_t(_text(spec, where, "lbl")), _checked_id(spec, where),
                                           _t(_text(spec, where, "num")), _inline(site, _text(spec, where, "title")))


def _c_clauses(site, doc, where):
    spec = _need(doc, where, ("component", "items"))
    rows = ['<li><span class="n">{}</span><div>{}</div></li>'.format(_t(item["n"]), _inline(site, item["html"]))
            for item in _items(spec, where, "items", ("n", "html"))]
    return '<ol class="clauses">{}</ol>'.format("".join(rows))


def _c_note(site, doc, where):
    spec = _need(doc, where, ("component", "label", "html"))
    return '<div class="note"><span class="label">{}</span><div>{}</div></div>'.format(
        _t(_text(spec, where, "label")), _inline(site, _text(spec, where, "html")))


def _c_callout(site, doc, where):
    spec = _need(doc, where, ("component", "html"), ("label", "rule"))
    classes = "callout rule" if _flag(spec, where, "rule") else "callout"
    body = '<div class="{}">{}</div>'.format(classes, _inline(site, _text(spec, where, "html")))
    if "label" in spec:
        return '<p class="lbl accent blk">{}</p>\n{}'.format(_t(_text(spec, where, "label")), body)
    return body


def _c_figures(site, doc, where):
    spec = _need(doc, where, ("component", "items"))
    figs = ['<div class="fig"><span class="num">{}</span><span class="cap">{}</span></div>'.format(
        _t(item["num"]), _t(item["cap"])) for item in _items(spec, where, "items", ("num", "cap"))]
    return '<div class="wide"><div class="figures">{}</div></div>'.format("".join(figs))


def _table_html(site, spec, where):
    sticky = _flag(spec, where, "sticky")
    classes = " ".join(name for name, on in (("sticky", sticky),
                                             ("fixed", _flag(spec, where, "fixed"))) if on)
    cols = ""
    if "cols" in spec:
        value = spec["cols"]
        if not isinstance(value, list) or not value or not all(
                isinstance(col, str) and re.fullmatch(r"w[0-9]{1,2}", col) for col in value):
            raise RefusedPage("{}: cols must be an array of wN column classes".format(where))
        cols = "<colgroup>{}</colgroup>".format("".join('<col class="{}">'.format(col) for col in value))
    head = spec.get("head")
    if not isinstance(head, list) or not head or not all(isinstance(cell, str) for cell in head):
        raise RefusedPage("{}: head must be a non-empty array of strings".format(where))
    rows = spec.get("rows")
    if not isinstance(rows, list) or not rows:
        raise RefusedPage("{}: rows must be a non-empty array".format(where))
    body = []
    for number, row in enumerate(rows, 1):
        if not isinstance(row, list) or len(row) != len(head) or not all(isinstance(cell, str) for cell in row):
            raise RefusedPage("{}: row {} must be an array of {} cell strings".format(where, number, len(head)))
        cells = []
        for cell in row:
            if cell.startswith("<td"):
                if not cell.endswith("</td>"):
                    raise RefusedPage("{}: row {}: a raw cell must be a whole td element".format(where, number))
                cells.append(_inline(site, cell))
            else:
                cells.append("<td>{}</td>".format(_inline(site, cell)))
        body.append("<tr>{}</tr>".format("".join(cells)))
    table = ('<table class="{}"><caption class="vh">{}</caption>{}<thead><tr>{}</tr></thead>'
             '<tbody>{}</tbody></table>').format(
                 classes, _t(_text(spec, where, "caption")), cols,
                 "".join("<th>{}</th>".format(_inline(site, cell)) for cell in head), "".join(body))
    # A sticky table's wrapper gets the bounded class (template specification section 7.31); any other
    # table's wrapper does not. The template's :has() fallback bounds an unmarked wrapper only when it
    # directly holds a sticky table, and only in browsers that support :has().
    wrap = "tablewrap bounded" if sticky else "tablewrap"
    return '<div class="{}" tabindex="0">{}</div>'.format(wrap, table)


def _c_table(site, doc, where):
    spec = _need(doc, where, ("component", "caption", "head", "rows"), ("sticky", "fixed", "cols"))
    return '<div class="wide">{}</div>'.format(_table_html(site, spec, where))


def _c_register(site, doc, where):
    spec = _need(doc, where, ("component", "reg", "title", "count", "caption", "head", "rows"),
                 ("sticky", "fixed", "cols"))
    cap = '<p class="reg-cap"><b>{}</b><span>{}</span><span class="count">{}</span></p>'.format(
        _t(_text(spec, where, "reg")), _t(_text(spec, where, "title")), _t(_text(spec, where, "count")))
    return '<div class="wide"><div class="register">{}{}</div></div>'.format(
        cap, _table_html(site, spec, where))


def _c_code(site, doc, where):
    spec = _need(doc, where, ("component", "id", "text"), ("variant", "wide", "note", "file"))
    variant = _text(spec, where, "variant", "plain")
    if variant not in CODE_VARIANTS:
        raise RefusedPage("{}: variant must be one of {}".format(where, ", ".join(CODE_VARIANTS)))
    block_id = _checked_id(spec, where)
    classes, extra = "hascopy", ""
    if variant == "prompt-block":
        classes, extra = "hascopy prompt-block", ' tabindex="0"'
    elif variant == "prompt-flow":
        classes = "hascopy prompt-flow"
    pre = ('<pre id="{}" class="{}"{}><button class="copy" type="button" data-copy="{}" hidden>Copy</button>'
           '{}</pre>').format(block_id, classes, extra, block_id,
                              _html.escape(_text(spec, where, "text"), quote=False))
    if _flag(spec, where, "wide"):
        pre = '<div class="wide">{}</div>'.format(pre)
    if "file" in spec:
        # The file label bar names the block's file path above it (`.lbl.file`).
        pre = '<p class="lbl file">{}</p>\n{}'.format(_html.escape(_text(spec, where, "file"), quote=False), pre)
    if "note" in spec:
        pre += '\n<p class="code-note">{}</p>'.format(_inline(site, _text(spec, where, "note")))
    return pre


def _c_picker(site, doc, where):
    spec = _need(doc, where, ("component", "items", "reset"))
    links = []
    for item in _items(spec, where, "items", ("family", "label")):
        if ID_RE.fullmatch(item["family"]) is None:
            raise RefusedPage("{}: {!r} is not a valid family id".format(where, item["family"]))
        links.append('<li><a href="#{0}" data-family-link="{0}">{1}</a></li>'.format(
            item["family"], _t(item["label"])))
    return ('<ul class="picker" data-picker>{}</ul>'
            '<button class="btn picker-reset" type="button" data-picker-reset hidden>{}</button>'
            '<p class="vh" role="status" aria-live="polite" data-picker-status></p>').format(
                "".join(links), _t(_text(spec, where, "reset")))


def _c_family(site, doc, where):
    spec = _need(doc, where, ("component", "family", "lbl", "heading", "verified"),
                 ("intro", "steps", "after"))
    family = _text(spec, where, "family")
    if ID_RE.fullmatch(family) is None:
        raise RefusedPage("{}: {!r} is not a valid family id".format(where, family))
    parts = ['<p class="lbl sec">{}</p>'.format(_t(_text(spec, where, "lbl"))),
             "<h2>{}</h2>".format(_inline(site, _text(spec, where, "heading"))),
             '<p class="verified">{}</p>'.format(_inline(site, _text(spec, where, "verified")))]
    if "intro" in spec:
        parts.append(_inline(site, _text(spec, where, "intro")))
    if "steps" in spec:
        steps = spec["steps"]
        if not isinstance(steps, list) or not steps or not all(isinstance(step, str) for step in steps):
            raise RefusedPage("{}: steps must be a non-empty array of strings".format(where))
        parts.append('<ol class="steps">{}</ol>'.format(
            "".join("<li><span>{}</span></li>".format(_inline(site, step)) for step in steps)))
    if "after" in spec:
        parts.append(_inline(site, _text(spec, where, "after")))
    return '<section id="{0}" data-family-section="{0}">\n{1}\n</section>'.format(family, "\n".join(parts))


def _c_download(site, doc, where):
    spec = _need(doc, where, ("component", "href", "filename", "size", "html"), ("label",))
    link = _any_link(site, _text(spec, where, "href"), _t(_text(spec, where, "filename")), where)
    body = ('<div class="download"><p class="file">{}</p><p class="lbl size">{}</p><p>{}</p></div>').format(
        link, _t(_text(spec, where, "size")), _inline(site, _text(spec, where, "html")))
    if "label" in spec:
        return '<p class="lbl blk">{}</p>\n{}'.format(_t(_text(spec, where, "label")), body)
    return body


def _c_plan(site, doc, where):
    spec = _need(doc, where, ("component", "items"), ("variant", "described"))
    variant = _text(spec, where, "variant", "")
    if variant not in PLAN_VARIANTS:
        raise RefusedPage("{}: variant must be one of one, three".format(where))
    classes = "plan" + ((" " + variant) if variant else "") + (" described" if _flag(spec, where, "described") else "")
    rows = []
    for item in _items(spec, where, "items", ("href", "title"), ("n", "small")):
        row = '<a href="{}">{}</a>'.format(_attr(_internal_href(site, item["href"], where)),
                                           _inline(site, item["title"]))
        if "n" in item:
            row += '<span class="n">{}</span>'.format(_inline(site, item["n"]))
        if "small" in item:
            row += "<small>{}</small>".format(_inline(site, item["small"]))
        rows.append("<li>{}</li>".format(row))
    return '<ul class="{}">{}</ul>'.format(classes, "".join(rows))


def _c_scope(site, doc, where):
    spec = _need(doc, where, ("component", "html"))
    return '<p class="lbl blk">Scope</p>\n<p>{}</p>'.format(_inline(site, _text(spec, where, "html")))


def _c_next(site, doc, where):
    spec = _need(doc, where, ("component", "text", "href"), ("html",))
    line = ""
    if "html" in spec:
        line = "<p>{}</p>".format(_inline(site, _text(spec, where, "html")))
    arrow = '<a class="start" href="{}">{} <span class="arrow" aria-hidden="true">&rarr;</span></a>'.format(
        _attr(_internal_href(site, spec["href"], where)), _t(_text(spec, where, "text")))
    return '<div class="next"><p class="lbl">Next</p>{}{}</div>'.format(line, arrow)


def _c_lead(site, doc, where):
    spec = _need(doc, where, ("component", "html"))
    return '<p class="lead">{}</p>'.format(_inline(site, _text(spec, where, "html")))


def _c_label(site, doc, where):
    spec = _need(doc, where, ("component", "text"), ("variant", "id"))
    variant = _text(spec, where, "variant", "blk")
    if variant not in ("blk", "sec", "accent blk"):
        raise RefusedPage("{}: variant must be blk, sec or accent blk".format(where))
    attr = ""
    if "id" in spec:
        attr = ' id="{}"'.format(_checked_id(spec, where))
    return '<p class="lbl {}"{}>{}</p>'.format(variant, attr, _t(_text(spec, where, "text")))


def _c_section(site, doc, where):
    spec = _need(doc, where, ("component", "lbl", "heading"), ("id", "rail"))
    attr = ""
    if "id" in spec:
        attr = ' id="{}"'.format(_checked_id(spec, where))
    return '<p class="lbl sec">{}</p>\n<h2{}>{}</h2>'.format(
        _t(_text(spec, where, "lbl")), attr, _inline(site, _text(spec, where, "heading")))


def _c_entry(site, doc, where):
    spec = _need(doc, where, ("component", "lbl", "id", "heading", "claim", "limitation", "evidence"),
                 ("rail",))
    return ('<p class="lbl sec">{}</p>\n<h2 id="{}">{}</h2>\n'
            '<p><strong>Claim:</strong> {}</p>\n<p><strong>Limitation:</strong> {}</p>\n'
            '<p><strong>Evidence:</strong> {}</p>').format(
                _t(_text(spec, where, "lbl")), _checked_id(spec, where), _inline(site, _text(spec, where, "heading")),
                _inline(site, _text(spec, where, "claim")), _inline(site, _text(spec, where, "limitation")),
                _inline(site, _text(spec, where, "evidence")))


def _c_prose(site, doc, where):
    spec = _need(doc, where, ("component", "html"))
    return _inline(site, _text(spec, where, "html"))


def _c_pullquote(site, doc, where):
    """A pull quote: the section's one-line thesis set apart in the document's blockquote voice."""
    spec = _need(doc, where, ("component", "html"))
    return "<blockquote><p>{}</p></blockquote>".format(_inline(site, _text(spec, where, "html")))


def _c_record(site, doc, where):
    """A record list (specification section 7.18, `dl.record`): each term and its value in 1 group, with
    the `cols` variant for a grid of short values."""
    spec = _need(doc, where, ("component", "items"), ("cols",))
    classes = "record cols" if _flag(spec, where, "cols") else "record"
    groups = "".join("<div><dt>{}</dt><dd>{}</dd></div>".format(_t(item["term"]), _inline(site, item["html"]))
                     for item in _items(spec, where, "items", ("term", "html")))
    return '<dl class="{}">{}</dl>'.format(classes, groups)


def _c_subhead(site, doc, where):
    """An h3 inside a section; its optional id is checked for shape here and for duplicates per page."""
    spec = _need(doc, where, ("component", "heading"), ("id",))
    attr = ""
    if "id" in spec:
        attr = ' id="{}"'.format(_checked_id(spec, where))
    return "<h3{}>{}</h3>".format(attr, _inline(site, _text(spec, where, "heading")))


def _c_steps(site, doc, where):
    """Plain numbered steps outside the install selector (`.steps`, the selector's own step markup)."""
    spec = _need(doc, where, ("component", "items"))
    items = spec.get("items")
    if not isinstance(items, list) or not items or not all(isinstance(item, str) and item for item in items):
        raise RefusedPage("{}: items must be a non-empty array of non-empty strings".format(where))
    return '<ol class="steps">{}</ol>'.format(
        "".join("<li><span>{}</span></li>".format(_inline(site, item)) for item in items))


def _c_list(site, doc, where):
    """A plain index list (specification section 7.13, `.plan` with its column variant) whose items are
    text, not links."""
    spec = _need(doc, where, ("component", "items"), ("variant",))
    variant = _text(spec, where, "variant", "")
    if variant not in PLAN_VARIANTS:
        raise RefusedPage("{}: variant must be one of one, three".format(where))
    items = spec.get("items")
    if not isinstance(items, list) or not items or not all(isinstance(item, str) and item for item in items):
        raise RefusedPage("{}: items must be a non-empty array of non-empty strings".format(where))
    return '<ul class="{}">{}</ul>'.format("plan" + ((" " + variant) if variant else ""),
                                          "".join("<li>{}</li>".format(_inline(site, item)) for item in items))


# The figure's diagram geometry (specification section 7.18: an inline SVG `.wide`, a `.stack` variant shown
# at 600 px and below, and a figcaption). Every node is a box with a tracked label (`t`), a monospace name
# (`m`) and a serif line (`s`); an edge label sits on each arrow, and the `under` line runs beneath.
FIG_NODE_W, FIG_NODE_H, FIG_GAP = 210, 88, 100
FIG_STACK_W, FIG_STACK_GAP, FIG_STACK_CHARS = 320, 52, 38


def _fig_node(x, y, width, node):
    esc = _html.escape
    return ('<rect class="node" x="{x}" y="{y}" width="{w}" height="{h}" rx="6"></rect>'
            '<text class="t" x="{tx}" y="{y1}">{t}</text><text class="m" x="{tx}" y="{y2}">{m}</text>'
            '<text class="s" x="{tx}" y="{y3}">{s}</text>').format(
                x=x, y=y, w=width, h=FIG_NODE_H, tx=x + 14, y1=y + 26, y2=y + 50, y3=y + 70,
                t=esc(node["t"]), m=esc(node["m"]), s=esc(node["s"]))


def _fig_lines(text, limit):
    """The words of `text` greedily packed into lines of at most `limit` characters; a word longer than
    `limit` is kept whole on a line of its own, so that line is longer than `limit`."""
    lines = []
    for word in text.split():
        if lines and len(lines[-1]) + 1 + len(word) <= limit:
            lines[-1] += " " + word
        else:
            lines.append(word)
    return lines


def _c_figure(site, doc, where):
    spec = _need(doc, where, ("component", "id", "caption", "under", "items"))
    fig_id = _checked_id(spec, where)
    nodes = _items(spec, where, "items", ("t", "m", "s"), ("edge",))
    if not 2 <= len(nodes) <= 4:
        raise RefusedPage("{}: a figure has 2 to 4 nodes".format(where))
    for position, node in enumerate(nodes, 1):
        if ("edge" in node) != (position < len(nodes)):
            raise RefusedPage("{}: every node but the last carries an edge label, the last none".format(where))
    esc = _html.escape
    under = _text(spec, where, "under")
    words = []
    for node in nodes:
        words.append("{}, {}, {}".format(node["t"], node["m"], node["s"]))
        if "edge" in node:
            words.append(node["edge"])
    label = esc("; ".join(words) + ". " + under[:1].upper() + under[1:] + ".")
    width = len(nodes) * FIG_NODE_W + (len(nodes) - 1) * FIG_GAP + 20
    mid = 20 + FIG_NODE_H // 2
    wide = []
    for position, node in enumerate(nodes):
        x = 10 + position * (FIG_NODE_W + FIG_GAP)
        wide.append(_fig_node(x, 20, FIG_NODE_W, node))
        if "edge" in node:
            start, end = x + FIG_NODE_W, x + FIG_NODE_W + FIG_GAP
            wide.append('<line class="arrow" x1="{}" y1="{}" x2="{}" y2="{}"></line>'
                        '<polygon class="head" points="{},{} {},{} {},{}"></polygon>'
                        '<text class="t accent" x="{}" y="{}" text-anchor="middle">{}</text>'.format(
                            start + 4, mid, end - 10, mid, end - 10, mid - 6, end, mid, end - 10, mid + 6,
                            start + FIG_GAP // 2, mid - 10, esc(node["edge"])))
    wide.append('<text class="s" x="{}" y="140" text-anchor="middle">{}</text>'.format(width // 2, esc(under)))
    stack = []
    centre = FIG_STACK_W // 2
    y = 10
    for node in nodes:
        stack.append(_fig_node(20, y, FIG_STACK_W - 40, node))
        y += FIG_NODE_H
        if "edge" in node:
            stack.append('<line class="arrow" x1="{0}" y1="{1}" x2="{0}" y2="{2}"></line>'
                         '<polygon class="head" points="{3},{2} {0},{4} {5},{2}"></polygon>'
                         '<text class="t accent" x="{6}" y="{7}">{8}</text>'.format(
                             centre, y + 4, y + FIG_STACK_GAP - 10, centre - 6, y + FIG_STACK_GAP, centre + 6,
                             centre + 16, y + FIG_STACK_GAP // 2 + 4, esc(node["edge"])))
            y += FIG_STACK_GAP
    for line in _fig_lines(under, FIG_STACK_CHARS):
        y += 22
        stack.append('<text class="s" x="{}" y="{}" text-anchor="middle">{}</text>'.format(centre, y, esc(line)))
    return ('<figure class="figure" id="{id}">'
            '<svg class="wide" viewBox="0 0 {ww} 160" role="img" aria-label="{label}">{wide}</svg>'
            '<svg class="stack" viewBox="0 0 {sw} {sh}" role="img" aria-label="{label}">{stack}</svg>'
            '<figcaption>{cap}</figcaption></figure>').format(
                id=fig_id, ww=width, label=label, wide="".join(wide), sw=FIG_STACK_W, sh=y + 16,
                stack="".join(stack), cap=_inline(site, _text(spec, where, "caption")))


def _c_srcview(site, doc, where):
    """The source beside its generated view (specification section 7.18, `.srcview`): the source in a copy
    block under its file label, the view under its own, with the generated-by line, its heading and its
    rows; an optional code note follows."""
    spec = _need(doc, where, ("component", "id", "source_label", "text", "view_label", "gen", "heading",
                              "items"), ("note",))
    block_id = _checked_id(spec, where)
    rows = "".join("<li>{}</li>".format(_inline(site, item["html"]))
                   for item in _items(spec, where, "items", ("html",)))
    out = ('<div class="srcview"><div><p class="lbl">{}</p><pre id="{}" class="hascopy"><button class="copy" '
           'type="button" data-copy="{}" hidden>Copy</button>{}</pre></div><div><p class="lbl">{}</p>'
           '<div class="view"><p class="gen">{}</p><h4>{}</h4><ul>{}</ul></div></div></div>').format(
               _html.escape(_text(spec, where, "source_label"), quote=False), block_id, block_id,
               _html.escape(_text(spec, where, "text"), quote=False),
               _html.escape(_text(spec, where, "view_label"), quote=False), _inline(site, _text(spec, where, "gen")),
               _inline(site, _text(spec, where, "heading")), rows)
    if "note" in spec:
        out += '\n<p class="code-note">{}</p>'.format(_inline(site, _text(spec, where, "note")))
    return out


def _checked_id(spec, where):
    value = spec.get("id")
    if not isinstance(value, str) or ID_RE.fullmatch(value) is None:
        raise RefusedPage("{}: {!r} is not a valid id".format(where, value))
    return value


COMPONENTS = {
    "hero": _c_hero, "entries": _c_entries, "region": _c_region, "value": _c_value, "about": _c_about,
    "clause": _c_clause, "clauses": _c_clauses, "note": _c_note,
    "callout": _c_callout, "figures": _c_figures, "table": _c_table, "register": _c_register,
    "code": _c_code, "picker": _c_picker, "family": _c_family, "download": _c_download, "plan": _c_plan,
    "scope": _c_scope, "next": _c_next, "lead": _c_lead, "label": _c_label, "section": _c_section,
    "entry": _c_entry, "prose": _c_prose, "list": _c_list, "figure": _c_figure,
    "srcview": _c_srcview, "pullquote": _c_pullquote, "record": _c_record, "subhead": _c_subhead,
    "steps": _c_steps,
}
# Components whose rail key lists the section on the page's rail (specification section 7.6).
RAIL_BEARING = ("clause", "section", "entry")


# ---------------------------------------------------------------------------------------------------
# Page data files.

def _page_from_doc(doc, where):
    """A validated page model from one parsed data file."""
    if not isinstance(doc, dict):
        raise RefusedPage("{}: the page must be a TOML table".format(where))
    token_at = _aiqt_token_where(doc, where)
    if token_at is not None:
        raise RefusedPage("{}: the {} token is AIQT-only (it counts the AIQT hooks manifest); an OPF page "
                          "cannot carry it".format(token_at, AIQT_ONLY_TOKEN))
    spec = _need(doc, where, ("path", "title", "kind", "body"), ("label", "heading", "meta", "description"))
    rel = _text(spec, where, "path")
    if REL_RE.fullmatch(rel) is None:
        raise RefusedPage("{}: {!r} is not a safe page path".format(where, rel))
    title = _text(spec, where, "title")
    meta = _text(spec, where, "meta") if "meta" in spec else None
    # A head-only description, for a page that shows no meta line (the home and 404 pages).
    description = _text(spec, where, "description") if "description" in spec else None
    if meta is not None and description is not None:
        raise RefusedPage("{}: set meta or description, not both".format(where))
    kind = _text(spec, where, "kind")
    if kind not in KINDS:
        raise RefusedPage("{}: kind must be one of {}".format(where, ", ".join(KINDS)))
    if kind == "document":
        _text(spec, where, "label")
        _text(spec, where, "heading")
    elif "label" in spec or "heading" in spec or "meta" in spec:
        raise RefusedPage("{}: label, heading and meta belong to a document page".format(where))
    body = spec.get("body")
    if not isinstance(body, list) or not body or not all(isinstance(item, dict) for item in body):
        raise RefusedPage("{}: body must be a non-empty array of component tables".format(where))
    rail = []
    ids = {}
    for position, component in enumerate(body, 1):
        comp_where = "{}.body[{}]".format(where, position)
        name = component.get("component")
        # A malformed name (an array or a table) is refused here, before it is looked up by value.
        if not isinstance(name, str):
            raise RefusedPage("{}: component must be a string, not {}".format(comp_where, type(name).__name__))
        if name in AIQT_ONLY_COMPONENTS:
            raise RefusedPage("{}: {!r} is an AIQT-only component (it reads AIQT inputs); an OPF page "
                              "cannot carry it".format(comp_where, name))
        if name not in COMPONENTS:
            raise RefusedPage("{}: unknown component {!r}".format(comp_where, name))
        # An id a component carries is a fragment target, so 2 components on 1 page may not share it.
        if isinstance(component.get("id"), str):
            if component["id"] in ids:
                raise RefusedPage("{}: duplicate id {!r} (also in {})".format(
                    comp_where, component["id"], ids[component["id"]]))
            ids[component["id"]] = comp_where
        if "rail" in component:
            if name not in RAIL_BEARING:
                raise RefusedPage("{}: only a clause, section or entry joins the rail".format(comp_where))
            rail.append((_checked_id(component, comp_where), _text(component, comp_where, "rail")))
        if name == "about" and kind == "document" and position != len(body):
            raise RefusedPage("{}: the About region closes a document page".format(comp_where))
    return {"rel": rel, "title": title, "kind": kind, "label": spec.get("label"),
            "heading": spec.get("heading"), "meta": meta, "description": description, "body": body, "rail": rail,
            "where": where}


def _load_pages(src, label=None):
    """Every page model from the data directory, sorted by file name; RefusedPage on anything that is
    not a plain readable TOML page file, on a directory that cannot be listed, and on an empty set.
    `label` names the directory in diagnostics (the site's repository-relative source directory)."""
    label = str(src) if label is None else label
    names = []
    try:
        with os.scandir(src) as entries:
            listing = sorted(entries, key=lambda entry: entry.name)
            for entry in listing:
                mode = entry.stat(follow_symlinks=False).st_mode
                if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
                    raise RefusedPage("{}/{}: not a regular file".format(src, entry.name))
                if not entry.name.endswith(".toml"):
                    raise RefusedPage("{}/{}: not a .toml page data file".format(src, entry.name))
                names.append(entry.name)
    except OSError as exc:
        raise RefusedPage("{}: cannot list the page data directory ({})".format(src, exc)) from exc
    if not names:
        raise RefusedPage("{}: no page data files".format(src))
    pages, seen = [], {}
    for name in names:
        where = "{}/{}".format(label, name)
        try:
            with open(Path(src) / name, "rb") as handle:
                data = handle.read(MAX_PAGE_BYTES + 1)
        except OSError as exc:
            raise RefusedPage("{}: unreadable ({})".format(where, exc)) from exc
        if len(data) > MAX_PAGE_BYTES:
            raise RefusedPage("{}: larger than {} bytes".format(where, MAX_PAGE_BYTES))
        try:
            doc = tomllib.loads(data.decode("utf-8"))
        except (RecursionError, MemoryError, ValueError, UnicodeError) as exc:
            # The parse is guarded at its site: tomllib raises the ValueError family on malformed TOML
            # and RecursionError or MemoryError on hostile input; each becomes a refusal here.
            raise RefusedPage("{}: cannot parse ({}: {})".format(where, type(exc).__name__, exc)) from exc
        page = _page_from_doc(doc, where)
        if page["rel"] in seen:
            raise RefusedPage("{}: duplicate page path {!r} (also in {})".format(
                where, page["rel"], seen[page["rel"]]))
        seen[page["rel"]] = where
        pages.append(page)
    return pages


def _page_scripts(page):
    """The page's derived script set, in head order: copy.js with a copy block, select.js with the
    selector, rail.js only where the rail lists in-page sections (specification sections 7.6 and 8)."""
    names = {component["component"] for component in page["body"]}
    scripts = []
    if "code" in names or "srcview" in names:
        scripts.append("copy.js")
    if "picker" in names:
        scripts.append("select.js")
    if page["rail"]:
        scripts.append("rail.js")
    return scripts


def _rail_html(site, page):
    logo = '<img class="rail-logo" src="{}" alt="{}">'.format(
        _asset(site, "logos/{}.svg".format(site["key"])), site["logo-alt"])
    if not page["rail"]:
        return '<div class="rail">{}</div>'.format(logo)
    links = "".join('<li><a href="#{}">{}</a></li>'.format(anchor, _t(label)) for anchor, label in page["rail"])
    return ('<nav class="rail" aria-label="On this page">{}<p class="lbl">On this page</p>'
            '<ol>{}</ol></nav>').format(logo, links)


def _body_html(site, page):
    where = page["where"]
    rendered = []
    for position, component in enumerate(page["body"], 1):
        comp_where = "{}.body[{}]".format(where, position)
        rendered.append((component["component"], COMPONENTS[component["component"]](site, component, comp_where)))
    if page["kind"] == "landing":
        return '<div class="wrap">{}</div>'.format("\n".join(html for _name, html in rendered))
    parts = ['<p class="lbl first">{}</p>'.format(_t(page["label"])),
             "<h1>{}</h1>".format(_inline(site, page["heading"])), _rail_html(site, page)]
    if page["meta"]:
        parts.append('<p class="meta">{}</p>'.format(_inline(site, page["meta"])))
    tail = []
    for name, html in rendered:
        if name == "about":
            tail.append(html)
        else:
            parts.append(html)
    doc = '<div class="doc">{}\n</div>'.format("\n".join(parts))
    return '<div class="wrap">{}</div>'.format("\n".join([doc] + tail))


# The link guard is a structural pass (html.parser) over each rendered page, so it sees every element and
# attribute whatever its case or quoting. It enforces: no element that loads, embeds, submits or redirects
# outside the link rules (LINK_REFUSED_TAGS, and a meta carrying http-equiv), except each script element
# the shell itself writes (exactly src plus defer, with a src the shell chose, and once the scheme script:
# exactly data-cfasync="false" then src, with the scheme src the shell chose, wherever on the page it
# appears; the guard does not check that it sits in the head, which render_page's order provides and the
# shell self-test asserts); every URL attribute
# (LINK_URL_ATTRS) is an https URL, a #fragment, a path under the site's base or one of its kept live
# paths, with no backslash, whitespace or control character, no protocol-relative "//" start and no "." or
# ".." segment, raw or percent-encoded (%2e, either case); and every a or area whose href is https carries
# target="_blank", rel with noopener and noreferrer, and the "(opens in a new tab)" hint in its text or
# aria-label. Residual: the guard judges the markup as Python's html.parser tokenizes it, so a malformed
# construct a browser repairs differently is outside it; it reads no stylesheet (inline styles are refused
# separately) and treats a URL-shaped value in any attribute not listed here as text; and it does not
# check that an allowed target exists.
LINK_REFUSED_TAGS = frozenset((
    "script", "iframe", "frame", "frameset", "base", "form", "object", "embed", "applet", "portal",
    "animate", "set", "animatemotion", "animatetransform", "handler", "foreignobject"))
LINK_URL_ATTRS = frozenset((
    "href", "src", "action", "formaction", "data", "poster", "cite", "background", "srcset", "imagesrcset",
    "longdesc", "manifest", "ping", "xlink:href", "codebase", "archive", "usemap", "icon", "profile",
    "lowsrc", "dynsrc"))
NEWTAB_HINT = "(opens in a new tab)"
_URL_BAD_CHAR_RE = re.compile(r"[\x00-\x20\x7f\\]")


class _LinkGuard(HTMLParser):
    def __init__(self, site, scripts, scheme_src=None):
        super().__init__(convert_charrefs=True)
        self.site = site
        self.scripts = list(scripts)
        self.scheme_src = scheme_src
        self.found = []
        self._anchor = None

    def _url_finding(self, name, tag, value):
        site, base = self.site, self.site["base"]
        if value is None:
            return "{} attribute on <{}> has no value".format(name, tag)
        if name in ("srcset", "imagesrcset", "ping", "archive"):
            return "{} attribute on <{}> (a URL list) is not allowed".format(name, tag)
        # A browser reads %2e as "." in a path segment, so each segment is tested after one decoding.
        segments = value.split("#", 1)[0].split("?", 1)[0].split("/")
        if _URL_BAD_CHAR_RE.search(value) or value.startswith("//") \
                or any(unquote(segment) in (".", "..") for segment in segments):
            return "{} {!r} on <{}> is not a plain URL".format(name, value, tag)
        if value.startswith("https://") or value.startswith("#") or value.startswith(base) \
                or _kept_live(site, value):
            return None
        return "{} {!r} on <{}> is neither under {} nor an https URL".format(name, value, tag, base)

    def handle_starttag(self, tag, attrs):
        first = {}
        for name, value in attrs:
            first.setdefault(name, value)
        if tag == "script" and len(attrs) == 2 and attrs[0][0] == "src" and attrs[1] == ("defer", None) \
                and attrs[0][1] in self.scripts:
            self.scripts.remove(attrs[0][1])
        elif tag == "script" and self.scheme_src is not None \
                and attrs == [("data-cfasync", "false"), ("src", self.scheme_src)]:
            self.scheme_src = None
        elif tag in LINK_REFUSED_TAGS:
            self.found.append("<{}> element".format(tag))
        if tag == "meta" and "http-equiv" in first:
            self.found.append("<meta http-equiv> element")
        for name, value in attrs:
            if name in LINK_URL_ATTRS:
                problem = self._url_finding(name, tag, value)
                if problem is not None:
                    self.found.append(problem)
        if tag in ("a", "area") and isinstance(first.get("href"), str) and first["href"].startswith("https://"):
            rel = (first.get("rel") or "").split()
            if first.get("target") != "_blank" or "noopener" not in rel or "noreferrer" not in rel:
                self.found.append("external link {!r} without the new-tab attributes".format(first["href"]))
            # The hint is in the aria-label, or (an a element only) in the text the anchor closes over.
            if NEWTAB_HINT in (first.get("aria-label") or ""):
                pass
            elif tag == "a":
                self._anchor = [first["href"], []]
            else:
                self.found.append("external link {!r} without the new-tab hint".format(first["href"]))

    def handle_data(self, data):
        if self._anchor is not None:
            self._anchor[1].append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self._anchor is not None:
            if NEWTAB_HINT not in "".join(self._anchor[1]):
                self.found.append("external link {!r} without the new-tab hint".format(self._anchor[0]))
            self._anchor = None


def _link_findings(site, html, scripts=(), scheme_src=None):
    """Every link-rule finding on a built page (see the link guard comment above). `scripts` is the list of
    script src values the shell wrote; each allows 1 script element with exactly that src and defer.
    `scheme_src` is the head's scheme script src; it allows 1 script element with exactly
    data-cfasync="false" then that src, and nothing else."""
    guard = _LinkGuard(site, scripts, scheme_src)
    guard.feed(html)
    guard.close()
    if guard._anchor is not None:
        guard.found.append("external link {!r} is never closed".format(guard._anchor[0]))
    return guard.found


class _PlainText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


def _description(site, page):
    """The page's description, or else its meta, as plain text (its inline markup reduced to the text,
    character references decoded, whitespace runs folded to 1 space), or "" for a page with neither."""
    source = page.get("description") or page["meta"]
    if not source:
        return ""
    reader = _PlainText()
    reader.feed(_inline(site, source))
    reader.close()
    return " ".join("".join(reader.parts).split())


def render_page(site, pages, page):
    rel, title = page["rel"], page["title"]
    head = ['<meta charset="utf-8">', '<meta name="viewport" content="width=device-width, initial-scale=1">']
    if site["base"] == DRAFT_BASE or (site["base"] == "/" and rel.startswith(DRAFT_DIR)):
        head.append('<meta name="robots" content="noindex, nofollow">')
    # The page's meta text, as plain text, is also its search and link-preview description. Specification
    # section 5 does not place these metas; they join the other metas before the title, where every
    # reference page keeps its robots meta, so the elements section 5 lists keep their order.
    description = _description(site, page)
    if description:
        head.append('<meta name="description" content="{}">'.format(_attr(description)))
        head.append('<meta property="og:description" content="{}">'.format(_attr(description)))
    # The link-preview card metas follow the description metas, on every page.
    head.extend(_social_metas(site, page))
    head.append("<title>{} | {}.ai</title>".format(_t(title), site["name"]))
    head.append('<link rel="icon" href="{}" type="image/svg+xml">'.format(
        _asset(site, "logos/{}-favicon.svg".format(site["key"]))))
    head.append('<link rel="apple-touch-icon" href="{}">'.format(
        _asset(site, "logos/{}-touch.png".format(site["key"]))))
    scheme_src = _asset(site, SCHEME_SCRIPT)
    head.append('<script data-cfasync="false" src="{}"></script>'.format(scheme_src))
    for sheet in ("fonts.css", "palettes.css", "baseline.css"):
        head.append('<link rel="stylesheet" href="{}">'.format(_asset(site, sheet)))
    scripts = [_asset(site, script) for script in _page_scripts(page)]
    for src in scripts:
        head.append('<script src="{}" defer></script>'.format(src))
    html = "\n".join([
        "<!doctype html>",
        '<html lang="en" class="site-{}">'.format(site["key"]),
        "<head>"] + head + [
        "</head>",
        "<body>",
        '<a class="skip" href="#main-content">Skip to content</a>',
        '<header class="mast">{}{}{}</header>'.format(
            _brand(site), _nav(site, pages, _page_url(site, rel)), SCHEME_BUTTON),
        '<main id="main-content">',
        _body_html(site, page),
        "</main>",
        _footer(site),
        '<p class="vh" id="copy-status" role="status" aria-live="polite"></p>',
        "</body>",
        "</html>",
    ]) + "\n"
    refusals = refusals_for(html) + _link_findings(site, html, scripts, scheme_src)
    if refusals:
        raise RefusedPage("{}/{}: {}".format(site["root"], rel, "; ".join(refusals)))
    return html


DASHES = ("–", "—")
FRAMING_RE = re.compile(r"what\s+(this|it)\s+is\b", re.IGNORECASE)


class _Refusals(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.found = []
        self._script = None
        self.text = []

    def handle_starttag(self, tag, attrs):
        names = [name for name, _value in attrs]
        if tag == "style":
            self.found.append("inline <style> element")
        if tag == "script":
            self._script = dict(attrs)
            if not self._script.get("src"):
                self.found.append("inline <script> element")
        for name in names:
            if name == "style":
                self.found.append("inline style attribute on <{}>".format(tag))
            elif name.startswith("on"):
                self.found.append("{} attribute on <{}>".format(name, tag))

    def handle_endtag(self, tag):
        if tag == "script":
            self._script = None

    def handle_data(self, data):
        if self._script is not None and data.strip():
            self.found.append("inline script content")
        self.text.append(data)


def refusals_for(html):
    """Every template code-style refusal a page triggers (specification sections 5 and 12)."""
    found = []
    if any(dash in html for dash in DASHES):
        found.append("en or em dash")
    parser = _Refusals()
    parser.feed(html)
    parser.close()
    found.extend(parser.found)
    if FRAMING_RE.search(" ".join(parser.text)):
        found.append('"what this is" framing')
    return found


def _existing(path):
    """The target's bytes, or None when absent; OSError for a non-regular or unreadable target."""
    try:
        mode = os.lstat(path).st_mode
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(mode):
        raise OSError("{} is not a regular file (a symbolic link or other entry)".format(path))
    with open(path, "rb") as handle:
        return handle.read()


def _kept_live_missing(site, tree=ROOT):
    """Every kept live path of the site with no target under its live root in `tree`: the path as-is for a
    file target, or the path plus ".html" for an extensionless page, matching how the live site serves it."""
    missing = []
    for path in site["kept"]:
        rel = path.lstrip("/")
        candidates = [Path(tree) / site["live"] / rel]
        if "." not in rel.rsplit("/", 1)[-1]:
            candidates.append(Path(tree) / site["live"] / (rel + ".html"))
        if not any(candidate.is_file() for candidate in candidates):
            missing.append(path)
    return missing


def build(pages=None, site=SITE, tree=ROOT):
    """{target path relative to the OPF root: text} for every page of the site. The pages are loaded from
    the site's data directory under `tree` unless a fixture `pages` list is given; with the default page
    set the built set must equal OPF_PAGES. Every kept live path must exist under the site's live root in
    `tree` before anything renders. The site's data directory must resolve inside `tree`: one that leads
    out of it (a symbolic link to a directory elsewhere) is refused, so no page is rendered from outside the
    OPF root."""
    missing = _kept_live_missing(site, tree)
    if missing:
        raise RefusedPage("kept live path(s) with no target under {}/: {}".format(site["live"], ", ".join(missing)))
    declared = pages is None
    if declared:
        root = Path(os.path.realpath(tree))
        src = Path(os.path.realpath(Path(tree) / site["src"]))
        if src != root and root not in src.parents:
            raise RefusedPage("{}: the page data directory resolves to {}, outside the OPF root {}".format(
                site["src"], src, root))
    site_pages = _load_pages(Path(tree) / site["src"], site["src"]) if declared else pages
    outputs = {}
    for page in site_pages:
        target = "{}/{}".format(site["root"], page["rel"])
        if target in outputs:
            raise RefusedPage("{}: built twice".format(target))
        outputs[target] = render_page(dict(site, tree=tree), site_pages, page)
    if declared and set(outputs) != set(OPF_PAGES):
        raise RefusedPage("built pages {} differ from OPF_PAGES {}".format(sorted(outputs), sorted(OPF_PAGES)))
    return outputs


def check(tree=ROOT):
    """Compare every rendered page with the committed page under `tree`: 0 when all match, 1 on drift, 2
    when a page is refused or a committed page cannot be read (a missing page is drift)."""
    try:
        outputs = build(tree=tree)
    except RefusedPage as exc:
        print("error: refused: {}".format(exc), file=sys.stderr)
        return 2
    drift = False
    for rel in OPF_PAGES:
        try:
            current = _existing(Path(tree) / rel)
        except OSError as exc:
            print("error: {}: {}".format(rel, exc), file=sys.stderr)
            return 2
        if current != outputs[rel].encode("utf-8"):
            print("drift: {} differs from its OPF render".format(rel))
            drift = True
    if drift:
        print("the parent's tools/gen_draft_site.py owns these pages; a render that differs here is a defect in "
              "opf/tools/_site_render.py or a stale page")
    return 1 if drift else 0


# ---------------------------------------------------------------------------------------------------
# Self-test.

# The standard-library modules this file may import; any other import (a parent repository module such as
# gen_mappings, gen_rules, gen_agents or _standards included) is a self-test failure.
_STDLIB_IMPORTS = frozenset((
    "ast", "html", "html.parser", "os", "pathlib", "re", "shutil", "signal", "stat", "subprocess", "sys",
    "tempfile", "tomllib", "urllib.parse"))
_CHILD_TIMEOUT_SECONDS = 300


def _import_findings(source):
    """Every import in `source` outside _STDLIB_IMPORTS (a relative import included), and every use of
    sys.path, which would let a later import reach outside the standard library."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found.extend("import {}".format(alias.name) for alias in node.names if alias.name not in _STDLIB_IMPORTS)
        elif isinstance(node, ast.ImportFrom):
            if node.level or node.module not in _STDLIB_IMPORTS:
                found.append("from {}{} import".format("." * node.level, node.module or ""))
        elif isinstance(node, ast.Attribute) and node.attr == "path" and isinstance(node.value, ast.Name) \
                and node.value.id == "sys":
            found.append("sys.path use")
    return found


def _opf_only_copy(dest, tree=ROOT, script=None):
    """Copy into dest only what this renderer reads: this file, tools/opf.py, the page data, the committed
    pages and the kept live paths. Nothing of the parent repository is copied."""
    script = Path(__file__) if script is None else Path(script)
    os.makedirs(Path(dest) / "tools")
    shutil.copyfile(script, Path(dest) / "tools" / "_site_render.py")
    shutil.copyfile(Path(tree) / STATUS_SOURCE, Path(dest) / STATUS_SOURCE)
    shutil.copytree(Path(tree) / SITE["src"], Path(dest) / SITE["src"])
    for rel in OPF_PAGES + tuple("{}/{}".format(SITE["live"], path.lstrip("/")) for path in SITE["kept"]):
        os.makedirs((Path(dest) / rel).parent, exist_ok=True)
        shutil.copyfile(Path(tree) / rel, Path(dest) / rel)


def _run_child(script, cwd):
    """Run `script --check` with -I -B as a child in its own session and process group from `cwd`; the
    group is killed when the child ends or times out. Returns (exit code or None on timeout, output)."""
    with subprocess.Popen([sys.executable, "-I", "-B", str(script), "--check"], cwd=cwd,
                          stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          start_new_session=True) as proc:
        try:
            out, _err = proc.communicate(timeout=_CHILD_TIMEOUT_SECONDS)
            code = proc.returncode
        except subprocess.TimeoutExpired:
            out, code = b"", None
        finally:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            if proc.poll() is None:
                proc.wait()
    return code, out.decode("utf-8", "replace")


def _self_test():
    failures = []
    count = [0]

    def expect(name, ok, detail=""):
        count[0] += 1
        if not ok:
            failures.append("{}: {!r}".format(name, detail))

    def refused(name, needle, callable_, *args):
        count[0] += 1
        try:
            callable_(*args)
        except RefusedPage as exc:
            if needle not in str(exc):
                failures.append("{}: refused without {!r}: {}".format(name, needle, exc))
            return
        except Exception as exc:  # a crash where a refusal is due is a failure of this check, not of the run
            failures.append("{}: raised {} instead of a refusal: {}".format(name, type(exc).__name__, exc))
            return
        failures.append("{}: not refused".format(name))

    # The 9 pages render byte for byte as the committed pages the parent generator writes and holds.
    try:
        outputs = build()
    except RefusedPage as exc:
        outputs = {}
        failures.append("pages/build: refused: {}".format(exc))
    expect("pages/nine-declared", len(OPF_PAGES) == 9 and len(set(OPF_PAGES)) == 9, OPF_PAGES)
    expect("pages/built-set", sorted(outputs) == sorted(OPF_PAGES), sorted(outputs))
    for rel in OPF_PAGES:
        try:
            committed = _existing(ROOT / rel)
        except OSError as exc:
            committed = exc
        expect("pages/identical " + rel, rel in outputs and committed == outputs[rel].encode("utf-8"), rel)
    expect("pages/check-clean", check() == 0)

    # The AIQT-only components and token are refused by name, and a page without them renders.
    page = {"path": "index.html", "title": "Fixture", "kind": "landing",
            "body": [{"component": "region", "label": "L", "html": "<p>$BASE/ fixture</p>"}]}
    try:
        model = _page_from_doc(page, "fixture.toml")
        html = render_page(SITE, [model], model)
    except RefusedPage as exc:
        html = str(exc)
    expect("aiqt/plain-page-renders", "<p>/ fixture</p>" in html, html)
    for name in AIQT_ONLY_COMPONENTS:
        expect("aiqt/component-absent " + name, name not in COMPONENTS, sorted(COMPONENTS))
    refused("aiqt/control-refused", "AIQT-only component", _page_from_doc,
            dict(page, body=[{"component": "control", "document": "D", "source_text": "S",
                              "source_href": "https://example.com/"}]), "fixture.toml")
    refused("aiqt/mappings-refused", "AIQT-only component", _page_from_doc,
            dict(page, body=[{"component": "mappings", "view": "by-rule"}]), "fixture.toml")
    refused("hook-count/inline-html-refused", "AIQT-only", _page_from_doc,
            dict(page, body=[{"component": "region", "label": "L", "html": "<p>$HOOK_COUNT hooks</p>"}]),
            "fixture.toml")
    refused("hook-count/escaped-field-refused", "AIQT-only", _page_from_doc,
            dict(page, title="$HOOK_COUNT"), "fixture.toml")
    refused("hook-count/inline-render-refused", "AIQT-only", _inline, SITE, "$HOOK_COUNT")
    with tempfile.TemporaryDirectory() as tmp:
        with open(Path(tmp) / "page.toml", "w", encoding="utf-8") as handle:
            handle.write('path = "index.html"\ntitle = "Fixture"\nkind = "landing"\n[[body]]\n'
                         'component = "region"\nlabel = "L"\nhtml = "<p>\\u0024HOOK_COUNT</p>"\n')
        refused("hook-count/toml-escape-refused", "AIQT-only", _load_pages, tmp)
    # The token in a table key is refused as AIQT-only by the page walk itself, before the key checks
    # would refuse it as an unknown key.
    refused("hook-count/key-refused", "AIQT-only", _page_from_doc, dict(page, **{"x$HOOK_COUNT": "x"}),
            "fixture.toml")
    try:
        note, rendered = status_note(verb_status(ROOT)), _inline(SITE, "$OPF_STATUS")
    except RefusedPage as exc:
        note, rendered = None, str(exc)
    expect("status/opf-status-rendered", note is not None and rendered == note, rendered)

    # A component name that is not a string (an array, or an array of names) is refused as malformed
    # input, never looked up by value.
    for label, value in (("empty-array", []), ("array-of-names", ["region"]), ("table", {"name": "region"})):
        refused("page/component-not-string-refused " + label, "component must be a string", _page_from_doc,
                dict(page, body=[{"component": value, "label": "L", "html": "<p>x</p>"}]), "fixture.toml")

    # Page data files: a file larger than MAX_PAGE_BYTES is refused and one of exactly MAX_PAGE_BYTES loads;
    # a symbolic link to a page and a directory named like a page are refused as not regular files.
    page_text = ('path = "index.html"\ntitle = "Fixture"\nkind = "landing"\n[[body]]\n'
                 'component = "region"\nlabel = "L"\nhtml = "<p>x</p>"\n#')
    with tempfile.TemporaryDirectory() as tmp:
        for name, size in (("at-limit", MAX_PAGE_BYTES), ("over-limit", MAX_PAGE_BYTES + 1)):
            os.makedirs(Path(tmp) / name)
            with open(Path(tmp) / name / "page.toml", "w", encoding="utf-8") as handle:
                handle.write(page_text + "x" * (size - len(page_text) - 1) + "\n")
        try:
            loaded = [page["rel"] for page in _load_pages(Path(tmp) / "at-limit")]
        except RefusedPage as exc:
            loaded = str(exc)
        expect("load/page-at-size-limit-loads", loaded == ["index.html"], loaded)
        refused("load/page-over-size-limit-refused", "larger than", _load_pages, Path(tmp) / "over-limit")
        os.makedirs(Path(tmp) / "linked")
        os.symlink(Path(tmp) / "at-limit" / "page.toml", Path(tmp) / "linked" / "page.toml")
        refused("load/page-symlink-refused", "not a regular file", _load_pages, Path(tmp) / "linked")
        os.makedirs(Path(tmp) / "nested" / "page.toml")
        refused("load/page-directory-refused", "not a regular file", _load_pages, Path(tmp) / "nested")

    # tools/opf.py: a file of exactly MAX_STATUS_SOURCE_BYTES is read and a larger one refused, both before
    # parsing; a symbolic link is refused as not a regular file.
    status_text = 'VERB_STATUS = (("fixture_verb", "available", ""),)\n#'
    with tempfile.TemporaryDirectory() as tmp:
        for name, size in (("at-limit", MAX_STATUS_SOURCE_BYTES), ("over-limit", MAX_STATUS_SOURCE_BYTES + 1)):
            os.makedirs(Path(tmp) / name / "tools")
            with open(Path(tmp) / name / STATUS_SOURCE, "w", encoding="utf-8") as handle:
                handle.write(status_text + "x" * (size - len(status_text) - 1) + "\n")
        try:
            table = verb_status(Path(tmp) / "at-limit")
        except RefusedPage as exc:
            table = str(exc)
        expect("status/source-at-size-limit-read", table == (("fixture_verb", "available", ""),), table)
        refused("status/source-over-size-limit-refused", "larger than", verb_status, Path(tmp) / "over-limit")
        os.makedirs(Path(tmp) / "linked" / "tools")
        os.symlink(Path(tmp) / "at-limit" / STATUS_SOURCE, Path(tmp) / "linked" / STATUS_SOURCE)
        refused("status/source-symlink-refused", "not a regular file", verb_status, Path(tmp) / "linked")

    # $OPF_STATUS reads VERB_STATUS from the tree build() is given, never from the live OPF root: a fixture
    # tree whose table names only fixture_verb renders that table, through _inline and through build().
    with tempfile.TemporaryDirectory() as tmp:
        os.makedirs(Path(tmp) / "tools")
        with open(Path(tmp) / STATUS_SOURCE, "w", encoding="utf-8") as handle:
            handle.write('VERB_STATUS = (("fixture_verb", "available", ""),)\n')
        for path in SITE["kept"]:
            os.makedirs((Path(tmp) / SITE["live"] / path.lstrip("/")).parent, exist_ok=True)
            with open(Path(tmp) / SITE["live"] / path.lstrip("/"), "w", encoding="utf-8") as handle:
                handle.write("kept\n")
        fixture_note = "Reference CLI status. Available: fixture_verb."
        try:
            inline = _inline(dict(SITE, tree=Path(tmp)), "$OPF_STATUS")
            model = _page_from_doc(dict(page, body=[{"component": "region", "label": "L",
                                                     "html": "<p>$OPF_STATUS</p>"}]), "fixture.toml")
            built = build([model], tree=Path(tmp)).get("site/index.html", "")
        except RefusedPage as exc:
            inline = built = str(exc)
        expect("status/explicit-tree-inline", inline == fixture_note, inline)
        expect("status/explicit-tree-build", "<p>{}</p>".format(fixture_note) in built, built[:300])

    # The page data directory must resolve inside the OPF root: a site/draft-src that is a symbolic link to
    # a directory outside it is refused, though the pages there are the committed ones.
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "opf"
        try:
            _opf_only_copy(dest)
            shutil.copytree(dest / SITE["src"], Path(tmp) / "outside-src")
            shutil.rmtree(dest / SITE["src"])
            os.symlink(Path(tmp) / "outside-src", dest / SITE["src"])
        except OSError as exc:
            failures.append("src/outside-root: cannot build the fixture: {}".format(exc))
        refused("src/outside-root-refused", "outside the OPF root", build, None, SITE, dest)

    # Imports: only the standard library, and a copy holding nothing of the parent renders and checks clean
    # from an unrelated working directory; the same copy with 1 page changed is drift (exit 1).
    with open(__file__, encoding="utf-8") as handle:
        source = handle.read()
    expect("imports/stdlib-only", _import_findings(source) == [], _import_findings(source))
    expect("imports/parent-import-found", _import_findings("import gen_mappings\nfrom _standards import x\n"
                                                           "sys.path.insert(0, 'tools')\n") == [
        "import gen_mappings", "from _standards import", "sys.path use"])
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "opf"
        os.makedirs(Path(tmp) / "elsewhere")
        try:
            _opf_only_copy(dest)
            code, out = _run_child(dest / "tools" / "_site_render.py", Path(tmp) / "elsewhere")
        except OSError as exc:
            code, out = None, "cannot build the OPF-only copy: {}".format(exc)
        expect("standalone/check-clean", code == 0, (code, out))
        if code is not None:
            with open(dest / "site" / "model" / "index.html", "ab") as handle:
                handle.write(b"\n")
            code, out = _run_child(dest / "tools" / "_site_render.py", Path(tmp) / "elsewhere")
        expect("standalone/drift-red", code == 1 and "drift: site/model/index.html" in out, (code, out))
        # A FIFO in place of tools/opf.py is refused (exit 2) before it is opened; opening it would block
        # until a writer came, so the check runs in the bounded child, where a hang reads as a timeout.
        if hasattr(os, "mkfifo"):
            try:
                os.unlink(dest / STATUS_SOURCE)
                os.mkfifo(dest / STATUS_SOURCE)
                code, out = _run_child(dest / "tools" / "_site_render.py", Path(tmp) / "elsewhere")
            except OSError as exc:
                code, out = None, "cannot build the FIFO fixture: {}".format(exc)
            expect("standalone/status-source-fifo-refused", code == 2 and "not a regular file" in out, (code, out))

    if failures:
        for failure in failures:
            print("FAIL " + failure, file=sys.stderr)
        print("{} of {} checks failed".format(len(failures), count[0]), file=sys.stderr)
        return 1
    print("ok: {} checks (the 9 OPF pages render as committed, the AIQT-only components and $HOOK_COUNT are "
          "refused, malformed, oversized and non-regular inputs are refused, $OPF_STATUS reads the given tree, "
          "and an OPF-only copy imports, renders and checks without the parent)".format(count[0]))
    return 0


def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]
    if argv == ["--self-test"]:
        return _self_test()
    if argv == ["--check"]:
        return check()
    print("error: unrecognized arguments {!r} (use --check or --self-test); fail closed".format(argv),
          file=sys.stderr)
    return 2


# The child-status guard: an inline copy of the canonical guard in opf/tools/_child_status.py, which
# tools/check_child_status_coverage.py pins this copy to by AST. The self-test reads the exit status of the
# --check child it launches, so under an ignored SIGCHLD it would read a failing child as a pass; this
# module refuses at its entry line instead, before main() runs.
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
    sys.exit(_child_status_refusal_exit("_site_render.py") or main())
