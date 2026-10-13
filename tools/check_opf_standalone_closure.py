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
import signal
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
    ("opf-adopt-hook-selftest", "_opf_adopt_hook.py", ["--self-test"]),
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
# so it must sit ABOVE the work the leg is sanctioned to do: one flat constant below a leg's real work
# kills a correctly-running leg. Each leg bound is the LARGEST of:
#   * _LOAD_HEADROOM times the slowest run of that leg recorded on this tree (_LEG_CEILING_S);
#   * the SUM of the child waits the leg declares in its own sources (numeric literals in timeout=
#     arguments and `timeout` parameter defaults, read from the copied sources on every run by
#     _declared_wait_s), plus _LAUNCH_MARGIN_S: sequential waits add up, so the sum, not the largest
#     single wait, is the leg's declared end-to-end budget, and the gate never kills a leg inside work
#     the leg itself sanctions;
#   * _MIN_LEG_BOUND_S, the floor for the legs that finish in seconds.
# The wait scan reads only literals: a wait computed at run time (opf.py sizes its watchdog case budgets
# at run time, for one) is missed, and arithmetic contributes only its literal operands (timeout=600 * 3
# reads as 600), so the scan can only UNDER-read; an under-read bound kills the leg at its bound and the
# gate exits 2, a loud refusal to evaluate, never a clean pass, and the measured-ceiling term is what
# keeps such legs runnable. The legs run one after another, so the worst case of one gate run is the SUM
# of the bounds; the self-test requires the whole opf-closure CI job's worst case (its self-test step
# plus its live gate step) to fit that job's time limit, and refuses BY NAME any single leg bound that
# exceeds the limit rather than shrinking it. A change that adds or lengthens a leg's wait updates this
# table in the same change.
# Every ceiling below is whole seconds, rounded up, from ONE source: a serial isolated run of every leg
# at this revision on 2026-10-13, under nice -n 10 on an otherwise busy 24-CPU host, with the 5-minute
# load average at the leg's start named on its row. _LOAD_HEADROOM is the allowance for a slower or more
# loaded host than that run's; the multiplier is an INFERENCE (a saturated host can slow a leg
# several-fold), not a measurement, and a leg that outgrows it trips its bound (exit 2, fail-closed),
# never passes clean.
_LEG_CEILING_S = {
    "opf-homes-selftest": 53,  # measured 2026-10-13: 52.7 s at load5 13.1
    "opf-homes-contract": 1,  # measured 2026-10-13: 0.2 s at load5 13.0
    "opf-tooling-selftest": 334,  # measured 2026-10-13: 333.5 s at load5 13.0
    "opf-drift-selftest": 2,  # measured 2026-10-13: 1.6 s at load5 9.1
    "opf-doctor-selftest": 44,  # measured 2026-10-13: 43.8 s at load5 9.1
    "opf-init-selftest": 153,  # measured 2026-10-13: 152.3 s at load5 8.8
    "opf-init-contract-validator-selftest": 1,  # measured 2026-10-13: 0.0 s at load5 8.0
    "opf-init-contract-check-selftest": 1,  # measured 2026-10-13: 0.0 s at load5 8.0
    "opf-upgrade-selftest": 60,  # measured 2026-10-13: 59.7 s at load5 8.0
    "opf-adopt-selftest": 128,  # measured 2026-10-13: 127.4 s at load5 7.5
    "opf-adopt-apply-selftest": 5,  # measured 2026-10-13: 4.6 s at load5 6.6
    "opf-adopt-hook-selftest": 1,  # measured 2026-10-13: 0.7 s at load5 6.6
    "opf-pack-manifest-selftest": 12,  # measured 2026-10-13: 11.8 s at load5 6.6
    "opf-adopt-observe-selftest": 246,  # measured 2026-10-13: 245.3 s at load5 6.6
    "opf-adopt-discover-selftest": 1,  # measured 2026-10-13: 0.3 s at load5 6.0
    "opf-prompt-pack-selftest": 1,  # measured 2026-10-13: 0.5 s at load5 6.0
    "opf-prompt-pack": 1,  # measured 2026-10-13: 0.1 s at load5 6.0
    "opf-oplock-selftest": 75,  # measured 2026-10-13: 74.2 s at load5 6.0
    "opf-init-substrate-selftest": 3,  # measured 2026-10-13: 2.6 s at load5 5.5
    "opf-init-builders-selftest": 1,  # measured 2026-10-13: 0.1 s at load5 5.5
    "opf-init-operation-selftest": 71,  # measured 2026-10-13: 70.8 s at load5 5.5
    "opf-init-p0-selftest": 24,  # measured 2026-10-13: 23.3 s at load5 5.0
    "opf-init-observe-selftest": 1,  # measured 2026-10-13: 0.7 s at load5 4.7
    "commonmark-headings-selftest": 8,  # measured 2026-10-13: 7.5 s at load5 4.7
    "commonmark-conformance": 1,  # measured 2026-10-13: 0.1 s at load5 4.7
}
_LOAD_HEADROOM = 4
_LAUNCH_MARGIN_S = 60
_MIN_LEG_BOUND_S = 120

