#!/usr/bin/env python3
"""Site-integrity gate for site/*.html: en/em dashes, link/anchor resolution, and basic HTML validity.

Link classification uses urlsplit: an absolute URL on the site's own host (aiqt.ai) is internal; every
other scheme (http/https elsewhere, mailto, tel, ftp, javascript, data, ...) and protocol-relative
//host links are external and skipped. An internal path resolves to an existing file under site/ by
trying the path as-is, path + ".html", and path + "/index.html"; "/" resolves to index.html; a path
escaping site/ (e.g. /../x) is broken. A fragment (#id) is validated against the target page's ids; an
anchor-only or query-only href is validated against the current page.

Basic HTML validity: a page must have a non-empty <title>, and must not reuse an id (a duplicate id is
invalid and silently breaks in-page anchors).

Tag structure: open/close balance and nesting, with void elements, self-closing tags (a trailing slash
is honoured on void and foreign svg/math elements, ignored on non-void HTML so <div/> stays open), and
HTML5 optional-close tables. The subtree of svg/math and of <textarea> (and of <script>/<style> via
parser CDATA) is NOT structurally validated, so embedded content never false-positives; the root's own
open/close balance IS checked, so an unclosed one is caught, and links/ids inside such a subtree are not
validated (a deferred coverage gap). Non-nestable nesting IS detected (slice-2): a <form> inside a
<form>, and an interactive element (<a>/<button>) inside another interactive element, both of which the
parser accepts as well-balanced but which break rendering/behaviour. Download-artifact checksums are
tracked separately (they need a final content baseline). Exit 0 clean, 1 on any finding, 2 on a read error (unreadable dir/file, fail-closed).

Logo consistency: within a root, every page's header logo block must be byte-identical to the majority
block of the pages not named 404.html. A 404 page is served at any missing URL, however deep, so it must
open its logo with the root-absolute home link href="/" where that block opens with the relative
href="./" (the relative form is a finding there); every other byte must still match, and no other page
gets this allowance. For the same reason every relative href or src on a page named 404.html (a
stylesheet, icon, script, image or link, inside the logo block or not) is a finding; root-absolute,
external and fragment-only or query-only values pass there.

Apple touch icon: every live page (one not under the top-level draft/ directory of its root, 404 pages
included) must carry a <link rel="apple-touch-icon"> whose href names an existing file under that root,
taken as the exact path (no .html or index.html fallback); a missing link, or one naming a missing file, a
directory, an external or protocol-relative URL or a path escaping the root, is a finding, and so is a
relative href on a 404 page. Draft pages are built under the site template's own rules and are not checked here.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: check_site.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import os
import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _walk import walk_files  # noqa: E402  fail-closed tree walk (os.walk, not rglob)

EN, EM = "–", "—"
# Site host from AIQT_SITE_HOST (default, and empty-value fallback, aiqt.ai; lowercased), so a
# non-aiqt.ai adopter can vendor the pack unpatched. Unchanged when the variable is unset or empty.
_SITE_HOST = (os.environ.get("AIQT_SITE_HOST", "aiqt.ai") or "aiqt.ai").lower()
SITE_HOSTS = {_SITE_HOST, "www." + _SITE_HOST}

# Tags that never take an end tag; never pushed on the open-tag stack.
VOID_ELEMENTS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
}
# Roots whose SUBTREE is not structurally validated in this slice: svg/math (foreign, XML-ish, with
# different self-closing rules) and textarea (raw text, not markup). Structure INSIDE them is a slice-2
# non-goal; the root's own open/close balance IS still checked, so an unclosed one is caught.
SUSPEND_ROOTS = {"svg", "math", "textarea"}
# HTML5 elements whose end tag may be legally omitted (left open or auto-closed);
# an implicit close of one of these is not a structural finding.
OPTIONAL_END_TAGS = {
    "html", "head", "body", "li", "p", "dt", "dd",
    "option", "optgroup", "thead", "tbody", "tfoot", "tr", "td", "th",
    "rt", "rp", "colgroup",
}
# Sibling start tags that auto-close an open optional-end-tag element
# (WHATWG tree construction, simplified to the common cases).
AUTOCLOSERS = {
    "li": {"li"},
    "p": {"address", "article", "aside", "blockquote", "details", "div", "dl", "fieldset",
          "figcaption", "figure", "footer", "form", "h1", "h2", "h3", "h4", "h5", "h6",
          "header", "hgroup", "hr", "main", "menu", "nav", "ol", "p", "pre", "section",
          "summary", "table", "ul"},
    "option": {"option", "optgroup"},
    "optgroup": {"optgroup"},
    "tr": {"tr"},
    "td": {"td", "th", "tr"},
    "th": {"td", "th", "tr"},
    "thead": {"tbody", "tfoot"},
    "tbody": {"tbody", "tfoot"},
    "tfoot": {"tbody"},
    "dt": {"dt", "dd"},
    "dd": {"dt", "dd"},
}
# Non-nestable content models (WHATWG), the slice-2 subset: a <form> may not contain another <form>, and
# an interactive element (<a>/<button>) may not contain another interactive element. These nest silently
# in the parser (well-balanced) but break rendering/behaviour, so open/close balance never catches them.
INTERACTIVE = {"a", "button"}
# The Apple touch icon link every live page carries, and the directory name whose subtree holds draft pages
# (built under the site template's own rules, so not held to the live pages' head).
TOUCH_ICON_REL = "apple-touch-icon"
DRAFT_DIR = "draft"


class Page(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links = []       # (value, line)
        self.ids = set()
        self.id_list = []     # (value, line) for duplicate detection
        self.title_present = False
        self.title_text = ""
        self._in_title = False
        self.stack = []           # open non-void tags: (tag, line)
        self.tag_findings = []    # (line, msg) structural findings
        self.suspend_tag = None   # SUSPEND_ROOT we are inside (subtree not checked), or None
        self.suspend_count = 0    # nesting of that same root, so the matching close ends suspension
        self.touch_icons = []     # (href, line) of each <link rel="apple-touch-icon">

    def handle_starttag(self, tag, attrs):
        if self.suspend_tag is not None:
            if tag == self.suspend_tag:
                self.suspend_count += 1
            return
        d = dict(attrs)
        for key in ("href", "src"):
            if d.get(key):
                self.links.append((d[key], self.getpos()[0]))
        if tag == "link" and TOUCH_ICON_REL in (d.get("rel") or "").lower().split():
            self.touch_icons.append(((d.get("href") or "").strip(), self.getpos()[0]))
        if d.get("id"):
            self.ids.add(d["id"])
            self.id_list.append((d["id"], self.getpos()[0]))
        # Only <a name="..."> is a fragment-navigable anchor target; a `name` on any other element
        # (input/meta/form/param/...) is not, so registering those would let a broken #anchor pass.
        if tag == "a" and d.get("name"):
            self.ids.add(d["name"])
        if tag == "title":
            self.title_present = True
            self._in_title = True
        while self.stack and tag in AUTOCLOSERS.get(self.stack[-1][0], ()):
            self.stack.pop()
        # Non-nestable check, before this tag is pushed. A <form> may not be nested in a <form>. For the
        # interactive family two distinct HTML rules apply: (1) any <a> forbids an <a> descendant (nested
        # anchors, regardless of href), and (2) <a>/<button> forbid an INTERACTIVE descendant, where <a>
        # is interactive only WITH an href (a bare <a> is legal phrasing content, e.g. inside a <button>)
        # and <button> is always interactive.
        if tag == "form":
            if any(t == "form" for t, _ in self.stack):
                self.tag_findings.append(
                    (self.getpos()[0], "<form> must not be nested inside another <form>"))
        elif tag in INTERACTIVE:
            if tag == "a" and any(t == "a" for t, _ in self.stack):
                self.tag_findings.append(
                    (self.getpos()[0], "<a> must not be nested inside another <a>"))
            elif tag == "button" or "href" in d:
                bad = next((t for t, _ in reversed(self.stack) if t in INTERACTIVE), None)
                if bad is not None:
                    self.tag_findings.append((self.getpos()[0],
                        "<{}> (interactive content) must not be nested inside <{}>".format(tag, bad)))
        if tag not in VOID_ELEMENTS:
            self.stack.append((tag, self.getpos()[0]))
            if tag in SUSPEND_ROOTS:
                self.suspend_tag = tag       # enter suspension; the root stays at the top of the stack
                self.suspend_count = 1

    def handle_startendtag(self, tag, attrs):
        if self.suspend_tag is not None:
            return
        if tag in VOID_ELEMENTS:
            self.handle_starttag(tag, attrs)   # extract href/src/id; void is not pushed on the stack
            return
        if tag in SUSPEND_ROOTS:
            d = dict(attrs)                    # <svg id=.. />: register id, but do not enter suspension.
            if d.get("id"):                    # (name on svg/math/textarea is not an <a> anchor target)
                self.ids.add(d["id"])
                self.id_list.append((d["id"], self.getpos()[0]))
            return
        # a non-void HTML element with a trailing slash is NOT self-closed (the parser ignores the
        # slash), so <div/> is an open <div> that must be closed later.
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        if self.suspend_tag is not None:
            if tag == self.suspend_tag:
                self.suspend_count -= 1
                if self.suspend_count == 0:
                    self.stack.pop()          # the suspend root is at the top; nothing pushed inside
                    self.suspend_tag = None
            return
        if tag in VOID_ELEMENTS:
            self.tag_findings.append(
                (self.getpos()[0], "end tag </{}> for a void element (invalid HTML)".format(tag)))
            return
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                for t, line in self.stack[i + 1:]:
                    if t not in OPTIONAL_END_TAGS:
                        self.tag_findings.append(
                            (line, "<{}> opened but never closed (implicitly closed by </{}> at line {})".format(
                                t, tag, self.getpos()[0])))
                del self.stack[i:]
                return
        self.tag_findings.append(
            (self.getpos()[0], "end tag </{}> has no matching open tag".format(tag)))

    def finalize(self):
        for tag, line in self.stack:
            if tag not in OPTIONAL_END_TAGS:
                self.tag_findings.append(
                    (line, "<{}> opened but never closed before end of document".format(tag)))
        self.stack = []

    def handle_data(self, data):
        if self._in_title:
            self.title_text += data


def _under(site_root, resolved):
    return resolved == site_root or str(resolved).startswith(str(site_root) + os.sep)


def resolve_link(base, path, site_root):
    p = path.rstrip("/")
    if p == "":
        candidates = [site_root / "index.html"]
    else:
        rel = p.lstrip("/") if path.startswith("/") else p
        candidates = [base / rel, base / (rel + ".html"), base / rel / "index.html"]
    for cand in candidates:
        resolved = cand.resolve()
        if _under(site_root, resolved) and resolved.is_file():
            return resolved
    return None


def classify(v):
    # urlsplit does not percent-decode. The PATH is unquoted so an encoded link (my%20page.html) resolves
    # against the real file "my page.html". The FRAGMENT is returned RAW: HTML fragment navigation matches
    # an id equal to the raw fragment FIRST, then the percent-decoded form, so the anchor check tries both
    # (see main()). Unquoting the fragment here unconditionally would drop the raw-match case.
    parts = urlsplit(v)
    if parts.scheme:
        if parts.scheme.lower() in ("http", "https") and parts.netloc.lower() in SITE_HOSTS:
            return unquote(parts.path), parts.fragment
        return None
    if parts.netloc:
        return None
    return unquote(parts.path), parts.fragment


_LOGO_RE = re.compile(r'<a class="logo".*?</a>', re.S)
# A 404 page is served at any missing URL, however deep, so a relative home link there resolves below the
# root. On a page named NOT_FOUND_PAGE the logo must therefore open with the root-absolute home link where
# the canonical opens with the relative one; every other byte must still match.
NOT_FOUND_PAGE = "404.html"
_LOGO_RELATIVE_HOME = '<a class="logo" href="./"'
_LOGO_ROOT_HOME = '<a class="logo" href="/"'


def logo_findings(pages):
    """Findings for header-logo drift across pages. `pages` is a list of (rel, text). The logo block is
    duplicated per page (no page generator yet), so every page that carries one must carry the SAME bytes
    as the majority (canonical) block of the pages not named 404.html (of all pages when only 404 pages
    carry one); any other block is reported. The one exception: a 404 page carries the canonical block
    with its relative home link href="./" written as the root-absolute href="/", and the relative form is
    reported there. A page without a logo is not a finding."""
    found = []
    for rel, text in pages:
        match = _LOGO_RE.search(text)
        if match:
            found.append((rel, match.group(0), Path(rel).name == NOT_FOUND_PAGE))
    counts = {}
    for _rel, block, _not_found in [item for item in found if not item[2]] or found:
        counts[block] = counts.get(block, 0) + 1
    if not counts:
        return []
    canonical = max(counts, key=lambda block: counts[block])
    accepted_not_found = {canonical}
    if canonical.startswith(_LOGO_RELATIVE_HOME):
        accepted_not_found = {_LOGO_ROOT_HOME + canonical[len(_LOGO_RELATIVE_HOME):]}
    out = []
    for rel, block, not_found in found:
        if block in (accepted_not_found if not_found else {canonical}):
            continue
        out.append("{}: header logo block differs from the canonical logo (logos must be "
                   "byte-identical across pages)".format(rel))
    return out


def _touch_icon_file(href, page_dir, site_root):
    """The existing regular file under site_root that href names as an exact path, or None. An external
    or empty-path href, a directory, a path escaping the root and a missing file all give None."""
    classified = classify(href)
    if classified is None or classified[0] == "":
        return None
    path = classified[0]
    base = site_root if path.startswith("/") else page_dir
    resolved = (base / path.lstrip("/")).resolve()
    if _under(site_root, resolved) and resolved != site_root and resolved.is_file():
        return resolved
    return None


def touch_icon_findings(label, page_rel, icons, page_dir, site_root):
    """Findings for one page's Apple touch icon links. `label` names the page in a finding, `page_rel` is
    its path relative to its root, `icons` its (href, line) list. A page under the root's top-level draft/
    directory (the /draft/* of _headers) is not checked; any other page needs at least one link, and every
    link must name an existing file. A 404 page is served at any missing URL, however deep, so its links
    must be root-absolute: a relative one resolves below the root there."""
    parts = Path(page_rel).parts
    if len(parts) > 1 and parts[0] == DRAFT_DIR:
        return []
    if not icons:
        return ['{}: missing <link rel="{}"> (every live page links the touch icon)'.format(
            label, TOUCH_ICON_REL)]
    out = []
    for href, line in icons:
        classified = classify(href)
        split = urlsplit(href)
        if split.netloc and not split.scheme:
            out.append("{}:{}: {} is a protocol-relative URL (//host/...), not a path to a file under the root; "
                       "it must be a root-absolute path -> {!r}".format(label, line, TOUCH_ICON_REL, href))
        elif parts[-1] == NOT_FOUND_PAGE and classified is not None and classified[0] != "" \
                and not classified[0].startswith("/"):
            out.append("{}:{}: {} on a 404 page is relative; a 404 page is served at any path, so it must "
                       "be root-absolute -> {!r}".format(label, line, TOUCH_ICON_REL, href))
        elif _touch_icon_file(href, page_dir, site_root) is None:
            out.append("{}:{}: {} names a file that does not exist under the root -> {!r}".format(
                label, line, TOUCH_ICON_REL, href))
    return out


def _self_test():
    good = '<a class="logo" href="/">L</a>'
    bad = '<a class="logo" href="/">L&trade;</a>'
    failures = []
    cases = [
        ("all identical -> no finding", [("a.html", good), ("b.html", good), ("c.html", good)], 0),
        ("one outlier -> one finding", [("a.html", good), ("b.html", good), ("c.html", bad)], 1),
        ("page without a logo is not a finding", [("a.html", good), ("b.html", "<p>no logo</p>")], 0),
        ("empty set -> no finding", [], 0),
    ]
    for label, pages, want in cases:
        got = len(logo_findings(pages))
        if got != want:
            failures.append("{}: expected {} finding(s), got {}".format(label, want, got))
    # the outlier must be the one named (canonical is the majority)
    named = logo_findings([("keep.html", good), ("keep2.html", good), ("drift.html", bad)])
    if not (len(named) == 1 and named[0].startswith("drift.html")):
        failures.append("outlier naming: expected drift.html named, got {}".format(named))
    # A 404 page must open its logo with the root-absolute home link where the canonical is relative; any
    # other page may not, and a 404 page that differs in any other byte is still drift.
    rel_logo = '<a class="logo" href="./">L</a>'
    root_logo = '<a class="logo" href="/">L</a>'
    not_found_cases = [
        ("404 page with root-absolute home on a relative site", [("a.html", rel_logo), ("404.html", root_logo)],
         []),
        ("nested 404 page with root-absolute home", [("a.html", rel_logo), ("b/404.html", root_logo)], []),
        ("404 page with the canonical relative home", [("a.html", rel_logo), ("404.html", rel_logo)],
         ["404.html"]),
        ("nested 404 page with the canonical relative home", [("a.html", rel_logo), ("b/404.html", rel_logo)],
         ["b/404.html"]),
        ("only 404 pages, relative home", [("404.html", rel_logo), ("b/404.html", rel_logo)],
         ["404.html", "b/404.html"]),
        ("404 page with root-absolute home on a root-absolute site",
         [("a.html", root_logo), ("404.html", root_logo)], []),
        ("non-404 page with root-absolute home on a relative site",
         [("a.html", rel_logo), ("b.html", rel_logo), ("c.html", root_logo)], ["c.html"]),
        ("404 page with root-absolute home and other drift",
         [("a.html", rel_logo), ("404.html", '<a class="logo" href="/">L&trade;</a>')], ["404.html"]),
        ("404 majority does not set the canonical",
         [("a.html", rel_logo), ("404.html", bad), ("x/404.html", bad)], ["404.html", "x/404.html"]),
    ]
    for label, pages, want in not_found_cases:
        got = [line.split(":")[0] for line in logo_findings(pages)]
        if got != want:
            failures.append("{}: expected {} named, got {}".format(label, want, got))
    # AIQT_SITE_HOST derives SITE_HOSTS at import, so probe it in a child process under each env value.
    # Unset/empty -> default aiqt.ai (unchanged from today); a set host -> that host and its www.; the
    # env host is lowercased; a no-dot host derives its own exact pair.
    import subprocess
    tools_dir = str(Path(__file__).resolve().parent)
    probe = ("import sys; sys.path.insert(0, {!r}); import check_site; "
             "print(' '.join(sorted(check_site.SITE_HOSTS)))".format(tools_dir))

    def hosts_under(env_val):
        env = dict(os.environ)
        if env_val is None:
            env.pop("AIQT_SITE_HOST", None)
        else:
            env["AIQT_SITE_HOST"] = env_val
        out = subprocess.run([sys.executable, "-I", "-c", probe], capture_output=True, text=True,
                             env=env, check=True)
        return set(out.stdout.split())

    host_cases = [
        ("unset -> default aiqt.ai", None, {"aiqt.ai", "www.aiqt.ai"}),
        ("empty -> fallback aiqt.ai", "", {"aiqt.ai", "www.aiqt.ai"}),
        ("set host -> host + www.", "example.test", {"example.test", "www.example.test"}),
        ("host lowercased", "EXAMPLE.TEST", {"example.test", "www.example.test"}),
        ("no-dot host derives its own pair", "localhost", {"localhost", "www.localhost"}),
    ]
    for label, env_val, want in host_cases:
        got = hosts_under(env_val)
        if got != want:
            failures.append("SITE_HOSTS {}: expected {} got {}".format(label, sorted(want), sorted(got)))

    # Coverage-root integration: _scan_root treats opf/site as its own link-containment boundary, so a
    # nested draft page's relative link resolves within opf/site (clean) while a dangling one is a finding;
    # main() skips absent roots (both absent -> clean pass). Output captured so it does not leak here.
    import tempfile
    import contextlib
    import io

    def quiet(fn, *a):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return fn(*a)
    with tempfile.TemporaryDirectory() as d:
        r = Path(d)
        draft = r / "opf" / "site" / "draft"
        draft.mkdir(parents=True)
        (r / "opf" / "site" / "touch.png").write_bytes(b"png")
        (r / "opf" / "site" / "index.html").write_text(
            '<!doctype html><title>t</title><link rel="apple-touch-icon" href="/touch.png"><nav></nav>',
            encoding="utf-8")
        (draft / "disclosure.html").write_text("<!doctype html><title>t</title><p>d</p>", encoding="utf-8")
        (draft / "manifest.html").write_text(
            '<!doctype html><title>t</title><nav><a href="./disclosure">D</a></nav>', encoding="utf-8")
        if quiet(_scan_root, r, "opf/site") != 0:
            failures.append("coverage: a clean opf/site tree with relative links did not pass")
        (draft / "broken.html").write_text(
            '<!doctype html><title>t</title><a href="./nope">x</a>', encoding="utf-8")
        if quiet(_scan_root, r, "opf/site") != 1:
            failures.append("coverage: a dangling relative link under opf/site/draft was not caught")
    with tempfile.TemporaryDirectory() as d2:
        if quiet(run, Path(d2)) != 0:
            failures.append("coverage: neither root present did not pass clean")

    _touch_icon_self_test(quiet)
    _not_found_links_self_test()
    failures.extend(FAILURES)

    if failures:
        print("SELF-TEST FAIL:")
        for x in failures:
            print("  - " + x)
        return 1
    print("PASS: check_site self-test ({} logo cases + {} 404-page logo cases + {} SITE_HOSTS env cases + "
          "coverage-root legs + {} registered checks)".format(len(cases), len(not_found_cases),
                                                             len(host_cases), len(EXECUTED)))
    return 0


# The registered execution-suite identity (tools/selftest_checks.toml) and the executed-check-id record
# the instrumented check() helper appends to; the execution gate reconciles the report against the
# registry.
SUITE_ID = "site-selftest"
EXECUTED = []
FAILURES = []


def check(name, ok, detail=""):
    if name in EXECUTED:
        print("SELF-TEST HARNESS ERROR: duplicate check id {!r}".format(name), file=sys.stderr)
        sys.exit(2)
    EXECUTED.append(name)
    if not ok:
        FAILURES.append("{}: {!r}".format(name, detail))


def _touch_icon_self_test(quiet):
    """The touch-icon check: a live page passes only with a link naming an existing file under its root;
    a draft page is exempt, a page merely named draft is not, and the scan and the live tree agree."""
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        base = Path(d)
        root = base / "site"
        (root / "assets" / "logos").mkdir(parents=True)
        (root / "sub").mkdir()
        (root / "assets" / "logos" / "t.png").write_bytes(b"png")
        (base / "outside.png").write_bytes(b"png")
        site_root = root.resolve()

        def findings(page_rel, icons):
            return touch_icon_findings(page_rel, page_rel, icons, root / Path(page_rel).parent, site_root)

        got = findings("a.html", [("/assets/logos/t.png", 3)])
        check("touch-icon/root-absolute-href-passes", got == [], got)
        got = findings("sub/a.html", [("../assets/logos/t.png", 3)])
        check("touch-icon/relative-href-passes", got == [], got)
        got = findings("404.html", [("/assets/logos/t.png?v=1#x", 3)])
        check("touch-icon/query-and-fragment-ignored", got == [], got)
        got = findings("a.html", [])
        check("touch-icon/missing-link-fails", len(got) == 1 and "missing <link" in got[0], got)
        got = findings("404.html", [])
        check("touch-icon/404-page-checked", len(got) == 1 and "missing <link" in got[0], got)
        got = findings("a.html", [("/assets/logos/gone.png", 3)])
        check("touch-icon/missing-file-fails", len(got) == 1 and "does not exist" in got[0], got)
        got = findings("a.html", [("/assets/logos/t.png", 3), ("/assets/logos/gone.png", 4)])
        check("touch-icon/second-link-missing-file-fails", len(got) == 1 and "a.html:4:" in got[0], got)
        for name, href in (("touch-icon/directory-href-fails", "/assets/logos/"),
                           ("touch-icon/root-href-fails", "/"),
                           ("touch-icon/empty-href-fails", ""),
                           ("touch-icon/html-fallback-not-taken", "/assets/logos/t"),
                           ("touch-icon/escaping-href-fails", "/../outside.png"),
                           ("touch-icon/external-href-fails", "https://example.test/t.png")):
            got = findings("a.html", [(href, 3)])
            check(name, len(got) == 1 and "does not exist" in got[0], got)
        got = findings("draft/a.html", [])
        check("touch-icon/draft-page-exempt", got == [], got)
        got = findings("draft/sub/a.html", [("/nope.png", 3)])
        check("touch-icon/nested-draft-page-exempt", got == [], got)
        got = findings("draft.html", [])
        check("touch-icon/page-named-draft-checked", len(got) == 1, got)
        got = findings("drafts/a.html", [])
        check("touch-icon/drafts-directory-checked", len(got) == 1, got)
        got = findings("guide/draft/a.html", [])
        check("touch-icon/nested-draft-directory-checked", len(got) == 1 and "missing <link" in got[0], got)
        got = findings("404.html", [("assets/logos/t.png", 3)])
        check("touch-icon/404-relative-href-fails", len(got) == 1 and "must be root-absolute" in got[0], got)
        got = findings("sub/404.html", [("../assets/logos/t.png", 3)])
        check("touch-icon/nested-404-relative-href-fails", len(got) == 1 and "must be root-absolute" in got[0],
              got)
        for name, page_rel in (("touch-icon/404-protocol-relative-href-named", "404.html"),
                               ("touch-icon/nested-404-protocol-relative-href-named", "sub/404.html"),
                               ("touch-icon/protocol-relative-href-named", "a.html")):
            got = findings(page_rel, [("//aiqt.ai/assets/logos/t.png", 3)])
            check(name, len(got) == 1 and "is a protocol-relative URL" in got[0], got)

        page = Page()
        page.feed('<link rel="icon" href="/f.svg"><link rel="Apple-Touch-Icon" href=" /t.png ">'
                  '<link rel="stylesheet" href="/s.css">')
        check("touch-icon/parser-reads-link", page.touch_icons == [("/t.png", 1)], page.touch_icons)

        (root / "index.html").write_text(
            '<!doctype html><title>t</title><link rel="apple-touch-icon" href="/assets/logos/t.png">',
            encoding="utf-8")
        (root / "sub" / "a.html").write_text("<!doctype html><title>t</title><p>x</p>", encoding="utf-8")
        check("touch-icon/scan-reports-missing-link", quiet(_scan_root, base, "site") == 1, None)
        (root / "sub" / "a.html").write_text(
            '<!doctype html><title>t</title><link rel="apple-touch-icon" href="../assets/logos/t.png">',
            encoding="utf-8")
        check("touch-icon/scan-passes-when-linked", quiet(_scan_root, base, "site") == 0, None)

    got = quiet(main)
    check("live/real-tree-passes", got == 0, got)


def _not_found_links_self_test():
    """The 404-page link rule: every relative href or src on a page named 404.html, top-level or nested, is
    a finding even where it resolves from the page's own directory; root-absolute and fragment-only links
    pass there, and the same relative links pass on any other page."""
    import contextlib
    import io
    import tempfile

    def scan(base):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            return _scan_root(base, "site"), out.getvalue()

    icon = '<link rel="apple-touch-icon" href="/t.png">'
    with tempfile.TemporaryDirectory() as d:
        base = Path(d)
        root = base / "site"
        (root / "sub").mkdir(parents=True)
        for directory in (root, root / "sub"):
            for asset in ("s.css", "f.svg", "j.js", "i.png", "l.png", "t.png"):
                (directory / asset).write_bytes(b"x")
            (directory / "b.html").write_text("<!doctype html><title>t</title>" + icon, encoding="utf-8")
        (root / "index.html").write_text('<!doctype html><title>t</title>' + icon +
                                         '<a class="logo" href="./"><img src="l.png" alt=""></a>', encoding="utf-8")

        def page(head, body):
            return "<!doctype html><title>t</title>" + head + "<body>" + body + "</body>"

        stylesheet = '<link rel="stylesheet" href="s.css">'
        favicon = '<link rel="icon" href="f.svg">'
        script = '<script src="j.js"></script>'
        img = '<img src="i.png" alt="">'
        nav = '<nav><a href="b">B</a></nav>'
        logo = '<a class="logo" href="/"><img src="l.png" alt=""></a>'
        for name, page_rel, head, body, value in (
                ("404-links/relative-stylesheet-fails", "404.html", icon + stylesheet, "", "s.css"),
                ("404-links/nested-relative-stylesheet-fails", "sub/404.html", icon + stylesheet, "", "s.css"),
                ("404-links/relative-icon-fails", "404.html", icon + favicon, "", "f.svg"),
                ("404-links/nested-relative-icon-fails", "sub/404.html", icon + favicon, "", "f.svg"),
                ("404-links/relative-script-fails", "404.html", icon + script, "", "j.js"),
                ("404-links/nested-relative-script-fails", "sub/404.html", icon + script, "", "j.js"),
                ("404-links/relative-img-fails", "404.html", icon, img, "i.png"),
                ("404-links/nested-relative-img-fails", "sub/404.html", icon, img, "i.png"),
                ("404-links/relative-nav-link-fails", "404.html", icon, nav, "b"),
                ("404-links/nested-relative-nav-link-fails", "sub/404.html", icon, nav, "b"),
                ("404-links/relative-logo-img-fails", "404.html", icon, logo, "l.png"),
                ("404-links/nested-relative-logo-img-fails", "sub/404.html", icon, logo, "l.png")):
            (root / page_rel).write_text(page(head, body), encoding="utf-8")
            got = scan(base)
            (root / page_rel).unlink()
            check(name, got[0] == 1 and "FAIL: 1 site/" in got[1] and "relative href or src on a 404 page" in got[1]
                  and got[1].rstrip().endswith("-> " + value), got)

        absolute = page(icon + '<link rel="stylesheet" href="/s.css"><link rel="icon" href="/f.svg">'
                        '<script src="/j.js"></script>',
                        '<p id="top"><img src="https://aiqt.ai/i.png" alt=""></p><nav><a href="/b">B</a>'
                        '<a href="#top">T</a></nav><a class="logo" href="/"><img src="/l.png" alt=""></a>')
        (root / "404.html").write_text(absolute, encoding="utf-8")
        (root / "index.html").write_text('<!doctype html><title>t</title>' + icon +
                                         '<a class="logo" href="./"><img src="/l.png" alt=""></a>', encoding="utf-8")
        got = scan(base)
        check("404-links/root-absolute-and-fragment-links-pass", got[0] == 0, got)
        (root / "404.html").unlink()

        (root / "sub" / "c.html").write_text(page(icon + '<link rel="stylesheet" href="s.css">',
                                                  '<img src="i.png" alt=""><nav><a href="b">B</a></nav>'),
                                             encoding="utf-8")
        got = scan(base)
        check("404-links/relative-links-on-other-page-pass", got[0] == 0, got)

        (root / "sub" / "404.html").write_text(page('<link rel="apple-touch-icon" href="t.png">', ""),
                                               encoding="utf-8")
        got = scan(base)
        check("404-links/relative-touch-icon-reported-once", got[0] == 1 and "FAIL: 1 site/" in got[1]
              and "apple-touch-icon on a 404 page is relative" in got[1], got)


# Coverage roots: site/ (aiqt.ai) and opf/site/ (opfiles.ai: the served site pages). Matching this gate's
# existing site/ idiom, each root is OPTIONAL: an absent root is skipped (not fail-closed), so a vendored
# pack with neither still passes; a present root is scanned, and an unreadable present root fails closed.
# Each root is its own link-containment boundary, so a page's relative links (./manifest, ./disclosure)
# resolve within opf/site and never escape.
COVERAGE_ROOTS = ("site", "opf/site")


def run(root):
    """Scan every present coverage root under `root`. Return the worst per-root exit code; roots are
    optional (absent is skipped), a present root is scanned, and an unreadable present root fails closed."""
    present = [sub for sub in COVERAGE_ROOTS if (root / sub).is_dir()]
    if not present:
        print("PASS: no site/ or opf/site/ directory")
        return 0
    worst = 0
    for sub in present:
        worst = max(worst, _scan_root(root, sub))
    return worst


def main():
    return run(Path(__file__).resolve().parents[1])


def _scan_root(root, subdir):
    site = root / subdir
    site_root = site.resolve()
    docs, ids_by_path, findings = [], {}, []
    try:
        html_files = sorted(walk_files(site, suffixes={".html"}))
    except OSError as exc:
        # an unreadable directory under the root is a read error, not a clean skip: fail closed (exit 2)
        # so the site gate never reports clean without having scanned an unreadable subtree.
        print("error: cannot scan {}/ ({}); fail-closed".format(subdir, exc), file=sys.stderr)
        return 2
    for f in html_files:
        rel = f.relative_to(root)
        try:
            text = f.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            findings.append("{}: could not read as UTF-8".format(rel))
            continue
        except OSError as exc:
            print("error: cannot read {} ({}); fail-closed".format(rel, exc), file=sys.stderr)
            return 2
        page = Page()
        try:
            page.feed(text)
            page.finalize()
        except (ValueError, AssertionError):
            page = None
        docs.append((f, rel, text, page))
        ids_by_path[str(f.resolve())] = (page.ids if page else None)
    for f, rel, text, page in docs:
        for number, line in enumerate(text.splitlines(), 1):
            if EN in line:
                findings.append("{}:{}: en dash".format(rel, number))
            if EM in line:
                findings.append("{}:{}: em dash".format(rel, number))
        if page is None:
            findings.append("{}: could not parse as HTML".format(rel))
            continue
        for line, msg in page.tag_findings:
            findings.append("{}:{}: {}".format(rel, line, msg))
        if not page.title_present or not page.title_text.strip():
            findings.append("{}: missing or empty <title>".format(rel))
        seen = {}
        for value, line in page.id_list:
            seen.setdefault(value, []).append(line)
        for value, lines in seen.items():
            if len(lines) > 1:
                findings.append("{}:{}: duplicate id '{}' (repeated at line(s) {})".format(
                    rel, lines[0], value, ", ".join(str(n) for n in lines[1:])))
        for value, line in page.links:
            v = value.strip()
            if not v:
                continue
            classified = classify(v)
            if classified is None:
                continue
            pathpart, frag = classified
            # A 404 page is served at any missing URL, however deep, so a relative href or src there resolves
            # below the root. A relative touch icon is already reported by touch_icon_findings.
            if f.name == NOT_FOUND_PAGE and pathpart and not pathpart.startswith("/") \
                    and (v, line) not in page.touch_icons:
                findings.append("{}:{}: relative href or src on a 404 page; a 404 page is served at any path, so "
                                "it must be root-absolute -> {}".format(rel, line, v))
            if pathpart == "":
                if not frag:
                    continue
                anchor_key = str(f.resolve())
            else:
                base = site_root if pathpart.startswith("/") else f.parent
                target = resolve_link(base, pathpart, site_root)
                if target is None:
                    findings.append("{}:{}: broken internal link -> {}".format(rel, line, v))
                    continue
                anchor_key = str(target)
            if frag:
                ids = ids_by_path.get(anchor_key)
                # Match the id against the RAW fragment first, then its percent-decoded form, mirroring
                # HTML fragment navigation (raw match, else decoded match).
                if ids is not None and frag not in ids and unquote(frag) not in ids:
                    findings.append("{}:{}: broken anchor -> {} (#{} not found)".format(rel, line, v, frag))
        findings.extend(touch_icon_findings(rel, f.relative_to(site), page.touch_icons, f.parent, site_root))
    # Logo consistency (M-04): the header logo block is duplicated per page (no page generator yet), so it
    # must stay byte-identical on every page that carries it; an outlier is drift.
    findings.extend(logo_findings([(str(rel), text) for _f, rel, text, _p in docs]))
    if findings:
        print("FAIL: {} {}/ site-integrity issue(s)".format(len(findings), subdir))
        for finding in sorted(set(findings)):
            print("  " + finding)
        return 1
    print("PASS: {}/ dashes, links, anchors, titles, unique ids, tag structure and touch icons all check out"
          .format(subdir))
    return 0


def _entry(argv):
    # The execution gate appends `--execution-report ABS_PATH` to the self-test launch; accepted only in
    # that trailing two-token form, with an absolute path, alone or after --self-test.
    if len(argv) >= 2 and argv[-2] == "--execution-report" and os.path.isabs(argv[-1]) \
            and argv[:-2] in ([], ["--self-test"]):
        # Finalized at interpreter exit by tools/_selftest_exit_report.py; nothing writes it in band.
        import _selftest_exit_report
        _selftest_exit_report.arm(argv[-1], SUITE_ID, EXECUTED)
        return _self_test()
    if "--self-test" in argv:
        return _self_test()
    return main()


if __name__ == "__main__":
    _code = _entry(sys.argv[1:])
    _finalizer = sys.modules.get("_selftest_exit_report")
    (sys.exit if _finalizer is None else _finalizer.exit_with)(_code)
