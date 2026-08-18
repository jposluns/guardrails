#!/usr/bin/env python3
"""Cross-history version-monotonicity gate for the pack SemVer. Offline, stdlib only, fail-closed.

check_versions.py sees only the committed files at HEAD, so a rewrite that replaces the whole release
array with a lower but internally-consistent sequence passes it. This gate (GA-3) adds the cross-history
layer: the changelog as it exists now is compared against the changelog as it existed at a baseline commit,
and (once tags exist) against the shipped tags. check_versions.py is not modified; this gate imports its
SEMVER regex and _parse helper (the sibling-import idiom check_rule_placement.py already uses for gen_rules).

Layer A (changelog-history append-only, buildable now). The authoritative history is the git history of
changelog.toml itself. The baseline file is read with `git show BASE:changelog.toml`; the current file is
read from the working tree via _gen_common.load_toml. Invariants:
  M1 (prefix identity): the base release version list is a prefix of the head release version list, same
     version strings in the same order, with no removal, no insertion before an existing entry, and no
     rewrite of an existing entry's version. Title, date, and items of a shipped release MAY change (this
     matches the established post-release curation practice); only `version` is the identity key.
  M2 (no decrease): latest(head) >= latest(base), asserted separately from M1 so a tail rewrite produces a
     finding that names the decrease, not only a prefix mismatch.
  M3 (well-formedness): both sides parse as bare SemVer via the imported helpers; a malformed version on
     either side is fail-closed (exit 2), since the comparison cannot answer its question on bad input.
Soundness: the protected branch forbids force-push and every change to main passes through this gate at its
merge, so base-vs-head prefix checking composes inductively into monotonicity across all of main's history;
no full-history walk is needed.

Layer B (tag monotonicity, ships DORMANT, activated only by data). A `git tag` returning empty is ambiguous
between "no tags exist" and "tags were not fetched" (a depth-1 CI checkout fetches none), so dormancy is
NEVER decided by probing the environment; it is decided from the single-source file, per guard-input-
soundness. When a release is tagged, its [[release]] entry gains an optional `tag = "vX.Y.Z"` key (inert to
gen_changelog.render_md and check_versions, both of which read neither). Behaviour:
  No release carries `tag`: print NOT APPLICABLE and contribute exit 0 (the honest dormant state).
  Any release carries `tag`:
    T1 the tag value equals "v" + the entry's own version (a malformed tag is a finding);
    T2 the tag resolves locally (a recorded-but-unresolvable tag is exit 2: unfetched and deleted are
       indistinguishable and neither may pass);
    T3 the tagged commit's changelog has that version as its latest release;
    T4 the maximum recorded tag version is not above the head latest version.

Baseline resolution (Layer A), in precedence order:
  1. an explicit --base REF flag (what CI passes);
  2. with no flag, `git merge-base HEAD origin/main`;
  3. if no baseline resolves, exit 2 with a remediation message (never a silent "nothing to compare, pass").
Two distinguishable NON-error states are each printed explicitly and contribute exit 0:
  - the baseline ref resolves but changelog.toml is absent there (introduced since base);
  - HEAD is the root commit (no parent).
Everything else that prevents the comparison (an unresolvable ref, a git failure, unparseable TOML on either
side) is exit 2; every git return code is checked and a nonzero exit is never treated as an empty result.

  check_version_monotonicity.py [--base REF]   check the invariants (default base: merge-base with origin/main)
  check_version_monotonicity.py --self-test    deterministic self-test (no wall clock, no randomness)

Exit convention (matches the repo's gates):
  0  clean, or a printed NOT APPLICABLE
  1  a real finding (append-only violation, decrease, or a tag-layer finding)
  2  malformed input, an unresolvable ref, or a git/read error (fail-closed)
"""
import io
import subprocess
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    sys.exit("error: check_version_monotonicity.py requires Python 3.11+ (tomllib).")

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _gen_common import repo_root, load_toml  # noqa: E402
from check_versions import _parse  # noqa: E402  reuse the shipped bare-SemVer parser (M3 well-formedness)


class GateError(Exception):
    """An input the gate cannot read, parse, or resolve. Caught at run() and reported as exit 2
    (fail-closed): an unreadable or unresolvable input is never treated as an empty or clean result."""


# --- git helpers (every return code is checked; a nonzero exit is never treated as empty) -----------

