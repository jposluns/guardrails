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
  2  malformed input or a harness error (fail-closed): opf/ absent/unreadable, copy failed, no interpreter,
     a per-leg bound that cannot be derived, or a leg killed at its wall-clock bound (named as the bound
     tripping, never as a failure the leg reported; see _LEG_CEILING_S for how each bound is derived)
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: check_opf_standalone_closure.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import ast
import contextlib
import io
import math
import os
import re
import shutil
import subprocess
import tempfile
import time
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
    ("opf-adopt-state-selftest", "_opf_adopt_state.py", ["--self-test"]),
    ("opf-adopt-hook-selftest", "_opf_adopt_hook.py", ["--self-test"]),
    ("opf-adopt-complete-selftest", "_opf_adopt_complete.py", ["--self-test"]),
    ("opf-import-batch-selftest", "_opf_import_batch.py", ["--self-test"]),
    ("opf-import-complete-selftest", "_opf_import_complete.py", ["--self-test"]),
    ("opf-pack-manifest-selftest", "_opf_pack_manifest.py", ["--self-test"]),
    ("opf-adopt-observe-selftest", "_opf_adopt_observe.py", ["--self-test"]),
    ("opf-adopt-discover-selftest", "_opf_adopt_discover.py", ["--self-test"]),
    ("opf-prompt-pack-selftest", "check_opf_prompt_pack.py", ["--self-test"]),
    ("opf-prompt-pack", "check_opf_prompt_pack.py", []),
    ("opf-oplock-selftest", "_opf_oplock.py", ["--self-test"]),
    ("opf-init-substrate-selftest", "_opf_init_substrate.py", ["--self-test"]),
    ("opf-init-builders-selftest", "_opf_init.py", ["--self-test"]),
    ("opf-init-operation-selftest", "_opf_init_operation.py", ["--self-test"]),
    ("opf-init-p0-selftest", "check_opf_init_p0.py", ["--self-test", "--red-on-revert"]),
    ("opf-init-observe-selftest", "check_opf_init_observe.py", ["--self-test", "--red-on-revert"]),
    ("commonmark-headings-selftest", "selftest_commonmark_headings.py", []),
    ("commonmark-conformance", "selftest_commonmark_conformance.py", []),
]

