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
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
# opf/tools/ carries the ONE shared bare-SemVer parser (_semver), extracted there for OPF self-containment.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "opf" / "tools"))
from _gen_common import repo_root, load_toml  # noqa: E402
from _semver import SEMVER, _parse  # noqa: E402  re-exported so `from check_versions import _parse` callers are unchanged

def main():
    root = repo_root()
    # Single-source pin (OPF-SELF-CONTAIN): the bare-SemVer grammar and parser this gate uses ARE the
    # _semver objects, never a second copy. A future re-implementation that shadowed the import with a local
    # definition would fork the grammar silently; this identity check fails closed if it ever does. It runs
    # here, in the gate's own executed path, so run_all_checks exercises it on every run.
    import _semver  # noqa: E402
    if not (_parse is _semver._parse and SEMVER is _semver.SEMVER):
        print("error: SemVer primitives are not the single _semver source (a silent fork); fail-closed",
              file=sys.stderr)
        return 2
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
