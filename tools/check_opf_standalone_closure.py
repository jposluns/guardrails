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
subset member runs), each run on its own so that a case that could not be evaluated never hides another
case's refutation. Attribution (see _preflight_exit): no opf/ subtree, a host primitive that failed
(a scratch directory, copy, fixture write, store read, flip write or child launch that raised an
OSError, or a child that did not finish within its bound; _harness_watch lists what is not watched),
malformed input, or any other ordinary exception raised by a case's own code outside its attributed
calls is cannot-evaluate; a crash or a wrong result of the code under test is a refutation, and
_check_attribution_inventory pins every call a case makes to it. When any preflight case was refuted
the self-test exits 1; when none was refuted but one could not be evaluated, it exits 2. In both of
those outcomes the closure legs never run.
Then the two closure legs:
exit 0 (both legs held), 1 (a leg was refuted: the real opf/ did not verify, or the flipped copy was
not caught for the intended reason; failure-first, whatever else could not be evaluated) or 2 (no leg
was refuted but one could not be evaluated: run() returned 2, the flipped copy timed out or could not
be launched, its scratch directory, copy or flip could not be made, or its _opf_store.py is not UTF-8
text).
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
    _attributed("closure/pack-manifest-registration", lambda: _check_pack_manifest_registration(_SUBSET))
    print("PASS closure/pack-manifest-registration")
    # Mutate the real roster, independently of the shell-runner deletion.
    # No digest or generated-manifest check participates in the verdict.
    changed = list(_SUBSET)
    changed.remove(("opf-pack-manifest-selftest", "_opf_pack_manifest.py", ["--self-test"]))
    try:
        _attributed("closure/pack-manifest-registration", lambda: _check_pack_manifest_registration(changed))
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
    _attributed("closure/adopt-observe-registration", lambda: _check_adopt_observe_registration(_SUBSET))
    print("PASS closure/adopt-observe-registration")
    # Mutate the real roster, independently of the shell-runner deletion.
    # No digest or generated-manifest check participates in the verdict.
    changed = list(_SUBSET)
    changed.remove(("opf-adopt-observe-selftest", "_opf_adopt_observe.py", ["--self-test"]))
    try:
        _attributed("closure/adopt-observe-registration", lambda: _check_adopt_observe_registration(changed))
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
    _attributed("closure/member-timeout-rows", lambda: _check_member_timeout_rows(_SUBSET, _MEMBER_TIMEOUT_S))
    print("PASS closure/member-timeout-rows")
    # Mutate a COPY of the real table: a row naming no member must go red.
    changed = dict(_MEMBER_TIMEOUT_S)
    changed["no-such-member"] = 60
    try:
        _attributed("closure/member-timeout-rows", lambda: _check_member_timeout_rows(_SUBSET, changed))
    except AssertionError as exc:
        if str(exc) != "closure/member-timeout-rows: no-such-member":
            raise
    else:
        raise AssertionError("closure/member-timeout-rows-not-red")
    print("RED closure-registration -> closure/member-timeout-rows")
    # A bound that is not a positive int (bool is refused even though it subclasses int) must go red.
    for bad in (0, -60, 1800.0, True, "1800"):
        changed = dict(_MEMBER_TIMEOUT_S)
        changed["opf-tooling-selftest"] = bad
        try:
            _attributed("closure/member-timeout-rows", lambda: _check_member_timeout_rows(_SUBSET, changed))
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


def _read_copied_store(store):
    """The negative leg's read of the copied opf/tools/_opf_store.py: one function, so that the
    self-test's harness watch can observe it."""
    return store.read_text(encoding="utf-8")


def _write_copied_store(store, text):
    """The negative leg's write of the flipped _opf_store.py: one function, so that the self-test's
    harness watch can observe it."""
    store.write_text(text, encoding="utf-8")


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
            text = _read_copied_store(store)
        except UnicodeDecodeError as exc:
            # Malformed input (the exit-2 convention): bytes that are not UTF-8 text cannot be flipped at
            # all, so nothing was observed. A UTF-8 store without the _semver import is well-formed input
            # the flip no longer matches: that is fixture drift, a failure below, because the negative leg
            # has lost the edge it exists to re-introduce and must be updated.
            cannot.append(_NEG_DECODE_ERROR + str(exc))
            return failures, cannot
        flipped = text.replace("from _semver import _parse",
                               "from check_versions import _parse", 1)
        if flipped == text:
            failures.append("negative: could not locate the _semver import to flip (fixture drift)")
        else:
            _write_copied_store(store, flipped)
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
    """A self-test case or sub-check observed nothing it could judge; never a refuted leg. Raised when
    the root has no opf/ subtree; when a scratch directory, copy, fixture write, store read, flip
    write or child launch failed, or a child did not finish within its bound (observed by
    _harness_watch, or by a case's own except OSError around building its fixture); when the copied
    _opf_store.py is not UTF-8 text (malformed input); and when an attribution pin's case could not
    reach the call it pins (see _pin). The self-test exits 2 on it only when no case and no leg was
    refuted (failure-first)."""


class _HarnessFailure(_CannotEvaluate):
    """The _CannotEvaluate that _observed raises when its OWN _harness_watch saw a host primitive
    fail, as opposed to one the code under test raised or one a nested watch raised."""


def _write_fixture(base, files):
    """Write each (relative path, bytes) of `files` under base, making its parent directories. Every
    self-test fixture is written through this one function, so _harness_watch observes its
    directory creation and its writes."""
    for rel, data in files:
        path = Path(base) / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


def _harness_watch(broke):
    """Stand-ins for the host primitives tempfile.mkdtemp, _materialize, _write_fixture,
    _read_copied_store, _write_copied_store, _not_utf8 and subprocess.run. Each delegates to the one
    bound now and appends to `broke` when it raised an OSError (the host refused the allocation, copy,
    write, read or launch) or, for subprocess.run only, TimeoutExpired (a child did not finish within its
    bound). Such a failure is the host's: the fixture or the child's result was not made. Any other
    exception raised inside a watched call (a TypeError from an invalid call, a crash inside
    _materialize) is not recorded: it is a defect of the caller, so where the caller is the code
    under test it is a refutation. The watch observes the primitive itself, never a message the code
    under test produced. Residuals: an OSError or a timeout that the caller's own wrong argument
    caused (a nonexistent dir= for mkdtemp, a too-short timeout for a child) still reads as the
    host's, so the outcome is cannot-evaluate (exit 2), fail-closed, never a pass. Host I/O outside
    these primitives is not watched: the removal of scratch directories (errors ignored), and the
    is_dir and is_file probes in run(), _run_one and _require_subtree, where an unreadable path reads
    as absent. _require_subtree then makes the case cannot-evaluate, but a probe in run() or _run_one
    that reads a path as absent after the case found or wrote it (a path that became unreadable in
    between) is a refutation, and so is the UTF-8 sub-check of _check_undecodable_attribution reading
    the fixture it has just written as absent. A child the host kills or starves after it launched
    (for example out of memory) still completes with an exit code, which is judged."""
    def watched(what, inner, refused=(OSError,)):
        def call(*args, **kwargs):
            try:
                return inner(*args, **kwargs)
            except refused as exc:
                broke.append("{} failed: {}".format(what, exc))
                raise
        return call
    return dict(
        tempfile=types.SimpleNamespace(mkdtemp=watched("scratch allocation", tempfile.mkdtemp)),
        _materialize=watched("copy", _materialize),
        _write_fixture=watched("fixture write", _write_fixture),
        _read_copied_store=watched("copied store read", _read_copied_store),
        _write_copied_store=watched("flip write", _write_copied_store),
        _not_utf8=watched("store read", _not_utf8),
        subprocess=types.SimpleNamespace(
            run=watched("child process", subprocess.run, (OSError, subprocess.TimeoutExpired)),
            PIPE=subprocess.PIPE, STDOUT=subprocess.STDOUT, TimeoutExpired=subprocess.TimeoutExpired))