# Per-leg wall-clock bounds (rule kill-timeout-exceeds-callee-wait). A bound exists to cut off a hung leg,
# so it must sit ABOVE the work the leg is sanctioned to do; one flat constant below the work of a leg kills a
# correctly-running leg. The former flat 600 s did exactly that on a loaded host (the opf-adopt-apply
# self-test took 651 to 672 s and opf.py --self-test 894 s), and it sat below the 1800 s the adopt-apply
# self-test itself waits on its class-sweep child. Each leg bound is the LARGEST of:
#   * _LOAD_HEADROOM times the slowest known run of that leg (_LEG_CEILING_S);
#   * the largest single child wait the leg declares in its own sources (a numeric timeout= argument or
#     `timeout` parameter default, read from the copied sources on every run by _declared_wait_s) plus
#     _LAUNCH_MARGIN_S, so the gate never kills a leg inside a wait the leg itself sanctions;
#   * _MIN_LEG_BOUND_S, the floor for the legs that finish in seconds.
# The legs run one after another, so the worst case is the SUM of the bounds; the self-test requires that sum
# plus _CI_SETUP_ALLOWANCE_S to fit inside the time limit of this gate CI job.
# The literal scan does not see computed waits. opf.py --self-test sizes its watchdog cases at run time (one
# alone, blocked-isolation, is 31 fixture calls of about 186 s each), and their sum approaches the CI job limit
# on its own, so no bound on that leg can honour it and still fit the job. Its bound is therefore a deliberate
# deadline sized from its known runs; when it trips, _report names the bound, never a failure of the leg.
# Each ceiling below is whole seconds, rounded up, from one of the sources named on its row: a serial
# isolated run on 2026-10-11 (1-minute load average 8.8 to 12.9 on 24 CPUs, under nice -n 10), or, for a
# leg added after it, a serial run on 2026-10-14 or 2026-10-17 (not under nice; its load average on
# the row); the
# 2026-10-11 loaded-host record of the two legs the former bound killed; or, for a leg that run could not
# measure (its fixtures refuse a scratch directory below an unreadable ancestor), the former 600 s bound,
# which that record shows the leg finishing under (an inference, not a measurement: re-measure and tighten).
_LEG_CEILING_S = {
    "opf-homes-selftest": 600,  # not measured 2026-10-11; under the former 600 s bound (inferred)
    "opf-homes-contract": 1,  # measured 2026-10-11: 0.3 s
    "opf-tooling-selftest": 894,  # recorded 2026-10-11, loaded host
    "opf-drift-selftest": 3,  # measured 2026-10-11: 2.9 s
    "opf-doctor-selftest": 72,  # measured 2026-10-11: 71.5 s
    "opf-init-selftest": 600,  # not measured 2026-10-11; under the former 600 s bound (inferred)
    "opf-init-contract-validator-selftest": 1,  # measured 2026-10-11: under 0.1 s
    "opf-init-contract-check-selftest": 1,  # measured 2026-10-11: under 0.1 s
    "opf-upgrade-selftest": 600,  # not measured 2026-10-11; under the former 600 s bound (inferred)
    "opf-adopt-selftest": 600,  # not measured 2026-10-11; under the former 600 s bound (inferred)
    "opf-adopt-apply-selftest": 672,  # recorded 2026-10-11, loaded host (651 to 672 s)
    "opf-adopt-state-selftest": 600,  # not measured 2026-10-11; under the former 600 s bound (inferred)
    "opf-adopt-hook-selftest": 2,  # measured 2026-10-11: 1.3 s
    "opf-adopt-complete-selftest": 600,  # not measured 2026-10-11; under the former 600 s bound (inferred)
    "opf-import-batch-selftest": 1,  # measured 2026-10-17: 0.6 s (load average 17.7 on 24 CPUs)
    # opf-import-complete-selftest: the slowest run times 20 / 3 for load, 2.4 * 20 / 3 = 16.0, rounded up to
    # a multiple of 5, 20 s.
    "opf-import-complete-selftest": 20,  # measured 2026-10-14: 2.3 to 2.4 s (three runs)
    "opf-pack-manifest-selftest": 18,  # measured 2026-10-11: 17.3 s
    "opf-adopt-observe-selftest": 600,  # not measured 2026-10-11; under the former 600 s bound (inferred)
    "opf-adopt-discover-selftest": 1,  # measured 2026-10-14: 0.4 s (load average 17.1 on 24 CPUs)
    "opf-prompt-pack-selftest": 1,  # measured 2026-10-11: 0.7 s
    "opf-prompt-pack": 1,  # measured 2026-10-11: 0.1 s
    "opf-oplock-selftest": 600,  # not measured 2026-10-11; under the former 600 s bound (inferred)
    "opf-init-substrate-selftest": 600,  # not measured 2026-10-11; under the former 600 s bound (inferred)
    "opf-init-builders-selftest": 1,  # measured 2026-10-11: 0.2 s
    "opf-init-operation-selftest": 600,  # not measured 2026-10-11; under the former 600 s bound (inferred)
    "opf-init-p0-selftest": 40,  # measured 2026-10-11: 39.8 s
    "opf-init-observe-selftest": 1,  # measured 2026-10-11: 1.0 s
    "commonmark-headings-selftest": 14,  # measured 2026-10-11: 13.3 s
    "commonmark-conformance": 1,  # measured 2026-10-11: 0.2 s
}
_LOAD_HEADROOM = 2
_LAUNCH_MARGIN_S = 60
_MIN_LEG_BOUND_S = 120

_CI_WORKFLOW = Path(".github") / "workflows" / "quality.yml"
_CI_JOB = "opf-closure"
_CI_DEFAULT_JOB_MINUTES = 360  # the GitHub Actions limit for a hosted-runner job that sets no timeout-minutes
_CI_SETUP_ALLOWANCE_S = 600    # checkout with full history and setup-python, before the gate step starts


class _BoundError(Exception):
    """A per-leg bound cannot be derived (no ceiling, or a source that cannot be read or parsed)."""


