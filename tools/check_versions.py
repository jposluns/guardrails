#!/usr/bin/env python3
"""Version-format and single-source gate for the pack SemVer. Offline, stdlib only, fail-closed.

changelog.toml is the single source of truth for the pack's SemVer; the root VERSION file is generated
from it by gen_changelog.py. This gate asserts the invariants that keep the two from diverging and keep the
version well-formed:

  1. Every [[release]] carries a `version` that is a bare SemVer `MAJOR.MINOR.PATCH` (no pre-release or
     build identifier; policy R5 accepts only X.Y.Z before and after 1.0.0). The optional [unreleased]
     table (main between release cuts) has keys among version, title and items; its `version` is required
     and is a bare SemVer too (the -dev suffix is generated, never authored). A malformed table is exit 2.
  2. The release versions are strictly SemVer-increasing in array order (releases are append-only, oldest
     to newest), and an [unreleased] version is strictly greater than the latest release (exit 1 if not).
  3. The root VERSION file is the single-source derivative: the [unreleased] version plus "-dev" and a
     newline when [unreleased] is present, otherwise the latest release version plus a newline.

This is NOT the cross-release version-monotonicity gate (tools/check_version_monotonicity.py), which compares
the committed base and head and the tagged history; this gate covers only what is decidable from the
working-tree files.

  check_versions.py              check the invariants (also the default; no flags needed)
  check_versions.py --check      same, for parity with the generator gates
  check_versions.py --self-test  run the built-in vectors (also covering gen_changelog's VERSION and
                                 CHANGELOG.md rendering of [unreleased])

Exit convention (matches the repo's gates):
  0  clean
  1  a real finding (drift, non-increasing sequence, VERSION mismatch)
  2  malformed input or a read error (fail-closed)
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: check_versions.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
# opf/tools/ carries the ONE shared bare-SemVer parser (_semver), extracted there for OPF self-containment.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "opf" / "tools"))
from _gen_common import repo_root, load_toml  # noqa: E402
from _semver import SEMVER, _parse  # noqa: E402  re-exported so `from check_versions import _parse` callers are unchanged
# The tree-version grammar (X.Y.Z or X.Y.Z-dev) and the [unreleased] shape check, re-exported from the same
# single source so tools/ callers never fork them.
from _semver import DEV_SUFFIX, parse_tree_version, tree_precedence, unreleased_problem  # noqa: E402


class Malformed(ValueError):
    """Malformed input: the gate cannot evaluate (exit 2)."""


def evaluate(data, on_disk):
    """Judge one parsed changelog.toml and the VERSION text (str, or None when the file is missing).

    Returns (findings, summary). Raises Malformed for exit-2 input. Pure: no file access."""
    releases = data.get("release")
    if not isinstance(releases, list) or not releases:
        raise Malformed("changelog.toml has no [[release]] tables")

    # 1 + 2: every release version is well-formed, and the sequence strictly increases in array order.
    parsed = []
    for idx, rel in enumerate(releases):
        if not isinstance(rel, dict):
            raise Malformed("release #{} is not a table ({!r})".format(idx + 1, rel))
        version = rel.get("version")
        if not isinstance(version, str):
            raise Malformed("release #{} ({}) has no string `version`".format(idx + 1, rel.get("title", "?")))
        tup = _parse(version)
        if tup is None:
            raise Malformed("release #{} version {!r} is not a bare SemVer X.Y.Z".format(idx + 1, version))
        parsed.append((version, tup))

    findings = []
    for i in range(1, len(parsed)):
        if parsed[i][1] <= parsed[i - 1][1]:
            findings.append(
                "release #{} version {} is not strictly greater than the preceding {}".format(
                    i + 1, parsed[i][0], parsed[i - 1][0]))

    latest = parsed[-1][0]
    expected = latest + "\n"
    planned = None
    if "unreleased" in data:
        problem = unreleased_problem(data["unreleased"])
        if problem is not None:
            raise Malformed("changelog.toml " + problem)
        planned = data["unreleased"]["version"]
        if _parse(planned) <= parsed[-1][1]:
            findings.append("[unreleased] version {} is not strictly greater than the latest release {}".format(
                planned, latest))
        expected = planned + DEV_SUFFIX + "\n"

    # 3: the root VERSION file is the single-source derivative. The caller reads raw BYTES, not read_text:
    # read_text applies universal-newline translation, which silently rewrites a CR-terminated "1.0.0\r\n" to
    # "1.0.0\n" and would let a non-canonical VERSION compare equal. Exact comparison keeps this check in step
    # with gen_manifest.read_version: only the expected text (no CR, no surrounding whitespace) is canonical.
    if on_disk is None:
        findings.append("VERSION file is missing; run tools/gen_changelog.py to generate it")
    elif on_disk != expected:
        if planned is None:
            findings.append("VERSION ({!r}) does not equal the latest release version {!r} plus newline".format(
                on_disk, latest))
        else:
            findings.append("VERSION ({!r}) does not equal the [unreleased] version {!r} plus {!r} and newline "
                            "(latest release {})".format(on_disk, planned, DEV_SUFFIX, latest))
    if planned is None:
        summary = "{} release version(s) well-formed and increasing; VERSION == latest ({})".format(
            len(parsed), latest)
    else:
        summary = ("{} release version(s) well-formed and increasing; [unreleased] {} above latest {}; VERSION "
                   "== {}".format(len(parsed), planned, latest, planned + DEV_SUFFIX))
    return findings, summary


def main():
    root = repo_root()
    # Single-source pin (OPF-SELF-CONTAIN): the SemVer grammars and parsers this gate uses ARE the _semver
    # objects, never a second copy. A future re-implementation that shadowed the import with a local
    # definition would fork the grammar silently; this identity check fails closed if it ever does. It runs
    # here, in the gate's own executed path, so run_all_checks exercises it on every run.
    import _semver  # noqa: E402
    if not (_parse is _semver._parse and SEMVER is _semver.SEMVER
            and parse_tree_version is _semver.parse_tree_version
            and unreleased_problem is _semver.unreleased_problem and DEV_SUFFIX == _semver.DEV_SUFFIX):
        print("error: SemVer primitives are not the single _semver source (a silent fork); fail-closed",
              file=sys.stderr)
        return 2
    try:
        data = load_toml(root / "changelog.toml")
    except (OSError, ValueError) as exc:
        print("error: cannot read changelog.toml: {}; fail-closed".format(exc), file=sys.stderr)
        return 2

    version_path = root / "VERSION"
    try:
        on_disk = version_path.read_bytes().decode("utf-8")
    except FileNotFoundError:
        on_disk = None
    except UnicodeDecodeError as exc:
        print("error: cannot read VERSION (not valid UTF-8: {}); fail-closed".format(exc), file=sys.stderr)
        return 2
    except OSError as exc:
        print("error: cannot read VERSION ({}); fail-closed".format(exc), file=sys.stderr)
        return 2

    try:
        findings, summary = evaluate(data, on_disk)
    except Malformed as exc:
        print("error: {}; fail-closed".format(exc), file=sys.stderr)
        return 2
    if findings:
        print("FAIL: {} version finding(s)".format(len(findings)))
        for finding in findings:
            print("  " + finding)
        print("changelog.toml is the single source; run tools/gen_changelog.py to regenerate VERSION")
        return 1
    print("PASS: " + summary)
    return 0


def _exit_of(data, on_disk):
    try:
        findings, _ = evaluate(data, on_disk)
    except Malformed:
        return 2
    return 1 if findings else 0


def self_test():
    """Built-in vectors. Each names the guard that turns it red when reverted."""
    import tomllib
    import gen_changelog  # imported here only: gen_manifest imports this module at load time

    head = 'title = "T"\nnote = "N"\n\n[[release]]\ntitle = "R"\nversion = "1.0.5"\ndate = "2026-09-18"\n'
    dev = head + '\n[unreleased]\nversion = "1.1.0"\n'
    cases = [
        # (name, changelog text, VERSION text or None, expected exit)
        ("released-plain", head, "1.0.5\n", 0),
        ("development", dev, "1.1.0-dev\n", 0),                       # guard: the -dev expectation
        ("development-stale-version", dev, "1.0.5\n", 1),             # guard: the -dev expectation
        ("development-plain-version", dev, "1.1.0\n", 1),
        ("released-dev-version", head, "1.0.5-dev\n", 1),
        ("development-crlf", dev, "1.1.0-dev\r\n", 1),
        ("development-missing-version", dev, None, 1),
        ("unreleased-equal-latest", head + '\n[unreleased]\nversion = "1.0.5"\n', "1.0.5-dev\n", 1),  # guard: >
        ("unreleased-below-latest", head + '\n[unreleased]\nversion = "1.0.4"\n', "1.0.4-dev\n", 1),  # guard: >
        ("unreleased-authored-suffix", head + '\n[unreleased]\nversion = "1.1.0-dev"\n', "1.1.0-dev\n", 2),
        ("unreleased-rc", head + '\n[unreleased]\nversion = "1.1.0-rc1"\n', "1.1.0-rc1-dev\n", 2),
        ("unreleased-unknown-key", dev + 'date = "2026-11-19"\n', "1.1.0-dev\n", 2),  # guard: the shape check
        ("unreleased-no-version", head + '\n[unreleased]\ntitle = "x"\n', "1.0.5\n", 2),  # guard: the shape check
        ("unreleased-items-type", dev + 'items = "x"\n', "1.1.0-dev\n", 2),
        ("unreleased-not-table", 'unreleased = "1.1.0"\n' + head, "1.1.0-dev\n", 2),  # guard: the shape check
        ("unreleased-blank-title", dev + 'title = " "\n', "1.1.0-dev\n", 2),
        ("unreleased-version-int", head + '\n[unreleased]\nversion = 7\n', "7-dev\n", 2),  # guard: the shape check
    ]
    failures = []
    for name, text, on_disk, want in cases:
        # A vector whose input reaches past a missing guard (the shape check, say) fails by its own name,
        # never by a traceback that hides every later vector.
        try:
            got = _exit_of(tomllib.loads(text), on_disk)
        except Exception as exc:  # noqa: BLE001  a raised error is this vector's failure, reported by name
            failures.append("{}: raised {} (expected exit {})".format(name, type(exc).__name__, want))
            continue
        if got != want:
            failures.append("{}: exit {} (expected {})".format(name, got, want))

    # gen_changelog renders the same single source: VERSION and the CHANGELOG.md Unreleased section.
    full = dev + 'title = "Next"\nitems = ["One", "Two"]\n'
    renders = [
        ("render-version-plain", gen_changelog.render_version(tomllib.loads(head)), "1.0.5\n"),
        ("render-version-development", gen_changelog.render_version(tomllib.loads(dev)), "1.1.0-dev\n"),
        ("render-md-plain", gen_changelog.render_md(tomllib.loads(head)),
         "# T\n\nN\n\n## 1.0.5: R (2026-09-18)\n"),
        ("render-md-development", gen_changelog.render_md(tomllib.loads(full)),
         "# T\n\nN\n\n## 1.0.5: R (2026-09-18)\n\n## Unreleased (toward 1.1.0): Next\n\n- One\n- Two\n"),
        ("render-md-development-bare", gen_changelog.render_md(tomllib.loads(dev)),
         "# T\n\nN\n\n## 1.0.5: R (2026-09-18)\n\n## Unreleased (toward 1.1.0)\n"),
    ]
    for name, got, want in renders:
        if got != want:
            failures.append("{}: rendered {!r} (expected {!r})".format(name, got, want))
    for name, text in (("render-refuses-authored-suffix", head + '\n[unreleased]\nversion = "1.1.0-dev"\n'),
                       ("render-refuses-unknown-key", dev + 'date = "x"\n')):
        try:
            gen_changelog.render_version(tomllib.loads(text))
        except TypeError:
            continue
        failures.append(name + ": rendered instead of refusing")
    # A self-consistent generator/gate pair: what gen_changelog renders, this gate accepts.
    if _exit_of(tomllib.loads(full), gen_changelog.render_version(tomllib.loads(full))) != 0:
        failures.append("generator-gate-agreement: the gate refuses gen_changelog's own VERSION")

    total = len(cases) + len(renders) + 3
    if failures:
        print("FAIL: check_versions self-test, {} of {} vector(s) failed".format(len(failures), total))
        for failure in failures:
            print("  " + failure)
        return 1
    print("PASS: check_versions self-test, {} vector(s)".format(total))
    return 0


if __name__ == "__main__":
    args = sys.argv[1:]
    if args == ["--self-test"]:
        sys.exit(self_test())
    if args not in ([], ["--check"]):
        print("usage: check_versions.py [--check | --self-test]", file=sys.stderr)
        sys.exit(2)
    sys.exit(main())
