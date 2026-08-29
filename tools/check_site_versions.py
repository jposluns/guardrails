#!/usr/bin/env python3
"""Site version-currency gate: every `AIQT X.Y.Z` product-version token on the site is current.

changelog.toml is the single source of truth for the pack's released SemVer (the latest [[release]]'s
`version`, the same source check_versions.py reads). This gate asserts that every product-version token
rendered on the site, of the exact form `AIQT X.Y.Z`, names a version that is currently valid, so a stale
version left behind after a release bump is caught and the release process is forced to update the site.

The valid set is the released pack version plus the hand-maintained in-development allowlist below. A token
whose version is outside that set is a finding.

Scope (deliberately narrow, an honest partial currency catcher, not a per-page context checker):
  - It matches ONLY the `AIQT X.Y.Z` product-version pattern. It does NOT touch other phrasings, some of
    which are MATERIALIZED current-version references that would go stale at the next bump and pass unseen:
    the dev-track "(1.1.0, in development)" in titles/og-tags, "1.0.0 chat"/"1.0.0 pack"/"the 1.0.0
    release", a bare version number with no AIQT prefix, or a third-party framework version such as
    "CSA AICM v1.1.0". None carries the `AIQT ` prefix, so none is matched; leaving those other phrasings
    uncovered is a deliberate scope limit, not a claim that all of them are historical.
  - It enforces set-membership (is this version currently valid?), NOT per-page context correctness
    (whether THIS page should name THIS version). It is a partial (class c) currency catcher: it holds
    the site's product-version tokens to the current-version set and no more.

site/ is a required coverage input: an absent, symlinked, unreadable, unlistable, or page-less site/ fails
closed (exit 2), never a clean pass over nothing (per the check-fails-closed-on-unreadable rule and the
check_newtab site-input shape). A non-UTF-8 site html is likewise fail-closed, matching check_newtab and
check_links; it is never silently skipped, and a symlinked site/*.html page fails closed rather than being
followed outside site/.

Disclosed residuals (class c is partial): it scans each source line for `AIQT` + spaces/tabs + a bare
3-segment X.Y.Z, so a token separated from `AIQT` by a MARKUP TAG (`AIQT <strong>1.0.0</strong>`) or a
SOURCE-LINE WRAP (`AIQT` ending one line, the version opening the next) is unmatched even though the page
renders `AIQT 1.0.0`; likewise a token separated by an HTML entity (`AIQT&nbsp;1.0.0`), a `v` prefix
(`AIQT v1.0.0`), or a different case (`aiqt`). A stale value in any such form would pass; none exists in
current site content (verified as of the 2026-08-29 audit). A
4th-segment, pre-release, or build-suffixed form (`AIQT 1.0.0.0`, `AIQT 1.0.0-rc1`, `AIQT 1.0.0+build`) is
deliberately out of scope (unmatched, not read as its 3-segment prefix; no gate scans the site for those),
while a leading-zero form (`AIQT 01.0.0`) IS matched and flagged as unknown (the safe direction). A
sentence-final period is allowed, so a stale token ending a sentence is still caught. A symlinked DIRECTORY inside site/ is not descended by
the shared os.walk-based walker, so pages behind it are not scanned; committed symlinks are rejected by the
manifest gate (gen_manifest rejects any tracked symlink repo-wide), and the proper fix for the
not-descended directory is the tracked shared-walker redesign, not this gate.

  check_site_versions.py             scan site/**/*.html against the current-version set
  check_site_versions.py --self-test  hermetic fixture cases (temp-dir only; host untouched)

Exit convention (matches the repo's gates):
  0  clean (every AIQT X.Y.Z token is a current version)
  1  a real finding (a stale/unknown product version on the site)
  2  malformed input or a read error (fail-closed): unreadable changelog.toml, a malformed or missing
     [[release]]/version, an absent/symlinked/unreadable/unlistable/page-less site/, or a non-UTF-8 page
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _walk import walk_files  # noqa: E402  fail-closed tree walk (os.walk, not rglob)
from _gen_common import load_toml  # noqa: E402

SKIP_DIRS = set()  # scan the whole of site/ (matching check_newtab); skipping no content directory means a
# page under a node_modules/, __pycache__/, or similar cannot escape the currency check.

# Product-version token: the literal `AIQT ` prefix (one or more spaces or tabs) then a bare 3-segment
# X.Y.Z. The prefix scopes this gate to the product version and away from bare numbers, "the 1.0.0
# release", and framework versions like "CSA AICM v1.1.0" (no `AIQT ` prefix, so unmatched). The trailing
# negative lookaheads reject a 4th segment (`.` then a digit) and any suffix continuation (a letter, digit,
# underscore, `+`, or `-`: "AIQT 1.0.0.0", "AIQT 1.0.0-rc1", "AIQT 1.0.0rc1", "AIQT 1.0.0+build"), so a
# malformed/suffixed form is NOT silently read as its 3-segment prefix; such forms are out of this gate's
# scope (no gate scans the site for those forms). A sentence-final period (`.` NOT followed by a digit) is
# allowed, so a stale token ending a sentence ("...on AIQT 9.9.9.") still matches and is caught.
VERSION_TOKEN = re.compile(r"\bAIQT[ \t]+(\d+\.\d+\.\d+)(?!\.\d)(?![\w+-])")

# A released version read from changelog must itself be a clean bare SemVer, matching check_versions.py's
# grammar (no leading zeros, no suffix, no trailing whitespace); anything else is a malformed source and is
# fail-closed rather than admitted to the valid set.
SEMVER = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", re.ASCII)


def _semver_tuple(v):
    """(major, minor, patch) ints for a dotted version, or None if a component exceeds CPython's
    integer-string-conversion digit limit (regex-valid but malformed; the caller fails closed rather than
    letting the ValueError escape as a traceback, matching check_versions.py)."""
    try:
        return tuple(int(p) for p in v.split("."))
    except ValueError:
        return None

# The in-development track has no changelog entry yet (it is unreleased), so it has no single source to read.
# This is a hand-maintained allowlist entry until it gains one: DROP it here once it ships in changelog.toml
# and becomes the latest [[release]] version (at which point this gate reads it from source automatically).
# A future maintainer reconciling the record: when 1.1.0 ships, remove it from this set (and update the
# self-test's in-development case, which relies on 1.1.0 being valid via this constant).
IN_DEVELOPMENT_VERSIONS = {"1.1.0"}


def _current_version(root):
    """Return (version, None) for the latest released pack version from changelog.toml, or (None, exit_code)
    on a fail-closed condition (2), matching check_versions.py's single-source parsing and error messages."""
    changelog = root / "changelog.toml"
    if changelog.is_symlink():
        print("error: changelog.toml is a symlink; the authoritative version source must not be redirected "
              "out of tree; fail-closed", file=sys.stderr)
        return None, 2
    try:
        data = load_toml(changelog)
    except (OSError, ValueError) as exc:
        print("error: cannot read changelog.toml: {}; fail-closed".format(exc), file=sys.stderr)
        return None, 2
    releases = data.get("release")
    if not isinstance(releases, list) or not releases:
        print("error: changelog.toml has no [[release]] tables; fail-closed", file=sys.stderr)
        return None, 2
    latest = releases[-1]
    if not isinstance(latest, dict):
        print("error: the latest release is not a table ({!r}); fail-closed".format(latest), file=sys.stderr)
        return None, 2
    version = latest.get("version")
    if not isinstance(version, str) or not version:
        print("error: the latest release has no string `version`; fail-closed", file=sys.stderr)
        return None, 2
    if SEMVER.fullmatch(version) is None:
        print("error: the latest release version {!r} is not a bare SemVer X.Y.Z; fail-closed".format(
            version), file=sys.stderr)
        return None, 2
    return version, None


