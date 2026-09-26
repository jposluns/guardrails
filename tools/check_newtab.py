#!/usr/bin/env python3
"""New-tab gate: every EXTERNAL link in recursively covered HTML opens in a new tab, safely.

Every off-site link (a host other than aiqt.ai or a subdomain) must carry target="_blank" AND a rel that
includes "noopener" (target=_blank without noopener is a reverse-tabnabbing risk). Internal (relative) links
and same-site aiqt.ai links are out of scope. This keeps the new-tab behaviour from silently rotting when a
future hand-authored off-site link is added without the attributes.

Covered navigation surfaces: HTML <a href>, SVG <a xlink:href> (when plain href is absent), and <area href>;
plus any <base href> is flagged fail-closed (it can retarget relative links off-site). Residual (disclosed
per the pack's disclose-guard-residuals rule, and erring toward disclosure rather than silent coverage): the
gate operates on the project's OWN trusted, well-formed HTML and does not cover other or future navigation
surfaces (for example script-driven navigation, or link elements outside the set above). The project authors
none of those today; this is a mistake-catcher for a forgotten new-tab attribute, not an adversarial
validator of attacker-controlled hrefs.

Coverage precedence: AIQT_NEWTAB_ROOTS, when present, is an os.pathsep-separated list
(for example site:docs/site on Linux); otherwise .aiqt/newtab.toml must contain exactly
roots = [...], a nonempty array of unique repository-relative POSIX paths. Entries
are literal, with no globbing or shell expansion. Absolute paths, drive prefixes,
backslashes, control characters, and empty, '.' or '..' components are invalid.
Without an override or declaration, scan the present conventional roots site/ and
opf/site/. At least one must exist. Every selected root must be readable and contain
HTML pages. Declare roots to detect their subsequent removal. Coverage is recursive.
Invalid configuration and missing, unreadable, or page-less selected roots fail
closed (exit 2). A page opened symlink-safe is not needed here (read-only text scan
of trusted pages); this gate retains the site drift gates' traversal.

  check_newtab.py             recursively scan selected roots' .html pages
  check_newtab.py --self-test  present/missing-target/missing-rel/internal-skip/fail-closed cases

Exit 0 clean, 1 on any finding, 2 on a missing/unreadable required input (fail-closed).
"""
import os
import stat
import sys
from html.parser import HTMLParser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _walk import walk_files  # noqa: E402  fail-closed tree walk
from _gen_common import is_external_url, load_toml  # noqa: E402


