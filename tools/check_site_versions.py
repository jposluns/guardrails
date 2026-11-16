#!/usr/bin/env python3
"""Site version-currency gate, with the pack-version qualification lint: every `AIQT X.Y.Z` or
`AIQT pack X.Y.Z` product-version token in the scanned public text is current, and guardrails public text
names the pack version in the qualified form `AIQT pack X.Y.Z`.

changelog.toml is the single source of truth for the pack's released SemVer (the latest [[release]]'s
`version`, the same source check_versions.py reads). This gate asserts that every product-version token
rendered on the site or written in the declared public Markdown files, of the exact form `AIQT X.Y.Z` (bare)
or `AIQT pack X.Y.Z` (qualified), names a version that is currently valid, so a stale version left behind
after a release bump is caught and the release process is forced to update the public text.

The valid set is the released pack version plus the hand-maintained in-development allowlist below. A token
whose version is outside that set is a finding.

Two rules share the one scan:
  - CURRENCY (blocking). Every bare or qualified token under site/**/*.html, opf/site/**/*.html and the
    declared public Markdown files (PUBLIC_TEXT_FILES) names a currently valid version. opf/site/ keeps the
    currency coverage it had; reading the qualified form there too only widens it (no qualified token
    exists there today).
  - QUALIFICATION (the maintainer's pack-version naming decision). In guardrails public text every
    mention of the pack version reads `AIQT pack X.Y.Z`, so a bare `AIQT X.Y.Z` there is a qualification
    finding. Guardrails
    public text here is site/**/*.html (QUALIFIED_ROOTS) plus PUBLIC_TEXT_FILES. opf/site/ is OPF's public
    text and is outside this rule (OPF wording is handed to OPF). The rule lands REPORT-ONLY behind the one
    constant QUALIFICATION_ENFORCED: while it is False the findings and their count are printed and do not
    change the exit code; the public text is reworded at the 1.1.1 cut, and flipping the constant to True
    is part of that cut, after which a bare token in guardrails public text exits 1.

Scope (deliberately narrow, an honest partial currency catcher, not a per-page context checker):
  - It matches ONLY the `AIQT X.Y.Z` and `AIQT pack X.Y.Z` product-version patterns. It does NOT touch
    other phrasings, some of which are MATERIALIZED current-version references that would go stale at the
    next bump and pass unseen: the dev-track "(1.2.0, in development)" in titles/og-tags, "1.0.0 chat"/
    "1.0.0 pack"/"the 1.0.0 release", a bare version number with no AIQT prefix, or a third-party framework
    version such as "CSA AICM v1.1.0". None carries the `AIQT ` prefix, so none is matched; leaving those
    other phrasings uncovered is a deliberate scope limit, not a claim that all of them are historical.
  - It enforces set-membership (is this version currently valid?), NOT per-page context correctness
    (whether THIS page should name THIS version). It is a partial (class c) currency catcher: it holds
    the public text's product-version tokens to the current-version set and no more.
  - The declared public Markdown files are the published repository documents README.md, ROADMAP.md and
    DISCLOSURE.md. Their sources (roadmap.toml, disclosure.toml, docs/evidence.md, site/draft-src/*.toml)
    are not scanned directly; their text reaches a scanned output. CHANGELOG.md (and changelog.toml) is
    NOT declared: its release rows are historical record, and how historical quotations are treated is an
    open maintainer decision, so a bare token there is neither a currency nor a qualification finding.

site/ is a required coverage input: an absent, symlinked, unreadable, unlistable, or page-less site/ fails
closed (exit 2), never a clean pass over nothing (per the check-fails-closed-on-unreadable rule and the
check_newtab site-input shape). A non-UTF-8 site html is likewise fail-closed, matching check_newtab and
check_links; it is never silently skipped, and a symlinked site/*.html page fails closed rather than being
followed outside site/. Each declared public Markdown file is a required input too: an absent, symlinked,
non-regular, unreadable or non-UTF-8 declared file cannot be evaluated (exit 2) in either qualification
mode.

Disclosed residuals (class c is partial): it scans each source line for `AIQT` + spaces/tabs (+ `pack` +
spaces/tabs) + a bare 3-segment X.Y.Z, so a token separated from `AIQT` by a MARKUP TAG
(`AIQT <strong>1.0.0</strong>`) or a SOURCE-LINE WRAP (`AIQT` ending one line, the version opening the next)
is unmatched even though the page renders `AIQT 1.0.0`; likewise a token separated by an HTML entity
(`AIQT&nbsp;1.0.0`), a `v` prefix (`AIQT v1.0.0`), or a different case (`aiqt`, `AIQT Pack 1.0.0`). A stale
value in any such form would pass; none exists in current site content (verified as of the 2026-08-29
audit). A 4th-segment, pre-release, or build-suffixed form (`AIQT 1.0.0.0`, `AIQT 1.0.0-rc1`,
`AIQT 1.0.0+build`, and the same after `pack`) is deliberately out of scope (unmatched, not read as its
3-segment prefix; no gate scans the site for those), while a leading-zero form (`AIQT 01.0.0`) IS matched
and flagged as unknown (the safe direction). A sentence-final period is allowed, so a stale token ending a
sentence is still caught. A symlinked DIRECTORY inside site/ is not descended by the shared os.walk-based
walker, so pages behind it are not scanned; committed symlinks are rejected by the manifest gate
(gen_manifest rejects any tracked symlink repo-wide), and the proper fix for the not-descended directory is
the tracked shared-walker redesign, not this gate.

  check_site_versions.py             scan the public text against the current-version set and the
                                     qualification rule
  check_site_versions.py --self-test  hermetic fixture cases (temp-dir only; host untouched)

Exit convention (matches the repo's gates):
  0  clean (every token is a current version); in report-only qualification mode, bare-token findings are
     printed with their count and still exit 0
  1  a real finding (a stale/unknown product version in the scanned text), or, once QUALIFICATION_ENFORCED
     is True, a bare `AIQT X.Y.Z` token in guardrails public text
  2  malformed input or a read error (fail-closed, cannot evaluate): unreadable changelog.toml, a malformed
     or missing [[release]]/version, an absent/symlinked/unreadable/unlistable/page-less site/, a non-UTF-8
     page, an absent/symlinked/non-regular/unreadable/non-UTF-8 declared public Markdown file, or a
     non-bool QUALIFICATION_ENFORCED
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: check_site_versions.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import re
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _walk import walk_files  # noqa: E402  fail-closed tree walk (os.walk, not rglob)
from _gen_common import load_toml  # noqa: E402

SKIP_DIRS = set()  # scan the whole of site/ (matching check_newtab); skipping no content directory means a
# page under a node_modules/, __pycache__/, or similar cannot escape the currency check.

# Coverage roots: site/ (aiqt.ai) and opf/site/ (opfiles.ai: the index.html placeholder plus the
# opf/site/draft staging tree). Each is a REQUIRED input; the version-currency rule is identical on both.
COVERAGE_ROOTS = ("site", "opf/site")

# The coverage roots holding guardrails public text, where the qualification rule applies. opf/site/ is
# OPF's public text: its wording is handed to OPF, so it keeps currency coverage only.
QUALIFIED_ROOTS = ("site",)

# Declared guardrails public Markdown files (repository-relative). Each is a REQUIRED input for both rules:
# absent, symlinked, non-regular, unreadable or non-UTF-8 is cannot-evaluate (exit 2). CHANGELOG.md is
# deliberately not declared (historical release rows; see the module docstring).
PUBLIC_TEXT_FILES = ("README.md", "ROADMAP.md", "DISCLOSURE.md")

# Qualification mode. False = REPORT-ONLY: bare-token findings are printed with their count and
# do not change the exit code. The public text is reworded at the 1.1.1 cut; setting this to True is part of
# that cut, after which a bare `AIQT X.Y.Z` in guardrails public text exits 1. Must be a bool (validated).
QUALIFICATION_ENFORCED = False

# Product-version token: the literal `AIQT ` prefix (one or more spaces or tabs), an optional `pack`
# qualifier (lower-case, followed by one or more spaces or tabs), then a bare 3-segment X.Y.Z. Group 1 is the
# qualifier (None for the bare form), group 2 the version. The prefix scopes this gate to the product version
# and away from bare numbers, "the 1.0.0 release", and framework versions like "CSA AICM v1.1.0" (no `AIQT `
# prefix, so unmatched). The trailing negative lookaheads reject a 4th segment (`.` then a digit) and any
# suffix continuation (a letter, digit, underscore, `+`, or `-`: "AIQT 1.0.0.0", "AIQT 1.0.0-rc1",
# "AIQT 1.0.0rc1", "AIQT 1.0.0+build", and the same after `pack`), so a malformed/suffixed form is NOT
# silently read as its 3-segment prefix; such forms are out of this gate's scope (no gate scans the site for
# those forms). A sentence-final period (`.` NOT followed by a digit) is allowed, so a stale token ending a
# sentence ("...on AIQT 9.9.9.") still matches and is caught.
VERSION_TOKEN = re.compile(r"\bAIQT[ \t]+(pack[ \t]+)?(\d+\.\d+\.\d+)(?!\.\d)(?![\w+-])")

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
# A future maintainer reconciling the record: when 1.2.0 ships, remove it from this set (and update the
# self-test's in-development case, which relies on 1.2.0 being valid via this constant).
IN_DEVELOPMENT_VERSIONS = {"1.2.0"}


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
    # The qualification mode is a guard input: a non-bool (a truthy string, say) fails closed rather than
    # being read loosely as one mode or the other.
    if not isinstance(QUALIFICATION_ENFORCED, bool):
        print("error: QUALIFICATION_ENFORCED must be a bool; fail-closed", file=sys.stderr)
        return 2
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

    bare = []  # qualification findings across all guardrails public text, reported once below
    worst = 0
    for subdir in COVERAGE_ROOTS:
        worst = max(worst, _scan_root(root, subdir, valid, bare if subdir in QUALIFIED_ROOTS else None))
    worst = max(worst, _scan_public_files(root, valid, bare))
    worst = max(worst, _report_qualification(bare))
    return worst


def _scan_text(name, text, valid, stale, bare):
    """Scan one document's text line by line. Append currency findings to `stale`; when `bare` is a list
    (guardrails public text), append each bare `AIQT X.Y.Z` token to it as a qualification finding."""
    for number, line in enumerate(text.splitlines(), 1):
        for match in VERSION_TOKEN.finditer(line):
            qualified, ver = match.group(1) is not None, match.group(2)
            if ver not in valid:
                stale.append("{}:{}: AIQT {}{} is not a current version (valid: {})".format(
                    name, number, "pack " if qualified else "", ver, sorted(valid)))
            if bare is not None and not qualified:
                bare.append("{}:{}: bare `AIQT {}`; write `AIQT pack {}`".format(name, number, ver, ver))


def _report_currency(findings, where):
    if findings:
        print("FAIL: {} stale/unknown product-version token(s) in {}".format(len(findings), where))
        for finding in findings:
            print("  " + finding)
        print("update the public text to the current version, or reconcile IN_DEVELOPMENT_VERSIONS")
        return 1
    return 0


def _scan_root(root, subdir, valid, bare):
    """Scan one coverage root (root/subdir) for stale product-version tokens against `valid`, and (when
    `bare` is a list) collect its bare tokens as qualification findings. Return an exit code: 0 clean, 1 a
    stale/unknown token, 2 a missing/symlinked/unreadable/page-less required input."""
    site = root / subdir
    if site.is_symlink() or not site.is_dir():
        print("error: {}/ absent or a symlink; the version-currency gate cannot evaluate; fail-closed"
              .format(subdir), file=sys.stderr)
        return 2
    try:
        html_files = sorted(walk_files(site, SKIP_DIRS, suffixes={".html"}))
    except OSError as exc:
        print("error: cannot scan {}/ ({}); fail-closed".format(subdir, exc), file=sys.stderr)
        return 2
    if not html_files:
        print("error: {}/ contains no .html pages; a page-less required input is fail-closed".format(subdir),
              file=sys.stderr)
        return 2

    findings = []
    for f in html_files:
        if f.is_symlink():
            print("error: {} is a symlink; a symlinked site page cannot be scoped to {}/ and should not "
                  "exist (the manifest gate rejects committed symlinks repo-wide); fail-closed".format(
                      f.relative_to(root), subdir), file=sys.stderr)
            return 2
        try:
            text = f.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            print("error: cannot load {} ({}); fail-closed".format(f.relative_to(root), exc), file=sys.stderr)
            return 2
        _scan_text(f.relative_to(root), text, valid, findings, bare)

    if _report_currency(findings, "{}/".format(subdir)):
        return 1
    print("PASS: every product-version token under {}/ is a current version (valid: {})".format(
        subdir, sorted(valid)))
    return 0


def _scan_public_files(root, valid, bare):
    """Scan the declared guardrails public Markdown files (PUBLIC_TEXT_FILES) for both rules. Each is a
    required input: absent, symlinked, non-regular, unreadable or non-UTF-8 is cannot-evaluate (2)."""
    findings = []
    for rel in PUBLIC_TEXT_FILES:
        path = root / rel
        if path.is_symlink() or not path.is_file():
            print("error: declared public text {} is absent, a symlink, or not a regular file; the "
                  "version gate cannot evaluate it; fail-closed".format(rel), file=sys.stderr)
            return 2
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            print("error: cannot load declared public text {} ({}); fail-closed".format(rel, exc),
                  file=sys.stderr)
            return 2
        _scan_text(rel, text, valid, findings, bare)
    if _report_currency(findings, "the declared public Markdown files"):
        return 1
    print("PASS: every product-version token in {} is a current version".format(", ".join(PUBLIC_TEXT_FILES)))
    return 0


def _report_qualification(bare):
    """Print the qualification findings and their count. Return 1 only when QUALIFICATION_ENFORCED is True
    and there is a finding; report-only mode returns 0 whatever it found."""
    mode = "enforcing" if QUALIFICATION_ENFORCED else "report-only"
    if not bare:
        print("PASS: 0 bare `AIQT X.Y.Z` token(s) in guardrails public text ({} mode)".format(mode))
        return 0
    print("{}: {} bare `AIQT X.Y.Z` token(s) in guardrails public text ({} mode); the pack version reads "
          "`AIQT pack X.Y.Z`".format("FAIL" if QUALIFICATION_ENFORCED else "REPORT", len(bare), mode))
    for finding in bare:
        print("  " + finding)
    if not QUALIFICATION_ENFORCED:
        print("report-only: these findings do not change the exit code until QUALIFICATION_ENFORCED is set "
              "to True at the 1.1.1 cut")
        return 0
    return 1


def _self_test():
    """Hermetic fixture cases in a TemporaryDirectory: build a tiny changelog.toml + site tree + declared
    public Markdown files, run run() against it, and assert its exit code (and, for the report-only case, its
    printed findings). The fixture changelog names 1.0.0 as its current release; the module constant
    IN_DEVELOPMENT_VERSIONS = {"1.2.0"} makes 1.2.0 valid too. Each case pins QUALIFICATION_ENFORCED itself,
    so the self-test reads the same whichever mode the module ships in. The self-test creates no files
    outside its TemporaryDirectory (Python's own import bytecode cache, .pyc, aside - ordinary interpreter
    behaviour shared by every tool that imports a module), per 10-QUALI-test-hermeticity."""
    import tempfile
    import contextlib
    import io

    good_toml = b'[[release]]\nversion = "1.0.0"\ntitle = "r"\n'

    def write(p, content):
        if isinstance(content, bytes):
            p.write_bytes(content)
        else:
            p.write_text(content, encoding="utf-8")

    def build(tmp, toml_bytes, pages, files=None):
        root = Path(tmp)
        if toml_bytes is not None:
            (root / "changelog.toml").write_bytes(toml_bytes)
        site = root / "site"
        site.mkdir()
        for pname, content in pages.items():
            write(site / pname, content)
        # opf/site is a second required coverage root. Give it one benign, version-token-free page so the
        # existing site/-driven cases exercise the two-root run() without opf/site contributing a verdict;
        # the opf/site-specific behaviour is covered by its own legs below.
        opf_site = root / "opf" / "site"
        opf_site.mkdir(parents=True)
        (opf_site / "index.html").write_text("<p>coming soon</p>", encoding="utf-8")
        # The declared public Markdown files are required inputs: a benign token-free copy of each, unless
        # `files` overrides it (str/bytes content) or omits it (None).
        files = dict(files or {})
        for rel in PUBLIC_TEXT_FILES:
            content = files.pop(rel, "# doc\n")
            if content is not None:
                write(root / rel, content)
        for rel, content in files.items():  # any other fixture path (e.g. an opf/site page)
            if content is None:
                # An omitted path that is not declared: nothing to write. A case that omits a declared file
                # then fails by its own assertion (and the declared-set pin below), never by a TypeError.
                continue
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            write(root / rel, content)
        return root

    @contextlib.contextmanager
    def mode(enforced):
        saved = globals()["QUALIFICATION_ENFORCED"]
        globals()["QUALIFICATION_ENFORCED"] = enforced
        try:
            yield
        finally:
            globals()["QUALIFICATION_ENFORCED"] = saved

    def run_quiet(root, enforced=False):
        out = io.StringIO()
        with mode(enforced), contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            return run(root), out.getvalue()

    # (toml_bytes, pages, expected exit), all in REPORT-ONLY mode, so the currency outcomes below hold
    # whatever mode the module ships in. str page content is UTF-8; bytes is written raw.
    cases = [
        ("a: current version passes", good_toml, {"i.html": "<p>AIQT 1.0.0</p>"}, 0),
        ("b: in-development 1.2.0 passes", good_toml, {"i.html": "<p>AIQT 1.2.0</p>"}, 0),
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
    # The declared public Markdown files are pinned literally, so narrowing the set is a deliberate edit of this
    # suite too, and a dropped file is named here rather than only by the legs that plant it.
    if PUBLIC_TEXT_FILES != ("README.md", "ROADMAP.md", "DISCLOSURE.md"):
        failures.append("PUBLIC_TEXT_FILES must be ('README.md', 'ROADMAP.md', 'DISCLOSURE.md'), got {!r}".format(
            PUBLIC_TEXT_FILES))
    for label, toml_bytes, pages, want in cases:
        with tempfile.TemporaryDirectory() as tmp:
            got, _ = run_quiet(build(tmp, toml_bytes, pages))
            if got != want:
                failures.append("{}: expected exit {} got {}".format(label, want, got))

    # Pack-version qualification and the declared public Markdown inputs:
    # (label, pages, files, enforced, expected exit).
    qcases = [
        ("qa: bare 'AIQT 1.0.0' on a site page is a finding when enforcing", {"i.html": "<p>AIQT 1.0.0</p>"},
         None, True, 1),
        ("qb: bare 'AIQT 1.0.0' in a declared Markdown file is a finding when enforcing",
         {"i.html": "<p>ok</p>"}, {"ROADMAP.md": "AIQT 1.0.0 ships.\n"}, True, 1),
        ("qc: qualified current 'AIQT pack 1.0.0' and in-development 'AIQT pack 1.2.0' pass when enforcing",
         {"i.html": "<p>AIQT pack 1.0.0; AIQT pack 1.2.0.</p>"}, {"DISCLOSURE.md": "AIQT pack 1.0.0\n"},
         True, 0),
        ("qd: qualified stale 'AIQT pack 9.9.9' on a site page fails (report-only)",
         {"i.html": "<p>AIQT pack 9.9.9</p>"}, None, False, 1),
        ("qe: qualified stale 'AIQT pack 9.9.9' on a site page fails (enforcing)",
         {"i.html": "<p>AIQT pack 9.9.9</p>"}, None, True, 1),
        ("qf: tab-separated qualified stale 'AIQT\\tpack\\t9.9.9' fails",
         {"i.html": "<p>AIQT\tpack\t9.9.9</p>"}, None, False, 1),
        ("qg: stale bare 'AIQT 9.9.9' in a declared Markdown file fails (report-only)",
         {"i.html": "<p>ok</p>"}, {"README.md": "AIQT 9.9.9\n"}, False, 1),
        ("qh: qualified pre-release/4-segment forms stay out of scope (not read as bare) when enforcing",
         {"i.html": "<p>AIQT pack 1.0.0-rc1 and AIQT pack 1.0.0.0</p>"}, None, True, 0),
        ("qi: a bare token in opf/site (OPF's text) is not a qualification finding when enforcing",
         {"i.html": "<p>ok</p>"}, {"opf/site/o.html": "<p>AIQT 1.0.0</p>"}, True, 0),
        ("qj: a missing declared Markdown file cannot be evaluated", {"i.html": "<p>ok</p>"},
         {"ROADMAP.md": None}, False, 2),
        ("qk: a non-UTF-8 declared Markdown file cannot be evaluated", {"i.html": "<p>ok</p>"},
         {"DISCLOSURE.md": b"AIQT pack 1.0.0 \xff\xfe\n"}, False, 2),
        ("ql: a missing declared Markdown file cannot be evaluated when enforcing either",
         {"i.html": "<p>ok</p>"}, {"README.md": None}, True, 2),
    ]
    for label, pages, files, enforced, want in qcases:
        with tempfile.TemporaryDirectory() as tmp:
            got, _ = run_quiet(build(tmp, good_toml, pages, files), enforced)
            if got != want:
                failures.append("{}: expected exit {} got {}".format(label, want, got))

    # A declared Markdown path that is a directory (not a regular file) cannot be evaluated.
    with tempfile.TemporaryDirectory() as tmp:
        root = build(tmp, good_toml, {"i.html": "<p>ok</p>"}, {"ROADMAP.md": None})
        (root / "ROADMAP.md").mkdir()
        if run_quiet(root)[0] != 2:
            failures.append("qm: a directory at a declared Markdown path expected exit 2")

    # A symlinked declared Markdown file fails closed rather than being followed.
    with tempfile.TemporaryDirectory() as tmp:
        root = build(tmp, good_toml, {"i.html": "<p>ok</p>"}, {"README.md": None})
        outside = root / "outside.md"
        outside.write_text("AIQT pack 1.0.0\n", encoding="utf-8")
        try:
            (root / "README.md").symlink_to(outside)
            if run_quiet(root)[0] != 2:
                failures.append("qn: a symlinked declared Markdown file expected exit 2")
        except (OSError, NotImplementedError):
            pass  # platform without symlink support: skip rather than fail spuriously

    # Report-only versus enforcing on the same bare-token fixture: report-only exits 0 but prints every
    # finding (file and line) and their count; enforcing exits 1 on the same tree.
    with tempfile.TemporaryDirectory() as tmp:
        root = build(tmp, good_toml, {"i.html": "<p>x</p>\n<p>AIQT 1.0.0</p>"},
                     {"ROADMAP.md": "# r\n\nAIQT 1.2.0 is next.\n"})
        got, out = run_quiet(root, False)
        if got != 0:
            failures.append("qo: report-only with bare tokens expected exit 0 got {}".format(got))
        for needle in ("REPORT: 2 bare `AIQT X.Y.Z` token(s)", "site/i.html:2: bare `AIQT 1.0.0`",
                       "ROADMAP.md:3: bare `AIQT 1.2.0`"):
            if needle not in out:
                failures.append("qo: report-only output lacks {!r}".format(needle))
        got, out = run_quiet(root, True)
        if got != 1 or "FAIL: 2 bare `AIQT X.Y.Z` token(s)" not in out:
            failures.append("qp: enforcing with bare tokens expected exit 1 and a FAIL count line, got {}"
                            .format(got))

    # A non-bool qualification mode fails closed (guard-input soundness), not read loosely.
    with tempfile.TemporaryDirectory() as tmp:
        if run_quiet(build(tmp, good_toml, {"i.html": "<p>ok</p>"}), "yes")[0] != 2:
            failures.append("qq: non-bool QUALIFICATION_ENFORCED expected exit 2")

    # (g) a non-UTF-8 site html fails closed (exit 2), matching check_newtab/check_links, never skipped.
    with tempfile.TemporaryDirectory() as tmp:
        root = build(tmp, good_toml, {"bad.html": b"<p>AIQT 1.0.0 \xff\xfe not utf-8</p>"})
        got = run_quiet(root)[0]
        if got != 2:
            failures.append("g: non-utf-8 html expected exit 2 got {}".format(got))

    # page-less site fails closed (required coverage input), matching the check_newtab shape.
    with tempfile.TemporaryDirectory() as tmp:
        root = build(tmp, good_toml, {})
        got = run_quiet(root)[0]
        if got != 2:
            failures.append("h: page-less site/ expected exit 2 got {}".format(got))

    # a symlinked site page fails closed rather than being followed outside site/.
    with tempfile.TemporaryDirectory() as tmp:
        root = build(tmp, good_toml, {"real.html": "<p>AIQT 1.0.0</p>"})
        outside = root / "outside.html"
        outside.write_text("<p>AIQT 9.9.9</p>", encoding="utf-8")
        try:
            (root / "site" / "link.html").symlink_to(outside)
            if run_quiet(root)[0] != 2:
                failures.append("q: symlinked site page expected exit 2")
        except (OSError, NotImplementedError):
            pass  # platform without symlink support: skip rather than fail spuriously

    with tempfile.TemporaryDirectory() as tmp:  # symlinked changelog.toml fails closed (out-of-tree source)
        root = build(tmp, None, {"i.html": "<p>AIQT 1.0.0</p>"})
        target = root / "outside.toml"
        target.write_bytes(b'[[release]]\nversion = "9.9.9"\ntitle = "r"\n')
        try:
            (root / "changelog.toml").symlink_to(target)
            if run_quiet(root)[0] != 2:
                failures.append("w: symlinked changelog.toml expected exit 2")
        except (OSError, NotImplementedError):
            pass

    # opf/site is a second required coverage root: a stale token in an opf/site page is a finding, and an
    # absent opf/site root fails closed.
    with tempfile.TemporaryDirectory() as tmp:
        root = build(tmp, good_toml, {"i.html": "<p>AIQT 1.0.0</p>"})
        (root / "opf" / "site" / "draft").mkdir(parents=True)
        (root / "opf" / "site" / "draft" / "stale.html").write_text("<p>AIQT 9.9.9</p>", encoding="utf-8")
        if run_quiet(root)[0] != 1:
            failures.append("bb: a stale token in an opf/site page expected exit 1")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "changelog.toml").write_bytes(good_toml)
        (root / "site").mkdir()
        (root / "site" / "i.html").write_text("<p>AIQT 1.0.0</p>", encoding="utf-8")  # no opf/site
        for rel in PUBLIC_TEXT_FILES:
            (root / rel).write_text("# doc\n", encoding="utf-8")
        if run_quiet(root)[0] != 2:
            failures.append("cc: an absent opf/site coverage root expected exit 2 (fail-closed)")

    # a non-iterable in-development allowlist fails closed (guard-input-soundness), not a traceback.
    saved = IN_DEVELOPMENT_VERSIONS
    globals()["IN_DEVELOPMENT_VERSIONS"] = None
    try:
        with tempfile.TemporaryDirectory() as tmp:
            if run_quiet(build(tmp, good_toml, {"i.html": "<p>ok</p>"}))[0] != 2:
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
          "page, a page-less site/, and (where supported) a symlinked page or changelog all fail closed; "
          "qualification: bare 'AIQT X.Y.Z' in site/ or a declared Markdown file is a finding (report-only "
          "exits 0 and prints it with its count, enforcing exits 1), qualified current/in-dev pass, "
          "qualified stale fails, opf/site is outside the rule, and a missing/non-regular/non-UTF-8/symlinked declared "
          "file or a non-bool mode cannot be evaluated ({}+ cases)".format(len(cases) + len(qcases) + 9))
    return 0


def main():
    if "--self-test" in sys.argv[1:]:
        return _self_test()
    return run(Path(__file__).resolve().parents[1])


if __name__ == "__main__":
    sys.exit(main())
