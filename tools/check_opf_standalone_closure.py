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
     a per-leg bound that cannot be derived, a leg killed at its wall-clock bound (named as the bound
     tripping, never as a failure the leg reported; see _LEG_CEILING_S for how each bound is derived), or
     a leg ended by a signal this gate did not send (named by its signal: another process, the OOM killer
     or an interpreter crash, never a failure the leg reported), with any cleanup after a kill that did
     not complete named beside it (CLEANUP INCOMPLETE); a real finding beside any of these still exits 1
One bound is DELIBERATELY SHORTER than the work its leg is sanctioned to do: opf-tooling-selftest's,
below the budget opf.py grants its own blocked-isolation leg, because a bound above that budget would not
fit the CI job (see _LEG_CEILING_S); its trip is reported as the bound tripping (exit 2), never as a
failure the leg reported. A run under load heavier than the recorded ceilings' can trip a healthy leg's
bound and exit 2 (see _LOAD_HEADROOM).
--self-test follows the same convention: 1 only for a definite failure (the real opf/ does not verify,
the flipped copy is NOT caught, or a self-test vector fails), and 2 when a leg or the flip could not be
evaluated (a bound kill, a signal, a harness failure, a bound that cannot be derived, or any flip
outcome other than the exact failure the flipped import produces; see _negative_verdict), each named.
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
    ("opf-import-batch-selftest", "_opf_import_batch.py", ["--self-test"]),
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
# kills a correctly-running leg. ONE bound is a deliberate exception, opf-tooling-selftest's (see
# DELIBERATE SHORTER DEADLINE below). Each leg bound is the LARGEST of:
#   * _LOAD_HEADROOM times the slowest run of that leg recorded on this tree (_LEG_CEILING_S);
#   * a DELIBERATE DEADLINE derived from the leg's own sources: the SUM of the literal wait SITES they
#     declare (numeric literals in timeout= arguments and `timeout` parameter defaults, read from the
#     copied sources on every run by _declared_wait_s), plus _LAUNCH_MARGIN_S. This sum is NOT the
#     leg's end-to-end budget: a site that runs repeatedly (a helper called many times, a loop body)
#     counts ONCE, so repetition under-reads the waits that actually run, and a non-literal or
#     arithmetic timeout is MIS-READ in either direction (timeout=600 * 3 and timeout=600 / 3 both
#     read as 600; timeout=limit reads as nothing). A deadline that under-reads a leg's real
#     sequential waits kills the leg at its bound and the gate exits 2, a loud refusal to evaluate,
#     never a clean pass; the measured-ceiling term is what keeps such legs runnable.
#   * _MIN_LEG_BOUND_S, the floor for the legs that finish in seconds (see below).
# The legs run one after another, so the worst case of one gate run is the SUM of the bounds plus one
# bounded post-kill cleanup deadline per leg (_DRAIN_MARGIN_S, which the output drain and the leader
# reap SHARE, paid only by a leg killed at its bound); the self-test requires the whole opf-closure CI
# job's worst case (_job_worst_case_s: its self-test step plus its live gate step, cleanup deadlines
# included) to fit that job's time limit, and refuses BY NAME any single leg bound that exceeds the
# limit rather than shrinking it. A change that adds or lengthens a leg's wait updates this table in
# the same change.
# Every ceiling below is whole seconds, rounded up, from ONE source: a serial isolated run of every leg
# started 2026-10-15T08:38Z and finished 2026-10-15T09:07Z, at the merge of revision
# da4b403ab55892cdbac837ba0c51e092ab971ffd into this change, whose opf/ subtree (git tree
# 7bf8107f4c6d23f5c89298ef121af566f5e48614) is byte-identical to that revision's and to this tree's
# (the change carrying this table touches no file under opf/). Each leg ran as the gate runs it
# (python3 -I -B against a copy of opf/ alone), plain (no nice), one leg at a time, on a shared 24-CPU
# host, with the 5-minute load average at the leg's start on its row (13.7 to 24.6). A later refresh
# that changes opf/ CARRIES THESE FIGURES FORWARD AS INFERRED, not measured, until the legs are
# re-measured at the refreshed tree.
# _LOAD_HEADROOM is the allowance for a slower or more loaded host than that run's. The multiplier is
# an INFERENCE, not a measurement, and no observation supports it (one measured run contradicts it, see
# below): the host carried other work during the run, but a host loaded beyond it adds contention these
# ceilings do not hold, and such contention does not slow every leg alike. A leg that outgrows its bound
# trips it (exit 2, fail-closed), never passes clean. Two larger figures bear on it. Both were REPORTED
# during work on another change and measured at that change's tree (another branch; no revision, date
# or host was recorded). Both are UNVERIFIED: neither has been reproduced, and neither checks the multiplier.
#   * opf.py --self-test took 1348.7 s on a loaded host, 2.78 times this table's 485.6 s. Were this
#     tree's run that slow, 3 x 486 = 1458 s would cover it by 109.3 s.
#   * adopt-apply's self-test took 812.8 s inside a loaded opf.py --self-test run (opf.py runs the
#     same _opf_adopt_apply.self_test in-process), about 99 times this table's 8.2 s. Were the
#     standalone opf-adopt-apply-selftest leg that slow, slowdown under load would be far from uniform,
#     and the leg would trip its 60 s bound: exit 2, a loud refusal to evaluate, never a false pass.
# MEASURED against the second figure on 2026-10-15, at this table's opf/ tree, plain, on a shared
# 24-CPU host, each run against a copy of opf/ alone as the gate runs it: inside one opf.py
# --self-test run (446.6 s end to end, exit 0, load5 about 25) adopt-apply's self-test took 7.4 s,
# and four standalone runs of it, two just before and two just after, took 7.7 to 7.9 s (load5 24.2 to
# 25.1). That is one run at one load: it shows no in-process slowdown here, and says nothing about the
# heavier load or the host behind the reported figures.
# The multiplier is capped by the job fit (_job_worst_case_s against the 21600 s default job limit):
# on this table the worst case is 20576 s at 3 and 22772 s at 4, and the largest multiplier that fits
# is 3.474.
# The multiplier does NOT cover the one loaded run measured against it, and cannot. One --self-test
# run on 2026-10-15, at the revision carrying this table, at load5 about 50 (between 42 and 61 while it
# ran), passed (exit 0) with three legs past their ceilings by unequal factors: opf-doctor-selftest
# took 299.3 s, 4.34 times its 69 s ceiling; opf-init-selftest 299.6 s, 1.31 times its 228 s;
# opf-pack-manifest-selftest 28.7 s, 1.69 times its 17 s. opf-doctor-selftest finished inside its
# 1140 s bound only because its declared-wait term sets that bound (3 x 69 = 207 s alone would have
# killed it). That is one run at one load, REPORTED by its account and not reproduced here. Covering it
# on the headroom term alone needs a multiplier of at least 4.337 (299.3 / 69), above 4, where the job
# worst case is already 22772 s, over the 21600 s limit. So the multiplier stays 3, and a run under such
# load CAN exit 2 on a healthy leg: each leg whose bound is the headroom term alone (on this table
# opf-homes-selftest 273 s, opf-tooling-selftest 1458 s, opf-adopt-selftest 456 s and
# opf-oplock-selftest 324 s) trips its bound once it runs more than 3 times its ceiling. That exit 2 is
# reported as the bound tripping, never as a pass and never as a finding.
# DELIBERATE SHORTER DEADLINE: opf-tooling-selftest. READ FROM SOURCE (opf/tools/opf.py, the watchdog
# regression runner's budgets), opf.py --self-test grants its blocked-isolation leg ALONE
# 31 * _deadline_case_budget(180) = 31 * (180 + 5 + 1) = 5766 s (31 isolation cases, each its 180 s
# timeout, the 5 s fixture cleanup grace and the 1 s receipt allowance), so the whole self-test grants
# itself more; this leg's bound (1458 s on this table, 3 x its 486 s ceiling) sits far below that. A
# bound above it, 5766 + 60 = 5826 s, counts in both sweeps and in the negative leg: 3 x (5826 - 1458)
# = 13104 s more, a job worst case of 33680 s, over the 21600 s limit. So this bound takes the
# kill-timeout rule's exception (AGENTS.md): a deliberately shorter deadline, above the leg's recorded
# run and cutting a longer run short on purpose, with reaching it attributed to the deadline (LEG BOUND
# EXCEEDED, exit 2; the self-test's negative leg, under the same bound, reports its trip as cannot
# evaluate, exit 2), never as a failure the leg reported and never as a pass.
_LEG_CEILING_S = {
    "opf-homes-selftest": 91,  # measured: 90.1 s at load5 13.7
    "opf-homes-contract": 1,  # measured: 0.4 s at load5 13.9
    "opf-tooling-selftest": 486,  # measured: 485.6 s at load5 13.9
    "opf-drift-selftest": 3,  # measured: 2.7 s at load5 15.9
    "opf-doctor-selftest": 69,  # measured: 68.5 s at load5 15.9
    "opf-init-selftest": 228,  # measured: 227.4 s at load5 16.3
    "opf-init-contract-validator-selftest": 1,  # measured: 0.1 s at load5 17.6
    "opf-init-contract-check-selftest": 1,  # measured: 0.1 s at load5 17.6
    "opf-upgrade-selftest": 96,  # measured: 95.4 s at load5 17.6
    "opf-adopt-selftest": 152,  # measured: 151.1 s at load5 18.6
    "opf-adopt-apply-selftest": 9,  # measured: 8.2 s at load5 21.5
    "opf-adopt-hook-selftest": 1,  # measured: 1.0 s at load5 21.6
    "opf-import-batch-selftest": 1,  # measured: 0.7 s at load5 21.6
    "opf-pack-manifest-selftest": 17,  # measured: 16.8 s at load5 21.7
    "opf-adopt-observe-selftest": 284,  # measured: 283.7 s at load5 21.9
    "opf-adopt-discover-selftest": 1,  # measured: 0.5 s at load5 23.9
    "opf-prompt-pack-selftest": 1,  # measured: 0.8 s at load5 23.9
    "opf-prompt-pack": 1,  # measured: 0.2 s at load5 23.9
    "opf-oplock-selftest": 108,  # measured: 107.4 s at load5 23.9
    "opf-init-substrate-selftest": 4,  # measured: 3.4 s at load5 24.5
    "opf-init-builders-selftest": 1,  # measured: 0.3 s at load5 24.6
    "opf-init-operation-selftest": 100,  # measured: 99.2 s at load5 24.6
    "opf-init-p0-selftest": 40,  # measured: 39.8 s at load5 24.3
    "opf-init-observe-selftest": 2,  # measured: 1.1 s at load5 24.3
    "commonmark-headings-selftest": 13,  # measured: 12.2 s at load5 24.3
    "commonmark-conformance": 1,  # measured: 0.2 s at load5 24.2
}
_LOAD_HEADROOM = 3
_LAUNCH_MARGIN_S = 60
# The post-kill cleanup deadline (_kill_and_reap): ONE absolute deadline that the output drain and the
# leader reap share, so a leg killed at its bound pays at most this much cleanup. A descendant that left
# the leg's process group can hold the output pipe open longer; the drain then returns with the partial
# output it collected and the cleanup is reported incomplete by name, never hangs the gate.
_DRAIN_MARGIN_S = 10
# The floor equals _LAUNCH_MARGIN_S, which the deliberate-deadline term already reaches for every leg, so
# it adds nothing while the two stay equal; it keeps the lower limit explicit should the launch margin
# change. The self-test discriminates it all the same: it passes _leg_bounds a launch margin one second
# below the floor and requires every leg whose other terms then sit below the floor to be raised to
# exactly the floor. At the former 120 s floor this table's job worst case is 22236 s at a headroom of
# 3, over the job limit, and no single multiplier both covers the reported 1348.7 s figure above (that
# needs at least 2.775: 1348.7 / 486 = 2.7751) and fits the job (that allows at most 2.705).
_MIN_LEG_BOUND_S = 60

