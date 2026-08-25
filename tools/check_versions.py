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

  check_versions.py            check the invariants (also the default; no flags needed)
  check_versions.py --check    same, for parity with the generator gates

Exit convention (matches the repo's gates):
  0  clean
  1  a real finding (drift, non-increasing sequence, VERSION mismatch)
  2  malformed input or a read error (fail-closed)
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _gen_common import repo_root, load_toml  # noqa: E402

# Bare SemVer only: no leading zeros, no pre-release/build identifiers (policy R5).
SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")


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


def main():
    root = repo_root()
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

    # 3: the root VERSION file equals the latest release version (single-source).
    latest = parsed[-1][0]
    version_path = root / "VERSION"
    try:
        on_disk = version_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        findings.append("VERSION file is missing; run tools/gen_changelog.py to generate it")
        on_disk = None
    except OSError as exc:
        print("error: cannot read VERSION ({}); fail-closed".format(exc), file=sys.stderr)
        return 2
    if on_disk is not None and on_disk != latest + "\n":
        findings.append("VERSION ({!r}) does not equal the latest release version {!r} plus newline".format(
            on_disk, latest))

    if findings:
        print("FAIL: {} version finding(s)".format(len(findings)))
        for finding in findings:
            print("  " + finding)
        print("changelog.toml is the single source; run tools/gen_changelog.py to regenerate VERSION")
        return 1
    print("PASS: {} release version(s) well-formed and increasing; VERSION == latest ({})".format(
        len(parsed), latest))
    return 0


if __name__ == "__main__":
    sys.exit(main())
