#!/usr/bin/env python3
"""Generate the template sites (shared site template, baseline 1.7.0) at each site's root, plus the /draft/
placeholder page each keeps (noindex, linking the root).

  gen_draft_site.py              write every registered page
  gen_draft_site.py --check      exit 1 if any target differs from a fresh build or a page folder holds an
                                 unlisted page, 2 if one cannot be read or a root-relative link resolves
                                 to nothing the site serves (both checks also run on a normal run)
  gen_draft_site.py --self-test  assert the shell, the components, the refusals and the base-path rules

Every page is built from 1 shell (template specification section 5): `<html lang="en" class="site-<name>">`,
the head (charset, viewport, the robots noindex meta while the site's base is the draft path, the title
`<Page> | <Name>.ai`, the favicon, the touch icon, scheme.js with data-cfasync="false" and no defer or async,
fonts.css, palettes.css, baseline.css, then the page's scripts with defer), the skip link, the top bar (the
site's page links, then GitHub last; section 7.1; then the light and dark switch, hidden until scheme.js
shows it; section 7.33),
`main#main-content`, the family footer (section 7.19) and the `#copy-status` live region. The home page
carries the "About this project" region (section 7.24), the one place that links the Disclosure and Licence
pages. Each site's base (SITES) is the one base path for every internal href and src of that site; it
becomes "/" at that site's promotion, and the robots meta goes with it (a page under draft/ keeps it). Asset
URLs follow the site's separate asset base ("assets" in SITES), which stays "/draft/" after promotion, so
the pinned copy is served where it was staged. A page is refused (exit 2, nothing written) if it carries an
en or em dash, an inline style or script, an `on*` attribute, or "what this is" framing. The baseline
files under assets/baseline/ are a pinned copy, not generated here (tools/check_baseline_pin.py).

Content model: each page is 1 TOML data file under its site's data directory (site/draft-src/ for aiqt.ai,
opf/site/draft-src/ for opfiles.ai), discovered by listing that directory, so a page lands or changes as
data; each site renders only its own pages, with its own chrome, into its own draft root. A data file names the page's output path, title and kind (landing or
document) and a body of components (specification section 7); the generator refuses an unknown component,
an unknown or missing key, a wrong type, a duplicate output path, and a data directory it cannot list
(exit 2, nothing written). The page's scripts are derived from its content: copy.js where a copy block
renders, select.js where the install selector renders, and rail.js where the rail lists in-page sections
(section 7.6); no rail link carries a static aria-current. In an inline HTML value the token `$BASE/`
renders as the base path, so content survives promotion unchanged. The document control strip reads its
Version from the repository's VERSION file and its Status from changelog.toml.

Escaping: every data value is text, escaped for its context (element text, or a double-quoted attribute
value with quotes escaped, external hrefs included), except the inline HTML values, which render raw:
the page heading and meta; hero kicker, statement and intro; entries item title, n and d; plan item
title, n and small; every `html` key (region, value, clauses item, note, callout, download, scope, next,
lead, prose, pullquote, record item, srcview item); clause title; section, family and subhead heading;
family verified, intro, after and steps; entry heading, claim, limitation and evidence; table and register
head cells and row cells; steps and list items; figure caption; srcview gen and heading; code and srcview
note. Code text, code file labels, srcview labels and figure node text are escaped as before. Every
rendered page then passes the link guard (_LinkGuard), which refuses raw markup that breaks the link rules.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: gen_draft_site.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import html as _html
import os
import re
import shutil
import stat
import tempfile
import tomllib
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _standards import load_manifests, ManifestError  # noqa: E402
import gen_mappings  # noqa: E402  (the one mappings rendering source: rows, registry and wording)
import gen_rules  # noqa: E402  (the one frontmatter parser and corpus loader)
from gen_agents import sort_key  # noqa: E402  (canonical AIQT priority order for the rules)

# The staging base path; each site's own base (SITES) is this while staging and "/" at its promotion.
DRAFT_BASE = "/draft/"
ASSETS = "assets/baseline/"
# The draft folder of a promoted site: a page under it (the /draft/ placeholder) keeps the robots noindex
# meta that every page carried while staging.
DRAFT_DIR = "draft/"
MAX_PAGE_BYTES = 1 << 20
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

GENSRC_OUTPUTS = (
    {"target": "site/index.html", "kind": "file",
     "sources": ("tools/gen_draft_site.py", "site/draft-src/home.toml"),
     "regenerate": "python3 tools/gen_draft_site.py"},
    {"target": "site/404.html", "kind": "file",
     "sources": ("tools/gen_draft_site.py", "site/draft-src/404.toml"),
     "regenerate": "python3 tools/gen_draft_site.py"},
    {"target": "site/draft/index.html", "kind": "file",
     "sources": ("tools/gen_draft_site.py", "site/draft-src/draft.toml"),
     "regenerate": "python3 tools/gen_draft_site.py"},
    {"target": "site/disclosure/index.html", "kind": "file",
     "sources": ("tools/gen_draft_site.py", "site/draft-src/disclosure.toml"),
     "regenerate": "python3 tools/gen_draft_site.py"},
    {"target": "site/install/index.html", "kind": "file",
     "sources": ("tools/gen_draft_site.py", "site/draft-src/install.toml"),
     "regenerate": "python3 tools/gen_draft_site.py"},
    {"target": "site/license/index.html", "kind": "file",
     "sources": ("tools/gen_draft_site.py", "site/draft-src/license.toml"),
     "regenerate": "python3 tools/gen_draft_site.py"},
    {"target": "site/mappings/index.html", "kind": "file",
     "sources": ("tools/gen_draft_site.py", "site/draft-src/mappings.toml",
                 ".aiqt/standards/", ".aiqt/core/rules/"),
     "regenerate": "python3 tools/gen_draft_site.py"},
    {"target": "site/rules/index.html", "kind": "file",
     "sources": ("tools/gen_draft_site.py", "site/draft-src/rules.toml"),
     "regenerate": "python3 tools/gen_draft_site.py"},
    {"target": "site/standard/index.html", "kind": "file",
     "sources": ("tools/gen_draft_site.py", "site/draft-src/standard.toml"),
     "regenerate": "python3 tools/gen_draft_site.py"},
    {"target": "opf/site/index.html", "kind": "file",
     "sources": ("tools/gen_draft_site.py", "opf/site/draft-src/home.toml"),
     "regenerate": "python3 tools/gen_draft_site.py"},
    {"target": "opf/site/404.html", "kind": "file",
     "sources": ("tools/gen_draft_site.py", "opf/site/draft-src/404.toml"),
     "regenerate": "python3 tools/gen_draft_site.py"},
    {"target": "opf/site/adopt/index.html", "kind": "file",
     "sources": ("tools/gen_draft_site.py", "opf/site/draft-src/adopt.toml"),
     "regenerate": "python3 tools/gen_draft_site.py"},
    {"target": "opf/site/core-files/index.html", "kind": "file",
     "sources": ("tools/gen_draft_site.py", "opf/site/draft-src/core-files.toml"),
     "regenerate": "python3 tools/gen_draft_site.py"},
    {"target": "opf/site/draft/index.html", "kind": "file",
     "sources": ("tools/gen_draft_site.py", "opf/site/draft-src/draft.toml"),
     "regenerate": "python3 tools/gen_draft_site.py"},
    {"target": "opf/site/disclosure/index.html", "kind": "file",
     "sources": ("tools/gen_draft_site.py", "opf/site/draft-src/disclosure.toml"),
     "regenerate": "python3 tools/gen_draft_site.py"},
    {"target": "opf/site/license/index.html", "kind": "file",
     "sources": ("tools/gen_draft_site.py", "opf/site/draft-src/license.toml"),
     "regenerate": "python3 tools/gen_draft_site.py"},
    {"target": "opf/site/model/index.html", "kind": "file",
     "sources": ("tools/gen_draft_site.py", "opf/site/draft-src/model.toml"),
     "regenerate": "python3 tools/gen_draft_site.py"},
    {"target": "opf/site/standard/index.html", "kind": "file",
     "sources": ("tools/gen_draft_site.py", "opf/site/draft-src/standard.toml"),
     "regenerate": "python3 tools/gen_draft_site.py"},
)

# The 6 project sites in rainbow order (specification section 1): key, name word, domain, footer slice.
FAMILY = (
    ("secureconfig", "SecureConfig", "secureconfig.ai", 1),
    ("opfiles", "OPFiles", "opfiles.ai", 2),
    ("auditthis", "AuditThis", "auditthis.ai", 3),
    ("cleanlanguage", "CleanLanguage", "cleanlanguage.ai", 4),
    ("aiqt", "AIQT", "aiqt.ai", 5),
    ("grclibrary", "GRCLibrary", "grclibrary.ai", 6),
)

# Kept live paths (template SPECIFICATION 4.1): the root-relative live targets an aiqt.ai draft page may
# link, each of which exists under site/ today and keeps its path at promotion. A kept link renders as a
# plain root-relative anchor with no new-tab hint. The link guard (_LinkGuard) refuses every other URL
# attribute value that is not under the site's base, a #fragment or an https URL, in any element, case or
# quoting html.parser reads; its residual is stated at the guard.
KEPT_LIVE_PATHS = (
    "/development",
    "/downloads/aiqt-instructions-1_5k.txt",
    "/downloads/aiqt-instructions-5k.txt",
    "/downloads/aiqt-instructions-8k.txt",
    "/downloads/aiqt-instructions.txt",
    "/downloads/aiqt-skill-1.0.6.zip",
    "/downloads/mappings.csv",
    "/downloads/mappings.json",
    "/enforcement",
    "/evidence",
    "/tech-details",
)

# The opfiles.ai kept live paths: its machine-readable entry point and its adoption guide, each of which
# exists under opf/site/ today and keeps its path at promotion.
OPF_KEPT_LIVE_PATHS = (
    "/adopt.md",
    "/llms.txt",
)

# The per-site context; aiqt.ai first. Each site has its own page data directory ("src", 1 TOML file per
# page, discovered by listing it), its output root, its live root (where its kept live paths must exist),
# its base path ("/draft/" while staging, "/" at its own promotion; both sites are promoted), its asset
# base (the URL folder holding assets/baseline/, kept at "/draft/" where the files are served), its key and name, its logo alt text,
# its kept live paths and its nav. Nav items are the site's own pages, then GitHub last; Disclosure and
# Licence are not in the bar (the home page's About region links them). An internal nav item is emitted
# only once its page's data file is registered, so every nav link resolves while the pages land one at a
# time. Only a site with "control" set renders the document control strip (template specification 7.7:
# aiqt.ai only), so no other site's page can carry the AIQT VERSION as its own release.
SITES = (
    {"key": "aiqt", "name": "AIQT", "src": "site/draft-src", "root": "site", "live": "site",
     "base": "/", "assets": DRAFT_BASE, "control": True,
     "logo-alt": "A shield bearing a check mark, set on a guardrail",
     "github": "https://github.com/jposluns/guardrails",
     "kept": KEPT_LIVE_PATHS,
     "nav": (("install/", "Install"), ("standard/", "The standard"), ("rules/", "The 5 rules"),
             ("mappings/", "Mappings"))},
    {"key": "opfiles", "name": "OPFiles", "src": "opf/site/draft-src", "root": "opf/site",
     "live": "opf/site", "base": "/", "assets": DRAFT_BASE, "control": False,
     "logo-alt": "A machine-written source record and its generated view, joined by a drift gate",
     "github": "https://github.com/jposluns/guardrails",
     "kept": OPF_KEPT_LIVE_PATHS,
     "nav": (("model/", "The model"), ("standard/", "The standard"), ("core-files/", "Core files"),
             ("adopt/", "Adopt"))},
)


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
    """The table as a dict after shape checks: every required key present, no unknown key, string values
    (a key listed with a trailing marker in `required`/`optional` is checked by its own caller)."""
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
    """An inline HTML value with the $BASE/ token rendered as the site's base path."""
    return value.replace("$BASE/", site["base"])


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


def _version_and_status():
    """("AIQT X.Y.Z", "Published YYYY-MM-DD") from VERSION and changelog.toml; RefusedPage when the
    released version has no changelog entry, so a stale strip cannot render."""
    try:
        with open(ROOT / "VERSION", encoding="utf-8") as handle:
            version = handle.read().strip()
        with open(ROOT / "changelog.toml", "rb") as handle:
            data = handle.read()
    except (OSError, UnicodeError) as exc:
        raise RefusedPage("control strip inputs: {}".format(exc)) from exc
    try:
        log = tomllib.loads(data.decode("utf-8"))
    except (RecursionError, MemoryError, ValueError, UnicodeError) as exc:
        # tomllib raises the ValueError family on malformed TOML and RecursionError or MemoryError on
        # hostile nesting; each is disposed of here, at the parse site, as a refusal.
        raise RefusedPage("changelog.toml: cannot parse ({}: {})".format(type(exc).__name__, exc)) from exc
    for release in log.get("release", []):
        if isinstance(release, dict) and release.get("version") == version:
            date = release.get("date")
            if isinstance(date, str) and date:
                return "AIQT " + version, "Published " + date
    raise RefusedPage("changelog.toml: no dated release entry for VERSION {}".format(version))