def _git(root, args):
    """Run `git -C root <args>`. Returns the CompletedProcess; the caller inspects returncode. A missing
    git binary is itself a fail-closed condition (the comparison cannot run), surfaced as a GateError."""
    try:
        return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)
    except OSError as exc:
        raise GateError("git is not available: {}".format(exc))


def _is_root_commit(root):
    """True if HEAD has no parent (the degenerate CI push case). Raises if HEAD itself does not resolve."""
    if _git(root, ["rev-parse", "--verify", "--quiet", "HEAD"]).returncode != 0:
        raise GateError("cannot resolve HEAD")
    # A root commit has no HEAD^; --verify --quiet returns nonzero cleanly rather than erroring.
    return _git(root, ["rev-parse", "--verify", "--quiet", "HEAD^"]).returncode != 0


def _resolve_commit(root, ref):
    proc = _git(root, ["rev-parse", "--verify", "--quiet", ref + "^{commit}"])
    if proc.returncode != 0:
        raise GateError("cannot resolve baseline ref {!r} to a commit".format(ref))
    return proc.stdout.strip()


def _default_base(root):
    proc = _git(root, ["merge-base", "HEAD", "origin/main"])
    if proc.returncode != 0:
        raise GateError("no baseline resolved: `git merge-base HEAD origin/main` failed; pass --base REF, "
                        "or fetch origin/main")
    return proc.stdout.strip()


def _path_in_commit(root, commit, path):
    proc = _git(root, ["ls-tree", "--name-only", commit, "--", path])
    if proc.returncode != 0:
        raise GateError("git ls-tree failed for {} at {}: {}".format(path, commit, proc.stderr.strip()))
    return proc.stdout.strip() != ""


def _show_file(root, ref, path):
    proc = _git(root, ["show", "{}:{}".format(ref, path)])
    if proc.returncode != 0:
        raise GateError("git show {}:{} failed: {}".format(ref, path, proc.stderr.strip()))
    return proc.stdout


def _resolve_tag(root, tag):
    return _git(root, ["rev-parse", "--verify", "--quiet", "refs/tags/" + tag]).returncode == 0


# --- pure logic (M1/M2 and the tag name/ceiling checks; always run in --self-test) ------------------

def check_prefix(base_versions, head_versions):
    """M1. base_versions must be an exact prefix of head_versions. Returns a list of finding strings."""
    findings = []
    if len(base_versions) > len(head_versions):
        findings.append("the base changelog records {} release(s) but head records {}; an existing "
                        "release entry was removed (append-only violation)".format(
                            len(base_versions), len(head_versions)))
    for i in range(min(len(base_versions), len(head_versions))):
        if base_versions[i] != head_versions[i]:
            findings.append("release #{}: base version {} != head version {} (an existing release's "
                            "version was rewritten, reordered, or an entry inserted before it; only "
                            "version is the identity key)".format(i + 1, base_versions[i], head_versions[i]))
    return findings


def check_no_decrease(base_versions, head_versions):
    """M2. latest(head) >= latest(base). Returns a list of finding strings. Callers guarantee both sides
    are well-formed SemVer (M3), so _parse returns a tuple here."""
    if not base_versions or not head_versions:
        return []
    base_latest, head_latest = base_versions[-1], head_versions[-1]
    if _parse(head_latest) < _parse(base_latest):
        return ["the latest head version {} is lower than the latest base version {} "
                "(no-decrease violation)".format(head_latest, base_latest)]
    return []


def _tag_version(tag):
    """The numeric part of a `vX.Y.Z` tag string, or None if it is not v-prefixed."""
    return tag[1:] if isinstance(tag, str) and tag.startswith("v") else None


def check_tag_names(tagged):
    """T1. Each recorded tag must equal 'v' + its entry's version. tagged is a list of (version, tag)."""
    findings = []
    for version, tag in tagged:
        if tag != "v" + version:
            findings.append("release {} records tag {!r}; expected {!r} (a tag must be v + version)".format(
                version, tag, "v" + version))
    return findings


def check_tag_ceiling(tagged, head_latest):
    """T4. No recorded tag version may exceed the head latest version. tagged is a list of (version, tag)."""
    findings = []
    head_tup = _parse(head_latest)
    for version, tag in tagged:
        num = _tag_version(tag)
        tup = _parse(num) if num is not None else None
        if tup is not None and head_tup is not None and tup > head_tup:
            findings.append("recorded tag {} is above the head latest version {} (the pack must not move "
                            "behind a shipped tag)".format(tag, head_latest))
    return findings


# --- release extraction (M3 well-formedness on each side) -------------------------------------------