def _observed(label, call, injected=None):
    """call() under a _harness_watch of the host primitives bound now, with `injected` (the case's own
    stubs and stubbed failures, which are its fixture, not a host failure) patched over the watch.
    `injected` is a dict, or a function returning one that is called once the watch is bound (so a
    stub that delegates wraps the watch). Raises _HarnessFailure when the watch saw a host failure,
    whatever call() then did. Otherwise returns (result, None) when call() returned, or (None, exc):
    exc is call()'s AssertionError or _CannotEvaluate as raised, or, for any other Exception (a crash
    of the code under test, or a wrong result that the interpretation inside call() could not index
    or unpack), an AssertionError naming it. A BaseException that is not an Exception
    (KeyboardInterrupt, SystemExit) passes through."""
    broke = []
    with _patched(**_harness_watch(broke)):
        with _patched(**(injected() if callable(injected) else (injected or {}))):
            try:
                outcome = call(), None
            except (AssertionError, _CannotEvaluate) as exc:
                outcome = None, exc
            except Exception as exc:
                crash = AssertionError("{}: the code under test raised {}: {}".format(
                    label, type(exc).__name__, exc))
                crash.__cause__ = exc
                outcome = None, crash
    if broke:
        raise _HarnessFailure("{}: harness failure: {}".format(label, " | ".join(broke))) from outcome[1]
    return outcome


def _attributed(label, call, injected=None):
    """_observed's outcome, raised: call()'s result is returned; its AssertionError or _CannotEvaluate,
    or an AssertionError naming any other Exception it raised, is raised; so is _HarnessFailure when
    the host failed. A case puts its interpretation of the code under test's result (indexing,
    unpacking, comparing) inside call(), so a malformed result is a refutation too."""
    result, exc = _observed(label, call, injected)
    if exc is not None:
        raise exc
    return result


def _stubbed_run(root, outcomes):
    """run(root) with _run_one stubbed by outcomes, through _attributed; returns (rc, stdout, stderr,
    calls). Cannot-evaluate when root has no opf/ subtree, or when the watch saw the scratch
    directory or the copy fail (the message then names run()'s exit code and its own error).
    Otherwise a run() that crashed, or exited before any subset member ran, is refuted; the
    refutation says whether its copy was built, as observed at _materialize."""
    _require_subtree(root)
    calls, seen, made = [], [], []

    def stubs():
        inner = _materialize

        def materialize(opf_src, dest):
            made.append(inner(opf_src, dest))
            return made[-1]
        return dict(_run_one=_stub_member_runner(calls, outcomes), _materialize=materialize)
    try:
        _attributed("closure/stubbed-run", lambda: seen.append(_captured(run, root)), injected=stubs)
    except _HarnessFailure as exc:
        if not seen:
            raise
        raise _HarnessFailure("{}: run() exited {}: {}".format(
            exc, seen[0][0], seen[0][2].strip().replace("\n", " | "))) from exc
    rc, out, err = seen[0]
    if not calls:
        raise AssertionError("closure/stubbed-run: run() exited {} before any subset member ran, {}: {}".format(
            rc, "with its copy built" if made else "without building its copy",
            err.strip().replace("\n", " | ")))
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
    """A missing subset script, a member killed at its bound, or an interpreter that cannot be
    launched is cannot-evaluate (exit 2), both from _run_one itself and through run(). The kill and
    the launch refusal are stubbed subprocess.run results patched over the watch (this case's
    fixture), so no real child is killed: _run_one's own mapping, and the bound it passes, are what
    is judged. Real children pin the negative leg's stream handling: evidence split across stdout
    and stderr, or after a CR on one line, is not caught, while the evidence alone on a stderr line
    (where opf.py prints it) is. A real child that cannot be launched or does not finish within its
    bound is a host failure the watch observes (cannot-evaluate); any other result that is not a
    completed run refutes _run_one."""
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
            _write_fixture(tmp, [("tools/sleeper.py", b"import time\ntime.sleep(60)\n")] + [
                ("tools/" + name, "import sys\nE = {!r}\n{}sys.exit(1)\n".format(
                    _FLIP_EVIDENCE, body).encode("utf-8")) for name, body, _want in children])
        except OSError as exc:
            raise _CannotEvaluate("closure/harness-scratch: could not build the harness fixture: {}".format(exc))

        def missing_script():
            rc, _text, why = _run_one(tmp, "no_such_member.py", [], tmp, _isolated_env())
            if (rc, why) != (2, _HARNESS):
                raise AssertionError("closure/harness-missing-script: {!r}".format((rc, why)))
        _attributed("closure/harness-missing-script", missing_script)

        bounds = []

        def expire(cmd, **kwargs):
            bounds.append(kwargs.get("timeout"))
            raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout"))

        def refuse(*_args, **_kwargs):
            raise OSError("no interpreter (stubbed)")

        def stubbed(fn):
            return dict(subprocess=types.SimpleNamespace(
                run=fn, PIPE=subprocess.PIPE, STDOUT=subprocess.STDOUT, TimeoutExpired=subprocess.TimeoutExpired))

        def killed():
            rc, _text, why = _run_one(tmp, "sleeper.py", [], tmp, _isolated_env(), timeout_s=1)
            if (rc, why) != (2, _TIMEOUT) or bounds != [1]:
                raise AssertionError("closure/run-one-timeout: {!r}".format((rc, why, bounds)))
        _attributed("closure/run-one-timeout", killed, injected=stubbed(expire))

        def no_launch():
            rc, text, why = _run_one(tmp, "sleeper.py", [], tmp, _isolated_env())
            if (rc, why) != (2, _HARNESS) or "could not launch the interpreter" not in text:
                raise AssertionError("closure/harness-launch: {!r}".format((rc, why, text)))
        _attributed("closure/harness-launch", no_launch, injected=stubbed(refuse))
        for name, _body, want in children:
            def judge(name=name, want=want):
                rc, text, why = _run_one(tmp, name, [], tmp, _isolated_env(), timeout_s=120,
                                         separate_streams=True)
                if why is not None:
                    raise AssertionError("closure/evidence-streams: {} did not run to completion ({}) "
                                         "with no host failure observed: {}".format(name, why, text))
                got = _negative_leg_verdict(rc, text, why)[0]
                if got != want:
                    raise AssertionError("closure/evidence-streams: {} gave {}, expected {}".format(
                        name, got, want))
            _attributed("closure/evidence-streams", judge)
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
        def judge(args=args, want=want):
            got = _negative_leg_verdict(*args)[0]
            if got != want:
                raise AssertionError("closure/negative-verdict: {!r} gave {}, expected {}".format(args, got, want))
        _attributed("closure/negative-verdict", judge)
    for rc, want in ((0, "ok"), (1, "fail"), (2, "cannot")):
        def judge(rc=rc, want=want):
            got = _positive_leg_verdict(rc)[0]
            if got != want:
                raise AssertionError("closure/positive-verdict: rc {} gave {}, expected {}".format(rc, got, want))
        _attributed("closure/positive-verdict", judge)


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
    _require_subtree(root)
    for run_rc, neg, want in wiring:
        calls = []

        def wired(run_rc=run_rc, neg=neg, want=want, calls=calls):
            failures, cannot = _closure_legs(root)
            # A store that is not UTF-8 text (judged here through the watched _not_utf8, not from the
            # leg's message) is malformed input: the flip cannot be made, so the case is
            # cannot-evaluate, naming the leg's own message.
            if not calls and _not_utf8(Path(root) / "opf" / "tools" / "_opf_store.py"):
                raise _CannotEvaluate("closure/self-test-exit: the flipped copy could not be built: {}".format(
                    " | ".join(cannot)))
            # The negative leg's own bound lookup: the flipped opf.py --self-test runs under its row.
            if calls != [("opf-tooling-selftest", 1800)]:
                raise AssertionError("closure/negative-bound-lookup: {!r}".format(calls))
            got = _captured(_self_test_exit, failures, cannot)[0]
            if got != want:
                raise AssertionError("closure/self-test-exit: run()={} flipped={!r} gave {}, expected {}".format(
                    run_rc, neg, got, want))
        stub_run = (lambda rc: (lambda _root: rc))(run_rc)
        _attributed("closure/self-test-exit", wired, injected=dict(
            run=stub_run, _run_one=_stub_member_runner(calls, {"opf-tooling-selftest": neg})))