def _numbers(node):
    """Every int or float literal under node (a bool is not a duration)."""
    return [n.value for n in ast.walk(node)
            if isinstance(n, ast.Constant) and type(n.value) in (int, float)]


def _parse(path):
    try:
        return ast.parse(Path(path).read_text(encoding="utf-8"), filename=str(path))
    except (OSError, UnicodeDecodeError, SyntaxError, ValueError) as exc:
        raise _BoundError("cannot read the declared waits of {}: {}".format(path, exc))


def _declared_wait_s(paths):
    """The largest single wait, in seconds, that the given sources declare: a numeric literal in a `timeout=`
    keyword argument (both arms of a conditional count) or in the default of a parameter named `timeout`. A
    wait computed at run time (a name, a call, arithmetic) is not seen; 0 when none is declared. Raises
    _BoundError when a source cannot be read or parsed (fail-closed)."""
    found = [0]
    for path in paths:
        for node in ast.walk(_parse(path)):
            if isinstance(node, ast.keyword) and node.arg == "timeout":
                found.extend(_numbers(node.value))
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                spec = node.args
                positional = spec.posonlyargs + spec.args
                pairs = list(zip(positional[len(positional) - len(spec.defaults):], spec.defaults))
                pairs.extend((arg, default) for arg, default in zip(spec.kwonlyargs, spec.kw_defaults)
                             if default is not None)
                found.extend(n for arg, default in pairs if arg.arg == "timeout" for n in _numbers(default))
    return max(found)


def _self_test_modules(opf_py):
    """The helper modules whose self-tests opf.py --self-test runs in-process (the `<module>.self_test`
    references in its _self_tests() registry), as file names beside opf.py: their declared waits count
    toward the opf.py leg. Raises _BoundError when the registry cannot be found (fail-closed)."""
    for node in _parse(opf_py).body:
        if isinstance(node, ast.FunctionDef) and node.name == "_self_tests":
            names = sorted({ref.value.id if isinstance(ref.value, ast.Name) else ref.value.attr
                            for ref in ast.walk(node)
                            if isinstance(ref, ast.Attribute) and ref.attr == "self_test"
                            and isinstance(ref.value, (ast.Name, ast.Attribute))})
            if names:
                return [name + ".py" for name in names]
    raise _BoundError("{} has no _self_tests() registry naming helper self-tests".format(opf_py))


def _leg_bounds(tools_dir, ceilings=None):
    """{leg name: (bound_s, ceiling_s, declared wait_s)} for every subset leg, derived from the
    sources under tools_dir. Raises _BoundError (fail-closed) for a leg with no ceiling."""
    ceilings = _LEG_CEILING_S if ceilings is None else ceilings
    tools_dir = Path(tools_dir)
    bounds = {}
    for name, script, _args in _SUBSET:
        ceiling = ceilings.get(name)
        if type(ceiling) is not int or ceiling <= 0:
            raise _BoundError("leg {} has no ceiling in _LEG_CEILING_S".format(name))
        sources = [tools_dir / script]
        if script == "opf.py":
            sources.extend(tools_dir / module for module in _self_test_modules(tools_dir / script))
        declared = _declared_wait_s(sources)
        bound = max(_MIN_LEG_BOUND_S, _LOAD_HEADROOM * ceiling, math.ceil(declared) + _LAUNCH_MARGIN_S)
        bounds[name] = (bound, ceiling, declared)
    return bounds


def _bound_faults(bounds, required):
    """Each leg whose bound is missing, not a finite positive number, or below the bound its own work
    requires (required is the _leg_bounds result)."""
    faults = []
    for name, _script, _args in _SUBSET:
        have, need = bounds.get(name), required[name][0]
        if type(have) not in (int, float) or not math.isfinite(have) or have <= 0:
            faults.append("{}: bound {!r} is not a finite positive number".format(name, have))
        elif have < need:
            faults.append("{}: bound {}s below the {}s its work requires".format(name, have, need))
    return faults


