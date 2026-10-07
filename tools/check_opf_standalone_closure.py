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
  1  a real finding (a subset gate failed in isolation: closure is broken). Failure-first: a member
     that ran and failed exits 1 even when other members could not be evaluated; both are listed.
  2  malformed input or a harness error (fail-closed), with no member failing: opf/ absent/unreadable,
     the scratch directory could not be allocated, copy failed, a subset script missing from the copy,
     the interpreter could not be launched (reported as HARNESS ERROR), or a subset member KILLED at
     its time bound (reported as TIMEOUT). Each is cannot-evaluate: the member never ran to
     completion, so closure was neither observed nor refuted, and it is never reported as a closure
     finding.

--self-test runs in two stages. First the preflight: the registration cases and the stubbed cases (no
subset member runs), each run on its own so that a case that could not be set up never hides another
case's refutation. When any preflight case was refuted the self-test exits 1; otherwise, when one could
not be set up (for example with no opf/ subtree, or a scratch directory or copy that could not be
made), it exits 2. In both of those outcomes the closure legs never run. Then the two closure legs:
exit 0 (both legs held), 1 (a leg was refuted: the real opf/ did not verify, or the flipped copy was
not caught for the intended reason; failure-first, whatever else could not be evaluated) or 2 (no leg
was refuted but one could not be evaluated: run() returned 2, the flipped copy timed out or could not
be launched, or its scratch directory or copy could not be made).
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