def _not_utf8(path):
    """True when the bytes at path are not UTF-8 text, judged independently of the code under test."""
    try:
        path.read_bytes().decode("utf-8")
    except UnicodeDecodeError:
        return True
    return False


def _expect_negative_cannot(label, legs, calls, prefix):
    """The negative leg's result `legs`, interpreted inside the caller's _attributed call: exactly one
    cannot-evaluate message, starting with `prefix`, no member run, and a self-test exit of 2 over
    it. Anything else, a malformed `legs` included, refutes `label`."""
    failures, cannot = legs
    if failures or calls or len(cannot) != 1 or not cannot[0].startswith(prefix):
        raise AssertionError("{}: {!r}".format(label, (failures, cannot, calls)))
    if _captured(_self_test_exit, failures, cannot)[0] != 2:
        raise AssertionError("{}-exit: {!r}".format(label, cannot))


def _require_subtree(root):
    """Raise _CannotEvaluate unless root carries the opf/ subtree run() needs: a case built on the real
    subtree observes nothing without it, so its absence never reads as a refuted case."""
    if not (Path(root) / "opf" / "tools" / "opf.py").is_file():
        raise _CannotEvaluate("closure/no-subtree: opf/ subtree not found under {}".format(root))


def _no_scratch(*_args, **_kwargs):
    raise OSError("no scratch directory (stubbed)")


def _broken_copy(_src, _dest):
    raise OSError("copy refused (stubbed)")


def _check_scratch_and_copy_errors(root):
    """A scratch directory that cannot be allocated, or a copy of opf/ that run() cannot make, is
    cannot-evaluate, never a traceback and never a refuted leg: no scratch in the harness fixture, in
    the negative leg (one cannot-evaluate message, self-test exit 2) or in run() (exit 2, no member
    runs), and a refused copy in run() (exit 2, no member runs). Each refusal is a stub patched over
    the harness watch, so it is this case's fixture, not a host failure, and run()'s own exit code
    is what is checked. Setup order: the first two sub-checks need neither a scratch directory of
    the host's nor an opf/ subtree, so they run before the subtree is required; the run() scratch
    sub-check needs the subtree; the run() copy sub-check needs the subtree and a scratch directory
    of the host's, so it runs last. The negative leg's refused copy needs a scratch directory but no
    subtree, so it is its own case (_check_negative_copy_error)."""
    no_scratch = types.SimpleNamespace(mkdtemp=_no_scratch)
    _expect_cannot(_check_harness_mapping, root,
                   "closure/harness-scratch: could not build the harness fixture: no scratch directory (stubbed)",
                   "closure/scratch-harness", injected=dict(tempfile=no_scratch))

    calls = []
    _attributed("closure/negative-harness", lambda: _expect_negative_cannot(
        "closure/negative-harness", _closure_legs(root), calls, _NEG_SCRATCH_ERROR), injected=dict(
            run=lambda _root: 0, _run_one=_stub_member_runner(calls, {}), tempfile=no_scratch))

    _require_subtree(root)
    for label, refusal, cause in (
            ("closure/scratch-run", dict(tempfile=no_scratch), "could not allocate a scratch directory"),
            ("closure/copy-run", dict(_materialize=_broken_copy), "could not materialize the isolated opf/ copy")):
        calls = []

        def refused(label=label, cause=cause, calls=calls):
            rc, _out, err = _captured(run, root)
            if rc != 2 or calls or cause not in err:
                raise AssertionError("{}: run() gave {} with {!r}".format(label, rc, calls))
        _attributed(label, refused, injected=dict(refusal, _run_one=_stub_member_runner(calls, {})))


def _check_negative_copy_error(root):
    """A broken copy the negative leg cannot build is one cannot-evaluate message (self-test exit 2),
    never a traceback and never a refuted leg. The stubbed copy refuses before reading anything, so
    this needs no opf/ subtree; it needs a scratch directory, and when the host cannot allocate one
    (a host failure, observed by _attributed) the case is cannot-evaluate."""
    calls = []
    _attributed("closure/negative-copy", lambda: _expect_negative_cannot(
        "closure/negative-copy", _closure_legs(root), calls, _NEG_COPY_ERROR), injected=dict(
            run=lambda _root: 0, _run_one=_stub_member_runner(calls, {}), _materialize=_broken_copy))


def _expect_cannot(check, root, cause, label, injected=None):
    """check(root), through _observed with `injected` patched over the watch, must raise
    _CannotEvaluate naming `cause`: not pass, not refute a leg, not crash. A host failure that this
    call's own watch saw is cannot-evaluate (_HarnessFailure passes through); a _CannotEvaluate that
    check(root) raised, a nested watch's included, is judged by its cause."""
    _result, got = _observed(label, lambda: check(root), injected)
    if got is None:
        raise AssertionError("{}: {} passed with nothing to evaluate".format(label, check.__name__))
    if isinstance(got, _CannotEvaluate):
        if cause not in str(got):
            raise AssertionError("{}: {} did not name the cause {!r}: {}".format(
                label, check.__name__, cause, got))
        return
    raise AssertionError("{}: {} refuted a leg instead of cannot-evaluate: {}".format(
        label, check.__name__, got))