_CI_WORKFLOW = Path(".github") / "workflows" / "quality.yml"
_CI_JOB = "opf-closure"
_CI_DEFAULT_JOB_MINUTES = 360  # the GitHub Actions limit for a hosted-runner job that sets no timeout-minutes
_CI_SETUP_ALLOWANCE_S = 600    # checkout with full history and setup-python, before the gate steps start
# _VECTOR_ALLOWANCE_S is the job's work that no leg bound covers: the self-test step's registration,
# verdict, table, floor, scan, CI-read and time-scaled stub checks, the cleanup doubles, the kill vectors
# (termination and its marker window, leader reap, partial output, the runner's cleanup report) and
# their waits for the escapee, plus the opf/ copy and bound scan of each sweep (the self-test's positive
# and negative legs and the live gate step). It is sized in two parts:
#   * the deadlines those vectors hold on a passing run, READ FROM SOURCE, sum to 269 s: the half-ceiling
#     kill 11 s, the scaled run 6 s, the hang under 36 s, termination 52 s, the two escape runs 104 s,
#     the runner's cleanup report 57 s and the cleanup doubles 3 s;
#   * the work no deadline bounds (the bound checks and wait scans, and each sweep's copy and bound
#     scan) MEASURED at most 12.8 s in three runs on 2026-10-15 (plain, load5 about 23 to 26 on a
#     shared 24-CPU host; the whole vector phase took 43.7 to 44.9 s), given the legs' 3x headroom:
#     38.4 s.
# 269 + 38.4 = 307.4 s, rounded up to 310 s.
_VECTOR_ALLOWANCE_S = 310


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
    """Each literal wait SITE, in seconds, that the given sources carry: a `timeout=` keyword argument
    or the default of a parameter named `timeout`, counting only numeric literals (a conditional site
    counts once, at its largest literal arm). A SITE is a place in the source, never a run: a site
    executed repeatedly (a helper called many times, a loop body) still counts ONCE, so repetition
    under-reads the waits that actually run, and a non-literal or arithmetic timeout is MIS-READ in
    either direction (`timeout=600 * 3` and `timeout=600 / 3` both read as 600, below and above their
    real values; `timeout=limit` reads as nothing). The figures feed a deliberate deadline, never the
    leg's end-to-end budget; a deadline that under-reads kills the leg at its bound and the gate exits
    2, a loud refusal to evaluate, never a clean pass. Raises _BoundError when a source cannot be read
    or parsed (fail-closed)."""
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
    """The SUM of every literal wait site in the given sources, in seconds: the deliberate-deadline
    term of the leg bound (rule kill-timeout-exceeds-callee-wait: sequential waits add up, so the
    deadline sits above the sum of the sites, never only the largest single site). 0 when none is
    declared. This sum is NOT the leg's end-to-end budget: it can over-count a site that never runs
    (which only raises the deadline) and under-count a site that runs repeatedly or a mis-read
    arithmetic timeout (disclosed on _declared_wait_sites); under-reading fails closed."""
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


def _leg_bounds(tools_dir, ceilings=None, launch_margin_s=None):
    """{leg name: (bound_s, ceiling_s, declared waits sum_s)} for every subset leg, derived from the
    sources under tools_dir. Raises _BoundError (fail-closed) for a leg with no ceiling. ceilings and
    launch_margin_s default to _LEG_CEILING_S and _LAUNCH_MARGIN_S (the self-test passes a table
    missing a row, and a launch margin below the floor so that the floor term must raise a bound)."""
    ceilings = _LEG_CEILING_S if ceilings is None else ceilings
    margin = _LAUNCH_MARGIN_S if launch_margin_s is None else launch_margin_s
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
        bound = max(_MIN_LEG_BOUND_S, _LOAD_HEADROOM * ceiling, math.ceil(declared) + margin)
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


def _job_worst_case_s(bounds, negative_bound_s):
    """The opf-closure CI job's worst case in seconds: two subset sweeps (the self-test's positive leg
    and the live gate step), the self-test's negative leg, ONE cleanup deadline (_DRAIN_MARGIN_S, the
    drain and the leader reap sharing it) for every leg that can be killed (each leg of both sweeps and
    the negative leg), the work outside every leg bound (_VECTOR_ALLOWANCE_S: the self-test's vectors
    and each sweep's copy and bound scan), and the job's setup."""
    kills = 2 * len(_SUBSET) + 1
    return (2 * sum(bounds[name] for name, _script, _args in _SUBSET) + negative_bound_s
            + kills * _DRAIN_MARGIN_S + _VECTOR_ALLOWANCE_S + _CI_SETUP_ALLOWANCE_S)


def _fit_fault(bounds, negative_bound_s, limit_s):
    """None when the job's worst case fits limit_s, else the refusal naming every term of it."""
    total = _job_worst_case_s(bounds, negative_bound_s)
    if total <= limit_s:
        return None
    return ("{}s of worst-case job work (2 x subset sweep {}s, negative leg {}s, {} cleanup deadlines of "
            "{}s, vectors {}s, setup {}s) exceeds the {}s job limit".format(
                total, sum(bounds[name] for name, _script, _args in _SUBSET), negative_bound_s,
                2 * len(_SUBSET) + 1, _DRAIN_MARGIN_S, _VECTOR_ALLOWANCE_S, _CI_SETUP_ALLOWANCE_S,
                limit_s))


_CI_PLAIN_KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]*:(?=\s|$)")
_CI_PLAIN_ITEM = re.compile(r"[A-Za-z0-9._/ -]+")
_CI_BLOCK_HEADER = re.compile(r"([|>][0-9+-]*)(?:\s+#.*)?")
_CI_QUOTED_VALUE = re.compile(r"""('(?:[^']|'')*'|"(?:[^"\\]|\\.)*")(?:\s+#.*)?""")


def _ci_value(rest):
    """The form of the text after a plain key's colon, as (form, value): ("nested", "") for an empty or
    comment-only value (nested lines follow, and are scanned in turn), ("block", header) for a
    block-scalar header, ("quoted", text) for a single-line quoted scalar, ("plain", text) for a
    single-line plain scalar (a comment begins only at whitespace then `#`, so `30#x` is the scalar
    `30#x`), and (None, text) for any other value: an alias, anchor or tag, a flow collection, a
    multi-line or unterminated quoted scalar, or a plain scalar carrying a mapping colon introduces
    structure this line parser does not read."""
    text = rest.strip()
    if not text or text.startswith("#"):
        return "nested", ""
    block = _CI_BLOCK_HEADER.fullmatch(text)
    if block:
        return "block", block.group(1)
    quoted = _CI_QUOTED_VALUE.fullmatch(text)
    if quoted:
        return "quoted", quoted.group(1)
    scalar = re.split(r"\s+#", text, maxsplit=1)[0]
    if text[0] in "-?:,[]{}#&*!|>'\"%@`" or re.search(r":(\s|$)", scalar):
        return None, text
    return "plain", scalar


def _ci_job_limit_s(workflow_text, job=_CI_JOB):
    """The time limit, in seconds, of `job` in the workflow text, as (seconds, None), or (None, reason)
    naming what could not be evaluated. The limit is the JOB-LEVEL timeout-minutes (a key at the job
    mapping's own indentation), capped at _CI_DEFAULT_JOB_MINUTES, the GitHub Actions limit for a
    hosted-runner job that sets none. Job and step limits are DISTINCT: a step's own timeout-minutes is
    parsed and validated but does not cap the job budget (it kills that one step, a loud step failure,
    without shrinking the time the gate steps get). Fail-closed: before ANY limit (the default
    included) is returned, every line of the job must be one this line parser evaluates: a plain-key
    line (`KEY:` or `- KEY:`, the key a plain unquoted scalar with the colon directly after it) whose
    value is a form _ci_value reads (empty, a block-scalar header, or a single-line quoted or plain
    scalar), an UNQUOTED plain-scalar list item (no quote, colon or `#`), a comment or blank line, or
    the content of a block scalar that a plain-key line opened. A quoted, escaped, tagged, anchored or
    aliased key or step, a quoted list item, a value that is an alias, anchor, tag or flow collection
    (`steps: *closure_steps`, `steps: [{run: echo ok, timeout-minutes: 1}]`), and any other line shape
    are refused BY NAME, never scanned around: the YAML loader GitHub runs would evaluate them where
    this parser would not, so no limit the job might carry there can be trusted. A timeout-minutes
    value is validated BEFORE any block-scalar skip, so `timeout-minutes: |` is refused by name, never
    skipped as content; it must be a plain decimal integer without a leading zero (`030` reads as 24
    to a YAML 1.1 loader and as 30 to a YAML 1.2 one, and `30#x` is the string `30#x`, never 30). An
    absent or duplicated job header also refuses by name."""
    lines = workflow_text.splitlines()
    header = "  {}:".format(job)
    if lines.count(header) != 1:
        return None, "job {} appears {} times in the workflow (need exactly one)".format(
            job, lines.count(header))
    minutes = [_CI_DEFAULT_JOB_MINUTES]
    job_indent = None    # the indentation of the job mapping's own keys, from its first key line
    block_column = None  # the key column of an open block scalar; deeper lines are its content
    for line in lines[lines.index(header) + 1:]:
        stripped = line.strip()
        indent = len(line) - len(line.lstrip(" "))
        if block_column is not None:
            if not stripped or indent > block_column:
                continue  # block-scalar content, or a blank line inside it
            block_column = None
        if not stripped or stripped.startswith("#"):
            continue
        if indent <= 2:
            break  # the next job, or a top-level key, ends this job
        item, dashes = stripped, 0
        while item.startswith("- "):
            dashes += 1
            item = item[2:].lstrip()
        match = _CI_PLAIN_KEY.match(item)
        if not match:
            if dashes and _CI_PLAIN_ITEM.fullmatch(item):
                continue  # an unquoted plain-scalar list item carries no key and no step mapping
            return None, ("line {!r} in the {} job is not a form this parser evaluates (a plain-key "
                          "line, an unquoted plain-scalar list item, or block-scalar content); cannot "
                          "evaluate the job limit".format(stripped, job))
        if job_indent is None and dashes == 0:
            job_indent = indent
        form, value = _ci_value(item[match.end():])
        if match.group(0)[:-1] == "timeout-minutes":
            if form != "plain" or not re.fullmatch(r"0|[1-9][0-9]*", value):
                return None, ("timeout-minutes value {!r} in the {} job is not a plain decimal "
                              "integer without a leading zero (cannot evaluate)".format(
                                  value, job))
            if dashes == 0 and indent == job_indent:
                minutes.append(int(value))
            continue
        if form is None:
            return None, ("value {!r} of key {!r} in the {} job is not a form this parser evaluates "
                          "(an alias, anchor, tag or flow collection introduces structure it does not "
                          "read); cannot evaluate the job limit".format(
                              value, match.group(0)[:-1], job))
        if form == "block":
            block_column = indent + 2 * dashes
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