def _releases_from_data(data, side):
    """Validate and return the list of {'version', 'tag'} dicts from parsed changelog data. Raises
    GateError (fail-closed) on a missing array, a non-table entry, or a missing/malformed version."""
    releases = data.get("release")
    if side == "head":
        if not isinstance(releases, list) or not releases:
            raise GateError("changelog.toml has no [[release]] tables")
    else:
        # A baseline with release = [] is a valid (empty) prefix; only an absent array is malformed.
        if not isinstance(releases, list):
            raise GateError("baseline changelog.toml has no [[release]] array")
    out = []
    for idx, rel in enumerate(releases):
        if not isinstance(rel, dict):
            raise GateError("{} release #{} is not a table ({!r})".format(side, idx + 1, rel))
        version = rel.get("version")
        if not isinstance(version, str) or _parse(version) is None:
            raise GateError("{} release #{} has a missing or malformed version {!r}".format(
                side, idx + 1, version))
        out.append({"version": version, "tag": rel.get("tag")})
    return out


def _load_head_releases(root):
    try:
        data = load_toml(root / "changelog.toml")
    except (OSError, ValueError) as exc:
        raise GateError("cannot read changelog.toml at HEAD: {}".format(exc))
    return _releases_from_data(data, "head")


def _parse_base_releases(text):
    try:
        data = tomllib.loads(text)
    except (tomllib.TOMLDecodeError, ValueError) as exc:
        raise GateError("baseline changelog.toml does not parse: {}".format(exc))
    return _releases_from_data(data, "base")


# --- the two layers ---------------------------------------------------------------------------------

def layer_a(root, base, head_releases):
    """Changelog-history append-only. Prints its own status; returns a list of finding strings. Raises
    GateError on any fail-closed condition."""
    if _is_root_commit(root):
        print("changelog-history: NOT APPLICABLE (HEAD is the root commit; no baseline to compare)")
        return []
    if base is None:
        base = _default_base(root)
    base_commit = _resolve_commit(root, base)
    if not _path_in_commit(root, base_commit, "changelog.toml"):
        print("changelog-history: NOT APPLICABLE (changelog.toml is absent at base {}; introduced since "
              "base)".format(base))
        return []
    base_versions = [r["version"] for r in _parse_base_releases(_show_file(root, base_commit, "changelog.toml"))]
    head_versions = [r["version"] for r in head_releases]
    findings = check_prefix(base_versions, head_versions) + check_no_decrease(base_versions, head_versions)
    if not findings:
        print("changelog-history: PASS (base {}: {} base release(s) prefix-preserved into {} head "
              "release(s), no decrease)".format(base, len(base_versions), len(head_versions)))
    return findings


def layer_b(root, head_releases):
    """Tag monotonicity, dormant until a release records a tag. Prints its own status; returns a list of
    finding strings. Raises GateError on any fail-closed condition."""
    tagged = [(r["version"], r["tag"]) for r in head_releases if r.get("tag") is not None]
    if not tagged:
        print("tag-monotonicity: NOT APPLICABLE: no release records a tag; tag monotonicity activates "
              "with the first tagged release")
        return []
    for version, tag in tagged:
        if not isinstance(tag, str):
            raise GateError("release {} has a non-string tag {!r}".format(version, tag))
    findings = check_tag_names(tagged)
    head_latest = head_releases[-1]["version"]
    for version, tag in tagged:
        if not _resolve_tag(root, tag):
            raise GateError("release {} records tag {} but no such tag object is visible (fetch tags, "
                            "or the tag was deleted)".format(version, tag))
        tagged_versions = [r["version"] for r in _parse_base_releases(_show_file(root, tag, "changelog.toml"))]
        if not tagged_versions or tagged_versions[-1] != version:
            findings.append("tag {} points at a commit whose latest release is {}, not {}".format(
                tag, tagged_versions[-1] if tagged_versions else "(none)", version))
    findings += check_tag_ceiling(tagged, head_latest)
    if not findings:
        print("tag-monotonicity: PASS ({} tagged release(s) verified)".format(len(tagged)))
    return findings


def run(root, base):
    """Run both layers against `root`, resolving Layer A's baseline from `base` (None means the default
    merge-base). Returns the exit code 0/1/2."""
    try:
        head_releases = _load_head_releases(root)
        findings = layer_a(root, base, head_releases) + layer_b(root, head_releases)
    except GateError as exc:
        print("error: {}; fail-closed".format(exc), file=sys.stderr)
        return 2
    if findings:
        print("FAIL: {} version-monotonicity finding(s)".format(len(findings)))
        for finding in findings:
            print("  " + finding)
        return 1
    print("PASS: version monotonicity holds (changelog history append-only; tag layer checked)")
    return 0