def _check_absent_subtree(root):
    """With no opf/ subtree under the root, each stubbed case is cannot-evaluate naming the real cause
    (never a refuted leg). Over the real subtree, a copy that the watch sees fail before any member
    runs makes _stubbed_run cannot-evaluate, naming run()'s own error (run()'s exit 2 for a refused
    copy is pinned by closure/copy-run). Conversely, a run() that exits before any member runs with
    no host failure is a wrong result of the code under test: a refutation, never cannot-evaluate,
    saying whether its copy was built (here a refused registration check after the copy, and a run()
    that returns 0 without copying)."""
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
    # The refused copy sits OVER this call's watch and UNDER _stubbed_run's, so for _stubbed_run it
    # is a host failure.
    _expect_cannot(_check_timeout_mapping, root, "could not materialize", "closure/copy-before-members",
                   injected=dict(_materialize=_broken_copy))

    def refused_rows(_subset, _table):
        raise AssertionError("closure/member-timeout-rows: stubbed")
    for label, stub, want in (
            ("closure/members-never-ran", dict(_check_member_timeout_rows=refused_rows),
             "closure/stubbed-run: run() exited 1 before any subset member ran, with its copy built"),
            ("closure/members-never-copied", dict(run=lambda _root: 0),
             "closure/stubbed-run: run() exited 0 before any subset member ran, without building its copy")):
        _result, got = _observed(label, lambda: _check_timeout_mapping(root), stub)
        if type(got) is not AssertionError or not str(got).startswith(want):
            raise AssertionError("{}: a run() that ran no member gave {!r}, expected a refutation "
                                 "starting {!r}".format(label, got, want))


def _check_undecodable_fixture(_root):
    """A copied _opf_store.py that is not UTF-8 text is cannot-evaluate at the read boundary: the
    negative leg gives one cannot-evaluate message (never a traceback, never a refuted leg, self-test
    exit 2), and the leg-wiring case is cannot-evaluate naming that cause. Builds its own fixture tree,
    so it needs no opf/ subtree under the root. Attribution (see _preflight_exit): an OSError building
    the fixture, or a host failure inside either sub-check that the watch sees, is cannot-evaluate;
    once the fixture is built, a crash or a wrong result of _closure_legs or _check_leg_wiring is a
    refutation (_check_undecodable_attribution pins both directions)."""
    tmp = None
    try:
        try:
            tmp = Path(tempfile.mkdtemp(prefix="opf-closure-undecodable-"))
            fixture = tmp / "root"
            _write_fixture(fixture, (("opf/tools/opf.py", b""), ("opf/tools/_opf_store.py", b"\xff")))
        except OSError as exc:
            raise _CannotEvaluate("closure/undecodable-scratch: could not build the fixture: {}".format(exc))
        calls = []
        _attributed("closure/undecodable-fixture", lambda: _expect_negative_cannot(
            "closure/undecodable-fixture", _closure_legs(fixture), calls, _NEG_DECODE_ERROR), injected=dict(
                run=lambda _root: 0, _run_one=_stub_member_runner(calls, {})))
        _expect_cannot(_check_leg_wiring, fixture, _NEG_DECODE_ERROR, "closure/undecodable-wiring")
    finally:
        if tmp is not None:
            shutil.rmtree(tmp, ignore_errors=True)


def _failing_harness(scratch_at=None, copy_at=None, write_at=None):
    """Stand-ins for tempfile, _materialize and _write_fixture that delegate to the ones bound now,
    except that the scratch_at-th allocation, the copy_at-th copy or the write_at-th fixture write
    (1-based, counted from now) raises an injected OSError: a selective harness failure, after the
    steps before it succeeded."""
    inner_tempfile, inner_materialize, inner_write = tempfile, _materialize, _write_fixture
    count = {"scratch": 0, "copy": 0, "write": 0}

    def mkdtemp(*args, **kwargs):
        count["scratch"] += 1
        if count["scratch"] == scratch_at:
            raise OSError("injected allocation failure #{}".format(scratch_at))
        return inner_tempfile.mkdtemp(*args, **kwargs)

    def materialize(opf_src, dest):
        count["copy"] += 1
        if count["copy"] == copy_at:
            raise OSError("injected copy failure #{}".format(copy_at))
        return inner_materialize(opf_src, dest)

    def write_fixture(base, files):
        count["write"] += 1
        if count["write"] == write_at:
            raise OSError("injected fixture write failure #{}".format(write_at))
        return inner_write(base, files)
    return dict(tempfile=types.SimpleNamespace(mkdtemp=mkdtemp), _materialize=materialize,
                _write_fixture=write_fixture)


def _check_undecodable_attribution(_root):
    """Pins _check_undecodable_fixture's attribution in both directions. A selective harness failure
    injected over this case's watch (the fixture's own allocation or write, then the broken copy's
    allocation or copy in the negative-leg sub-check, then the same in the leg-wiring sub-check) is
    cannot-evaluate naming the injected failure, never a refutation. With the harness intact, a crash
    of _closure_legs or of the leg-wiring check, or a wrong result (a copy-error message where nothing
    failed to copy), is a refutation naming it, never cannot-evaluate; the same wrong result through
    the leg-wiring check over a UTF-8 store is refuted too, because that check reads a harness failure
    from the watched primitives themselves, not from the leg's message. A host failure this case's
    own watch sees (a scratch directory, copy or fixture write of the host's that failed) is
    _HarnessFailure: this case is cannot-evaluate, never refuted."""
    def crash(_root):
        raise UnboundLocalError("stubbed crash")

    def wrong_result(_root):
        return [], [_NEG_COPY_ERROR + "stubbed, although nothing failed to copy"]

    def wiring_crash(_root):
        raise RuntimeError("stubbed wiring crash")
    # Each run's patches are built only once the watch is bound, so an injected failure sits over
    # the watch and a real one under it is still observed.
    runs = [
        ("scratch_at=1", lambda: _failing_harness(scratch_at=1), _CannotEvaluate, "injected"),
        ("write_at=1", lambda: _failing_harness(write_at=1), _CannotEvaluate, "injected"),
        ("scratch_at=2", lambda: _failing_harness(scratch_at=2), _CannotEvaluate, "injected"),
        ("copy_at=1", lambda: _failing_harness(copy_at=1), _CannotEvaluate, "injected"),
        ("scratch_at=3", lambda: _failing_harness(scratch_at=3), _CannotEvaluate, "injected"),
        ("copy_at=2", lambda: _failing_harness(copy_at=2), _CannotEvaluate, "injected"),
        ("crash", lambda: dict(_closure_legs=crash), AssertionError,
         "closure/undecodable-fixture: the code under test raised UnboundLocalError: stubbed crash"),
        ("wrong-result", lambda: dict(_closure_legs=wrong_result), AssertionError,
         "closure/undecodable-fixture: "),
        ("wiring-crash", lambda: dict(_check_leg_wiring=wiring_crash), AssertionError,
         "closure/undecodable-wiring: the code under test raised RuntimeError: stubbed wiring crash"),
    ]
    for name, patches, want_type, want_text in runs:
        _result, got = _observed("closure/undecodable-attribution", lambda: _check_undecodable_fixture(None),
                                 patches)
        if not isinstance(got, want_type) or want_text not in str(got):
            raise AssertionError("closure/undecodable-attribution: {} gave {!r}, expected {} naming {!r}".format(
                name, got, want_type.__name__, want_text))

    tmp = None
    try:
        try:
            tmp = Path(tempfile.mkdtemp(prefix="opf-closure-utf8-"))
            _write_fixture(tmp, (("opf/tools/opf.py", b""), ("opf/tools/_opf_store.py", b"")))
        except OSError as exc:
            raise _CannotEvaluate("closure/undecodable-attribution: could not build the UTF-8 fixture: {}".format(
                exc))
        _result, got = _observed("closure/undecodable-attribution", lambda: _check_leg_wiring(tmp),
                                 dict(_closure_legs=wrong_result))
        if type(got) is not AssertionError or not str(got).startswith("closure/negative-bound-lookup"):
            raise AssertionError("closure/undecodable-attribution: wiring wrong-result gave {!r}, expected the "
                                 "closure/negative-bound-lookup refutation".format(got))
    finally:
        if tmp is not None:
            shutil.rmtree(tmp, ignore_errors=True)