def _ci_job_limit_s(workflow_text, job=_CI_JOB):
    """The time limit, in seconds, of `job` in the workflow text: the smallest timeout-minutes set on the
    job or on any of its steps, capped at _CI_DEFAULT_JOB_MINUTES. None when the job is absent or a
    timeout-minutes value is not a plain integer (cannot evaluate)."""
    lines = workflow_text.splitlines()
    try:
        start = lines.index("  {}:".format(job))
    except ValueError:
        return None
    minutes = [_CI_DEFAULT_JOB_MINUTES]
    for line in lines[start + 1:]:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and len(line) - len(line.lstrip(" ")) <= 2:
            break  # the next job, or a top-level key, ends this job
        if stripped.startswith("timeout-minutes:"):
            value = stripped[len("timeout-minutes:"):].split("#")[0].strip()
            if not re.fullmatch(r"[0-9]+", value):
                return None
            minutes.append(int(value))
    return 60 * min(minutes)


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


def _check_import_batch_registration(subset):
    """Independent requirement, not inferred from the release manifest.

    Covers this exact closure-roster tuple, not arbitrary dispatcher changes
    or the registrations of other gates.
    """
    expected = ("opf-import-batch-selftest", "_opf_import_batch.py", ["--self-test"])
    matches = [row for row in subset
               if row[0] == expected[0] or row[1] == expected[1]]
    if matches != [expected]:
        raise AssertionError("closure/import-batch-registration")


def _import_batch_registration_self_test():
    _check_import_batch_registration(_SUBSET)
    print("PASS closure/import-batch-registration")
    # Mutate the real roster, independently of the shell-runner deletion.
    # No digest or generated-manifest check participates in the verdict.
    changed = list(_SUBSET)
    changed.remove(("opf-import-batch-selftest", "_opf_import_batch.py", ["--self-test"]))
    try:
        _check_import_batch_registration(changed)
    except AssertionError as exc:
        if str(exc) != "closure/import-batch-registration":
            raise
    else:
        raise AssertionError("closure/import-batch-registration-not-red")
    print("RED closure-registration -> closure/import-batch-registration")


def _check_import_complete_registration(subset):
    """Independent requirement, not inferred from the release manifest.

    Covers this exact closure-roster tuple, not arbitrary dispatcher changes
    or the registrations of other gates.
    """
    expected = ("opf-import-complete-selftest", "_opf_import_complete.py", ["--self-test"])
    matches = [row for row in subset
               if row[0] == expected[0] or row[1] == expected[1]]
    if matches != [expected]:
        raise AssertionError("closure/import-complete-registration")


def _import_complete_registration_self_test():
    _check_import_complete_registration(_SUBSET)
    print("PASS closure/import-complete-registration")
    # Mutate the real roster, independently of the shell-runner deletion.
    # No digest or generated-manifest check participates in the verdict.
    changed = list(_SUBSET)
    changed.remove(("opf-import-complete-selftest", "_opf_import_complete.py", ["--self-test"]))
    try:
        _check_import_complete_registration(changed)
    except AssertionError as exc:
        if str(exc) != "closure/import-complete-registration":
            raise
    else:
        raise AssertionError("closure/import-complete-registration-not-red")
    print("RED closure-registration -> closure/import-complete-registration")


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


def _tail(output):
    return "\n".join((output or b"").decode("utf-8", "replace").splitlines()[-3:])


def _run_one(opf_root, script, args, run_dir, env, bound_s):
    """Run one subset member isolated against the copied opf/tools/<script>, killed once it has run for
    bound_s seconds. Returns (rc, tail_of_output, elapsed_s); rc is None when the leg was killed at its bound
    (the bound tripped; the leg itself reported nothing). cwd is run_dir, which is OUTSIDE any git repository
    and does not contain the tools/ tree, so only the copied opf/tools/ is reachable to `python3 -I`."""
    target = opf_root / "tools" / script
    if not target.is_file():
        return 2, "missing subset script in the copy: {}".format(target), 0.0
    start = time.monotonic()
    try:
        proc = subprocess.run(
            [sys.executable, "-I", "-B", str(target), *args],
            cwd=str(run_dir), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            timeout=bound_s,
        )
    except subprocess.TimeoutExpired as exc:
        return None, _tail(exc.output), time.monotonic() - start
    except OSError as exc:
        return 2, "could not launch the interpreter: {}".format(exc), time.monotonic() - start
    return proc.returncode, _tail(proc.stdout), time.monotonic() - start