def _run_one(opf_root, script, args, run_dir, env, timeout_s=_SUBSET_TIMEOUT_S, separate_streams=False):
    """Run one subset member isolated against the copied opf/tools/<script>, killed at `timeout_s`
    seconds. Returns (rc, text, cannot): `text` is the last three lines of the merged output, except
    that with separate_streams a member that ran to completion gives the pair (stdout, stderr) of its
    whole decoded streams, captured apart so that no line can be assembled across them. `cannot` is
    None when the member ran to completion, _TIMEOUT when it was killed at its bound, or _HARNESS when
    its script is missing from the copy or the interpreter could not be launched (`text` is then the
    harness message). A non-None `cannot` is rc 2 and CANNOT-EVALUATE (closure was neither observed
    nor refuted), distinct from a member that ran and failed. cwd is run_dir, which is OUTSIDE any git
    repository and does not contain the tools/ tree, so only the copied opf/tools/ is reachable to
    `python3 -I`."""
    target = opf_root / "tools" / script
    if not target.is_file():
        return 2, "missing subset script in the copy: {}".format(target), _HARNESS
    try:
        proc = subprocess.run(
            [sys.executable, "-I", "-B", str(target), *args],
            cwd=str(run_dir), env=env, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE if separate_streams else subprocess.STDOUT,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        return 2, "timed out after {}s (cannot evaluate: the member never finished)".format(timeout_s), _TIMEOUT
    except OSError as exc:
        return 2, "could not launch the interpreter: {}".format(exc), _HARNESS
    if separate_streams:
        return proc.returncode, tuple((data or b"").decode("utf-8", "replace")
                                      for data in (proc.stdout, proc.stderr)), None
    lines = (proc.stdout or b"").decode("utf-8", "replace").splitlines()
    return proc.returncode, "\n".join(lines[-3:]), None


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
    failed: closure broken, even when another member could not be evaluated), or 2 (no member failed,
    but a harness error, a member timeout, an unallocatable scratch directory or an unreadable opf/
    left closure unevaluated), fail-closed."""
    opf_src = Path(root) / "opf"
    if not (opf_src.is_dir() and (opf_src / "tools" / "opf.py").is_file()):
        print("error: opf/ subtree not found under {} (cannot evaluate closure)".format(root),
              file=sys.stderr)
        return 2
    tmp = None
    try:
        try:
            tmp = Path(tempfile.mkdtemp(prefix="opf-closure-"))
        except OSError as exc:
            print("error: could not allocate a scratch directory for the isolated opf/ copy: {} "
                  "(cannot evaluate closure)".format(exc), file=sys.stderr)
            return 2
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
        # Failure-first: a member that ran and failed is a definite closure break whatever else could
        # not be evaluated, so it decides the exit; cannot-evaluate (2) only when nothing failed.
        if failed:
            return 1
        if cannot:
            return 2
        print("STANDALONE CLOSURE: OK (opf/ verifies itself with no AIQT tree reachable)")
        return 0
    finally:
        if tmp is not None:
            shutil.rmtree(tmp, ignore_errors=True)


# The intended reason the flipped copy must fail for: opf.py's bootstrap refusing the re-introduced
# `check_versions` import (the line opf.py prints when that import raises ImportError).
_FLIP_EVIDENCE = "opf: cannot bootstrap: check_versions (cannot evaluate)"

# The negative leg's cannot-evaluate messages for a broken copy it could not build.
_NEG_SCRATCH_ERROR = "negative: could not allocate a scratch directory for the broken copy: "
_NEG_COPY_ERROR = "negative: harness error building the broken copy: "
_NEG_DECODE_ERROR = "negative: the copied opf/tools/_opf_store.py is not UTF-8 text, so the flip cannot be made: "


def _positive_leg_verdict(rc):
    """Classify run() over the real opf/: ("ok", None), ("cannot", msg) for exit 2, else ("fail", msg)."""
    if rc == 0:
        return "ok", None
    if rc == 2:
        return "cannot", "positive: run() could not evaluate the real opf/ subtree (exit 2)"
    return "fail", "positive: the real opf/ subtree did not verify in isolation (expected closure)"


def _output_lines(stream):
    """The complete lines of ONE decoded output stream: split on "\\n" only, each line losing at most
    one trailing "\\r" (CRLF output). str.splitlines is not used: it also breaks at CR, VT, FF, 0x1c
    to 0x1e, U+0085, U+2028 and U+2029, so text after any of them would read as a line of its own."""
    return [line[:-1] if line.endswith("\r") else line for line in stream.split("\n")]


def _negative_leg_verdict(rc, output, cannot):
    """Classify the flipped copy's run. ("caught", None) ONLY on positive evidence that it failed for
    the intended reason: a non-zero exit with _FLIP_EVIDENCE as a complete line of ONE output stream
    (see _output_lines); ("cannot", msg) when it never ran to completion (a timeout or a harness error
    says nothing about the edge); ("fail", msg) when it passed, or failed for some other reason (no
    evidence the edge itself was refused). `output` is the (stdout, stderr) pair _run_one gives with
    separate_streams, or one string standing for a single stream, so evidence split across the two
    streams is no evidence. The evidence is textual and does not authenticate its producer: any code
    in the flipped copy that printed that exact line would read as caught. It shows that the line was
    printed, not that opf.py's bootstrap refusal printed it."""
    streams = (output,) if isinstance(output, str) else tuple(output)
    if cannot is not None:
        return "cannot", "negative: the flipped copy did not run to completion ({}): {}".format(
            cannot, " | ".join(streams).replace("\n", " | "))
    if rc == 0:
        return "fail", ("negative: a re-introduced upward edge (import check_versions) was NOT "
                        "caught in isolation (the gate would give no coverage)")
    if not any(_FLIP_EVIDENCE in _output_lines(stream) for stream in streams):
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
    tmp = None
    try:
        try:
            tmp = Path(tempfile.mkdtemp(prefix="opf-closure-neg-"))
        except OSError as exc:
            cannot.append(_NEG_SCRATCH_ERROR + str(exc))
            return failures, cannot
        opf_root = _materialize(opf_src, tmp)
        store = opf_root / "tools" / "_opf_store.py"
        try:
            text = store.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            # A malformed fixture is not a refuted leg: the flip was never made, so nothing was observed.
            cannot.append(_NEG_DECODE_ERROR + str(exc))
            return failures, cannot
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
                separate_streams=True)
            kind, msg = _negative_leg_verdict(rc, output, why)
            if kind == "fail":
                failures.append(msg)
            elif kind == "cannot":
                cannot.append(msg)
    except OSError as exc:
        cannot.append(_NEG_COPY_ERROR + str(exc))
    finally:
        if tmp is not None:
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

    def stub(opf_root, script, args, run_dir, env, timeout_s=_SUBSET_TIMEOUT_S, separate_streams=False):
        name = by_row[(script, tuple(args))]
        calls.append((name, timeout_s))
        return outcomes.get(name, (0, "ok", None))
    return stub


class _CannotEvaluate(Exception):
    """A stubbed self-test case could not be set up (no opf/ subtree, a copy or scratch directory that
    could not be made): a harness error, never a refuted leg, so the self-test exits 2."""


def _stubbed_run(root, outcomes):
    """run(root) with _run_one stubbed by outcomes; returns (rc, stdout, stderr, calls). Raises
    _CannotEvaluate, naming run()'s own error, when run() exited before any subset member ran (opf/
    absent, its copy or scratch directory not made): the case then observed nothing."""
    calls = []
    with _patched(_run_one=_stub_member_runner(calls, outcomes)):
        rc, out, err = _captured(run, root)
    if not calls:
        raise _CannotEvaluate("closure/stubbed-run: run() exited {} before any subset member ran: {}".format(
            rc, err.strip().replace("\n", " | ")))
    return rc, out, err, calls