def _check_preflight_collection():
    """_preflight_exit runs every case whatever an earlier case did: a _CannotEvaluate, or any other
    ordinary exception (here a RuntimeError and a decode failure), is that case's cannot-evaluate, the
    latter naming the case (by its label, or by its function's name when the label is None) and the
    exception; only an AssertionError is a refutation; the exit is failure-first over everything
    collected. A KeyboardInterrupt or SystemExit stops the collection: it re-raises and no later case
    runs. Each _preflight_exit call goes through _attributed. Pure: no scratch directory and no opf/
    subtree."""
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
        ((("closure/stubbed-label", _rootless(_boom_case)), sentinel), 2,
         ["closure/preflight-error: closure/stubbed-label raised RuntimeError: boom (stubbed)"], ["sentinel"]),
        (((None, _undecodable_case), (None, _refuting_case), sentinel), 1,
         ["closure/preflight-error: _undecodable_case raised UnicodeDecodeError: "], ["refute", "sentinel"]),
    )
    for cases, want_rc, want_cannot, want_ran in runs:
        del ran[:]
        rc, _out, err = _attributed("closure/preflight-collection", lambda: _captured(_preflight_exit, None),
                                    injected=dict(_PREFLIGHT_CASES=cases))
        lines = err.splitlines()
        unevaluated = [line[len("SELF-TEST CANNOT EVALUATE: "):] for line in lines
                       if line.startswith("SELF-TEST CANNOT EVALUATE: ")]
        refuted = [line for line in lines if line.startswith("SELF-TEST FAIL: ")]
        if (rc != want_rc or ran != want_ran or len(unevaluated) != len(want_cannot)
                or not all(got.startswith(want) for got, want in zip(unevaluated, want_cannot))
                or refuted != (["SELF-TEST FAIL: closure/stubbed-refutation"] if want_rc == 1 else [])):
            raise AssertionError("closure/preflight-collection: gave {} after {!r}: {!r}".format(rc, ran, lines))
    for stop in (KeyboardInterrupt, SystemExit):
        def _stop_case(_root, stop=stop):
            raise stop("stubbed")
        del ran[:]
        try:
            _attributed("closure/preflight-stop", lambda: _captured(_preflight_exit, None),
                        injected=dict(_PREFLIGHT_CASES=((None, _stop_case), sentinel)))
        except stop:
            pass
        except BaseException as exc:
            raise AssertionError("closure/preflight-stop: {} became {!r}".format(stop.__name__, exc))
        else:
            raise AssertionError("closure/preflight-stop: {} did not stop the collection".format(stop.__name__))
        if ran:
            raise AssertionError("closure/preflight-stop: {} ran {!r} after the stop".format(stop.__name__, ran))


def _check_preflight_failure_first(root):
    """_preflight_exit is failure-first, the same rule as run() and _self_test_exit. With no scratch
    directory (so every case that needs one is cannot-evaluate) and the negative verdict mutated to a
    substring match, the other preflight cases, run in REVERSE so the pure verdict case comes after
    the fixture cases, must exit 1 with both the refutation and the cannot-evaluate cases listed;
    without the mutant the same scratch failure exits 2. Over a root with no opf/ subtree (scratch
    available), the same cases exit 2 with nothing refuted: a missing subtree is never a refutation.
    "The other cases" leaves out this case and _check_attribution_inventory, which runs this case
    (running either here would recurse). Each _preflight_exit call goes through _attributed."""
    no_scratch = types.SimpleNamespace(mkdtemp=_no_scratch)

    def substring_verdict(rc, output, cannot):
        if cannot is not None:
            return "cannot", "stubbed"
        text = output if isinstance(output, str) else "".join(output)
        return ("caught", None) if rc != 0 and _FLIP_EVIDENCE in text else ("fail", "stubbed")
    cases = tuple(reversed([case for case in _PREFLIGHT_CASES
                            if case[1] not in (_check_preflight_failure_first, _check_attribution_inventory)]))
    for patches, want in ((dict(_negative_leg_verdict=substring_verdict), 1), (dict(), 2)):
        rc, _out, err = _attributed("closure/preflight-failure-first", lambda: _captured(_preflight_exit, root),
                                    injected=dict(tempfile=no_scratch, _PREFLIGHT_CASES=cases, **patches))
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
        rc, _out, err = _attributed("closure/preflight-absent-subtree",
                                    lambda: _captured(_preflight_exit, tmp / "no-opf-root"),
                                    injected=dict(_PREFLIGHT_CASES=cases))
    finally:
        if tmp is not None:
            shutil.rmtree(tmp, ignore_errors=True)
    lines = err.splitlines()
    if (rc != 2 or any(line.startswith("SELF-TEST FAIL: ") for line in lines)
            or not any(line.startswith("SELF-TEST CANNOT EVALUATE: ") for line in lines)):
        raise AssertionError("closure/preflight-absent-subtree: a root with no opf/ gave {}: {!r}".format(
            rc, lines))


def _red_bound_lookup(root):
    """RED cases: with the bound table emptied, each lookup check must refuse the 600 s it now passes.
    Each check runs through _attributed under its own label, so a crash is not read as the RED."""
    for check, label in ((_check_timeout_mapping, "closure/member-bound-lookup"),
                         (_check_leg_wiring, "closure/negative-bound-lookup")):
        try:
            _attributed("closure/red-bound-lookup", lambda: check(root), injected=dict(_MEMBER_TIMEOUT_S=dict()))
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


# The text every stubbed crash or host failure of the attribution pins carries.
_PIN_MARKER = "attribution pin (stubbed)"


class _Overlay:
    """`target` with some attributes replaced; every other attribute is read through to `target`."""

    def __init__(self, target, **attrs):
        self._target = target
        self.__dict__.update(attrs)

    def __getattr__(self, key):
        return getattr(self._target, key)