def _run_subset(opf_root, run_dir, bounds):
    """Run every subset member under its own bound. Returns a list of (name, rc, tail, elapsed_s, bound_s)."""
    _check_pack_manifest_registration(_SUBSET)
    _check_adopt_observe_registration(_SUBSET)
    _check_import_batch_registration(_SUBSET)
    _check_import_complete_registration(_SUBSET)
    env = _isolated_env()
    results = []
    for name, script, args in _SUBSET:
        bound = bounds[name][0]
        results.append((name, *_run_one(opf_root, script, args, run_dir, env, bound), bound))
    return results


def _report(results):
    """Print one line per leg and the verdict, and return the exit code. A leg that exited non-zero is a
    finding (1). A leg killed at its bound is reported as the bound tripping, never as a failure the leg
    reported; with no finding beside it the gate cannot evaluate closure (2), and never reads as clean."""
    failed = [row for row in results if row[1] not in (0, None)]
    timed_out = [row for row in results if row[1] is None]
    for name, rc, _tail_text, elapsed, bound in results:
        if rc == 0:
            status = "OK ({:.1f}s of its {}s bound)".format(elapsed, bound)
        elif rc is None:
            status = "TIMED OUT (killed at its {}s bound)".format(bound)
        else:
            status = "FAILED rc={} ({:.1f}s)".format(rc, elapsed)
        print("  {:38s} {}".format(name, status))
    if failed:
        print("STANDALONE CLOSURE: BROKEN (the isolated opf/ subtree does not verify itself):",
              file=sys.stderr)
        for name, rc, tail, _elapsed, _bound in failed:
            print("  {} (rc={}): {}".format(name, rc, tail.replace("\n", " | ")), file=sys.stderr)
    if timed_out:
        print("STANDALONE CLOSURE: LEG BOUND EXCEEDED (the gate killed each leg below at its wall-clock bound; "
              "the bound tripped, not a failure the leg reported, so closure was not evaluated for it):",
              file=sys.stderr)
        for name, _rc, tail, elapsed, bound in timed_out:
            print("  {}: killed at its {}s bound after {:.1f}s; last output: {}".format(
                name, bound, elapsed, tail.replace("\n", " | ") or "(none)"), file=sys.stderr)
    if failed:
        return 1
    if timed_out:
        return 2
    print("STANDALONE CLOSURE: OK (opf/ verifies itself with no AIQT tree reachable)")
    return 0