def _check_timeout_mapping(root):
    """run() with _run_one stubbed so opf-tooling-selftest is killed at its bound: run() must exit 2
    with a TIMEOUT (cannot evaluate) line and no closure finding, and the per-member bound lookup must
    pass 1800 s to opf-tooling-selftest and 600 s to another member."""
    outcomes = {"opf-tooling-selftest": (2, "timed out after 1800s", _TIMEOUT)}
    rc, out, err, calls = _stubbed_run(root, outcomes)
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
    both from _run_one itself and through run(). Real children also pin the negative leg's stream
    handling: evidence split across stdout and stderr, or after a CR on one line, is not caught, while
    the evidence alone on a stderr line (where opf.py prints it) is."""
    children = (
        ("split.py", "sys.stdout.write(E[:20])\nsys.stdout.flush()\nsys.stderr.write(E[20:] + '\\n')\n",
         "fail"),
        ("carriage.py", "sys.stdout.write('AssertionError: expected\\r' + E + '\\n')\n", "fail"),
        ("evidence.py", "print('unit opf-store: FAIL')\nsys.stderr.write(E + '\\n')\n", "caught"),
    )
    tmp = None
    try:
        try:
            tmp = Path(tempfile.mkdtemp(prefix="opf-closure-harness-"))
            (tmp / "tools").mkdir()
            (tmp / "tools" / "sleeper.py").write_text("import time\ntime.sleep(60)\n", encoding="utf-8")
            for name, body, _want in children:
                (tmp / "tools" / name).write_text(
                    "import sys\nE = {!r}\n{}sys.exit(1)\n".format(_FLIP_EVIDENCE, body), encoding="utf-8")
        except OSError as exc:
            raise _CannotEvaluate("closure/harness-scratch: could not build the harness fixture: {}".format(exc))
        rc, _text, why = _run_one(tmp, "no_such_member.py", [], tmp, _isolated_env())
        if (rc, why) != (2, _HARNESS):
            raise AssertionError("closure/harness-missing-script: {!r}".format((rc, why)))
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
        for name, _body, want in children:
            rc, text, why = _run_one(tmp, name, [], tmp, _isolated_env(), timeout_s=120,
                                     separate_streams=True)
            if why is not None:
                raise _CannotEvaluate("closure/evidence-streams: {} did not run to completion ({}): {}".format(
                    name, why, text))
            got = _negative_leg_verdict(rc, text, why)[0]
            if got != want:
                raise AssertionError("closure/evidence-streams: {} gave {}, expected {}".format(name, got, want))
    finally:
        if tmp is not None:
            shutil.rmtree(tmp, ignore_errors=True)
    outcomes = {"opf-drift-selftest": (2, "could not launch the interpreter: stubbed", _HARNESS)}
    rc, out, err, _calls = _stubbed_run(root, outcomes)
    if rc != 2 or "BROKEN" in err:
        raise AssertionError("closure/harness-exit: run() returned {} for a harness error".format(rc))
    if "  {:32s} {}".format("opf-drift-selftest", "HARNESS ERROR (cannot evaluate)") not in out.splitlines():
        raise AssertionError("closure/harness-line: no HARNESS ERROR (cannot evaluate) line")


def _check_failure_first(root):
    """run() is failure-first: a member that ran and failed exits 1 even while another member timed
    out, and both are listed (the break under BROKEN, the timeout under CANNOT EVALUATE)."""
    outcomes = {"opf-drift-selftest": (1, "ImportError: check_versions", None),
                "opf-tooling-selftest": (2, "timed out after 1800s", _TIMEOUT)}
    rc, _out, err, _calls = _stubbed_run(root, outcomes)
    if rc != 1:
        raise AssertionError("closure/failure-first: run() returned {} for a failed member beside a "
                             "timed-out one".format(rc))
    lines = err.splitlines()
    if ("  opf-drift-selftest (rc=1): ImportError: check_versions" not in lines
            or "  opf-tooling-selftest [TIMEOUT]: timed out after 1800s" not in lines
            or not any(line.startswith("STANDALONE CLOSURE: BROKEN") for line in lines)
            or not any(line.startswith("STANDALONE CLOSURE: CANNOT EVALUATE") for line in lines)):
        raise AssertionError("closure/failure-first-listing: the break and the timeout are not both listed")


def _check_verdict_tables():
    """The self-test's own leg verdicts, pure (no scratch directory and no opf/ subtree, so no fixture
    that cannot be set up can hide them): a negative-leg timeout or harness error is cannot-evaluate
    and never caught; caught requires the import-refusal evidence as a complete line of one output
    stream; run()==2 on the positive leg is cannot-evaluate."""
    cases = [
        ((2, "timed out after 1800s", _TIMEOUT), "cannot"),
        ((2, _FLIP_EVIDENCE, _TIMEOUT), "cannot"),
        ((2, "could not launch the interpreter: x", _HARNESS), "cannot"),
        ((0, "SELF-TEST PASS", None), "fail"),
        ((1, "Traceback (most recent call last):\nSomeOtherError: unrelated", None), "fail"),
        ((2, "opf: cannot bootstrap: _opf_schema (cannot evaluate)", None), "fail"),
        ((2, _FLIP_EVIDENCE, None), "caught"),
        ((1, _FLIP_EVIDENCE, None), "caught"),
        ((2, "unit opf-store: FAIL\n" + _FLIP_EVIDENCE + "\nopf: self-test FAILED", None), "caught"),
        # Caught needs a non-zero exit: rc 0 with the evidence line is not caught.
        ((0, _FLIP_EVIDENCE, None), "fail"),
        # The evidence must be a whole line: prefixed, suffixed or quoted evidence is no evidence.
        ((2, "x " + _FLIP_EVIDENCE, None), "fail"),
        ((2, _FLIP_EVIDENCE + " x", None), "fail"),
        ((2, repr(_FLIP_EVIDENCE), None), "fail"),
        ((2, "AssertionError: expected {!r}".format(_FLIP_EVIDENCE), None), "fail"),
        # Whitespace around the evidence is no evidence (a strip()-based match would accept it).
        ((2, _FLIP_EVIDENCE + " ", None), "fail"),
        ((2, "  " + _FLIP_EVIDENCE, None), "fail"),
        ((2, _FLIP_EVIDENCE + "\t", None), "fail"),
        # Lines split on "\n" only, each losing at most one trailing "\r": CRLF output is evidence,
        # but no other str.splitlines boundary makes a line of its own.
        ((2, _FLIP_EVIDENCE + "\r\n", None), "caught"),
        ((2, "unit opf-store: FAIL\r\n" + _FLIP_EVIDENCE + "\r\nopf: self-test FAILED\r\n", None), "caught"),
        ((2, "AssertionError: expected\r" + _FLIP_EVIDENCE, None), "fail"),
        ((2, _FLIP_EVIDENCE + "\r\r", None), "fail"),
    ]
    for sep in ("\r", "\x0b", "\x0c", "\x1c", "\x1d", "\x1e", "\x85", "\u2028", "\u2029"):
        cases.append(((2, "x" + sep + _FLIP_EVIDENCE, None), "fail"))
        cases.append(((2, _FLIP_EVIDENCE + sep + "x", None), "fail"))
    # The evidence must be whole within ONE stream of the (stdout, stderr) pair.
    cases += [
        ((1, ("", _FLIP_EVIDENCE + "\n"), None), "caught"),
        ((1, (_FLIP_EVIDENCE + "\n", "Traceback (most recent call last):\n"), None), "caught"),
        ((1, (_FLIP_EVIDENCE[:20], _FLIP_EVIDENCE[20:] + "\n"), None), "fail"),
        ((1, (_FLIP_EVIDENCE[:20] + "\n", _FLIP_EVIDENCE[20:]), None), "fail"),
        ((2, ("timed out after 1800s", ""), _TIMEOUT), "cannot"),
    ]
    for args, want in cases:
        got = _negative_leg_verdict(*args)[0]
        if got != want:
            raise AssertionError("closure/negative-verdict: {!r} gave {}, expected {}".format(args, got, want))
    for rc, want in ((0, "ok"), (1, "fail"), (2, "cannot")):
        got = _positive_leg_verdict(rc)[0]
        if got != want:
            raise AssertionError("closure/positive-verdict: rc {} gave {}, expected {}".format(rc, got, want))


def _check_leg_wiring(root):
    """Both legs stubbed (run() and the flipped copy's runner) through the real _closure_legs: the
    flipped opf.py --self-test runs under its bound row, and the self-test exits 2 (not 0, not 1) when
    only cannot-evaluate remains, 1 when a leg was refuted whatever else could not be evaluated."""
    wiring = [
        (2, (2, "timed out after 1800s", _TIMEOUT), 2),
        (0, (2, "timed out after 1800s", _TIMEOUT), 2),
        (2, (2, _FLIP_EVIDENCE, None), 2),
        (0, (2, _FLIP_EVIDENCE, None), 0),
        (1, (2, _FLIP_EVIDENCE, None), 1),
        (0, (0, "SELF-TEST PASS", None), 1),
        # Failure-first: a refuted leg exits 1 whichever leg could not be evaluated.
        (1, (2, "timed out after 1800s", _TIMEOUT), 1),
        (1, (2, "could not launch the interpreter: x", _HARNESS), 1),
        (2, (0, "SELF-TEST PASS", None), 1),
        (2, (1, "Traceback (most recent call last):\nSomeOtherError: unrelated", None), 1),
        # The (stdout, stderr) pair: evidence alone on stderr is caught, split across the two is not.
        (0, (1, ("", _FLIP_EVIDENCE + "\n"), None), 0),
        (0, (1, (_FLIP_EVIDENCE[:20], _FLIP_EVIDENCE[20:] + "\n"), None), 1),
    ]
    for run_rc, neg, want in wiring:
        calls = []
        stub_run = (lambda rc: (lambda _root: rc))(run_rc)
        with _patched(run=stub_run, _run_one=_stub_member_runner(calls, {"opf-tooling-selftest": neg})):
            failures, cannot = _closure_legs(root)
        if not calls and any(c.startswith((_NEG_SCRATCH_ERROR, _NEG_COPY_ERROR, _NEG_DECODE_ERROR))
                             for c in cannot):
            raise _CannotEvaluate("closure/self-test-exit: the flipped copy could not be built: {}".format(
                " | ".join(cannot)))
        # The negative leg's own bound lookup: the flipped opf.py --self-test runs under its row.
        if calls != [("opf-tooling-selftest", 1800)]:
            raise AssertionError("closure/negative-bound-lookup: {!r}".format(calls))
        got = _captured(_self_test_exit, failures, cannot)[0]
        if got != want:
            raise AssertionError("closure/self-test-exit: run()={} flipped={!r} gave {}, expected {}".format(
                run_rc, neg, got, want))


def _require_subtree(root):
    """Raise _CannotEvaluate unless root carries the opf/ subtree run() needs: a case built on the real
    subtree observes nothing without it, so its absence never reads as a refuted case."""
    if not (Path(root) / "opf" / "tools" / "opf.py").is_file():
        raise _CannotEvaluate("closure/no-subtree: opf/ subtree not found under {}".format(root))


def _check_scratch_and_copy_errors(root):
    """A scratch directory that cannot be allocated, or a broken copy that cannot be built, is
    cannot-evaluate, never a traceback and never a refuted leg: in run() (exit 2, no member runs), in
    the negative leg (one cannot-evaluate message, self-test exit 2) and in the harness fixture. When
    the host itself cannot allocate a scratch directory, the broken-copy case is cannot-evaluate. The
    harness-fixture sub-check needs no opf/ subtree, so it runs before the subtree is required."""
    def no_mkdtemp(*_args, **_kwargs):
        raise OSError("no scratch directory (stubbed)")
    no_scratch = types.SimpleNamespace(mkdtemp=no_mkdtemp)
    with _patched(tempfile=no_scratch):
        try:
            _check_harness_mapping(root)
        except _CannotEvaluate as exc:
            if not str(exc).startswith("closure/harness-scratch"):
                raise AssertionError("closure/scratch-harness: {}".format(exc))
        except OSError as exc:
            raise AssertionError("closure/scratch-harness: raised {!r} instead of cannot-evaluate".format(exc))
        else:
            raise AssertionError("closure/scratch-harness: no cannot-evaluate without a scratch directory")

    _require_subtree(root)
    calls = []
    with _patched(tempfile=no_scratch, _run_one=_stub_member_runner(calls, {})):
        try:
            rc, _out, err = _captured(run, root)
        except OSError as exc:
            raise AssertionError("closure/scratch-run: run() raised {!r} instead of exiting 2".format(exc))
    if rc != 2 or calls or "could not allocate a scratch directory" not in err:
        raise AssertionError("closure/scratch-run: run() gave {} with {!r}".format(rc, calls))

    def broken_copy(_src, _dest):
        raise OSError("copy refused (stubbed)")
    for patches, prefix in ((dict(tempfile=no_scratch), _NEG_SCRATCH_ERROR),
                            (dict(_materialize=broken_copy), _NEG_COPY_ERROR)):
        calls = []
        with _patched(run=lambda _root: 0, _run_one=_stub_member_runner(calls, {}), **patches):
            try:
                failures, cannot = _closure_legs(root)
            except OSError as exc:
                raise AssertionError("closure/negative-harness: _closure_legs raised {!r}".format(exc))
        if (prefix == _NEG_COPY_ERROR and not failures and not calls and len(cannot) == 1
                and cannot[0].startswith(_NEG_SCRATCH_ERROR)):
            raise _CannotEvaluate("closure/negative-harness: no scratch directory for the broken-copy "
                                  "case: {}".format(cannot[0]))
        if failures or calls or len(cannot) != 1 or not cannot[0].startswith(prefix):
            raise AssertionError("closure/negative-harness: {!r}".format((failures, cannot, calls)))
        if _captured(_self_test_exit, failures, cannot)[0] != 2:
            raise AssertionError("closure/negative-harness-exit: {!r}".format(cannot))


def _expect_cannot(check, root, cause, label):
    """check(root) must raise _CannotEvaluate naming `cause`: not pass, not refute a leg."""
    try:
        check(root)
    except _CannotEvaluate as exc:
        if cause not in str(exc):
            raise AssertionError("{}: {} did not name the cause {!r}: {}".format(
                label, check.__name__, cause, exc))
    except AssertionError as exc:
        raise AssertionError("{}: {} refuted a leg instead of cannot-evaluate: {}".format(
            label, check.__name__, exc))
    else:
        raise AssertionError("{}: {} passed with nothing to evaluate".format(label, check.__name__))


def _check_absent_subtree(root):
    """With no opf/ subtree under the root, or a copy that fails before any member runs, each stubbed
    case is cannot-evaluate naming the real cause (never a refuted leg)."""
    tmp = None
    try:
        try:
            tmp = Path(tempfile.mkdtemp(prefix="opf-closure-absent-"))
        except OSError as exc:
            raise _CannotEvaluate("closure/absent-subtree-scratch: {}".format(exc))
        absent = tmp / "no-opf-root"
        for check in (_check_timeout_mapping, _check_harness_mapping, _check_failure_first,
                      _check_leg_wiring, _check_scratch_and_copy_errors):
            _expect_cannot(check, absent, str(absent), "closure/absent-subtree")
    finally:
        if tmp is not None:
            shutil.rmtree(tmp, ignore_errors=True)

    _require_subtree(root)

    def broken_copy(_src, _dest):
        raise OSError("copy refused (stubbed)")
    with _patched(_materialize=broken_copy):
        _expect_cannot(_check_timeout_mapping, root, "could not materialize", "closure/copy-before-members")


def _check_undecodable_fixture(_root):
    """A copied _opf_store.py that is not UTF-8 text is cannot-evaluate at the read boundary: the
    negative leg gives one cannot-evaluate message (never a traceback, never a refuted leg, self-test
    exit 2), and the leg-wiring case is cannot-evaluate naming that cause. Builds its own fixture tree,
    so it needs no opf/ subtree under the root."""
    tmp = None
    try:
        try:
            tmp = Path(tempfile.mkdtemp(prefix="opf-closure-undecodable-"))
            fixture = tmp / "root"
            (fixture / "opf" / "tools").mkdir(parents=True)
            (fixture / "opf" / "tools" / "opf.py").write_text("", encoding="utf-8")
            (fixture / "opf" / "tools" / "_opf_store.py").write_bytes(b"\xff")
        except OSError as exc:
            raise _CannotEvaluate("closure/undecodable-scratch: could not build the fixture: {}".format(exc))
        calls = []
        with _patched(run=lambda _root: 0, _run_one=_stub_member_runner(calls, {})):
            try:
                failures, cannot = _closure_legs(fixture)
            except ValueError as exc:
                raise AssertionError("closure/undecodable-fixture: _closure_legs raised {!r}".format(exc))
        if (failures or calls or len(cannot) != 1 or not cannot[0].startswith(_NEG_DECODE_ERROR)
                or _captured(_self_test_exit, failures, cannot)[0] != 2):
            raise AssertionError("closure/undecodable-fixture: {!r}".format((failures, cannot, calls)))
        try:
            _expect_cannot(_check_leg_wiring, fixture, _NEG_DECODE_ERROR, "closure/undecodable-wiring")
        except ValueError as exc:
            raise AssertionError("closure/undecodable-wiring: _check_leg_wiring raised {!r}".format(exc))
    finally:
        if tmp is not None:
            shutil.rmtree(tmp, ignore_errors=True)


def _check_preflight_collection():
    """_preflight_exit runs every case whatever an earlier case did: a _CannotEvaluate, or any other
    ordinary exception (here a RuntimeError and a decode failure), is that case's cannot-evaluate, the
    latter naming the case (a _rootless case by its function's name) and the exception; only an
    AssertionError is a refutation; the exit is failure-first over everything collected. Pure: no
    scratch directory and no opf/ subtree."""
    ran = []

    def _cannot_case(_root):
        raise _CannotEvaluate("closure/stubbed-run: stubbed")

    def _boom_case():
        raise RuntimeError("boom (stubbed)")

    def _undecodable_case(_root):
        b"\xff".decode("utf-8")

    def _sentinel_case(_root):
        ran.append("sentinel")

    def _refuting_case(_root):
        ran.append("refute")
        raise AssertionError("closure/stubbed-refutation")
    sentinel = ("closure/stubbed-sentinel", _sentinel_case)
    runs = (
        (((None, _cannot_case), sentinel), 2, ["closure/stubbed-run: stubbed"], ["sentinel"]),
        (((None, _rootless(_boom_case)), sentinel), 2,
         ["closure/preflight-error: _boom_case raised RuntimeError: boom (stubbed)"], ["sentinel"]),
        (((None, _undecodable_case), (None, _refuting_case), sentinel), 1,
         ["closure/preflight-error: _undecodable_case raised UnicodeDecodeError: "], ["refute", "sentinel"]),
    )
    for cases, want_rc, want_cannot, want_ran in runs:
        del ran[:]
        with _patched(_PREFLIGHT_CASES=cases):
            try:
                rc, _out, err = _captured(_preflight_exit, None)
            except Exception as exc:
                raise AssertionError("closure/preflight-collection: _preflight_exit raised {!r}".format(exc))
        lines = err.splitlines()
        unevaluated = [line[len("SELF-TEST CANNOT EVALUATE: "):] for line in lines
                       if line.startswith("SELF-TEST CANNOT EVALUATE: ")]
        refuted = [line for line in lines if line.startswith("SELF-TEST FAIL: ")]
        if (rc != want_rc or ran != want_ran or len(unevaluated) != len(want_cannot)
                or not all(got.startswith(want) for got, want in zip(unevaluated, want_cannot))
                or refuted != (["SELF-TEST FAIL: closure/stubbed-refutation"] if want_rc == 1 else [])):
            raise AssertionError("closure/preflight-collection: gave {} after {!r}: {!r}".format(rc, ran, lines))


def _check_preflight_failure_first(root):
    """_preflight_exit is failure-first, the same rule as run() and _self_test_exit. With no scratch
    directory (so every fixture case is cannot-evaluate) and the negative verdict mutated to a
    substring match, the other preflight cases, run in REVERSE so the pure verdict case comes after
    the fixture cases, must exit 1 with both the refutation and the cannot-evaluate cases listed;
    without the mutant the same scratch failure exits 2. Over a root with no opf/ subtree (scratch
    available), the same cases exit 2 with nothing refuted: a missing subtree is never a refutation."""
    def no_mkdtemp(*_args, **_kwargs):
        raise OSError("no scratch directory (stubbed)")
    no_scratch = types.SimpleNamespace(mkdtemp=no_mkdtemp)

    def substring_verdict(rc, output, cannot):
        if cannot is not None:
            return "cannot", "stubbed"
        text = output if isinstance(output, str) else "".join(output)
        return ("caught", None) if rc != 0 and _FLIP_EVIDENCE in text else ("fail", "stubbed")
    cases = tuple(reversed([case for case in _PREFLIGHT_CASES
                            if case[1] is not _check_preflight_failure_first]))
    for patches, want in ((dict(_negative_leg_verdict=substring_verdict), 1), (dict(), 2)):
        with _patched(tempfile=no_scratch, _PREFLIGHT_CASES=cases, **patches):
            rc, _out, err = _captured(_preflight_exit, root)
        lines = err.splitlines()
        refuted = [line for line in lines if line.startswith("SELF-TEST FAIL: ")]
        unevaluated = [line for line in lines if line.startswith("SELF-TEST CANNOT EVALUATE: ")]
        wrong_refutation = any(not line.startswith("SELF-TEST FAIL: closure/negative-verdict")
                               for line in refuted)
        if rc != want or not unevaluated or bool(refuted) != (want == 1) or wrong_refutation:
            raise AssertionError("closure/preflight-failure-first: {} gave {}, expected {}: {!r}".format(
                "substring mutant" if patches else "scratch failure alone", rc, want, lines))

    tmp = None
    try:
        try:
            tmp = Path(tempfile.mkdtemp(prefix="opf-closure-preflight-absent-"))
        except OSError as exc:
            raise _CannotEvaluate("closure/preflight-absent-scratch: {}".format(exc))
        with _patched(_PREFLIGHT_CASES=cases):
            rc, _out, err = _captured(_preflight_exit, tmp / "no-opf-root")
    finally:
        if tmp is not None:
            shutil.rmtree(tmp, ignore_errors=True)
    lines = err.splitlines()
    if (rc != 2 or any(line.startswith("SELF-TEST FAIL: ") for line in lines)
            or not any(line.startswith("SELF-TEST CANNOT EVALUATE: ") for line in lines)):
        raise AssertionError("closure/preflight-absent-subtree: a root with no opf/ gave {}: {!r}".format(
            rc, lines))


def _red_bound_lookup(root):
    """RED cases: with the bound table emptied, each lookup check must refuse the 600 s it now passes."""
    for check, label in ((_check_timeout_mapping, "closure/member-bound-lookup"),
                         (_check_leg_wiring, "closure/negative-bound-lookup")):
        with _patched(_MEMBER_TIMEOUT_S=dict()):
            try:
                check(root)
            except AssertionError as exc:
                if not str(exc).startswith(label):
                    raise
            else:
                raise AssertionError(label + "-not-red")
        print("RED closure-bound-lookup -> " + label)


def _rootless(fn):
    """fn() as a preflight check(root) that ignores root, keeping fn's name for the case's diagnostics."""
    def check(_root):
        return fn()
    check.__name__ = fn.__name__
    return check


# The self-test's preflight: the registration cases and the in-process stubbed cases (no subset member
# runs), as (PASS label, or None for a case that prints its own lines, check(root)). The first five need
# no scratch directory and no opf/ subtree; the rest build fixtures or stub run() over the real subtree.
_PREFLIGHT_CASES = (
    (None, _rootless(_pack_manifest_registration_self_test)),
    (None, _rootless(_adopt_observe_registration_self_test)),
    (None, _rootless(_member_timeout_rows_self_test)),
    ("closure/self-test-leg-verdicts", _rootless(_check_verdict_tables)),
    ("closure/preflight-collection", _rootless(_check_preflight_collection)),
    ("closure/timeout-cannot-evaluate", _check_timeout_mapping),
    ("closure/harness-cannot-evaluate", _check_harness_mapping),
    ("closure/failure-first", _check_failure_first),
    ("closure/self-test-leg-wiring", _check_leg_wiring),
    ("closure/scratch-and-copy-cannot-evaluate", _check_scratch_and_copy_errors),
    ("closure/absent-subtree-cannot-evaluate", _check_absent_subtree),
    ("closure/undecodable-fixture-cannot-evaluate", _check_undecodable_fixture),
    ("closure/preflight-failure-first", _check_preflight_failure_first),
    (None, _red_bound_lookup),
)


def _preflight_exit(root):
    """Run every _PREFLIGHT_CASES case on its own, collecting each outcome, so no case's outcome stops a
    later case. An AssertionError is that case's refutation (SELF-TEST FAIL). A _CannotEvaluate, or any
    other Exception (a decode failure, an OSError, a RuntimeError), is that case's cannot-evaluate
    (SELF-TEST CANNOT EVALUATE; for any other Exception the line names the case and the exception).
    Only a BaseException that is not an Exception (KeyboardInterrupt, SystemExit) stops the collection.
    Failure-first, the same rule as run() and _self_test_exit: 1 when any case was refuted, else 2 when
    any could not be evaluated, else None (every case held). Within ONE case the first sub-check that
    cannot be evaluated ends that case: its later sub-checks do not run, so a refutation they would give
    stays unobserved (the case reads as cannot-evaluate) until that case can be set up. A sub-check that
    needs no fixture is therefore its own case (the pure ones) or runs before its case's fixture setup."""
    failures, cannot = [], []
    for label, check in _PREFLIGHT_CASES:
        try:
            check(root)
        except _CannotEvaluate as exc:
            cannot.append(str(exc))
            continue
        except AssertionError as exc:
            failures.append(str(exc))
            continue
        except Exception as exc:
            cannot.append("closure/preflight-error: {} raised {}: {}".format(
                label if label is not None else check.__name__, type(exc).__name__, exc))
            continue
        if label is not None:
            print("PASS " + label)
    if not failures and not cannot:
        return None
    return _self_test_exit(failures, cannot)


def self_test_main():
    """Prove the gate fails without the self-containment property (change-carries-check). The positive leg
    runs run() over the real opf/ and requires 0. The negative leg materializes opf/ alone, then RE-INTRODUCES
    the pre-move upward edge (opf/tools/_opf_store.py importing `_parse` from AIQT's `check_versions` instead
    of the extracted `_semver`); with no AIQT tree reachable that import fails, and the flipped copy counts as
    caught only when it exits non-zero WITH opf.py's import-refusal line. A gate that passed the
    deliberately-broken copy would provide no coverage. The preflight (_preflight_exit) runs first: when
    any of its cases was refuted the self-test exits 1, otherwise when one could not be set up (or raised
    any other ordinary exception) it exits 2, and in both outcomes the legs never run. Otherwise the legs decide, failure-first: 1 when either leg
    was refuted, whatever else could not be evaluated; 2 when neither was refuted but one could not be
    evaluated (run() exit 2, or the flipped copy timed out, could not launch, or its scratch directory
    or copy could not be made); 0 only when both held, never PASS on a leg that was not evaluated."""
    root = repo_root()
    rc = _preflight_exit(root)
    if rc is not None:
        return rc
    return _self_test_exit(*_closure_legs(root))


def main():
    if "--self-test" in sys.argv[1:]:
        return self_test_main()
    return run(repo_root())


if __name__ == "__main__":
    sys.exit(main())