def _signal_name(rc):
    """The signal that ended a leg whose return code is rc (subprocess reports signal N as -N), by name
    and number, or by number alone when this platform does not name it."""
    try:
        return "{} (signal {})".format(signal.Signals(-rc).name, -rc)
    except ValueError:
        return "signal {}".format(-rc)


def _kill_and_reap(proc, drain_s=_DRAIN_MARGIN_S, killpg=os.killpg):
    """Kill the timed-out leg's process GROUP (the leg was started as its own session leader, so every
    descendant that has not itself left the group shares it), then drain its output and reap its
    LEADER under ONE absolute deadline of drain_s seconds that the two waits SHARE (the reap gets only
    what the drain left, so the whole cleanup costs at most drain_s, the one cleanup term the fit model
    counts per killed leg), and close the output pipe this runner owns in a finally, on EVERY path (a
    normal drain, a drain timeout, an OSError from the kill, the drain or the reap). Returns (output,
    faults): output is whatever the drain collected (TimeoutExpired carries the partial output; b""
    when the drain failed), and faults names each cleanup step that did not complete:
    `group-kill-error` for a group kill that failed while the leader kill was delivered (descendants
    in the group may survive), `kill-error` when neither kill was delivered, `drain-timeout`,
    `drain-error`, `reap-error` for an OSError from the reap, and `leader-unreaped` for a leader still
    unreaped at the deadline (never suppressed, never claimed reaped). Disclosed residual: a descendant
    that called setpgid() or setsid() has LEFT the group; it is neither killed nor reaped here, and it
    can hold the output pipe open past the kill, which is why the drain is bounded (a `drain-timeout`
    fault), never a wait on the whole descendant tree. killpg is os.killpg; the self-test's process
    doubles replace it with a recorder, so a double is never signalled."""
    deadline = time.monotonic() + drain_s
    out, faults = b"", []
    try:
        try:
            killpg(proc.pid, signal.SIGKILL)
        except OSError as group_exc:
            try:
                proc.kill()
            except OSError as exc:
                faults.append("kill-error: neither the group kill nor the leader kill was "
                              "delivered: {}".format(exc))
            else:
                # The leader kill reaches the leader alone: the rest of the group was not signalled.
                faults.append("group-kill-error: the process-group kill failed: {}; descendants "
                              "may survive".format(group_exc))
        try:
            out, _err = proc.communicate(timeout=max(0.0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired as exc:
            out = exc.stdout or b""
            faults.append("drain-timeout: the output pipe was still held open at the {}s cleanup "
                          "deadline (a descendant that left the group holds it)".format(drain_s))
        except OSError as exc:
            faults.append("drain-error: the output drain failed: {}".format(exc))
        if proc.returncode is None:
            try:
                proc.wait(timeout=max(0.0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                pass  # the leader is still unreaped: named below, never suppressed
            except OSError as exc:
                faults.append("reap-error: the leader reap failed: {}".format(exc))
    finally:
        if proc.stdout is not None:
            proc.stdout.close()
    if proc.returncode is None:
        faults.append("leader-unreaped: leader pid {} was still unreaped at the {}s cleanup "
                      "deadline".format(proc.pid, drain_s))
    return out, faults


def _run_one(opf_root, script, args, run_dir, env, bound_s, keep=3, drain_s=_DRAIN_MARGIN_S):
    """Run one subset member isolated against the copied opf/tools/<script>, killed once it has run for
    bound_s seconds. The leg runs as its own session leader (start_new_session), so a timeout kills its
    whole process GROUP and reaps the LEADER: work a hung leg forked does not continue past the kill
    unless the descendant itself left the group with setpgid() or setsid(), the disclosed residual on
    _kill_and_reap (such a survivor is neither killed nor reaped). Returns
    (rc, tail_of_output, elapsed_s, launched, cleanup): rc is None when the leg was killed at its bound
    (the bound tripped; the leg itself reported nothing), a negative rc is a signal this runner did not
    send (-N for signal N; the leg reported nothing either), launched is False for a harness failure (a
    missing script, an interpreter that could not start) whose rc 2 is this runner's verdict, NEVER an
    outcome the leg reported, whatever text it carries, and cleanup lists the named faults of a kill
    whose cleanup did not complete (_kill_and_reap; empty otherwise). cwd is run_dir, which is OUTSIDE
    any git repository and does not contain the tools/ tree, so only the copied opf/tools/ is
    reachable to `python3 -I`."""
    target = opf_root / "tools" / script
    if not target.is_file():
        return 2, "missing subset script in the copy: {}".format(target), 0.0, False, []
    start = time.monotonic()
    try:
        proc = subprocess.Popen(
            [sys.executable, "-I", "-B", str(target), *args],
            cwd=str(run_dir), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    except OSError as exc:
        return (2, "could not launch the interpreter: {}".format(exc), time.monotonic() - start, False,
                [])
    try:
        stdout, _err = proc.communicate(timeout=bound_s)
    except subprocess.TimeoutExpired:
        stdout, cleanup = _kill_and_reap(proc, drain_s)
        return None, _tail(stdout, keep), time.monotonic() - start, True, cleanup
    return proc.returncode, _tail(stdout, keep), time.monotonic() - start, True, []


def _run_subset(opf_root, run_dir, bounds):
    """Run every subset member under its own bound. Returns a list of
    (name, rc, tail, elapsed_s, launched, bound_s, cleanup)."""
    _check_pack_manifest_registration(_SUBSET)
    _check_adopt_observe_registration(_SUBSET)
    _check_import_batch_registration(_SUBSET)
    env = _isolated_env()
    results = []
    for name, script, args in _SUBSET:
        bound = bounds[name][0]
        rc, tail, elapsed, launched, cleanup = _run_one(opf_root, script, args, run_dir, env, bound)
        results.append((name, rc, tail, elapsed, launched, bound, cleanup))
    return results


def _report(results):
    """Print one line per leg and the verdict, and return the exit code. A leg that ITSELF exited
    non-zero (a positive status) is a finding (1). A leg killed at its bound is reported as the bound
    tripping, a leg ended by a signal this gate did not send (a negative status: another process, the
    OOM killer, or an interpreter crash) by that signal's name, and a leg that never launched (launched
    False: a missing script or an interpreter that could not start) as a harness failure, never as a
    failure the leg reported; in each case, with no finding beside it the gate cannot evaluate closure
    (2), and never reads as clean. A killed leg whose cleanup did not complete (row[6], the named faults
    from _kill_and_reap: an unreaped leader, a pipe still held by an escaped descendant) is reported as
    CLEANUP INCOMPLETE by name, and never reads as clean either."""
    failed = [row for row in results if row[4] and row[1] is not None and row[1] > 0]
    signalled = [row for row in results if row[4] and row[1] is not None and row[1] < 0]
    unlaunched = [row for row in results if not row[4]]
    timed_out = [row for row in results if row[1] is None]
    uncleaned = [row for row in results if row[6]]
    for name, rc, _tail_text, elapsed, launched, bound, _cleanup in results:
        if not launched:
            status = "NOT LAUNCHED (harness failure; the leg reported nothing)"
        elif rc == 0:
            status = "OK ({:.1f}s of its {}s bound)".format(elapsed, bound)
        elif rc is None:
            status = "TIMED OUT (killed at its {}s bound)".format(bound)
        elif rc < 0:
            status = "ENDED BY {} after {:.1f}s (not this gate's kill; the leg reported nothing)".format(
                _signal_name(rc), elapsed)
        else:
            status = "FAILED rc={} ({:.1f}s)".format(rc, elapsed)
        print("  {:38s} {}".format(name, status))
    if failed:
        print("STANDALONE CLOSURE: BROKEN (the isolated opf/ subtree does not verify itself):",
              file=sys.stderr)
        for name, rc, tail, _elapsed, _launched, _bound, _cleanup in failed:
            print("  {} (rc={}): {}".format(name, rc, tail.replace("\n", " | ")), file=sys.stderr)
    if unlaunched:
        print("STANDALONE CLOSURE: NOT EVALUATED (a leg below never launched; the failure is the "
              "harness's, never the leg's, whatever text it carries):", file=sys.stderr)
        for name, _rc, tail, _elapsed, _launched, _bound, _cleanup in unlaunched:
            print("  {}: {}".format(name, tail.replace("\n", " | ")), file=sys.stderr)
    if signalled:
        print("STANDALONE CLOSURE: NOT EVALUATED (a signal this gate did not send ended each leg below "
              "before it reported: another process, the OOM killer, or an interpreter crash; closure was "
              "not evaluated for it):", file=sys.stderr)
        for name, rc, tail, elapsed, _launched, _bound, _cleanup in signalled:
            print("  {}: ended by {} after {:.1f}s; last output: {}".format(
                name, _signal_name(rc), elapsed, tail.replace("\n", " | ") or "(none)"), file=sys.stderr)
    if timed_out:
        print("STANDALONE CLOSURE: LEG BOUND EXCEEDED (the gate killed each leg below at its wall-clock bound; "
              "the bound tripped, not a failure the leg reported, so closure was not evaluated for it):",
              file=sys.stderr)
        for name, _rc, tail, elapsed, _launched, bound, _cleanup in timed_out:
            print("  {}: killed at its {}s bound after {:.1f}s; last output: {}".format(
                name, bound, elapsed, tail.replace("\n", " | ") or "(none)"), file=sys.stderr)
    if uncleaned:
        print("STANDALONE CLOSURE: CLEANUP INCOMPLETE (the cleanup after the kill of each leg below did "
              "not complete; nothing here is claimed reaped or drained):", file=sys.stderr)
        for name, _rc, _tail_text, _elapsed, _launched, _bound, cleanup in uncleaned:
            print("  {}: {}".format(name, "; ".join(cleanup)), file=sys.stderr)
    if failed:
        return 1
    if timed_out or signalled or unlaunched or uncleaned:
        return 2
    print("STANDALONE CLOSURE: OK (opf/ verifies itself with no AIQT tree reachable)")
    return 0


def run(root, ceilings=None):
    """Materialize opf/ alone and run the subset in isolation. Returns 0 (closure holds), 1 (a subset gate
    failed: closure broken), or 2 (harness error, opf/ unreadable, a leg with no ceiling row, refused
    before any leg launches, or a leg killed at its bound or ended by a signal), fail-closed. ceilings
    defaults to _LEG_CEILING_S (the self-test passes a table missing a row)."""
    return _run_closure(root, ceilings)[0]


def _run_closure(root, ceilings=None):
    """run(), returning (exit code, results): results is the per-leg rows _report read, or None when
    the run stopped before any leg ran (the self-test's positive leg names each leg it could not
    evaluate from them)."""
    opf_src = Path(root) / "opf"
    if not (opf_src.is_dir() and (opf_src / "tools" / "opf.py").is_file()):
        print("error: opf/ subtree not found under {} (cannot evaluate closure)".format(root),
              file=sys.stderr)
        return 2, None
    tmp = Path(tempfile.mkdtemp(prefix="opf-closure-"))
    try:
        try:
            opf_root = _materialize(opf_src, tmp)
        except OSError as exc:
            print("error: could not materialize the isolated opf/ copy: {}".format(exc), file=sys.stderr)
            return 2, None
        try:
            bounds = _leg_bounds(opf_root / "tools", ceilings)
        except _BoundError as exc:
            print("error: cannot derive the per-leg bounds: {}".format(exc), file=sys.stderr)
            return 2, None
        try:
            results = _run_subset(opf_root, tmp, bounds)
        except AssertionError as exc:
            print("STANDALONE CLOSURE: FAILED:", str(exc), file=sys.stderr)
            return 1, None
        return _report(results), results
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
    # _leg_bounds, so a _leg_bounds that drops a term (the headroom, the wait sum) is a named fault,
    # not a tautology. Dropping the floor changes no LIVE bound while it equals the launch margin; the
    # lowered-margin vector below is what discriminates the floor term.
    needs, waits = {}, {}
    for name, script, _args in _SUBSET:
        sources = [tools_dir / script]
        if script == "opf.py":
            sources.extend(tools_dir / module for module in _self_test_modules(tools_dir / script))
        waits[name] = math.ceil(sum(_declared_wait_sites(sources)))
        needs[name] = max(_MIN_LEG_BOUND_S, _LOAD_HEADROOM * _LEG_CEILING_S[name],
                          waits[name] + _LAUNCH_MARGIN_S)
    faults = _bound_faults(live, needs)
    if faults:
        raise AssertionError("closure/leg-bounds-cover-their-work: " + "; ".join(faults))
    for name in names:
        bound, ceiling, declared = required[name]
        print("  bound {:38s} {}s (ceiling {}s x {}; literal wait sites sum {}s + {}s)".format(
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
    # The floor term, discriminated. The launch margin passed to _leg_bounds is LOWERED to one second
    # below _MIN_LEG_BOUND_S (the module constant untouched): every leg whose headroom and wait-sum
    # terms both sit below the floor must then be raised to exactly the floor, so a _leg_bounds that
    # dropped the floor term is a named fault here. At the floor and one second above it the wait-sum
    # term reaches the floor by itself (the max is tested at and beside its boundary). The lowered case
    # must raise at least one leg, else the vector discriminates nothing: a named fault, never a pass.
    floored = []
    for margin in (_MIN_LEG_BOUND_S - 1, _MIN_LEG_BOUND_S, _MIN_LEG_BOUND_S + 1):
        lowered = dict((name, row[0]) for name, row in
                       _leg_bounds(tools_dir, launch_margin_s=margin).items())
        others = dict((name, max(_LOAD_HEADROOM * _LEG_CEILING_S[name], waits[name] + margin))
                      for name in names)
        want = dict((name, max(_MIN_LEG_BOUND_S, others[name])) for name in names)
        raised = sorted(name for name in names if others[name] < _MIN_LEG_BOUND_S)
        floor_faults = _bound_faults(lowered, want) + [
            "{}: bound {}s, not raised to exactly the {}s floor".format(name, lowered[name],
                                                                         _MIN_LEG_BOUND_S)
            for name in raised if lowered[name] != _MIN_LEG_BOUND_S]
        if floor_faults:
            raise AssertionError("closure/floor-raises-a-bound (launch margin {}s): {}".format(
                margin, "; ".join(floor_faults)))
        if margin < _MIN_LEG_BOUND_S:
            if not raised:
                raise AssertionError("closure/floor-not-discriminated: at a launch margin of {}s no "
                                     "leg's other terms sit below the {}s floor".format(
                                         margin, _MIN_LEG_BOUND_S))
            floorless = sorted(fault.split(":", 1)[0] for fault in _bound_faults(others, want))
            if floorless != raised:
                raise AssertionError("closure/floorless-bounds-not-named: named {!r}, want {!r}".format(
                    floorless, raised))
            floored = raised
    print("RED a launch margin one second below the {}s floor -> the floor raises {} leg(s) to exactly "
          "that floor ({}), and the same bounds without the floor term are named for exactly those "
          "legs; at the floor and one second above it -> every bound covers its recombined need".format(
              _MIN_LEG_BOUND_S, len(floored), ", ".join(floored)))
    # The former flat 600 s bound is refused for exactly the legs whose table-derived need exceeds it,
    # an expectation derived from the live table, never from a recorded constant. LIMIT: this vector
    # exercises only the have-below-need comparison over the live needs (the mutant above already tests
    # that comparison at its boundary); when no need exceeds 600 the PASS arm below adds nothing.
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
    # End to end through run(): a subset leg with no ceiling row is refused BY NAME before ANY leg
    # launches. The row dropped is the LAST leg's, so a runner that checked each ceiling only as it
    # reached that leg would launch every earlier leg first. Every launch is recorded and refused here
    # (Popen raises, so nothing runs even when the refusal comes too late), and one is a named fault.
    last = _SUBSET[-1][0]
    ceilings = dict(_LEG_CEILING_S)
    del ceilings[last]
    launches = []

    def refuse_launch(argv, **_kwargs):
        launches.append(argv)
        raise OSError("launch refused by the missing-row vector")

    real_popen = subprocess.Popen
    out = io.StringIO()
    subprocess.Popen = refuse_launch
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = run(root, ceilings)
    finally:
        subprocess.Popen = real_popen
    said = out.getvalue()
    if code != 2 or "leg {} has no ceiling in _LEG_CEILING_S".format(last) not in said or launches:
        raise AssertionError("closure/missing-row-refused-by-name-before-any-launch (exit {}, {} "
                             "launch(es)): {!r}".format(code, len(launches), said))
    print("RED a subset leg with no ceiling row ({}) -> refused by name through run(), exit 2, before "
          "any leg launches".format(last))
    # The declared-wait scan reads what it says it reads: sequential sites SUM; a conditional site
    # counts once at its largest arm; a repeated site counts once; arithmetic is MIS-READ in either
    # direction (600 * 3 reads as 600, under its real 1800; 600 / 3 reads as 600, over its real 200);
    # a computed wait reads as nothing.
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
                # A SITE is a place in the source, never a run: repetition is not counted, so a loop or
                # a repeatedly-called helper under-reads the waits that actually run (the scanned
                # figure stays 200 and 300 however many times each site runs).
                ("for i in range(10):\n    subprocess.run(a, timeout=200)\n", 200),
                ("def _run(a):\n    return subprocess.run(a, timeout=300)\n"
                 "_run(x)\n_run(y)\n_run(z)\n", 300),
                # Arithmetic is mis-read in EITHER direction: every operand is a literal and the
                # largest wins, so 600 * 3 under-reads (1800 real) and 600 / 3 over-reads (200 real).
                ("subprocess.run(a, timeout=600 * 3)\n", 600),
                ("subprocess.run(a, timeout=600 / 3)\n", 600),
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
    print("PASS closure/declared-wait-scan (literal SITES sum: a repeated site counts once and "
          "arithmetic is mis-read in either direction, so the sum is a deliberate deadline, never an "
          "end-to-end budget; an under-read deadline fails closed)")
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
    # The expectation is recombined here, independently of _job_worst_case_s: every leg of both sweeps
    # and the negative leg can be killed, and each kill pays ONE cleanup deadline (_DRAIN_MARGIN_S,
    # shared by the drain and the leader reap in _kill_and_reap), so a model that drops or halves the
    # cleanup term is a named fault, not a tautology.
    subset_sum = sum(live.values())
    negative = required["opf-tooling-selftest"][0]
    kills = 2 * len(_SUBSET) + 1
    cleanup_free = 2 * subset_sum + negative + _VECTOR_ALLOWANCE_S + _CI_SETUP_ALLOWANCE_S
    want_total = cleanup_free + kills * _DRAIN_MARGIN_S
    total = _job_worst_case_s(live, negative)
    if total != want_total:
        raise AssertionError("closure/fit-model-counts-one-cleanup-deadline-per-kill: {}s, recombined "
                             "{}s".format(total, want_total))
    fault = _fit_fault(live, negative, limit)
    if fault is not None:
        raise AssertionError("closure/bounds-fit-the-ci-job: " + fault)
    print("PASS closure/bounds-fit-the-ci-job: 2 x subset sweep {}s + negative leg {}s + {} cleanup "
          "deadlines x {}s + vectors {}s + setup {}s = {}s <= {}s ({} job)".format(
              subset_sum, negative, kills, _DRAIN_MARGIN_S, _VECTOR_ALLOWANCE_S,
              _CI_SETUP_ALLOWANCE_S, total, limit, _CI_JOB))
    # The fit check's cleanup term is discriminating: a job limit that covers every bound but not the
    # cleanup deadlines is refused, as is one second below the worst case; at the worst case it fits.
    for probe_limit, fits in ((want_total, True), (want_total - 1, False), (cleanup_free, False)):
        if (_fit_fault(live, negative, probe_limit) is None) != fits:
            raise AssertionError("closure/fit-check-cleanup-term (limit {}s, worst case {}s)".format(
                probe_limit, want_total))
    print("RED a job limit covering every bound but not the {} cleanup deadlines ({}s) -> refused; one "
          "second below the {}s worst case -> refused; at it -> fits".format(
              kills, cleanup_free, want_total))
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
    # Job and step limits are DISTINCT: a step's own timeout-minutes (first key of its `- ` line or
    # not) is parsed and validated, kills only that step in CI, and never caps the budget the job
    # gives the gate steps, so a short setup-step timeout cannot make this fit check reject the job.
    default_s = 60 * _CI_DEFAULT_JOB_MINUTES
    step = "      - name: OPF standalone-closure gate\n"
    reordered = text.replace(
        step, "      - timeout-minutes: 1\n        name: OPF standalone-closure gate\n", 1)
    if reordered == text or _ci_job_limit_s(reordered) != (default_s, None):
        raise AssertionError("closure/ci-step-timeout-capped-the-job-budget (first key)")
    stepwise = text.replace(step, step + "        timeout-minutes: 1\n", 1)
    if stepwise == text or _ci_job_limit_s(stepwise) != (default_s, None):
        raise AssertionError("closure/ci-step-timeout-capped-the-job-budget")
    # The fail-closed line scan: a key or step this parser does not evaluate but the YAML loader
    # GitHub runs would (a quoted key, an escape-carrying key, an alias step) is refused by name,
    # never scanned around to the default; block-scalar CONTENT is data, not keys, and is skipped.
    quoted = text.replace(header, header + '    "timeout-minutes": 1\n', 1)
    quoted_limit, quoted_reason = _ci_job_limit_s(quoted)
    if quoted_limit is not None or "timeout-minutes" not in (quoted_reason or ""):
        raise AssertionError("closure/ci-quoted-timeout-key-not-refused-by-name")
    escaped = text.replace(header, header + '    "timeout\\u002dminutes": 1\n', 1)
    escaped_limit, escaped_reason = _ci_job_limit_s(escaped)
    if escaped_limit is not None or "u002dminutes" not in (escaped_reason or ""):
        raise AssertionError("closure/ci-escaped-timeout-key-not-refused-by-name")
    aliased = text.replace(step, "      - *closure_gate\n" + step, 1)
    alias_limit, alias_reason = _ci_job_limit_s(aliased)
    if aliased == text or alias_limit is not None or "*closure_gate" not in (alias_reason or ""):
        raise AssertionError("closure/ci-alias-step-not-refused-by-name")
    blocky = text.replace(
        step, "      - run: |\n          echo widen\n          \"timeout-minutes\": 1\n" + step, 1)
    if blocky == text or _ci_job_limit_s(blocky) != (default_s, None):
        raise AssertionError("closure/ci-block-scalar-content-misread-as-a-key")
    # Value forms, on a minimal job: each entry is (check, job body, the limit it must read, or None
    # with the text its refusal must name). A timeout-minutes value is validated before any
    # block-scalar skip; a value that opens structure this parser does not read (an alias, a flow
    # collection) is refused by name; a comment begins only at whitespace then `#`; a leading zero is
    # refused; a quoted list item is not a plain-scalar item, an unquoted one is; and an indentless
    # step sequence (its `- ` at the job keys' own column) keeps a step's timeout off the job budget.
    for check, body, want_limit, want_named in (
            ("ci-block-scalar-timeout-not-refused-by-name", "    timeout-minutes: |\n      1\n", None,
             "timeout-minutes value '|'"),
            ("ci-alias-steps-value-not-refused-by-name", "    steps: *closure_steps\n", None,
             "*closure_steps"),
            ("ci-flow-steps-value-not-refused-by-name",
             "    steps: [{run: echo ok, timeout-minutes: 1}]\n", None, "[{run: echo ok"),
            ("ci-hash-without-space-read-as-a-comment", "    timeout-minutes: 30#x\n", None, "30#x"),
            ("ci-leading-zero-timeout-not-refused", "    timeout-minutes: 030\n", None, "'030'"),
            ("ci-quoted-list-item-not-refused", "    needs:\n      - 'lint'\n", None, "'lint'"),
            ("ci-spaced-comment-misread", "    timeout-minutes: 30 # half an hour\n", 1800, None),
            ("ci-plain-list-item-refused", "    needs:\n      - lint\n", default_s, None),
            ("ci-indentless-step-timeout-capped-the-job-budget",
             "    steps:\n    - timeout-minutes: 1\n      run: echo ok\n", default_s, None)):
        got_limit, got_reason = _ci_job_limit_s(
            "jobs:\n  {}:\n    runs-on: ubuntu-latest\n{}".format(_CI_JOB, body))
        if want_limit is not None:
            if (got_limit, got_reason) != (want_limit, None):
                raise AssertionError("closure/{}: {!r}".format(check, (got_limit, got_reason)))
        elif got_limit is not None or want_named not in (got_reason or ""):
            raise AssertionError("closure/{}: {!r}".format(check, (got_limit, got_reason)))
    print("RED ci-job-30-minutes -> does not fit; ci-job-absent, a non-integer value, a quoted key, an "
          "escaped key, an alias step, a block-scalar timeout-minutes, an alias or flow steps value, "
          "timeout-minutes 30#x and 030, and a quoted list item -> each refused by name; a step's own "
          "timeout-minutes: 1 (first key or not, and in an indentless step sequence) -> parsed, the "
          "job budget stays {}s; `30 # comment` -> 30 minutes; an unquoted list item -> accepted; "
          "quoted text inside a block scalar -> content, not a key".format(default_s))
    return required


_STUB_CEILING_SLEEP_S = 2.0  # the slowest leg's recorded ceiling maps to this many real stub seconds


def _pid_gone(pid, deadline_s):
    """True once no process with this pid exists (or the pid was recycled to another user's process,
    so the one asked about is gone), polled for up to deadline_s seconds. Signal 0 probes existence
    without signalling anything (never a signal to a process this run did not start)."""
    deadline = time.monotonic() + deadline_s
    while True:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.1)


def _await_file(path, deadline_s):
    """The monotonic time at which path was first seen to exist, polled for up to deadline_s seconds,
    or None when it never appeared (a readiness wait under its OWN deadline)."""
    deadline = time.monotonic() + deadline_s
    while True:
        if path.is_file():
            return time.monotonic()
        if time.monotonic() >= deadline:
            return None
        time.sleep(0.1)


def _readiness_pids(path, count, check):
    """The `count` pids a fixture wrote to its readiness file. A file holding anything else (a partly
    written file included) raises AssertionError naming `check`: a named self-test failure, never a
    traceback and never cleanup credit. The fixtures write the file aside and rename it into place, so
    a reader sees it whole or not at all; this check is the backstop."""
    text = path.read_text(encoding="utf-8")
    parts = text.split()
    if len(parts) != count or not all(re.fullmatch(r"[1-9][0-9]*", part) for part in parts):
        raise AssertionError("closure/{}-readiness-malformed: {!r} (want {} pid(s))".format(
            check, text, count))
    return [int(part) for part in parts]


class _PipeDouble:
    """A stand-in for a killed leg's output pipe: counts its closes."""

    def __init__(self):
        self.closes = 0

    @property
    def closed(self):
        return self.closes > 0

    def close(self):
        self.closes += 1


class _LegDouble:
    """A stand-in for a killed leg's Popen whose kill, drain and reap outcomes are scripted. No process
    stands behind it and nothing is signalled: the group kill goes to the recorder the test passes as
    _kill_and_reap's killpg, kill() only records (or raises, scripted), and the pid is -1, which killpg
    refuses (EINVAL) rather than reads as a group, should a broken caller pass it there. kill is "ok",
    "group" (the group kill raises, the leader kill is delivered) or "both" (both raise). The drain
    spends drain_used_s seconds before its scripted outcome, so the reap's share of the one cleanup
    deadline is observable."""

    def __init__(self, drain, reap, kill="ok", drain_used_s=0.0):
        self.pid = -1
        self.args = ["leg-double"]
        self.returncode = None
        self.stdout = _PipeDouble()
        self.drain, self.reap, self.drain_used_s = drain, reap, drain_used_s
        self.group_kill_fails, self.kill_fails = kill in ("group", "both"), kill == "both"
        self.calls = []

    def kill(self):
        self.calls.append(("kill", None))
        if self.kill_fails:
            raise OSError("scripted leader-kill failure")

    def communicate(self, timeout=None):
        self.calls.append(("drain", timeout))
        time.sleep(self.drain_used_s)
        if self.drain == "timeout":
            raise subprocess.TimeoutExpired(self.args, timeout, output=b"partial before the kill\n")
        if self.drain == "oserror":
            raise OSError("scripted drain failure")
        self.returncode = -9
        return b"whole output\n", None

    def wait(self, timeout=None):
        self.calls.append(("reap", timeout))
        if self.reap == "timeout":
            raise subprocess.TimeoutExpired(self.args, timeout)
        if self.reap == "oserror":
            raise OSError("scripted reap failure")
        self.returncode = -9
        return self.returncode


def _cleanup_doubles_self_test():
    """_kill_and_reap at the unit, on process doubles (deterministic: no scheduling delay can turn a
    scripted drain timeout into a normal drain). On every path the doubles script (a normal drain, a
    drain timeout, a reap timeout, an OSError from the drain or the reap, a group kill that failed while
    the leader kill was delivered, a kill that could not be delivered at all) the pipe is closed and each
    incomplete step is NAMED (an unreaped leader as leader-unreaped, never claimed reaped; a failed group
    kill as group-kill-error with its error, even when the leader kill was delivered; a reap OSError as
    reap-error with its error, the leader still named unreaped); the leader kill is tried exactly when
    the group kill failed; the partial output is kept; and the reap gets only what the drain left of the
    ONE cleanup deadline."""
    drain_s, used_s = 1.0, 0.6
    partial = b"partial before the kill\n"
    for check, drain, reap, kill, want_faults, want_out, want_reaped in (
            ("cleanup-normal-drain", "ok", "ok", "ok", [], b"whole output\n", True),
            ("cleanup-drain-timeout", "timeout", "ok", "ok", ["drain-timeout"], partial, True),
            ("cleanup-reap-timeout", "timeout", "timeout", "ok", ["drain-timeout", "leader-unreaped"],
             partial, False),
            ("cleanup-reap-oserror", "timeout", "oserror", "ok",
             ["drain-timeout", "reap-error", "leader-unreaped"], partial, False),
            ("cleanup-drain-oserror", "oserror", "ok", "ok", ["drain-error"], b"", True),
            ("cleanup-group-kill-error", "ok", "ok", "group", ["group-kill-error"], b"whole output\n",
             True),
            ("cleanup-kill-error", "timeout", "timeout", "both",
             ["kill-error", "drain-timeout", "leader-unreaped"], partial, False)):
        double = _LegDouble(drain, reap, kill, 0.0 if drain == "ok" else used_s)
        group_kills = []

        def killpg(pid, sig, double=double, group_kills=group_kills):
            group_kills.append((pid, sig))
            if double.group_kill_fails:
                raise OSError("scripted group-kill failure")

        try:
            out, faults = _kill_and_reap(double, drain_s, killpg=killpg)
        except Exception as exc:
            raise AssertionError("closure/{}: _kill_and_reap raised {!r} instead of naming the outcome "
                                 "(pipe closed: {})".format(check, exc, double.stdout.closed))
        named = [fault.split(":", 1)[0] for fault in faults]
        reaps = [timeout for call, timeout in double.calls if call == "reap"]
        leader_kills = [call for call, _timeout in double.calls if call == "kill"]
        if (named != want_faults or out != want_out or not double.stdout.closed
                or (double.returncode is not None) != want_reaped
                or group_kills != [(double.pid, signal.SIGKILL)]
                or leader_kills != (["kill"] if double.group_kill_fails else [])
                or any(timeout > drain_s - used_s + 1e-6 for timeout in reaps)):
            raise AssertionError("closure/{}: faults {!r}, output {!r}, pipe closed {}, returncode {!r}, "
                                 "group kills {!r}, calls {!r}".format(
                                     check, faults, out, double.stdout.closed, double.returncode,
                                     group_kills, double.calls))
        if "leader-unreaped" in named and "leader pid {}".format(double.pid) not in faults[-1]:
            raise AssertionError("closure/{}: the unreaped leader is not named by pid: {!r}".format(
                check, faults))
        for name, cause in (("group-kill-error", "scripted group-kill failure"),
                            ("reap-error", "scripted reap failure")):
            if name in named and cause not in faults[named.index(name)]:
                raise AssertionError("closure/{}: {} does not carry its error: {!r}".format(
                    check, name, faults))
    print("RED cleanup doubles -> a drain timeout, a reap timeout, a drain or reap OSError, a failed "
          "group kill (the leader kill delivered) and an undelivered kill each close the pipe and name "
          "the incomplete step (an unreaped leader by pid, never claimed reaped); partial output kept; "
          "the reap waits only the {:g}s the drain left of the {:g}s cleanup deadline".format(
              drain_s - used_s, drain_s))


def _timeout_vectors_self_test(required):
    """Time-scaled stub legs through the real runner, derived from the live ceiling table (never from a
    recorded constant): a run exactly as slow as the slowest recorded ceiling finishes inside that leg's
    derived bound but is killed by a bound of half its ceiling (any flat constant below a leg's recorded
    work kills a correct run); a genuine hang is cut off at the bound and reported as the bound
    tripping, never as a leg failure. LIMIT: the stubs are time-SCALED (the slowest ceiling maps to
    _STUB_CEILING_SLEEP_S real seconds), so these vectors exercise the kill-or-finish decision around
    the scaled bound, never the real legs or their durations. Then the kill itself on real processes,
    ONE PROPERTY PER VECTOR: TERMINATION (a descendant that stayed in the leg's process group is gone
    after the kill, confirmed by its pid and by its marker staying absent past the moment it would
    have been written; readiness is established under its own deadline BEFORE the hang deadline
    starts, and no readiness is a self-test failure, never cleanup credit), LEADER REAPING (the killed
    leg's leader is reaped even when the drain times out because an escaped descendant holds the
    pipe), PARTIAL OUTPUT (that timed-out drain keeps what it collected), and the RUNNER'S CLEANUP
    REPORT (_run_one returns that drain timeout by name). The escape fixture's descendant leaves the
    group with start_new_session, the disclosed residual on _kill_and_reap: the kill does not reach
    it, and it holds the pipe until the run RELEASES it after the assertions (or its directory is
    removed, or 120 s pass), so the drain timeout is certain, never a scheduling accident, and each
    run then waits until it is gone."""
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
        rc, _tail_text, _elapsed, _launched, _cleanup = _run_one(stub_root, "stub_sleep.py", [slow],
                                                                 tmp, env, undersized)
        if rc is not None:
            raise AssertionError(
                "closure/half-ceiling-bound-did-not-kill-the-ceiling-run (rc={!r})".format(rc))
        print("RED half-ceiling flat bound -> a correct run at the recorded {}s {} ceiling is killed "
              "(time-scaled 1:{:g})".format(ceiling, leg, scale))
        rc, _tail_text, _elapsed, _launched, _cleanup = _run_one(stub_root, "stub_sleep.py", [slow],
                                                                 tmp, env, scaled_bound)
        if rc != 0:
            raise AssertionError("closure/derived-bound-killed-the-ceiling-run (rc={!r})".format(rc))
        print("PASS closure/derived-bound-lets-the-ceiling-run-finish ({}s bound over the {}s "
              "ceiling)".format(bound, ceiling))
        rc, tail, elapsed, _launched, cleanup = _run_one(stub_root, "stub_sleep.py", [hang], tmp, env,
                                                         scaled_bound)
        if rc is not None or not scaled_bound <= elapsed < scaled_bound + 30 or cleanup:
            raise AssertionError("closure/hang-not-cut-off (rc={!r}, {:.1f}s, cleanup {!r})".format(
                rc, elapsed, cleanup))
        unreaped = "leader-unreaped: leader pid 4242 was still unreaped at the 10s cleanup deadline"
        for rows, want_code, want, refused in (
                ([(leg, None, tail, elapsed, True, bound, [])], 2,
                 ("TIMED OUT (killed at its {}s bound)".format(bound), "LEG BOUND EXCEEDED",
                  "{}: killed at its {}s bound".format(leg, bound), "not a failure the leg reported"),
                 ("BROKEN", "CLOSURE: OK", "CLEANUP INCOMPLETE")),
                ([(leg, None, tail, elapsed, True, bound, []),
                  ("opf-homes-contract", 1, "boom", 1.0, True, 120, [])], 1,
                 ("BROKEN", "opf-homes-contract (rc=1): boom",
                  "{}: killed at its {}s bound".format(leg, bound)),
                 ("CLOSURE: OK",)),
                ([(leg, 2, "could not launch the interpreter: boom", 0.0, False, bound, [])], 2,
                 ("NOT LAUNCHED", "NOT EVALUATED", "never the leg's"),
                 ("BROKEN", "CLOSURE: OK", "FAILED rc=2")),
                ([(leg, None, tail, elapsed, True, bound, [unreaped])], 2,
                 ("LEG BOUND EXCEEDED", "CLEANUP INCOMPLETE", "{}: {}".format(leg, unreaped)),
                 ("BROKEN", "CLOSURE: OK")),
                # A leg ended by a signal this gate did not send (a negative status: another process,
                # the OOM killer) reported nothing: cannot evaluate, naming the signal, never BROKEN.
                ([("opf-tooling-selftest", -9, "partial", 12.0, True, bound, [])], 2,
                 ("ENDED BY SIGKILL (signal 9)", "NOT EVALUATED",
                  "opf-tooling-selftest: ended by SIGKILL (signal 9)"),
                 ("BROKEN", "CLOSURE: OK", "FAILED rc=-9")),
                ([("opf-tooling-selftest", -9, "partial", 12.0, True, bound, []),
                  ("opf-homes-contract", 1, "boom", 1.0, True, 120, [])], 1,
                 ("BROKEN", "opf-homes-contract (rc=1): boom",
                  "opf-tooling-selftest: ended by SIGKILL (signal 9)"),
                 ("CLOSURE: OK", "opf-tooling-selftest (rc=-9)"))):
            out = io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
                code = _report(rows)
            said = out.getvalue()
            if code != want_code or not all(w in said for w in want) or any(r in said for r in refused):
                raise AssertionError("closure/timeout-report (exit {}): {!r}".format(code, said))
        if _signal_name(-200) != "signal 200":
            raise AssertionError("closure/unnamed-signal-not-named-by-number: {!r}".format(
                _signal_name(-200)))
        print("RED hang -> cut off at its bound and reported as LEG BOUND EXCEEDED (exit 2; exit 1 "
              "beside a real failure; a leg that never launched -> NOT EVALUATED, exit 2, never "
              "BROKEN; a killed leg whose leader is unreaped -> CLEANUP INCOMPLETE naming it, exit 2; "
              "a leg ended by a signal this gate did not send -> NOT EVALUATED naming the signal, "
              "exit 2, never BROKEN, and exit 1 beside a real failure)")
        # A readiness file holding anything but whole pids (a partly written file included) is a
        # named self-test failure, never a traceback.
        partly = tmp / "partly-written.pid"
        for text, count in (("", 1), ("4242 ", 2), ("42x", 1)):
            partly.write_text(text, encoding="utf-8")
            try:
                _readiness_pids(partly, count, "partly-written")
            except AssertionError as exc:
                if "closure/partly-written-readiness-malformed" not in str(exc):
                    raise
            else:
                raise AssertionError("closure/partly-written-readiness-not-refused: {!r}".format(text))
        print("RED a partly written or malformed readiness file -> a named self-test failure")
        tools = stub_root / "tools"
        # TERMINATION: the hung leader forks a holder that STAYS in the leg's process group. The holder
        # writes its own pid to the readiness file (aside, then renamed into place) and writes its
        # marker holder_delay_s later. Readiness is established under its OWN 30 s deadline; only then
        # does the 2 s hang deadline start. The group kill must leave the cleanup complete and the
        # holder gone by pid, and the marker is checked until AFTER the moment the holder would have
        # written it, so the marker check can fire inside its window even if the pid check were wrong.
        # No readiness is a self-test failure (there was no descendant to clean up), never credit.
        holder_delay_s, marker_margin_s = 8.0, 4.0
        (tools / "stub_holder.py").write_text(
            "import os, sys, time\n"
            "with open(sys.argv[2] + '.tmp', 'w') as f:\n"
            "    f.write(str(os.getpid()))\n"
            "os.replace(sys.argv[2] + '.tmp', sys.argv[2])\n"
            "time.sleep(float(sys.argv[1]))\n"
            "with open(sys.argv[3], 'w') as f:\n"
            "    f.write('leaked')\n", encoding="utf-8")
        (tools / "stub_tree.py").write_text(
            "import os, subprocess, sys, time\n"
            "subprocess.Popen([sys.executable, '-I', '-B',\n"
            "                  os.path.join(os.path.dirname(os.path.abspath(__file__)), 'stub_holder.py'),\n"
            "                  *sys.argv[1:4]])\n"
            "time.sleep(3600)\n", encoding="utf-8")
        marker = tmp / "descendant-marker.txt"
        ready = tmp / "descendant-ready.txt"
        proc = subprocess.Popen(
            [sys.executable, "-I", "-B", str(tools / "stub_tree.py"), repr(holder_delay_s), str(ready),
             str(marker)],
            cwd=str(tmp), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            start_new_session=True)
        try:
            ready_at = _await_file(ready, 30.0)
            if ready_at is None:
                raise AssertionError("closure/descendant-readiness-missing: the fixture leg never "
                                     "spawned its descendant inside its own 30s readiness deadline (a "
                                     "self-test failure, never cleanup credit)")
            descendant, = _readiness_pids(ready, 1, "descendant")
            try:
                proc.communicate(timeout=2.0)
            except subprocess.TimeoutExpired:
                pass  # the hang the deadline cuts off
            else:
                raise AssertionError("closure/descendant-fixture-shape: the leader exited (rc={!r}) "
                                     "inside its 2.0s hang deadline".format(proc.returncode))
        finally:
            _out, cleanup = _kill_and_reap(proc)
        if cleanup:
            raise AssertionError("closure/descendant-kill-cleanup-incomplete: " + "; ".join(cleanup))
        if not _pid_gone(descendant, 10.0):
            raise AssertionError("closure/descendant-survived-the-kill: descendant pid {} still "
                                 "exists after the group kill".format(descendant))
        while time.monotonic() < ready_at + holder_delay_s + marker_margin_s and not marker.exists():
            time.sleep(0.1)
        if marker.exists():
            raise AssertionError("closure/descendant-survived-the-kill: the descendant wrote its "
                                 "marker after the leg was killed")
        print("RED descendant-bearing hang -> readiness confirmed under its own deadline (holder pid {}), "
              "then the 2.0s hang deadline and the group kill: cleanup complete, the holder gone, and "
              "no marker {:g}s past the moment it would have been written".format(
                  descendant, marker_margin_s))
        # LEADER REAPING and PARTIAL OUTPUT, one run each, at the unit (_kill_and_reap holds the
        # Popen, so interpreter cleanup of a dropped handle cannot reap for it), then the RUNNER'S
        # CLEANUP REPORT through _run_one: the escape fixture prints, then starts an escapee OUTSIDE
        # the leg's process group (start_new_session, the disclosed residual: the kill does not reach
        # it) that holds the output pipe until RELEASED after the assertions, so the bounded drain
        # TIMES OUT whatever the scheduling, and the returned drain-timeout fault proves that branch
        # ran. The leader must be reaped and the pipe closed anyway, and the drain must keep the
        # partial output it collected.
        (tools / "stub_escapee.py").write_text(
            "import os, sys, time\n"
            "with open(sys.argv[1] + '.tmp', 'w') as f:\n"
            "    f.write(str(os.getpid()))\n"
            "os.replace(sys.argv[1] + '.tmp', sys.argv[1])\n"
            "deadline = time.monotonic() + 120\n"
            "while (time.monotonic() < deadline and not os.path.exists(sys.argv[2])\n"
            "       and os.path.isdir(os.path.dirname(sys.argv[2]))):\n"
            "    time.sleep(0.05)\n", encoding="utf-8")
        (tools / "stub_escape.py").write_text(
            "import os, subprocess, sys, time\n"
            "sys.stdout.write('partial output before the hang\\n')\n"
            "sys.stdout.flush()\n"
            "subprocess.Popen([sys.executable, '-I', '-B',\n"
            "                  os.path.join(os.path.dirname(os.path.abspath(__file__)), 'stub_escapee.py'),\n"
            "                  sys.argv[1], sys.argv[2]], start_new_session=True)\n"
            "time.sleep(3600)\n", encoding="utf-8")
        for check in ("leader-reaped-after-a-drain-timeout", "partial-output-kept-on-a-drain-timeout"):
            pids = tmp / (check + ".pid")
            release = tmp / (check + ".release")
            proc = subprocess.Popen(
                [sys.executable, "-I", "-B", str(tools / "stub_escape.py"), str(pids), str(release)],
                cwd=str(tmp), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                start_new_session=True)
            try:
                try:
                    if _await_file(pids, 30.0) is None:
                        raise AssertionError("closure/{}-fixture-shape: no readiness inside 30s (a "
                                             "self-test failure, never credit)".format(check))
                    escapee, = _readiness_pids(pids, 1, check)
                finally:
                    out, faults = _kill_and_reap(proc, drain_s=2.0)
                if [fault.split(":", 1)[0] for fault in faults] != ["drain-timeout"]:
                    raise AssertionError("closure/{}-drain-did-not-time-out: {!r} (the branch under "
                                         "test never ran)".format(check, faults))
                if check == "leader-reaped-after-a-drain-timeout":
                    if proc.returncode is None or not proc.stdout.closed:
                        raise AssertionError("closure/leader-not-reaped-or-pipe-left-open after the "
                                             "drain (returncode={!r})".format(proc.returncode))
                elif "partial output before the hang" not in out.decode("utf-8", "replace"):
                    raise AssertionError(
                        "closure/partial-output-lost-on-a-drain-timeout: {!r}".format(out))
            finally:
                release.write_text("released", encoding="utf-8")
            if not _pid_gone(escapee, 20.0):
                raise AssertionError("closure/escape-fixture-descendant-still-running: pid {} (it "
                                     "exits once released)".format(escapee))
            print("PASS closure/{} (the drain timed out on escapee pid {}, released after the "
                  "assertions)".format(check, escapee))
        pids = tmp / "runner-cleanup-report.pid"
        release = tmp / "runner-cleanup-report.release"
        try:
            rc, tail, elapsed, _launched, cleanup = _run_one(
                stub_root, "stub_escape.py", [str(pids), str(release)], tmp, env, 5.0, drain_s=2.0)
        finally:
            release.write_text("released", encoding="utf-8")
        if (rc is not None or [fault.split(":", 1)[0] for fault in cleanup] != ["drain-timeout"]
                or "partial output before the hang" not in tail):
            raise AssertionError("closure/runner-cleanup-not-reported (rc={!r}, cleanup {!r}, tail "
                                 "{!r})".format(rc, cleanup, tail))
        if _await_file(pids, 30.0) is None:
            raise AssertionError("closure/runner-cleanup-report-fixture-shape: no readiness inside 30s")
        escapee, = _readiness_pids(pids, 1, "runner-cleanup-report")
        if not _pid_gone(escapee, 20.0):
            raise AssertionError("closure/escape-fixture-descendant-still-running: pid {} (it exits "
                                 "once released)".format(escapee))
        print("PASS closure/runner-reports-an-incomplete-cleanup (_run_one returned the drain-timeout "
              "fault by name, with the partial output, after {:.1f}s)".format(elapsed))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


_INJECTED_EDGE = "check_versions"
# The one outcome the flipped copy produces: opf.py's guarded bootstrap (_bootstrap) maps the injected
# module's ImportError to its exit 2 and this one line, before any self-test runs. Observed on
# 2026-10-15 at this change's opf/ tree: the flipped opf.py --self-test exited 2, printing exactly this
# line and nothing else.
_FLIP_EXIT = 2
_FLIP_OUTPUT = "opf: cannot bootstrap: {} (cannot evaluate)".format(_INJECTED_EDGE)


def _negative_verdict(rc, tail, bound, launched):
    """The negative leg's verdict: None when the re-introduced upward edge was caught, else (exit code,
    text). Caught requires EXACTLY the outcome the flipped import produces: the leg launched, exited
    _FLIP_EXIT, and its whole output is the one _FLIP_OUTPUT line (tail holds the output's last lines,
    so a tail equal to that one line is the whole output). A clean pass (rc 0) is the flip NOT caught,
    a definite failure (1). Every other outcome leaves the flip unevaluated (2), never coverage credit:
    launched False (the runner's STRUCTURAL harness-failure flag, rejected whatever text it carries: a
    launch error under a path that contains the token names the token without evaluating the flip), rc
    None (the bound tripped), a negative rc (a signal the runner did not send), and any other status or
    output, the token included (an unrelated failure that mentions it, another cannot-bootstrap line,
    the right line under another status or beside other output)."""
    shown = (tail or "").replace("\n", " | ") or "(no output)"
    if not launched:
        return 2, ("negative: the flipped leg never launched (a harness failure is never coverage, "
                   "whatever its text; the flip was not evaluated): {}".format(shown))
    if rc is None:
        return 2, ("negative: the flipped opf.py --self-test was killed at its {}s bound "
                   "(the bound tripped; the flip was not evaluated)".format(bound))
    if rc < 0:
        return 2, ("negative: the flipped opf.py --self-test was ended by {}, a signal this gate did "
                   "not send (the flip was not evaluated): {}".format(_signal_name(rc), shown))
    if rc == 0:
        return 1, ("negative: a re-introduced upward edge (import check_versions) was NOT "
                   "caught in isolation (the gate would give no coverage)")
    if rc != _FLIP_EXIT or tail != _FLIP_OUTPUT:
        return 2, ("negative: the leg exited rc={} with output other than the flipped import's exact "
                   "failure (exit {} and the one line {!r}), so the flip was not evaluated (a failure "
                   "that merely mentions {!r} is never coverage): {}".format(
                       rc, _FLIP_EXIT, _FLIP_OUTPUT, _INJECTED_EDGE, shown))
    return None


def _negative_verdict_self_test():
    """The negative-leg verdict is discriminating: only the flipped import's exact failure (exit
    _FLIP_EXIT, output exactly _FLIP_OUTPUT) counts as caught; a clean pass is a definite failure (1);
    a bound kill, a signal, a harness failure (launched False: a missing script or an interpreter that
    could not start), an unrelated failure, and every non-zero exit that carries the token but is not
    that failure each leave the flip unevaluated (2). The harness-failure rejection is STRUCTURAL: a
    real launch failure whose pathname contains the token, so that its text names the token, is still
    rejected."""
    caught = _negative_verdict(_FLIP_EXIT, _FLIP_OUTPUT, 600, True)
    if caught is not None:
        raise AssertionError("closure/negative-verdict-rejects-a-genuine-catch: {!r}".format(caught))
    print("PASS closure/negative-verdict-accepts-the-exact-flipped-import-failure")
    for rc, tail, launched, label, want_code in (
            (None, "", True, "bound-kill", 2),
            (0, "SELF-TEST PASS", True, "clean-pass", 1),
            (2, "could not launch the interpreter: [Errno 2] No such file or directory", False,
             "launch-error", 2),
            (2, "could not launch the interpreter: [Errno 2] No such file or directory: "
                "'/sandbox/check_versions/bin/python3'", False, "launch-error-naming-the-token", 2),
            (1, "SELF-TEST FAIL: unrelated", True, "unrelated-failure", 2),
            # A non-zero exit that carries the token but is not the flipped import's failure: an
            # unrelated failure whose text mentions it, another cannot-bootstrap line naming it, the
            # exact line under another status or beside other output, the bare import traceback (not
            # the shape the guarded bootstrap prints), and a signal after the exact line.
            (1, "unrelated assertion failed; debug path /tmp/check_versions/cache", True,
             "unrelated-failure-naming-the-token", 2),
            (2, "opf: cannot bootstrap: a helper module could not be read (PermissionError: "
                "/x/check_versions.py) (cannot evaluate)", True, "other-bootstrap-line-naming-the-token",
             2),
            (1, _FLIP_OUTPUT, True, "exact-line-under-exit-1", 2),
            (3, _FLIP_OUTPUT, True, "exact-line-under-exit-3", 2),
            (2, "SELF-TEST FAIL: opf-store\n" + _FLIP_OUTPUT, True, "exact-line-beside-other-output", 2),
            (1, "ModuleNotFoundError: No module named 'check_versions'", True,
             "bare-import-traceback", 2),
            (-9, _FLIP_OUTPUT, True, "signal-after-the-exact-line", 2)):
        verdict = _negative_verdict(rc, tail, 600, launched)
        if verdict is None or verdict[0] != want_code:
            raise AssertionError("closure/negative-verdict-not-red: {} (rc={!r}): {!r}, want exit "
                                 "{}".format(label, rc, verdict, want_code))
    signalled = _negative_verdict(-9, _FLIP_OUTPUT, 600, True)
    if "SIGKILL (signal 9)" not in signalled[1]:
        raise AssertionError("closure/negative-signal-not-named: {!r}".format(signalled))
    # REAL harness failures through _run_one, under a path that contains the token, one per
    # launched-False producer: the missing-script branch names the script's full path, and the
    # interpreter-launch branch (an EXISTING script, an interpreter path that does not exist, so Popen
    # itself raises OSError naming it) names the interpreter's; the text alone would read as a catch
    # either way, so the structural flag, not the text, must reject each.
    tmp = Path(tempfile.mkdtemp(prefix="opf-closure-neg-launch-"))
    real_interpreter = sys.executable
    try:
        token_dir = Path(tempfile.mkdtemp(prefix="check_versions-", dir=tmp))
        fake_root = token_dir / "opf"
        os.makedirs(fake_root / "tools")
        (fake_root / "tools" / "present.py").write_text("", encoding="utf-8")
        for label, script, interpreter, want_text in (
                ("missing-script", "missing.py", real_interpreter, "missing subset script"),
                ("interpreter-launch", "present.py", str(token_dir / "bin" / "python3"),
                 "could not launch the interpreter")):
            sys.executable = interpreter
            try:
                rc, tail, _elapsed, launched, _cleanup = _run_one(fake_root, script, [], tmp,
                                                                  _isolated_env(), 10)
            finally:
                sys.executable = real_interpreter
            if (launched is not False or rc != 2 or want_text not in (tail or "")
                    or _INJECTED_EDGE not in (tail or "")):
                raise AssertionError("closure/negative-launch-fixture-shape: {} (rc={!r}, launched={!r}, "
                                     "tail={!r})".format(label, rc, launched, tail))
            verdict = _negative_verdict(rc, tail, 600, launched)
            if verdict is None or verdict[0] != 2:
                raise AssertionError("closure/negative-verdict-not-red: a real {} failure whose path "
                                     "contains {!r}: {!r}".format(label, _INJECTED_EDGE, verdict))
    finally:
        sys.executable = real_interpreter
        shutil.rmtree(tmp, ignore_errors=True)
    print("RED negative-leg clean-pass -> a definite failure (exit 1); bound-kill, a signal, "
          "unrelated-failure, launch-error, every non-zero exit carrying {!r} that is not the flipped "
          "import's exact failure (exit {} and its one line), and a real missing-script failure and a "
          "real interpreter-launch failure under a path containing it -> each not evaluated (exit 2), "
          "never coverage credit".format(_INJECTED_EDGE, _FLIP_EXIT))


def _positive_verdict(code, results):
    """The positive leg's verdict from _run_closure's (exit code, results): None when closure holds
    (0), (1, text) when a leg failed in isolation (1), and (2, text) for every other code, naming each
    leg that was not evaluated (killed at its bound, ended by a signal, never launched, or its cleanup
    incomplete) or, with no results, the run refused before any leg ran (its reason printed above), so
    a bound kill reads as the bound tripping, never as the real opf/ failing to verify."""
    if code == 0:
        return None
    if code == 1:
        return 1, "positive: the real opf/ subtree did not verify in isolation (expected closure)"
    named = []
    for name, rc, _tail_text, _elapsed, launched, bound, cleanup in results or ():
        if not launched:
            named.append("{} never launched (a harness failure)".format(name))
        elif rc is None:
            named.append("{} killed at its {}s bound (the bound tripped)".format(name, bound))
        elif rc < 0:
            named.append("{} ended by {}".format(name, _signal_name(rc)))
        if cleanup:
            named.append("{} cleanup incomplete: {}".format(name, "; ".join(cleanup)))
    reason = "; ".join(named) or "refused before any leg ran (its reason is printed above)"
    return 2, ("positive: closure was not evaluated for the real opf/ (exit {}, never a closure "
               "failure): {}".format(code, reason))


def _self_test_outcome(verdicts):
    """Print each verdict that is not None ((exit code, text)) and return the self-test's exit code: 1
    when any is a definite failure, else 2 when any leg was not evaluated, else 0. A definite failure
    outranks a leg that could not be evaluated, as in _report."""
    found = [verdict for verdict in verdicts if verdict is not None]
    for code, text in found:
        print("SELF-TEST {}: {}".format("FAIL" if code == 1 else "CANNOT EVALUATE", text),
              file=sys.stderr)
    if any(code == 1 for code, _text in found):
        return 1
    return 2 if found else 0


def _closure_legs(root, required, run_closure=None, run_one=None):
    """The self-test's positive and negative legs over root's opf/, returning the self-test's exit code
    (_self_test_outcome). run_closure and run_one default to _run_closure and _run_one; the routing
    vector passes stubs (_verdict_routing_self_test), so the wiring it holds is this one."""
    run_closure = _run_closure if run_closure is None else run_closure
    run_one = _run_one if run_one is None else run_one

    # Positive: the real opf/ subtree verifies itself in isolation.
    verdicts = [_positive_verdict(*run_closure(root))]

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
            verdicts.append((2, "negative: could not locate the _semver import to flip (fixture drift; "
                                "the flip was not evaluated)"))
        else:
            store.write_text(flipped, encoding="utf-8")
            env = _isolated_env()
            bound = required["opf-tooling-selftest"][0]
            rc, tail, _elapsed, launched, cleanup = run_one(opf_root, "opf.py", ["--self-test"], tmp,
                                                            env, bound, keep=40)
            verdicts.append(_negative_verdict(rc, tail, bound, launched))
            if cleanup:
                verdicts.append((2, "negative: the cleanup after the flipped leg's kill did not "
                                    "complete: {}".format("; ".join(cleanup))))
    except OSError as exc:
        verdicts.append((2, "negative: harness error building the broken copy (the flip was not "
                            "evaluated): {}".format(exc)))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return _self_test_outcome(verdicts)


def _verdict_routing_self_test():
    """The self-test's own exit follows the gate's convention. run() exit 2 (a leg killed at its bound,
    ended by a signal, or never launched, or a run refused before any leg ran) makes the positive leg
    NOT EVALUATED, naming each such leg with its bound or signal, and the self-test exit 2, never the
    positive-failure text or exit 1; run() exit 1 is the real opf/ failing to verify (1); 0 is clean;
    a flip outcome that is not the exact failure leaves the self-test at 2; and a definite failure
    outranks a leg that could not be evaluated. The wiring is held through _closure_legs, the legs
    self_test_main runs, with both runners stubbed over a throwaway tree that holds only the import
    the flip rewrites: the copy and the flip are real, and nothing is launched."""
    killed = ("opf-doctor-selftest", None, "partial", 1140.3, True, 1140, [])
    clean = ("opf-homes-contract", 0, "", 0.4, True, 120, [])
    for code, rows, want_code, want in (
            (2, [killed, clean], 2, "opf-doctor-selftest killed at its 1140s bound"),
            (2, [("opf-tooling-selftest", -9, "", 12.0, True, 1458, []), clean], 2,
             "opf-tooling-selftest ended by SIGKILL (signal 9)"),
            (2, [("opf-homes-contract", 2, "could not launch the interpreter: boom", 0.0, False, 120,
                  [])], 2, "opf-homes-contract never launched"),
            (2, [killed[:6] + (["leader-unreaped: leader pid 4242"],)], 2,
             "opf-doctor-selftest cleanup incomplete: leader-unreaped"),
            (2, None, 2, "refused before any leg ran"),
            (1, [("opf-homes-contract", 1, "boom", 1.0, True, 120, [])], 1, "did not verify")):
        verdict = _positive_verdict(code, rows)
        if (verdict is None or verdict[0] != want_code or want not in verdict[1]
                or (want_code == 2 and "did not verify" in verdict[1])):
            raise AssertionError("closure/positive-verdict (run() exit {}): {!r}, want exit {} naming "
                                 "{!r}".format(code, verdict, want_code, want))
    if _positive_verdict(0, [clean]) is not None:
        raise AssertionError("closure/positive-verdict-rejects-a-clean-run")
    caught = (_FLIP_EXIT, _FLIP_OUTPUT, 0.1, True, [])
    flips = []

    def flip_runner(result):
        def run_one(opf_root, script, args, _run_dir, _env, bound, keep=3):
            flips.append(((opf_root / "tools" / "_opf_store.py").read_text(encoding="utf-8"), script,
                          args, bound))
            return result
        return run_one

    tmp = Path(tempfile.mkdtemp(prefix="opf-closure-routing-"))
    try:
        os.makedirs(tmp / "opf" / "tools")
        (tmp / "opf" / "tools" / "_opf_store.py").write_text("from _semver import _parse\n",
                                                             encoding="utf-8")
        for label, positive, negative, want_code, want, refused in (
                ("bound-kill", (2, [killed, clean]), caught, 2,
                 ("SELF-TEST CANNOT EVALUATE: positive", "opf-doctor-selftest killed at its 1140s bound"),
                 ("SELF-TEST FAIL", "did not verify")),
                ("refused-before-any-leg", (2, None), caught, 2, ("refused before any leg ran",),
                 ("SELF-TEST FAIL",)),
                ("unrelated-flip-failure-naming-the-token", (0, [clean]),
                 (1, "unrelated assertion failed; debug path /tmp/check_versions/cache", 0.1, True, []),
                 2, ("SELF-TEST CANNOT EVALUATE: negative",), ("SELF-TEST FAIL",)),
                ("flip-not-caught-beside-a-bound-kill", (2, [killed]), (0, "SELF-TEST PASS", 0.1, True, []),
                 1, ("SELF-TEST FAIL: negative", "SELF-TEST CANNOT EVALUATE: positive"), ()),
                ("positive-failure", (1, None), caught, 1, ("SELF-TEST FAIL: positive",), ()),
                ("clean", (0, [clean]), caught, 0, (), ("SELF-TEST",))):
            del flips[:]
            out = io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
                code = _closure_legs(tmp, {"opf-tooling-selftest": (1458, 486, 0)},
                                     run_closure=lambda _root, positive=positive: positive,
                                     run_one=flip_runner(negative))
            said = out.getvalue()
            if (code != want_code or not all(w in said for w in want) or any(r in said for r in refused)
                    or flips != [("from check_versions import _parse\n", "opf.py", ["--self-test"],
                                  1458)]):
                raise AssertionError("closure/self-test-routing: {} (exit {}, want {}; flips {!r}): "
                                     "{!r}".format(label, code, want_code, flips, said))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("RED run() exit 2 (a leg killed at its bound, a signal, a leg never launched, an incomplete "
          "cleanup, or a run refused before any leg) -> the positive leg CANNOT EVALUATE naming each "
          "leg and its bound or signal, self-test exit 2, never the positive-failure text; run() exit "
          "1 -> exit 1; an unrelated flip failure naming the token -> exit 2; the flip not caught "
          "beside a bound kill -> exit 1")


def self_test_main():
    """Prove the gate fails without the self-containment property (change-carries-check). The bound and
    verdict legs run first and fast: the per-leg bounds cover each leg's table-derived need (the floor
    term included, shown raising bounds under a launch margin lowered below it) and fit the CI job,
    time-scaled stub legs show a flat bound below the recorded work killing a correct leg while
    a hang is still cut off and named (descendants that stayed in the leg's process group included),
    the post-kill cleanup closes the pipe and names what it could not complete on every path the
    cleanup doubles script (a failed group kill, whether or not the leader kill was delivered; a drain
    timeout or OSError; a reap timeout or OSError), and the negative-leg verdict and the self-test's own
    exit routing are shown discriminating. The positive leg runs the gate over the real opf/ and
    requires 0: exit 1 is a failure, and exit 2 (a leg killed at its bound or ended by a signal, a leg
    never launched, or a run refused before any leg) is CANNOT EVALUATE naming each such leg
    (_positive_verdict). The negative leg materializes opf/ alone, then RE-INTRODUCES the pre-move
    upward edge (opf/tools/_opf_store.py importing `_parse` from AIQT `check_versions` instead of the
    extracted `_semver`); with no AIQT tree reachable that import fails, so the leg must produce
    exactly the failure the flipped import produces (a clean pass is a failure; a leg killed at its
    bound, a signal, a harness or launch failure (rejected structurally, whatever its text), and any
    other failure, one naming the injected import included, leave the flip unevaluated; see
    _negative_verdict). A gate that passed the deliberately-broken copy would provide no coverage. Exit
    1 when anything definite failed, else 2 when anything could not be evaluated (_self_test_outcome)."""
    root = repo_root()
    try:
        _pack_manifest_registration_self_test()
        _adopt_observe_registration_self_test()
        _import_batch_registration_self_test()
        _negative_verdict_self_test()
        _verdict_routing_self_test()
        _cleanup_doubles_self_test()
        required = _bounds_self_test(root)
        _timeout_vectors_self_test(required)
    except AssertionError as exc:
        print("SELF-TEST FAIL:", str(exc), file=sys.stderr)
        return 1
    except (_BoundError, OSError) as exc:
        print("SELF-TEST CANNOT EVALUATE: {} (a bound that cannot be derived or a harness error, never "
              "a self-test failure)".format(exc), file=sys.stderr)
        return 2
    code = _closure_legs(root, required)
    if code == 0:
        print("SELF-TEST PASS: standalone closure holds for opf/, and a re-introduced upward edge is "
              "caught.")
    return code


def main():
    if "--self-test" in sys.argv[1:]:
        return self_test_main()
    return run(repo_root())


if __name__ == "__main__":
    sys.exit(main())