def _nth_call(name, attr, n, mode, fired):
    """A patch for the global `name` (or its attribute `attr`) that delegates to the one bound now,
    except that its n-th outermost call (1-based, counted from now; a call made while an earlier one
    is still running is delegated and not counted) appends n to `fired` and then, by mode:
    "crash" raises RuntimeError, "none" returns None (a malformed result), "oserror" raises OSError
    and "timeout" raises subprocess.TimeoutExpired (the last two stand for a host failure)."""
    target = globals()[name]
    inner = getattr(target, attr) if attr else target
    count, depth = [0], [0]

    def call(*args, **kwargs):
        if depth[0]:
            return inner(*args, **kwargs)
        count[0] += 1
        if count[0] != n:
            depth[0] += 1
            try:
                return inner(*args, **kwargs)
            finally:
                depth[0] -= 1
        fired.append(n)
        if mode == "crash":
            raise RuntimeError(_PIN_MARKER)
        if mode == "oserror":
            raise OSError(_PIN_MARKER)
        if mode == "timeout":
            raise subprocess.TimeoutExpired(_PIN_MARKER, 120)
        return None
    call.__name__ = getattr(inner, "__name__", name)
    return {name: _Overlay(target, **{attr: call}) if attr else call}


def _pin(entry, root, absent_root):
    """Run one _ATTRIBUTION_PINS entry: the case's check, over root (or over absent_root, a path with
    no opf/ subtree, when the entry says so), with its target's n-th call patched by mode, under this
    pin's own watch (a host failure there is _HarnessFailure). Nothing is attributed for the check:
    its own raw outcome is judged. When the patched call fired, a "crash" or "none" entry must give
    an AssertionError naming the entry's expected label (its last field) and "the code under test
    raised" (and, for a crash, the marker), and an "oserror" or "timeout" entry a _CannotEvaluate naming "harness failure" and
    the marker; anything else is a refutation. When it did not fire: the case's own AssertionError is
    raised as is (a refutation); a _CannotEvaluate is cannot-evaluate (its setup was missing), except
    for an absent-root entry, where reaching the call without the subtree is the point, so it is a
    refutation; a pass is a refutation (the call was skipped); any other exception is raised as is."""
    label, check, absent, name, attr, n, mode, want = entry
    site = "{} {}{}#{} {}".format(label, name, "." + attr if attr else "", n, mode)
    if absent and absent_root is None:
        raise _CannotEvaluate("closure/attribution-pin: {}: no scratch directory for a root with no opf/".format(site))
    fired, host = [], []
    with _patched(**_harness_watch(host)):
        with _patched(**_nth_call(name, attr, n, mode, fired)):
            try:
                _captured(check, absent_root if absent else root)
            except Exception as exc:
                got = exc
            else:
                got = None
    if host:
        raise _HarnessFailure("closure/attribution-pin: {}: harness failure: {}".format(site, " | ".join(host)))
    text = str(got)
    if fired:
        if mode in ("oserror", "timeout"):
            held = isinstance(got, _CannotEvaluate) and "harness failure" in text and _PIN_MARKER in text
            expected = "cannot-evaluate naming the host failure"
        else:
            held = (type(got) is AssertionError and want in text and "the code under test raised" in text
                    and (mode != "crash" or _PIN_MARKER in text))
            expected = "a refutation naming {!r}".format(want)
        if not held:
            raise AssertionError("closure/attribution-pin: {} gave {!r}, expected {}".format(site, got, expected))
        return
    if isinstance(got, AssertionError):
        raise got
    if isinstance(got, _CannotEvaluate) and not absent:
        raise _CannotEvaluate("closure/attribution-pin: {} was not reached: {}".format(site, got))
    if got is None or isinstance(got, _CannotEvaluate):
        raise AssertionError("closure/attribution-pin: {} was not reached{}: {!r}".format(
            site, " over a root with no opf/ subtree" if absent else "", got))
    raise got


def _check_pin_rules():
    """Pins _pin itself, over a pure target: an attributed crash holds; a crash that escapes raw, or
    that the case turns into cannot-evaluate, is refuted; a case that never reaches the call is
    cannot-evaluate, or refuted when the entry is over a root with no opf/ (or the case passed); a
    watched host failure holds, and the same failure outside any watch is refuted."""
    def attributed(_root):
        _attributed("closure/pin-probe", lambda: _positive_leg_verdict(0))

    def bare(_root):
        _positive_leg_verdict(0)

    def masked(_root):
        try:
            _positive_leg_verdict(0)
        except RuntimeError as exc:
            raise _CannotEvaluate("closure/pin-probe: {}".format(exc))

    def unreached(_root):
        raise _CannotEvaluate("closure/pin-probe: no fixture (stubbed)")

    def skipped(_root):
        return None

    def watched(_root):
        _attributed("closure/pin-probe", lambda: _write_fixture(Path("."), ()))

    def unwatched(_root):
        _write_fixture(Path("."), ())
    probes = (
        (attributed, False, "_positive_leg_verdict", "crash", None),
        (bare, False, "_positive_leg_verdict", "crash", AssertionError),
        (masked, False, "_positive_leg_verdict", "crash", AssertionError),
        (unreached, False, "_positive_leg_verdict", "crash", _CannotEvaluate),
        (unreached, True, "_positive_leg_verdict", "crash", AssertionError),
        (skipped, False, "_positive_leg_verdict", "crash", AssertionError),
        (watched, False, "_write_fixture", "oserror", None),
        (unwatched, False, "_write_fixture", "oserror", AssertionError),
    )
    for check, absent, name, mode, want in probes:
        try:
            _pin(("closure/pin-probe", check, absent, name, None, 1, mode, "closure/pin-probe"),
                 None, Path("no-opf-root") if absent else None)
        except (AssertionError, _CannotEvaluate) as exc:
            got = exc
        else:
            got = None
        if (type(got) if got is not None else None) is not want:
            raise AssertionError("closure/pin-rules: {} over {} ({}) gave {!r}, expected {}".format(
                check.__name__, name, mode, got, want.__name__ if want else "a pass"))


def _check_attribution_inventory(root):
    """The attribution inventory: every site at which a _PREFLIGHT_CASES case calls the code under test
    (for a site in a loop, its first iteration) is pinned by an _ATTRIBUTION_PINS entry (see _pin), so
    reverting that site's attribution (calling it bare, or interpreting its result outside
    _attributed) fails here, and so do widening the watch past OSError and TimeoutExpired and
    requiring the opf/ subtree before the sub-checks of _check_scratch_and_copy_errors and
    _check_negative_copy_error that do not need it. _check_pin_rules runs first. Unlike the other cases, this one does not stop at its
    first cannot-evaluate entry: it runs every entry and is failure-first over them, raising the
    first refutation at once and the cannot-evaluate entries together at the end."""
    _check_pin_rules()
    unevaluated = []
    tmp = None
    try:
        try:
            tmp = Path(tempfile.mkdtemp(prefix="opf-closure-pins-"))
        except OSError as exc:
            unevaluated.append("closure/attribution-pin: no scratch directory for a root with no opf/: {}".format(exc))
        for entry in _ATTRIBUTION_PINS:
            try:
                _pin(entry, root, None if tmp is None else tmp / "no-opf-root")
            except _CannotEvaluate as exc:
                unevaluated.append(str(exc))
    finally:
        if tmp is not None:
            shutil.rmtree(tmp, ignore_errors=True)
    if unevaluated:
        raise _CannotEvaluate(" | ".join(unevaluated))


# The attribution inventory (see _check_attribution_inventory and _pin): one entry per call that a
# _PREFLIGHT_CASES case makes to the code under test, as (the case's label, its check, True when the
# entry runs over a root with no opf/ subtree, the patched global, its patched attribute or None,
# which call to patch, mode, the label the refutation must name or None for a host failure).
_PACK_REG, _ADOPT_REG, _ROWS = (_rootless(_pack_manifest_registration_self_test),
                                _rootless(_adopt_observe_registration_self_test),
                                _rootless(_member_timeout_rows_self_test))