def _c_control(site, doc, where):
    if not site.get("control"):
        raise RefusedPage("{}: the document control strip belongs to aiqt.ai only".format(where))
    spec = _need(doc, where, ("component", "document", "source_text", "source_href"),
                 ("licence_text", "licence_href"))
    source_href = _text(spec, where, "source_href")
    if not source_href.startswith("https://"):
        raise RefusedPage("{}: source_href must be an https URL".format(where))
    version, status = _version_and_status()
    licence = '<a href="{}">{}</a>'.format(
        _attr(_internal_href(site, spec.get("licence_href", "license/"), where)),
        _t(_text(spec, where, "licence_text", "Apache 2.0, see NOTICE")))
    return ('<dl class="control"><div><dt>Document</dt><dd>{}</dd></div>'
            '<div><dt>Version</dt><dd>{}</dd></div><div><dt>Status</dt><dd>{}</dd></div>'
            '<div><dt>Source</dt><dd>{}</dd></div><div><dt>Licence</dt><dd>{}</dd></div></dl>').format(
                _t(_text(spec, where, "document")), _t(version), _t(status),
                _external(source_href, _t(_text(spec, where, "source_text"))), licence)


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
    """The words of `text` greedily packed into lines of at most `limit` characters."""
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


_MAPPINGS_VIEWS = ("by-rule", "by-framework")
_MAPPINGS_DATA = None


def _mappings_data():
    """(rows, registry) derived from the same corpus and manifests tools/gen_mappings.py renders the
    live page from, loaded once per process, so the draft bodies cannot drift from the mappings data."""
    global _MAPPINGS_DATA
    if _MAPPINGS_DATA is None:
        try:
            manifests = load_manifests(ROOT / ".aiqt" / "standards", root=ROOT)
            corpus = gen_rules.load_corpus(ROOT / ".aiqt" / "core" / "rules")
            corpus.sort(key=lambda item: sort_key(item[1]))
            rows = gen_mappings.build_rows(corpus, manifests)
            reg = gen_mappings.registry_rows(manifests, rows)
        except (ManifestError, ValueError, OSError, KeyError) as exc:
            raise RefusedPage("mappings data: {}".format(exc)) from exc
        _MAPPINGS_DATA = (rows, reg)
    return _MAPPINGS_DATA


_COMMIT_HASH = re.compile(r"(?<![0-9a-f])[0-9a-f]{40}(?![0-9a-f])")


def _label_html(value):
    """Element text for a framework label, each full commit hash in it shown as its first 7 characters
    with the full hash in the title (the registry cell's form), so a 40-character run cannot widen a
    narrow page."""
    text = gen_mappings._text
    out, pos = [], 0
    for m in _COMMIT_HASH.finditer(value):
        out.append(text(value[pos:m.start()]))
        out.append('<code class="hash" title="{}">{}</code>'.format(m.group(), m.group()[:7]))
        pos = m.end()
    out.append(text(value[pos:]))
    return "".join(out)


def _mappings_by_rule(rows):
    """The draft By rule body: every mapped rule disclosed with its frameworks and identifiers, in the
    live page's canonical order, in the draft page's plain-details markup."""
    text = gen_mappings._text
    blocks = []
    for _rid, rrows in gen_mappings._group_ordered(rows, "rule_id").items():
        parts = ["<details><summary>{}</summary><p>{}</p><ul>".format(
            text(rrows[0]["rule_title"]),
            _external(rrows[0]["rule_source"], "View this rule on GitHub"))]
        groups = gen_mappings._group_ordered(rrows, "framework")
        for _fw, frows in sorted(groups.items(),
                                 key=lambda item: gen_mappings._framework_sort_key(item[1][0])):
            head = frows[0]
            parts.append("<li>{} ({}): {}<ul>".format(
                _label_html(head["framework_name"]), _label_html(head["edition"]),
                text(head["relation"])))
            for row in frows:
                parts.append("<li>{}: {} ({})</li>".format(
                    text(row["identifier"]), text(row["identifier_title"]), text(row["fit"])))
            parts.append("</ul></li>")
        parts.append("</ul></details>")
        blocks.append("".join(parts))
    return '<div class="wide">{}</div>'.format("".join(blocks))


def _mappings_by_framework(rows, reg):
    """The draft By framework body: each cited framework disclosed with its identifiers and the rules
    that cite them, the coverage sentence worded by the catalogue invariant, in the draft markup."""
    text = gen_mappings._text
    export_rows = sorted(rows, key=gen_mappings._framework_sort_key)
    by_fw = gen_mappings._group_ordered(export_rows, "framework")
    blocks = []
    for r in reg:
        frows = by_fw.get(r["framework"])
        if not frows:
            continue
        parts = ["<details><summary>{} ({})</summary><p>{}. {}.</p><ul>".format(
            _label_html(r["name"]), _label_html(r["edition"]), text(r["relation"]),
            text(gen_mappings._coverage_line(r)))]
        by_id = {}
        for row in frows:
            by_id.setdefault(row["identifier"], []).append(row)
        for cid in sorted(by_id, key=gen_mappings.natkey):
            idrows = by_id[cid]
            parts.append("<li>{}: {}<ul>".format(text(cid), text(idrows[0]["identifier_title"])))
            for row in idrows:
                parts.append("<li>{} ({})</li>".format(
                    _external(row["rule_source"], text(row["rule_title"])),
                    text(row["fit"])))
            parts.append("</ul></li>")
        parts.append("</ul></details>")
        blocks.append("".join(parts))
    return '<div class="wide">{}</div>'.format("".join(blocks))


def _c_mappings(site, doc, where):
    spec = _need(doc, where, ("component", "view"))
    view = _text(spec, where, "view")
    if view not in _MAPPINGS_VIEWS:
        raise RefusedPage("{}: view must be one of {}".format(where, ", ".join(_MAPPINGS_VIEWS)))
    rows, reg = _mappings_data()
    if view == "by-rule":
        return _mappings_by_rule(rows)
    return _mappings_by_framework(rows, reg)


def _checked_id(spec, where):
    value = spec.get("id")
    if not isinstance(value, str) or ID_RE.fullmatch(value) is None:
        raise RefusedPage("{}: {!r} is not a valid id".format(where, value))
    return value


