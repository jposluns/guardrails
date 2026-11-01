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

Link-preview head block (site/ only): every site/*.html page, hand-maintained and generated alike, except
the pages HEAD_TAG_EXEMPT names with their reasons, goes through gen_site._validate_head_tags, the one
shared check, with that page's own URL (gen_site.page_url). The page set is read from the site/
directory listing, so a new page is covered without editing a list; an unreadable listing or page fails
closed (exit 2), and a *.html entry that is a symbolic link or not a regular file is a finding. This gate
runs it because the CI workflow and tools/run_all_checks.sh both run this gate; gen_site.py --check
runs in neither directly. Pages in subdirectories of site/ and the opf/site/ root are not checked for it.
The block is aiqt.ai's, so the head check runs only when the site host (AIQT_SITE_HOST, default aiqt.ai)
is gen_site.SITE_BASE_URL's host, and is skipped under any other AIQT_SITE_HOST.
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
import gen_site  # noqa: E402  the link-preview head block and its one shared per-page check

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
# The coverage root whose top-level pages carry the link-preview head block (tools/card/TAGS.md), and the
# pages in it that do not, each with its reason. This is the one place a page is exempted.
HEAD_TAG_ROOT = "site"
HEAD_TAG_EXEMPT = {
    "404.html": "a noindex page: it is served for any missing path, so it has no URL of its own to name "
                "as canonical, and it is not meant to be indexed or shared",
}


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

    def handle_starttag(self, tag, attrs):
        if self.suspend_tag is not None:
            if tag == self.suspend_tag:
                self.suspend_count += 1
            return
        d = dict(attrs)
        for key in ("href", "src"):
            if d.get(key):
                self.links.append((d[key], self.getpos()[0]))
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


def logo_findings(pages):
    """Findings for header-logo drift across pages. `pages` is a list of (rel, text). The logo block is
    duplicated per page (no page generator yet), so every page that carries one must carry the SAME bytes;
    any block differing from the majority (canonical) is reported. A page without a logo is not a finding."""
    logos = {}
    for rel, text in pages:
        match = _LOGO_RE.search(text)
        if match:
            logos.setdefault(match.group(0), []).append(rel)
    if len(logos) <= 1:
        return []
    canonical = max(logos, key=lambda block: len(logos[block]))
    out = []
    for block, rels in logos.items():
        if block == canonical:
            continue
        for rel in rels:
            out.append("{}: header logo block differs from the canonical logo (logos must be "
                       "byte-identical across pages)".format(rel))
    return out


def head_tag_findings(site, subdir):
    """Findings for the link-preview head block over every top-level *.html page of `site` (named
    `subdir` in messages) except HEAD_TAG_EXEMPT, each through gen_site._validate_head_tags with the
    page's own URL. Returns (findings, error); error is set when the listing or a page cannot be read,
    and the caller fails closed (exit 2) on it."""
    try:
        with os.scandir(site) as listing:
            entries = sorted(listing, key=lambda e: e.name)
    except OSError as exc:
        return [], "cannot list {}/ for the head tag check ({})".format(subdir, exc)
    findings = []
    for entry in entries:
        if not entry.name.endswith(".html") or entry.name in HEAD_TAG_EXEMPT:
            continue
        rel = "{}/{}".format(subdir, entry.name)
        try:
            regular = not entry.is_symlink() and entry.is_file(follow_symlinks=False)
        except OSError as exc:
            return [], "cannot stat {} ({})".format(rel, exc)
        if not regular:
            findings.append("{}: is a symbolic link or not a regular file; its head tags cannot be checked"
                            .format(rel))
            continue
        try:
            text = Path(entry.path).read_text(encoding="utf-8")
        except UnicodeDecodeError:
            findings.append("{}: could not read as UTF-8 for the head tag check".format(rel))
            continue
        except OSError as exc:
            return [], "cannot read {} ({})".format(rel, exc)
        try:
            gen_site._validate_head_tags(text, gen_site.page_url(rel), rel)
        except gen_site.SchemaError as exc:
            findings.append(str(exc))
    return findings, None


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
        (r / "opf" / "site" / "index.html").write_text(
            "<!doctype html><title>t</title><nav></nav>", encoding="utf-8")
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

    # Link-preview head block over a site/ listing: every top-level page but the exempt 404 goes through
    # gen_site's shared check, so a clean set passes, a hand-maintained page missing a tag and a new page
    # with no block are each a finding naming the page, and an unreadable listing fails closed.
    def page_with(url, drop=None):
        tags = [t.format(url=url) for t in gen_site.HEAD_TAG_BLOCK if t != drop]
        return ("<!doctype html><html><head><title>t</title>\n" + "\n".join(tags)
                + "\n</head><body><p>x</p></body></html>")
    twitter_card = '<meta name="twitter:card" content="summary_large_image">'
    head_cases = 0
    with tempfile.TemporaryDirectory() as d3:
        r3 = Path(d3)
        s3 = r3 / "site"
        s3.mkdir()
        (s3 / "index.html").write_text(page_with(gen_site.SITE_BASE_URL), encoding="utf-8")
        (s3 / "mappings.html").write_text(page_with(gen_site.SITE_BASE_URL + "mappings"), encoding="utf-8")
        (s3 / "404.html").write_text('<!doctype html><html><head><title>t</title><meta name="robots" '
                                     'content="noindex"></head><body><p>x</p></body></html>', encoding="utf-8")
        head_cases += 1
        got = head_tag_findings(s3, "site")
        if got != ([], None):
            failures.append("head tags: a clean page set with the exempt 404 did not pass, got {}".format(got))
        head_cases += 1
        if quiet(_scan_root, r3, "site") != 0:
            failures.append("head tags: a clean site/ tree did not pass the gate")
        (s3 / "mappings.html").write_text(
            page_with(gen_site.SITE_BASE_URL + "mappings", drop=twitter_card), encoding="utf-8")
        head_cases += 1
        got, err = head_tag_findings(s3, "site")
        if err or len(got) != 1 or not got[0].startswith("site/mappings.html: ") or "twitter:card" not in got[0]:
            failures.append("head tags: a hand-maintained page missing twitter:card was not named, got {} {}"
                            .format(got, err))
        head_cases += 1
        if quiet(_scan_root, r3, "site") != 1:
            failures.append("head tags: a hand-maintained page missing a tag did not fail the gate")
        (s3 / "mappings.html").write_text(page_with(gen_site.SITE_BASE_URL + "mappings"), encoding="utf-8")
        (s3 / "newpage.html").write_text("<!doctype html><html><head><title>t</title></head><body></body></html>",
                                         encoding="utf-8")
        head_cases += 1
        got, err = head_tag_findings(s3, "site")
        if err or len(got) != 1 or not got[0].startswith("site/newpage.html: "):
            failures.append("head tags: a new page without the block was not named, got {} {}".format(got, err))
        (s3 / "newpage.html").unlink()
        head_cases += 1
        got, err = head_tag_findings(s3 / "absent", "site")
        if got or not err:
            failures.append("head tags: an unreadable listing did not fail closed, got {} {}".format(got, err))
        head_cases += 1
        if quiet(_scan_root, r3, "site") != 0:
            failures.append("head tags: the restored clean site/ tree did not pass the gate")

    # The block is aiqt.ai's: _SITE_HOST is fixed at import, so in a child process under each env value,
    # a site/ whose one page carries no block passes under another host (and its PASS line does not name
    # the head block), and fails with the missing-block finding under the default host.
    scan_probe = ("import sys; from pathlib import Path; sys.path.insert(0, {!r}); import check_site; "
                  "sys.exit(check_site._scan_root(Path(sys.argv[1]), 'site'))".format(tools_dir))
    with tempfile.TemporaryDirectory() as d4:
        s4 = Path(d4) / "site"
        s4.mkdir()
        (s4 / "index.html").write_text("<!doctype html><html><head><title>t</title></head><body><p>x</p>"
                                       "</body></html>", encoding="utf-8")
        for label, env_val, want_rc, needle, absent in (
                ("another host skips the head check", "example.test", 0, "PASS: site/", "head block"),
                ("the default host checks the head block", None, 1, "page must carry exactly one element "
                 "keyed", None)):
            env = dict(os.environ)
            if env_val is None:
                env.pop("AIQT_SITE_HOST", None)
            else:
                env["AIQT_SITE_HOST"] = env_val
            head_cases += 1
            out = subprocess.run([sys.executable, "-I", "-c", scan_probe, d4], capture_output=True,
                                 text=True, env=env)
            if out.returncode != want_rc or needle not in out.stdout or (absent and absent in out.stdout):
                failures.append("head tags: {}: expected exit {} with {!r}, got exit {}: {}".format(
                    label, want_rc, needle, out.returncode, (out.stdout + out.stderr).strip()))

    if failures:
        print("SELF-TEST FAIL:")
        for x in failures:
            print("  - " + x)
        return 1
    print("PASS: check_site self-test ({} logo cases + {} SITE_HOSTS env cases + coverage-root legs + {} "
          "head tag cases)".format(len(cases), len(host_cases), head_cases))
    return 0


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
    # Logo consistency (M-04): the header logo block is duplicated per page (no page generator yet), so it
    # must stay byte-identical on every page that carries it; an outlier is drift.
    findings.extend(logo_findings([(str(rel), text) for _f, rel, text, _p in docs]))
    head_checked = subdir == HEAD_TAG_ROOT and _SITE_HOST == urlsplit(gen_site.SITE_BASE_URL).hostname
    if head_checked:
        head_findings, error = head_tag_findings(site, subdir)
        if error:
            print("error: {}; fail-closed".format(error), file=sys.stderr)
            return 2
        findings.extend(head_findings)
    if findings:
        print("FAIL: {} {}/ site-integrity issue(s)".format(len(findings), subdir))
        for finding in sorted(set(findings)):
            print("  " + finding)
        return 1
    print("PASS: {}/ dashes, links, anchors, titles, unique ids, and tag structure{} all check out"
          .format(subdir, ", and the link-preview head block" if head_checked else ""))
    return 0


if __name__ == "__main__":
    if "--self-test" in sys.argv[1:]:
        sys.exit(_self_test())
    sys.exit(main())