# --- self-test --------------------------------------------------------------------------------------
# Pure-function cases (prefix/no-decrease/tag logic) always run and are deterministic. The git-level cases
# build throwaway repositories in a private tempdir and are skipped with a printed note (never a false
# pass) where git or a writable tempdir is unavailable; CI always has both. No wall clock, no randomness.

def _changelog_text(versions, tag_on=None):
    tag_on = tag_on or {}
    lines = ['title = "Changelog: self-test"', 'note = "self-test"', ""]
    for v in versions:
        lines += ["[[release]]", 'title = "r"', 'version = "{}"'.format(v), 'date = "2026-01-01"']
        if v in tag_on:
            lines.append('tag = "{}"'.format(tag_on[v]))
        lines += ['items = ["x"]', ""]
    return "\n".join(lines)


def _run_quiet(root, base):
    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        return run(root, base)


def self_test_main():
    failures = []

    # M1 (prefix identity): (base, head, expect_a_finding).
    prefix_cases = [
        (["1.0.0"], ["1.0.0"], False),                          # identical
        (["1.0.0"], ["1.0.0", "1.1.0"], False),                 # append one
        (["1.0.0"], ["1.0.0", "1.1.0", "1.2.0"], False),        # append two
        (["1.0.0", "1.1.0"], ["1.0.0", "0.9.0"], True),         # tail rewrite to a lower version
        (["1.0.0", "1.1.0"], ["1.0.0", "1.2.0"], True),         # tail rewrite to a higher version
        (["1.0.0", "1.1.0"], ["1.0.0"], True),                  # removal
        (["1.0.0", "1.1.0"], ["1.0.0", "1.0.5", "1.1.0"], True),  # insertion before an existing entry
        (["1.0.0", "1.1.0"], ["1.1.0", "1.0.0"], True),         # reorder
        ([], ["1.0.0"], False),                                 # empty base
    ]
    for base, head, expect in prefix_cases:
        got = bool(check_prefix(base, head))
        if got != expect:
            failures.append("check_prefix({}, {}) finding={}; expected {}".format(base, head, got, expect))

    # M2 (no decrease): (base, head, expect_a_finding).
    nodecrease_cases = [
        (["1.0.0", "1.1.0"], ["1.0.0", "0.9.0"], True),   # decrease
        (["1.0.0"], ["1.0.0", "1.1.0"], False),           # increase
        (["1.0.0"], ["1.0.0"], False),                    # equal
        ([], ["1.0.0"], False),                           # empty base
    ]
    for base, head, expect in nodecrease_cases:
        got = bool(check_no_decrease(base, head))
        if got != expect:
            failures.append("check_no_decrease({}, {}) finding={}; expected {}".format(
                base, head, got, expect))

    # Tag logic (pure): name match/mismatch, ceiling equal/above.
    if check_tag_names([("1.0.0", "v1.0.0")]):
        failures.append("check_tag_names expected no finding for a matching tag")
    if not check_tag_names([("1.0.0", "v1.0.1")]):
        failures.append("check_tag_names expected a finding for a tag/version mismatch")
    if check_tag_ceiling([("1.0.0", "v1.0.0")], "1.0.0"):
        failures.append("check_tag_ceiling expected no finding when the tag equals the head latest")
    if not check_tag_ceiling([("1.0.0", "v2.0.0")], "1.0.0"):
        failures.append("check_tag_ceiling expected a finding when a tag is above the head latest")

    # Git-level cases: real repositories in a private tempdir. Skipped (with a note) where unavailable.
    import shutil
    import tempfile

    git_ok = True
    try:
        if subprocess.run(["git", "--version"], capture_output=True, text=True).returncode != 0:
            git_ok = False
    except OSError:
        git_ok = False
    try:
        base_tmp = Path(tempfile.mkdtemp(prefix="aiqt-monotonicity-selftest-")) if git_ok else None
    except OSError:
        base_tmp = None

    if not git_ok or base_tmp is None:
        print("SELF-TEST NOTE: git or a writable temp directory is unavailable; git-level cases SKIPPED "
              "(the pure prefix/no-decrease/tag coverage above still ran)", file=sys.stderr)
    else:
        def _init(path):
            path.mkdir(parents=True, exist_ok=True)
            for args in (["init", "-q"], ["config", "user.name", "AIQT Self-Test"],
                         ["config", "user.email", "selftest@example.invalid"]):
                subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True, text=True)

        def _write(path, text):
            (path / "changelog.toml").write_text(text, encoding="utf-8")

        def _commit(path, msg):
            subprocess.run(["git", "-C", str(path), "add", "-A"], check=True, capture_output=True, text=True)
            subprocess.run(["git", "-C", str(path), "commit", "-q", "-m", msg],
                           check=True, capture_output=True, text=True)

        def _root(path):
            # A parent commit, so HEAD is not itself the root commit; the root-commit N/A path is exercised
            # separately below rather than masking the real comparison in every fixture.
            (path / "README.md").write_text("seed\n", encoding="utf-8")
            _commit(path, "initial")

        try:
            # (1) append-in-working-tree passes; (2) a tail rewrite fails; (4) a garbage base is exit 2.
            r1 = base_tmp / "append"
            _init(r1)
            _root(r1)
            _write(r1, _changelog_text(["1.0.0"]))
            _commit(r1, "seed 1.0.0")
            _write(r1, _changelog_text(["1.0.0", "1.1.0"]))
            if _run_quiet(r1, "HEAD") != 0:
                failures.append("git case: appending 1.1.0 with --base HEAD expected exit 0")
            _write(r1, _changelog_text(["0.9.0"]))
            if _run_quiet(r1, "HEAD") != 1:
                failures.append("git case: rewriting 1.0.0 to 0.9.0 with --base HEAD expected exit 1")
            if _run_quiet(r1, "no-such-ref-xyz") != 2:
                failures.append("git case: a garbage --base expected fail-closed exit 2")

            # (3) a base commit lacking changelog.toml yields the printed NOT APPLICABLE (exit 0). The
            # root commit here has no changelog; changelog arrives at HEAD, so --base HEAD~1 is "absent".
            r2 = base_tmp / "introduced"
            _init(r2)
            _root(r2)
            _write(r2, _changelog_text(["1.0.0"]))
            _commit(r2, "introduce changelog")
            if _run_quiet(r2, "HEAD~1") != 0:
                failures.append("git case: changelog absent at base expected NOT APPLICABLE exit 0")

            # (3b) a true root-commit HEAD yields the printed NOT APPLICABLE (exit 0).
            r2b = base_tmp / "rootcommit"
            _init(r2b)
            _write(r2b, _changelog_text(["1.0.0"]))
            _commit(r2b, "sole commit")
            if _run_quiet(r2b, "HEAD") != 0:
                failures.append("git case: a root-commit HEAD expected NOT APPLICABLE exit 0")

            # (5) a recorded tag with no tag object is exit 2; (6) creating the tag makes it pass.
            r3 = base_tmp / "tagged"
            _init(r3)
            _root(r3)
            _write(r3, _changelog_text(["1.0.0"]))
            _commit(r3, "seed 1.0.0 untagged")
            _write(r3, _changelog_text(["1.0.0"], tag_on={"1.0.0": "v1.0.0"}))
            if _run_quiet(r3, "HEAD") != 2:
                failures.append("git case: a recorded tag with no tag object expected fail-closed exit 2")
            subprocess.run(["git", "-C", str(r3), "tag", "v1.0.0", "HEAD"],
                           check=True, capture_output=True, text=True)
            if _run_quiet(r3, "HEAD") != 0:
                failures.append("git case: creating the recorded tag expected exit 0")
        finally:
            shutil.rmtree(base_tmp, ignore_errors=True)

    if failures:
        print("SELF-TEST FAIL:")
        for f in failures:
            print("  - " + f)
        return 1
    print("SELF-TEST PASS: prefix identity (M1), no-decrease (M2), tag name/ceiling logic, and the "
          "git-level history and tag cases all hold")
    return 0


def _parse_args(argv):
    base = None
    self_test = False
    i = 0
    while i < len(argv):
        if argv[i] == "--base" and i + 1 < len(argv):
            base = argv[i + 1]
            i += 2
        elif argv[i] == "--self-test":
            self_test = True
            i += 1
        else:
            print("usage: check_version_monotonicity.py [--base REF] | --self-test", file=sys.stderr)
            return None
    return (base, self_test)


def main():
    parsed = _parse_args(sys.argv[1:])
    if parsed is None:
        return 2
    base, self_test = parsed
    if self_test:
        return self_test_main()
    return run(repo_root(), base)


if __name__ == "__main__":
    sys.exit(main())
