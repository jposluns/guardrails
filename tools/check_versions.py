#!/usr/bin/env python3
"""Version-format and single-source gate for the pack SemVer. Offline, stdlib only, fail-closed.

changelog.toml is the single source of truth for the pack's SemVer; the root VERSION file is generated
from the latest release by gen_changelog.py. This gate asserts the invariants that keep the two from
diverging and keep the version well-formed:

  1. Every [[release]] carries a `version` that is a bare SemVer `MAJOR.MINOR.PATCH` (no pre-release or
     build identifier; policy R5 accepts only X.Y.Z before and after 1.0.0).
  2. The release versions are strictly SemVer-increasing in array order (releases are append-only, oldest
     to newest). With one release this holds trivially; it guards the append-only invariant as releases
     accrue.
  3. The root VERSION file equals the latest release version (the last table in the array). This is the
     single-source check: the one place the version is authored is changelog.toml, and VERSION must be
     its faithful derivative.

This is NOT the cross-release version-monotonicity gate (GA-3): comparing the pack version against the
previous vX.Y.Z git tag, and per-rule date regression, need a first tagged release and git history that do
not exist yet. Those stay deferred; this gate covers only what is decidable from the committed files today.

The skill is now INDEPENDENTLY versioned (its own version lives in .aiqt/core/skill/skill-source.md), so
the shipped-skill-zip leg here is version-AGNOSTIC: it asserts exactly one version-numbered
site/downloads/aiqt-skill-*.zip ships and the stable alias site/downloads/aiqt-skill.zip is byte-identical
to it, without deriving the expected name from the pack version. gen_skill.py ties the version-numbered
filename to the skill meta version.

  check_versions.py            check the invariants (also the default; no flags needed)
  check_versions.py --check    same, for parity with the generator gates
  check_versions.py --self-test  deterministic self-test of the shipped-skill-zip leg (a single
                                 byte-identical version-numbered/alias pair passes; none, two, a
                                 byte-differing alias, or a missing alias fails exit 1; a clean fixture
                                 is enumerated in one os.scandir pass; a read error during the listing
                                 fails closed exit 2; an absent downloads dir reads as a missing zip exit 1)

Exit convention (matches the repo's gates):
  0  clean
  1  a real finding (drift, non-increasing sequence, VERSION mismatch, none or more than one
     version-numbered skill zip, a missing alias, or a byte mismatch between the versioned copy and the alias)
  2  malformed input or a read error (fail-closed)
"""
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _gen_common import repo_root, load_toml  # noqa: E402

# Bare SemVer only: no leading zeros, no pre-release/build identifiers (policy R5). The digit class is the
# explicit ASCII [0-9], never `\d`, and the pattern is compiled with re.ASCII (belt-and-suspenders): `\d`
# matches Unicode decimal digits, so `1٢.0.0` (an Arabic-Indic two) matched `[1-9]\d*` and int() then
# read it as 12, letting a non-ASCII-digit version pass every SemVer parse path. ASCII-only closes that gap.
SEMVER = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$", re.ASCII)