def run(root):
    version, code = _current_version(root)
    if code is not None:
        return code
    # The in-development allowlist is a guard input: validate it against the source rather than trusting it.
    # Each entry must be a bare SemVer strictly GREATER than the released version, so a shipped version left
    # in the constant (e.g. 1.1.0 after the pack bumps past it) fails closed and forces reconciliation at
    # the bump, rather than silently keeping a now-stale token valid.
    rel = _semver_tuple(version)
    if rel is None:
        print("error: released version {!r} has an oversized component; fail-closed".format(version),
              file=sys.stderr)
        return 2
    if not isinstance(IN_DEVELOPMENT_VERSIONS, (set, frozenset, list, tuple)):
        print("error: IN_DEVELOPMENT_VERSIONS must be a set/list/tuple of version strings; fail-closed",
              file=sys.stderr)
        return 2
    for dev in IN_DEVELOPMENT_VERSIONS:
        dv = _semver_tuple(dev) if isinstance(dev, str) and SEMVER.fullmatch(dev) else None
        if dv is None or dv <= rel:
            print("error: in-development version {!r} is not a bare SemVer strictly greater than the "
                  "released {!r}; drop or update IN_DEVELOPMENT_VERSIONS; fail-closed".format(dev, version),
                  file=sys.stderr)
            return 2
    valid = {version, *IN_DEVELOPMENT_VERSIONS}

    site = root / "site"
    if site.is_symlink() or not site.is_dir():
        print("error: site/ absent or a symlink; the version-currency gate cannot evaluate; fail-closed",
              file=sys.stderr)
        return 2
    try:
        html_files = sorted(walk_files(site, SKIP_DIRS, suffixes={".html"}))
    except OSError as exc:
        print("error: cannot scan site/ ({}); fail-closed".format(exc), file=sys.stderr)
        return 2
    if not html_files:
        print("error: site/ contains no .html pages; a page-less required input is fail-closed",
              file=sys.stderr)
        return 2

    findings = []
    for f in html_files:
        if f.is_symlink():
            print("error: {} is a symlink; a symlinked site page cannot be scoped to site/ and should not "
                  "exist (the manifest gate rejects committed symlinks repo-wide); fail-closed".format(
                      f.relative_to(root)), file=sys.stderr)
            return 2
        try:
            text = f.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            print("error: cannot load {} ({}); fail-closed".format(f.relative_to(root), exc), file=sys.stderr)
            return 2
        name = f.relative_to(root)
        for number, line in enumerate(text.splitlines(), 1):
            for match in VERSION_TOKEN.finditer(line):
                ver = match.group(1)
                if ver not in valid:
                    findings.append("{}:{}: AIQT {} is not a current version (valid: {})".format(
                        name, number, ver, sorted(valid)))

    if findings:
        print("FAIL: {} stale/unknown product-version token(s) on the site".format(len(findings)))
        for finding in findings:
            print("  " + finding)
        print("update the site to the current version, or reconcile IN_DEVELOPMENT_VERSIONS")
        return 1
    print("PASS: every AIQT X.Y.Z token on the site is a current version (valid: {})".format(sorted(valid)))
    return 0