_VERDICTS, _COLLECTION = _rootless(_check_verdict_tables), _rootless(_check_preflight_collection)
_ATTRIBUTION_PINS = (
    ("pack-manifest-registration", _PACK_REG, False, "_check_pack_manifest_registration", None, 1, "crash",
     "closure/pack-manifest-registration"),
    ("pack-manifest-registration", _PACK_REG, False, "_check_pack_manifest_registration", None, 2, "crash",
     "closure/pack-manifest-registration"),
    ("adopt-observe-registration", _ADOPT_REG, False, "_check_adopt_observe_registration", None, 1, "crash",
     "closure/adopt-observe-registration"),
    ("adopt-observe-registration", _ADOPT_REG, False, "_check_adopt_observe_registration", None, 2, "crash",
     "closure/adopt-observe-registration"),
    ("member-timeout-rows", _ROWS, False, "_check_member_timeout_rows", None, 1, "crash", "closure/member-timeout-rows"),
    ("member-timeout-rows", _ROWS, False, "_check_member_timeout_rows", None, 2, "crash", "closure/member-timeout-rows"),
    ("member-timeout-rows", _ROWS, False, "_check_member_timeout_rows", None, 3, "crash", "closure/member-timeout-rows"),
    ("self-test-leg-verdicts", _VERDICTS, False, "_negative_leg_verdict", None, 1, "none", "closure/negative-verdict"),
    ("self-test-leg-verdicts", _VERDICTS, False, "_positive_leg_verdict", None, 1, "none", "closure/positive-verdict"),
    ("preflight-collection", _COLLECTION, False, "_preflight_exit", None, 1, "crash", "closure/preflight-collection"),
    ("preflight-collection", _COLLECTION, False, "_preflight_exit", None, 5, "crash", "closure/preflight-stop"),
    ("timeout-cannot-evaluate", _check_timeout_mapping, False, "run", None, 1, "crash", "closure/stubbed-run"),
    ("timeout-cannot-evaluate", _check_timeout_mapping, False, "tempfile", "mkdtemp", 1, "crash", "closure/stubbed-run"),
    ("timeout-cannot-evaluate", _check_timeout_mapping, False, "_materialize", None, 1, "crash", "closure/stubbed-run"),
    ("harness-cannot-evaluate", _check_harness_mapping, False, "_run_one", None, 1, "none",
     "closure/harness-missing-script"),
    ("harness-cannot-evaluate", _check_harness_mapping, False, "_run_one", None, 2, "none", "closure/run-one-timeout"),
    ("harness-cannot-evaluate", _check_harness_mapping, False, "_run_one", None, 3, "none", "closure/harness-launch"),
    ("harness-cannot-evaluate", _check_harness_mapping, False, "_run_one", None, 4, "none", "closure/evidence-streams"),
    ("harness-cannot-evaluate", _check_harness_mapping, False, "_negative_leg_verdict", None, 1, "none",
     "closure/evidence-streams"),
    ("harness-cannot-evaluate", _check_harness_mapping, False, "_isolated_env", None, 1, "crash",
     "closure/harness-missing-script"),
    ("harness-cannot-evaluate", _check_harness_mapping, False, "_isolated_env", None, 2, "crash",
     "closure/run-one-timeout"),
    ("harness-cannot-evaluate", _check_harness_mapping, False, "_isolated_env", None, 3, "crash",
     "closure/harness-launch"),
    ("harness-cannot-evaluate", _check_harness_mapping, False, "_isolated_env", None, 4, "crash",
     "closure/evidence-streams"),
    ("harness-cannot-evaluate", _check_harness_mapping, False, "subprocess", "run", 1, "crash",
     "closure/evidence-streams"),
    ("harness-cannot-evaluate", _check_harness_mapping, False, "subprocess", "run", 1, "oserror", None),
    ("harness-cannot-evaluate", _check_harness_mapping, False, "subprocess", "run", 2, "timeout", None),
    ("harness-cannot-evaluate", _check_harness_mapping, False, "run", None, 1, "crash", "closure/stubbed-run"),
    ("failure-first", _check_failure_first, False, "run", None, 1, "crash", "closure/stubbed-run"),
    ("self-test-leg-wiring", _check_leg_wiring, False, "_closure_legs", None, 1, "none", "closure/self-test-exit"),
    ("self-test-leg-wiring", _check_leg_wiring, False, "_self_test_exit", None, 1, "crash", "closure/self-test-exit"),
    ("self-test-leg-wiring", _check_leg_wiring, False, "_materialize", None, 1, "crash", "closure/self-test-exit"),
    ("self-test-leg-wiring", _check_leg_wiring, False, "_read_copied_store", None, 1, "crash",
     "closure/self-test-exit"),
    ("self-test-leg-wiring", _check_leg_wiring, False, "_read_copied_store", None, 1, "oserror", None),
    ("self-test-leg-wiring", _check_leg_wiring, False, "_write_copied_store", None, 1, "oserror", None),
    ("scratch-and-copy-cannot-evaluate", _check_scratch_and_copy_errors, False, "_check_harness_mapping", None, 1,
     "crash", "closure/scratch-harness"),
    ("scratch-and-copy-cannot-evaluate", _check_scratch_and_copy_errors, False, "_closure_legs", None, 1, "none",
     "closure/negative-harness"),
    ("scratch-and-copy-cannot-evaluate", _check_scratch_and_copy_errors, False, "_self_test_exit", None, 1, "crash",
     "closure/negative-harness"),
    ("scratch-and-copy-cannot-evaluate", _check_scratch_and_copy_errors, False, "run", None, 1, "crash",
     "closure/scratch-run"),
    ("scratch-and-copy-cannot-evaluate", _check_scratch_and_copy_errors, False, "run", None, 2, "crash",
     "closure/copy-run"),
    ("scratch-and-copy-cannot-evaluate", _check_scratch_and_copy_errors, True, "_closure_legs", None, 1, "none",
     "closure/negative-harness"),
    ("negative-copy-cannot-evaluate", _check_negative_copy_error, False, "_closure_legs", None, 1, "none",
     "closure/negative-copy"),
    ("negative-copy-cannot-evaluate", _check_negative_copy_error, False, "_self_test_exit", None, 1, "crash",
     "closure/negative-copy"),
    ("negative-copy-cannot-evaluate", _check_negative_copy_error, True, "_closure_legs", None, 1, "none",
     "closure/negative-copy"),
    ("absent-subtree-cannot-evaluate", _check_absent_subtree, False, "_check_timeout_mapping", None, 1, "crash",
     "closure/absent-subtree"),
    ("absent-subtree-cannot-evaluate", _check_absent_subtree, False, "_check_harness_mapping", None, 1, "crash",
     "closure/absent-subtree"),
    ("absent-subtree-cannot-evaluate", _check_absent_subtree, False, "_check_failure_first", None, 1, "crash",
     "closure/absent-subtree"),
    ("absent-subtree-cannot-evaluate", _check_absent_subtree, False, "_check_leg_wiring", None, 1, "crash",
     "closure/absent-subtree"),
    ("absent-subtree-cannot-evaluate", _check_absent_subtree, False, "_check_scratch_and_copy_errors", None, 1,
     "crash", "closure/absent-subtree"),
    ("absent-subtree-cannot-evaluate", _check_absent_subtree, False, "_check_timeout_mapping", None, 2, "crash",
     "closure/copy-before-members"),
    ("absent-subtree-cannot-evaluate", _check_absent_subtree, False, "_check_timeout_mapping", None, 3, "crash",
     "closure/members-never-ran"),
    ("absent-subtree-cannot-evaluate", _check_absent_subtree, False, "_check_timeout_mapping", None, 4, "crash",
     "closure/members-never-copied"),
    ("undecodable-fixture-cannot-evaluate", _check_undecodable_fixture, False, "_closure_legs", None, 1, "none",
     "closure/undecodable-fixture"),
    ("undecodable-fixture-cannot-evaluate", _check_undecodable_fixture, False, "_self_test_exit", None, 1, "crash",
     "closure/undecodable-fixture"),
    ("undecodable-fixture-cannot-evaluate", _check_undecodable_fixture, False, "_check_leg_wiring", None, 1, "crash",
     "closure/undecodable-wiring"),
    ("undecodable-fixture-cannot-evaluate", _check_undecodable_fixture, False, "_not_utf8", None, 1, "oserror", None),
    ("undecodable-attribution", _check_undecodable_attribution, False, "_check_undecodable_fixture", None, 1,
     "crash", "closure/undecodable-attribution"),
    ("undecodable-attribution", _check_undecodable_attribution, False, "_check_leg_wiring", None, 3, "crash",
     "closure/undecodable-attribution"),
    ("undecodable-attribution", _check_undecodable_attribution, False, "_write_fixture", None, 1, "oserror", None),
    ("undecodable-attribution", _check_undecodable_attribution, False, "_not_utf8", None, 3, "oserror", None),
    ("preflight-failure-first", _check_preflight_failure_first, False, "_preflight_exit", None, 1, "crash",
     "closure/preflight-failure-first"),
    ("preflight-failure-first", _check_preflight_failure_first, False, "_preflight_exit", None, 3, "crash",
     "closure/preflight-absent-subtree"),
    ("red-bound-lookup", _red_bound_lookup, False, "_check_timeout_mapping", None, 1, "crash",
     "closure/red-bound-lookup"),
    ("red-bound-lookup", _red_bound_lookup, False, "_check_leg_wiring", None, 1, "crash", "closure/red-bound-lookup"),
)


