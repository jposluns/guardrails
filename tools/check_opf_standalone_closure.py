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
  2  malformed input or a harness error (fail-closed): opf/ absent/unreadable, copy failed, a
     subset script missing from the copy, the interpreter could not be launched (reported as
     HARNESS ERROR), or a subset member KILLED at its time bound (reported as TIMEOUT). Each is
     cannot-evaluate: the member never ran to completion, so closure was neither observed nor
     refuted, and it is never reported as a closure finding.

--self-test exits 0 (both legs held), 1 (a leg was refuted: the real opf/ did not verify, or the
flipped copy was not caught for the intended reason) or 2 (a leg could not be evaluated: run()
returned 2, or the flipped copy timed out or could not be launched).
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    sys.stderr.write(
        "error: check_opf_standalone_closure.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
        "Nothing was run (cannot evaluate).\n"
        % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
    raise SystemExit(2)

import contextlib
import io
import os
import shutil
import subprocess
import tempfile
import types
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

# Per-member kill bounds overriding the default. These are CI DEADLINES, deliberately SHORTER than
# a completion bound. The kill-timeout rule sizes a completion bound ABOVE the callee's own budget,
# and opf.py grants itself a much larger one: its _UNIT_OUTER_BOUNDS give each of its 22 units a
# hard outer bound of its own, summing to 30120 s (the largest, opf-watchdog-regressions, is
# 10800 s). That self-granted budget is a per-unit ceiling against a hung unit, not an expected run
# time, and the gate does not wait for it: a CI step that sits for over eight hours on a hung suite
# delays every verdict behind it. A deadline shorter than the callee's budget is legitimate here
# ONLY because a member killed at it is reported TIMEOUT (cannot evaluate, exit 2), never as a
# closure finding: a deadline that proves too short can cost a verdict, never produce a wrong one.
#
# opf-tooling-selftest (opf.py --self-test, the subprocess unit runner of merge train 2):
#   MEASURED: on a loaded 16-core build host under `nice -n 10` the full suite ran in 741.5 s; on
#   GitHub-hosted CI runners the same suite step took 912 s and 825 s on the two most recent train
#   runs (gh run view, 2026-10-05..07).
#   ESTIMATED: the bound is a 2x margin over the measured CI worst of 912 s, rounded down to
#   1800 s. The 2x factor is an assumption, not a measured ratio: hosted runners are shared and
#   their load varies run to run. The old flat 600 s sat BELOW the measured CI wall time, so it
#   killed a HEALTHY member on every train run.
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
    # A bound that is not a positive int (bool is refused even though it subclasses int) must go red.
    for bad in (0, -60, 1800.0, True, "1800"):
        changed = dict(_MEMBER_TIMEOUT_S)
        changed["opf-tooling-selftest"] = bad
        try:
            _check_member_timeout_rows(_SUBSET, changed)
        except AssertionError as exc:
            if str(exc) != "closure/member-timeout-rows: opf-tooling-selftest":
                raise
        else:
            raise AssertionError("closure/member-timeout-rows-bad-bound-not-red: {!r}".format(bad))
        print("RED closure-registration -> closure/member-timeout-rows bound {!r}".format(bad))


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


# The cannot-evaluate labels _run_one returns in place of None (the member never ran to completion).
_TIMEOUT = "TIMEOUT"
_HARNESS = "HARNESS ERROR"


def _run_one(opf_root, script, args, run_dir, env, timeout_s=_SUBSET_TIMEOUT_S, full_output=False):
    """Run one subset member isolated against the copied opf/tools/<script>, killed at `timeout_s`
    seconds. Returns (rc, text, cannot): `text` is the last three output lines (the whole output
    when full_output is True), and `cannot` is None when the member ran to completion, _TIMEOUT when
    it was killed at its bound, or _HARNESS when its script is missing from the copy or the
    interpreter could not be launched. A non-None `cannot` is rc 2 and CANNOT-EVALUATE (closure was
    neither observed nor refuted), distinct from a member that ran and failed. cwd is run_dir, which
    is OUTSIDE any git repository and does not contain the tools/ tree, so only the copied
    opf/tools/ is reachable to `python3 -I`."""
    target = opf_root / "tools" / script
    if not target.is_file():
        return 2, "missing subset script in the copy: {}".format(target), _HARNESS
    try:
        proc = subprocess.run(
            [sys.executable, "-I", "-B", str(target), *args],
            cwd=str(run_dir), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        return 2, "timed out after {}s (cannot evaluate: the member never finished)".format(timeout_s), _TIMEOUT
    except OSError as exc:
        return 2, "could not launch the interpreter: {}".format(exc), _HARNESS
    lines = (proc.stdout or b"").decode("utf-8", "replace").splitlines()
    return proc.returncode, "\n".join(lines if full_output else lines[-3:]), None


def _run_subset(opf_root, run_dir):
    """Run every subset member, each under its kill bound (_MEMBER_TIMEOUT_S row or the default).
    Returns a list of (name, rc, tail, cannot) with `cannot` as _run_one returns it."""
    _check_pack_manifest_registration(_SUBSET)
    _check_adopt_observe_registration(_SUBSET)
    _check_member_timeout_rows(_SUBSET, _MEMBER_TIMEOUT_S)
    env = _isolated_env()
    return [(name, *(_run_one(opf_root, script, args, run_dir, env,
                              timeout_s=_MEMBER_TIMEOUT_S.get(name, _SUBSET_TIMEOUT_S))))
            for name, script, args in _SUBSET]


def run(root):
    """Materialize opf/ alone and run the subset in isolation. Returns 0 (closure holds), 1 (a subset gate
    failed: closure broken), or 2 (harness error, a member timeout, opf/ unreadable), fail-closed."""
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
        cannot = [(name, why, tail) for name, rc, tail, why in results if why is not None]
        failed = [(name, rc, tail) for name, rc, tail, why in results if rc != 0 and why is None]
        for name, rc, tail, why in results:
            verdict = "{} (cannot evaluate)".format(why) if why is not None else (
                "OK" if rc == 0 else "FAILED rc={}".format(rc))
            print("  {:32s} {}".format(name, verdict))
        if failed:
            print("STANDALONE CLOSURE: BROKEN (the isolated opf/ subtree does not verify itself):",
                  file=sys.stderr)
            for name, rc, tail in failed:
                print("  {} (rc={}): {}".format(name, rc, tail.replace("\n", " | ")), file=sys.stderr)
        if cannot:
            # A member killed at its bound, missing from the copy, or not launchable is
            # CANNOT-EVALUATE (exit 2), never a closure finding: the member did not run to
            # completion, so this run observed neither closure nor its absence.
            print("STANDALONE CLOSURE: CANNOT EVALUATE (a subset member did not run to completion):",
                  file=sys.stderr)
            for name, why, tail in cannot:
                print("  {} [{}]: {}".format(name, why, tail.replace("\n", " | ")), file=sys.stderr)
            return 2
        if failed:
            return 1
        print("STANDALONE CLOSURE: OK (opf/ verifies itself with no AIQT tree reachable)")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# The intended reason the flipped copy must fail for: opf.py's bootstrap refusing the re-introduced
# `check_versions` import (the line opf.py prints when that import raises ImportError).
_FLIP_EVIDENCE = "opf: cannot bootstrap: check_versions (cannot evaluate)"


def _positive_leg_verdict(rc):
    """Classify run() over the real opf/: ("ok", None), ("cannot", msg) for exit 2, else ("fail", msg)."""
    if rc == 0:
        return "ok", None
    if rc == 2:
        return "cannot", "positive: run() could not evaluate the real opf/ subtree (exit 2)"
    return "fail", "positive: the real opf/ subtree did not verify in isolation (expected closure)"


def _negative_leg_verdict(rc, output, cannot):
    """Classify the flipped copy's run. ("caught", None) ONLY on positive evidence that it failed for
    the intended reason (a non-zero exit carrying the _FLIP_EVIDENCE line); ("cannot", msg) when it
    never ran to completion (a timeout or a harness error says nothing about the edge); ("fail", msg)
    when it passed, or failed for some other reason (no evidence the edge itself was refused)."""
    if cannot is not None:
        return "cannot", "negative: the flipped copy did not run to completion ({}): {}".format(
            cannot, output.replace("\n", " | "))
    if rc == 0:
        return "fail", ("negative: a re-introduced upward edge (import check_versions) was NOT "
                        "caught in isolation (the gate would give no coverage)")
    if _FLIP_EVIDENCE not in output.splitlines():
        return "fail", ("negative: the flipped copy exited rc={} without the import refusal ({!r}); a "
                        "failure for another reason is no evidence the edge is caught".format(
                            rc, _FLIP_EVIDENCE))
    return "caught", None


def _closure_legs(root):
    """Run the positive and negative legs. Returns (failures, cannot): lists of messages."""
    failures, cannot = [], []
    kind, msg = _positive_leg_verdict(run(root))
    if kind == "fail":
        failures.append(msg)
    elif kind == "cannot":
        cannot.append(msg)

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
            rc, output, why = _run_one(
                opf_root, "opf.py", ["--self-test"], tmp, env,
                timeout_s=_MEMBER_TIMEOUT_S.get("opf-tooling-selftest", _SUBSET_TIMEOUT_S),
                full_output=True)
            kind, msg = _negative_leg_verdict(rc, output, why)
            if kind == "fail":
                failures.append(msg)
            elif kind == "cannot":
                cannot.append(msg)
    except OSError as exc:
        cannot.append("negative: harness error building the broken copy: {}".format(exc))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return failures, cannot


def _self_test_exit(failures, cannot):
    """The self-test verdict: 1 when a leg was refuted, else 2 when a leg could not be evaluated, else 0."""
    for f in failures:
        print("SELF-TEST FAIL: {}".format(f), file=sys.stderr)
    for c in cannot:
        print("SELF-TEST CANNOT EVALUATE: {}".format(c), file=sys.stderr)
    if failures:
        return 1
    if cannot:
        return 2
    print("SELF-TEST PASS: standalone closure holds for opf/, and a re-introduced upward edge is caught.")
    return 0


@contextlib.contextmanager
def _patched(**values):
    """Rebind module globals for the duration of a stubbed case, restoring them on every path."""
    g = globals()
    saved = {name: g[name] for name in values}
    g.update(values)
    try:
        yield
    finally:
        g.update(saved)


def _captured(fn, *args):
    """Call fn(*args) with stdout and stderr captured; returns (result, stdout_text, stderr_text)."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        result = fn(*args)
    return result, out.getvalue(), err.getvalue()


def _stub_member_runner(calls, outcomes):
    """A stand-in for _run_one: records (member name, timeout_s) and returns outcomes.get(name) or a pass."""
    by_row = {(script, tuple(args)): name for name, script, args in _SUBSET}

    def stub(opf_root, script, args, run_dir, env, timeout_s=_SUBSET_TIMEOUT_S, full_output=False):
        name = by_row[(script, tuple(args))]
        calls.append((name, timeout_s))
        return outcomes.get(name, (0, "ok", None))
    return stub


def _check_timeout_mapping(root):
    """run() with _run_one stubbed so opf-tooling-selftest is killed at its bound: run() must exit 2
    with a TIMEOUT (cannot evaluate) line and no closure finding, and the per-member bound lookup must
    pass 1800 s to opf-tooling-selftest and 600 s to another member."""
    calls = []
    outcomes = {"opf-tooling-selftest": (2, "timed out after 1800s", _TIMEOUT)}
    with _patched(_run_one=_stub_member_runner(calls, outcomes)):
        rc, out, err = _captured(run, root)
    bounds = dict(calls)
    if bounds.get("opf-tooling-selftest") != 1800 or bounds.get("opf-homes-selftest") != 600:
        raise AssertionError("closure/member-bound-lookup: {!r}".format(bounds))
    if rc != 2:
        raise AssertionError("closure/timeout-exit: run() returned {} for a timed-out member".format(rc))
    lines = out.splitlines()
    if "  {:32s} {}".format("opf-tooling-selftest", "TIMEOUT (cannot evaluate)") not in lines:
        raise AssertionError("closure/timeout-line: no TIMEOUT (cannot evaluate) line")
    if "CANNOT EVALUATE" not in err or "BROKEN" in err:
        raise AssertionError("closure/timeout-attribution: a timeout was not reported as cannot-evaluate")


def _check_harness_mapping(root):
    """A missing subset script or an interpreter that cannot be launched is cannot-evaluate (exit 2),
    both from _run_one itself and through run()."""
    tmp = Path(tempfile.mkdtemp(prefix="opf-closure-harness-"))
    try:
        (tmp / "tools").mkdir()
        rc, _text, why = _run_one(tmp, "no_such_member.py", [], tmp, _isolated_env())
        if (rc, why) != (2, _HARNESS):
            raise AssertionError("closure/harness-missing-script: {!r}".format((rc, why)))
        (tmp / "tools" / "sleeper.py").write_text("import time\ntime.sleep(60)\n", encoding="utf-8")
        rc, _text, why = _run_one(tmp, "sleeper.py", [], tmp, _isolated_env(), timeout_s=1)
        if (rc, why) != (2, _TIMEOUT):
            raise AssertionError("closure/run-one-timeout: {!r}".format((rc, why)))

        def refuse(*_args, **_kwargs):
            raise OSError("no interpreter (stubbed)")
        no_launch = types.SimpleNamespace(run=refuse, PIPE=subprocess.PIPE, STDOUT=subprocess.STDOUT,
                                          TimeoutExpired=subprocess.TimeoutExpired)
        with _patched(subprocess=no_launch):
            rc, text, why = _run_one(tmp, "sleeper.py", [], tmp, _isolated_env())
        if (rc, why) != (2, _HARNESS) or "could not launch the interpreter" not in text:
            raise AssertionError("closure/harness-launch: {!r}".format((rc, why, text)))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    calls = []
    outcomes = {"opf-drift-selftest": (2, "could not launch the interpreter: stubbed", _HARNESS)}
    with _patched(_run_one=_stub_member_runner(calls, outcomes)):
        rc, out, err = _captured(run, root)
    if rc != 2 or "BROKEN" in err:
        raise AssertionError("closure/harness-exit: run() returned {} for a harness error".format(rc))
    if "  {:32s} {}".format("opf-drift-selftest", "HARNESS ERROR (cannot evaluate)") not in out.splitlines():
        raise AssertionError("closure/harness-line: no HARNESS ERROR (cannot evaluate) line")


def _check_leg_verdicts(root):
    """The self-test's own verdicts: a negative-leg timeout or harness error is cannot-evaluate and
    never caught; caught requires the import-refusal evidence; run()==2 on the positive leg is
    cannot-evaluate; and the self-test exits 2 (not 0, not 1) when only cannot-evaluate remains."""
    cases = [
        ((2, "timed out after 1800s", _TIMEOUT), "cannot"),
        ((2, _FLIP_EVIDENCE, _TIMEOUT), "cannot"),
        ((2, "could not launch the interpreter: x", _HARNESS), "cannot"),
        ((0, "SELF-TEST PASS", None), "fail"),
        ((1, "Traceback (most recent call last):\nSomeOtherError: unrelated", None), "fail"),
        ((2, "opf: cannot bootstrap: _opf_schema (cannot evaluate)", None), "fail"),
        ((2, _FLIP_EVIDENCE, None), "caught"),
    ]
    for args, want in cases:
        got = _negative_leg_verdict(*args)[0]
        if got != want:
            raise AssertionError("closure/negative-verdict: {!r} gave {}, expected {}".format(args, got, want))
    for rc, want in ((0, "ok"), (1, "fail"), (2, "cannot")):
        got = _positive_leg_verdict(rc)[0]
        if got != want:
            raise AssertionError("closure/positive-verdict: rc {} gave {}, expected {}".format(rc, got, want))
    # The wiring: both legs stubbed (run() and the flipped copy's runner) through the real _closure_legs.
    wiring = [
        (2, (2, "timed out after 1800s", _TIMEOUT), 2),
        (0, (2, "timed out after 1800s", _TIMEOUT), 2),
        (2, (2, _FLIP_EVIDENCE, None), 2),
        (0, (2, _FLIP_EVIDENCE, None), 0),
        (1, (2, _FLIP_EVIDENCE, None), 1),
        (0, (0, "SELF-TEST PASS", None), 1),
    ]
    for run_rc, neg, want in wiring:
        calls = []
        stub_run = (lambda rc: (lambda _root: rc))(run_rc)
        with _patched(run=stub_run, _run_one=_stub_member_runner(calls, {"opf-tooling-selftest": neg})):
            failures, cannot = _closure_legs(root)
        got = _captured(_self_test_exit, failures, cannot)[0]
        if got != want:
            raise AssertionError("closure/self-test-exit: run()={} flipped={!r} gave {}, expected {}".format(
                run_rc, neg, got, want))


def _stubbed_self_test(root):
    """In-process legs over stubbed runners (no subset member runs): the timeout and harness-error
    mappings, the per-member bound lookup, and the self-test's own leg verdicts. Then a RED case:
    with the bound table emptied, the lookup check must refuse the 600 s it now passes."""
    _check_timeout_mapping(root)
    print("PASS closure/timeout-cannot-evaluate")
    _check_harness_mapping(root)
    print("PASS closure/harness-cannot-evaluate")
    _check_leg_verdicts(root)
    print("PASS closure/self-test-leg-verdicts")
    with _patched(_MEMBER_TIMEOUT_S={}):
        try:
            _check_timeout_mapping(root)
        except AssertionError as exc:
            if not str(exc).startswith("closure/member-bound-lookup"):
                raise
        else:
            raise AssertionError("closure/member-bound-lookup-not-red")
    print("RED closure-bound-lookup -> closure/member-bound-lookup")


def self_test_main():
    """Prove the gate fails without the self-containment property (change-carries-check). The positive leg
    runs run() over the real opf/ and requires 0. The negative leg materializes opf/ alone, then RE-INTRODUCES
    the pre-move upward edge (opf/tools/_opf_store.py importing `_parse` from AIQT's `check_versions` instead
    of the extracted `_semver`); with no AIQT tree reachable that import fails, and the flipped copy counts as
    caught only when it exits non-zero WITH opf.py's import-refusal line. A gate that passed the
    deliberately-broken copy would provide no coverage. A leg that could not be evaluated (run() exit 2, or
    the flipped copy timed out or could not launch) makes the self-test exit 2, never PASS."""
    root = repo_root()
    try:
        _pack_manifest_registration_self_test()
        _adopt_observe_registration_self_test()
        _member_timeout_rows_self_test()
        _stubbed_self_test(root)
    except AssertionError as exc:
        print("SELF-TEST FAIL:", str(exc), file=sys.stderr)
        return 1
    return _self_test_exit(*_closure_legs(root))


def main():
    if "--self-test" in sys.argv[1:]:
        return self_test_main()
    return run(repo_root())


if __name__ == "__main__":
    sys.exit(main())