def _parse(version):
    """Return the (major, minor, patch) int tuple for a well-formed version, or None if malformed. Uses
    fullmatch, not match: the `$`-anchored SEMVER pattern otherwise accepts a trailing newline (Python's
    `$` matches before a final newline), so `_parse("1.0.0\\n")` was truthy. fullmatch requires the whole
    string to match, closing that trailing-whitespace acceptance. This helper feeds every version
    comparison the release gates make, so the tightening is load-bearing; no legitimate caller passes
    trailing whitespace.

    The int() conversions are guarded: a component within the SemVer grammar can still exceed CPython's
    integer-string-conversion digit limit (default 4300) and raise ValueError. An oversized component is
    malformed input, so it returns None (a cannot-evaluate that every caller already handles as a
    fail-closed malformation) rather than propagating a ValueError up through the release gates."""
    m = SEMVER.fullmatch(version)
    if m is None:
        return None
    try:
        return (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def _scan_versioned_zips(downloads):
    """Enumerate the version-numbered skill zips under `downloads` in ONE guarded os.scandir pass.

    A FileNotFoundError raised by the os.scandir() OPEN means the directory is genuinely absent, and only
    then is [] returned (the caller reads that as a genuinely absent versioned zip, a normal finding, exit
    1). Every OTHER OSError from the open, and ANY error raised DURING the iteration, including a
    mid-iteration FileNotFoundError (a subclass of OSError, e.g. the directory vanishing mid-listing), is
    left to PROPAGATE out of this helper so check_versions()'s `except OSError -> return 2` fails closed. A
    read error is never misread as an absent directory. The name filter mirrors the old aiqt-skill-*.zip
    glob: it requires the aiqt-skill- prefix (excluding the dash-less alias aiqt-skill.zip) and the .zip
    suffix."""
    try:
        scandir_it = os.scandir(downloads)
    except FileNotFoundError:
        return []
    with scandir_it as it:
        return sorted(downloads / e.name for e in it
                      if e.name.startswith("aiqt-skill-") and e.name.endswith(".zip"))


def check_versions(root):
    try:
        data = load_toml(root / "changelog.toml")
    except (OSError, ValueError) as exc:
        print("error: cannot read changelog.toml: {}; fail-closed".format(exc), file=sys.stderr)
        return 2

    releases = data.get("release")
    if not isinstance(releases, list) or not releases:
        print("error: changelog.toml has no [[release]] tables; fail-closed", file=sys.stderr)
        return 2

    # 1 + 2: every release version is well-formed, and the sequence strictly increases in array order.
    parsed = []
    for idx, rel in enumerate(releases):
        if not isinstance(rel, dict):
            print("error: release #{} is not a table ({!r}); fail-closed".format(
                idx + 1, rel), file=sys.stderr)
            return 2
        version = rel.get("version")
        if not isinstance(version, str):
            print("error: release #{} ({}) has no string `version`; fail-closed".format(
                idx + 1, rel.get("title", "?")), file=sys.stderr)
            return 2
        tup = _parse(version)
        if tup is None:
            print("error: release #{} version {!r} is not a bare SemVer X.Y.Z; fail-closed".format(
                idx + 1, version), file=sys.stderr)
            return 2
        parsed.append((version, tup))

    findings = []
    for i in range(1, len(parsed)):
        if parsed[i][1] <= parsed[i - 1][1]:
            findings.append(
                "release #{} version {} is not strictly greater than the preceding {}".format(
                    i + 1, parsed[i][0], parsed[i - 1][0]))

    # 3: the root VERSION file equals the latest release version (single-source). Read the raw BYTES, not
    # read_text: read_text applies universal-newline translation, which silently rewrites a CR-terminated
    # "1.0.0\r\n" to "1.0.0\n" and would let a non-canonical VERSION compare equal to `latest + "\n"` and
    # pass. Reading bytes keeps this on-disk check exact, matching gen_manifest.read_version so the two gates
    # agree that only `latest + "\n"` (no CR, no surrounding whitespace) is the canonical VERSION.
    latest = parsed[-1][0]
    version_path = root / "VERSION"
    try:
        on_disk = version_path.read_bytes().decode("utf-8")
    except FileNotFoundError:
        findings.append("VERSION file is missing; run tools/gen_changelog.py to generate it")
        on_disk = None
    except UnicodeDecodeError as exc:
        print("error: cannot read VERSION (not valid UTF-8: {}); fail-closed".format(exc), file=sys.stderr)
        return 2
    except OSError as exc:
        print("error: cannot read VERSION ({}); fail-closed".format(exc), file=sys.stderr)
        return 2
    if on_disk is not None and on_disk != latest + "\n":
        findings.append("VERSION ({!r}) does not equal the latest release version {!r} plus newline".format(
            on_disk, latest))

    # 4: exactly ONE version-numbered skill zip ships, and the stable "latest" alias
    # site/downloads/aiqt-skill.zip is BYTE-IDENTICAL to it. The skill is now independently versioned (its
    # own version lives in .aiqt/core/skill/skill-source.md, decoupled from the pack SemVer here), so this
    # is a version-AGNOSTIC shipped-surface invariant: it does NOT derive the expected name from the pack
    # version. It globs the version-numbered copies, requires exactly one, and requires the alias to equal
    # it byte-for-byte (the site links to the version-numbered copy; the alias keeps a direct link stable
    # across releases). None, more than one, a missing alias, or a byte mismatch is a normal finding (exit
    # 1); any read failure on a present file is fail-closed (exit 2), so an unreadable zip can never scan
    # as a match. gen_skill.py ties the version-numbered filename to the skill meta version.
    downloads = root / "site" / "downloads"
    alias_rel = "site/downloads/aiqt-skill.zip"
    alias_path = root / alias_rel
    # Enumerate the version-numbered zips in ONE guarded os.scandir pass, never Path.glob: glob can SWALLOW
    # an os.scandir error (a PermissionError on the directory itself) and yield an empty match, which would
    # misread an unreadable dir as "no version-numbered zip ships" (a misleading exit 1) instead of the
    # fail-closed exit 2 a read error demands. Collecting matches directly from the scandir iterator makes a
    # read error raised at ANY point in the listing propagate as OSError -> exit 2 (a cannot-evaluate the
    # two-valued finding path cannot carry); there is no second enumeration to swallow it. An ABSENT
    # directory (FileNotFoundError) is not a read error: it reads as a genuinely absent versioned zip (a
    # normal finding, exit 1), the same outcome an absent downloads dir produced before. The name match
    # mirrors the old aiqt-skill-*.zip glob (the alias aiqt-skill.zip has no dash and is excluded).
    try:
        versioned = _scan_versioned_zips(downloads)
    except OSError as exc:
        print("error: cannot list site/downloads ({}); fail-closed".format(exc), file=sys.stderr)
        return 2
    if not versioned:
        findings.append("no version-numbered skill zip (site/downloads/aiqt-skill-*.zip) ships; exactly "
                        "one must")
    elif len(versioned) > 1:
        names = ", ".join(p.name for p in versioned)
        findings.append("more than one version-numbered skill zip ships ({}); exactly one must".format(
            names))
    else:
        versioned_path = versioned[0]
        versioned_rel = versioned_path.relative_to(root).as_posix()
        if not alias_path.exists():
            findings.append("{} is missing; it must be a byte-identical alias of {}".format(
                alias_rel, versioned_rel))
        else:
            try:
                versioned_bytes = versioned_path.read_bytes()
                alias_bytes = alias_path.read_bytes()
            except OSError as exc:
                print("error: cannot read a skill zip ({}); fail-closed".format(exc), file=sys.stderr)
                return 2
            if versioned_bytes != alias_bytes:
                findings.append("{} is not byte-identical to {} (the stable alias must equal the "
                                "version-numbered copy)".format(alias_rel, versioned_rel))

    if findings:
        print("FAIL: {} version finding(s)".format(len(findings)))
        for finding in findings:
            print("  " + finding)
        print("changelog.toml is the single source; run tools/gen_changelog.py to regenerate VERSION")
        return 1
    print("PASS: {} release version(s) well-formed and increasing; VERSION == latest ({}); the "
          "version-numbered skill zip ships and the stable alias is byte-identical".format(
              len(parsed), latest))
    return 0


# --- self-test --------------------------------------------------------------------------------------
# Proves the version-agnostic shipped-surface leg fires on the things it must catch, against synthetic temp
# trees, never the real tree: a single byte-identical version-numbered/alias pair passes; zero, two, or a
# byte-differing version-numbered zip each fail (exit 1); a missing alias fails (exit 1). Three legs guard
# the single-pass enumeration specifically, and are MUTATION-SENSITIVE against the reverted probe-then-glob
# implementation: a clean fixture enumerates the downloads dir with EXACTLY ONE os.scandir call (the revert
# probes then globs, calling it twice); a read error raised DURING the listing fails closed (exit 2), never
# misread as an absent dir; and a genuinely absent downloads dir (FileNotFoundError at the OPEN) reads as an
# absent versioned zip (exit 1), proving the [] path is distinct from the fail-closed path. Offline, stdlib
# only. The zip names are arbitrary versions, since the leg no longer derives the name from the pack version.

_CHANGELOG = (
    'title = "t"\nnote = "n"\n\n'
    '[[release]]\ntitle = "r"\nversion = "1.0.0"\ndate = "2026-01-01"\nitems = ["x"]\n')


def _write_versions_fixture(root, versioned, alias_bytes):
    """Write a minimal single-source tree (changelog.toml, VERSION) plus the skill zips. `versioned` is a
    list of (filename, bytes) version-numbered copies to write (empty for the none case, two for the
    multiple case); `alias_bytes` is the aiqt-skill.zip bytes, or None to omit the alias."""
    (root / "changelog.toml").write_text(_CHANGELOG, encoding="utf-8")
    (root / "VERSION").write_text("1.0.0\n", encoding="utf-8")
    downloads = root / "site" / "downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    for fname, data in versioned:
        (downloads / fname).write_bytes(data)
    if alias_bytes is not None:
        (downloads / "aiqt-skill.zip").write_bytes(alias_bytes)


class _FakeEntry:
    """A minimal os.DirEntry stand-in carrying only the .name the enumeration filters on."""

    def __init__(self, name):
        self.name = name


class _MidIterScandir:
    """A fake os.scandir result that context-manages, yields ONE DirEntry-like item, then RAISES on the
    next iteration step. This puts the read error DURING the listing rather than at the open, so the
    self-test can prove a mid-iteration vanish fails closed (exit 2) rather than being misread as an absent
    directory (exit 1)."""

    def __init__(self, exc):
        self._exc = exc
        self._items = iter([_FakeEntry("aiqt-skill-1.0.1.zip")])

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def __iter__(self):
        return self

    def __next__(self):
        try:
            return next(self._items)
        except StopIteration:
            raise self._exc


class _CountingScandir:
    """Wraps a real os.scandir result and counts every entry actually yielded through it, so the self-test
    can prove the enumeration ran THROUGH the guarded os.scandir pass rather than through a separate,
    invisible enumeration such as Path.glob (which does not route through a patched os.scandir attribute).
    The single-pass helper consumes the directory through this wrapper; a probe-then-glob revert enters and
    exits the wrapper without consuming any entry, so its yielded-entry count is zero."""

    def __init__(self, it, on_entry):
        self._it = it
        self._on_entry = on_entry

    def __enter__(self):
        self._it.__enter__()
        return self

    def __exit__(self, *a):
        return self._it.__exit__(*a)

    def __iter__(self):
        return self

    def __next__(self):
        e = next(self._it)
        self._on_entry()
        return e


def self_test_main():
    import io
    import shutil
    import tempfile
    from contextlib import redirect_stdout, redirect_stderr

    def _run_quiet(root):
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            return check_versions(root)

    def _single_enumeration(root, failures):
        # Leg (a): on a CLEAN fixture, check_versions enumerates the downloads dir in ONE guarded
        # os.scandir pass and consumes the directory THROUGH it. Counting only calls whose path argument is
        # the downloads dir gives exactly one; counting the entries yielded through that call proves the
        # single pass did the real enumeration. The reverted probe-then-glob enters and exits the guarded
        # scandir without consuming any entry (its `with os.scandir(...): pass` probe) and enumerates via
        # Path.glob, which does not route through the patched os.scandir attribute, so its yielded-entry
        # count is zero: this leg fails against that revert, making the self-test mutation-sensitive.
        downloads = root / "site" / "downloads"
        real_scandir = os.scandir
        calls = {"n": 0}
        entries = {"n": 0}

        def counting_scandir(path, *a, **k):
            it = real_scandir(path, *a, **k)
            try:
                is_downloads = os.fspath(path) == os.fspath(downloads)
            except (TypeError, ValueError):
                is_downloads = False
            if is_downloads:
                calls["n"] += 1
                return _CountingScandir(it, lambda: entries.__setitem__("n", entries["n"] + 1))
            return it

        os.scandir = counting_scandir
        try:
            rc = _run_quiet(root)
        finally:
            os.scandir = real_scandir
        if rc != 0:
            failures.append("leg (a): a clean fixture expected exit 0, got {}".format(rc))
        if calls["n"] != 1:
            failures.append("leg (a): expected exactly ONE os.scandir on the downloads dir, saw {} "
                            "(a separate probe pass would raise this)".format(calls["n"]))
        if entries["n"] < 1:
            failures.append("leg (a): the downloads dir was not enumerated THROUGH the guarded os.scandir "
                            "pass (0 entries consumed); the reverted probe-then-glob enumerates elsewhere")

    def _mid_iteration_error(root, failures):
        # Leg (b): a read error raised DURING the listing (not at the open) fails closed (exit 2). The
        # fake yields one DirEntry-like item then raises FileNotFoundError on the next step, so the error
        # is mid-iteration; it must propagate as OSError -> exit 2, never be misread as an absent dir.
        real_scandir = os.scandir
        os.scandir = lambda path, *a, **k: _MidIterScandir(FileNotFoundError("vanished mid-listing"))
        try:
            rc = _run_quiet(root)
        finally:
            os.scandir = real_scandir
        if rc != 2:
            failures.append("leg (b): a mid-iteration read error expected exit 2 (fail-closed), got "
                            "{}".format(rc))

    failures = []
    try:
        tmp = Path(tempfile.mkdtemp(prefix="aiqt-check-versions-selftest-"))
    except OSError as exc:
        print("SELF-TEST ERROR: no writable temporary directory: {}".format(exc), file=sys.stderr)
        return 2
    try:
        same = b"PK\x03\x04 identical skill zip bytes"
        # 1. exactly one byte-identical version-numbered/alias pair passes (exit 0).
        good = tmp / "good"
        good.mkdir()
        _write_versions_fixture(good, [("aiqt-skill-1.0.1.zip", same)], same)
        if _run_quiet(good) != 0:
            failures.append("a single byte-identical version-numbered/alias pair expected exit 0")

        # 2. no version-numbered zip fails (exit 1).
        none = tmp / "none"
        none.mkdir()
        _write_versions_fixture(none, [], same)
        if _run_quiet(none) != 1:
            failures.append("no version-numbered zip expected exit 1")

        # 3. two version-numbered zips fail (exit 1): exactly one must ship.
        two = tmp / "two"
        two.mkdir()
        _write_versions_fixture(two, [("aiqt-skill-1.0.1.zip", same), ("aiqt-skill-2.0.0.zip", same)], same)
        if _run_quiet(two) != 1:
            failures.append("two version-numbered zips expected exit 1")

        # 4. a byte-differing alias fails (exit 1).
        differ = tmp / "differ"
        differ.mkdir()
        _write_versions_fixture(differ, [("aiqt-skill-1.0.1.zip", same)], same + b" extra")
        if _run_quiet(differ) != 1:
            failures.append("a byte-differing alias expected exit 1")

        # 5. a missing alias fails (exit 1).
        noalias = tmp / "noalias"
        noalias.mkdir()
        _write_versions_fixture(noalias, [("aiqt-skill-1.0.1.zip", same)], None)
        if _run_quiet(noalias) != 1:
            failures.append("a missing alias expected exit 1")

        # (a) SINGLE-ENUMERATION INVARIANT: a clean fixture is enumerated with exactly one os.scandir on
        #     the downloads dir (kills the Finding-B hollow test; fails against the reverted probe-then-glob).
        clean = tmp / "clean"
        clean.mkdir()
        _write_versions_fixture(clean, [("aiqt-skill-1.0.1.zip", same)], same)
        _single_enumeration(clean, failures)

        # (b) MID-ITERATION READ ERROR -> exit 2: a FileNotFoundError raised DURING the listing fails
        #     closed, proving a mid-listing vanish is not misread as an absent dir (covers Finding A). The
        #     fixture is well-formed so the run reaches the downloads leg before the injected fake fires.
        midfix = tmp / "midfix"
        midfix.mkdir()
        _write_versions_fixture(midfix, [("aiqt-skill-1.0.1.zip", same)], same)
        _mid_iteration_error(midfix, failures)

        # (c) ABSENT DIR -> exit 1: with a valid changelog and VERSION but NO site/downloads dir, os.scandir
        #     raises FileNotFoundError at the OPEN -> [] -> the gate reports a missing versioned zip (exit
        #     1), NOT fail-closed. This proves the [] path is distinct from the (b) fail-closed path.
        absent = tmp / "absent"
        absent.mkdir()
        (absent / "changelog.toml").write_text(_CHANGELOG, encoding="utf-8")
        (absent / "VERSION").write_text("1.0.0\n", encoding="utf-8")
        if _run_quiet(absent) != 1:
            failures.append("leg (c): an absent downloads dir expected exit 1 (absent versioned zip), "
                            "not fail-closed")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if failures:
        print("SELF-TEST FAIL:")
        for f in failures:
            print("  - " + f)
        return 1
    print("SELF-TEST PASS: a single byte-identical version-numbered/alias zip pair passes; no "
          "version-numbered zip, two of them, a byte-differing alias, and a missing alias each fail "
          "(exit 1); a clean fixture is enumerated with exactly one os.scandir; a mid-iteration read "
          "error fails closed (exit 2); and an absent downloads dir reads as a missing versioned zip "
          "(exit 1).")
    return 0


def main():
    argv = sys.argv[1:]
    if "--self-test" in argv:
        return self_test_main()
    return check_versions(repo_root())


if __name__ == "__main__":
    sys.exit(main())