# The self-test's preflight: the registration cases and the in-process stubbed cases (no subset member
# runs), as (PASS label, or None for a case that prints its own lines, check(root)). The first five need
# no scratch directory and no opf/ subtree; the rest build fixtures or stub run() over the real subtree,
# each ordered by the setup its sub-checks need (see _preflight_exit). The attribution inventory runs
# last, once every case it pins has run on its own.
_PREFLIGHT_CASES = (
    (None, _PACK_REG),
    (None, _ADOPT_REG),
    (None, _ROWS),
    ("closure/self-test-leg-verdicts", _VERDICTS),
    ("closure/preflight-collection", _COLLECTION),
    ("closure/timeout-cannot-evaluate", _check_timeout_mapping),
    ("closure/harness-cannot-evaluate", _check_harness_mapping),
    ("closure/failure-first", _check_failure_first),
    ("closure/self-test-leg-wiring", _check_leg_wiring),
    ("closure/scratch-and-copy-cannot-evaluate", _check_scratch_and_copy_errors),
    ("closure/negative-copy-cannot-evaluate", _check_negative_copy_error),
    ("closure/absent-subtree-cannot-evaluate", _check_absent_subtree),
    ("closure/undecodable-fixture-cannot-evaluate", _check_undecodable_fixture),
    ("closure/undecodable-attribution", _check_undecodable_attribution),
    ("closure/preflight-failure-first", _check_preflight_failure_first),
    (None, _red_bound_lookup),
    ("closure/attribution-inventory", _check_attribution_inventory),
)


def _preflight_exit(root):
    """Run every _PREFLIGHT_CASES case on its own, collecting each outcome, so no case's outcome stops a
    later case.

    The attribution rule: a failure of the HOST (a scratch directory, copy, fixture write, store read,
    flip write or child launch that raised an OSError, or a child that did not finish within its
    bound, at any point, including one step inside a case after others succeeded) is cannot-evaluate;
    a crash or a wrong result of the CODE UNDER TEST is a refutation. Every call a case makes to the
    code under test goes through _attributed (or _observed, when the case judges the raised outcome
    itself), with any indexing or unpacking of its result inside that call. (Around run(), _captured
    always gives a (rc, stdout, stderr) triple whose rc alone comes from the code under test, and rc
    is only compared and printed.) _attributed tells a host failure apart by watching the host
    primitives themselves (_harness_watch, which documents what it does not watch), never the code's
    messages. _check_attribution_inventory pins every such call; it and _check_pin_rules call cases
    and a probe target without _attributed on purpose, to judge their raw outcome (see _pin).

    So an AssertionError is that case's refutation (SELF-TEST FAIL). A _CannotEvaluate, or any other
    Exception (one raised by the case's own code outside every attributed call: a fixture it could not
    build, a decode failure, a RuntimeError), is that case's cannot-evaluate (SELF-TEST CANNOT
    EVALUATE; for any other Exception the line names the case, by its label or else its function's
    name, and the exception). Only a BaseException that is not an Exception (KeyboardInterrupt,
    SystemExit) stops the collection: it re-raises. Failure-first, the same rule as run() and
    _self_test_exit: 1 when any case was refuted, else 2 when any could not be evaluated, else None
    (every case held). Within ONE case (except _check_attribution_inventory, which runs all its
    entries) the first sub-check that cannot be evaluated ends that case: its later sub-checks do not
    run, so a refutation they would give stays unobserved until that case can be set up. Hence the
    cases are split and ordered by setup: the pure cases need none; _check_negative_copy_error needs a
    scratch directory but no opf/ subtree, so it is its own case; in _check_scratch_and_copy_errors
    and _check_absent_subtree the sub-checks that need no opf/ subtree run before it is required
    (the inventory's entries over a root with no opf/ pin that order). Within a case whose fixture
    is built first, every sub-check waits for that fixture even when it would need less (in
    _check_harness_mapping, closure/harness-missing-script needs no scratch directory)."""
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
    deliberately-broken copy would provide no coverage. The preflight (_preflight_exit) runs first and
    follows its attribution rule: when any of its cases was refuted (a wrong result or a crash of the
    code under test) the self-test exits 1; when none was refuted but one could not be evaluated (no
    opf/ subtree, a host primitive that failed, malformed input, or any other ordinary exception
    raised by a case's own code outside its attributed calls) it exits 2; in both outcomes the legs
    never run. Otherwise the legs decide, failure-first: 1 when either leg was refuted, whatever else
    could not be evaluated; 2 when neither was refuted but one could not be evaluated (run() exit 2,
    or the flipped copy timed out, could not launch, its scratch directory, copy or flip could not be
    made, or its _opf_store.py is not UTF-8 text); 0 only when both held, never PASS on a leg that was
    not evaluated."""
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