_CI_WORKFLOW = Path(".github") / "workflows" / "quality.yml"
_CI_JOB = "opf-closure"
_CI_DEFAULT_JOB_MINUTES = 360  # the GitHub Actions limit for a hosted-runner job that sets no timeout-minutes
_CI_SETUP_ALLOWANCE_S = 600    # checkout with full history and setup-python, before the gate steps start
_VECTOR_ALLOWANCE_S = 120      # the registration, table, scan and time-scaled stub checks of the self-test step


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


def _declared_wait_sites(paths):
    """Each declared wait, in seconds, that the given sources carry, one entry per site: a `timeout=`
    keyword argument or the default of a parameter named `timeout`, counting only numeric literals (a
    conditional site counts once, at its largest literal arm). The scan is literal-only: a wait computed
    at run time contributes only the literals it contains, or nothing, so it can only UNDER-read
    (`timeout=600 * 3` reads as 600, `timeout=limit` as nothing); an under-read bound kills the leg at
    its bound and the gate exits 2, a loud refusal to evaluate, never a clean pass. Raises _BoundError
    when a source cannot be read or parsed (fail-closed)."""
    sites = []
    for path in paths:
        for node in ast.walk(_parse(path)):
            values = None
            if isinstance(node, ast.keyword) and node.arg == "timeout":
                values = _numbers(node.value)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                spec = node.args
                positional = spec.posonlyargs + spec.args
                pairs = list(zip(positional[len(positional) - len(spec.defaults):], spec.defaults))
                pairs.extend((arg, default) for arg, default in zip(spec.kwonlyargs, spec.kw_defaults)
                             if default is not None)
                values = [n for arg, default in pairs if arg.arg == "timeout"
                          for n in _numbers(default)]
            if values:
                sites.append(max(values))
    return sites