def run(root):
    """Materialize opf/ alone and run the subset in isolation. Returns 0 (closure holds), 1 (a subset gate
    failed: closure broken), or 2 (harness error, opf/ unreadable, or a leg killed at its bound), fail-closed."""
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
            bounds = _leg_bounds(opf_root / "tools")
        except _BoundError as exc:
            print("error: cannot derive the per-leg bounds: {}".format(exc), file=sys.stderr)
            return 2
        try:
            results = _run_subset(opf_root, tmp, bounds)
        except AssertionError as exc:
            print("STANDALONE CLOSURE: FAILED:", str(exc), file=sys.stderr)
            return 1
        return _report(results)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _bounds_self_test(root):
    """The per-leg bounds hold the rule on the real tree, fit the CI job, and the rule is discriminating.
    Returns the derived bounds; raises AssertionError naming the first broken property."""
    names = [name for name, _script, _args in _SUBSET]
    if sorted(_LEG_CEILING_S) != sorted(names):
        raise AssertionError("closure/leg-ceilings-match-the-subset")
    required = _leg_bounds(Path(root) / "opf" / "tools")
    live = dict((name, row[0]) for name, row in required.items())
    faults = _bound_faults(live, required)
    if faults:
        raise AssertionError("closure/leg-bounds-cover-their-work: " + "; ".join(faults))
    for name in names:
        bound, ceiling, declared = required[name]
        print("  bound {:38s} {}s (ceiling {}s x {}; declared wait {}s + {}s)".format(
            name, bound, ceiling, _LOAD_HEADROOM, declared, _LAUNCH_MARGIN_S))
    print("PASS closure/leg-bounds-cover-their-work")
    # The former flat 600 s on every leg is refused, naming the legs whose work it under-covers.
    refused = "; ".join(_bound_faults(dict((name, 600) for name in names), required))
    for name in ("opf-tooling-selftest", "opf-adopt-apply-selftest"):
        if name + ": bound 600s below" not in refused:
            raise AssertionError("closure/flat-600-not-refused: " + name)
    print("RED flat-600 -> " + refused)
    ceilings = dict(_LEG_CEILING_S)
    del ceilings["opf-adopt-apply-selftest"]
    try:
        _leg_bounds(Path(root) / "opf" / "tools", ceilings)
    except _BoundError:
        print("RED missing-ceiling -> cannot derive the bound (fail-closed)")
    else:
        raise AssertionError("closure/missing-ceiling-not-refused")
    # The declared-wait scan reads what it says it reads.
    tmp = Path(tempfile.mkdtemp(prefix="opf-closure-scan-"))
    try:
        probe = tmp / "probe.py"
        for source, want in (
                ("subprocess.run(a, timeout=1800)\n", 1800),
                ("def f(timeout=2400):\n    pass\n", 2400),
                ("def f(*, timeout=7.5):\n    pass\n", 7.5),
                ("p.communicate(timeout=120 if quick else 600)\n", 600),
                ("subprocess.run(a, timeout=limit)\nx = 9999\n", 0),
                ("subprocess.run(a, timeout=True)\n", 0)):
            probe.write_text(source, encoding="utf-8")
            got = _declared_wait_s([probe])
            if got != want:
                raise AssertionError("closure/declared-wait-scan: {!r} -> {!r}, want {!r}".format(
                    source, got, want))
        probe.write_text("def f(:\n", encoding="utf-8")
        try:
            _declared_wait_s([probe])
        except _BoundError:
            pass
        else:
            raise AssertionError("closure/declared-wait-scan-unparseable-not-refused")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("PASS closure/declared-wait-scan")
    # The worst case (every leg killed at its bound, one after another) fits the CI job.
    text = (Path(root) / _CI_WORKFLOW).read_text(encoding="utf-8")
    limit = _ci_job_limit_s(text)
    total = sum(live.values()) + _CI_SETUP_ALLOWANCE_S
    if limit is None or total > limit:
        raise AssertionError("closure/bounds-fit-the-ci-job: {}s of bounds and setup, job limit {!r}s".format(
            total, limit))
    print("PASS closure/bounds-fit-the-ci-job: {}s of leg bounds + {}s setup = {}s <= {}s ({} job)".format(
        sum(live.values()), _CI_SETUP_ALLOWANCE_S, total, limit, _CI_JOB))
    header = "  {}:\n".format(_CI_JOB)
    tight = text.replace(header, header + "    timeout-minutes: 30\n", 1)
    if tight == text or _ci_job_limit_s(tight) != 1800 or total <= 1800:
        raise AssertionError("closure/ci-job-30-minutes-not-refused")
    if _ci_job_limit_s(text.replace(header, "  renamed-job:\n", 1)) is not None:
        raise AssertionError("closure/ci-job-absent-not-refused")
    if _ci_job_limit_s(text.replace(header, header + "    timeout-minutes: thirty\n", 1)) is not None:
        raise AssertionError("closure/ci-job-non-integer-not-refused")
    print("RED ci-job-30-minutes -> does not fit; RED ci-job-absent and non-integer -> cannot evaluate")
    return required


_TIME_SCALE = 300  # the bound vectors run the real _run_one and _report at 1/300 of real time


