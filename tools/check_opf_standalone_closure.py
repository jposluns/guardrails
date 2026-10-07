#!/usr/bin/env python3
"""OPF standalone-closure gate (OPF-SELF-CONTAIN, hold H-12 ratified).

This gate proves, by PHYSICAL ABSENCE, that the `opf/` subtree is dependency-closed: it materializes
`opf/` ALONE into a throwaway temporary directory with NO AIQT checkout reachable, then runs the OPF
tooling's own gate subset there. Because the subset runs under `python3 -I` (isolated: neither the current
working directory nor PYTHONPATH is on sys.path) with only the copied `opf/tools/` directory reachable, any
surviving UPWARD edge into `tools/` (a `from check_versions import _parse`, an `import check_byte_canon`,
the emitter's pinned sibling-file authority load) fails at import or run time
in the isolated copy. Grep evidence is not accepted; closure is OBSERVED, not inferred. This is the durable
check the restructure carries: it fails without the self-containment property (see --self-test).

The two closure-critical edges are lazy or dynamic, so an import-only or `--help` smoke test would miss a
survivor. The subset therefore drives the exact command paths that exercise them: `opf.py --self-test` runs
the render leg (the lazy `_byte_canon` import) and the emitter self-test (the pinned sibling-file authority
load); the commonmark selftests exercise the vendored parser and its manifest.

  check_opf_standalone_closure.py             materialize opf/ alone and run the subset (also the default)
  check_opf_standalone_closure.py --self-test  the same, PLUS a deliberate flip proving it fails without the move

Exit convention (matches the repo's gates):
  0  the isolated opf/ subtree verifies (closure holds)
  1  a real finding (a subset gate failed in isolation: closure is broken)
  2  malformed input or a harness error (fail-closed): opf/ absent/unreadable, copy failed, no
     interpreter, or a subset member KILLED at its time bound (a timeout is cannot-evaluate: the
     member never finished, so closure was neither observed nor refuted; it is reported as
     TIMEOUT, never as a closure finding)
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    sys.stderr.write(
        "error: check_opf_standalone_closure.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
        "Nothing was run (cannot evaluate).\n"
        % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
    raise SystemExit(2)

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _gen_common import repo_root  # noqa: E402

# The OPF gate subset, relative to the copied opf/tools/ directory. Each entry is (name, [args...]); the
# self-test legs are deterministic and git-independent (they build their own throwaway fixtures), so they
# run correctly in an isolated copy that is not itself an adopter or a git repository, while still driving
# the lazy render / emit paths that a survivor would break.
_SUBSET = [
    ("opf-homes-selftest", "check_opf_homes.py", ["--self-test"]),
    ("opf-homes-contract", "check_opf_homes.py", []),
    ("opf-tooling-selftest", "opf.py", ["--self-test"]),
    ("opf-drift-selftest", "check_opf_drift.py", ["--self-test"]),
    ("opf-doctor-selftest", "check_opf_doctor.py", ["--self-test"]),
    ("opf-init-selftest", "check_opf_init.py", ["--self-test"]),
    ("opf-init-contract-validator-selftest", "_opf_init_contract.py", ["--self-test"]),
    ("opf-init-contract-check-selftest", "check_opf_init_contract.py", ["--self-test"]),
    ("opf-upgrade-selftest", "check_opf_upgrade.py", ["--self-test"]),
    ("opf-adopt-selftest", "_opf_adopt.py", ["--self-test"]),
    ("opf-adopt-apply-selftest", "_opf_adopt_apply.py", ["--self-test"]),
    ("opf-adopt-hook-selftest", "_opf_adopt_hook.py", ["--self-test"]),
    ("opf-pack-manifest-selftest", "_opf_pack_manifest.py", ["--self-test"]),
    ("opf-adopt-observe-selftest", "_opf_adopt_observe.py", ["--self-test"]),
    ("opf-prompt-pack-selftest", "check_opf_prompt_pack.py", ["--self-test"]),
    ("opf-prompt-pack", "check_opf_prompt_pack.py", []),
    ("opf-oplock-selftest", "_opf_oplock.py", ["--self-test"]),
    ("opf-init-substrate-selftest", "_opf_init_substrate.py", ["--self-test"]),
    ("opf-init-builders-selftest", "_opf_init.py", ["--self-test"]),
    ("opf-init-operation-selftest", "_opf_init_operation.py", ["--self-test"]),
    ("opf-init-p0-selftest", "check_opf_init_p0.py", ["--self-test", "--red-on-revert"]),
    ("opf-init-observe-selftest", "check_opf_init_observe.py", ["--self-test", "--red-on-revert"]),
    ("commonmark-headings-selftest", "selftest_commonmark_headings.py", ["--self-test"]),
    ("commonmark-conformance", "selftest_commonmark_conformance.py", []),
]

# The DEFAULT kill bound per subset member. Generous for every member except the tooling
# self-test, which carries its own row below.
_SUBSET_TIMEOUT_S = 600

# Per-member kill bounds overriding the default (rule: a kill timeout OUTLIVES the wait it bounds,
# sized from the member's measured end-to-end wall time, never the other way around).
#
# opf-tooling-selftest (opf.py --self-test, the subprocess unit runner of merge train 2):
#   MEASURED: on a loaded 16-core build host under `nice -n 10` the full suite ran in 741.5 s
#   (and 679.6 s with the round-17 per-unit bytecode cache); on GitHub-hosted CI runners the same
#   suite step took 912 s and 825 s on the two most recent train runs (gh run view, 2026-10-05..07),
#   so CI measured 1.11x to 1.23x the measuring host. The suite's cost is real test work, not
#   runner overhead: its aggregator unit alone measures about 281-287 s and the watchdog pair
#   about 240-300 s.
#   ESTIMATED: the ASSUMED host-to-CI ratio is 2x (measured only 1.23x worst, but hosted runners
#   are shared and their load varies run to run, so the assumed ratio doubles the worst measured
#   one). The bound below is the measured CI worst (912 s) with that margin, rounded to 1800 s.
#   The old flat 600 s sat BELOW the measured CI wall time, so it killed a HEALTHY member on
#   every train run; 1800 s sits above every observed run with margin and still ends a genuinely
#   hung suite within one CI step.
_MEMBER_TIMEOUT_S = {
    "opf-tooling-selftest": 1800,
}


def _check_pack_manifest_registration(subset):
    """Independent requirement, not inferred from the release manifest.

    Covers this exact closure-roster tuple, not arbitrary dispatcher changes
    or the registrations of other gates.
    """
    expected = ("opf-pack-manifest-selftest", "_opf_pack_manifest.py", ["--self-test"])
    matches = [row for row in subset
               if row[0] == expected[0] or row[1] == expected[1]]
    if matches != [expected]:
        raise AssertionError("closure/pack-manifest-registration")


def _pack_manifest_registration_self_test():
    _check_pack_manifest_registration(_SUBSET)
    print("PASS closure/pack-manifest-registration")
    # Mutate the real roster, independently of the shell-runner deletion.
    # No digest or generated-manifest check participates in the verdict.
    changed = list(_SUBSET)
    changed.remove(("opf-pack-manifest-selftest", "_opf_pack_manifest.py", ["--self-test"]))
    try:
        _check_pack_manifest_registration(changed)
    except AssertionError as exc:
        if str(exc) != "closure/pack-manifest-registration":
            raise
    else:
        raise AssertionError("closure/pack-manifest-registration-not-red")
    print("RED closure-registration -> closure/pack-manifest-registration")


def _check_adopt_observe_registration(subset):
    """Independent requirement, not inferred from the release manifest.

    Covers this exact closure-roster tuple, not arbitrary dispatcher changes
    or the registrations of other gates.
    """
    expected = ("opf-adopt-observe-selftest", "_opf_adopt_observe.py", ["--self-test"])
    matches = [row for row in subset
               if row[0] == expected[0] or row[1] == expected[1]]
    if matches != [expected]:
        raise AssertionError("closure/adopt-observe-registration")


def _adopt_observe_registration_self_test():
    _check_adopt_observe_registration(_SUBSET)
    print("PASS closure/adopt-observe-registration")
    # Mutate the real roster, independently of the shell-runner deletion.
    # No digest or generated-manifest check participates in the verdict.
    changed = list(_SUBSET)
    changed.remove(("opf-adopt-observe-selftest", "_opf_adopt_observe.py", ["--self-test"]))
    try:
        _check_adopt_observe_registration(changed)
    except AssertionError as exc:
        if str(exc) != "closure/adopt-observe-registration":
            raise
    else:
        raise AssertionError("closure/adopt-observe-registration-not-red")
    print("RED closure-registration -> closure/adopt-observe-registration")


def _check_member_timeout_rows(subset, table):
    """The per-member bound table names only real members: a renamed or removed subset member must
    not leave a stale override behind (the stale row would silently stop bounding anything while
    its member fell back to the default). Covers exactly the (roster, table) pair it is given."""
    names = set(row[0] for row in subset)
    stale = sorted(set(table) - names)
    if stale:
        raise AssertionError("closure/member-timeout-rows: " + ", ".join(stale))
    for name, bound in table.items():
        if type(bound) is not int or bound <= 0:
            raise AssertionError("closure/member-timeout-rows: " + name)


def _member_timeout_rows_self_test():
    _check_member_timeout_rows(_SUBSET, _MEMBER_TIMEOUT_S)
    print("PASS closure/member-timeout-rows")
    # Mutate a COPY of the real table: a row naming no member must go red.
    changed = dict(_MEMBER_TIMEOUT_S)
    changed["no-such-member"] = 60
    try:
        _check_member_timeout_rows(_SUBSET, changed)
    except AssertionError as exc:
        if not str(exc).startswith("closure/member-timeout-rows"):
            raise
    else:
        raise AssertionError("closure/member-timeout-rows-not-red")
    print("RED closure-registration -> closure/member-timeout-rows")


def _isolated_env():
    """A minimal environment for the isolated subset. PYTHONPATH and PYTHONHOME are dropped so nothing off
    the copied tree can be imported (belt-and-suspenders atop `python3 -I`, which already ignores them), and
    every ambient GIT_-prefixed variable is dropped so an inherited GIT_DIR/GIT_WORK_TREE cannot rebind the
    copy to a different repository. Bytecode writing is suppressed to keep the copy pristine."""
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("GIT_") and k not in ("PYTHONPATH", "PYTHONHOME")}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def _materialize(opf_src, dest):
    """Copy the opf/ subtree ALONE into dest/opf (no tools/, no .aiqt), skipping bytecode artefacts. Raises
    on any copy failure so a truncated materialization fails closed rather than passing on a partial tree."""
    shutil.copytree(opf_src, dest / "opf",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    return dest / "opf"


def _run_one(opf_root, script, args, run_dir, env, timeout_s=_SUBSET_TIMEOUT_S):
    """Run one subset member isolated against the copied opf/tools/<script>, killed at `timeout_s`
    seconds. Returns (rc, tail_of_output, timed_out): a member killed at its bound is rc 2 with
    timed_out True, CANNOT-EVALUATE (the member never finished, so closure was neither observed nor
    refuted), distinct from a member that ran and failed. cwd is run_dir, which is OUTSIDE any git
    repository and does not contain the tools/ tree, so only the copied opf/tools/ is reachable to
    `python3 -I`."""
    target = opf_root / "tools" / script
    if not target.is_file():
        return 2, "missing subset script in the copy: {}".format(target), False
    try:
        proc = subprocess.run(
            [sys.executable, "-I", "-B", str(target), *args],
            cwd=str(run_dir), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        return 2, "timed out after {}s (cannot evaluate: the member never finished)".format(timeout_s), True
    except OSError as exc:
        return 2, "could not launch the interpreter: {}".format(exc), False
    tail = (proc.stdout or b"").decode("utf-8", "replace").splitlines()[-3:]
    return proc.returncode, "\n".join(tail), False


def _run_subset(opf_root, run_dir):
    """Run every subset member, each under its kill bound (_MEMBER_TIMEOUT_S row or the default).
    Returns a list of (name, rc, tail, timed_out)."""
    _check_pack_manifest_registration(_SUBSET)
    _check_adopt_observe_registration(_SUBSET)
    _check_member_timeout_rows(_SUBSET, _MEMBER_TIMEOUT_S)
    env = _isolated_env()
    return [(name, *(_run_one(opf_root, script, args, run_dir, env,
                              timeout_s=_MEMBER_TIMEOUT_S.get(name, _SUBSET_TIMEOUT_S))))
            for name, script, args in _SUBSET]


def run(root):
    """Materialize opf/ alone and run the subset in isolation. Returns 0 (closure holds), 1 (a subset gate
    failed: closure broken), or 2 (harness error / opf/ unreadable), fail-closed."""
    opf_src = Path(root) / "opf"
    if not (opf_src.is_dir() and (opf_src / "tools" / "opf.py").is_file()):
        print("error: opf/ subtree not found under {} (cannot evaluate closure)".format(root),
              file=sys.stderr)
        return 2
    tmp = Path(tempfile.mkdtemp(prefix="opf-closure-"))
    try:
        try:
            opf_root = _materialize(opf_src, tmp)
        except OSError as exc:
            print("error: could not materialize the isolated opf/ copy: {}".format(exc), file=sys.stderr)
            return 2
        try:
            results = _run_subset(opf_root, tmp)
        except AssertionError as exc:
            print("STANDALONE CLOSURE: FAILED:", str(exc), file=sys.stderr)
            return 1
        timed_out = [(name, rc, tail) for name, rc, tail, expired in results if expired]
        failed = [(name, rc, tail) for name, rc, tail, expired in results if rc != 0 and not expired]
        for name, rc, tail, expired in results:
            verdict = "OK" if rc == 0 else (
                "TIMEOUT (cannot evaluate)" if expired else "FAILED rc={}".format(rc))
            print("  {:32s} {}".format(name, verdict))
        if failed:
            print("STANDALONE CLOSURE: BROKEN (the isolated opf/ subtree does not verify itself):",
                  file=sys.stderr)
            for name, rc, tail in failed:
                print("  {} (rc={}): {}".format(name, rc, tail.replace("\n", " | ")), file=sys.stderr)
        if timed_out:
            # A member killed at its bound is CANNOT-EVALUATE (exit 2), never a closure finding:
            # the member did not finish, so this run observed neither closure nor its absence.
            print("STANDALONE CLOSURE: CANNOT EVALUATE (a subset member was killed at its time "
                  "bound before it finished):", file=sys.stderr)
            for name, rc, tail in timed_out:
                print("  {}: {}".format(name, tail.replace("\n", " | ")), file=sys.stderr)
            return 2
        if failed:
            return 1
        print("STANDALONE CLOSURE: OK (opf/ verifies itself with no AIQT tree reachable)")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def self_test_main():
    """Prove the gate fails without the self-containment property (change-carries-check). The positive leg
    runs run() over the real opf/ and requires 0. The negative leg materializes opf/ alone, then RE-INTRODUCES
    the pre-move upward edge (opf/tools/_opf_store.py importing `_parse` from AIQT's `check_versions` instead
    of the extracted `_semver`); with no AIQT tree reachable that import fails, so the subset must return
    non-zero. A gate that passed the deliberately-broken copy would provide no coverage."""
    try:
        _pack_manifest_registration_self_test()
        _adopt_observe_registration_self_test()
        _member_timeout_rows_self_test()
    except AssertionError as exc:
        print("SELF-TEST FAIL:", str(exc), file=sys.stderr)
        return 1
    failures = []
    root = repo_root()

    # Positive: the real opf/ subtree verifies itself in isolation.
    if run(root) != 0:
        failures.append("positive: the real opf/ subtree did not verify in isolation (expected closure)")

    # Negative (deliberate flip): a surviving upward edge must be caught.
    opf_src = Path(root) / "opf"
    tmp = Path(tempfile.mkdtemp(prefix="opf-closure-neg-"))
    try:
        opf_root = _materialize(opf_src, tmp)
        store = opf_root / "tools" / "_opf_store.py"
        text = store.read_text(encoding="utf-8")
        flipped = text.replace("from _semver import _parse",
                               "from check_versions import _parse", 1)
        if flipped == text:
            failures.append("negative: could not locate the _semver import to flip (fixture drift)")
        else:
            store.write_text(flipped, encoding="utf-8")
            env = _isolated_env()
            rc, _tail, _expired = _run_one(
                opf_root, "opf.py", ["--self-test"], tmp, env,
                timeout_s=_MEMBER_TIMEOUT_S.get("opf-tooling-selftest", _SUBSET_TIMEOUT_S))
            if rc == 0:
                failures.append("negative: a re-introduced upward edge (import check_versions) was NOT "
                                "caught in isolation (the gate would give no coverage)")
    except OSError as exc:
        failures.append("negative: harness error building the broken copy: {}".format(exc))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if failures:
        for f in failures:
            print("SELF-TEST FAIL: {}".format(f), file=sys.stderr)
        return 1
    print("SELF-TEST PASS: standalone closure holds for opf/, and a re-introduced upward edge is caught.")
    return 0


def main():
    if "--self-test" in sys.argv[1:]:
        return self_test_main()
    return run(repo_root())


if __name__ == "__main__":
    sys.exit(main())