def _declared_wait_s(paths):
    """The leg's declared end-to-end wait budget, in seconds: the SUM of every declared wait site in
    the given sources (rule kill-timeout-exceeds-callee-wait: a leg's waits can run one after another,
    so the bound must sit above their sum, not only the largest single wait). 0 when none is declared.
    Summing can over-count a site that never runs, which only raises the bound; the under-read
    direction is disclosed on _declared_wait_sites and fails closed."""
    return sum(_declared_wait_sites(paths))


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
    """{leg name: (bound_s, ceiling_s, declared waits sum_s)} for every subset leg, derived from the
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


def _bound_faults(bounds, needs):
    """Each leg whose bound is missing, not a finite positive number, or below the bound its own work
    requires (needs maps each leg name to that requirement in seconds, derived independently of the
    bounds under test)."""
    faults = []
    for name, _script, _args in _SUBSET:
        have, need = bounds.get(name), needs[name]
        if type(have) not in (int, float) or not math.isfinite(have) or have <= 0:
            faults.append("{}: bound {!r} is not a finite positive number".format(name, have))
        elif have < need:
            faults.append("{}: bound {}s below the {}s its work requires".format(name, have, need))
    return faults


def _oversize_legs(bounds, limit_s):
    """Each leg whose own bound exceeds the CI job limit. Such a bound is refused BY NAME (the ceiling
    table or the leg's declared waits must change under review), never silently shrunk to fit the job."""
    return ["{}: bound {}s alone exceeds the {}s job limit".format(name, bounds[name], limit_s)
            for name, _script, _args in _SUBSET if bounds[name] > limit_s]


def _ci_job_limit_s(workflow_text, job=_CI_JOB):
    """The time limit, in seconds, of `job` in the workflow text, as (seconds, None), or (None, reason)
    naming what could not be evaluated. The limit is the smallest timeout-minutes found at job level or
    at ANY key position of any of the job's steps (including as the first key of a step's `- ` line),
    capped at _CI_DEFAULT_JOB_MINUTES, the GitHub Actions limit for a hosted-runner job that sets no
    timeout-minutes. Fail-closed: an absent or duplicated job header, a timeout-minutes value that is
    not a plain integer literal, and any other job line carrying the token timeout-minutes in a form
    this line parser does not evaluate (a quoted or flow-mapping key, an expression value) each refuse
    by name rather than returning a limit the workflow might not enforce."""
    lines = workflow_text.splitlines()
    header = "  {}:".format(job)
    if lines.count(header) != 1:
        return None, "job {} appears {} times in the workflow (need exactly one)".format(
            job, lines.count(header))
    minutes = [_CI_DEFAULT_JOB_MINUTES]
    for line in lines[lines.index(header) + 1:]:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if len(line) - len(line.lstrip(" ")) <= 2:
            break  # the next job, or a top-level key, ends this job
        key_line = stripped[2:].lstrip() if stripped.startswith("- ") else stripped
        if key_line.startswith("timeout-minutes:"):
            value = key_line[len("timeout-minutes:"):].split("#")[0].strip()
            if not re.fullmatch(r"[0-9]+", value):
                return None, ("timeout-minutes value {!r} in the {} job is not a plain integer "
                              "(cannot evaluate)".format(value, job))
            minutes.append(int(value))
        elif "timeout-minutes" in key_line:
            return None, ("unrecognized timeout-minutes form {!r} in the {} job "
                          "(cannot evaluate)".format(stripped, job))
    return 60 * min(minutes), None


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


def _tail(output, keep=3):
    return "\n".join((output or b"").decode("utf-8", "replace").splitlines()[-keep:])


def _kill_and_reap(proc):
    """Kill the timed-out leg's WHOLE process group (the leg was started as its own session leader, so
    every descendant that did not itself change session shares its group), then reap the leg and drain
    what it wrote. Disclosed residual: a descendant that called setsid() itself has left the group and
    escapes this kill; it can also keep the output pipe open, so the drain is bounded and gives up with
    what it has rather than hanging the gate."""
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except OSError:
        proc.kill()
    try:
        out, _err = proc.communicate(timeout=_LAUNCH_MARGIN_S)
    except subprocess.TimeoutExpired:
        out = b""
    return out


def _run_one(opf_root, script, args, run_dir, env, bound_s, keep=3):
    """Run one subset member isolated against the copied opf/tools/<script>, killed once it has run for
    bound_s seconds. The leg runs as its own session leader (start_new_session), so a timeout kills and
    reaps its whole process group, not only the immediate child: work a hung leg forked does not
    continue past the kill (a descendant that itself starts a new session is the disclosed residual on
    _kill_and_reap). Returns (rc, tail_of_output, elapsed_s); rc is None when the leg was killed at its
    bound (the bound tripped; the leg itself reported nothing). cwd is run_dir, which is OUTSIDE any git
    repository and does not contain the tools/ tree, so only the copied opf/tools/ is reachable to
    `python3 -I`."""
    target = opf_root / "tools" / script
    if not target.is_file():
        return 2, "missing subset script in the copy: {}".format(target), 0.0
    start = time.monotonic()
    try:
        proc = subprocess.Popen(
            [sys.executable, "-I", "-B", str(target), *args],
            cwd=str(run_dir), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    except OSError as exc:
        return 2, "could not launch the interpreter: {}".format(exc), time.monotonic() - start
    try:
        stdout, _err = proc.communicate(timeout=bound_s)
    except subprocess.TimeoutExpired:
        stdout = _kill_and_reap(proc)
        return None, _tail(stdout, keep), time.monotonic() - start
    return proc.returncode, _tail(stdout, keep), time.monotonic() - start


def _run_subset(opf_root, run_dir, bounds):
    """Run every subset member under its own bound. Returns a list of (name, rc, tail, elapsed_s, bound_s)."""
    _check_pack_manifest_registration(_SUBSET)
    _check_adopt_observe_registration(_SUBSET)
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
    """The per-leg bounds hold the rule on the real tree, fit the CI job, and every rule here is
    discriminating (each has a RED case beside its PASS). Returns the derived bounds; raises
    AssertionError naming the first broken property."""
    names = [name for name, _script, _args in _SUBSET]
    if sorted(_LEG_CEILING_S) != sorted(names):
        raise AssertionError("closure/leg-ceilings-match-the-subset")
    tools_dir = Path(root) / "opf" / "tools"
    required = _leg_bounds(tools_dir)
    live = dict((name, row[0]) for name, row in required.items())
    # The expectation is recombined here from the ceiling table and the wait scan, independently of
    # _leg_bounds, so a _leg_bounds that drops a term (the headroom, the wait sum, the floor) is a
    # named fault, not a tautology.
    needs = {}
    for name, script, _args in _SUBSET:
        sources = [tools_dir / script]
        if script == "opf.py":
            sources.extend(tools_dir / module for module in _self_test_modules(tools_dir / script))
        needs[name] = max(_MIN_LEG_BOUND_S, _LOAD_HEADROOM * _LEG_CEILING_S[name],
                          math.ceil(sum(_declared_wait_sites(sources))) + _LAUNCH_MARGIN_S)
    faults = _bound_faults(live, needs)
    if faults:
        raise AssertionError("closure/leg-bounds-cover-their-work: " + "; ".join(faults))
    for name in names:
        bound, ceiling, declared = required[name]
        print("  bound {:38s} {}s (ceiling {}s x {}; declared waits sum {}s + {}s)".format(
            name, bound, ceiling, _LOAD_HEADROOM, declared, _LAUNCH_MARGIN_S))
    print("PASS closure/leg-bounds-cover-their-work (expectation recombined from the table and the "
          "wait scan, independently of _leg_bounds)")
    # Mutant: a bounds table standing in for a broken _leg_bounds. One second below the independent
    # need is a fault naming the leg; at the need and one above are clean (each side of the have < need
    # comparison is tested at and beside its boundary).
    leg = max(needs, key=lambda name: (needs[name], name))
    for delta, expected in (
            (-1, ["{}: bound {}s below the {}s its work requires".format(
                leg, needs[leg] - 1, needs[leg])]),
            (0, []),
            (1, [])):
        mutated = dict(live)
        mutated[leg] = needs[leg] + delta
        got = _bound_faults(mutated, needs)
        if got != expected:
            raise AssertionError("closure/leg-bounds-mutant (need{:+d}): {!r}".format(delta, got))
    print("RED mutant bound (one second below the independent need of {}) -> named fault; at and one "
          "above the need -> clean".format(leg))
    # The former flat 600 s bound is refused for exactly the legs whose table-derived need exceeds it,
    # an expectation derived from the live table, never from a recorded constant.
    under = sorted(name for name in names if needs[name] > 600)
    flat_faults = _bound_faults(dict((name, 600) for name in names), needs)
    flat_named = sorted(fault.split(":", 1)[0] for fault in flat_faults)
    if flat_named != under:
        raise AssertionError("closure/flat-600-refusals-do-not-match-the-table: named {!r}, "
                             "table-derived {!r}".format(flat_named, under))
    if under:
        print("RED flat-600 -> " + "; ".join(flat_faults))
    else:
        print("PASS closure/flat-600-covers-every-leg (no table-derived need exceeds 600s)")
    ceilings = dict(_LEG_CEILING_S)
    del ceilings[leg]
    try:
        _leg_bounds(tools_dir, ceilings)
    except _BoundError:
        print("RED missing-ceiling -> cannot derive the bound (fail-closed)")
    else:
        raise AssertionError("closure/missing-ceiling-not-refused")
    # The declared-wait scan reads what it says it reads: sequential sites SUM; a conditional site
    # counts once at its largest arm; arithmetic and computed waits are under-read, never over-trusted.
    tmp = Path(tempfile.mkdtemp(prefix="opf-closure-scan-"))
    try:
        probe = tmp / "probe.py"
        for source, want in (
                ("subprocess.run(a, timeout=1800)\n", 1800),
                ("def f(timeout=2400):\n    pass\n", 2400),
                ("def f(*, timeout=7.5):\n    pass\n", 7.5),
                ("p.communicate(timeout=120 if quick else 600)\n", 600),
                ("subprocess.run(a, timeout=200)\nsubprocess.run(b, timeout=200)\n", 400),
                ("subprocess.run(a, timeout=30)\ndef f(timeout=60):\n    pass\n", 90),
                ("subprocess.run(a, timeout=600 * 3)\n", 600),
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
    print("PASS closure/declared-wait-scan (sequential sites sum; literal-only, so arithmetic and "
          "computed waits are under-read, which fails closed)")
    # The worst case of the whole opf-closure CI job must fit its time limit: the job runs the
    # --self-test step (one full subset sweep in its positive leg, a negative leg bounded by the
    # opf-tooling bound, and the fast vectors) and then the live gate step (one more subset sweep).
    text = (Path(root) / _CI_WORKFLOW).read_text(encoding="utf-8")
    limit, reason = _ci_job_limit_s(text)
    if limit is None:
        raise AssertionError("closure/ci-job-limit-cannot-evaluate: " + reason)
    oversize = _oversize_legs(live, limit)
    if oversize:
        raise AssertionError("closure/leg-bound-exceeds-the-ci-job: " + "; ".join(oversize))
    subset_sum = sum(live.values())
    total = (2 * subset_sum + required["opf-tooling-selftest"][0]
             + _VECTOR_ALLOWANCE_S + _CI_SETUP_ALLOWANCE_S)
    if total > limit:
        raise AssertionError("closure/bounds-fit-the-ci-job: {}s of worst-case job work, job limit "
                             "{}s".format(total, limit))
    print("PASS closure/bounds-fit-the-ci-job: 2 subset sweeps {}s + negative leg {}s + vectors {}s + "
          "setup {}s = {}s <= {}s ({} job)".format(
              2 * subset_sum, required["opf-tooling-selftest"][0], _VECTOR_ALLOWANCE_S,
              _CI_SETUP_ALLOWANCE_S, total, limit, _CI_JOB))
    # A single leg bound over the job limit is refused by name, never shrunk: one second over is named,
    # at and one under the limit are not (each side of its boundary tested).
    for delta, want_named in ((-1, False), (0, False), (1, True)):
        probe_bounds = dict(live)
        probe_bounds[leg] = limit + delta
        named = [entry for entry in _oversize_legs(probe_bounds, limit)
                 if entry.startswith(leg + ":")]
        if bool(named) != want_named:
            raise AssertionError("closure/oversize-leg-refusal (limit{:+d}): {!r}".format(delta, named))
    print("RED a leg bound one second over the job limit -> refused by name, never shrunk; at and one "
          "under the limit -> not oversize")
    header = "  {}:\n".format(_CI_JOB)
    tight = text.replace(header, header + "    timeout-minutes: 30\n", 1)
    if tight == text or _ci_job_limit_s(tight) != (1800, None) or total <= 1800:
        raise AssertionError("closure/ci-job-30-minutes-not-refused")
    absent_limit, absent_reason = _ci_job_limit_s(text.replace(header, "  renamed-job:\n", 1))
    if absent_limit is not None or _CI_JOB not in (absent_reason or ""):
        raise AssertionError("closure/ci-job-absent-not-refused-by-name")
    bad_limit, bad_reason = _ci_job_limit_s(
        text.replace(header, header + "    timeout-minutes: thirty\n", 1))
    if bad_limit is not None or "thirty" not in (bad_reason or ""):
        raise AssertionError("closure/ci-job-non-integer-not-refused-by-name")
    step = "      - name: OPF standalone-closure gate\n"
    reordered = text.replace(
        step, "      - timeout-minutes: 1\n        name: OPF standalone-closure gate\n", 1)
    if reordered == text or _ci_job_limit_s(reordered) != (60, None):
        raise AssertionError("closure/ci-step-timeout-as-first-key-not-found")
    quoted = text.replace(header, header + '    "timeout-minutes": 1\n', 1)
    quoted_limit, quoted_reason = _ci_job_limit_s(quoted)
    if quoted_limit is not None or "timeout-minutes" not in (quoted_reason or ""):
        raise AssertionError("closure/ci-unevaluable-timeout-form-not-refused-by-name")
    print("RED ci-job-30-minutes -> does not fit; ci-job-absent, a non-integer value and a quoted-key "
          "timeout form -> refused by name; a step whose FIRST key is timeout-minutes: 1 -> 60s limit "
          "found")
    return required


_STUB_CEILING_SLEEP_S = 2.0  # the slowest leg's recorded ceiling maps to this many real stub seconds


def _timeout_vectors_self_test(required):
    """Time-scaled stub legs through the real runner, derived from the live ceiling table (never from a
    recorded constant): a run exactly as slow as the slowest recorded ceiling finishes inside that leg's
    derived bound but is killed by a bound of half its ceiling (any flat constant below a leg's recorded
    work kills a correct run); a genuine hang is cut off at the bound and reported as the bound
    tripping, never as a leg failure; and a timed-out leg's WHOLE process group is killed, so work a
    hung leg forked does not run on past the kill."""
    leg = max(_LEG_CEILING_S, key=lambda name: (_LEG_CEILING_S[name], name))
    ceiling = _LEG_CEILING_S[leg]
    bound = required[leg][0]
    scale = ceiling / _STUB_CEILING_SLEEP_S
    slow = repr(ceiling / scale)       # the ceiling-long correct run, scaled
    undersized = (ceiling / 2) / scale  # a flat bound at half the recorded ceiling, scaled
    scaled_bound = bound / scale        # at least 2 x _STUB_CEILING_SLEEP_S, since bound >= _LOAD_HEADROOM x ceiling
    hang = repr(max(3600, 2 * bound) / scale)
    tmp = Path(tempfile.mkdtemp(prefix="opf-closure-bound-"))
    try:
        stub_root = tmp / "opf"
        os.makedirs(stub_root / "tools")
        (stub_root / "tools" / "stub_sleep.py").write_text(
            "import sys, time\ntime.sleep(float(sys.argv[1]))\n", encoding="utf-8")
        env = _isolated_env()
        rc, _tail_text, _elapsed = _run_one(stub_root, "stub_sleep.py", [slow], tmp, env, undersized)
        if rc is not None:
            raise AssertionError(
                "closure/half-ceiling-bound-did-not-kill-the-ceiling-run (rc={!r})".format(rc))
        print("RED half-ceiling flat bound -> a correct run at the recorded {}s {} ceiling is killed "
              "(time-scaled 1:{:g})".format(ceiling, leg, scale))
        rc, _tail_text, _elapsed = _run_one(stub_root, "stub_sleep.py", [slow], tmp, env, scaled_bound)
        if rc != 0:
            raise AssertionError("closure/derived-bound-killed-the-ceiling-run (rc={!r})".format(rc))
        print("PASS closure/derived-bound-lets-the-ceiling-run-finish ({}s bound over the {}s "
              "ceiling)".format(bound, ceiling))
        rc, tail, elapsed = _run_one(stub_root, "stub_sleep.py", [hang], tmp, env, scaled_bound)
        if rc is not None or not scaled_bound <= elapsed < scaled_bound + 30:
            raise AssertionError("closure/hang-not-cut-off (rc={!r}, {:.1f}s)".format(rc, elapsed))
        for rows, want_code, want, refused in (
                ([(leg, None, tail, elapsed, bound)], 2,
                 ("TIMED OUT (killed at its {}s bound)".format(bound), "LEG BOUND EXCEEDED",
                  "{}: killed at its {}s bound".format(leg, bound), "not a failure the leg reported"),
                 ("BROKEN", "CLOSURE: OK")),
                ([(leg, None, tail, elapsed, bound), ("opf-homes-contract", 1, "boom", 1.0, 120)], 1,
                 ("BROKEN", "opf-homes-contract (rc=1): boom",
                  "{}: killed at its {}s bound".format(leg, bound)),
                 ("CLOSURE: OK",))):
            out = io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
                code = _report(rows)
            said = out.getvalue()
            if code != want_code or not all(w in said for w in want) or any(r in said for r in refused):
                raise AssertionError("closure/timeout-report (exit {}): {!r}".format(code, said))
        print("RED hang -> cut off at its bound and reported as LEG BOUND EXCEEDED (exit 2; exit 1 "
              "beside a real failure)")
        # Descendant-bearing hang: the hung leg forks a child that would write a marker AFTER the kill.
        # The runner kills the leg's whole process group at the bound, so the marker never appears; a
        # runner that killed only the immediate child would let the descendant write it (red on revert).
        marker = tmp / "descendant-marker.txt"
        (stub_root / "tools" / "stub_tree.py").write_text(
            "import subprocess, sys, time\n"
            "subprocess.Popen([sys.executable, '-I', '-c',\n"
            "    'import sys, time; time.sleep(float(sys.argv[1])); '\n"
            "    'open(sys.argv[2], \"w\").write(\"leaked\")', sys.argv[1], sys.argv[2]])\n"
            "time.sleep(3600)\n", encoding="utf-8")
        start = time.monotonic()
        rc, _tail_text, elapsed = _run_one(stub_root, "stub_tree.py", ["2.5", str(marker)], tmp, env, 1.0)
        if rc is not None or not 1.0 <= elapsed < 31.0:
            raise AssertionError(
                "closure/descendant-hang-not-cut-off (rc={!r}, {:.1f}s)".format(rc, elapsed))
        time.sleep(max(0.0, start + 4.0 - time.monotonic()))
        if marker.exists():
            raise AssertionError("closure/descendant-survived-the-kill: a process the hung leg forked "
                                 "wrote its marker after the leg was killed at its bound")
        print("RED descendant-bearing hang -> the leg's process group is killed whole; the forked "
              "descendant's marker never appears")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


_INJECTED_EDGE = "check_versions"


def _negative_verdict(rc, tail, bound):
    """The negative leg's verdict: None when the re-introduced upward edge was caught, else the failure
    text. Caught requires BOTH a failure the leg itself reported (rc None is the bound tripping, rc 0 is
    a pass, and a launch error never evaluated the flip at all) AND output naming the injected upward
    import, so the rc 2 that _run_one returns for a missing script or an interpreter launch failure, or
    any unrelated failure, is a self-test failure, never silent coverage credit."""
    if rc is None:
        return ("negative: the flipped opf.py --self-test was killed at its {}s bound "
                "(the bound tripped; the flip was not evaluated)".format(bound))
    if rc == 0:
        return ("negative: a re-introduced upward edge (import check_versions) was NOT "
                "caught in isolation (the gate would give no coverage)")
    if _INJECTED_EDGE not in (tail or ""):
        return ("negative: the leg exited rc={} without naming the injected upward import {!r} (a "
                "launch error or an unrelated failure is not coverage): {}".format(
                    rc, _INJECTED_EDGE, (tail or "").replace("\n", " | ") or "(no output)"))
    return None


def _negative_verdict_self_test():
    """The negative-leg verdict is discriminating: only a leg-reported failure naming the injected
    import counts as caught; a bound kill, a clean pass, an interpreter launch error (the rc 2 branch)
    and an unrelated failure are each a self-test failure."""
    caught = _negative_verdict(2, "opf: cannot bootstrap: check_versions (cannot evaluate)", 600)
    if caught is not None:
        raise AssertionError("closure/negative-verdict-rejects-a-genuine-catch: " + caught)
    print("PASS closure/negative-verdict-accepts-a-leg-naming-the-injected-import")
    for rc, tail, label in (
            (None, "", "bound-kill"),
            (0, "SELF-TEST PASS", "clean-pass"),
            (2, "could not launch the interpreter: [Errno 2] No such file or directory",
             "launch-error"),
            (1, "SELF-TEST FAIL: unrelated", "unrelated-failure")):
        if _negative_verdict(rc, tail, 600) is None:
            raise AssertionError("closure/negative-verdict-not-red: {} (rc={!r})".format(label, rc))
    print("RED negative-leg bound-kill, clean-pass, launch-error and unrelated-failure -> each is a "
          "self-test failure, never coverage credit")


def self_test_main():
    """Prove the gate fails without the self-containment property (change-carries-check). The bound and
    verdict legs run first and fast: the per-leg bounds cover each leg's table-derived need and fit the
    CI job, time-scaled stub legs show a flat bound below the recorded work killing a correct leg while
    a hang (descendants included) is still cut off and named, and the negative-leg verdict is shown
    discriminating. The positive leg runs run() over the real opf/ and requires 0. The negative leg
    materializes opf/ alone, then RE-INTRODUCES the pre-move upward edge (opf/tools/_opf_store.py
    importing `_parse` from AIQT `check_versions` instead of the extracted `_semver`); with no AIQT tree
    reachable that import fails, so the leg must itself report a failure NAMING the injected import (a
    leg killed at its bound, a launch error, or an unrelated failure does not count as caught; see
    _negative_verdict). A gate that passed the deliberately-broken copy would provide no coverage."""
    root = repo_root()
    try:
        _pack_manifest_registration_self_test()
        _adopt_observe_registration_self_test()
        _negative_verdict_self_test()
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
            rc, tail, _elapsed = _run_one(opf_root, "opf.py", ["--self-test"], tmp, env, bound,
                                          keep=40)
            verdict = _negative_verdict(rc, tail, bound)
            if verdict is not None:
                failures.append(verdict)
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
