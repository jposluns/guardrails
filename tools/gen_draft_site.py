#!/usr/bin/env python3
"""Generate the staged draft sites (shared site template, baseline 1.3.1) under each site's draft/ root.

  gen_draft_site.py              write every registered page
  gen_draft_site.py --check      exit 1 if any target differs from a fresh build, 2 if one cannot be read
  gen_draft_site.py --self-test  assert the shell, the components, the refusals and the base-path rules

Every page is built from 1 shell (template specification section 5): `<html lang="en" class="site-<name>">`,
the head (charset, viewport, the robots noindex meta while BASE is the draft path, the title
`<Page> | <Name>.ai`, the favicon, the touch icon, fonts.css, palettes.css, baseline.css, then the page's
scripts with defer), the skip link, the top bar (the site's page links, then GitHub last; section 7.1),
`main#main-content`, the family footer (section 7.19) and the `#copy-status` live region. The home page
carries the "About this project" region (section 7.24), the one place that links the Disclosure and Licence
pages. BASE is the one base path for every internal href and src; it becomes "/" at
promotion, and the robots meta goes with it. A page is refused (exit 2, nothing written) if it carries an
en or em dash, an inline style or script, an `on*` attribute, or "what this is" framing. The baseline
files under assets/baseline/ are a pinned copy, not generated here (tools/check_baseline_pin.py).

Content model: each page is 1 TOML data file under site/draft-src/, discovered by listing that directory,
so a page lands or changes as data. A data file names the page's output path, title and kind (landing or
document) and a body of components (specification section 7); the generator refuses an unknown component,
an unknown or missing key, a wrong type, a duplicate output path, and a data directory it cannot list
(exit 2, nothing written). The page's scripts are derived from its content: copy.js where a copy block
renders, select.js where the install selector renders, and rail.js where the rail lists in-page sections
(section 7.6); no rail link carries a static aria-current. In an inline HTML value the token `$BASE/`
renders as the base path, so content survives promotion unchanged. The document control strip reads its
Version from the repository's VERSION file and its Status from changelog.toml.
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
import stat
import tempfile
import tomllib
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# The one base path for every internal href and src: "/draft/" while staging, "/" at promotion.
BASE = "/draft/"
DRAFT_BASE = "/draft/"
ASSETS = "assets/baseline/"
# The page data files, 1 per page, discovered by listing this directory.
SRC_REL = "site/draft-src"
MAX_PAGE_BYTES = 1 << 20

GENSRC_OUTPUTS = (
    {"target": "site/draft/index.html", "kind": "file",
     "sources": ("tools/gen_draft_site.py", "site/draft-src/home.toml"),
     "regenerate": "python3 tools/gen_draft_site.py"},
    {"target": "site/draft/disclosure/index.html", "kind": "file",
     "sources": ("tools/gen_draft_site.py", "site/draft-src/disclosure.toml"),
     "regenerate": "python3 tools/gen_draft_site.py"},
    {"target": "site/draft/license/index.html", "kind": "file",
     "sources": ("tools/gen_draft_site.py", "site/draft-src/license.toml"),
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

# Per-site chrome; aiqt.ai first. The pages themselves are data files under SRC_REL. Nav items are the
# site's own pages, then GitHub last; Disclosure and Licence are not in the bar (the home page's About
# region links them). An internal nav item is emitted only once its page's data file is registered, so
# every internal link resolves while the pages land one at a time.
SITES = (
    {"key": "aiqt", "name": "AIQT", "root": "site/draft",
     "logo-alt": "A shield bearing a check mark, set on a guardrail",
     "github": "https://github.com/jposluns/guardrails",
     "nav": (("install/", "Install"), ("standard/", "The standard"), ("rules/", "The 5 rules"),
             ("mappings/", "Mappings"))},
)


class RefusedPage(Exception):
    """A built page or a page data file breaks a rule; nothing is written."""


def _url(path=""):
    return BASE + path


def _asset(path):
    return BASE + ASSETS + path


def _page_url(rel):
    """The served URL of a page path under the draft root: index.html is its folder."""
    if rel == "index.html":
        return _url()
    if rel.endswith("/index.html"):
        return _url(rel[:-len("index.html")])
    return _url(rel)


def _external(href, label):
    return ('<a href="{}" target="_blank" rel="noopener noreferrer">{}<span class="vh"> (opens in a new tab)'
            '</span></a>').format(href, label)


def _brand(site):
    return ('<a class="brand" href="{}"><span class="name">{}<span class="tld">.ai</span></span>'
            '<span class="rule" aria-hidden="true"></span></a>').format(_url(), site["name"])


def _nav(site, pages, current):
    registered = {_page_url(page["rel"]) for page in pages}
    items = []
    for path, label in site["nav"]:
        href = _url(path)
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
        image = '<img src="{}" alt="">'.format(_asset("logos/{}.svg".format(key)))
        name = '<span class="cname">{}<span class="tld">.ai</span></span>'.format(word)
        if key == site["key"]:
            link = '<a class="card" href="{}" aria-current="page">{}{}<span class="vh"> (this site)</span></a>'.format(
                _url(), image, name)
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
    ).format(_asset("marks/github-invertocat-black.svg"), _asset("marks/github-invertocat-white.svg"), newtab,
             _asset("marks/linkedin-in-bug.png"), newtab, _asset("marks/orcid-id.svg"), newtab)
    sig = ('<div class="tile sigtile"><a class="sig" href="https://posluns.ca/" target="_blank" '
           'rel="noopener noreferrer" aria-label="A Jeff at Posluns dot ca project: posluns.ca (opens in a new '
           'tab)"><span class="pre">A </span>Jeff<span class="gold">@</span>Posluns<span class="gold">.ca</span>'
           '<span class="post"> Project</span></a></div>')
    return ('<footer class="family wrap"><div class="bar"><ul class="cards rainbow" aria-label="The 6 project '
            'sites">{}</ul><ul class="tail" aria-label="Jeff Posluns elsewhere">{}</ul>{}</div></footer>').format(
                "".join(cards), tail, sig)


def _about():
    """The "About this project" region (specification section 7.24, reference/landing.html)."""
    dot = '<span class="dot" aria-hidden="true">&middot;</span>'
    return ('<div class="region"><p class="lbl">About this project</p><div><p class="about">'
            '<a href="{}">Disclosure</a>{}<a href="{}">Licence</a>{}{}{}{}</p></div></div>').format(
                _url("disclosure/"), dot, _url("license/"), dot,
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


def _internal_href(href, where):
    """BASE + href for a site-relative target ("" is the home page); refused outside that shape."""
    if not isinstance(href, str) or href.startswith("/") or href.lower().startswith("http") \
            or ".." in href or INTERNAL_HREF_RE.fullmatch(href) is None:
        raise RefusedPage("{}: {!r} is not a site-relative href".format(where, href))
    return _url(href)


def _any_link(href, text, where):
    """An anchor for an internal site-relative href, or a new-tab anchor for an https URL."""
    if isinstance(href, str) and href.startswith("https://"):
        return _external(href, text)
    return '<a href="{}">{}</a>'.format(_internal_href(href, where), text)


def _inline(value):
    """An inline HTML value with the $BASE/ token rendered as the base path."""
    return value.replace("$BASE/", BASE)


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
                _inline(_text(spec, where, "kicker")), classes, _inline(_text(spec, where, "statement")),
                _inline(_text(spec, where, "intro")), _internal_href(spec["start_href"], where),
                _text(spec, where, "start_text"), _asset("logos/{}.svg".format(site["key"])),
                site["logo-alt"])


def _c_entries(site, doc, where):
    spec = _need(doc, where, ("component", "items"), ("label",))
    label = _text(spec, where, "label", "Start here")
    tiles = []
    for item in _items(spec, where, "items", ("href", "title", "n", "d")):
        tiles.append('<li><a href="{}"><span class="t">{}</span><span class="n">{}</span>'
                     '<span class="d">{}</span></a></li>'.format(
                         _internal_href(item["href"], where), _inline(item["title"]),
                         _inline(item["n"]), _inline(item["d"])))
    return ('<div class="region"><p class="lbl">{}</p><div><ol class="entries">{}</ol></div></div>').format(
        label, "".join(tiles))


def _c_region(site, doc, where):
    spec = _need(doc, where, ("component", "label", "html"))
    return '<div class="region"><p class="lbl">{}</p><div>{}</div></div>'.format(
        _text(spec, where, "label"), _inline(_text(spec, where, "html")))


def _c_value(site, doc, where):
    spec = _need(doc, where, ("component", "html"), ("label",))
    return ('<div class="region"><p class="lbl">{}</p><div><p class="value">{}</p></div></div>').format(
        _text(spec, where, "label", "What it gives you"), _inline(_text(spec, where, "html")))


def _c_about(site, doc, where):
    _need(doc, where, ("component",))
    return _about()


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
    spec = _need(doc, where, ("component", "document", "source_text", "source_href"),
                 ("licence_text", "licence_href"))
    source_href = _text(spec, where, "source_href")
    if not source_href.startswith("https://"):
        raise RefusedPage("{}: source_href must be an https URL".format(where))
    version, status = _version_and_status()
    licence = '<a href="{}">{}</a>'.format(
        _internal_href(spec.get("licence_href", "license/"), where),
        _text(spec, where, "licence_text", "Apache 2.0, see NOTICE"))
    return ('<dl class="control"><div><dt>Document</dt><dd>{}</dd></div>'
            '<div><dt>Version</dt><dd>{}</dd></div><div><dt>Status</dt><dd>{}</dd></div>'
            '<div><dt>Source</dt><dd>{}</dd></div><div><dt>Licence</dt><dd>{}</dd></div></dl>').format(
                _text(spec, where, "document"), version, status,
                _external(source_href, _text(spec, where, "source_text")), licence)


def _c_clause(site, doc, where):
    spec = _need(doc, where, ("component", "id", "num", "title", "lbl"), ("rail",))
    return ('<p class="lbl sec">{}</p>\n<h2 class="clause" id="{}"><span class="num">{}</span>'
            '<span>{}</span></h2>').format(_text(spec, where, "lbl"), _checked_id(spec, where),
                                           _text(spec, where, "num"), _inline(_text(spec, where, "title")))


def _c_clauses(site, doc, where):
    spec = _need(doc, where, ("component", "items"))
    rows = ['<li><span class="n">{}</span><div>{}</div></li>'.format(item["n"], _inline(item["html"]))
            for item in _items(spec, where, "items", ("n", "html"))]
    return '<ol class="clauses">{}</ol>'.format("".join(rows))


def _c_note(site, doc, where):
    spec = _need(doc, where, ("component", "label", "html"))
    return '<div class="note"><span class="label">{}</span><div>{}</div></div>'.format(
        _text(spec, where, "label"), _inline(_text(spec, where, "html")))


def _c_callout(site, doc, where):
    spec = _need(doc, where, ("component", "html"), ("label", "rule"))
    classes = "callout rule" if _flag(spec, where, "rule") else "callout"
    body = '<div class="{}">{}</div>'.format(classes, _inline(_text(spec, where, "html")))
    if "label" in spec:
        return '<p class="lbl accent blk">{}</p>\n{}'.format(_text(spec, where, "label"), body)
    return body


def _c_figures(site, doc, where):
    spec = _need(doc, where, ("component", "items"))
    figs = ['<div class="fig"><span class="num">{}</span><span class="cap">{}</span></div>'.format(
        item["num"], item["cap"]) for item in _items(spec, where, "items", ("num", "cap"))]
    return '<div class="wide"><div class="figures">{}</div></div>'.format("".join(figs))


def _table_html(spec, where):
    classes = " ".join(name for name, on in (("sticky", _flag(spec, where, "sticky")),
                                             ("fixed", _flag(spec, where, "fixed"))) if on)
    cols = ""
    if "cols" in spec:
        value = spec["cols"]
        if not isinstance(value, list) or not value or not all(
                isinstance(col, str) and re.fullmatch(r"w\d{1,2}", col) for col in value):
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
                cells.append(_inline(cell))
            else:
                cells.append("<td>{}</td>".format(_inline(cell)))
        body.append("<tr>{}</tr>".format("".join(cells)))
    table = ('<table class="{}"><caption class="vh">{}</caption>{}<thead><tr>{}</tr></thead>'
             '<tbody>{}</tbody></table>').format(
                 classes, _text(spec, where, "caption"), cols,
                 "".join("<th>{}</th>".format(_inline(cell)) for cell in head), "".join(body))
    return '<div class="tablewrap" tabindex="0">{}</div>'.format(table)


def _c_table(site, doc, where):
    spec = _need(doc, where, ("component", "caption", "head", "rows"), ("sticky", "fixed", "cols"))
    return '<div class="wide">{}</div>'.format(_table_html(spec, where))


def _c_register(site, doc, where):
    spec = _need(doc, where, ("component", "reg", "title", "count", "caption", "head", "rows"),
                 ("sticky", "fixed", "cols"))
    cap = '<p class="reg-cap"><b>{}</b><span>{}</span><span class="count">{}</span></p>'.format(
        _text(spec, where, "reg"), _text(spec, where, "title"), _text(spec, where, "count"))
    return '<div class="wide"><div class="register">{}{}</div></div>'.format(cap, _table_html(spec, where))


def _c_code(site, doc, where):
    spec = _need(doc, where, ("component", "id", "text"), ("variant", "wide", "note"))
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
    if "note" in spec:
        pre += '\n<p class="code-note">{}</p>'.format(_inline(_text(spec, where, "note")))
    return pre


def _c_picker(site, doc, where):
    spec = _need(doc, where, ("component", "items", "reset"))
    links = []
    for item in _items(spec, where, "items", ("family", "label")):
        if ID_RE.fullmatch(item["family"]) is None:
            raise RefusedPage("{}: {!r} is not a valid family id".format(where, item["family"]))
        links.append('<li><a href="#{0}" data-family-link="{0}">{1}</a></li>'.format(
            item["family"], item["label"]))
    return ('<ul class="picker" data-picker>{}</ul>'
            '<button class="btn picker-reset" type="button" data-picker-reset hidden>{}</button>'
            '<p class="vh" role="status" aria-live="polite" data-picker-status></p>').format(
                "".join(links), _text(spec, where, "reset"))


def _c_family(site, doc, where):
    spec = _need(doc, where, ("component", "family", "lbl", "heading", "verified"),
                 ("intro", "steps", "after"))
    family = _text(spec, where, "family")
    if ID_RE.fullmatch(family) is None:
        raise RefusedPage("{}: {!r} is not a valid family id".format(where, family))
    parts = ['<p class="lbl sec">{}</p>'.format(_text(spec, where, "lbl")),
             "<h2>{}</h2>".format(_inline(_text(spec, where, "heading"))),
             '<p class="verified">{}</p>'.format(_inline(_text(spec, where, "verified")))]
    if "intro" in spec:
        parts.append(_inline(_text(spec, where, "intro")))
    if "steps" in spec:
        steps = spec["steps"]
        if not isinstance(steps, list) or not steps or not all(isinstance(step, str) for step in steps):
            raise RefusedPage("{}: steps must be a non-empty array of strings".format(where))
        parts.append('<ol class="steps">{}</ol>'.format(
            "".join("<li><span>{}</span></li>".format(_inline(step)) for step in steps)))
    if "after" in spec:
        parts.append(_inline(_text(spec, where, "after")))
    return '<section id="{0}" data-family-section="{0}">\n{1}\n</section>'.format(family, "\n".join(parts))


def _c_download(site, doc, where):
    spec = _need(doc, where, ("component", "href", "filename", "size", "html"), ("label",))
    link = _any_link(_text(spec, where, "href"), _text(spec, where, "filename"), where)
    body = ('<div class="download"><p class="file">{}</p><p class="lbl size">{}</p><p>{}</p></div>').format(
        link, _text(spec, where, "size"), _inline(_text(spec, where, "html")))
    if "label" in spec:
        return '<p class="lbl blk">{}</p>\n{}'.format(_text(spec, where, "label"), body)
    return body


def _c_plan(site, doc, where):
    spec = _need(doc, where, ("component", "items"), ("variant", "described"))
    variant = _text(spec, where, "variant", "")
    if variant not in PLAN_VARIANTS:
        raise RefusedPage("{}: variant must be one of one, three".format(where))
    classes = "plan" + ((" " + variant) if variant else "") + (" described" if _flag(spec, where, "described") else "")
    rows = []
    for item in _items(spec, where, "items", ("href", "title"), ("n", "small")):
        row = '<a href="{}">{}</a>'.format(_internal_href(item["href"], where), _inline(item["title"]))
        if "n" in item:
            row += '<span class="n">{}</span>'.format(_inline(item["n"]))
        if "small" in item:
            row += "<small>{}</small>".format(_inline(item["small"]))
        rows.append("<li>{}</li>".format(row))
    return '<ul class="{}">{}</ul>'.format(classes, "".join(rows))


def _c_scope(site, doc, where):
    spec = _need(doc, where, ("component", "html"))
    return '<p class="lbl blk">Scope</p>\n<p>{}</p>'.format(_inline(_text(spec, where, "html")))


def _c_next(site, doc, where):
    spec = _need(doc, where, ("component", "text", "href"), ("html",))
    line = ""
    if "html" in spec:
        line = "<p>{}</p>".format(_inline(_text(spec, where, "html")))
    arrow = '<a class="start" href="{}">{} <span class="arrow" aria-hidden="true">&rarr;</span></a>'.format(
        _internal_href(spec["href"], where), _text(spec, where, "text"))
    return '<div class="next"><p class="lbl">Next</p>{}{}</div>'.format(line, arrow)


def _c_lead(site, doc, where):
    spec = _need(doc, where, ("component", "html"))
    return '<p class="lead">{}</p>'.format(_inline(_text(spec, where, "html")))


def _c_label(site, doc, where):
    spec = _need(doc, where, ("component", "text"), ("variant",))
    variant = _text(spec, where, "variant", "blk")
    if variant not in ("blk", "sec", "accent blk"):
        raise RefusedPage("{}: variant must be blk, sec or accent blk".format(where))
    return '<p class="lbl {}">{}</p>'.format(variant, _text(spec, where, "text"))


def _c_section(site, doc, where):
    spec = _need(doc, where, ("component", "lbl", "heading"), ("id", "rail"))
    attr = ""
    if "id" in spec:
        attr = ' id="{}"'.format(_checked_id(spec, where))
    return '<p class="lbl sec">{}</p>\n<h2{}>{}</h2>'.format(
        _text(spec, where, "lbl"), attr, _inline(_text(spec, where, "heading")))


def _c_entry(site, doc, where):
    spec = _need(doc, where, ("component", "lbl", "id", "heading", "claim", "limitation", "evidence"),
                 ("rail",))
    return ('<p class="lbl sec">{}</p>\n<h2 id="{}">{}</h2>\n'
            '<p><strong>Claim:</strong> {}</p>\n<p><strong>Limitation:</strong> {}</p>\n'
            '<p><strong>Evidence:</strong> {}</p>').format(
                _text(spec, where, "lbl"), _checked_id(spec, where), _inline(_text(spec, where, "heading")),
                _inline(_text(spec, where, "claim")), _inline(_text(spec, where, "limitation")),
                _inline(_text(spec, where, "evidence")))


def _c_prose(site, doc, where):
    spec = _need(doc, where, ("component", "html"))
    return _inline(_text(spec, where, "html"))


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
    "entry": _c_entry, "prose": _c_prose,
}
# Components whose rail key lists the section on the page's rail (specification section 7.6).
RAIL_BEARING = ("clause", "section", "entry")


# ---------------------------------------------------------------------------------------------------
# Page data files.

def _page_from_doc(doc, where):
    """A validated page model from one parsed data file."""
    if not isinstance(doc, dict):
        raise RefusedPage("{}: the page must be a TOML table".format(where))
    spec = _need(doc, where, ("path", "title", "kind", "body"), ("label", "heading", "meta"))
    rel = _text(spec, where, "path")
    if REL_RE.fullmatch(rel) is None:
        raise RefusedPage("{}: {!r} is not a safe page path".format(where, rel))
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
    for position, component in enumerate(body, 1):
        comp_where = "{}.body[{}]".format(where, position)
        name = component.get("component")
        if name not in COMPONENTS:
            raise RefusedPage("{}: unknown component {!r}".format(comp_where, name))
        if "rail" in component:
            if name not in RAIL_BEARING:
                raise RefusedPage("{}: only a clause, section or entry joins the rail".format(comp_where))
            rail.append((_checked_id(component, comp_where), _text(component, comp_where, "rail")))
        if name == "about" and kind == "document" and position != len(body):
            raise RefusedPage("{}: the About region closes a document page".format(comp_where))
    return {"rel": rel, "title": spec["title"], "kind": kind, "label": spec.get("label"),
            "heading": spec.get("heading"), "meta": spec.get("meta"), "body": body, "rail": rail,
            "where": where}


def _load_pages(src):
    """Every page model from the data directory, sorted by file name; RefusedPage on anything that is
    not a plain readable TOML page file, on a directory that cannot be listed, and on an empty set."""
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
        where = "{}/{}".format(SRC_REL, name)
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
    if "code" in names:
        scripts.append("copy.js")
    if "picker" in names:
        scripts.append("select.js")
    if page["rail"]:
        scripts.append("rail.js")
    return scripts


def _rail_html(site, page):
    logo = '<img class="rail-logo" src="{}" alt="{}">'.format(
        _asset("logos/{}.svg".format(site["key"])), site["logo-alt"])
    if not page["rail"]:
        return '<div class="rail">{}</div>'.format(logo)
    links = "".join('<li><a href="#{}">{}</a></li>'.format(anchor, label) for anchor, label in page["rail"])
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
    parts = ['<p class="lbl first">{}</p>'.format(page["label"]), "<h1>{}</h1>".format(_inline(page["heading"])),
             _rail_html(site, page)]
    if page["meta"]:
        parts.append('<p class="meta">{}</p>'.format(_inline(page["meta"])))
    tail = []
    for name, html in rendered:
        if name == "about":
            tail.append(html)
        else:
            parts.append(html)
    doc = '<div class="doc">{}\n</div>'.format("\n".join(parts))
    return '<div class="wrap">{}</div>'.format("\n".join([doc] + tail))


LINK_RE = re.compile(r'(?:href|src)="([^"]*)"')
ANCHOR_RE = re.compile(r"<a [^>]*>")


def _link_findings(html):
    """Every base-path or new-tab finding on a built page: an internal href or src outside BASE, a non-https
    external URL, or an external anchor without the new-tab attributes and hint."""
    found = []
    for target in LINK_RE.findall(html):
        if target.startswith(BASE) or target.startswith("#"):
            continue
        if not target.startswith("https://"):
            found.append("{!r} is neither under {} nor an https URL".format(target, BASE))
    for anchor in ANCHOR_RE.findall(html):
        href = LINK_RE.search(anchor)
        if href is None or not href.group(1).startswith("https://"):
            continue
        if 'target="_blank" rel="noopener noreferrer"' not in anchor:
            found.append("external link {!r} without the new-tab attributes".format(href.group(1)))
    return found


def render_page(site, pages, page):
    rel, title = page["rel"], page["title"]
    head = ['<meta charset="utf-8">', '<meta name="viewport" content="width=device-width, initial-scale=1">']
    if BASE == DRAFT_BASE:
        head.append('<meta name="robots" content="noindex, nofollow">')
    head.append("<title>{} | {}.ai</title>".format(title, site["name"]))
    head.append('<link rel="icon" href="{}" type="image/svg+xml">'.format(
        _asset("logos/{}-favicon.svg".format(site["key"]))))
    head.append('<link rel="apple-touch-icon" href="{}">'.format(_asset("logos/{}-touch.png".format(site["key"]))))
    for sheet in ("fonts.css", "palettes.css", "baseline.css"):
        head.append('<link rel="stylesheet" href="{}">'.format(_asset(sheet)))
    for script in _page_scripts(page):
        head.append('<script src="{}" defer></script>'.format(_asset(script)))
    html = "\n".join([
        "<!doctype html>",
        '<html lang="en" class="site-{}">'.format(site["key"]),
        "<head>"] + head + [
        "</head>",
        "<body>",
        '<a class="skip" href="#main-content">Skip to content</a>',
        '<header class="mast">{}{}</header>'.format(_brand(site), _nav(site, pages, _page_url(rel))),
        '<main id="main-content">',
        _body_html(site, page),
        "</main>",
        _footer(site),
        '<p class="vh" id="copy-status" role="status" aria-live="polite"></p>',
        "</body>",
        "</html>",
    ]) + "\n"
    refusals = refusals_for(html) + _link_findings(html)
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


def build(pages=None):
    """{target path: text} for every registered output. With the default page set (the data files under
    SRC_REL), the built set must match the GENSRC_OUTPUTS declaration, so a page cannot land or vanish
    without its registry entry."""
    declared = pages is None
    if pages is None:
        pages = _load_pages(ROOT / SRC_REL)
    outputs = {}
    for site in SITES:
        for page in pages:
            outputs["{}/{}".format(site["root"], page["rel"])] = render_page(site, pages, page)
    if declared:
        targets = {entry["target"] for entry in GENSRC_OUTPUTS}
        if set(outputs) != targets:
            raise RefusedPage("built outputs {} differ from GENSRC_OUTPUTS {}".format(
                sorted(outputs), sorted(targets)))
    return outputs


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


def run(root, check):
    try:
        outputs = build()
    except RefusedPage as exc:
        print("error: refused: {}".format(exc), file=sys.stderr)
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
    if drift:
        print("run tools/gen_draft_site.py to regenerate")
        return 1
    return 0


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

    global BASE
    site = SITES[0]
    outputs = build()
    page = outputs["site/draft/index.html"]
    expect("shell/html-class", '<html lang="en" class="site-aiqt">' in page)
    expect("shell/title", "<title>Home | AIQT.ai</title>" in page)
    expect("shell/robots-meta", page.count('<meta name="robots" content="noindex, nofollow">') == 1)
    order = [page.find(_asset(sheet)) for sheet in ("fonts.css", "palettes.css", "baseline.css")]
    expect("shell/stylesheet-order", -1 not in order and order == sorted(order), order)
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
    expect("nav/unregistered-items-absent", all('href="/draft/install/"' not in nav for nav in navs), navs)
    expect("nav/no-disclosure-or-licence", all("disclosure" not in nav and "license" not in nav for nav in navs),
           navs)
    expect("nav/github-last", all(nav.endswith('GitHub<span class="vh"> (opens in a new tab)</span></a>')
                                  for nav in navs), navs)
    about = ('<div class="region"><p class="lbl">About this project</p><div><p class="about">'
             '<a href="/draft/disclosure/">Disclosure</a><span class="dot" aria-hidden="true">&middot;</span>'
             '<a href="/draft/license/">Licence</a><span class="dot" aria-hidden="true">&middot;</span>'
             '<a href="https://posluns.ca/privacy/" target="_blank" rel="noopener noreferrer">Privacy'
             '<span class="vh"> (opens in a new tab)</span></a><span class="dot" aria-hidden="true">&middot;</span>'
             '<a href="https://posluns.ca/security/" target="_blank" rel="noopener noreferrer">Security'
             '<span class="vh"> (opens in a new tab)</span></a></p></div></div>')
    expect("about/landing-exact-markup", page.count(about) == 1)
    expect("about/after-paragraph", page.find("arrive here one at a time.</p>") < page.find(about))
    expect("about/inner-pages-none", all('class="about"' not in outputs["site/draft/" + rel]
                                         for rel in ("disclosure/index.html", "license/index.html")))
    for rel, heading in (("disclosure/index.html", "Disclosure"), ("license/index.html", "Licence")):
        inner = outputs["site/draft/" + rel]
        expect("placeholder/" + rel, inner.count("<h1") == 1 and "<h1>{}</h1>".format(heading) in inner
               and "<title>{} | AIQT.ai</title>".format(heading) in inner)
    hrefs = re.findall(r'(?:href|src)="([^"]*)"', page)
    internal = [h for h in hrefs if not h.startswith("https://") and not h.startswith("#")]
    expect("base/every-internal-path", internal and all(h.startswith(BASE) for h in internal), internal)
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
    expect("component/about", rendered('component = "about"\n', kind="landing") == _about())
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
        '<div class="wide"><div class="tablewrap" tabindex="0"><table class="sticky fixed">'
        '<caption class="vh">C</caption><colgroup><col class="w26"><col class="w17"></colgroup>'
        '<thead><tr><th>A</th><th>B</th></tr></thead><tbody><tr><td>x</td><td class="id"><code>y</code>'
        '</td></tr></tbody></table></div></div>'), table)
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
    expect("links/clean-page-passes", _link_findings(railed_html) == [], _link_findings(railed_html))
    expect("links/internal-outside-base", _link_findings('<a href="/live/">x</a>') != [])
    expect("links/non-https-external", _link_findings('<a href="http://example.com/">x</a>') != [])
    expect("links/external-without-newtab", _link_findings('<a href="https://example.com/">x</a>') != [])
    bad = fixture_page('[[body]]\ncomponent = "prose"\nhtml = "<a href=\\"/elsewhere/\\">x</a>"\n')
    try:
        render_page(site, [bad], bad)
        expect("links/bad-inline-refused", False)
    except RefusedPage:
        pass
    hostile = fixture_page('[[body]]\ncomponent = "prose"\nhtml = "<p onclick=\\"x()\\">x</p>"\n')
    try:
        build([hostile])
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

    BASE = "/"
    try:
        pages = _load_pages(ROOT / SRC_REL)
        promoted = render_page(site, pages, [page for page in pages if page["rel"] == "index.html"][0])
    finally:
        BASE = DRAFT_BASE
    expect("base/promotion-drops-robots", "noindex" not in promoted)
    expect("base/promotion-root-paths", 'href="/assets/baseline/fonts.css"' in promoted
           and "/draft/" not in promoted)

    with tempfile.TemporaryDirectory(prefix="gen-draft-site-selftest-") as tmp:
        first = run(tmp, check=True)
        second = run(tmp, check=False)
        third = run(tmp, check=True)
        target = Path(tmp) / "site/draft/index.html"
        with open(target, "ab") as handle:
            handle.write(b"\n")
        fourth = run(tmp, check=True)
        os.unlink(target)
        os.symlink(Path(tmp) / "site/draft/license/index.html", target)
        fifth = run(tmp, check=True)
    expect("check/codes", [first, second, third, fourth, fifth] == [1, 0, 0, 1, 2],
           [first, second, third, fourth, fifth])

    if failures:
        print("SELF-TEST FAIL: gen_draft_site {} failure(s)".format(len(failures)))
        for failure in failures:
            print("  - " + failure)
        return 1
    print("SELF-TEST PASS: gen_draft_site (shell order, robots meta, footer cycle, base path, every "
          "component's reference markup, the data-file refusals, derived scripts with rail.js only where "
          "the rail lists sections, the link rules, promotion switch and --check exit codes)")
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