COMPONENTS = {
    "hero": _c_hero, "entries": _c_entries, "region": _c_region, "value": _c_value, "about": _c_about,
    "control": _c_control, "clause": _c_clause, "clauses": _c_clauses, "note": _c_note,
    "callout": _c_callout, "figures": _c_figures, "table": _c_table, "register": _c_register,
    "code": _c_code, "picker": _c_picker, "family": _c_family, "download": _c_download, "plan": _c_plan,
    "scope": _c_scope, "next": _c_next, "lead": _c_lead, "label": _c_label, "section": _c_section,
    "entry": _c_entry, "prose": _c_prose, "mappings": _c_mappings, "list": _c_list, "figure": _c_figure,
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


def _kept_live_missing(site):
    """Every kept live path of the site with no target under its live root: the path as-is for a file
    target, or the path plus ".html" for an extensionless page, matching how the live site serves it."""
    missing = []
    for path in site["kept"]:
        rel = path.lstrip("/")
        candidates = [ROOT / site["live"] / rel]
        if "." not in rel.rsplit("/", 1)[-1]:
            candidates.append(ROOT / site["live"] / (rel + ".html"))
        if not any(candidate.is_file() for candidate in candidates):
            missing.append(path)
    return missing


def promoted_sites(sites=SITES):
    """Each site as it stands at its promotion: base "/" and its pages written to its live root."""
    return tuple(dict(site, base="/", root=site["live"]) for site in sites)


def staged_sites(sites=SITES):
    """Each site as it stood before its promotion: base "/draft/" and its pages written under its live
    root's draft/ folder. The self-test builds this form too, so the staging rules stay proven."""
    return tuple(dict(site, base=DRAFT_BASE, root=site["live"] + "/draft") for site in sites)


def build(pages=None, sites=SITES, registered=True):
    """{target path: text} for every registered output. Each site is loaded from its own data directory
    and rendered with its own context; with the default page sets, the built set must match the
    GENSRC_OUTPUTS declaration, so a page cannot land or vanish without its registry entry. A given
    `pages` list (a fixture) is rendered for every site in `sites`. Every kept live path of a site must
    exist under its live root before anything renders, and no 2 sites may build the same output path.
    `registered` false skips the declaration match, for a fixture build of the declared pages at another
    base (promoted_sites)."""
    declared = pages is None
    outputs = {}
    for site in sites:
        missing = _kept_live_missing(site)
        if missing:
            raise RefusedPage("kept live path(s) with no target under {}/: {}".format(
                site["live"], ", ".join(missing)))
        site_pages = _load_pages(ROOT / site["src"], site["src"]) if declared else pages
        for page in site_pages:
            target = "{}/{}".format(site["root"], page["rel"])
            if target in outputs:
                raise RefusedPage("{}: built by more than 1 site".format(target))
            outputs[target] = render_page(site, site_pages, page)
    if declared and registered:
        targets = {entry["target"] for entry in GENSRC_OUTPUTS}
        if set(outputs) != targets:
            raise RefusedPage("built outputs {} differ from GENSRC_OUTPUTS {}".format(
                sorted(outputs), sorted(targets)))
    return outputs


# The ASCII whitespace a browser strips from both ends of a URL attribute value.
_URL_SPACE = "\t\n\f\r "


class _RootLinks(HTMLParser):
    """Every root-relative href and src value a page writes (one leading "/", not a "//host" URL, once the
    whitespace a browser strips is set aside), as written."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.found = []

    def handle_starttag(self, tag, attrs):
        for name, value in attrs:
            if name in ("href", "src") and isinstance(value, str) and value.strip(_URL_SPACE).startswith("/") \
                    and not value.strip(_URL_SPACE).startswith("//"):
                self.found.append(value)


def _asset_file(asset_dir, rest):
    """True only when `rest` (the URL path after the asset folder's URL) names a file inside asset_dir: it
    must be relative, with no empty, "." or ".." segment (also after one percent-decoding, as a browser reads
    %2e), and must resolve inside the folder."""
    if any(unquote(segment) in ("", ".", "..") for segment in rest.split("/")):
        return False
    folder = Path(asset_dir).resolve()
    candidate = (folder / rest).resolve()
    return candidate.is_relative_to(folder) and candidate != folder and candidate.is_file()


def _redirect_sources(path):
    """The source path (the first field) of every rule line of a _redirects file; none when it is absent.
    OSError or UnicodeDecodeError when it cannot be read."""
    try:
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
    except FileNotFoundError:
        return frozenset()
    return frozenset(line.split()[0] for line in text.splitlines()
                     if line.strip() and not line.lstrip().startswith("#"))


def unresolved_links(outputs, sites=SITES, tree=ROOT):
    """Every root-relative href or src on a built page that resolves to none of: a page the same site
    generates, a file under the site's template asset folder (in `tree`), one of its kept live paths, or the
    source of a rule in its live root's _redirects file. The path is compared without its #fragment or
    ?query, so it holds at any base: the generated page URLs, the asset folder and the redirects all follow
    the site's own base and root. A value with leading or trailing whitespace is refused, as is an asset path
    that is absolute, has an empty or dot segment, or resolves outside the asset folder (_asset_file)."""
    found = []
    for site in sites:
        prefix = site["root"] + "/"
        rels = sorted(target[len(prefix):] for target in outputs if target.startswith(prefix))
        pages = {_page_url(site, rel) for rel in rels}
        asset_url = site["assets"] + ASSETS
        asset_dir = Path(tree) / _asset_folder(site)
        redirects = _redirect_sources(Path(tree) / site["live"] / "_redirects")
        for rel in rels:
            parser = _RootLinks()
            parser.feed(outputs[prefix + rel])
            parser.close()
            for value in parser.found:
                if value != value.strip(_URL_SPACE):
                    found.append("{}{}: {!r} has leading or trailing whitespace".format(prefix, rel, value))
                    continue
                path, sep, frag = value.partition("#")
                path = path.split("?", 1)[0]
                if path in pages or path in redirects or _kept_live(site, path + sep + frag):
                    continue
                if path.startswith(asset_url) and _asset_file(asset_dir, path[len(asset_url):]):
                    continue
                found.append("{}{}: {} resolves to no generated page, template asset, kept live path or "
                             "redirect".format(prefix, rel, value))
    return found


def stray_outputs(tree, sites=SITES, targets=None):
    """Every .html file (the extension in any case) in a folder that holds a generated page of a site (the
    folder of a target) that the targets (GENSRC_OUTPUTS by default) do not list, apart from a kept live
    path's own file. Only those
    folders are listed, not their subfolders, so the asset folder and a promoted root's other folders (its
    downloads, its draft placeholder) are not read; a page in a folder holding no generated page is not
    found here."""
    if targets is None:
        targets = {entry["target"] for entry in GENSRC_OUTPUTS}
    found = []
    for site in sites:
        prefix = site["root"] + "/"
        kept = set()
        for path in site["kept"]:
            live = "{}/{}".format(site["live"], path.lstrip("/"))
            kept.update((live, live + ".html"))
        for folder in sorted({target.rsplit("/", 1)[0] for target in targets if target.startswith(prefix)}):
            try:
                with os.scandir(Path(tree) / folder) as entries:
                    names = sorted(entry.name for entry in entries if not entry.is_dir(follow_symlinks=False))
            except FileNotFoundError:
                continue
            for name in names:
                rel = "{}/{}".format(folder, name)
                if name.lower().endswith(".html") and rel not in targets and rel not in kept:
                    found.append(rel)
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


def run(root, check, outputs=None):
    """Write (or with `check`, compare) the built pages under root. `outputs` (a self-test's prebuilt pages)
    defaults to build()."""
    try:
        if outputs is None:
            outputs = build()
    except RefusedPage as exc:
        print("error: refused: {}".format(exc), file=sys.stderr)
        return 2
    try:
        unresolved = unresolved_links(outputs)
    except (OSError, ValueError) as exc:
        print("error: cannot read a redirects file ({}); fail-closed".format(exc), file=sys.stderr)
        return 2
    if unresolved:
        for finding in unresolved:
            print("error: refused: {}".format(finding), file=sys.stderr)
        return 2
    drift = False
    for rel, text in sorted(outputs.items()):
        path = Path(root) / rel
        data = text.encode("utf-8")
        try:
            current = _existing(path)
        except OSError as exc:
            print("error: {}: {}".format(rel, exc), file=sys.stderr)
            return 2
        if current == data:
            continue
        if check:
            print("drift: {}".format(rel))
            drift = True
            continue
        for parent in reversed(path.relative_to(root).parents):
            if parent != Path(".") and (Path(root) / parent).is_symlink():
                print("error: {} is a symbolic link; refusing".format(parent), file=sys.stderr)
                return 2
        os.makedirs(path.parent, exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(data)
        print("wrote {}".format(rel))
    try:
        stray = stray_outputs(root)
    except OSError as exc:
        print("error: cannot list a page folder ({}); fail-closed".format(exc), file=sys.stderr)
        return 2
    for rel in stray:
        print("stray: {} is in a generated page folder but not in GENSRC_OUTPUTS".format(rel))
    if drift:
        print("run tools/gen_draft_site.py to regenerate")
    return 1 if drift or stray else 0


def _self_test():
    failures = []

    def expect(name, ok, detail=""):
        if not ok:
            failures.append("{}: {!r}".format(name, detail))

    def refused(name, callable_, *args):
        try:
            callable_(*args)
        except RefusedPage as exc:
            return str(exc)
        failures.append("{}: expected a refusal".format(name))
        return ""

    # The declared build is the promoted one (base "/", pages at each live root); most fixtures below are
    # the same pages built in their staged form (staged_sites), so the staging rules keep their cases.
    live_outputs = build()
    staged_ = staged_sites()
    site = staged_[0]
    outputs = build(sites=staged_, registered=False)
    page = outputs["site/draft/index.html"]
    expect("shell/html-class", '<html lang="en" class="site-aiqt">' in page)
    expect("shell/title", "<title>Home | AIQT.ai</title>" in page)
    expect("shell/robots-meta", page.count('<meta name="robots" content="noindex, nofollow">') == 1)
    order = [page.find(_asset(site, sheet)) for sheet in ("fonts.css", "palettes.css", "baseline.css")]
    expect("shell/stylesheet-order", -1 not in order and order == sorted(order), order)
    # Every page of both sites: scheme.js once in the head, before the first stylesheet, and the switch
    # once, as the last child of header.mast right after nav.nav.
    scheme_tag = '<script data-cfasync="false" src="{}"></script>'
    for target, built in sorted(outputs.items()):
        built_site = next(s for s in staged_ if target.startswith(s["root"] + "/"))
        tag = scheme_tag.format(_asset(built_site, SCHEME_SCRIPT))
        head = built.split("</head>", 1)[0]
        expect("shell/scheme-script-once " + target, built.count(tag) == 1 and tag in head)
        expect("shell/scheme-script-before-stylesheets " + target,
               -1 < head.find(tag) < head.find('<link rel="stylesheet"'))
        expect("shell/scheme-button-last-in-mast " + target, built.count(SCHEME_BUTTON) == 1
               and re.search(r'<header class="mast">.*?</nav>' + re.escape(SCHEME_BUTTON) + "</header>", built))
    # Every page of both sites: each head line in render_page's order (the description metas after the
    # robots meta and before the title), and the 2 description metas, each on its own line, exactly on the
    # pages whose data carries a meta, with that meta's plain text.
    head_order = ('<meta charset="utf-8">', '<meta name="viewport" ', '<meta name="robots" ',
                  '<meta name="description" ', '<meta property="og:description" ', "<title>",
                  '<link rel="icon" ', '<link rel="apple-touch-icon" ', '<script data-cfasync="false" ',
                  '<link rel="stylesheet" ', '<script src="')
    described = {}
    for built_site in staged_:
        for model_page in _load_pages(ROOT / built_site["src"]):
            described[built_site["root"] + "/" + model_page["rel"]] = _description(built_site, model_page)
    expect("meta/description-every-page", sorted(described) == sorted(outputs), sorted(described))
    expect("meta/description-every-page-has-one", all(described.values()), described)
    for target, built in sorted(outputs.items()):
        lines = built.split("<head>\n", 1)[1].split("\n</head>", 1)[0].split("\n")
        ranks = [next((rank for rank, start in enumerate(head_order) if line.startswith(start)), None)
                 for line in lines]
        expect("shell/head-order " + target, None not in ranks and ranks == sorted(ranks), lines)
        text = described.get(target, "")
        metas = ['<meta name="description" content="{}">'.format(_attr(text)),
                 '<meta property="og:description" content="{}">'.format(_attr(text))]
        if text:
            expect("meta/description-present " + target,
                   all(lines.count(meta) == 1 and built.count(meta) == 1 for meta in metas), lines)
        else:
            expect("meta/description-absent " + target, "description" not in built.split("</head>", 1)[0])
    mappings_head = outputs["site/draft/mappings/index.html"].split("</head>", 1)[0]
    expect("meta/mappings-parenthetical", mappings_head.count(
        "frameworks (OWASP, NIST, MITRE ATLAS, ISO/IEC, CSA)") == 2, mappings_head)
    expect("shell/scheme-button-hidden", SCHEME_BUTTON.startswith(
        '<button class="scheme" type="button" data-scheme-toggle aria-pressed="false" hidden>')
           and SCHEME_BUTTON.endswith('<span class="vh">Dark mode</span></button>'))
    expect("shell/no-site-css", "site.css" not in page)
    expect("shell/favicon", '<link rel="icon" href="/draft/assets/baseline/logos/aiqt-favicon.svg"' in page)
    for rel in ("index.html", "disclosure/index.html", "license/index.html"):
        expect("shell/touch-icon " + rel, outputs["site/draft/" + rel].count(
            '<link rel="apple-touch-icon" href="/draft/assets/baseline/logos/aiqt-touch.png">') == 1)
    expect("shell/skip-first", page.split("<body>\n", 1)[1].startswith('<a class="skip" href="#main-content">'))
    expect("shell/one-h1", page.count("<h1") == 1)
    expect("shell/copy-status-last", page.endswith(
        '<p class="vh" id="copy-status" role="status" aria-live="polite"></p>\n</body>\n</html>\n'))
    slices = re.findall(r'<li class="slice slice-(\d)">', page)
    expect("footer/aiqt-first-cycle", slices == ["5", "6", "1", "2", "3", "4"], slices)
    expect("footer/current-card", page.count('aria-current="page"><img src="/draft/assets/baseline/logos/aiqt.svg"') == 1)
    navs = [re.search(r'<nav class="nav" aria-label="Site">(.*?)</nav>', outputs["site/draft/" + rel]).group(1)
            for rel in ("index.html", "disclosure/index.html", "license/index.html")]
    # The unregistered set is derived from the site nav against the loaded pages, so the check follows
    # the pages as they land instead of hard-coding one path as absent.
    registered_urls = {_page_url(site, page["rel"]) for page in _load_pages(ROOT / site["src"])}
    nav_urls = [_url(site, path) for path, _label in site["nav"]]
    unregistered = [href for href in nav_urls if href not in registered_urls]
    expect("nav/unregistered-items-absent",
           all('href="{}"'.format(href) not in nav for nav in navs for href in unregistered),
           (unregistered, navs))
    expect("nav/registered-items-present",
           all('href="{}"'.format(href) in nav for nav in navs
               for href in nav_urls if href in registered_urls), navs)
    expect("nav/no-disclosure-or-licence", all("disclosure" not in nav and "license" not in nav for nav in navs),
           navs)
    expect("nav/github-last", all(nav.endswith('GitHub<span class="vh"> (opens in a new tab)</span></a>')
                                  for nav in navs), navs)
    about = ('<div class="region"><p class="lbl">About this project</p><div><p class="about">'
             '<a href="/draft/disclosure/">Disclosure</a><span class="dot" aria-hidden="true">&middot;</span> '
             '<a href="/draft/license/">Licence</a><span class="dot" aria-hidden="true">&middot;</span> '
             '<a href="https://posluns.ca/privacy/" target="_blank" rel="noopener noreferrer">Privacy'
             '<span class="vh"> (opens in a new tab)</span></a><span class="dot" aria-hidden="true">&middot;</span> '
             '<a href="https://posluns.ca/security/" target="_blank" rel="noopener noreferrer">Security'
             '<span class="vh"> (opens in a new tab)</span></a></p></div></div>')
    expect("about/landing-exact-markup", page.count(about) == 1)
    expect("about/after-paragraph", 0 < page.find("arrive with 1.1.0.</p>") < page.find(about))
    expect("about/inner-pages-none", all('class="about"' not in outputs["site/draft/" + rel]
                                         for rel in ("disclosure/index.html", "license/index.html")))
    expect("placeholder/index.html", page.count("<h1") == 1
           and '<h1 class="statement two">Rules your AI follows. <em>Controls that catch what slips.'
               '</em></h1>' in page
           and "<title>Home | AIQT.ai</title>" in page)
    for rel, title, heading in (("disclosure/index.html", "Disclosure", "Disclosure"),
                                ("license/index.html", "Licence", "Apache License 2.0")):
        inner = outputs["site/draft/" + rel]
        expect("placeholder/" + rel, inner.count("<h1") == 1 and "<h1>{}</h1>".format(heading) in inner
               and "<title>{} | AIQT.ai</title>".format(title) in inner)
    hrefs = re.findall(r'(?:href|src)="([^"]*)"', page)
    internal = [h for h in hrefs if not h.startswith("https://") and not h.startswith("#")]
    expect("base/every-internal-path", internal and all(h.startswith(DRAFT_BASE) for h in internal), internal)
    externals = re.findall(r'<a [^>]*href="https://[^"]*"[^>]*>', page)
    expect("base/external-newtab", externals and all('target="_blank" rel="noopener noreferrer"' in a for a in externals))
    expect("refuse/clean-page-passes", refusals_for(page) == [], refusals_for(page))

    for name, text in (("refuse/en-dash", "<p>a – b</p>"), ("refuse/em-dash", "<p>a — b</p>"),
                       ("refuse/style-element", "<style>p{}</style>"),
                       ("refuse/style-attribute", '<p style="color:red">x</p>'),
                       ("refuse/inline-script", "<script>alert(1)</script>"),
                       ("refuse/onclick", '<a href="/" onclick="x()">x</a>'),
                       ("refuse/onload-uppercase", '<body ONLOAD="x()">'),
                       ("refuse/framing", "<p>What this is: a site.</p>"),
                       ("refuse/framing-split", "<p>what\n  it is</p>")):
        expect(name, refusals_for(text) != [], text)
    expect("refuse/external-script-allowed", refusals_for('<script src="/draft/x.js" defer></script>') == [])
    expect("refuse/word-only-allowed", refusals_for("<p>Here is what the standard is for.</p>") == [])

    # The data model: a page dict straight from parsed TOML text, as _load_pages produces it.
    def model(text, where="fixture.toml"):
        return _page_from_doc(tomllib.loads(text), where)

    def fixture_page(body_toml, kind="document"):
        head = 'path = "index.html"\ntitle = "Home"\nkind = "{}"\n'.format(kind)
        if kind == "document":
            head += 'label = "Draft"\nheading = "Fixture"\n'
        return model(head + body_toml)

    def rendered(component_toml, kind="document"):
        page_model = fixture_page('[[body]]\n' + component_toml, kind)
        return COMPONENTS[page_model["body"][0]["component"]](site, page_model["body"][0], "fixture.body[1]")

    # Every component renders the reference markup (specification section 7 and the reference pages).
    hero = rendered('component = "hero"\nkicker = "K"\nstatement = "S <em>e.</em>"\ntwo = true\n'
                    'intro = "I <span class=\\"ordering\\">O</span>"\nstart_text = "Go"\nstart_href = "install/"\n',
                    kind="landing")
    expect("component/hero", hero == (
        '<section class="hero" aria-label="Introduction"><div class="hero-text"><p class="lbl gold">K</p>'
        '<h1 class="statement two">S <em>e.</em></h1><p class="intro">I <span class="ordering">O</span></p>'
        '<a class="start" href="/draft/install/">Go <span class="arrow" aria-hidden="true">&rarr;</span></a>'
        '</div><div class="hero-art"><img src="/draft/assets/baseline/logos/aiqt.svg" '
        'alt="A shield bearing a check mark, set on a guardrail"></div></section>'), hero)
    tiles = rendered('component = "entries"\n[[body.items]]\nhref = "install/"\ntitle = "T"\nn = "4 steps"\n'
                     'd = "D"\n', kind="landing")
    expect("component/entries", tiles == (
        '<div class="region"><p class="lbl">Start here</p><div><ol class="entries"><li>'
        '<a href="/draft/install/"><span class="t">T</span><span class="n">4 steps</span>'
        '<span class="d">D</span></a></li></ol></div></div>'), tiles)
    region = rendered('component = "region"\nlabel = "L"\nhtml = "<p>X</p>"\n', kind="landing")
    expect("component/region", region == '<div class="region"><p class="lbl">L</p><div><p>X</p></div></div>',
           region)
    value = rendered('component = "value"\nhtml = "V <a href=\\"$BASE/license/\\">free</a>."\n', kind="landing")
    expect("component/value", value == (
        '<div class="region"><p class="lbl">What it gives you</p><div><p class="value">V '
        '<a href="/draft/license/">free</a>.</p></div></div>'), value)
    expect("component/about", rendered('component = "about"\n', kind="landing") == _about(site))
    with open(ROOT / "VERSION", encoding="utf-8") as handle:
        released = handle.read().strip()
    control = rendered('component = "control"\ndocument = "aiqt.ai"\nsource_text = "jposluns/guardrails"\n'
                       'source_href = "https://github.com/jposluns/guardrails"\n')
    expect("component/control", control.startswith(
        '<dl class="control"><div><dt>Document</dt><dd>aiqt.ai</dd></div>'
        '<div><dt>Version</dt><dd>AIQT {}</dd></div><div><dt>Status</dt><dd>Published '.format(released))
        and '<div><dt>Licence</dt><dd><a href="/draft/license/">Apache 2.0, see NOTICE</a></dd></div></dl>'
        in control, control)
    clause = rendered('component = "clause"\nid = "st-1"\nnum = "1"\ntitle = "T"\nlbl = "Clause 1"\n'
                      'rail = "T"\n')
    expect("component/clause", clause == (
        '<p class="lbl sec">Clause 1</p>\n<h2 class="clause" id="st-1"><span class="num">1</span>'
        '<span>T</span></h2>'), clause)
    clauses = rendered('component = "clauses"\n[[body.items]]\nn = "2.1"\nhtml = "<p><strong>A.</strong> B</p>"\n')
    expect("component/clauses", clauses == (
        '<ol class="clauses"><li><span class="n">2.1</span><div><p><strong>A.</strong> B</p></div></li></ol>'),
        clauses)
    note = rendered('component = "note"\nlabel = "Rule 1"\nhtml = "<p>N</p>"\n')
    expect("component/note", note == (
        '<div class="note"><span class="label">Rule 1</span><div><p>N</p></div></div>'), note)
    callout = rendered('component = "callout"\nlabel = "Note"\nrule = true\nhtml = "<p>C</p>"\n')
    expect("component/callout", callout == (
        '<p class="lbl accent blk">Note</p>\n<div class="callout rule"><p>C</p></div>'), callout)
    figures = rendered('component = "figures"\n[[body.items]]\nnum = "17"\ncap = "frameworks"\n')
    expect("component/figures", figures == (
        '<div class="wide"><div class="figures"><div class="fig"><span class="num">17</span>'
        '<span class="cap">frameworks</span></div></div></div>'), figures)
    table = rendered('component = "table"\ncaption = "C"\nsticky = true\nfixed = true\ncols = ["w26", "w17"]\n'
                     'head = ["A", "B"]\nrows = [["x", "<td class=\\"id\\"><code>y</code></td>"]]\n')
    expect("component/table", table == (
        '<div class="wide"><div class="tablewrap bounded" tabindex="0"><table class="sticky fixed">'
        '<caption class="vh">C</caption><colgroup><col class="w26"><col class="w17"></colgroup>'
        '<thead><tr><th>A</th><th>B</th></tr></thead><tbody><tr><td>x</td><td class="id"><code>y</code>'
        '</td></tr></tbody></table></div></div>'), table)
    fixed = rendered('component = "table"\ncaption = "C"\nfixed = true\nhead = ["A"]\nrows = [["x"]]\n')
    expect("component/table-fixed-not-sticky-unbounded", fixed == (
        '<div class="wide"><div class="tablewrap" tabindex="0"><table class="fixed">'
        '<caption class="vh">C</caption><thead><tr><th>A</th></tr></thead><tbody><tr><td>x</td></tr>'
        '</tbody></table></div></div>'), fixed)
    register = rendered('component = "register"\nreg = "Register 1"\ntitle = "T"\ncount = "1 row"\n'
                        'caption = "C"\nhead = ["A"]\nrows = [["x"]]\n')
    expect("component/register", register == (
        '<div class="wide"><div class="register"><p class="reg-cap"><b>Register 1</b><span>T</span>'
        '<span class="count">1 row</span></p><div class="tablewrap" tabindex="0"><table class="">'
        '<caption class="vh">C</caption><thead><tr><th>A</th></tr></thead><tbody><tr><td>x</td></tr>'
        '</tbody></table></div></div></div>'), register)
    code = rendered('component = "code"\nid = "pin"\ntext = "a <b> & c"\n')
    expect("component/code-plain", code == (
        '<pre id="pin" class="hascopy"><button class="copy" type="button" data-copy="pin" hidden>Copy'
        '</button>a &lt;b&gt; &amp; c</pre>'), code)
    block = rendered('component = "code"\nid = "p1"\ntext = "t"\nvariant = "prompt-block"\n')
    expect("component/code-prompt-block", block == (
        '<pre id="p1" class="hascopy prompt-block" tabindex="0"><button class="copy" type="button" '
        'data-copy="p1" hidden>Copy</button>t</pre>'), block)
    flow = rendered('component = "code"\nid = "p2"\ntext = "t"\nvariant = "prompt-flow"\nwide = true\n'
                    'note = "No script note."\n')
    expect("component/code-prompt-flow", flow == (
        '<div class="wide"><pre id="p2" class="hascopy prompt-flow"><button class="copy" type="button" '
        'data-copy="p2" hidden>Copy</button>t</pre></div>\n<p class="code-note">No script note.</p>'), flow)
    picker = rendered('component = "picker"\nreset = "Show all AI systems"\n[[body.items]]\n'
                      'family = "claude"\nlabel = "Claude"\n')
    expect("component/picker", picker == (
        '<ul class="picker" data-picker><li><a href="#claude" data-family-link="claude">Claude</a></li>'
        '</ul><button class="btn picker-reset" type="button" data-picker-reset hidden>Show all AI systems'
        '</button><p class="vh" role="status" aria-live="polite" data-picker-status></p>'), picker)
    family = rendered('component = "family"\nfamily = "claude"\nlbl = "Claude"\nheading = "H"\n'
                      'verified = "Last verified 1 September 2026."\nintro = "<p>I</p>"\nsteps = ["S1", "S2"]\n')
    expect("component/family", family == (
        '<section id="claude" data-family-section="claude">\n<p class="lbl sec">Claude</p>\n<h2>H</h2>\n'
        '<p class="verified">Last verified 1 September 2026.</p>\n<p>I</p>\n'
        '<ol class="steps"><li><span>S1</span></li><li><span>S2</span></li></ol>\n</section>'), family)
    download = rendered('component = "download"\nlabel = "The skill"\nhref = "https://example.com/x.zip"\n'
                        'filename = "x.zip"\nsize = "zip, version 1"\nhtml = "D"\n')
    expect("component/download", download == (
        '<p class="lbl blk">The skill</p>\n<div class="download"><p class="file"><a '
        'href="https://example.com/x.zip" target="_blank" rel="noopener noreferrer">x.zip<span class="vh">'
        ' (opens in a new tab)</span></a></p><p class="lbl size">zip, version 1</p><p>D</p></div>'), download)
    plan = rendered('component = "plan"\nvariant = "three"\ndescribed = true\n[[body.items]]\n'
                    'href = "rules/"\ntitle = "T"\nn = "5"\nsmall = "s"\n')
    expect("component/plan", plan == (
        '<ul class="plan three described"><li><a href="/draft/rules/">T</a><span class="n">5</span>'
        '<small>s</small></li></ul>'), plan)
    scope = rendered('component = "scope"\nhtml = "S"\n')
    expect("component/scope", scope == '<p class="lbl blk">Scope</p>\n<p>S</p>', scope)
    nxt = rendered('component = "next"\nhtml = "Line."\ntext = "Home"\nhref = ""\n')
    expect("component/next", nxt == (
        '<div class="next"><p class="lbl">Next</p><p>Line.</p><a class="start" href="/draft/">Home '
        '<span class="arrow" aria-hidden="true">&rarr;</span></a></div>'), nxt)
    lead = rendered('component = "lead"\nhtml = "L"\n')
    expect("component/lead", lead == '<p class="lead">L</p>', lead)
    label = rendered('component = "label"\ntext = "Pick your AI"\n')
    expect("component/label", label == '<p class="lbl blk">Pick your AI</p>', label)
    section = rendered('component = "section"\nlbl = "Parts"\nheading = "H"\nid = "parts"\nrail = "Parts"\n')
    expect("component/section", section == '<p class="lbl sec">Parts</p>\n<h2 id="parts">H</h2>', section)
    entry = rendered('component = "entry"\nlbl = "Entry 1"\nid = "d-1"\nheading = "H"\nclaim = "C"\n'
                     'limitation = "L"\nevidence = "E"\n')
    expect("component/entry", entry == (
        '<p class="lbl sec">Entry 1</p>\n<h2 id="d-1">H</h2>\n<p><strong>Claim:</strong> C</p>\n'
        '<p><strong>Limitation:</strong> L</p>\n<p><strong>Evidence:</strong> E</p>'), entry)
    prose = rendered('component = "prose"\nhtml = "<p>P</p>"\n')
    expect("component/prose", prose == "<p>P</p>", prose)
    labelled = rendered('component = "label"\ntext = "Pick your assistant"\nid = "pick"\n')
    expect("component/label-id", labelled == '<p class="lbl blk" id="pick">Pick your assistant</p>',
           labelled)

    # The mappings component renders the two generated bodies from one synthetic (rows, registry) pair,
    # so the draft markup for each view is pinned exactly, escaping included.
    global _MAPPINGS_DATA
    saved_mappings = _MAPPINGS_DATA
    _MAPPINGS_DATA = ([{
        "rule_id": "r1", "rule_title": "R & T", "rule_source": "https://example.com/r1",
        "framework": "alpha", "framework_name": "Alpha <F>", "publisher": "P", "edition": "1.0",
        "kind": "control", "relation": "supports control", "status": "stable", "fit": "tight",
        "identifier": "ST01", "identifier_title": "one",
    }], [{
        "framework": "alpha", "name": "Alpha <F>", "publisher": "P", "edition": "1.0",
        "kind": "control", "relation": "supports control", "status": "stable",
        "citation_unit": "control", "source_artefact": "fixture", "retrieved": "2026-01-01",
        "catalogue": "full", "ids_cited": 1, "ids_total": 1,
    }])
    try:
        by_rule = rendered('component = "mappings"\nview = "by-rule"\n')
        expect("component/mappings-by-rule", by_rule == (
            '<div class="wide"><details><summary>R &amp; T</summary><p><a href="https://example.com/r1" '
            'target="_blank" rel="noopener noreferrer">View this rule on GitHub<span class="vh"> (opens '
            'in a new tab)</span></a></p><ul><li>Alpha &lt;F&gt; (1.0): supports control<ul>'
            '<li>ST01: one (tight)</li></ul></li></ul></details></div>'), by_rule)
        by_framework = rendered('component = "mappings"\nview = "by-framework"\n')
        expect("component/mappings-by-framework", by_framework == (
            '<div class="wide"><details><summary>Alpha &lt;F&gt; (1.0)</summary><p>supports control. '
            '1 of 1 identifiers referenced.</p><ul><li>ST01: one<ul><li><a href="https://example.com/r1" '
            'target="_blank" rel="noopener noreferrer">R &amp; T<span class="vh"> (opens in a new tab)'
            '</span></a> (tight)</li></ul></li></ul></details></div>'), by_framework)
        expect("refuse/mappings-bad-view", "view" in refused(
            "refuse/mappings-bad-view", rendered, 'component = "mappings"\nview = "sideways"\n'))
        # A 40-character commit hash in a framework label renders as the registry cell's short form,
        # the full hash only in the title, in both views; a 39- or 41-character hex run is left as text.
        full = "f54d8ded2764010ecacd0deb87e1586da32d41dd"
        short = '<code class="hash" title="{}">f54d8de</code>'.format(full)
        edition = "commit {} (2026-07-04)".format(full)
        _MAPPINGS_DATA = ([dict(_MAPPINGS_DATA[0][0], edition=edition)],
                          [dict(_MAPPINGS_DATA[1][0], edition=edition)])
        hashed_framework = rendered('component = "mappings"\nview = "by-framework"\n')
        expect("component/mappings-hash-by-framework",
               '<summary>Alpha &lt;F&gt; (commit {} (2026-07-04))</summary>'.format(short) in hashed_framework
               and hashed_framework.count(full) == 1, hashed_framework)
        hashed_rule = rendered('component = "mappings"\nview = "by-rule"\n')
        expect("component/mappings-hash-by-rule",
               '<li>Alpha &lt;F&gt; (commit {} (2026-07-04)): supports control<ul>'.format(short) in hashed_rule
               and hashed_rule.count(full) == 1, hashed_rule)
        expect("component/mappings-hash-bounds",
               _label_html(full[:39]) == full[:39] and _label_html(full + "0") == full + "0"
               and _label_html("<{}>".format(full)) == "&lt;{}&gt;".format(short), _label_html(full[:39]))
    finally:
        _MAPPINGS_DATA = saved_mappings

    # Kept live paths: a listed root-relative target renders unchanged with no new-tab hint; an unlisted
    # one, a prefix overrun and a malformed fragment are refused.
    kept_next = rendered('component = "next"\ntext = "Evidence"\nhref = "/evidence"\n')
    expect("links/kept-live-plain", kept_next == (
        '<div class="next"><p class="lbl">Next</p><a class="start" href="/evidence">Evidence '
        '<span class="arrow" aria-hidden="true">&rarr;</span></a></div>'), kept_next)
    kept_download = rendered('component = "download"\nhref = "/downloads/mappings.csv"\n'
                             'filename = "mappings.csv"\nsize = "s"\nhtml = "D"\n')
    expect("links/kept-live-download",
           '<a href="/downloads/mappings.csv">mappings.csv</a>' in kept_download, kept_download)
    expect("refuse/unkept-root-path", "kept live path" in refused(
        "refuse/unkept-root-path", rendered, 'component = "next"\ntext = "T"\nhref = "/elsewhere"\n'))
    expect("refuse/kept-path-prefix", "kept live path" in refused(
        "refuse/kept-path-prefix", rendered, 'component = "next"\ntext = "T"\nhref = "/evidence2"\n'))
    expect("refuse/kept-bad-fragment", "kept live path" in refused(
        "refuse/kept-bad-fragment", rendered, 'component = "next"\ntext = "T"\nhref = "/evidence#a b"\n'))
    expect("links/kept-fragment-passes", _link_findings(site, '<a href="/evidence#limits">x</a>') == [])
    expect("links/kept-is-per-site", _link_findings(staged_[1], '<a href="/evidence#limits">x</a>') != [])

    # The data model refuses what it does not know, with every key named.
    expect("refuse/unknown-component", "unknown component" in refused(
        "refuse/unknown-component", fixture_page, '[[body]]\ncomponent = "widget"\n'))
    expect("refuse/unknown-page-key", "unknown key" in refused(
        "refuse/unknown-page-key", model,
        'path = "index.html"\ntitle = "T"\nkind = "landing"\ncolour = "blue"\n[[body]]\ncomponent = "about"\n'))
    expect("refuse/unknown-component-key", "unknown key" in refused(
        "refuse/unknown-component-key", rendered, 'component = "scope"\nhtml = "S"\nextra = "x"\n'))
    expect("refuse/missing-key", "missing key" in refused(
        "refuse/missing-key", rendered, 'component = "note"\nlabel = "L"\n'))
    expect("refuse/wrong-type", refused("refuse/wrong-type", rendered,
                                        'component = "scope"\nhtml = 3\n') != "")
    expect("refuse/bad-kind", "kind" in refused(
        "refuse/bad-kind", model, 'path = "index.html"\ntitle = "T"\nkind = "poster"\n[[body]]\ncomponent = "about"\n'))
    expect("refuse/landing-with-heading", refused(
        "refuse/landing-with-heading", model,
        'path = "index.html"\ntitle = "T"\nkind = "landing"\nheading = "H"\n[[body]]\ncomponent = "about"\n') != "")
    expect("refuse/bad-path", "safe page path" in refused(
        "refuse/bad-path", model, 'path = "../x.html"\ntitle = "T"\nkind = "landing"\n[[body]]\ncomponent = "about"\n'))
    expect("refuse/rail-on-prose", "joins the rail" in refused(
        "refuse/rail-on-prose", fixture_page, '[[body]]\ncomponent = "prose"\nhtml = "<p>P</p>"\nrail = "P"\n'))
    expect("refuse/about-not-last", "closes a document page" in refused(
        "refuse/about-not-last", fixture_page,
        '[[body]]\ncomponent = "about"\n[[body]]\ncomponent = "prose"\nhtml = "<p>P</p>"\n'))
    expect("refuse/absolute-internal-href", "site-relative" in refused(
        "refuse/absolute-internal-href", rendered, 'component = "next"\ntext = "T"\nhref = "/draft/x/"\n'))
    expect("refuse/http-external-href", "site-relative" in refused(
        "refuse/http-external-href", rendered,
        'component = "download"\nhref = "http://example.com/x"\nfilename = "x"\nsize = "s"\nhtml = "D"\n'))
    expect("refuse/bad-code-variant", "variant" in refused(
        "refuse/bad-code-variant", rendered, 'component = "code"\nid = "x"\ntext = "t"\nvariant = "inline"\n'))
    expect("refuse/bad-id", "not a valid id" in refused(
        "refuse/bad-id", rendered, 'component = "clause"\nid = "1 bad"\nnum = "1"\ntitle = "T"\nlbl = "L"\n'))
    # Column classes are ASCII w0 to w99 only: a non-ASCII digit (TOML escapes, so the source stays ASCII),
    # a third digit or a trailing newline is refused, and both ends of the range render.
    for name, col in (("refuse/cols-arabic-indic-digit", "w\\u0661"), ("refuse/cols-fullwidth-digit", "w\\uff11"),
                      ("refuse/cols-three-digits", "w100"), ("refuse/cols-trailing-newline", "w1\\n")):
        expect(name, "wN column classes" in refused(
            name, rendered, 'component = "table"\ncaption = "C"\ncols = ["{}"]\nhead = ["A"]\nrows = [["x"]]\n'
            .format(col)))
    for name, col in (("component/table-cols-w0", "w0"), ("component/table-cols-w99", "w99")):
        cols_table = rendered('component = "table"\ncaption = "C"\ncols = ["{}"]\nhead = ["A"]\nrows = [["x"]]\n'
                              .format(col))
        expect(name, '<col class="{}">'.format(col) in cols_table, cols_table)

    # Scripts are derived from the content: rail.js only where the rail lists sections (section 7.6).
    plain = fixture_page('[[body]]\ncomponent = "prose"\nhtml = "<p>P</p>"\n')
    railed = fixture_page('[[body]]\ncomponent = "clause"\nid = "st-1"\nnum = "1"\ntitle = "T"\n'
                          'lbl = "Clause 1"\nrail = "T"\n')
    coded = fixture_page('[[body]]\ncomponent = "code"\nid = "x"\ntext = "t"\n')
    picked = fixture_page('[[body]]\ncomponent = "picker"\nreset = "R"\n[[body.items]]\nfamily = "claude"\n'
                          'label = "Claude"\n')
    expect("scripts/none-for-plain", _page_scripts(plain) == [], _page_scripts(plain))
    expect("scripts/rail-when-listed", _page_scripts(railed) == ["rail.js"], _page_scripts(railed))
    expect("scripts/copy-for-code", _page_scripts(coded) == ["copy.js"], _page_scripts(coded))
    expect("scripts/select-for-picker", _page_scripts(picked) == ["select.js"], _page_scripts(picked))
    railed_html = render_page(site, [railed], railed)
    plain_html = render_page(site, [plain], plain)
    expect("rail/script-loaded", '<script src="/draft/assets/baseline/rail.js" defer></script>' in railed_html)
    expect("rail/script-absent-without-sections", "rail.js" not in plain_html)
    expect("rail/nav-markup", ('<nav class="rail" aria-label="On this page"><img class="rail-logo" '
                               'src="/draft/assets/baseline/logos/aiqt.svg" alt="A shield bearing a check '
                               'mark, set on a guardrail"><p class="lbl">On this page</p>'
                               '<ol><li><a href="#st-1">T</a></li></ol></nav>') in railed_html, railed_html)
    expect("rail/logo-only-without-sections",
           '<div class="rail"><img class="rail-logo" src="/draft/assets/baseline/logos/aiqt.svg"' in plain_html)
    rail_nav = re.search(r'<nav class="rail".*?</nav>', railed_html)
    expect("rail/no-static-aria-current", rail_nav is not None and "aria-current" not in rail_nav.group(0))

    # Every link on a built page stays under BASE or is an https link with the new-tab attributes.
    railed_scripts = [_asset(site, "rail.js")]
    railed_scheme = _asset(site, SCHEME_SCRIPT)
    expect("links/clean-page-passes", _link_findings(site, railed_html, railed_scripts, railed_scheme) == [],
           _link_findings(site, railed_html, railed_scripts, railed_scheme))
    expect("links/internal-outside-base", _link_findings(site, '<a href="/live/">x</a>') != [])
    expect("links/non-https-external", _link_findings(site, '<a href="http://example.com/">x</a>') != [])
    expect("links/external-without-newtab", _link_findings(site, '<a href="https://example.com/">x</a>') != [])
    bad = fixture_page('[[body]]\ncomponent = "prose"\nhtml = "<a href=\\"/elsewhere/\\">x</a>"\n')
    try:
        render_page(site, [bad], bad)
        expect("links/bad-inline-refused", False)
    except RefusedPage:
        pass
    hostile = fixture_page('[[body]]\ncomponent = "prose"\nhtml = "<p onclick=\\"x()\\">x</p>"\n')
    try:
        build([hostile], sites=(site,))
        expect("refuse/build-raises", False)
    except RefusedPage:
        pass

    # The data directory fails closed: a stray entry, a symlink, an empty or missing directory refuse.
    with tempfile.TemporaryDirectory(prefix="gen-draft-site-selftest-") as tmp:
        src = Path(tmp) / "draft-src"
        os.makedirs(src)
        page_text = ('path = "index.html"\ntitle = "Home"\nkind = "document"\nlabel = "Draft"\n'
                     'heading = "H"\n[[body]]\ncomponent = "prose"\nhtml = "<p>P</p>"\n')
        with open(src / "home.toml", "w", encoding="utf-8") as handle:
            handle.write(page_text)
        expect("data/loads", [page["rel"] for page in _load_pages(src)] == ["index.html"])
        with open(src / "stray.txt", "w", encoding="utf-8") as handle:
            handle.write("x")
        expect("data/stray-refused", "not a .toml" in refused("data/stray-refused", _load_pages, src))
        os.unlink(src / "stray.txt")
        os.symlink(src / "home.toml", src / "link.toml")
        expect("data/symlink-refused", "not a regular file" in refused("data/symlink-refused", _load_pages, src))
        os.unlink(src / "link.toml")
        with open(src / "second.toml", "w", encoding="utf-8") as handle:
            handle.write(page_text)
        expect("data/duplicate-path-refused", "duplicate page path" in refused(
            "data/duplicate-path-refused", _load_pages, src))
        os.unlink(src / "second.toml")
        with open(src / "bad.toml", "w", encoding="utf-8") as handle:
            handle.write("path = [unterminated\n")
        expect("data/bad-toml-refused", "cannot parse" in refused("data/bad-toml-refused", _load_pages, src))
        os.unlink(src / "bad.toml")
        os.unlink(src / "home.toml")
        expect("data/empty-refused", "no page data files" in refused("data/empty-refused", _load_pages, src))
        expect("data/missing-refused", "cannot list" in refused(
            "data/missing-refused", _load_pages, Path(tmp) / "absent"))
        # The size bound at its ends: one byte under, exactly at, and one byte over MAX_PAGE_BYTES.
        for name, extra, over in (("data/size-under", -1, False), ("data/size-at", 0, False),
                                  ("data/size-over", 1, True)):
            pad = MAX_PAGE_BYTES - len(page_text.encode("utf-8")) + extra
            body = page_text + "#" + "x" * (pad - 2) + "\n"
            with open(src / "home.toml", "w", encoding="utf-8") as handle:
                handle.write(body)
            got = len(body.encode("utf-8")) == MAX_PAGE_BYTES + extra
            try:
                _load_pages(src)
                refused_run = False
            except RefusedPage as exc:
                refused_run = "larger than" in str(exc)
            expect(name, got and refused_run == over, (len(body), refused_run))
        os.unlink(src / "home.toml")

    # Every kept live path has its target under site/ today; a kept entry with no target refuses the
    # build before anything renders.
    expect("kept/all-targets-exist", all(_kept_live_missing(each) == [] for each in SITES),
           [_kept_live_missing(each) for each in SITES])
    absent = dict(site, kept=site["kept"] + ("/absent-page",))
    expect("kept/missing-detected", _kept_live_missing(absent) == ["/absent-page"], _kept_live_missing(absent))
    try:
        build([plain], sites=(absent,))
        expect("refuse/kept-missing-build", False)
    except RefusedPage as exc:
        expect("refuse/kept-missing-build", "kept live path" in str(exc), str(exc))
    try:
        build([plain], sites=(site, dict(staged_[1], root=site["root"])))
        expect("refuse/shared-output-path", False)
    except RefusedPage as exc:
        expect("refuse/shared-output-path", "more than 1 site" in str(exc), str(exc))

    # Promotion is per site: 1 site rebuilt at "/" while the other stays under /draft/.
    promoted_set = build(sites=(dict(site, base="/", assets="/"), staged_[1]), registered=False)
    promoted = promoted_set["site/draft/index.html"]
    expect("base/promotion-drops-robots", "noindex" not in promoted)
    expect("base/promotion-root-paths", 'href="/assets/baseline/fonts.css"' in promoted
           and "/draft/" not in promoted)
    still = promoted_set["opf/site/draft/index.html"]
    expect("base/other-site-stays-draft", still == outputs["opf/site/draft/index.html"]
           and 'href="/draft/assets/baseline/fonts.css"' in still and "noindex" in still)
    # The declared build: both sites at "/", their pages at the live roots, the asset URLs still under
    # /draft/assets/baseline/ (the asset base stays where the files are served), the robots meta gone from
    # every page but the /draft/ placeholder.
    expect("base/live-targets-at-root", sorted(live_outputs) == sorted(e["target"] for e in GENSRC_OUTPUTS)
           and all("/draft/" not in rel or rel.endswith("/draft/index.html") for rel in live_outputs),
           sorted(live_outputs))
    for rel, text in sorted(live_outputs.items()):
        placeholder = rel.endswith("/draft/index.html")
        expect("base/live-robots " + rel, ("noindex" in text) == placeholder)
        expect("base/live-assets-stay " + rel, '<link rel="stylesheet" href="/draft/assets/baseline/fonts.css">'
               in text and 'href="/assets/baseline/' not in text)
        expect("base/live-page-urls-at-root " + rel, re.search(r'href="/draft/(?!assets/baseline/)', text)
               is None or placeholder)
    # Failing on purpose: a promoted page under draft/ with its robots meta dropped is caught by the check
    # above, and so is a page outside draft/ that kept it.
    flipped = dict(live_outputs)
    flipped["site/draft/index.html"] = flipped["site/draft/index.html"].replace(
        '<meta name="robots" content="noindex, nofollow">\n', "", 1)
    flipped["site/install/index.html"] = flipped["site/install/index.html"].replace(
        "<title>", '<meta name="robots" content="noindex, nofollow">\n<title>', 1)
    expect("base/live-robots-flip-caught", [rel for rel, text in sorted(flipped.items())
                                            if ("noindex" in text) != rel.endswith("/draft/index.html")]
           == ["site/draft/index.html", "site/install/index.html"])
    expect("base/live-placeholder-home-link", '<a href="/">home page</a>' in live_outputs["site/draft/index.html"]
           and '<a href="/">home page</a>' in live_outputs["opf/site/draft/index.html"])

    # opfiles.ai: its own page set, chrome, footer cycle and nav, with no control strip.
    opf = staged_[1]
    home = outputs["opf/site/draft/index.html"]
    expect("opfiles/outputs", sorted(rel for rel in outputs if rel.startswith("opf/site/draft/")) == [
        "opf/site/draft/404.html", "opf/site/draft/adopt/index.html", "opf/site/draft/core-files/index.html",
        "opf/site/draft/disclosure/index.html", "opf/site/draft/draft/index.html", "opf/site/draft/index.html",
        "opf/site/draft/license/index.html", "opf/site/draft/model/index.html",
        "opf/site/draft/standard/index.html"], sorted(outputs))
    expect("opfiles/html-class", '<html lang="en" class="site-opfiles">' in home)
    expect("opfiles/title", "<title>Home | OPFiles.ai</title>" in home)
    expect("opfiles/favicon", '<link rel="icon" href="/draft/assets/baseline/logos/opfiles-favicon.svg"' in home)
    expect("opfiles/touch-icon",
           '<link rel="apple-touch-icon" href="/draft/assets/baseline/logos/opfiles-touch.png">' in home)
    opf_slices = re.findall(r'<li class="slice slice-(\d)">', home)
    expect("opfiles/footer-cycle", opf_slices == ["2", "3", "4", "5", "6", "1"], opf_slices)
    expect("opfiles/footer-current-card",
           home.count('aria-current="page"><img src="/draft/assets/baseline/logos/opfiles.svg"') == 1)
    opf_nav = re.search(r'<nav class="nav" aria-label="Site">(.*?)</nav>',
                        outputs["opf/site/draft/model/index.html"]).group(1)
    expect("opfiles/nav", opf_nav.startswith(
        '<a href="/draft/model/" aria-current="page">The model</a><a href="/draft/standard/">The standard</a>'
        '<a href="/draft/core-files/">Core files</a><a href="/draft/adopt/">Adopt</a>'
        '<a href="https://github.com/jposluns/guardrails" target="_blank"'), opf_nav)
    expect("opfiles/nav-order", [label for _path, label in opf["nav"]] == [
        "The model", "The standard", "Core files", "Adopt"], opf["nav"])
    expect("opfiles/about", home.count(_about(opf)) == 1)
    expect("opfiles/no-control-strip", all('class="control"' not in outputs[rel] for rel in outputs
                                           if rel.startswith("opf/")))
    model_page = outputs["opf/site/draft/model/index.html"]
    expect("opfiles/model-figure", model_page.count('<figure class="figure" id="fig-1">') == 1
           and '<p class="lbl blk">Model</p>' in model_page and "<figcaption>Figure 1. " in model_page)
    expect("opfiles/model-srcview", model_page.count('<div class="srcview">') == 1
           and '<script src="/draft/assets/baseline/copy.js" defer></script>' in model_page)
    expect("opfiles/standard-list", '<ul class="plan three"><li>Store layout and naming</li>'
           in outputs["opf/site/draft/standard/index.html"])
    expect("refuse/control-off-aiqt", "aiqt.ai only" in refused(
        "refuse/control-off-aiqt", _c_control, opf,
        {"component": "control", "document": "d", "source_text": "s", "source_href": "https://example.com/"},
        "fixture.body[1]"))

    # The 3 section 7.18 and 7.13 components added for opfiles.ai, each pinned exactly.
    plain_list = rendered('component = "list"\nvariant = "three"\nitems = ["A", "B <code>c</code>"]\n')
    expect("component/list-three", plain_list == '<ul class="plan three"><li>A</li><li>B <code>c</code></li></ul>',
           plain_list)
    expect("component/list-default", rendered('component = "list"\nitems = ["A"]\n') == '<ul class="plan"><li>A</li></ul>')
    expect("refuse/list-empty-item", "non-empty strings" in refused(
        "refuse/list-empty-item", rendered, 'component = "list"\nitems = ["A", ""]\n'))
    expect("refuse/list-bad-variant", "variant" in refused(
        "refuse/list-bad-variant", rendered, 'component = "list"\nvariant = "four"\nitems = ["A"]\n'))
    srcview = rendered('component = "srcview"\nid = "src"\nsource_label = "a.toml"\ntext = "x = \\"<1>\\""\n'
                       'view_label = "A.md"\ngen = "Generated."\nheading = "A"\nnote = "N."\n'
                       '[[body.items]]\nhtml = "<code>BI-1</code>"\n')
    expect("component/srcview", srcview == (
        '<div class="srcview"><div><p class="lbl">a.toml</p><pre id="src" class="hascopy"><button class="copy" '
        'type="button" data-copy="src" hidden>Copy</button>x = "&lt;1&gt;"</pre></div><div><p class="lbl">A.md'
        '</p><div class="view"><p class="gen">Generated.</p><h4>A</h4><ul><li><code>BI-1</code></li></ul></div>'
        '</div></div>\n<p class="code-note">N.</p>'), srcview)
    figure = rendered('component = "figure"\nid = "f"\ncaption = "Figure 1. C."\nunder = "u &"\n'
                      '[[body.items]]\nt = "A"\nm = "a.toml"\ns = "s1"\nedge = "go"\n'
                      '[[body.items]]\nt = "B"\nm = "B.md"\ns = "s2"\n')
    expect("component/figure", figure == (
        '<figure class="figure" id="f"><svg class="wide" viewBox="0 0 540 160" role="img" '
        'aria-label="A, a.toml, s1; go; B, B.md, s2. U &amp;.">'
        '<rect class="node" x="10" y="20" width="210" height="88" rx="6"></rect>'
        '<text class="t" x="24" y="46">A</text><text class="m" x="24" y="70">a.toml</text>'
        '<text class="s" x="24" y="90">s1</text>'
        '<line class="arrow" x1="224" y1="64" x2="310" y2="64"></line>'
        '<polygon class="head" points="310,58 320,64 310,70"></polygon>'
        '<text class="t accent" x="270" y="54" text-anchor="middle">go</text>'
        '<rect class="node" x="320" y="20" width="210" height="88" rx="6"></rect>'
        '<text class="t" x="334" y="46">B</text><text class="m" x="334" y="70">B.md</text>'
        '<text class="s" x="334" y="90">s2</text>'
        '<text class="s" x="270" y="140" text-anchor="middle">u &amp;</text></svg>'
        '<svg class="stack" viewBox="0 0 320 276" role="img" aria-label="A, a.toml, s1; go; B, B.md, s2. U &amp;.">'
        '<rect class="node" x="20" y="10" width="280" height="88" rx="6"></rect>'
        '<text class="t" x="34" y="36">A</text><text class="m" x="34" y="60">a.toml</text>'
        '<text class="s" x="34" y="80">s1</text>'
        '<line class="arrow" x1="160" y1="102" x2="160" y2="140"></line>'
        '<polygon class="head" points="154,140 160,150 166,140"></polygon>'
        '<text class="t accent" x="176" y="128">go</text>'
        '<rect class="node" x="20" y="150" width="280" height="88" rx="6"></rect>'
        '<text class="t" x="34" y="176">B</text><text class="m" x="34" y="200">B.md</text>'
        '<text class="s" x="34" y="220">s2</text>'
        '<text class="s" x="160" y="260" text-anchor="middle">u &amp;</text></svg>'
        '<figcaption>Figure 1. C.</figcaption></figure>'), figure)
    expect("refuse/figure-one-node", "2 to 4 nodes" in refused(
        "refuse/figure-one-node", rendered, 'component = "figure"\nid = "f"\ncaption = "C"\nunder = "u"\n'
        '[[body.items]]\nt = "A"\nm = "a"\ns = "s"\n'))
    # The node count at its upper end: 4 nodes render, 5 are refused (2, the lower end, renders above).
    four = ('component = "figure"\nid = "f"\ncaption = "C"\nunder = "u"\n'
            + '[[body.items]]\nt = "A"\nm = "a"\ns = "s"\nedge = "e"\n' * 3 + '[[body.items]]\nt = "B"\nm = "b"\ns = "s"\n')
    expect("component/figure-four-nodes", rendered(four).count('<rect class="node"') == 8)
    expect("refuse/figure-five-nodes", "2 to 4 nodes" in refused(
        "refuse/figure-five-nodes", rendered, four.replace('[[body.items]]\nt = "B"',
                                                           '[[body.items]]\nt = "A"\nm = "a"\ns = "s"\nedge = "e"\n'
                                                           '[[body.items]]\nt = "B"', 1)))
    expect("refuse/figure-last-edge", "edge label" in refused(
        "refuse/figure-last-edge", rendered, 'component = "figure"\nid = "f"\ncaption = "C"\nunder = "u"\n'
        '[[body.items]]\nt = "A"\nm = "a"\ns = "s"\nedge = "e"\n[[body.items]]\nt = "B"\nm = "b"\ns = "s"\n'
        'edge = "e"\n'))
    expect("figure/lines-at-limit", _fig_lines("aa bb", 5) == ["aa bb"] and _fig_lines("aa bbb", 5) == ["aa", "bbb"]
           and _fig_lines("aa b", 5) == ["aa b"], (_fig_lines("aa bb", 5), _fig_lines("aa bbb", 5)))

    # The 5 components the opfiles.ai core files, adopt, disclosure and licence pages use, each pinned exactly.
    pull = rendered('component = "pullquote"\nhtml = "Declare it <em>once</em>."\n')
    expect("component/pullquote", pull == '<blockquote><p>Declare it <em>once</em>.</p></blockquote>', pull)
    record = rendered('component = "record"\n[[body.items]]\nterm = "Declares"\nhtml = "<code>[opf]</code>"\n'
                      '[[body.items]]\nterm = "Read by"\nhtml = "R"\n')
    expect("component/record", record == (
        '<dl class="record"><div><dt>Declares</dt><dd><code>[opf]</code></dd></div>'
        '<div><dt>Read by</dt><dd>R</dd></div></dl>'), record)
    record_cols = rendered('component = "record"\ncols = true\n[[body.items]]\nterm = "T"\nhtml = "V"\n')
    expect("component/record-cols", record_cols == '<dl class="record cols"><div><dt>T</dt><dd>V</dd></div></dl>',
           record_cols)
    expect("refuse/record-missing-term", "missing key" in refused(
        "refuse/record-missing-term", rendered, 'component = "record"\n[[body.items]]\nhtml = "V"\n'))
    filed = rendered('component = "code"\nid = "m"\nfile = ".working/<toml>/m.toml"\ntext = "t"\n')
    expect("component/code-file-label", filed == (
        '<p class="lbl file">.working/&lt;toml&gt;/m.toml</p>\n<pre id="m" class="hascopy"><button class="copy" '
        'type="button" data-copy="m" hidden>Copy</button>t</pre>'), filed)
    subhead = rendered('component = "subhead"\nid = "done"\nheading = "Completion <code>receipts</code>"\n')
    expect("component/subhead", subhead == '<h3 id="done">Completion <code>receipts</code></h3>', subhead)
    expect("component/subhead-no-id", rendered('component = "subhead"\nheading = "H"\n') == "<h3>H</h3>")
    expect("refuse/subhead-bad-id", "not a valid id" in refused(
        "refuse/subhead-bad-id", rendered, 'component = "subhead"\nid = "9 bad"\nheading = "H"\n'))
    expect("refuse/duplicate-id", "duplicate id" in refused(
        "refuse/duplicate-id", fixture_page, '[[body]]\ncomponent = "subhead"\nid = "done"\nheading = "A"\n'
        '[[body]]\ncomponent = "section"\nlbl = "L"\nid = "done"\nheading = "B"\n'))
    steps = rendered('component = "steps"\nitems = ["Give it <code>/llms.txt</code>.", "Review."]\n')
    expect("component/steps", steps == (
        '<ol class="steps"><li><span>Give it <code>/llms.txt</code>.</span></li><li><span>Review.</span></li></ol>'),
        steps)
    expect("refuse/steps-empty-item", "non-empty strings" in refused(
        "refuse/steps-empty-item", rendered, 'component = "steps"\nitems = ["A", ""]\n'))

    # Text fields are escaped for their context: each carries "<q>&" and must render "&lt;q&gt;&amp;", never
    # the raw tag; inline HTML fields (the documented raw set) are unchanged above.
    raw, esc = "<q>&", "&lt;q&gt;&amp;"
    for name, toml, want, kind in (
            ("escape/hero-start-text", 'component = "hero"\nkicker = "K"\nstatement = "S"\nintro = "I"\n'
             'start_text = "X"\nstart_href = ""\n', '/draft/">{} <span'.format(esc), "landing"),
            ("escape/entries-label", 'component = "entries"\nlabel = "X"\n[[body.items]]\nhref = ""\ntitle = "T"\n'
             'n = "n"\nd = "d"\n', '<p class="lbl">{}</p>'.format(esc), "landing"),
            ("escape/region-label", 'component = "region"\nlabel = "X"\nhtml = "H"\n',
             '<p class="lbl">{}</p>'.format(esc), "landing"),
            ("escape/value-label", 'component = "value"\nlabel = "X"\nhtml = "H"\n',
             '<p class="lbl">{}</p>'.format(esc), "landing"),
            ("escape/control-document", 'component = "control"\ndocument = "X"\nsource_text = "s"\n'
             'source_href = "https://example.com/"\n', "<dd>{}</dd>".format(esc), "document"),
            ("escape/control-source-text", 'component = "control"\ndocument = "d"\nsource_text = "X"\n'
             'source_href = "https://example.com/"\n', 'noreferrer">{}<span'.format(esc), "document"),
            ("escape/control-licence-text", 'component = "control"\ndocument = "d"\nsource_text = "s"\n'
             'source_href = "https://example.com/"\nlicence_text = "X"\n', '/license/">{}</a>'.format(esc),
             "document"),
            ("escape/control-source-href", 'component = "control"\ndocument = "d"\nsource_text = "s"\n'
             'source_href = "https://example.com/?a=1&b=\\"x"\n',
             'href="https://example.com/?a=1&amp;b=&quot;x"', "document"),
            ("escape/clause-lbl-num", 'component = "clause"\nid = "c"\nnum = "X"\ntitle = "T"\nlbl = "X"\n',
             '<p class="lbl sec">{0}</p>\n<h2 class="clause" id="c"><span class="num">{0}</span>'.format(esc),
             "document"),
            ("escape/clauses-n", 'component = "clauses"\n[[body.items]]\nn = "X"\nhtml = "H"\n',
             '<span class="n">{}</span>'.format(esc), "document"),
            ("escape/note-label", 'component = "note"\nlabel = "X"\nhtml = "H"\n',
             '<span class="label">{}</span>'.format(esc), "document"),
            ("escape/callout-label", 'component = "callout"\nlabel = "X"\nhtml = "H"\n',
             '<p class="lbl accent blk">{}</p>'.format(esc), "document"),
            ("escape/figures-num-cap", 'component = "figures"\n[[body.items]]\nnum = "X"\ncap = "X"\n',
             '<span class="num">{0}</span><span class="cap">{0}</span>'.format(esc), "document"),
            ("escape/table-caption", 'component = "table"\ncaption = "X"\nhead = ["A"]\nrows = [["x"]]\n',
             '<caption class="vh">{}</caption>'.format(esc), "document"),
            ("escape/register-fields", 'component = "register"\nreg = "X"\ntitle = "X"\ncount = "X"\n'
             'caption = "C"\nhead = ["A"]\nrows = [["x"]]\n',
             '<b>{0}</b><span>{0}</span><span class="count">{0}</span>'.format(esc), "document"),
            ("escape/picker-label-reset", 'component = "picker"\nreset = "X"\n[[body.items]]\nfamily = "f"\n'
             'label = "X"\n', 'data-family-link="f">{0}</a></li></ul><button class="btn picker-reset" '
             'type="button" data-picker-reset hidden>{0}</button>'.format(esc), "document"),
            ("escape/family-lbl", 'component = "family"\nfamily = "f"\nlbl = "X"\nheading = "H"\nverified = "V"\n',
             '<p class="lbl sec">{}</p>'.format(esc), "document"),
            ("escape/download-fields", 'component = "download"\nlabel = "X"\nhref = "https://example.com/x"\n'
             'filename = "X"\nsize = "X"\nhtml = "D"\n', '<p class="lbl blk">{0}</p>\n<div class="download">'
             '<p class="file"><a href="https://example.com/x" target="_blank" rel="noopener noreferrer">{0}'
             '<span class="vh"> (opens in a new tab)</span></a></p><p class="lbl size">{0}</p>'.format(esc),
             "document"),
            ("escape/next-text", 'component = "next"\ntext = "X"\nhref = ""\n', '/draft/">{} <span'.format(esc),
             "document"),
            ("escape/label-text", 'component = "label"\ntext = "X"\n', '<p class="lbl blk">{}</p>'.format(esc),
             "document"),
            ("escape/section-lbl", 'component = "section"\nlbl = "X"\nheading = "H"\n',
             '<p class="lbl sec">{}</p>'.format(esc), "document"),
            ("escape/entry-lbl", 'component = "entry"\nlbl = "X"\nid = "e"\nheading = "H"\nclaim = "C"\n'
             'limitation = "L"\nevidence = "E"\n', '<p class="lbl sec">{}</p>'.format(esc), "document"),
            ("escape/record-term", 'component = "record"\n[[body.items]]\nterm = "X"\nhtml = "H"\n',
             "<dt>{}</dt>".format(esc), "document")):
        got = rendered(toml.replace('"X"', '"{}"'.format(raw)), kind=kind)
        expect(name, want in got and "<q>" not in got, got)
    escaped_page = model('path = "index.html"\ntitle = "{0}"\nkind = "document"\nlabel = "{0}"\nheading = "H"\n'
                         '[[body]]\ncomponent = "section"\nlbl = "L"\nid = "s"\nheading = "H"\nrail = "{0}"\n'
                         .format(raw))
    escaped_html = render_page(site, [escaped_page], escaped_page)
    expect("escape/page-title", "<title>{} | AIQT.ai</title>".format(esc) in escaped_html, escaped_html)
    expect("escape/page-label", '<p class="lbl first">{}</p>'.format(esc) in escaped_html, escaped_html)
    expect("escape/rail-label", '<li><a href="#s">{}</a></li>'.format(esc) in escaped_html, escaped_html)
    expect("escape/no-raw-tag", "<q>" not in escaped_html, escaped_html)
    expect("escape/external-href", _external('https://example.com/?a="1"&b', "L").startswith(
        '<a href="https://example.com/?a=&quot;1&quot;&amp;b" '), _external('https://example.com/?a="1"&b', "L"))

    # The page keys title and meta go through the checked reader: a wrong type or an empty string is a
    # named refusal, never a traceback or a rendered page; a present meta renders.
    page_head = 'path = "index.html"\nkind = "document"\nlabel = "L"\nheading = "H"\n'
    page_body = '[[body]]\ncomponent = "prose"\nhtml = "<p>P</p>"\n'
    for name, line, key in (("refuse/title-not-string", "title = 3\n", "title"),
                            ("refuse/title-empty", 'title = ""\n', "title"),
                            ("refuse/title-table", "title = {a = 1}\n", "title"),
                            ("refuse/meta-not-string", 'title = "T"\nmeta = ["m"]\n', "meta"),
                            ("refuse/meta-empty", 'title = "T"\nmeta = ""\n', "meta"),
                            ("refuse/meta-boolean", 'title = "T"\nmeta = true\n', "meta")):
        expect(name, "{} must be a non-empty string".format(key) in refused(
            name, model, page_head + line + page_body))
    metad = model(page_head + 'title = "T"\nmeta = "M <code>c</code>"\n' + page_body)
    expect("page/meta-renders", '<p class="meta">M <code>c</code></p>' in render_page(site, [metad], metad))
    # The description metas carry the meta escaped for a double-quoted attribute: the markup reduced to its
    # text, character references decoded and escaped again, no raw quote, the base token rendered, and
    # whitespace runs folded; each sits on its own head line, after the robots meta and before the title.
    quoted = model(page_head + 'title = "T"\nmeta = """Say "a" &amp; &lt;b&gt; it\'s\n<code>c</code> $BASE/x"""\n'
                   + page_body)
    quoted_lines = render_page(site, [quoted], quoted).split("</head>", 1)[0].split("\n")
    content = "Say &quot;a&quot; &amp; &lt;b&gt; it&#x27;s c /draft/x"
    escaped_metas = ['<meta name="description" content="{}">'.format(content),
                     '<meta property="og:description" content="{}">'.format(content)]
    expect("meta/description-escaped", all(quoted_lines.count(meta) == 1 for meta in escaped_metas), quoted_lines)
    positions = [quoted_lines.index(line) if line in quoted_lines else -1 for line in
                 ['<meta name="robots" content="noindex, nofollow">'] + escaped_metas + ["<title>T | AIQT.ai</title>"]]
    expect("meta/description-order", -1 not in positions and positions == list(range(positions[0], positions[0] + 4)),
           positions)
    plain = model(page_head + 'title = "T"\n' + page_body)
    expect("meta/description-none-without-meta", "description" not in render_page(site, [plain], plain))
    with tempfile.TemporaryDirectory(prefix="gen-draft-site-selftest-") as tmp:
        with open(Path(tmp) / "home.toml", "w", encoding="utf-8") as handle:
            handle.write(page_head + "title = 3\n" + page_body)
        expect("refuse/title-from-data-dir", "title must be a non-empty string" in refused(
            "refuse/title-from-data-dir", _load_pages, Path(tmp)))

    # The link guard is structural: every element and attribute is seen whatever its case or quoting.
    for name, text in (
            ("guard/single-quoted-href", "<a href='/elsewhere/'>x</a>"),
            ("guard/unquoted-href", "<a href=/elsewhere/>x</a>"),
            ("guard/uppercase-href", '<A HREF="/elsewhere/">x</A>'),
            ("guard/javascript-href", '<a href="javascript:x()">x</a>'),
            ("guard/protocol-relative", '<a href="//example.com/">x</a>'),
            ("guard/backslash", '<a href="/draft/\\example.com">x</a>'),
            ("guard/dot-dot-segment", '<a href="/draft/../x">x</a>'),
            ("guard/encoded-dot-dot-lower", '<a href="/draft/%2e%2e/x">x</a>'),
            ("guard/encoded-dot-dot-upper", '<a href="/draft/%2E%2E/x">x</a>'),
            ("guard/encoded-dot-dot-second", '<a href="/draft/.%2e/x">x</a>'),
            ("guard/encoded-dot-dot-first", '<a href="/draft/%2e./x">x</a>'),
            ("guard/encoded-dot-dot-lower-query", '<a href="/draft/%2e%2e?q=1">x</a>'),
            ("guard/encoded-dot-dot-upper-query", '<a href="/draft/%2E%2E?q=1">x</a>'),
            ("guard/encoded-dot-dot-second-query", '<a href="/draft/.%2e?q=1">x</a>'),
            ("guard/encoded-dot-dot-first-query", '<a href="/draft/%2e.?q=1">x</a>'),
            ("guard/encoded-dot-dot-lower-fragment", '<a href="/draft/%2e%2e#top">x</a>'),
            ("guard/encoded-dot-dot-upper-fragment", '<a href="/draft/%2E%2E#top">x</a>'),
            ("guard/encoded-dot-dot-second-fragment", '<a href="/draft/.%2e#top">x</a>'),
            ("guard/encoded-dot-dot-first-fragment", '<a href="/draft/%2e.#top">x</a>'),
            ("guard/whitespace-in-url", '<a href=" /draft/x">x</a>'),
            ("guard/img-src", '<img src="http://example.com/x.png" alt="">'),
            ("guard/srcset", '<img src="/draft/x.png" srcset="/draft/x.png 1x" alt="">'),
            ("guard/form-action", '<form action="/draft/x"></form>'),
            ("guard/button-formaction", '<button formaction="http://example.com/">x</button>'),
            ("guard/meta-refresh", '<meta http-equiv="refresh" content="0; url=https://example.com/">'),
            ("guard/meta-refresh-uppercase", '<META HTTP-EQUIV=refresh CONTENT="0">'),
            ("guard/base", '<base href="https://example.com/">'),
            ("guard/script-not-shell", '<script src="/draft/assets/baseline/rail.js" defer></script>'),
            ("guard/iframe", '<iframe src="https://example.com/"></iframe>'),
            ("guard/object", '<object data="/draft/x"></object>'),
            ("guard/embed", '<embed src="/draft/x">'),
            ("guard/svg-animate", '<svg><animate attributeName="href" to="javascript:x()"></animate></svg>'),
            ("guard/svg-xlink-href", '<svg><a xlink:href="javascript:x()">x</a></svg>'),
            ("guard/external-no-target", '<a href="https://example.com/" rel="noopener noreferrer">x'
             '<span class="vh"> (opens in a new tab)</span></a>'),
            ("guard/external-no-rel", '<a href="https://example.com/" target="_blank">x'
             '<span class="vh"> (opens in a new tab)</span></a>'),
            ("guard/external-no-hint", '<a href="https://example.com/" target="_blank" '
             'rel="noopener noreferrer">x</a>'),
            ("guard/external-single-quoted", "<a href='https://example.com/'>x</a>"),
            ("guard/area-no-hint", '<area href="https://example.com/" target="_blank" rel="noopener noreferrer">')):
        expect(name, _link_findings(site, text) != [], text)
    for name, text in (
            ("guard/external-uppercase-passes", '<A HREF="https://example.com/" TARGET="_blank" '
             "REL='noopener noreferrer'>x<span class=\"vh\"> (opens in a new tab)</span></A>"),
            ("guard/aria-label-hint-passes", '<a href="https://example.com/" target="_blank" '
             'rel="noopener noreferrer" aria-label="Example (opens in a new tab)">x</a>'),
            ("guard/fragment-and-base-pass", '<a href="#top">x</a><img src="/draft/x.png" alt="">'),
            ("guard/meta-name-passes", '<meta name="robots" content="noindex, nofollow">'),
            ("guard/plain-path-passes", '<a href="/draft/install/">x</a>'),
            ("guard/encoded-non-dot-segment-passes", '<a href="/draft/a%2eb/">x</a>')):
        expect(name, _link_findings(site, text) == [], (text, _link_findings(site, text)))
    shell_src = _asset(site, "rail.js")
    expect("guard/shell-script-passes", _link_findings(
        site, '<script src="{}" defer></script>'.format(shell_src), [shell_src]) == [])
    expect("guard/shell-script-once", _link_findings(
        site, '<script src="{0}" defer></script><script src="{0}" defer></script>'.format(shell_src),
        [shell_src]) != [])
    expect("guard/shell-script-extra-attr", _link_findings(
        site, '<script src="{}" defer async></script>'.format(shell_src), [shell_src]) != [])
    # The head's scheme script passes only in its exact form: data-cfasync="false" then the shell's src,
    # once, with no other attribute; each near miss is refused.
    scheme_src = _asset(site, SCHEME_SCRIPT)
    expect("guard/scheme-script-passes", _link_findings(
        site, '<script data-cfasync="false" src="{}"></script>'.format(scheme_src), (), scheme_src) == [])
    for name, text in (
            ("guard/scheme-script-once", '<script data-cfasync="false" src="{0}"></script>'
             '<script data-cfasync="false" src="{0}"></script>'),
            ("guard/scheme-script-async", '<script data-cfasync="false" src="{0}" async></script>'),
            ("guard/scheme-script-defer", '<script data-cfasync="false" src="{0}" defer></script>'),
            ("guard/scheme-src-before-cfasync", '<script src="{0}" data-cfasync="false"></script>'),
            ("guard/scheme-cfasync-not-false", '<script data-cfasync="true" src="{0}"></script>'),
            ("guard/scheme-cfasync-missing", '<script src="{0}"></script>'),
            ("guard/scheme-cross-origin", '<script data-cfasync="false" src="https://example.com/scheme.js">'
             '</script>'),
            ("guard/scheme-other-asset", '<script data-cfasync="false" src="{1}"></script>')):
        expect(name, _link_findings(site, text.format(scheme_src, shell_src), (), scheme_src) != [], text)
    expect("guard/scheme-script-not-shell", _link_findings(
        site, '<script data-cfasync="false" src="{}"></script>'.format(scheme_src)) != [])
    expect("guard/coded-page-passes", "copy.js" in render_page(site, [coded], coded))
    for name, html in (("guard/inline-single-quoted-refused", "<a href='/elsewhere/'>x</a>"),
                       ("guard/inline-iframe-refused", '<iframe src=\\"https://example.com/\\"></iframe>'),
                       ("guard/inline-form-refused", '<form action=\\"https://example.com/\\"></form>')):
        hostile_page = fixture_page('[[body]]\ncomponent = "prose"\nhtml = "{}"\n'.format(html))
        expect(name, refused(name, render_page, site, [hostile_page], hostile_page) != "")

    # opfiles.ai keeps its own live paths: rendered root-relative with no new-tab hint on that site only.
    opf_kept = COMPONENTS["next"](opf, {"component": "next", "text": "Guide", "href": "/adopt.md"}, "fixture.body[1]")
    expect("links/opfiles-kept-plain", opf_kept == (
        '<div class="next"><p class="lbl">Next</p><a class="start" href="/adopt.md">Guide '
        '<span class="arrow" aria-hidden="true">&rarr;</span></a></div>'), opf_kept)
    expect("links/opfiles-kept-not-aiqt", _link_findings(site, '<a href="/llms.txt">x</a>') != []
           and _link_findings(opf, '<a href="/llms.txt">x</a>') == [])
    expect("refuse/opfiles-kept-prefix", "kept live path" in refused(
        "refuse/opfiles-kept-prefix", COMPONENTS["next"], opf,
        {"component": "next", "text": "T", "href": "/llms.txt2"}, "fixture.body[1]"))
    adopt_page = outputs["opf/site/draft/adopt/index.html"]
    expect("opfiles/adopt-kept-links", '<a href="/llms.txt"><code>/llms.txt</code></a>' in adopt_page
           and '<a href="/adopt.md"><code>/adopt.md</code></a>' in adopt_page
           and "https://opfiles.ai/" not in adopt_page and '<ol class="steps">' in adopt_page)
    core_page = outputs["opf/site/draft/core-files/index.html"]
    expect("opfiles/core-files-components", '<dl class="record">' in core_page and "<blockquote>" in core_page
           and '<p class="lbl file">.working/toml/manifest.toml</p>' in core_page
           and '<h3 id="done">' in core_page)

    # Link resolution and stray outputs, staged (base /draft/, the declared build) and promoted (the same
    # pages built at base "/" into each site's live root, in a fixture tree holding the asset folder and the
    # redirects files at their promoted places).
    expect("links/resolve-staged-clean", unresolved_links(outputs, staged_) == [],
           unresolved_links(outputs, staged_))
    promoted_sites_ = promoted_sites(staged_)
    promoted = build(sites=promoted_sites_, registered=False)
    expect("links/promoted-targets-at-live-root", sorted(promoted) == sorted(
        entry["target"] for entry in GENSRC_OUTPUTS) and promoted == live_outputs, sorted(promoted))
    expect("links/resolve-live-clean", unresolved_links(live_outputs) == [], unresolved_links(live_outputs))
    staged_home, opf_home = "site/draft/index.html", "opf/site/draft/index.html"

    def with_link(built, target, markup):
        changed = dict(built)
        changed[target] = changed[target].replace("</main>", markup + "</main>", 1)
        return changed

    for name, target, markup, want in (
            ("links/staged-missing-page-flagged", staged_home, '<a href="/draft/absent/">x</a>', 1),
            ("links/staged-missing-asset-flagged", staged_home, '<img src="/draft/assets/baseline/absent.svg">', 1),
            ("links/staged-asset-folder-itself-flagged", staged_home, '<a href="/draft/assets/baseline/">x</a>', 1),
            ("links/staged-live-only-path-flagged", staged_home, '<a href="/install">x</a>', 1),
            ("links/staged-other-site-page-flagged", opf_home, '<a href="/draft/install/">x</a>', 1),
            ("links/staged-promoted-url-flagged", staged_home, '<a href="/install/">x</a>', 1),
            ("links/staged-page-with-fragment-passes", staged_home, '<a href="/draft/install/#x">x</a>', 0),
            ("links/staged-redirect-source-passes", staged_home, '<a href="/rule1">x</a>', 0),
            ("links/staged-kept-live-passes", staged_home, '<a href="/development">x</a>', 0),
            ("links/staged-protocol-relative-not-root-relative", staged_home, '<a href="//example.com/">x</a>', 0),
            ("links/staged-asset-file-passes", staged_home, '<img src="/draft/assets/baseline/baseline.css">', 0),
            ("links/staged-asset-absolute-remainder-flagged", staged_home,
             '<img src="/draft/assets/baseline//etc/passwd">', 1),
            ("links/staged-asset-dot-segments-to-tool-flagged", staged_home,
             '<img src="/draft/assets/baseline/../../../../tools/gen_draft_site.py">', 1),
            ("links/staged-asset-dot-segments-to-readme-flagged", staged_home,
             '<img src="/draft/assets/baseline/../../../../README.md">', 1),
            ("links/staged-asset-dot-segment-back-inside-flagged", staged_home,
             '<img src="/draft/assets/baseline/../baseline/baseline.css">', 1),
            ("links/staged-asset-single-dot-segment-flagged", staged_home,
             '<img src="/draft/assets/baseline/./baseline.css">', 1),
            ("links/staged-asset-encoded-dot-segment-flagged", staged_home,
             '<img src="/draft/assets/baseline/%2e/baseline.css">', 1),
            ("links/staged-asset-empty-segment-flagged", staged_home,
             '<img src="/draft/assets/baseline/fonts//baseline.css">', 1),
            ("links/staged-asset-trailing-slash-flagged", staged_home,
             '<img src="/draft/assets/baseline/fonts/">', 1),
            ("links/staged-kept-live-with-query-passes", staged_home, '<a href="/development?x">x</a>', 0),
            ("links/staged-kept-live-with-query-and-fragment-passes", staged_home,
             '<a href="/development?x#top">x</a>', 0),
            ("links/staged-missing-page-with-query-flagged", staged_home, '<a href="/draft/absent/?x=1">x</a>', 1),
            ("links/staged-leading-space-flagged", staged_home, '<a href=" /draft/install/">x</a>', 1),
            ("links/staged-trailing-space-flagged", staged_home, '<a href="/draft/install/ ">x</a>', 1),
            ("links/staged-leading-tab-kept-live-flagged", staged_home, '<a href="\t/development">x</a>', 1)):
        got = unresolved_links(with_link(outputs, target, markup), staged_)
        expect(name, len(got) == want, got)
    got = unresolved_links(with_link(outputs, staged_home, '<a href=" /development">x</a>'), staged_)
    expect("links/staged-whitespace-named", len(got) == 1 and "leading or trailing whitespace" in got[0], got)
    with tempfile.TemporaryDirectory(prefix="gen-draft-site-bare-") as bare:
        for site_ in SITES:
            os.makedirs(Path(bare) / site_["live"])
            shutil.copyfile(ROOT / site_["live"] / "_redirects", Path(bare) / site_["live"] / "_redirects")
        expect("links/promoted-without-assets-flagged", unresolved_links(promoted, promoted_sites_, bare) != [])
    with tempfile.TemporaryDirectory(prefix="gen-draft-site-promoted-") as tmp:
        for site_ in SITES:
            shutil.copytree(ROOT / _asset_folder(site_), Path(tmp) / _asset_folder(site_))
            shutil.copyfile(ROOT / site_["live"] / "_redirects", Path(tmp) / site_["live"] / "_redirects")
        found = unresolved_links(promoted, promoted_sites_, tmp)
        expect("links/resolve-promoted-clean", found == [], found)
        promoted_home = "site/index.html"
        for name, markup, want in (
                ("links/promoted-missing-page-flagged", '<a href="/absent/">x</a>', 1),
                ("links/promoted-staged-url-flagged", '<a href="/draft/install/">x</a>', 1),
                ("links/promoted-missing-asset-flagged", '<img src="/assets/baseline/absent.svg">', 1),
                ("links/promoted-asset-at-root-flagged", '<img src="/assets/baseline/baseline.css">', 1),
                ("links/promoted-asset-at-asset-base-passes", '<img src="/draft/assets/baseline/baseline.css">', 0),
                ("links/promoted-placeholder-passes", '<a href="/draft/">x</a>', 0),
                ("links/promoted-deleted-page-flagged", '<a href="/teams">x</a>', 1),
                ("links/promoted-page-passes", '<a href="/install/">x</a>', 0),
                ("links/promoted-redirect-source-passes", '<a href="/rule1">x</a>', 0),
                ("links/promoted-kept-live-passes", '<a href="/development">x</a>', 0)):
            got = unresolved_links(with_link(promoted, promoted_home, markup), promoted_sites_, tmp)
            expect(name, len(got) == want, got)
        for rel, text in promoted.items():
            os.makedirs((Path(tmp) / rel).parent, exist_ok=True)
            with open(Path(tmp) / rel, "w", encoding="utf-8") as handle:
                handle.write(text)
        promoted_targets = set(promoted)
        expect("stray/promoted-clean", stray_outputs(tmp, promoted_sites_, promoted_targets) == [])
        for rel in ("site/development.html", "site/draft/index.html", "site/assets/baseline/x.html",
                    "site/robots.txt"):
            os.makedirs((Path(tmp) / rel).parent, exist_ok=True)
            with open(Path(tmp) / rel, "w", encoding="utf-8") as handle:
                handle.write("x")
        got = stray_outputs(tmp, promoted_sites_, promoted_targets)
        expect("stray/promoted-kept-placeholder-assets-and-other-files-pass", got == [], got)
        for rel in ("site/old.html", "site/install/extra.html", "opf/site/model/old.html"):
            with open(Path(tmp) / rel, "w", encoding="utf-8") as handle:
                handle.write("x")
        got = stray_outputs(tmp, promoted_sites_, promoted_targets)
        expect("stray/promoted-unlisted-pages-flagged", sorted(got) == [
            "opf/site/model/old.html", "site/install/extra.html", "site/old.html"], got)
        with open(Path(tmp) / "site/install/UPPER.HTML", "w", encoding="utf-8") as handle:
            handle.write("x")
        got = stray_outputs(tmp, promoted_sites_, promoted_targets)
        expect("stray/promoted-upper-case-extension-flagged", "site/install/UPPER.HTML" in got, got)
        got = stray_outputs(tmp, staged_, set(outputs))
        expect("stray/promoted-staged-targets-see-none", got == [], got)
    expect("stray/tree-clean", stray_outputs(ROOT) == [], stray_outputs(ROOT))

    with tempfile.TemporaryDirectory(prefix="gen-draft-site-selftest-") as tmp:
        first = run(tmp, check=True)
        second = run(tmp, check=False)
        third = run(tmp, check=True)
        target = Path(tmp) / "site/draft/index.html"
        with open(target, "ab") as handle:
            handle.write(b"\n")
        fourth = run(tmp, check=True)
        os.unlink(target)
        os.symlink(Path(tmp) / "site/license/index.html", target)
        fifth = run(tmp, check=True)
        os.unlink(target)
        sixth = run(tmp, check=False)
        with open(Path(tmp) / "site/install/old.html", "w", encoding="utf-8") as handle:
            handle.write("x")
        seventh = run(tmp, check=True)
        eighth = run(tmp, check=False)
    codes = [first, second, third, fourth, fifth, sixth, seventh, eighth]
    expect("check/codes", codes == [1, 0, 0, 1, 2, 0, 1, 1], codes)
    # An unresolved link refuses both the check and the normal run (exit 2) before anything is written; the
    # same prebuilt pages without it only drift against the empty tree.
    broken = with_link(live_outputs, "site/index.html", '<a href="/absent/">x</a>')
    with tempfile.TemporaryDirectory(prefix="gen-draft-site-unresolved-") as tmp:
        codes = [run(tmp, check=True, outputs=broken), run(tmp, check=False, outputs=broken)]
        written = sorted(os.listdir(tmp))
        codes.append(run(tmp, check=True, outputs=live_outputs))
    expect("check/unresolved-link-refused", codes == [2, 2, 1] and written == [], (codes, written))

    if failures:
        print("SELF-TEST FAIL: gen_draft_site {} failure(s)".format(len(failures)))
        for failure in failures:
            print("  - " + failure)
        return 1
    print("SELF-TEST PASS: gen_draft_site (shell and head order, the scheme script and switch, robots meta, "
          "the description metas (presence, escaping and order), footer "
          "cycle, base path, every "
          "component's reference markup, the data-file refusals, derived scripts with rail.js only where "
          "the rail lists sections, the link rules, kept live paths, the mappings bodies, promotion "
          "switch, text-field escaping, the structural link guard, the checked title and meta keys and "
          "--check exit codes)")
    return 0


def main(argv):
    if argv not in ([], ["--check"], ["--self-test"]):
        print("error: unrecognized arguments {!r}; fail closed".format(argv), file=sys.stderr)
        return 2
    if argv == ["--self-test"]:
        return _self_test()
    return run(ROOT, check=argv == ["--check"])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