def _self_test():
    """Hermetic fixture cases in a TemporaryDirectory: build a tiny changelog.toml + site tree, run run()
    against it, and assert its exit code. The fixture changelog names 1.0.0 as its current release; the
    module constant IN_DEVELOPMENT_VERSIONS = {"1.1.0"} makes 1.1.0 valid too. The self-test creates no
    files outside its TemporaryDirectory (Python's own import bytecode cache, .pyc, aside - ordinary
    interpreter behaviour shared by every tool that imports a module), per 10-QUALI-test-hermeticity."""
    import tempfile
    import contextlib
    import io

    good_toml = b'[[release]]\nversion = "1.0.0"\ntitle = "r"\n'

    def build(tmp, toml_bytes, pages):
        root = Path(tmp)
        if toml_bytes is not None:
            (root / "changelog.toml").write_bytes(toml_bytes)
        site = root / "site"
        site.mkdir()
        for pname, content in pages.items():
            p = site / pname
            if isinstance(content, bytes):
                p.write_bytes(content)
            else:
                p.write_text(content, encoding="utf-8")
        return root

    def run_quiet(root):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return run(root)

    # (toml_bytes, pages, expected exit). str page content is UTF-8; bytes is written raw.
    cases = [
        ("a: current version passes", good_toml, {"i.html": "<p>AIQT 1.0.0</p>"}, 0),
        ("b: in-development 1.1.0 passes", good_toml, {"i.html": "<p>AIQT 1.1.0</p>"}, 0),
        ("c: stale AIQT 9.9.9 is a finding", good_toml, {"i.html": "<p>AIQT 9.9.9</p>"}, 1),
        ("d: 'the 1.0.0 release' phrasing yields no finding", good_toml,
         {"i.html": "<p>the 1.0.0 release shipped; 1.0.0 pack too</p>"}, 0),
        ("e: framework 'CSA AICM v1.1.0' yields no finding", good_toml,
         {"i.html": "<p>maps to CSA AICM v1.1.0</p>"}, 0),
        ("f: malformed changelog fails closed", b"not [[valid toml", {"i.html": "<p>AIQT 1.0.0</p>"}, 2),
        ("f2: missing changelog fails closed", None, {"i.html": "<p>AIQT 1.0.0</p>"}, 2),
        ("f3: changelog with no [[release]] fails closed", b'title = "x"\n', {"i.html": "<p>ok</p>"}, 2),
        ("f4: latest release with no version fails closed", b'[[release]]\ntitle = "r"\n',
         {"i.html": "<p>ok</p>"}, 2),
        ("i: 4-segment AIQT 1.0.0.0 is out of scope (no false-valid, no false-finding)", good_toml,
         {"i.html": "<p>AIQT 1.0.0.0</p>"}, 0),
        ("j: AIQT 9.9.9.1 is not misread as stale 9.9.9", good_toml, {"i.html": "<p>AIQT 9.9.9.1</p>"}, 0),
        ("k: pre-release AIQT 1.0.0-rc1 is out of scope", good_toml, {"i.html": "<p>AIQT 1.0.0-rc1</p>"}, 0),
        ("l: multi-space 'AIQT  1.0.0' matches and is valid", good_toml, {"i.html": "<p>AIQT  1.0.0</p>"}, 0),
        ("m: tab-separated stale 'AIQT\\t9.9.9' matches and is a finding", good_toml,
         {"i.html": "<p>AIQT\t9.9.9</p>"}, 1),
        ("n: release version 1.0 (2-segment) fails closed", b'[[release]]\nversion = "1.0"\ntitle = "r"\n',
         {"i.html": "<p>ok</p>"}, 2),
        ("o: release version 1.0.0-alpha (suffix) fails closed",
         b'[[release]]\nversion = "1.0.0-alpha"\ntitle = "r"\n', {"i.html": "<p>ok</p>"}, 2),
        ("p: release version 01.0.0 (leading zero) fails closed",
         b'[[release]]\nversion = "01.0.0"\ntitle = "r"\n', {"i.html": "<p>ok</p>"}, 2),
        ("r: sentence-final stale 'AIQT 9.9.9.' is a finding", good_toml,
         {"i.html": "<p>runs on AIQT 9.9.9.</p>"}, 1),
        ("s: sentence-final current 'AIQT 1.0.0.' passes", good_toml,
         {"i.html": "<p>runs on AIQT 1.0.0.</p>"}, 0),
        ("t: suffix 'AIQT 1.0.0rc1' is out of scope", good_toml, {"i.html": "<p>AIQT 1.0.0rc1</p>"}, 0),
        ("u: build-metadata 'AIQT 1.0.0+build' is out of scope", good_toml,
         {"i.html": "<p>AIQT 1.0.0+build</p>"}, 0),
        ("v: an in-dev entry not strictly greater than the release fails closed (release 2.0.0)",
         b'[[release]]\nversion = "2.0.0"\ntitle = "r"\n', {"i.html": "<p>ok</p>"}, 2),
        ("x: leading-zero SITE token 'AIQT 01.0.0' is matched and flagged", good_toml,
         {"i.html": "<p>AIQT 01.0.0</p>"}, 1),
        ("z: an oversized released version component fails closed (not a traceback)",
         b'[[release]]\nversion = "' + b"9" * 5000 + b'.0.0"\ntitle = "r"\n', {"i.html": "<p>ok</p>"}, 2),
    ]
    failures = []
    for label, toml_bytes, pages, want in cases:
        with tempfile.TemporaryDirectory() as tmp:
            got = run_quiet(build(tmp, toml_bytes, pages))
            if got != want:
                failures.append("{}: expected exit {} got {}".format(label, want, got))

    # (g) a non-UTF-8 site html fails closed (exit 2), matching check_newtab/check_links, never skipped.
    with tempfile.TemporaryDirectory() as tmp:
        root = build(tmp, good_toml, {"bad.html": b"<p>AIQT 1.0.0 \xff\xfe not utf-8</p>"})
        got = run_quiet(root)
        if got != 2:
            failures.append("g: non-utf-8 html expected exit 2 got {}".format(got))

    # page-less site fails closed (required coverage input), matching the check_newtab shape.
    with tempfile.TemporaryDirectory() as tmp:
        root = build(tmp, good_toml, {})
        got = run_quiet(root)
        if got != 2:
            failures.append("h: page-less site/ expected exit 2 got {}".format(got))

    # a symlinked site page fails closed rather than being followed outside site/.
    with tempfile.TemporaryDirectory() as tmp:
        root = build(tmp, good_toml, {"real.html": "<p>AIQT 1.0.0</p>"})
        outside = root / "outside.html"
        outside.write_text("<p>AIQT 9.9.9</p>", encoding="utf-8")
        try:
            (root / "site" / "link.html").symlink_to(outside)
            if run_quiet(root) != 2:
                failures.append("q: symlinked site page expected exit 2")
        except (OSError, NotImplementedError):
            pass  # platform without symlink support: skip rather than fail spuriously

    with tempfile.TemporaryDirectory() as tmp:  # symlinked changelog.toml fails closed (out-of-tree source)
        root = build(tmp, None, {"i.html": "<p>AIQT 1.0.0</p>"})
        target = root / "outside.toml"
        target.write_bytes(b'[[release]]\nversion = "9.9.9"\ntitle = "r"\n')
        try:
            (root / "changelog.toml").symlink_to(target)
            if run_quiet(root) != 2:
                failures.append("w: symlinked changelog.toml expected exit 2")
        except (OSError, NotImplementedError):
            pass

    # a non-iterable in-development allowlist fails closed (guard-input-soundness), not a traceback.
    saved = IN_DEVELOPMENT_VERSIONS
    globals()["IN_DEVELOPMENT_VERSIONS"] = None
    try:
        with tempfile.TemporaryDirectory() as tmp:
            if run_quiet(build(tmp, good_toml, {"i.html": "<p>ok</p>"})) != 2:
                failures.append("aa: non-iterable IN_DEVELOPMENT_VERSIONS expected exit 2")
    finally:
        globals()["IN_DEVELOPMENT_VERSIONS"] = saved

    if failures:
        print("FAIL: check_site_versions self-test")
        for x in failures:
            print("  " + x)
        return 1
    print("SELF-TEST PASS: current/in-dev pass; stale is a finding (incl. a sentence-final 'AIQT 9.9.9.'); "
          "'the 1.0.0 release', bare '1.0.0 pack', 'CSA AICM v1.1.0', and out-of-scope 4-segment/pre-release/"
          "build-suffixed forms yield no finding; multi-space and tab separators match; malformed/missing/"
          "release-less/version-less/non-SemVer changelog, an in-dev entry not > the release, a non-UTF-8 "
          "page, a page-less site/, and (where supported) a symlinked page or changelog all fail closed "
          "({}+ cases)".format(len(cases) + 2))
    return 0


def main():
    if "--self-test" in sys.argv[1:]:
        return _self_test()
    return run(Path(__file__).resolve().parents[1])


if __name__ == "__main__":
    sys.exit(main())