class _Anchors(HTMLParser):
    """Collect (href, target, rel) for every <a>/<area>/SVG-<a xlink:href> link, and any <base href>."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links = []
        self.base_hrefs = []

    def _first_attrs(self, attrs):
        first = {}
        for k, v in attrs:          # HTML keeps the FIRST duplicate attribute; dict(attrs) would keep the last
            first.setdefault(k, v)
        return first

    def handle_starttag(self, tag, attrs):
        if tag in ("a", "area"):
            first = self._first_attrs(attrs)
            href = first.get("href")
            if href is None and tag == "a":
                href = first.get("xlink:href")   # SVG <a> is navigable via xlink:href when plain href is absent
            if href is not None:
                self.links.append((href, first.get("target") or "", first.get("rel") or ""))
        elif tag == "base":
            href = self._first_attrs(attrs).get("href")
            if href is not None:
                self.base_hrefs.append(href)


def page_findings(name, text):
    """Findings for one page: each external link missing target=_blank or a noopener rel, plus any
    <base href>, which is unsupported because it can silently retarget relative links off-site (the
    project's pages carry no <base>; the gate fails closed on one rather than mis-resolving against it)."""
    parser = _Anchors()
    parser.feed(text)
    out = []
    for base in parser.base_hrefs:
        out.append("{}: unsupported <base href> {!r} (it can retarget relative links off-site; "
                   "the new-tab gate does not resolve against it)".format(name, base))
    for href, target, rel in parser.links:
        if not is_external_url(href):
            continue
        if target.strip() != "_blank":
            out.append("{}: external link {!r} is missing target=\"_blank\"".format(name, href))
        elif "noopener" not in rel.lower().split():
            out.append("{}: external link {!r} has target=_blank without a noopener rel "
                       "(reverse-tabnabbing risk)".format(name, href))
    return out


# Recursive HTML coverage: AIQT_NEWTAB_ROOTS (os.pathsep-separated), then
# .aiqt/newtab.toml (exactly roots = [...]), then present conventional roots.
# Every selected root is required; configured roots are never presence-filtered.
DEFAULT_CANDIDATES = ("site", "opf/site")


def _inspect(root, relative, *, regular=False):
    """Inspect each component without following symlinks; only ENOENT means absent."""
    current = root
    parts = relative.split("/")
    for index, part in enumerate(parts):
        current = current / part
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError:
            return False
        want_file = regular and index == len(parts) - 1
        if stat.S_ISLNK(mode):
            raise ValueError("{}: symlink component {}".format(relative, current))
        if not (stat.S_ISREG(mode) if want_file else stat.S_ISDIR(mode)):
            raise ValueError("{}: {} is not a {}".format(
                relative, current, "regular file" if want_file else "directory"))
    return True


def _validate_roots(values, source):
    if not isinstance(values, list) or not values:
        raise ValueError("{}: roots must be a nonempty array of strings".format(source))
    seen = set()
    for value in values:
        if not isinstance(value, str) or not value:
            raise ValueError("{}: each root must be a nonempty string".format(source))
        drive = (len(value) >= 2 and value[0] in
                 "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ" and value[1] == ":")
        if (value.startswith("/") or drive or "\\" in value
                or any(ord(c) < 32 or 127 <= ord(c) <= 159 for c in value)
                or any(part in ("", ".", "..") for part in value.split("/"))):
            raise ValueError("{}: invalid repository-relative POSIX root {!r}".format(source, value))
        if value in seen:
            raise ValueError("{}: duplicate root {!r}".format(source, value))
        seen.add(value)
    return tuple(values)


def _coverage_roots(root):
    """Resolve once at call time; explicit sources replace lower-precedence sources.

    Discovery cannot detect removal of an undeclared optional root or find arbitrary
    website locations. An override can intentionally narrow coverage. This repository's
    declaration supplies its required-root contract. Inspection is not race-proof;
    the existing trusted-HTML/navigation-surface limitations still apply.
    """
    if "AIQT_NEWTAB_ROOTS" in os.environ:
        return _validate_roots(os.environ["AIQT_NEWTAB_ROOTS"].split(os.pathsep),
                               "AIQT_NEWTAB_ROOTS")
    declaration = ".aiqt/newtab.toml"
    try:
        if _inspect(root, declaration, regular=True):
            data = load_toml(root / declaration)
            if set(data) != {"roots"}:
                raise ValueError("expected exactly roots = [...]")
            return _validate_roots(data["roots"], declaration)
    except (OSError, ValueError) as exc:
        raise ValueError("{}: {}".format(declaration, exc)) from exc
    selected = tuple(path for path in DEFAULT_CANDIDATES if _inspect(root, path))
    if not selected:
        raise ValueError("no conventional coverage roots present (site/, opf/site/)")
    return selected


def _run_one(root, subdir):
    site = root / subdir
    try:
        if not _inspect(root, subdir):
            raise ValueError("{}/ absent".format(subdir))
        html_files = sorted(walk_files(site, suffixes={".html"}))
    except (OSError, ValueError) as exc:
        print("error: cannot scan {}/ ({}); fail-closed".format(subdir, exc), file=sys.stderr)
        return 2
    if not html_files:
        print("error: {}/ contains no .html pages; a page-less required input is fail-closed".format(subdir),
              file=sys.stderr)
        return 2
    findings = []
    for f in html_files:
        name = str(f.relative_to(root))
        try:
            findings += page_findings(name, f.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError) as exc:
            print("error: cannot load {} ({}); fail-closed".format(f.relative_to(root), exc), file=sys.stderr)
            return 2
    if findings:
        print("FAIL: {} external link(s) under {}/ not opening safely in a new tab".format(len(findings), subdir))
        for x in sorted(set(findings)):
            print("  " + x)
        return 1
    print("PASS: every external {}/ link opens in a new tab with a noopener rel".format(subdir))
    return 0


def run(root):
    """Recursively scan roots selected by environment, declaration, or present defaults.

    AIQT_NEWTAB_ROOTS uses os.pathsep; .aiqt/newtab.toml uses roots = [...].
    Without either, scan present site/ and opf/site/; at least one must exist.
    Every selected root is required. Return the maximum per-root exit code.
    """
    try:
        roots = _coverage_roots(root)
    except (OSError, ValueError) as exc:
        print("error: cannot resolve NEWTAB coverage ({}); fail-closed".format(exc), file=sys.stderr)
        return 2
    worst = 0
    for subdir in roots:
        worst = max(worst, _run_one(root, subdir))
    return worst


# Synthetic declaration for portable self-tests, independent of repository config.
_SELF_TEST_DECLARATION = b'roots = ["site", "opf/site"]\n'


def _self_test():
    ext_ok = '<a href="https://github.com/x" target="_blank" rel="noopener noreferrer">gh</a>'
    cases = [
        ("external ok", ext_ok, []),
        ("external missing target",
         '<a href="https://github.com/x" rel="noopener">gh</a>',
         ['p.html: external link \'https://github.com/x\' is missing target="_blank"']),
        ("external target no noopener",
         '<a href="https://github.com/x" target="_blank" rel="noreferrer">gh</a>',
         ["p.html: external link 'https://github.com/x' has target=_blank without a noopener rel "
          "(reverse-tabnabbing risk)"]),
        ("internal link skipped", '<a href="/mappings">m</a>', []),
        ("aiqt.ai absolute skipped", '<a href="https://aiqt.ai/x">x</a>', []),
        ("aiqt.ai subdomain-prefix host is EXTERNAL",
         '<a href="https://aiqt.ai.evil.example/x">x</a>',
         ['p.html: external link \'https://aiqt.ai.evil.example/x\' is missing target="_blank"']),
        ("aiqt.ai in userinfo, host external",
         '<a href="https://aiqt.ai@evil.example/x">x</a>',
         ['p.html: external link \'https://aiqt.ai@evil.example/x\' is missing target="_blank"']),
        ("aiqt.ai in path, host external",
         '<a href="https://evil.example/path/aiqt.ai">x</a>',
         ['p.html: external link \'https://evil.example/path/aiqt.ai\' is missing target="_blank"']),
        ("uppercase scheme external",
         '<a href="HTTPS://evil.example/x">x</a>',
         ['p.html: external link \'HTTPS://evil.example/x\' is missing target="_blank"']),
    ]
    # Fail-closed classes (round-5, Codex): a browser-divergent backslash authority, a malformed
    # http(s) URL, and a protocol-relative URL each classify EXTERNAL, so an unattributed one is a
    # finding. Expected strings are computed with %r to match page_findings' own {!r} formatting.
    for _h in ("https://evil.example\\@aiqt.ai/x", "https://[invalid/x", "//evil.example/x"):
        cases.append(("fail-closed external %r" % _h, '<a href="%s">x</a>' % _h,
                      ['p.html: external link %r is missing target="_blank"' % _h]))
    # A genuine aiqt.ai host behind userinfo is internal (a browser agrees: host is aiqt.ai).
    cases.append(("userinfo with genuine aiqt.ai host is internal",
                  '<a href="https://user@aiqt.ai/x">x</a>', []))
    # Duplicate attributes: HTML keeps the FIRST (browser behaviour); the gate must classify on it.
    cases.append(("duplicate href: first (external) wins -> finding",
                  '<a href="https://evil.example/x" href="/internal">x</a>',
                  ['p.html: external link \'https://evil.example/x\' is missing target="_blank"']))
    cases.append(("duplicate target: first (_blank) wins -> external link is safe, no finding",
                  '<a href="https://evil.example/x" target="_blank" target="_self" rel="noopener">x</a>', []))
    # Missing-solidus special-scheme (WHATWG normalizes to '//'): each resolves to an off-site host.
    for _h in ("http:/evil.example/x", "http:\\evil.example/x", "http:evil.example/x"):
        cases.append(("missing-solidus external %r" % _h, '<a href="%s">x</a>' % _h,
                      ['p.html: external link %r is missing target="_blank"' % _h]))
    # Same-SITE links (other scheme/port on aiqt.ai, or a subdomain) are internal by design: no finding.
    for _h in ("http://aiqt.ai/x", "https://aiqt.ai:444/x", "https://sub.aiqt.ai/x"):
        cases.append(("same-site internal %r" % _h, '<a href="%s">x</a>' % _h, []))
    # A confusable registrable domain is EXTERNAL (pins the .aiqt.ai dot-boundary; endswith('aiqt.ai') would miss it).
    for _h in ("https://notaiqt.ai/x", "https://xaiqt.ai/x"):
        cases.append(("confusable registrable domain external %r" % _h, '<a href="%s">x</a>' % _h,
                      ['p.html: external link %r is missing target="_blank"' % _h]))
    # <base href> is unsupported: it can retarget relative links off-site, so the gate fails closed on it.
    cases.append(("base href flagged fail-closed",
                  '<base href="https://evil.example/root/"><a href="child">x</a>',
                  ["p.html: unsupported <base href> 'https://evil.example/root/' (it can retarget "
                   "relative links off-site; the new-tab gate does not resolve against it)"]))
    # SVG <a xlink:href> is navigable when plain href is absent; an off-site one needs the attrs.
    cases.append(("svg anchor via xlink:href external -> finding",
                  '<svg><a xlink:href="https://evil.example/x"><text>x</text></a></svg>',
                  ['p.html: external link \'https://evil.example/x\' is missing target="_blank"']))
    cases.append(("svg anchor plain href wins over xlink:href (mutation-sensitive)",
                  '<svg><a href="/internal" xlink:href="https://evil.example/x"><text>x</text></a></svg>',
                  []))
    # <area href> (image map) is a link too; an off-site one needs the attrs.
    cases.append(("area href external -> finding",
                  '<map><area href="https://evil.example/x"></map>',
                  ['p.html: external link \'https://evil.example/x\' is missing target="_blank"']))
    failures = []
    for label, html, expected in cases:
        got = sorted(page_findings("p.html", html))
        if got != sorted(expected):
            failures.append("{}: expected {} got {}".format(label, sorted(expected), got))
    # AIQT_SITE_HOST: is_external_url derives the site host at call time (default, and empty-value
    # fallback, aiqt.ai; lowercased). Behaviour is unchanged when the variable is unset or empty.
    import os
    env_cases = [
        # (AIQT_SITE_HOST value or None=unset, href, expected is_external_url)
        (None, "https://aiqt.ai/x", False),                 # (a) unset -> default aiqt.ai internal
        (None, "https://example.test/x", True),             #     non-aiqt host external
        ("example.test", "https://example.test/x", False),  # (b) set host internal
        ("example.test", "https://www.example.test/x", False),  #     its www. subdomain internal
        ("example.test", "https://aiqt.ai/x", True),        #     aiqt.ai now external
        ("", "https://example.test/x", True),               # (c) empty -> fallback aiqt.ai (not all-internal)
        ("", "https://aiqt.ai/x", False),                   #     aiqt.ai still internal under fallback
        ("EXAMPLE.TEST", "https://example.test/x", False),  #     env host lowercased
        ("localhost", "https://localhost/x", False),        # (d) no-dot host: exact match internal
        ("localhost", "https://aiqt.ai/x", True),           #     other host external
    ]
    _saved = os.environ.get("AIQT_SITE_HOST")
    try:
        for env_val, href, want_ext in env_cases:
            if env_val is None:
                os.environ.pop("AIQT_SITE_HOST", None)
            else:
                os.environ["AIQT_SITE_HOST"] = env_val
            got_ext = is_external_url(href)
            if got_ext != want_ext:
                failures.append("AIQT_SITE_HOST={!r} is_external_url({!r}): expected {} got {}".format(
                    env_val, href, want_ext, got_ext))
    finally:
        if _saved is None:
            os.environ.pop("AIQT_SITE_HOST", None)
        else:
            os.environ["AIQT_SITE_HOST"] = _saved
    # Filesystem legs use TMPDIR when the caller supplies a per-worker scratch root.
    import builtins
    import contextlib
    import io
    import json
    import subprocess
    import tempfile
    import tomllib
    from unittest.mock import patch

    # Synthetic declaration bytes: the portable self-test never reads repository
    # configuration. The live gate and review own this repository's root contract.
    declaration_bytes = _SELF_TEST_DECLARATION
    declared = tomllib.loads(declaration_bytes.decode("utf-8"))

    safe = "<p>safe</p>"
    unsafe = '<a href="https://newtab.invalid/x">unsafe</a>'

    @contextlib.contextmanager
    def fixture(pages=("site/index.html", "opf/site/index.html"),
                declaration=None, override=None):
        # Each case isolates and restores the override, including explicitly empty values.
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ):
            os.environ.pop("AIQT_NEWTAB_ROOTS", None)
            os.environ.pop("AIQT_SITE_HOST", None)
            if override is not None:
                os.environ["AIQT_NEWTAB_ROOTS"] = override
            r = Path(directory)
            for name in pages:
                path = r / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(safe, encoding="utf-8")
            if declaration is not None:
                (r / ".aiqt").mkdir()
                (r / ".aiqt/newtab.toml").write_bytes(declaration)
            yield r

    def expect(label, r, wanted, mention="", single=None):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                got = run(r) if single is None else _run_one(r, single)
            except Exception as exc:
                got = "{}: {}".format(type(exc).__name__, exc)
        diagnostic = out.getvalue() + err.getvalue()
        if got != wanted or mention not in diagnostic:
            failures.append("{}: expected {} and {!r}, got {!r}: {}".format(
                label, wanted, mention, got, diagnostic))

    # Copy only the gate and its import dependencies, never repository config.
    # runpy supplies a private recursion guard only to the child self-test; no
    # inherited environment variable can suppress these adopter discriminators.
    if not globals().get("_NEWTAB_SELFTEST_CHILD", False):
        tools_dir = Path(__file__).resolve().parent
        child_self_test = (
            "import runpy, sys; script = sys.argv.pop(1); "
            "runpy.run_path(script, run_name='__main__', "
            "init_globals={'_NEWTAB_SELFTEST_CHILD': True})"
        )
        layouts = (
            ("undeclared site-only", ("site",), None),
            ("declared site-only", ("site",), b'roots = ["site"]\n'),
            ("declared custom roots", ("docs/site", "manual/site"),
             b'roots = ["docs/site", "manual/site"]\n'),
        )
        for label, roots, config in layouts:
            pages = tuple(path + "/index.html" for path in roots)
            with fixture(pages=pages, declaration=config) as r:
                copied_tools = r / "tools"
                copied_tools.mkdir()
                for name in ("check_newtab.py", "_walk.py", "_gen_common.py"):
                    (copied_tools / name).write_bytes((tools_dir / name).read_bytes())
                unrelated_cwd = r / "unrelated"
                unrelated_cwd.mkdir()
                script = str(copied_tools / "check_newtab.py")
                commands = (
                    ("--self-test",
                     [sys.executable, "-I", "-B", "-c", child_self_test,
                      script, "--self-test"],
                     ("PASS: check_newtab self-test",)),
                    ("live", [sys.executable, "-I", "-B", script],
                     tuple("PASS: every external {}/ link".format(path) for path in roots)),
                )
                for mode, command, expected_output in commands:
                    try:
                        result = subprocess.run(command, cwd=unrelated_cwd,
                                                capture_output=True, text=True, timeout=30)
                    except (OSError, subprocess.SubprocessError) as exc:
                        failures.append("copied-tools {} {}: {}".format(label, mode, exc))
                        continue
                    if (result.returncode != 0 or
                            any(text not in result.stdout for text in expected_output)):
                        failures.append("copied-tools {} {}: expected exit 0 and {}, "
                                        "got {}: {}{}".format(
                                            label, mode, expected_output, result.returncode,
                                            result.stdout, result.stderr))
                    else:
                        print("PASS: copied-tools {} {}".format(label, mode))

    # Existing absent/page-less/safe/unsafe single-root expectations remain.
    with fixture(pages=()) as r:
        expect("absent single root", r, 2, "site", single="site")
        (r / "site").mkdir()
        expect("page-less single root", r, 2, "no .html", single="site")
        (r / "site/a.html").write_text(ext_ok, encoding="utf-8")
        expect("safe single root", r, 0, single="site")
        (r / "site/b.html").write_text(unsafe, encoding="utf-8")
        expect("unsafe single root", r, 1, "site/b.html", single="site")

    with fixture(pages=("site/index.html",)) as r:
        expect("undeclared site-only adopter", r, 0)
    with fixture(pages=("opf/site/index.html",)) as r:
        expect("undeclared OPF-only adopter", r, 0)
    with fixture(pages=()) as r:
        expect("no conventional roots", r, 2)
    with fixture() as r:
        expect("both present defaults", r, 0)

    with fixture(declaration=declaration_bytes) as r:
        expect("synthetic declaration accepted", r, 0)
    for missing in declared["roots"]:
        pages = tuple(path + "/index.html" for path in declared["roots"] if path != missing)
        with fixture(pages=pages, declaration=declaration_bytes) as r:
            expect("declared missing " + missing, r, 2, missing)
    for bad_page in ("site/deep/bad.html", "opf/site/draft/bad.html"):
        with fixture(declaration=declaration_bytes) as r:
            page = r / bad_page
            page.parent.mkdir(parents=True, exist_ok=True)
            page.write_text(unsafe, encoding="utf-8")
            expect("recursive declared coverage " + bad_page, r, 1, bad_page)
    with fixture(declaration=b'roots = ["site", "opf/site", "docs/site"]\n') as r:
        expect("required custom declaration", r, 2, "docs/site")
    with fixture(pages=("docs/site/index.html",),
                 declaration=b'roots = ["docs/site"]\n') as r:
        expect("custom declaration accepted", r, 0)

    with fixture(pages=("site/index.html",), declaration=declaration_bytes,
                 override="site") as r:
        expect("override replaces declaration", r, 0)
    with fixture(override="docs/site") as r:
        (r / "docs/site").mkdir(parents=True)
        (r / "docs/site/bad.html").write_text(unsafe, encoding="utf-8")
        expect("override selects custom input", r, 1, "docs/site/bad.html")
    # On Linux os.pathsep == ":", so this cannot distinguish os.pathsep from
    # a hardcoded colon. A Windows CI leg would strengthen that discriminator.
    with fixture(pages=("custom/one/index.html", "custom/two/index.html"),
                 override=os.pathsep.join(("custom/one", "custom/two"))) as r:
        (r / "custom/two/index.html").write_text(unsafe, encoding="utf-8")
        expect("override scans second entry", r, 1, "custom/two/index.html")
    with fixture(override="docs/missing") as r:
        expect("missing override root", r, 2, "docs/missing")
    with fixture(declaration=b"invalid TOML", override="site") as r:
        expect("override ignores malformed lower source", r, 0)

    # Literal handling and root binding: these paths must not expand to another location.
    for literal in ("literal/*", "literal/$HOME", "literal/~"):
        for source in ("declaration", "environment"):
            config = ("roots = " + json.dumps([literal]) + "\n").encode()
            with fixture(pages=(literal + "/index.html",),
                         declaration=config if source == "declaration" else None,
                         override=literal if source == "environment" else None) as r:
                expect("literal " + source + " " + literal, r, 0)

    # Validation occurs before existence filtering, even with both defaults safe.
    invalid = ("", "/absolute", "C:/drive", "z:drive", "a\\b", "a//b",
               ".", "..", "./site", "../site", "a/./b", "a/../b", "site/",
               "a\nb", "a\tb", "a\x7fb", "a\x85b")
    for value in invalid:
        config = ("roots = " + json.dumps([value]) + "\n").encode()
        with fixture(declaration=config) as r:
            expect("invalid declared path " + repr(value), r, 2, ".aiqt/newtab.toml")
        # A separator is syntax in the environment, so drive-prefix strings there
        # may represent multiple entries; declaration entries exercise that rejection.
        if os.pathsep not in value:
            with fixture(override=value) as r:
                expect("invalid override path " + repr(value), r, 2, "AIQT_NEWTAB_ROOTS")
    for raw in (b"", b"roots = [", b"\xff", b"other = []\n", b"roots = []\n",
                b'roots = "site"\n', b"roots = [1]\n", b"roots = [true]\n",
                b'roots = ["site", "site"]\n', b'roots = ["site"]\nextra = 1\n'):
        with fixture(declaration=raw) as r:
            expect("invalid declaration " + repr(raw), r, 2, ".aiqt/newtab.toml")
    for value in (os.pathsep.join(("site", "site")),
                  os.pathsep + "site", "site" + os.pathsep):
        with fixture(override=value) as r:
            expect("invalid override list " + repr(value), r, 2, "AIQT_NEWTAB_ROOTS")

    # Directory/file confusion and live/dangling symlinks at both component depths.
    for relative in ("opf", "opf/site"):
        for kind in ("file", "symlink", "dangling"):
            with fixture(pages=("site/index.html",)) as r:
                path = r / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                if kind == "file":
                    path.write_text("wrong type", encoding="utf-8")
                else:
                    path.symlink_to(r / ("site" if kind == "symlink" else "absent"),
                                    target_is_directory=True)
                expect("discovery " + relative + " " + kind, r, 2, "opf/site")
    for relative in (".aiqt", ".aiqt/newtab.toml"):
        for kind in ("wrong-type", "symlink", "dangling"):
            with fixture() as r:
                path = r / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                if kind == "wrong-type":
                    if relative == ".aiqt":
                        path.write_text("wrong type", encoding="utf-8")
                    else:
                        path.mkdir()
                else:
                    target = r / ("site" if relative == ".aiqt" else "site/index.html")
                    path.symlink_to(target if kind == "symlink" else r / "absent")
                expect("configuration " + relative + " " + kind, r, 2, ".aiqt/newtab.toml")
    for source in ("declaration", "environment"):
        config = b'roots = ["alias/site"]\n'
        with fixture(declaration=config if source == "declaration" else None,
                     override="alias/site" if source == "environment" else None) as r:
            (r / "alias").symlink_to(r / "opf", target_is_directory=True)
            expect("configured intermediate symlink " + source, r, 2, "alias/site")

    # Selected empty/binary pages and max-exit aggregation (input failure beats finding).
    for source in ("declaration", "environment"):
        with fixture(declaration=declaration_bytes if source == "declaration" else None,
                     override=os.pathsep.join(declared["roots"]) if source == "environment" else None) as r:
            (r / "opf/site/index.html").unlink()
            expect("selected page-less " + source, r, 2, "opf/site")
            (r / "opf/site/index.html").write_bytes(b"\xff")
            expect("selected non-UTF-8 " + source, r, 2, "opf/site/index.html")
    with fixture(pages=("site/index.html",), declaration=declaration_bytes) as r:
        (r / "site/index.html").write_text(unsafe, encoding="utf-8")
        expect("finding followed by missing input", r, 2, "opf/site")
    with fixture(pages=("opf/site/index.html",), declaration=declaration_bytes) as r:
        (r / "opf/site/index.html").write_text(unsafe, encoding="utf-8")
        expect("missing input followed by finding", r, 2, "opf/site/index.html")

    # Inject at the actual I/O boundary; do not depend on chmod under a privileged user.
    original_lstat = Path.lstat
    original_open = Path.open
    original_builtin_open = builtins.open
    for stage in ("discovery", "selected", "configuration-inspection",
                  "configuration-read", "traversal", "html-read"):
        config = declaration_bytes if stage in ("selected", "configuration-read") else None
        with fixture(declaration=config) as r:
            target = r / {
                "discovery": "opf", "selected": "opf/site",
                "configuration-inspection": ".aiqt", "configuration-read": ".aiqt/newtab.toml",
                "traversal": "opf/site", "html-read": "opf/site/index.html",
            }[stage]
            touched = []

            def deny_lstat(path, *args, **kwargs):
                if path == target:
                    touched.append(path)
                    raise PermissionError("injected: " + str(path))
                return original_lstat(path, *args, **kwargs)

            def deny_open(path, *args, **kwargs):
                if path == target:
                    touched.append(path)
                    raise PermissionError("injected: " + str(path))
                return original_open(path, *args, **kwargs)

            def deny_builtin_open(path, *args, **kwargs):
                if Path(path) == target:
                    touched.append(path)
                    raise PermissionError("injected: " + str(path))
                return original_builtin_open(path, *args, **kwargs)

            original_walk = walk_files

            def deny_walk(path, *args, **kwargs):
                if path == target:
                    touched.append(path)
                    raise PermissionError("injected: " + str(path))
                yield from original_walk(path, *args, **kwargs)

            if stage in ("discovery", "selected", "configuration-inspection"):
                fault = patch.object(Path, "lstat", deny_lstat)
            elif stage == "configuration-read":
                fault = patch.object(builtins, "open", deny_builtin_open)
            elif stage == "html-read":
                fault = patch.object(Path, "open", deny_open)
            else:
                fault = patch.dict(globals(), walk_files=deny_walk)
            with fault:
                expect("permission failure " + stage, r, 2, "injected:")
            if not touched:
                failures.append("permission injection not exercised: " + stage)
    if failures:
        print("FAIL: check_newtab self-test")
        for x in failures:
            print("  " + x)
        return 1
    print("PASS: check_newtab self-test ({} page cases + {} AIQT_SITE_HOST cases + run() exit-code "
          "legs)".format(len(cases), len(env_cases)))
    return 0


def main():
    if "--self-test" in sys.argv[1:]:
        return _self_test()
    return run(Path(__file__).resolve().parents[1])


if __name__ == "__main__":
    sys.exit(main())
