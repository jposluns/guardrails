#!/usr/bin/env python3
"""Generate the staged draft sites (shared site template, baseline 1.2.1) under each site's draft/ root.

  gen_draft_site.py              write every registered page
  gen_draft_site.py --check      exit 1 if any target differs from a fresh build, 2 if one cannot be read
  gen_draft_site.py --self-test  assert the shell, the refusals and the base-path rules

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

import os
import re
import stat
import tempfile
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# The one base path for every internal href and src: "/draft/" while staging, "/" at promotion.
BASE = "/draft/"
DRAFT_BASE = "/draft/"
ASSETS = "assets/baseline/"

GENSRC_OUTPUTS = (
    {"target": "site/draft/index.html", "kind": "file",
     "sources": ("tools/gen_draft_site.py",),
     "regenerate": "python3 tools/gen_draft_site.py"},
    {"target": "site/draft/disclosure/index.html", "kind": "file",
     "sources": ("tools/gen_draft_site.py",),
     "regenerate": "python3 tools/gen_draft_site.py"},
    {"target": "site/draft/license/index.html", "kind": "file",
     "sources": ("tools/gen_draft_site.py",),
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

# Per-site configuration; aiqt.ai first. Each page is (output path under the root, title, nav label or
# None, scripts, body builder name). Nav items are the site's own pages, then GitHub last; Disclosure and
# Licence are not in the bar (the home page's About region links them). An internal nav item is emitted only
# once its page is registered, so every internal link resolves while the pages land one at a time; the 2
# About targets are registered as placeholder pages for the same reason.
SITES = (
    {"key": "aiqt", "name": "AIQT", "root": "site/draft",
     "logo-alt": "A shield bearing a check mark, set on a guardrail",
     "github": "https://github.com/jposluns/guardrails",
     "nav": (("install/", "Install"), ("standard/", "The standard"), ("rules/", "The 5 rules"),
             ("mappings/", "Mappings")),
     "pages": (("index.html", "Home", None, (), "placeholder"),
               ("disclosure/index.html", "Disclosure", None, (), "placeholder-disclosure"),
               ("license/index.html", "Licence", None, (), "placeholder-license"))},
)


class RefusedPage(Exception):
    """A built page breaks a template code-style rule; nothing is written."""


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


def _nav(site, current):
    registered = {_page_url(rel) for rel, _title, _label, _scripts, _body in site["pages"]}
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


def _doc(site, label, heading, sentence):
    return ('<div class="doc"><p class="lbl first">{}</p>\n<h1>{}</h1>\n'
            '<div class="rail"><img class="rail-logo" src="{}" alt="{}"></div>\n'
            '<p>{}</p>\n</div>').format(label, heading, _asset("logos/{}.svg".format(site["key"])),
                                        site["logo-alt"], sentence)


def _body_placeholder(site):
    # The home page keeps its placeholder until the landing page lands; the About region follows its
    # paragraph, where it will follow the value paragraph.
    return '<div class="wrap">{}\n{}</div>'.format(
        _doc(site, "Draft", "Draft in progress",
             "The new {}.ai pages arrive here one at a time.".format(site["name"])), _about())


def _body_placeholder_disclosure(site):
    return '<div class="wrap">{}</div>'.format(
        _doc(site, "Draft", "Disclosure", "The {}.ai disclosure arrives here in a later draft.".format(
            site["name"])))


def _body_placeholder_license(site):
    return '<div class="wrap">{}</div>'.format(
        _doc(site, "Draft", "Licence", "The {}.ai licence page arrives here in a later draft.".format(
            site["name"])))


BODIES = {"placeholder": _body_placeholder, "placeholder-disclosure": _body_placeholder_disclosure,
          "placeholder-license": _body_placeholder_license}


def render_page(site, page):
    rel, title, _label, scripts, body = page
    head = ['<meta charset="utf-8">', '<meta name="viewport" content="width=device-width, initial-scale=1">']
    if BASE == DRAFT_BASE:
        head.append('<meta name="robots" content="noindex, nofollow">')
    head.append("<title>{} | {}.ai</title>".format(title, site["name"]))
    head.append('<link rel="icon" href="{}" type="image/svg+xml">'.format(
        _asset("logos/{}-favicon.svg".format(site["key"]))))
    head.append('<link rel="apple-touch-icon" href="{}">'.format(_asset("logos/{}-touch.png".format(site["key"]))))
    for sheet in ("fonts.css", "palettes.css", "baseline.css"):
        head.append('<link rel="stylesheet" href="{}">'.format(_asset(sheet)))
    for script in scripts:
        head.append('<script src="{}" defer></script>'.format(_asset(script)))
    html = "\n".join([
        "<!doctype html>",
        '<html lang="en" class="site-{}">'.format(site["key"]),
        "<head>"] + head + [
        "</head>",
        "<body>",
        '<a class="skip" href="#main-content">Skip to content</a>',
        '<header class="mast">{}{}</header>'.format(_brand(site), _nav(site, _page_url(rel))),
        '<main id="main-content">',
        BODIES[body](site),
        "</main>",
        _footer(site),
        '<p class="vh" id="copy-status" role="status" aria-live="polite"></p>',
        "</body>",
        "</html>",
    ]) + "\n"
    refusals = refusals_for(html)
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


def build():
    """{target path: text} for every registered output."""
    outputs = {}
    for site in SITES:
        for page in site["pages"]:
            outputs["{}/{}".format(site["root"], page[0])] = render_page(site, page)
    declared = {entry["target"] for entry in GENSRC_OUTPUTS}
    if set(outputs) != declared:
        raise RefusedPage("built outputs {} differ from GENSRC_OUTPUTS {}".format(
            sorted(outputs), sorted(declared)))
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

    global BASE
    outputs = build()
    site = SITES[0]
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

    original = BODIES["placeholder"]
    BODIES["placeholder"] = lambda s: '<p onclick="x()">x</p>'
    try:
        build()
        refused = False
    except RefusedPage:
        refused = True
    finally:
        BODIES["placeholder"] = original
    expect("refuse/build-raises", refused)

    BASE = "/"
    try:
        promoted = render_page(site, site["pages"][0])
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
    print("SELF-TEST PASS: gen_draft_site (shell order, robots meta, footer cycle, base path, refusals, "
          "promotion switch and --check exit codes)")
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