def _timeout_vectors_self_test(required):
    """Time-scaled stub legs through the real runner: the slowest recorded correct opf-adopt-apply run
    (672 s) is killed by the former flat 600 s bound and finishes inside its derived bound, and a genuine
    hang is still cut off at that bound and reported as the bound tripping, not as a leg failure."""
    leg = "opf-adopt-apply-selftest"
    bound = required[leg][0]
    scaled = bound / _TIME_SCALE
    slow = repr(672 / _TIME_SCALE)
    tmp = Path(tempfile.mkdtemp(prefix="opf-closure-bound-"))
    try:
        stub_root = tmp / "opf"
        os.makedirs(stub_root / "tools")
        (stub_root / "tools" / "stub_sleep.py").write_text(
            "import sys, time\ntime.sleep(float(sys.argv[1]))\n", encoding="utf-8")
        env = _isolated_env()
        rc, _tail_text, _elapsed = _run_one(stub_root, "stub_sleep.py", [slow], tmp, env, 600 / _TIME_SCALE)
        if rc is not None:
            raise AssertionError("closure/flat-600-did-not-kill-the-672s-leg (rc={!r})".format(rc))
        print("RED flat-600 -> the recorded 672 s {} run is killed (time-scaled 1:{})".format(leg, _TIME_SCALE))
        rc, _tail_text, _elapsed = _run_one(stub_root, "stub_sleep.py", [slow], tmp, env, scaled)
        if rc != 0:
            raise AssertionError("closure/derived-bound-killed-the-672s-leg (rc={!r})".format(rc))
        print("PASS closure/derived-bound-lets-the-672s-leg-finish ({}s bound)".format(bound))
        rc, tail, elapsed = _run_one(stub_root, "stub_sleep.py", ["3600"], tmp, env, scaled)
        if rc is not None or not scaled <= elapsed < scaled + 30:
            raise AssertionError("closure/hang-not-cut-off (rc={!r}, {:.1f}s)".format(rc, elapsed))
        for rows, want_code, want, refused in (
                ([(leg, None, tail, elapsed, bound)], 2,
                 ("TIMED OUT (killed at its {}s bound)".format(bound), "LEG BOUND EXCEEDED",
                  "{}: killed at its {}s bound".format(leg, bound), "not a failure the leg reported"),
                 ("BROKEN", "CLOSURE: OK")),
                ([(leg, None, tail, elapsed, bound), ("opf-homes-contract", 1, "boom", 1.0, 120)], 1,
                 ("BROKEN", "opf-homes-contract (rc=1): boom", "{}: killed at its {}s bound".format(leg, bound)),
                 ("CLOSURE: OK",))):
            out = io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
                code = _report(rows)
            said = out.getvalue()
            if code != want_code or not all(w in said for w in want) or any(r in said for r in refused):
                raise AssertionError("closure/timeout-report (exit {}): {!r}".format(code, said))
        print("RED hang -> cut off at its bound and reported as LEG BOUND EXCEEDED (exit 2; exit 1 beside a "
              "real failure)")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def self_test_main():
    """Prove the gate fails without the self-containment property (change-carries-check). The bound legs
    run first and fast: the per-leg bounds cover the work of each leg and fit the CI job, and time-scaled stub
    legs show the former flat bound killing a correct leg while a hang is still cut off and named. The
    positive leg runs run() over the real opf/ and requires 0. The negative leg materializes opf/ alone, then
    RE-INTRODUCES the pre-move upward edge (opf/tools/_opf_store.py importing `_parse` from AIQT
    `check_versions` instead of the extracted `_semver`); with no AIQT tree reachable that import fails, so
    the subset must exit non-zero (a leg killed at its bound does not count as caught). A gate that passed
    the deliberately-broken copy would provide no coverage."""
    root = repo_root()
    try:
        _pack_manifest_registration_self_test()
        _adopt_observe_registration_self_test()
        _import_batch_registration_self_test()
        _import_complete_registration_self_test()
        required = _bounds_self_test(root)
        _timeout_vectors_self_test(required)
    except (AssertionError, _BoundError, OSError) as exc:
        print("SELF-TEST FAIL:", str(exc), file=sys.stderr)
        return 1
    failures = []

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
            bound = required["opf-tooling-selftest"][0]
            rc, _tail_text, _elapsed = _run_one(opf_root, "opf.py", ["--self-test"], tmp, env, bound)
            if rc is None:
                failures.append("negative: the flipped opf.py --self-test was killed at its {}s bound "
                                "(the bound tripped; the flip was not evaluated)".format(bound))
            elif rc == 0:
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
