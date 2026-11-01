#!/usr/bin/env python3
"""OPF standalone-closure gate (OPF-SELF-CONTAIN).

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
  ... --ci-job <job>                          either of the above over that CI job's legs alone (_CI_JOB_LEGS
                                              partitions the subset between six CI jobs because the
                                              whole roster's worst case does not fit one job, nor four
                                              once every lane-member leg's bound reaches its config-lane
                                              deadline plus the launch margin, and the sixth holds a leg
                                              the other five have no room for; the flip runs in the job
                                              that holds opf-tooling-selftest, whose leg it is)

Exit convention (matches the repo's gates):
  0  the isolated opf/ subtree verifies (closure holds)
  1  a real finding: a subset gate failed in isolation (closure is broken; a leg's own exit 2 counts as a
     finding UNLESS its final output lines carry the positive marker CANNOT EVALUATE: exit 2 also
     carries real failures, a helper that cannot be imported or a self-check that exits 2, so an
     unmarked exit 2 is never read as closure-not-evaluated, fail-closed), or REGISTRATION DRIFT, one of
     the closure-roster registration checks failing on its own assertion (each named; the checks run
     first, before opf/ is copied, any bound is derived or any leg runs). The whole-roster check holds
     every row: the leg names must be exactly those of the ceiling table, each once, and the ceiling
     table's legs must be the count and digest recorded in _ROSTER_PIN; every script must be a plain
     file name (no path separator, never '', '.' or '..', never absolute) and never one the isolated
     copy leaves out (*.pyc, __pycache__); and whenever opf/tools/ exists, every script must be a
     regular file that is itself there, opf.py included: never a symlink (read with os.lstat,
     whatever it points at, inside opf/tools/ or out) and never reached through a symlinked opf/ or
     opf/tools/ (its resolved path must be that file under the resolved repository root), so no row
     names a file outside opf/tools/ and the code a leg runs is the file opf/tools/ holds. So a row
     removed, added or repeated, a leg renamed, a row removed or added together with its ceiling row,
     a script that is not a plain file name or that the copy leaves out, and a script opf/tools/ does
     not hold as a regular file of its own (missing, a symlink, or resolving outside it) are each
     named as drift, never as a bound that cannot be derived. LIMIT: a change that also records the
     new count and digest in _ROSTER_PIN passes; the pin makes a roster change a deliberate third
     edit, and does not judge whether the change is right. The opf-pack-manifest-selftest,
     opf-adopt-observe-selftest, opf-import-batch-selftest and opf-import-complete-selftest rows
     are also pinned as exact tuples, so for those alone a changed argument list, or a script
     changed to another file that exists, is drift too
  2  malformed input or a harness error (fail-closed): no opf/tools/ directory (opf/ absent included),
     an opf/tools/ entry that cannot be checked (an OSError other than a missing entry: a directory
     that cannot be searched), an argument the gate does not recognise (--ci-job=VALUE included: the
     job name follows --ci-job as its own argument), a sweep that did not run exactly the legs it must
     run (that job's own legs under --ci-job, every leg otherwise; a sweep that ran no leg included),
     refused with no sweep result trusted and the OK line never printed,
     a temporary directory that cannot be created, copy failed, a symlink
     anywhere in opf/ that resolves outside the isolated copy (the copy keeps every symlink as the link
     itself, never its target, and is refused when one leaves it, so no symlink the copy holds
     reaches outside it), no interpreter, a per-leg bound that cannot be derived, any other exception
     before, around or after the legs (a harness fault, named, whatever it carries, an AssertionError
     or a SystemExit included, as is a leg result the report cannot read; one that ends the wait for
     a launched leg is raised only after that leg is killed, reaped and its pipe closed, any cleanup
     step that did not complete named beside it), a leg killed at its wall-clock bound (named as the
     bound tripping, never as a failure the leg reported; see _LEG_CEILING_S for how each bound is
     derived), a leg that ITSELF exited 2 AND printed CANNOT EVALUATE in its final lines (the leg
     said its own checks did not run), or a leg ended by a signal this gate did not send
     (named by its signal; a negative status cannot tell an outside signal, from another process, the
     OOM killer or an interpreter crash, from one the leg or its own process group sent itself, so it
     is never read as a failure the leg reported), with any cleanup after a kill that did not complete
     named beside it (CLEANUP INCOMPLETE); a real finding beside any of these still exits 1, except
     beside a harness fault: that stops the run where it is raised and keeps no leg's row, so the run
     exits 2 even when a leg already failed (a fault raised while the report prints can leave part of
     it above the NOT EVALUATED line)
EVERY leg bound is a DELIBERATE SHORTER DEADLINE under the kill-timeout rule's exception (AGENTS.md),
not a bound sized above its leg's end-to-end budget (see _LEG_CEILING_S): each sits above its leg's
recorded run and, for each leg whose command is a config-lane member, at or above the deadline those
lanes give the same command plus the launch margin (_LEG_BOUND_FLOOR_S), so the closure never kills
such a leg before the lane would; reaching one is attributed to the deadline (LEG BOUND EXCEEDED,
exit 2), never as a failure the leg reported and never as a pass. opf-tooling-selftest's is shown
below a budget READ FROM SOURCE, the one opf.py grants its own blocked-isolation leg, which its whole
self-test exceeds, because a bound above that whole budget would not fit the CI job (see DELIBERATE
SHORTER DEADLINE beside _LEG_CEILING_S); on this tree it is 5691 s, its copied sources'
declared waits plus the launch margin (a term re-derived on every run, so it moves with them), above
the 3660 s its config-lane deadline plus that margin gives (it rose from 1458 s, 3 x the leg's 486 s
ceiling, to that 3660 s floor, and then past it, as opf.py's declared waits grew), and still below
that blocked-isolation budget. A run under load heavier than the recorded ceilings' can trip a
healthy leg's bound and exit 2 (see _LOAD_HEADROOM).
--self-test follows the same convention: 1 only for a definite failure (the real opf/ does not verify,
registration drift, the flipped copy is NOT caught or its caught failure is NOT reported as closure
broken, or a self-test check fails, which it reports by
raising _CheckFailure), and 2 when a leg or the flip could not be evaluated (a bound kill, a signal, a
harness failure or fault, a temporary directory that cannot be created, a bound that cannot be derived,
a check that cannot evaluate what it checks (_CannotEvaluate: the CI job limit, or the config lanes'
roster or deadlines, cannot be read, or there is no opf/tools/ directory and the roster itself has
no drift), any
other exception or a SystemExit in the fast phase, an AssertionError of any other type included
whatever its message, any exception or SystemExit running or judging the positive leg (the flip still
runs), building or running the flipped copy (a source that cannot be decoded included) or escaping the
closure legs, or any flip outcome other than the exact bytes the flipped import writes;
see _negative_verdict), each named. The exception's type routes it, never its message
(_self_test_phase), and where it is raised decides which type it reaches the phase as. A check's own
assertions raise _CheckFailure (1), those about its fixtures included (a readiness file that never
appears, a fixture leg of the wrong shape), and so does any exception or SystemExit the code under test
lets escape a call the check makes into it (_under_test), a host fault there included (an OSError from
a pipe inside _run_one); only an exception raised by the check's own work outside such calls (an
OSError creating its temporary directory, as on a full disk) reaches the phase as a harness fault (2).
No routing here catches a KeyboardInterrupt: an operator's interrupt ends the run.
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
import errno
import hashlib
import io
import math
import os
import re
import shutil
import signal
import stat
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
    ("opf-adopt-selftest", "_opf_adopt.py", ["--self-test"]),
    ("opf-adopt-apply-selftest", "_opf_adopt_apply.py", ["--self-test"]),
    ("opf-adopt-state-selftest", "_opf_adopt_state.py", ["--self-test"]),
    ("opf-adopt-hook-selftest", "_opf_adopt_hook.py", ["--self-test"]),
    ("opf-adopt-complete-selftest", "_opf_adopt_complete.py", ["--self-test"]),
    ("opf-import-batch-selftest", "_opf_import_batch.py", ["--self-test"]),
    ("opf-import-complete-selftest", "_opf_import_complete.py", ["--self-test"]),
    ("opf-release-history-selftest", "_opf_release_history.py", ["--self-test"]),
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
# and one flat constant below a leg's recorded work kills a correctly-running leg, so each bound sits
# above that leg's recorded run. EVERY leg bound is nonetheless a DELIBERATE SHORTER DEADLINE under the
# rule's exception (AGENTS.md), not a bound sized above the leg's end-to-end budget: no bound here is
# shown to sit above the sequential waits its command grants itself, which repeat and add up past any
# count of wait sites (READ FROM SOURCE: opf-homes-selftest's run_git helper waits up to 60 s per git
# call, and its self-test reaches that helper from dozens of call sites, several inside loops, one
# after another, so those waits alone can add up past its 1260 s bound), and opf-tooling-selftest's
# sits below a budget READ FROM SOURCE (see DELIBERATE SHORTER DEADLINE below). Cutting a longer
# run short is the intent, and reaching a bound is attributed to the deadline: LEG BOUND EXCEEDED,
# exit 2 (cannot evaluate), never a failure the leg reported and never a pass.
# Each leg bound is the LARGEST of:
#   * _LOAD_HEADROOM times the slowest run of that leg recorded at the opf/ tree named below
#     (_LEG_CEILING_S; INFERRED for any other opf/ tree, this one included);
#   * a DELIBERATE DEADLINE derived from the leg's own sources: the SUM of the literal wait SITES they
#     declare (numeric literals in timeout= arguments and `timeout` parameter defaults, read from the
#     copied sources on every run by _declared_wait_s), plus _LAUNCH_MARGIN_S. This sum is NOT the
#     leg's end-to-end budget: a site that runs repeatedly (a helper called many times, a loop body)
#     counts ONCE, so repetition under-reads the waits that actually run, and a non-literal or
#     arithmetic timeout is MIS-READ in either direction (timeout=600 * 3 and timeout=600 / 3 both
#     read as 600; timeout=limit reads as nothing). A deadline that under-reads a leg's real
#     sequential waits kills the leg at its bound and the gate exits 2, a loud refusal to evaluate,
#     never a clean pass; the measured-ceiling term is what keeps such legs runnable.
#   * _MIN_LEG_BOUND_S, the floor for the legs that finish in seconds (see below);
#   * a per-leg FLOOR (_LEG_BOUND_FLOOR_S, see its own comment): the deadline the config lanes give
#     the same command, plus _LAUNCH_MARGIN_S, for each leg whose command is a config-lane member and
#     whose three terms above sit below that. A floor can only RAISE a bound, never lower one. EVERY
#     leg whose command is a member of the lane runner's own roster must reach its lane deadline plus
#     the margin (closure/leg-bound-reaches-the-lane-deadline), so the closure never kills such a leg
#     before the config lane running that same command would.
# The legs run one after another, so the worst case of one gate run is the SUM of the bounds plus one
# bounded post-kill cleanup deadline per leg (_DRAIN_MARGIN_S, which the output drain and the leader
# reap SHARE, paid only by a leg killed at its bound); the closure work is split between six CI jobs
# (_CI_JOB_LEGS: the whole roster's worst case does not fit one job), and the self-test requires EACH
# job's worst case (_job_worst_case_s over that job's legs: its self-test step plus its live gate
# step, cleanup deadlines included) to fit that job's own time limit, and refuses BY NAME any single
# leg bound that exceeds its job's limit rather than shrinking it. A change that adds or lengthens a
# leg's wait updates this table in the same change.
# Every ceiling below is whole seconds, rounded up, from ONE source: a serial isolated run of every leg
# started 2026-10-15T08:38Z and finished 2026-10-15T09:07Z, at the merge of revision
# da4b403ab55892cdbac837ba0c51e092ab971ffd into this change, whose opf/ subtree (git tree
# 7bf8107f4c6d23f5c89298ef121af566f5e48614) is byte-identical to that revision's (the change carrying
# this table touches no file under opf/). That tree is the ONLY one these legs were measured at. Each
# leg ran as the gate runs it (python3 -I -B against a copy of opf/ alone), plain (no nice), one leg at
# a time, on a shared 24-CPU host, with the 5-minute load average at the leg's start on its row (13.7
# to 24.6). A later refresh that changes opf/ CARRIES THESE FIGURES FORWARD AS INFERRED, not measured,
# until the legs are re-measured at the refreshed tree. Refreshes of this change onto main have changed
# opf/ since (the first, on 2026-10-16, onto c66fbe560a12b5258c1c2589fdbeba4e82c78afc), so each row's
# figure is INFERRED for this tree, as for every opf/ tree other than the one named above (compare
# `git rev-parse HEAD:opf` with it). Two rows come from no run at all: opf-adopt-state-selftest and
# opf-adopt-complete-selftest, legs main added after that run, carry main's 600 s figure (not
# measured on 2026-10-11; under the former flat 600 s bound, an inference), never a measurement.
# One row comes from another run: opf-import-complete-selftest, a leg main added after that run, carries
# main's 20 s figure, sized from three runs main measured on 2026-10-14 at another opf/ tree (2.3 to
# 2.4 s), so INFERRED here.
# One row comes from a later run: opf-release-history-selftest, a leg added after that run, carries 2 s,
# from three runs MEASURED on 2026-10-28 at opf/ tree a01f2c2150c99349156aa5e05e93dabd11a950ec, each
# as the gate runs it (python3 -I -B against a copy of opf/ alone, through _materialize and _run_one),
# plain, one leg at a time, on a 24-CPU host: 1.48 to 1.51 s, each exit 0, load5 5.5 at each start,
# rounded up. That load is lighter than the run above's (13.7 to 24.6), so the figure is INFERRED at a
# heavier load, as at any other opf/ tree; the leg's bound is its 1260 s config-lane floor, not this.
# _LOAD_HEADROOM is the allowance for a slower or more loaded host than that run's. The multiplier is
# an INFERENCE, not a measurement, and no observation supports it (one measured run contradicts it, see
# below): the host carried other work during the run, but a host loaded beyond it adds contention these
# ceilings do not hold, and such contention does not slow every leg alike. A leg that outgrows its bound
# trips it (exit 2, fail-closed), never passes clean. Two larger figures bear on it. Both were REPORTED
# during work on another change and measured at that change's tree (another branch; no revision, date
# or host was recorded). Both are UNVERIFIED: neither has been reproduced, and neither checks the multiplier.
#   * opf.py --self-test took 1348.7 s on a loaded host, 2.78 times this table's 485.6 s. Were the
#     opf-tooling-selftest leg that slow, 3 x 486 = 1458 s would cover it by 109.3 s.
#   * adopt-apply's self-test took 812.8 s inside a loaded opf.py --self-test run (opf.py runs the
#     same _opf_adopt_apply.self_test in-process), about 99 times this table's 8.2 s. Were the
#     standalone opf-adopt-apply-selftest leg that slow, slowdown under load would be far from uniform;
#     on this tree its bound is 4086 s (its declared waits, 4026 s, plus the 60 s launch margin), so a
#     run at that reported figure would finish inside its bound, and one slower still would trip it:
#     exit 2, a loud refusal to evaluate, never a false pass.
# MEASURED against the second figure on 2026-10-15, at this table's opf/ tree, plain, on a shared
# 24-CPU host, each run against a copy of opf/ alone as the gate runs it: inside one opf.py
# --self-test run (446.6 s end to end, exit 0, load5 about 25) adopt-apply's self-test took 7.4 s,
# and four standalone runs of it, two just before and two just after, took 7.7 to 7.9 s (load5 24.2 to
# 25.1). That is one run at one load: it shows no in-process slowdown here, and says nothing about the
# heavier load or the host behind the reported figures.
# The multiplier is capped by the job fit (_job_worst_case_s against each CI job's 21600 s default
# limit): on this tree the whole roster's worst case is 96395 s at 3 (no one job fits it: the premise
# of the split), the per-job worst cases are 21193 s (A), 21420 s (B), 15810 s (C), 19930 s (D),
# 19302 s (E) and 3490 s (F) at 3, and the same at 4 but for C, 18210 s (at 3 and at 4 the
# config-lane floors, _LEG_BOUND_FLOOR_S, hold every floored leg's bound above its headroom term,
# opf-homes-selftest's 1260 s above its 273 s and 364 s included; only opf-adopt-state-selftest and
# opf-adopt-complete-selftest, both in C, are bounded by the headroom term), and the largest
# multiplier that fits every job is 5.412 (at 5.413, job C's worst case is 21601.2 s, over its
# limit).
# The multiplier does NOT cover the one loaded run measured against it, and cannot. One --self-test
# run on 2026-10-15, at the revision carrying this table, at load5 about 50 (between 42 and 61 while it
# ran), passed (exit 0) with three legs past their ceilings by unequal factors: opf-doctor-selftest
# took 299.3 s, 4.34 times its 69 s ceiling; opf-init-selftest 299.6 s, 1.31 times its 228 s;
# opf-pack-manifest-selftest 28.7 s, 1.69 times its 17 s. opf-doctor-selftest finished inside its
# then 1140 s bound only because its declared-wait term set that bound (3 x 69 = 207 s alone would
# have killed it); its bound is now 1560 s, its declared waits (1500 s) plus the 60 s launch margin,
# above its 1260 s config-lane deadline plus that margin. That is one run at one load,
# REPORTED by its account and not reproduced here. Covering it on the headroom term alone needs a
# multiplier above 299.3 / 69 = 4.33768... (so 4.338 to three places, since 4.337 x 69 = 299.253
# falls short of 299.3); the multiplier is one constant for every leg. At 4.338 every job now fits
# (job C's worst case is then 19021.2 s, the only one that moves, since only opf-adopt-state-selftest's
# and opf-adopt-complete-selftest's headroom terms, 2602.8 s each, then exceed their other terms), but
# this change, which holds the lane-member legs to their config-lane deadlines, leaves the
# multiplier at 3: raising it is a separate sizing decision. So a run under such load CAN exit 2 on a
# healthy leg: each leg whose bound is the headroom term alone (on this tree
# opf-adopt-state-selftest and opf-adopt-complete-selftest, 1800 s each) trips its bound once it
# runs more than 3 times its ceiling; opf-homes-selftest tripped exactly so (CI killed it at 273 s,
# 3 x its 91 s ceiling, in all four config-injection lanes, and then at a 297 s floor in two of them)
# and now carries the floor _LEG_BOUND_FLOOR_S documents. That exit 2 is reported as the bound
# tripping, never as a pass and never as a finding.
# DELIBERATE SHORTER DEADLINE: opf-tooling-selftest. READ FROM SOURCE (opf/tools/opf.py, the watchdog
# regression runner's budgets), opf.py --self-test grants its blocked-isolation leg ALONE
# 31 * _deadline_case_budget(180) = 31 * (180 + 5 + 1) = 5766 s (31 isolation cases, each its 180 s
# timeout, the 5 s fixture cleanup grace and the 1 s receipt allowance), so the whole self-test grants
# itself more; this leg's bound (5691 s on this tree: its declared waits, 5630.012 s rounded up to
# 5631 s, plus the 60 s launch margin, above the 3660 s its config-lane deadline gives, the 3600 s the
# lanes give opf/tools/opf.py --self-test plus that margin; it rose with those waits from the former
# 1458 s, 3 x its 486 s ceiling, to that 3660 s floor and then past it) sits below that. This
# bound counts in both sweeps and in the negative leg, so job A's worst case moves 3 s per second of
# it. A bound above that leg's budget alone, 5766 + 60 = 5826 s, is 3 x (5826 - 5691) = 405 s more, a
# job A worst case of 21598 s, 2 s inside the 21600 s limit: that leg alone no longer shows the
# misfit. The same runner launches its legs one after another (_watchdog_run_legs), and it grants its
# completion-poll-masked leg 2954 s more, so a bound above those two legs, 5766 + 2954 + 60 = 8780 s, is
# 3 x (8780 - 5691) = 9267 s more, a job A worst case of 30460 s, over the limit, and a bound above
# the whole self-test's budget is larger still. So this bound takes the
# kill-timeout rule's exception (AGENTS.md), as every other leg bound here does: a deliberately
# shorter deadline, above the leg's recorded run and cutting a longer run short on purpose, with
# reaching it attributed to the deadline (LEG BOUND EXCEEDED, exit 2; the self-test's negative leg,
# under the same bound, reports its trip as cannot evaluate, exit 2), never as a failure the leg
# reported and never as a pass.
_LEG_CEILING_S = {
    "opf-homes-selftest": 91,  # INFERRED here (measured at 7bf8107f: 90.1 s at load5 13.7)
    "opf-homes-contract": 1,  # INFERRED here (measured at 7bf8107f: 0.4 s at load5 13.9)
    "opf-tooling-selftest": 486,  # INFERRED here (measured at 7bf8107f: 485.6 s at load5 13.9)
    "opf-drift-selftest": 3,  # INFERRED here (measured at 7bf8107f: 2.7 s at load5 15.9)
    "opf-doctor-selftest": 69,  # INFERRED here (measured at 7bf8107f: 68.5 s at load5 15.9)
    "opf-init-selftest": 228,  # INFERRED here (measured at 7bf8107f: 227.4 s at load5 16.3)
    "opf-init-contract-validator-selftest": 1,  # INFERRED here (measured at 7bf8107f: 0.1 s at load5 17.6)
    "opf-init-contract-check-selftest": 1,  # INFERRED here (measured at 7bf8107f: 0.1 s at load5 17.6)
    "opf-adopt-selftest": 152,  # INFERRED here (measured at 7bf8107f: 151.1 s at load5 18.6)
    "opf-adopt-apply-selftest": 9,  # INFERRED here (measured at 7bf8107f: 8.2 s at load5 21.5)
    "opf-adopt-state-selftest": 600,  # INFERRED (main's row; not measured: 7bf8107f has no such leg)
    "opf-adopt-hook-selftest": 1,  # INFERRED here (measured at 7bf8107f: 1.0 s at load5 21.6)
    "opf-adopt-complete-selftest": 600,  # INFERRED (main's row; not measured: 7bf8107f has no such leg)
    "opf-import-batch-selftest": 1,  # INFERRED here (measured at 7bf8107f: 0.7 s at load5 21.6)
    # opf-import-complete-selftest: the slowest run times 20 / 3 for load, 2.4 * 20 / 3 = 16.0, rounded up to
    # a multiple of 5, 20 s.
    "opf-import-complete-selftest": 20,  # INFERRED (main's row, measured 2026-10-14: 2.3 to 2.4 s, three runs)
    "opf-release-history-selftest": 2,  # MEASURED at a01f2c21 (1.48 to 1.51 s at load5 5.5, three runs)
    "opf-pack-manifest-selftest": 17,  # INFERRED here (measured at 7bf8107f: 16.8 s at load5 21.7)
    "opf-adopt-observe-selftest": 284,  # INFERRED here (measured at 7bf8107f: 283.7 s at load5 21.9)
    "opf-adopt-discover-selftest": 1,  # INFERRED here (measured at 7bf8107f: 0.5 s at load5 23.9)
    "opf-prompt-pack-selftest": 1,  # INFERRED here (measured at 7bf8107f: 0.8 s at load5 23.9)
    "opf-prompt-pack": 1,  # INFERRED here (measured at 7bf8107f: 0.2 s at load5 23.9)
    "opf-oplock-selftest": 108,  # INFERRED here (measured at 7bf8107f: 107.4 s at load5 23.9)
    "opf-init-substrate-selftest": 4,  # INFERRED here (measured at 7bf8107f: 3.4 s at load5 24.5)
    "opf-init-builders-selftest": 1,  # INFERRED here (measured at 7bf8107f: 0.3 s at load5 24.6)
    "opf-init-operation-selftest": 100,  # INFERRED here (measured at 7bf8107f: 99.2 s at load5 24.6)
    "opf-init-p0-selftest": 40,  # INFERRED here (measured at 7bf8107f: 39.8 s at load5 24.3)
    "opf-init-observe-selftest": 2,  # INFERRED here (measured at 7bf8107f: 1.1 s at load5 24.3)
    "commonmark-headings-selftest": 13,  # INFERRED here (measured at 7bf8107f: 12.2 s at load5 24.3)
    "commonmark-conformance": 1,  # INFERRED here (measured at 7bf8107f: 0.2 s at load5 24.2)
}
# The roster's own pin, recorded apart from both tables: the number of legs in _LEG_CEILING_S and the
# sha256 of their names, sorted, each followed by one newline (_roster_pin). The whole-roster check
# holds _SUBSET to the ceiling table name by name and the ceiling table to this pin, so a row removed,
# added or renamed together with its ceiling row, which leaves the two tables agreeing, is still drift.
# A deliberate roster change records the new figures here in the same change (the drift line prints
# them); this pin does not judge whether that change is right.
_ROSTER_PIN = (29, "de659c6cc4dbdd48fda1f84466b8555da068da3ff4f2c8cc200a9bc6e85393b8")
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
# exactly the floor. The former 120 s floor put the three-job split's job B over its 21600 s limit
# (21986 s at a headroom of 3, before the config-lane floors); on this tree it would raise no bound
# (opf-homes-contract's and opf-prompt-pack's declared waits, 180 s and 425 s, plus the launch margin
# already reach 240 s and 485 s, and the config-lane floors hold every lane-member leg above it), so
# it would move no job's worst case. The reported 1348.7 s opf.py figure above is
# covered at any multiplier on this tree: that leg's bound is set by its declared waits (5691 s), not
# by the headroom term.
_MIN_LEG_BOUND_S = 60
# Per-leg FLOORS, each the CONFIG-LANE DEADLINE of the leg's command plus _LAUNCH_MARGIN_S: a minimum
# bound for a leg whose command the config-injection lanes (tools/selftest_git_fixture_env.py) also
# run, as the very same command against the real opf/tools/, and whose three derived terms sit below
# that deadline plus the margin; the largest term wins in _leg_bounds, so a floor can only RAISE a
# bound, never lower one, and a floor at or below what the other terms already reach is refused by the
# self-test as dead weight (the row must be re-fitted or removed). FIGURE BASIS, every row: the lanes
# bound each member at its _config_member_bound, READ FROM SOURCE (its CONFIG_MEMBER_BOUNDS entry,
# else CONFIG_MEMBER_BOUND_DEFAULT, 1200 s), and that bound is ITSELF a deliberate shorter deadline
# (the lane runner says so beside CONFIG_MEMBER_BOUND_DEFAULT), so a floor here is NOT a bound above
# the leg's end-to-end budget either: it is the lanes' deadline plus the launch margin, so that the
# closure never kills a leg before the lane running that same command would. In the lanes each leg runs
# the same command as a grandchild of a closure self-test member, one of the four members each lane's
# pool runs at once, under the same load. The rows are the lane deadlines on this tree: 1260 s (1200 +
# 60) for every leg the lanes bound at the default, and 1860 s (1800 + 60) for opf-init-selftest and
# opf-adopt-observe-selftest, whose commands carry 1800 s entries there. Five lane-member legs carry no
# row because their other terms already reach their lane deadline plus the margin
# (opf-tooling-selftest, whose command carries a 3600 s entry, so 3660 s, below the 5691 s its
# declared waits give on this tree; opf-doctor-selftest, whose command takes the 1200 s default, so
# 1260 s, below the 1560 s its declared waits, 1500 s, plus the margin give on this tree;
# opf-adopt-apply-selftest, opf-adopt-state-selftest and opf-adopt-complete-selftest), and two legs
# are no lane member at all (opf-homes-contract and opf-prompt-pack, whose commands carry no
# self-test flag, so the lane runner's roster does not select them).
# closure/leg-bound-reaches-the-lane-deadline reads the lane runner's own roster
# (_registered_selftests) and its deadlines on every self-test run and refuses ANY lane-member
# leg whose bound sits below its deadline plus the margin, so raising a lane deadline, or registering
# a new lane member, without raising the matching row here is a named failure. LIMIT: these rows are
# copies, checked against the runner on every self-test run but not read from it when the gate runs:
# the isolated copy of opf/ holds no lane runner.
# FIGURE BASIS, opf-homes-selftest, which also carries recorded evidence: the lanes grant its command
# CONFIG_MEMBER_BOUND_DEFAULT, 1200 s: it has no CONFIG_MEMBER_BOUNDS entry, since the lanes' SIZING
# formula over its lane times MEASURED there on 2026-10-12, at an earlier tree, stays below the
# default (it is one of the nine members whose slowest time was 142.55 s; 142.55 x 20 / 3 = 950.3 s).
# So its floor is 1200 + 60 = 1260 s. In the closure CI job the leg runs one leg at a time; no CI time
# of it there is recorded here, and every measured standalone run of it (below) sits far below the
# floor.
# MEASURED, CI run 39922918571 at c024de19 (the merge da1b98ff onto main 30f92d54), under the former
# 297 s floor: in the config-system lane the gate killed this leg at 297.1 s with no output, and in
# the config-malformed lane at 297.1 s with its final summary lines already printed
# (OPF-HOMES BOUNDARY SELF-TEST: OK (336 checks) | OPF-HOMES SELF-TEST: OK (3855 checks)); the
# config-combined lane's closure member passed. In those lane runs the lanes' own member running this
# command exited 0 under its 1200 s budget in all three lanes, and the other legs of the closure
# member took 1868.4 s, 164.9 s and 791.2 s (system) and 1851.2 s, 145.1 s and 568.5 s (malformed)
# (opf-tooling-selftest, opf-doctor-selftest and opf-adopt-apply-selftest, against recorded ceilings
# of 486 s, 69 s and 9 s). Earlier CI runs, REPORTED by the former basis of this floor and not
# re-read here, killed the leg at its then 273 s bound (3 x its 91 s ceiling) in all four
# config-injection lanes. Every kill is a LOWER bound on a lane run, never a
# length: no lane length of this leg has been measured, so that the floor covers its slowest lane
# run is an INFERENCE from the lanes' own member of the same command finishing inside the 1200 s
# the floor exceeds (in the same lane runs, though not beside the same other members), never a
# measured length; a lane run past the floor trips the bound again: exit 2, a loud refusal to
# evaluate, never a pass.
# MEASURED 2026-10-23 at c024de19 (24-CPU host, load5 2.8 to 4.5, as the gate runs it, through
# _materialize and _run_one): 53.0 s plain (exit 0, 3855 checks), and 54.5 to 58.3 s each (all exit
# 0) with four copies started at once and confined to four CPUs: four concurrent workers alone do not
# reproduce the lane figures on this host, so that the lanes' slowdown comes from their runner and
# their other members is an INFERENCE, not a measurement on the affected runner. MEASURED 2026-10-21
# (shared 24-CPU host, load5 about 18 to 20, as the gate runs it): 62.4 s plain, and 64.2 s (exit 0)
# under a rebuild of the lanes' injected configuration (HOME and XDG_CONFIG_HOME at the lanes' poison
# config carrying hooksPath, excludesFile, attributesFile and fsmonitor, the lanes' sh observer
# wrapper ahead of git on PATH) with the hook, fsmonitor and exposure logs all EMPTY afterwards.
# GROWTH: a change that grows this leg (one pending, REPORTED, takes check_opf_homes.py --self-test
# from about 3773 to about 10038 checks) is NOT shown absorbed by this sizing: its lane time must be
# re-measured and the lanes' deadline for the command re-sized, and once that deadline rises above
# 1200 s, closure/leg-bound-reaches-the-lane-deadline refuses this floor until it is raised to match.
# FIT: each of the 22 rows below raises its leg's bound, and the jobs those bounds fit are set out on
# _CI_JOB_LEGS.
# A row naming no subset leg, or a value that is not a positive int (a bool included), is refused
# fail-closed (_leg_bounds): a stale row left behind by a leg rename would otherwise silently stop
# raising the bound it records.
_LEG_BOUND_FLOOR_S = {
    "opf-homes-selftest": 1260,
    "opf-drift-selftest": 1260,
    "opf-init-selftest": 1860,
    "opf-init-contract-validator-selftest": 1260,
    "opf-init-contract-check-selftest": 1260,
    "opf-adopt-selftest": 1260,
    "opf-adopt-hook-selftest": 1260,
    "opf-import-batch-selftest": 1260,
    "opf-import-complete-selftest": 1260,
    "opf-release-history-selftest": 1260,
    "opf-pack-manifest-selftest": 1260,
    "opf-adopt-observe-selftest": 1860,
    "opf-adopt-discover-selftest": 1260,
    "opf-prompt-pack-selftest": 1260,
    "opf-oplock-selftest": 1260,
    "opf-init-substrate-selftest": 1260,
    "opf-init-builders-selftest": 1260,
    "opf-init-operation-selftest": 1260,
    "opf-init-p0-selftest": 1260,
    "opf-init-observe-selftest": 1260,
    "commonmark-headings-selftest": 1260,
    "commonmark-conformance": 1260,
}
# The config lanes' runner. Its own roster (_registered_selftests) says which closure legs' commands
# are lane members, and its deadlines (_lane_budgets, cross-checked against its _config_member_bound)
# what each must reach (closure/leg-bound-reaches-the-lane-deadline); both are read on every
# self-test run, which holds the copies in _LEG_BOUND_FLOOR_S to them.
_LANE_RUNNER = Path("tools") / "selftest_git_fixture_env.py"
# The names the isolated copy of opf/ leaves out (bytecode artefacts). _materialize copies through
# _COPY_IGNORE, and the whole-roster check refuses a script it would leave out through the same
# function, so the two cannot disagree on a name.
_COPY_PATTERNS = ("__pycache__", "*.pyc")
_COPY_IGNORE = shutil.ignore_patterns(*_COPY_PATTERNS)

# The positive cannot-evaluate marker. A subset leg that exits 2 is read as "could not evaluate its own
# checks" ONLY when its captured final lines carry this marker (the convention the adopt-apply self-test
# prints on an expired inner bound). Exit 2 WITHOUT it is a finding, because exit 2 also carries real
# failures: opf.py maps a helper import failure (exactly what a surviving upward edge produces) to exit 2
# with a lowercase "(cannot evaluate)", and the commonmark conformance selftest exits 2 on its own
# self-check failures. Each of those is closure broken, never closure-not-evaluated, so only the
# uppercase marker counts, and only in the output of a leg that launched: a row the runner never
# launched (launched False) is the harness's own failure, never the leg's declaration, whatever text it
# carries (_report). A roster script missing from opf/tools/ is registration drift, a finding (1),
# named before the copy is made (_check_roster_registration).
_CANNOT_EVALUATE_MARKER = "CANNOT EVALUATE"

_CI_WORKFLOW = Path(".github") / "workflows" / "quality.yml"
# The closure work is PARTITIONED between six CI jobs, because the whole roster's worst case
# (_job_worst_case_s over every leg) does not fit one job's time limit with the bound rule above
# unchanged, and, once every lane-member leg reaches its config-lane deadline plus the launch margin
# (see _LEG_BOUND_FLOOR_S), it does not fit four. ARITHMETIC on this tree: the legs' two sweeps,
# their cleanup deadlines and the flip come to 95445 s, and each job adds the 350 s vector allowance
# and the 600 s setup to them, so four jobs hold at most 4 x (21600 - 950) = 82600 s of that work,
# 12845 s short; five is the fewest that the total allows, and the sixth, F, holds the one leg the
# placement below finds no room for in the other five. The worst cases, at a headroom of 3, are
# 21193 s (A, the flip's job), 21420 s (B), 15810 s (C), 19930 s (D), 19302 s (E) and 3490 s (F),
# each within its 21600 s limit. opf-import-complete-selftest, a leg main added, sits in D beside opf-import-batch-selftest:
# its 1260 s config-lane floor adds 2540 s to a job's worst case (two sweeps and two cleanup
# deadlines), which A and E cannot hold (nor could B while it held the store-upgrade gate's leg), and D,
# unlike C, is not moved by the headroom multiplier
# up to 5.412. opf-release-history-selftest, added after it, sits alone in F: its 1260 s config-lane
# floor adds the same 2540 s, which D (22470 s), A (23733 s), E (21842 s) and B (23960 s, beside
# commonmark-headings-selftest) cannot hold. C could (18350 s), but C is the job the headroom
# multiplier moves: with the leg in C the largest multiplier that fits every job falls from 5.412 to
# 4.354 (at 4.355, job C's worst case is 21602.0 s), barely above the 4.338 the loaded
# opf-doctor-selftest run needs (see _LEG_CEILING_S). F, like B and D, is not moved by the headroom
# multiplier up to 5.412 (the leg's 1260 s floor exceeds its headroom term, 2 s times the
# multiplier). When the declared waits of opf.py's and _opf_adopt_apply.py's sources grew by 1206 s each
# (the two legs' declared-wait sums from 3554.012 s to 4760.012 s and from 2820 s to 4026 s), the
# opf-adopt-apply-selftest bound rose by 1206 s (2880 s to 4086 s) and the opf-tooling-selftest bound
# by 1161 s (from 3660 s, set by its former floor row, to 4821 s), A, which had held
# opf-adopt-apply-selftest since the three-job split, no longer fit (26175 s), so that leg moved to
# E, and E's commonmark-headings-selftest and opf-init-builders-selftest moved to A and B to make
# room; C, the only job whose worst case a headroom multiplier from 3 up to 5.412 moves (see
# _LEG_CEILING_S), is unchanged. When the in-process loader vectors added four literal wait sites to
# opf.py's sources (_opf_emit.py's loaded-exit probe at 300 s and fault-channel probe at 120 s, and
# _opf_views.py's real-SIGINT probe and fault-channel probe at 120 s each: opf-tooling-selftest's
# declared-wait sum from 4970.012 s to 5630.012 s) and a 120 s fault-channel probe to check_opf_doctor.py (1380 s to
# 1500 s), the opf-tooling-selftest bound rose by 660 s (5031 s to 5691 s), counted three times in A
# (both sweeps and the flip), and the opf-doctor-selftest bound by 120 s (1440 s to 1560 s), counted
# twice, so A no longer fit (23733 s). commonmark-headings-selftest, whose config-lane floor holds its
# bound at 1260 s (2540 s of a job's worst case), moved from A to B: only B (18880 s before the move)
# and C had room for it, and C is the job the headroom multiplier moves. opf-prompt-pack stays in B,
# where the --ci-job sweep vector removes it. Each job runs the --self-test step and then the live gate step over its own legs alone (--ci-job); the self-test's negative leg (the flip,
# which runs opf.py --self-test under the opf-tooling-selftest bound) runs only in the job that
# holds that leg. Every subset leg must be in exactly ONE job: a leg in no job would run in no CI job
# at all, and a leg in more than one would make the jobs' worst cases overlap, so _partition_fault
# names either shape and the fit check and the --ci-job runs refuse on it (fail-closed). A leg moved
# between jobs updates this table in the same change, under the fit check's per-job arithmetic.
# LIMIT: nothing here checks WHICH --ci-job value each workflow job passes. The workflow is read only
# for each job's time limit (_ci_job_limit_s), and the CI-parity gate compares the union of every
# job's commands, so a workflow job that ran another job's --ci-job value would pass both; what
# holds the pairing is review of each job's two steps in the workflow file itself.
_CI_JOB_LEGS = {
    "opf-closure-a": frozenset({
        "opf-tooling-selftest",
        "opf-doctor-selftest",
    }),
    "opf-closure-b": frozenset({
        "opf-homes-contract",
        "opf-drift-selftest",
        "opf-init-selftest",
        "opf-init-contract-validator-selftest",
        "opf-init-contract-check-selftest",
        "opf-prompt-pack-selftest",
        "opf-prompt-pack",
        "opf-init-builders-selftest",
        "commonmark-headings-selftest",
    }),
    "opf-closure-c": frozenset({
        "opf-homes-selftest",
        "opf-adopt-selftest",
        "opf-adopt-state-selftest",
        "opf-adopt-hook-selftest",
        "opf-adopt-complete-selftest",
    }),
    "opf-closure-d": frozenset({
        "opf-import-batch-selftest",
        "opf-import-complete-selftest",
        "opf-pack-manifest-selftest",
        "opf-adopt-observe-selftest",
        "opf-adopt-discover-selftest",
        "opf-oplock-selftest",
        "opf-init-substrate-selftest",
    }),
    "opf-closure-e": frozenset({
        "opf-adopt-apply-selftest",
        "opf-init-operation-selftest",
        "opf-init-p0-selftest",
        "opf-init-observe-selftest",
        "commonmark-conformance",
    }),
    "opf-closure-f": frozenset({
        "opf-release-history-selftest",
    }),
}
_CI_JOBS = tuple(sorted(_CI_JOB_LEGS))
# The leg whose job also runs the self-test's negative leg: the flip runs opf.py --self-test under
# this leg's bound, so it is this leg's work and budget.
_FLIP_LEG = "opf-tooling-selftest"
_CI_DEFAULT_JOB_MINUTES = 360  # the GitHub Actions limit for a hosted-runner job that sets no timeout-minutes
_CI_SETUP_ALLOWANCE_S = 600    # checkout with full history and setup-python, before the gate steps start
# _VECTOR_ALLOWANCE_S is the job's work that no leg bound covers: the self-test step's registration,
# verdict, table, floor, scan, CI-read and time-scaled stub checks, the cleanup doubles, the kill vectors
# (termination and its marker window, leader reap, partial output, the runner's cleanup report) and
# their waits for the escapee, the after-launch fault vectors, plus the opf/ copy and bound scan of each
# sweep (the self-test's positive and negative legs and the live gate step). It is sized in two parts:
#   * the deadlines those vectors hold on a passing run, READ FROM SOURCE, sum to 311 s: the half-ceiling
#     kill 11 s, the scaled run 6 s, the hang under 36 s, termination 52 s, the two escape runs 104 s,
#     the runner's cleanup report 57 s, the cleanup doubles 3 s, and the after-launch fault vectors 42 s
#     (the first's readiness 30 s, cleanup 2 s and marker window 4 s, and the 2 s cleanup of each of the
#     other three);
#   * the work no deadline bounds (the bound checks and wait scans, and each sweep's copy and bound
#     scan) MEASURED at most 12.8 s in three runs on 2026-10-15, at the opf/ tree named above (plain,
#     load5 about 23 to 26 on a shared 24-CPU host; the whole vector phase took 43.7 to 44.9 s), given
#     the legs' 3x headroom: 38.4 s. Each sweep's copy and bound scan read opf/, so at a later opf/
#     tree that figure is INFERRED. The routing vectors added since (registration drift, harness
#     faults, temporary-directory and fast-phase errors, the flipped leg's raw output) launch no
#     process and hold no deadline; one
#     run of the whole vector phase with them was REPORTED at 44.6 s on 2026-10-16 (plain; load not
#     recorded), a figure NOT reproduced. Nor do the ones added after (every drift shape and harness
#     fault through run(), the report and flip faults, the self-test's wiring to its fast phase, the
#     whole-roster check with its roster pin and script-name drift, and the routing of a crash or a
#     SystemExit in the code under test, a check that cannot evaluate and a fault creating a check's
#     own temporary directory); one run of the whole vector phase with them was REPORTED at 44.0 s on
#     2026-10-16 (plain, load5 27.0 at its start and 27.4 at its end), a figure NOT reproduced. Nor
#     do the ones added last (a roster script that is a symlink, resolves outside opf/tools/ or is a
#     name the copy leaves out, a copy holding a symlink that leaves it, and an exception or a
#     SystemExit from the positive leg or escaping the closure legs). With every vector this file
#     then held, ONE measured run of the whole vector phase on 2026-10-16 took 44.5 s (a single run,
#     plain, on a shared 24-CPU host, load5 18.1 at its start and 18.2 at its end): one run at one
#     load, never a bound. The after-launch fault vectors were added after that run; beyond their
#     deadlines above, their work (four launches, and one throwaway opf/ copy and bound scan through
#     _run_closure) is NOT measured into the 12.8 s. The per-job sweep vectors were added after it
#     too: they launch no process and hold no deadline, and their work (a few throwaway opf/ copies
#     and bound scans through run() and main(), four per CI job, so twenty-four over the six jobs) is NOT
#     measured into the 12.8 s either. So do the floor vectors: they launch no process and hold no
#     deadline, and their work (a few more bound scans through _leg_bounds) is NOT measured into the
#     12.8 s. Nor are the config-lane deadline vectors, which launch no process and hold no deadline
#     either: their work (one read of the lane runner's roster through its _registered_selftests, a
#     few parses of its source and three more bound scans) is NOT measured into it; one roster read
#     took 1.9 s in one probe on 2026-10-23 (plain, 24-CPU host, load not recorded), a figure, never
#     a bound.
# 311 + 38.4 = 349.4 s, rounded up to 350 s.
_VECTOR_ALLOWANCE_S = 350


class _BoundError(Exception):
    """A per-leg bound cannot be derived (no ceiling, or a source that cannot be read or parsed)."""


class _CheckFailure(AssertionError):
    """A check failing on its own assertion: a definite failure (exit 1), its message naming the check
    ("closure/..."). Every self-test check raises it, as do the closure-roster registration checks, and
    a check that calls the code under test converts an exception that code lets escape into one
    (_under_test). The TYPE is what routes it: an AssertionError of any other type is never read as a
    check failing, whatever its message (_self_test_phase, _registration_drift)."""


class _CannotEvaluate(Exception):
    """A self-test check that cannot evaluate what it checks: cannot evaluate (exit 2), named by its
    message, never a failure and never a pass. Raised when the CI job limit cannot be read."""


class _CopyRefused(Exception):
    """The isolated copy of opf/ holds a symlink that resolves outside it (_materialize): a harness
    fault, cannot evaluate (exit 2), named with each such link, never a leg run against it."""


def _under_test(check, func, *args, refuses=(), **kwargs):
    """func(*args, **kwargs), a call the self-test check named check makes into the code under test.
    An exception or a SystemExit func lets escape is that check's failure, a _CheckFailure naming the
    check, func and the exception's type (exit 1): a crash in the code under test is a definite
    failure, never a harness fault and never an exit the code under test chose, whatever its cause (a
    host fault inside it, such as an OSError from a pipe inside _run_one, included). Two kinds pass
    through as themselves: a _CheckFailure (a registration check's own finding, which the calling check
    judges) and an instance of a type in refuses, the documented refusal the calling check expects or
    leaves as cannot evaluate (a _BoundError: a bound that cannot be derived). An exception the check's
    own work raises outside such calls (an OSError creating its temporary directory, as on a full disk)
    is not converted, so it reaches _self_test_phase as a harness fault (2); the check's own assertions,
    about its fixtures included (a readiness file that never appears), raise _CheckFailure (1)."""
    try:
        return func(*args, **kwargs)
    except _CheckFailure:
        raise
    except refuses:
        raise
    except (Exception, SystemExit) as exc:
        raise _CheckFailure("closure/{}: the code under test, {}, raised {}: {}".format(
            check, getattr(func, "__name__", repr(func)), type(exc).__name__, exc)) from exc


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


def _leg_bounds(tools_dir, ceilings=None, launch_margin_s=None, bound_floors=None):
    """{leg name: (bound_s, ceiling_s, declared waits sum_s)} for every subset leg, derived from the
    sources under tools_dir. Raises _BoundError (fail-closed) for a leg with no ceiling, for a
    floor row (_LEG_BOUND_FLOOR_S) naming no subset leg, and for a floor value that is not a positive
    int, a bool included (a stale or mistyped row must never silently stop raising the bound it
    records). ceilings,
    launch_margin_s and bound_floors default to _LEG_CEILING_S, _LAUNCH_MARGIN_S and
    _LEG_BOUND_FLOOR_S (the self-test passes a table missing a row, a launch margin below the floor
    so that the floor term must raise a bound, and doctored floor tables around the point where a
    per-leg floor starts to raise a bound)."""
    ceilings = _LEG_CEILING_S if ceilings is None else ceilings
    margin = _LAUNCH_MARGIN_S if launch_margin_s is None else launch_margin_s
    floors = _LEG_BOUND_FLOOR_S if bound_floors is None else bound_floors
    subset_names = {name for name, _script, _args in _SUBSET}
    for name in sorted(floors):
        if name not in subset_names:
            raise _BoundError("per-leg floor for a name that is no subset leg: "
                              "{}".format(name))
        if type(floors[name]) is not int or floors[name] <= 0:
            raise _BoundError("per-leg floor for {} is not a positive int: "
                              "{!r}".format(name, floors[name]))
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
        bound = max(_MIN_LEG_BOUND_S, _LOAD_HEADROOM * ceiling, math.ceil(declared) + margin,
                    floors.get(name, 0))
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


def _job_legs(legs):
    """The subset leg names in legs, in roster order (every leg when legs is None)."""
    return [name for name, _script, _args in _SUBSET if legs is None or name in legs]


def _partition_fault(jobs=None):
    """Each fault in the partition of the subset legs across the CI jobs, [] when every subset leg is
    in exactly one job and every name a job holds is a subset leg. The fit check raises on any fault
    and a --ci-job run refuses before any leg launches (fail-closed): a leg in no job would run in no
    CI job at all, and a leg in more than one would make the jobs' worst cases overlap, so neither is
    ever read as a fitting partition. jobs defaults to _CI_JOB_LEGS (the self-test passes doctored
    tables to show each fault shape is named)."""
    jobs = _CI_JOB_LEGS if jobs is None else jobs
    faults = []
    for name, _script, _args in _SUBSET:
        holders = sorted(job for job, members in jobs.items() if name in members)
        if len(holders) != 1:
            faults.append("{}: in {} CI jobs ({})".format(
                name, len(holders), ", ".join(holders) or "none"))
    known = {name for name, _script, _args in _SUBSET}
    for job in sorted(jobs):
        for name in sorted(set(jobs[job]) - known):
            faults.append("{}: {} is not a subset leg".format(job, name))
    return faults


def _oversize_legs(bounds, limit_s, legs=None):
    """Each leg (of legs; every leg when None) whose own bound exceeds the CI job limit. Such a bound
    is refused BY NAME (the ceiling table or the leg's declared waits must change under review), never
    silently shrunk to fit the job."""
    return ["{}: bound {}s alone exceeds the {}s job limit".format(name, bounds[name], limit_s)
            for name in _job_legs(legs) if bounds[name] > limit_s]


def _job_worst_case_s(bounds, negative_bound_s, legs=None):
    """A closure CI job's worst case in seconds: two sweeps over its legs (the self-test's positive
    leg and the live gate step; legs None is every leg), the self-test's negative leg
    (negative_bound_s; 0 for the job that does not run the flip), ONE cleanup deadline
    (_DRAIN_MARGIN_S, the drain and the leader reap sharing it) for every leg that can be killed
    (each leg of both sweeps and, when it runs, the negative leg), the work outside every leg bound
    (_VECTOR_ALLOWANCE_S: the self-test's vectors and each sweep's copy and bound scan), and the
    job's setup."""
    names = _job_legs(legs)
    kills = 2 * len(names) + (1 if negative_bound_s else 0)
    return (2 * sum(bounds[name] for name in names) + negative_bound_s
            + kills * _DRAIN_MARGIN_S + _VECTOR_ALLOWANCE_S + _CI_SETUP_ALLOWANCE_S)


def _fit_fault(bounds, negative_bound_s, limit_s, legs=None, job=None):
    """None when the job's worst case fits limit_s, else the refusal naming every term of it. legs
    and job are the job's own legs and name (None: every leg, and the limit named unattributed)."""
    total = _job_worst_case_s(bounds, negative_bound_s, legs)
    if total <= limit_s:
        return None
    names = _job_legs(legs)
    return ("{}s of worst-case job work (2 x subset sweep {}s over {} legs, negative leg {}s, {} "
            "cleanup deadlines of {}s, vectors {}s, setup {}s) exceeds the {}s job limit{}".format(
                total, sum(bounds[name] for name in names), len(names), negative_bound_s,
                2 * len(names) + (1 if negative_bound_s else 0), _DRAIN_MARGIN_S,
                _VECTOR_ALLOWANCE_S, _CI_SETUP_ALLOWANCE_S, limit_s,
                " of {}".format(job) if job else ""))


_LANE_BOUND_NAMES = ("CONFIG_MEMBER_BOUND_DEFAULT", "CONFIG_MEMBER_BOUNDS")


def _lane_budgets(lane_text):
    """The config lanes' member budgets, read from the lane runner's source text (_LANE_RUNNER), as
    ((default_s, {argv tuple: seconds}), None), or (None, reason) naming what could not be evaluated.
    They are the two names that runner's _config_member_bound reads: an argv's CONFIG_MEMBER_BOUNDS
    entry, else CONFIG_MEMBER_BOUND_DEFAULT. Fail-closed: each must be assigned exactly once at module
    level, by a plain `NAME = literal` statement; the default a positive int (a bool is not a
    duration), and the table a dict literal whose keys are tuples of str, none repeated, and whose
    values are positive ints. Any other shape is refused by name, never read around. LIMIT: a later
    mutation of either at run time (an item assignment, an update call) is not read."""
    try:
        tree = ast.parse(lane_text)
    except (SyntaxError, ValueError) as exc:
        return None, "the lane runner does not parse: {}".format(exc)
    found = dict((name, []) for name in _LANE_BOUND_NAMES)
    for node in tree.body:
        targets = (node.targets if isinstance(node, ast.Assign)
                   else [node.target] if isinstance(node, (ast.AnnAssign, ast.AugAssign)) else [])
        for target in targets:
            if isinstance(target, ast.Name) and target.id in found:
                found[target.id].append(node)
    for name in _LANE_BOUND_NAMES:
        if len(found[name]) != 1:
            return None, ("{} is assigned {} times at module level in the lane runner (need exactly "
                          "one)".format(name, len(found[name])))
        if not isinstance(found[name][0], ast.Assign) or len(found[name][0].targets) != 1:
            return None, "{} is not assigned by a plain `{} = literal` statement".format(name, name)
    try:
        default = ast.literal_eval(found["CONFIG_MEMBER_BOUND_DEFAULT"][0].value)
    except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
        default = None
    if type(default) is not int or default <= 0:
        return None, "CONFIG_MEMBER_BOUND_DEFAULT is not a positive int literal"
    table_node = found["CONFIG_MEMBER_BOUNDS"][0].value
    if not isinstance(table_node, ast.Dict) or None in table_node.keys:
        return None, "CONFIG_MEMBER_BOUNDS is not a dict literal"
    table = {}
    for key_node, value_node in zip(table_node.keys, table_node.values):
        try:
            key, value = ast.literal_eval(key_node), ast.literal_eval(value_node)
        except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
            return None, "a CONFIG_MEMBER_BOUNDS entry is not a literal"
        if type(key) is not tuple or not key or any(type(part) is not str for part in key):
            return None, "CONFIG_MEMBER_BOUNDS key {!r} is not a tuple of str".format(key)
        if key in table:
            return None, "CONFIG_MEMBER_BOUNDS key {!r} is repeated".format(key)
        if type(value) is not int or value <= 0:
            return None, "CONFIG_MEMBER_BOUNDS value for {!r} is not a positive int".format(key)
        table[key] = value
    return (default, table), None


def _lane_runner():
    """The config lanes' runner module (_LANE_RUNNER, beside this gate), imported on first use. The
    self-test reads from it the roster the lanes run (_registered_selftests) and the deadline they give
    each member (_config_member_bound); only the self-test imports it, so the gate's own runs over the
    isolated copy need nothing of it."""
    import selftest_git_fixture_env
    return selftest_git_fixture_env


def _lane_member_legs(roster, identity=None):
    """{leg name: (roster argv, ...)} for every subset leg whose command, as the config lanes run it
    against the real opf/tools/ (opf/tools/<script> <args>), is a member of roster: the lane runner's
    own roster (_registered_selftests), never a list kept here. A roster argv matches through identity
    (the runner's _command_identity, which drops one leading ./; plain equality when None), so every
    spelling the lanes launch is kept. A leg no roster argv matches is no lane member. Pure: nothing
    is launched or read."""
    identity = tuple if identity is None else identity
    members = {}
    for name, script, args in _SUBSET:
        command = ("opf/tools/" + script,) + tuple(args)
        matched = tuple(sorted(tuple(argv) for argv in roster if tuple(identity(argv)) == command))
        if matched:
            members[name] = matched
    return members


def _lane_deadlines(members, default_s, table):
    """{leg name: seconds}, the deadline the config lanes give each lane-member leg's command (members,
    from _lane_member_legs): the largest, over the leg's roster argvs, of that argv's
    CONFIG_MEMBER_BOUNDS entry, else CONFIG_MEMBER_BOUND_DEFAULT (default_s and table, read through
    _lane_budgets), as the runner's _config_member_bound looks it up."""
    return dict((name, max(table.get(argv, default_s) for argv in argvs))
                for name, argvs in members.items())


def _lane_deadline_disagreements(members, deadlines, bound_of):
    """Each lane-member leg whose deadline read from the lane runner's source (deadlines, from
    _lane_deadlines) is not the one the runner's own lookup (bound_of, its _config_member_bound) gives
    the leg's command at run time; [] when they agree. A disagreement means the source read no longer
    says what the lanes do, so the check that relies on it cannot evaluate."""
    found = []
    for name in sorted(members):
        looked_up = max(bound_of(argv) for argv in members[name])
        if looked_up != deadlines[name]:
            found.append("{}: {}s read from the source, {}s from _config_member_bound".format(
                name, deadlines[name], looked_up))
    return found


def _lane_budget_faults(bounds, deadlines, members):
    """Each lane-member leg (members, from _lane_member_legs over the lane runner's own roster) whose
    bound is below the deadline the config lanes give its command (deadlines, from _lane_deadlines)
    plus _LAUNCH_MARGIN_S; [] when every such bound reaches it. A bound that reaches it means the
    closure never kills that leg before the lane running the same command would; it does not make the
    bound one above the leg's end-to-end budget, since the lanes' deadline is itself a deliberate
    shorter deadline."""
    faults = []
    for name in sorted(members):
        need = deadlines[name] + _LAUNCH_MARGIN_S
        if bounds[name] < need:
            faults.append("{}: bound {}s below the {}s the config lanes require ({} is given {}s "
                          "there, plus the {}s launch margin)".format(
                              name, bounds[name], need,
                              " / ".join(" ".join(argv) for argv in members[name]),
                              deadlines[name], _LAUNCH_MARGIN_S))
    return faults


# The workflow reader below is a STRICT ALLOWLIST of the YAML forms .github/workflows/quality.yml
# itself uses; it never parses YAML in general, and a line it cannot place under one of these forms
# makes the job-limit read cannot evaluate. What it checks is each line's form, by the rules below
# (a subset of YAML 1.2's), and the indentation of every block mapping and sequence: every key of a
# mapping sits at its first key's indentation, every entry of a sequence at its first entry's, and a
# nested value's first line sits deeper than its key (a sequence may also start at the key's own
# column). It does not check YAML 1.2's whole grammar: what it never reads (comment text and
# block-scalar content, see the end of this comment) is not checked at all. Whitespace is YAML's:
# indentation is ASCII spaces only, and tokens are separated by spaces or tabs; no other character (a
# no-break space, U+3000 or any other Unicode space) is ever stripped as whitespace, so it stays part
# of the text it sits in, as it does for a YAML loader. The forms:
#   * a blank line (spaces and tabs only), or a comment line (`#` after any spaces and tabs);
#   * a plain-key line, `KEY:` (KEY starts with a letter or `_` and holds letters, digits, `_`, `.`
#     and `-`; the colon follows it directly, then whitespace or the line end), optionally after ONE
#     sequence-entry indicator written `- ` (a dash, one space, then the key), whose value is:
#       - empty, or a comment: a nested mapping or sequence follows;
#       - the literal block-scalar header `|` with no indicator (clip chomping, its indentation
#         detected), then optionally whitespace and a comment; its content is every following line
#         deeper than the key, with the blank lines among them;
#       - a single-quoted scalar on one line, whose only escape is `''` (YAML defines no other in
#         that style), as in `python-version: '3.14'`;
#       - a flow sequence on one line of plain items, as in `branches: [main]` and
#         `types: [opened, synchronize, reopened]`;
#       - a plain scalar on one line, by YAML's own rules: it starts with no indicator character
#         (`-`, `?` and `:` only when a non-space follows), holds no `: ` and no ` #`, holds only
#         printable characters, and ends before any trailing whitespace or comment;
#   * a plain-scalar sequence entry, `- ITEM` (letters, digits, `_`, `.`, `/`, `-` and spaces,
#     starting with no `-`), as in the needs: list.
# Everything else is refused BY NAME: a double-quoted scalar (quality.yml uses none, so no escape in
# that style is ever read), a block-scalar header with an indicator (`|-`, `|2`, `>`) or a malformed
# one (`|++`, `|0`), a sequence entry not written `- ` then content, an alias, anchor, tag or flow
# mapping, a value holding a character YAML does not print (a control character), and a key or
# entry at no indentation an open mapping or sequence gives (over- or under-indented).
# A comment's text, like a comment line's and block-scalar content, is data: it is never read for
# structure, so its characters are not checked (a control character in a comment is not refused),
# and a block scalar's lines are only required to sit deeper than its key (YAML 1.2 also requires
# each to reach its first content line's indentation; one that does not is not refused).
_CI_QUOTED_CHAR = "[\t\x20-\x26\x28-\x7e\xa0-\ud7ff\ue000-\ufffd\U00010000-\U0010ffff]"
_CI_NON_SPACE = "[\x21-\x7e\xa0-\ud7ff\ue000-\ufefe\uff00-\ufffd\U00010000-\U0010ffff]"
_CI_TRAILER = "(?:[ \t]+(?:#.*)?)?"
_CI_FLOW_ITEM = r"[A-Za-z0-9_./][A-Za-z0-9_./-]*"
_CI_PLAIN_KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]*:(?=[ \t]|$)")
_CI_PLAIN_ITEM = re.compile(r"[A-Za-z0-9_./][A-Za-z0-9_./ -]*")
_CI_NESTED_VALUE = re.compile("(?:#.*)?")
_CI_BLOCK_HEADER = re.compile(r"(\|)" + _CI_TRAILER)
_CI_QUOTED_VALUE = re.compile("('(?:" + _CI_QUOTED_CHAR + "|'')*')" + _CI_TRAILER)
_CI_FLOW_SEQUENCE = re.compile(r"(\[[ \t]*(?:" + _CI_FLOW_ITEM + r"(?:[ \t]*,[ \t]*" + _CI_FLOW_ITEM
                               + r")*)?[ \t]*\])" + _CI_TRAILER)
_CI_PLAIN_VALUE = re.compile(
    "((?:(?![-?:,\\[\\]{}#&*!|>'\"%@`])" + _CI_NON_SPACE + "|[-?:](?=" + _CI_NON_SPACE + "))"
    "(?:[ \t]*(?:(?![:#])" + _CI_NON_SPACE + "|(?<![ \t])#|:(?=" + _CI_NON_SPACE + ")))*)"
    + _CI_TRAILER)
# The characters str.splitlines() ends a line at besides "\n" and "\r": a YAML 1.2 loader reads none of
# them as a line break, a YAML 1.1 loader reads \x85, \u2028 and \u2029 as one, so a workflow holding
# any of them has lines this parser and the loader GitHub runs need not agree on.
_CI_OTHER_LINE_BREAKS = frozenset("\x0b\x0c\x1c\x1d\x1e\x85\u2028\u2029")


def _ci_value(rest):
    """The form of the text after a plain key's colon (empty, or whitespace first), as (form, value):
    ("nested", "") for an empty or comment-only value (nested lines follow, and are scanned in turn),
    ("block", "|") for the bare literal block-scalar header, ("quoted", text) for a single-line
    single-quoted scalar, ("flow", text) for a single-line flow sequence of plain items, ("plain",
    text) for a single-line plain scalar (a comment begins only at whitespace then `#`, so `30#x` is
    the scalar `30#x`), and (None, text) for any other value: no other form is on the allowlist above
    (a double-quoted scalar, any other block-scalar header, an alias, anchor or tag, a flow mapping, a
    multi-line or unterminated quoted scalar, a plain scalar carrying a mapping colon or a control
    character). Only spaces and tabs are stripped, never other Unicode whitespace."""
    text = rest.lstrip(" \t")
    if _CI_NESTED_VALUE.fullmatch(text):
        return "nested", ""
    for form, pattern in (("block", _CI_BLOCK_HEADER), ("quoted", _CI_QUOTED_VALUE),
                          ("flow", _CI_FLOW_SEQUENCE), ("plain", _CI_PLAIN_VALUE)):
        match = pattern.fullmatch(text)
        if match:
            return form, match.group(1)
    return None, text


def _ci_job_header(lines, job):
    """Where `job` is declared in the workflow lines, as ((index of its header line, indentation of the
    jobs mapping's own keys, the placed lines), None), or (None, reason) naming what could not be
    placed. The placed lines are the plain-key lines and plain-scalar sequence entries, in file order,
    each as (index, indentation, sequence-entry indicators (0 or 1), key (None for an entry), value
    form (_ci_value; "item" for an entry), value); blank and comment lines and block-scalar content
    are not among them. The header must be a DIRECT key of the top-level `jobs:` mapping, exactly
    once, with no value on its own line; a line that merely reads like one inside a block scalar, under
    another top-level key, or nested deeper inside jobs: is not a job, and the loader GitHub runs would
    not read it as one. Fail-closed: every line of the file must be one of the allowlisted forms (the
    allowlist above _ci_value), so no header can hide inside structure this reader does not read.
    Each of these is refused BY NAME, never scanned around: a line of no allowlisted form (a quoted or
    escaped key, a sequence entry not written `- ` then content, a line starting with a character
    YAML does not read as whitespace, such as a no-break space), a value of no allowlisted form (a
    multi-line quoted or flow scalar, whose opening line is not one, a double-quoted scalar, a
    block-scalar header other than a bare `|`), a line deeper than a single-line value above it (a
    plain scalar continued, or no structure at all), a tab before a line's content, a sequence at
    column 0, a line indented before any top-level key, a second top-level jobs: key, a jobs: value
    that is not a mapping, a line inside jobs: that is neither one of its keys nor nested inside one,
    a job header with a value on its line, a sequence as the job's value, a key or entry at no
    indentation an open mapping or sequence gives (deeper than its siblings but not deeper than a
    key that opens a nested value, or at no sibling's indentation), no top-level jobs: key, and the
    job absent or repeated."""
    top = None           # the top-level key the scan is under
    jobs_line = None     # the index of the top-level jobs: key line
    jobs_indent = None   # the indentation of the jobs mapping's own keys, from its first key line
    block_column = None  # the key column of an open block scalar; deeper lines are its content
    value_column = None  # the column of a single-line value's key or item; a deeper line continues it
    columns = [(0, False)]  # the open block collections, outermost first: (indentation, a sequence?)
    opener = None        # (key column, the job's header?) of a key whose value is the lines below it
    found = []
    placed = []
    for index, line in enumerate(lines):
        body = line.lstrip(" ")
        indent = len(line) - len(body)
        content = body.lstrip(" \t")
        if block_column is not None:
            if not content or indent > block_column:
                continue  # block-scalar content, or a blank line inside it
            block_column = None
        if not content or content.startswith("#"):
            continue
        refusal = "line {} {!r} of the workflow ".format(index + 1, content)
        if body[0] == "\t":
            return None, refusal + "has a tab before its content; cannot place the {} job".format(job)
        if value_column is not None and indent > value_column:
            return None, (refusal + "is deeper than the single-line value above it (a continued "
                          "scalar, or no structure this parser reads); cannot place the {} "
                          "job".format(job))
        value_column = None
        item, dashes = body, 0
        if body.startswith("- "):
            item, dashes = body[2:], 1
        if dashes and indent == 0:
            return None, refusal + "opens a sequence at column 0; cannot place the {} job".format(job)
        if indent and top is None:
            return None, refusal + "is indented before any top-level key; cannot place the {} job".format(
                job)
        match = _CI_PLAIN_KEY.match(item)
        if match:
            key = match.group(0)[:-1]
            form, value = _ci_value(item[match.end():])
            if form is None:
                return None, (refusal + "carries the value {!r}, a form this parser does not place "
                              "(an alias, anchor, tag, double-quoted, multi-line quoted or flow scalar, "
                              "flow mapping, or a block-scalar header other than a bare |); cannot "
                              "place the {} job".format(value, job))
        elif dashes and _CI_PLAIN_ITEM.fullmatch(item):
            key, form, value = None, "item", item  # an unquoted plain-scalar list item
        else:
            return None, (refusal + "is not a form this parser places (a plain-key line, an unquoted "
                          "plain-scalar list item, or block-scalar content); cannot place the {} "
                          "job".format(job))
        placed.append((index, indent, dashes, key, form, value))
        if form == "block":
            block_column = indent + 2 * dashes
        elif form == "item":
            value_column = indent
        elif form != "nested":
            value_column = indent + 2 * dashes
        heads_job = False
        if indent == 0:
            top = key
            if key == "jobs":
                if jobs_line is not None:
                    return None, (refusal + "is a second top-level jobs: key; cannot place the {} "
                                  "job".format(job))
                if form != "nested":
                    return None, (refusal + "gives jobs: a value that is not a mapping; cannot place "
                                  "the {} job".format(job))
                jobs_line = index
        elif top == "jobs":
            if jobs_indent is None:
                jobs_indent = indent
            if indent < jobs_indent or (dashes and indent == jobs_indent):
                return None, (refusal + "is neither a key of the jobs: mapping nor nested inside one; "
                              "cannot place the {} job".format(job))
            if indent == jobs_indent and key == job:
                if form != "nested":
                    return None, (refusal + "gives the {} job a value that is not a mapping (cannot "
                                  "evaluate)".format(job))
                found.append(index)
                heads_job = True
        # Block structure: a line deeper than a key whose value is the lines below it (or an entry at
        # that key's own column, an indentless sequence) opens that value's mapping or sequence, never
        # a sequence under the job's header; any other line closes every collection deeper than it
        # and must then be a key at an open mapping's indentation or an entry at an open sequence's,
        # so a mapping's keys all sit at its first key's indentation, a sequence's entries at its
        # first entry's.
        if opener is not None and (indent > opener[0] or (dashes and indent == opener[0])):
            if dashes and opener[1]:
                return None, (refusal + "gives the {} job a sequence, not a mapping (cannot "
                              "evaluate)".format(job))
            columns.append((indent, bool(dashes)))
        else:
            while columns[-1][0] > indent or (columns[-1] == (indent, True) and not dashes):
                columns.pop()  # a key at an indentless sequence's own column also ends it
            if columns[-1] != (indent, bool(dashes)):
                return None, (refusal + "is at no indentation an open mapping or sequence gives (an "
                              "over- or under-indented key or entry); cannot place the {} "
                              "job".format(job))
        if dashes and key is not None:
            columns.append((indent + 2, False))  # the mapping a `- KEY:` entry opens
        opener = (indent + 2 * dashes, heads_job) if form == "nested" else None
    if jobs_line is None:
        return None, "the workflow has no top-level jobs: key; cannot place the {} job".format(job)
    if len(found) != 1:
        return None, ("job {} appears {} times as a key of the top-level jobs: mapping (need exactly "
                      "one)".format(job, len(found)))
    return (found[0], jobs_indent, placed), None


def _ci_job_limit_s(workflow_text, job):
    """The time limit, in seconds, of `job` in the workflow text, as (seconds, None), or (None, reason)
    naming what could not be evaluated. The limit is the JOB-LEVEL timeout-minutes (a key at the job
    mapping's own indentation), capped at _CI_DEFAULT_JOB_MINUTES, the GitHub Actions limit for a
    hosted-runner job that sets none. Job and step limits are DISTINCT: a step's own timeout-minutes is
    parsed and validated but does not cap the job budget (it kills that one step, a loud step failure,
    without shrinking the time the gate steps get). Fail-closed: before ANY limit (the default
    included) is returned, every line of the WHOLE workflow must be one of the allowlisted forms (the
    allowlist above _ci_value; _ci_job_header places them, and this reads the job's lines from what
    it placed). A quoted, escaped, tagged, anchored or aliased key or step, a quoted list item, a
    value that is an alias, anchor, tag, double-quoted scalar or flow mapping (`steps:
    *closure_steps`, `steps: [{run: echo ok, timeout-minutes: 1}]`, `run: "echo \\q"`), a
    block-scalar header other than a bare `|` (`|++`, `|0`), and any other line shape are refused BY
    NAME, never scanned around: the YAML loader GitHub runs would evaluate (or reject) them where this
    parser would not, so no limit the job might carry there can be trusted. A timeout-minutes value
    is validated BEFORE any block-scalar skip, so `timeout-minutes: |` is refused by name, never
    skipped as content; it must be a plain decimal integer without a leading zero (`030` reads as 24
    to a YAML 1.1 loader and as 30 to a YAML 1.2 one, and `30#x` is the string `30#x`, never 30). The
    job header is placed structurally (_ci_job_header): one that is absent or duplicated as a direct
    key of the top-level jobs: mapping, or that only reads like one inside a block scalar, under
    another top-level key or deeper inside jobs:, refuses by name, as does a workflow holding a line
    break this parser and a YAML loader need not agree on (_CI_OTHER_LINE_BREAKS), a key or entry
    at no indentation an open mapping or sequence gives, and a job whose value is not a mapping (a
    value on the header's line, a sequence, or no keys at all)."""
    breaks = sorted(set(workflow_text) & _CI_OTHER_LINE_BREAKS)
    if breaks:
        return None, ("the workflow holds {} (a line break str.splitlines and a YAML loader need not "
                      "agree on); cannot place the {} job".format(
                          ", ".join(repr(char) for char in breaks), job))
    placed, reason = _ci_job_header(workflow_text.splitlines(), job)
    if placed is None:
        return None, reason
    start, jobs_indent, entries = placed
    minutes = [_CI_DEFAULT_JOB_MINUTES]
    job_indent = None    # the indentation of the job mapping's own keys, from its first key line
    for index, indent, dashes, key, _form, value in entries:
        if index <= start:
            continue
        if indent <= jobs_indent:
            break  # the next job, or a top-level key, ends this job
        if key is None:
            continue  # an unquoted plain-scalar list item carries no key and no step mapping
        if job_indent is None and dashes == 0:
            job_indent = indent
        if key == "timeout-minutes":
            # Only a plain scalar can match: every other form's value is empty or keeps its
            # indicator (|, the quotes, the brackets).
            if not re.fullmatch(r"0|[1-9][0-9]*", value):
                return None, ("timeout-minutes value {!r} in the {} job is not a plain decimal "
                              "integer without a leading zero (cannot evaluate)".format(
                                  value, job))
            if dashes == 0 and indent == job_indent:
                minutes.append(int(value))
    if job_indent is None:
        return None, ("the {} job has no keys (an empty value, not a mapping; cannot "
                      "evaluate)".format(job))
    return 60 * min(minutes), None


def _check_pack_manifest_registration(subset, tools_dir=None):
    """Independent requirement, not inferred from the release manifest.

    Covers this exact closure-roster tuple, not arbitrary dispatcher changes
    or the registrations of other gates. tools_dir is unused: the tuple
    names its own script (_check_roster_registration checks scripts exist).
    """
    expected = ("opf-pack-manifest-selftest", "_opf_pack_manifest.py", ["--self-test"])
    matches = [row for row in subset
               if row[0] == expected[0] or row[1] == expected[1]]
    if matches != [expected]:
        raise _CheckFailure("closure/pack-manifest-registration")


def _pack_manifest_registration_self_test():
    _under_test("pack-manifest-registration", _check_pack_manifest_registration, _SUBSET)
    print("PASS closure/pack-manifest-registration")
    # Mutate the real roster, independently of the shell-runner deletion.
    # No digest or generated-manifest check participates in the verdict.
    changed = list(_SUBSET)
    changed.remove(("opf-pack-manifest-selftest", "_opf_pack_manifest.py", ["--self-test"]))
    try:
        _under_test("pack-manifest-registration", _check_pack_manifest_registration, changed)
    except _CheckFailure as exc:
        if str(exc) != "closure/pack-manifest-registration":
            raise
    else:
        raise _CheckFailure("closure/pack-manifest-registration-not-red")
    print("RED closure-registration -> closure/pack-manifest-registration")


def _check_adopt_observe_registration(subset, tools_dir=None):
    """Independent requirement, not inferred from the release manifest.

    Covers this exact closure-roster tuple, not arbitrary dispatcher changes
    or the registrations of other gates. tools_dir is unused: the tuple
    names its own script (_check_roster_registration checks scripts exist).
    """
    expected = ("opf-adopt-observe-selftest", "_opf_adopt_observe.py", ["--self-test"])
    matches = [row for row in subset
               if row[0] == expected[0] or row[1] == expected[1]]
    if matches != [expected]:
        raise _CheckFailure("closure/adopt-observe-registration")


def _adopt_observe_registration_self_test():
    _under_test("adopt-observe-registration", _check_adopt_observe_registration, _SUBSET)
    print("PASS closure/adopt-observe-registration")
    # Mutate the real roster, independently of the shell-runner deletion.
    # No digest or generated-manifest check participates in the verdict.
    changed = list(_SUBSET)
    changed.remove(("opf-adopt-observe-selftest", "_opf_adopt_observe.py", ["--self-test"]))
    try:
        _under_test("adopt-observe-registration", _check_adopt_observe_registration, changed)
    except _CheckFailure as exc:
        if str(exc) != "closure/adopt-observe-registration":
            raise
    else:
        raise _CheckFailure("closure/adopt-observe-registration-not-red")
    print("RED closure-registration -> closure/adopt-observe-registration")


def _check_import_batch_registration(subset, tools_dir=None):
    """Independent requirement, not inferred from the release manifest.

    Covers this exact closure-roster tuple, not arbitrary dispatcher changes
    or the registrations of other gates. tools_dir is unused: the tuple
    names its own script (_check_roster_registration checks scripts exist).
    """
    expected = ("opf-import-batch-selftest", "_opf_import_batch.py", ["--self-test"])
    matches = [row for row in subset
               if row[0] == expected[0] or row[1] == expected[1]]
    if matches != [expected]:
        raise _CheckFailure("closure/import-batch-registration")


def _import_batch_registration_self_test():
    _under_test("import-batch-registration", _check_import_batch_registration, _SUBSET)
    print("PASS closure/import-batch-registration")
    # Mutate the real roster, independently of the shell-runner deletion.
    # No digest or generated-manifest check participates in the verdict.
    changed = list(_SUBSET)
    changed.remove(("opf-import-batch-selftest", "_opf_import_batch.py", ["--self-test"]))
    try:
        _under_test("import-batch-registration", _check_import_batch_registration, changed)
    except _CheckFailure as exc:
        if str(exc) != "closure/import-batch-registration":
            raise
    else:
        raise _CheckFailure("closure/import-batch-registration-not-red")
    print("RED closure-registration -> closure/import-batch-registration")


def _check_import_complete_registration(subset, tools_dir=None):
    """Independent requirement, not inferred from the release manifest.

    Covers this exact closure-roster tuple, not arbitrary dispatcher changes
    or the registrations of other gates. tools_dir is unused: the tuple
    names its own script (_check_roster_registration checks scripts exist).
    """
    expected = ("opf-import-complete-selftest", "_opf_import_complete.py", ["--self-test"])
    matches = [row for row in subset
               if row[0] == expected[0] or row[1] == expected[1]]
    if matches != [expected]:
        raise _CheckFailure("closure/import-complete-registration")


def _import_complete_registration_self_test():
    _under_test("import-complete-registration", _check_import_complete_registration, _SUBSET)
    print("PASS closure/import-complete-registration")
    # Mutate the real roster, independently of the shell-runner deletion.
    # No digest or generated-manifest check participates in the verdict.
    changed = list(_SUBSET)
    changed.remove(("opf-import-complete-selftest", "_opf_import_complete.py", ["--self-test"]))
    try:
        _under_test("import-complete-registration", _check_import_complete_registration, changed)
    except _CheckFailure as exc:
        if str(exc) != "closure/import-complete-registration":
            raise
    else:
        raise _CheckFailure("closure/import-complete-registration-not-red")
    print("RED closure-registration -> closure/import-complete-registration")


def _roster_pin(legs):
    """(count, sha256) of the leg names in legs: how many there are, and the sha256 of the names,
    sorted, each followed by one newline, encoded UTF-8 (the form _ROSTER_PIN records)."""
    names = sorted(legs)
    return len(names), hashlib.sha256("".join(name + "\n" for name in names).encode("utf-8")).hexdigest()


def _plain_file_name(script):
    """True when script is a plain file name: a str naming an entry of the directory it is joined to,
    with no path separator and no NUL, never '', '.' or '..' and never absolute. Joined to opf/tools/,
    any other value (an absolute path, a '..' component, a nested path) names a file outside
    opf/tools/, or none."""
    return (type(script) is str and script not in ("", ".", "..") and "\x00" not in script
            and Path(script).name == script)


def _tools_home(tools_dir):
    """Where the entries of tools_dir (a repository's opf/tools/) must resolve: the repository root
    resolved (os.path.realpath), then tools_dir's own last two components as named. A roster script
    whose resolved path is not under it reaches the legs through a symlinked opf/ or opf/tools/."""
    tools_dir = Path(os.path.abspath(tools_dir))
    return os.path.join(os.path.realpath(tools_dir.parent.parent), tools_dir.parent.name, tools_dir.name)


def _script_fault(tools_dir, script):
    """None when the plain file name script is a regular file that is itself in tools_dir, else what it
    is instead. The entry is read with os.lstat, so a symlink is named as one whatever it points at,
    inside opf/tools/ or outside it (the copy keeps it a link, and a roster script must be the file
    itself); its resolved path must then be that file under _tools_home, so a script reached through a
    symlinked opf/ or opf/tools/ is named with where it resolves. No such entry, a component that is not
    a directory, or an entry that is not a regular file is not a file in opf/tools/. Any other OSError
    (a directory that cannot be searched, a name too long for the file system) propagates: whether the
    script is there cannot be told, so the gate names it a harness fault (exit 2, _run_closure), never a
    missing script. A hard link is a regular file in opf/tools/, whatever other names its file has."""
    path = Path(tools_dir) / script
    try:
        mode = os.lstat(path).st_mode
    except (FileNotFoundError, NotADirectoryError):
        return "is not a file in opf/tools/"
    if stat.S_ISLNK(mode):
        return "is a symlink in opf/tools/ (to {!r}), not the file itself".format(os.readlink(path))
    if not stat.S_ISREG(mode):
        return "is not a file in opf/tools/"
    resolved = os.path.realpath(path)
    if resolved != os.path.join(_tools_home(tools_dir), script):
        return "resolves to {}, outside opf/tools/".format(resolved)
    return None


def _check_roster_registration(subset, tools_dir=None):
    """The WHOLE closure roster, every row. Every leg of _LEG_CEILING_S (each row of which records
    that leg's ceiling, INFERRED for this tree: most rows carry a run measured at an earlier tree, and
    two record a figure no run measured) must be a row of subset exactly once, and subset must name no
    other leg;
    the ceiling table's legs must be the count and digest _ROSTER_PIN records (_roster_pin), so a row
    removed, added or renamed together with its ceiling row, which leaves the two tables agreeing, is
    still named. Every row's script must be a plain file name (_plain_file_name), so no row names a
    file outside the opf/tools/ the legs run from, whether or not there is one, and never a name the
    isolated copy leaves out (_COPY_IGNORE: a *.pyc file or __pycache__, which the copy would lack);
    and, when tools_dir is given (that opf/tools/; None when there is none, which the gate refuses as
    cannot evaluate), each such script must be a regular file that is itself in opf/tools/
    (_script_fault: never a symlink, whatever it points at, and never reached through a symlinked
    opf/ or opf/tools/; an entry that cannot be checked propagates its OSError, a harness fault, never
    drift). Raises _CheckFailure "closure/roster-registration: ..." naming each missing, extra or
    repeated row, a ceiling table that is not the pin (with its count and digest, the figures a
    deliberate roster change records in _ROSTER_PIN), each script that is not a plain file name, each
    one the copy leaves out, and each script that is missing, a symlink or resolves outside
    opf/tools/. LIMIT: it reads opf/tools/ before the copy is made, so a change between the two is not
    named as drift, though the copy still refuses any symlink in it that resolves outside it
    (_materialize). LIMIT: a roster change that also records its new pin passes; and the check holds
    names, the pin and each script's name and presence only, so a row's arguments, or its script
    changed to another file that exists, are held only for the four rows the tuple checks above
    pin."""
    names = [row[0] for row in subset]
    faults = ["row {} missing".format(name) for name in _LEG_CEILING_S if name not in names]
    faults.extend("row {} not in the ceiling table".format(name) for name in dict.fromkeys(names)
                  if name not in _LEG_CEILING_S)
    faults.extend("row {} listed {} times".format(name, names.count(name))
                  for name in dict.fromkeys(names) if names.count(name) > 1)
    count, digest = _roster_pin(_LEG_CEILING_S)
    if (count, digest) != _ROSTER_PIN:
        faults.append("the ceiling table's {} legs (sha256 {}) are not the {} legs _ROSTER_PIN records "
                      "(sha256 {})".format(count, digest, _ROSTER_PIN[0], _ROSTER_PIN[1]))
    faults.extend("script {!r} of row {} is not a plain file name".format(script, name)
                  for name, script, _args in subset if not _plain_file_name(script))
    faults.extend("script {!r} of row {} is a name the isolated copy leaves out ({})".format(
                      script, name, ", ".join(_COPY_PATTERNS))
                  for name, script, _args in subset
                  if _plain_file_name(script) and _COPY_IGNORE("opf/tools", [script]))
    if tools_dir is not None:
        for name, script, _args in subset:
            if _plain_file_name(script) and not _COPY_IGNORE("opf/tools", [script]):
                fault = _script_fault(tools_dir, script)
                if fault is not None:
                    faults.append("script {} of row {} {}".format(script, name, fault))
    if faults:
        raise _CheckFailure("closure/roster-registration: " + "; ".join(faults))


def _pin_fault(table):
    """The whole-roster check's fault for a ceiling table that is not _ROSTER_PIN, its count and digest
    recomputed here from the documented form (the names, sorted, each followed by one newline, UTF-8),
    independently of _roster_pin, so a pin computed some other way is a named fault."""
    digest = hashlib.sha256("".join(name + "\n" for name in sorted(table)).encode("utf-8")).hexdigest()
    return ("the ceiling table's {} legs (sha256 {}) are not the {} legs _ROSTER_PIN records (sha256 "
            "{})".format(len(table), digest, _ROSTER_PIN[0], _ROSTER_PIN[1]))


def _roster_registration_self_test(root):
    """The whole-roster check holds on the real roster and opf/tools/, and names each drift shape: a
    row removed (one the tuple checks do not pin), a row added, a row repeated, a leg renamed, a script
    renamed to a file opf/tools/ does not hold, a ceiling row removed (the ceiling table then lacks a
    leg the subset runs, and is not the pin), a row removed, added or renamed together with its ceiling
    row (only the pin then differs), a script that is not a plain file name (ten cases over nine
    values: an absolute path, inside opf/tools/ or outside it, a '..' path to a file that exists, '..',
    '.', '', a nested path, a value that is not a str, a name carrying a NUL, and the absolute path
    outside opf/tools/ again with no opf/tools/ to check), and a script the isolated copy leaves out (a
    *.pyc name, with and without an opf/tools/ to check, and __pycache__); with no opf/tools/ to
    check, a plain script's presence is not checked, never failed. The symlink shapes need a tree of
    their own, so _harness_routing_self_test holds them through run(). As in run(), drift in the
    real roster itself is a failure (1) whether or not root has an opf/tools/, and with a clean
    roster no opf/tools/ is cannot evaluate (2): the scripts are absent, not drifted
    (_check_routing_self_test holds this through self_test_main)."""
    tools_dir = Path(root) / "opf" / "tools"
    present = tools_dir.is_dir()
    _under_test("roster-registration", _check_roster_registration, _SUBSET,
                tools_dir if present else None)
    if not present:
        raise _CannotEvaluate("closure/roster-registration-cannot-evaluate: opf/tools/ not found under "
                              "{}, so the roster's scripts cannot be checked".format(root))
    print("PASS closure/roster-registration")

    def renamed_leg(rows):
        return [("opf-homes-renamed", script, args) if name == "opf-homes-selftest"
                else (name, script, args) for name, script, args in rows]

    def with_script(script):
        return [(name, script if name == "opf-homes-contract" else old, args)
                for name, old, args in _SUBSET]

    removed = [row for row in _SUBSET if row[0] != "opf-homes-contract"]
    extra = ("opf-extra-selftest", "check_opf_homes.py", ["--self-test"])
    ceilings = dict(_LEG_CEILING_S)
    del ceilings["opf-homes-contract"]
    extended = dict(_LEG_CEILING_S)
    extended[extra[0]] = 1
    relabelled = dict(("opf-homes-renamed" if name == "opf-homes-selftest" else name, ceiling)
                      for name, ceiling in _LEG_CEILING_S.items())
    cases = [
        ("row-removed", removed, _LEG_CEILING_S, tools_dir, "row opf-homes-contract missing"),
        ("row-added", _SUBSET + [extra], _LEG_CEILING_S, tools_dir,
         "row opf-extra-selftest not in the ceiling table"),
        ("row-repeated", _SUBSET + [_SUBSET[1]], _LEG_CEILING_S, tools_dir,
         "row opf-homes-contract listed 2 times"),
        ("leg-renamed", renamed_leg(_SUBSET), _LEG_CEILING_S, tools_dir,
         "row opf-homes-selftest missing; row opf-homes-renamed not in the ceiling table"),
        ("script-renamed", with_script("check_opf_homes_renamed.py"), _LEG_CEILING_S, tools_dir,
         "script check_opf_homes_renamed.py of row opf-homes-contract is not a file in opf/tools/"),
        ("script-renamed-no-tools-dir", with_script("check_opf_homes_renamed.py"), _LEG_CEILING_S, None,
         None),
        ("ceiling-row-removed", _SUBSET, ceilings, tools_dir,
         "row opf-homes-contract not in the ceiling table; " + _pin_fault(ceilings)),
        # The two tables changed together still agree with each other; only the pin names these.
        ("row-removed-with-its-ceiling-row", removed, ceilings, tools_dir, _pin_fault(ceilings)),
        ("row-added-with-a-ceiling-row", _SUBSET + [extra], extended, tools_dir, _pin_fault(extended)),
        ("leg-renamed-in-both-tables", renamed_leg(_SUBSET), relabelled, tools_dir,
         _pin_fault(relabelled))]
    # A script that is not a plain file name names a file outside opf/tools/ (the absolute cases and
    # the '..' path below name files that exist), or none: drift whatever is there, opf/ present or
    # not.
    for label, script, scripts in (
            ("script-absolute-inside-opf-tools", str(tools_dir.resolve() / "check_opf_homes.py"),
             tools_dir),
            ("script-absolute-outside-opf-tools", str(Path(root).resolve() / "tools" /
                                                      "check_python_floor.py"), tools_dir),
            ("script-parent-path", "../../tools/check_python_floor.py", tools_dir),
            ("script-dot-dot", "..", tools_dir),
            ("script-dot", ".", tools_dir),
            ("script-empty", "", tools_dir),
            ("script-nested-path", "nested/check_opf_homes.py", tools_dir),
            ("script-not-a-str", Path("check_opf_homes.py"), tools_dir),
            ("script-nul", "check_opf_homes.py\x00", tools_dir),
            ("script-absolute-no-tools-dir", str(Path(root).resolve() / "tools" /
                                                 "check_python_floor.py"), None)):
        cases.append((label, with_script(script), _LEG_CEILING_S, scripts,
                      "script {!r} of row opf-homes-contract is not a plain file name".format(script)))
    # A plain file name the isolated copy leaves out (_COPY_IGNORE) would be missing from the copy the
    # leg runs from: drift whatever opf/tools/ holds, opf/ present or not.
    for label, script, scripts in (
            ("script-bytecode", "check_opf_homes.pyc", tools_dir),
            ("script-bytecode-no-tools-dir", "check_opf_homes.pyc", None),
            ("script-pycache", "__pycache__", tools_dir)):
        cases.append((label, with_script(script), _LEG_CEILING_S, scripts,
                      "script {!r} of row opf-homes-contract is a name the isolated copy leaves out "
                      "(__pycache__, *.pyc)".format(script)))
    for label, subset, table, scripts, want in cases:
        try:
            with _swapped(_LEG_CEILING_S=table):
                _under_test("roster-registration", _check_roster_registration, subset, scripts)
        except _CheckFailure as exc:
            if want is None or str(exc) != "closure/roster-registration: " + want:
                raise _CheckFailure("closure/roster-registration-drift-misnamed: {}: {}".format(
                    label, exc))
        else:
            if want is not None:
                raise _CheckFailure("closure/roster-registration-not-red: " + label)
    print("RED closure-registration -> closure/roster-registration names a row removed, added or "
          "repeated, a leg renamed, a script opf/tools/ does not hold, a ceiling row removed, a row "
          "removed, added or renamed together with its ceiling row (the pin), a script that is not a "
          "plain file name (absolute, '..', '.', '', nested, carrying a NUL or not a str) and a "
          "script the isolated copy leaves out (*.pyc, __pycache__), opf/tools/ present or not; with "
          "no opf/tools/ to check, a plain script's presence is not checked")


# Each closure-roster registration check, by the assertion message that is its own finding (the
# whole-roster check's message carries its faults after the name and a colon). Each takes the roster
# and the opf/tools/ its legs run from (None when there is none).
_REGISTRATION_CHECKS = (
    ("closure/roster-registration", _check_roster_registration),
    ("closure/pack-manifest-registration", _check_pack_manifest_registration),
    ("closure/adopt-observe-registration", _check_adopt_observe_registration),
    ("closure/import-batch-registration", _check_import_batch_registration),
    ("closure/import-complete-registration", _check_import_complete_registration),
)


def _registration_drift(subset, tools_dir=None):
    """The drift text of every registration check that subset fails on that check's OWN assertion, [] when
    all hold: a _CheckFailure whose message is the check's name, or its name, a colon and the faults it
    names. Every check runs, so each drift is named. tools_dir is the opf/tools/ the legs run from (None
    when there is none). Any other exception a check raises propagates: it is a harness fault (exit 2),
    never registration drift and never read as no drift, an AssertionError of another type carrying the
    check's own message included, as is a _CheckFailure carrying another check's message
    (_harness_routing_self_test holds each)."""
    drift = []
    for name, check in _REGISTRATION_CHECKS:
        try:
            check(subset, tools_dir)
        except _CheckFailure as exc:
            if str(exc) != name and not str(exc).startswith(name + ": "):
                raise
            drift.append(str(exc))
    return drift


def _isolated_env():
    """A minimal environment for the isolated subset. PYTHONPATH and PYTHONHOME are dropped so nothing off
    the copied tree can be imported (belt-and-suspenders atop `python3 -I`, which already ignores them), and
    every ambient GIT_-prefixed variable is dropped so an inherited GIT_DIR/GIT_WORK_TREE cannot rebind the
    copy to a different repository. Bytecode writing is suppressed to keep the copy pristine."""
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("GIT_") and k not in ("PYTHONPATH", "PYTHONHOME")}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def _links_leaving(top):
    """Each symlink under the directory top (top itself excluded) whose target, resolved
    (os.path.realpath; a link that does not resolve, a loop included, reads as the path it names), is
    neither top nor under it, as its path from top's parent and its link text, sorted. The walk never
    follows a link; an OSError listing a directory propagates."""
    home = os.path.realpath(top)
    leaving = []

    def fail(exc):
        raise exc

    for parent, dirs, files in os.walk(top, onerror=fail):
        for name in dirs + files:
            path = os.path.join(parent, name)
            if os.path.islink(path) and os.path.commonpath([home, os.path.realpath(path)]) != home:
                leaving.append("{} -> {!r}".format(os.path.relpath(path, os.path.dirname(top)),
                                                   os.readlink(path)))
    return sorted(leaving)


def _materialize(opf_src, dest):
    """Copy the opf/ subtree ALONE into dest/opf (no tools/, no .aiqt), skipping bytecode artefacts
    (_COPY_IGNORE). A symlink is copied as the link itself, never its target, and the copy is then
    refused (_CopyRefused, a harness fault: exit 2) when any symlink in it resolves outside it
    (_links_leaving): copying the target would put code from outside opf/ into the copy, and a link
    kept as itself that leaves the copy (an absolute link, or a relative one that climbs out) would let
    a leg reach it. A link that resolves inside the copy stays a link there. Raises on any copy failure
    so a truncated materialization fails closed rather than passing on a partial tree."""
    copy = dest / "opf"
    shutil.copytree(opf_src, copy, symlinks=True, ignore=_COPY_IGNORE)
    leaving = _links_leaving(copy)
    if leaving:
        raise _CopyRefused("the copy holds {} symlink(s) resolving outside its opf/: {}".format(
            len(leaving), "; ".join(leaving)))
    return copy


def _tail(output, keep=3):
    return "\n".join((output or b"").decode("utf-8", "replace").splitlines()[-keep:])


def _shown_bytes(output, keep=200):
    """Raw output bytes as their Python literal, so every byte shows (a carriage return or a trailing
    space included), clipped to the last keep bytes; "(no output)" when empty."""
    if not output:
        return "(no output)"
    if len(output) <= keep:
        return repr(output)
    return "(last {} of {} bytes) {!r}".format(keep, len(output), output[-keep:])


def _signal_name(rc):
    """The signal that ended a leg whose return code is rc (subprocess reports signal N as -N), by name
    and number, or by number alone when this platform does not name it."""
    try:
        return "{} (signal {})".format(signal.Signals(-rc).name, -rc)
    except ValueError:
        return "signal {}".format(-rc)


def _fault_text(exc):
    """A harness fault as "<type>: <message>", then each note attached to it (the cleanup steps that
    did not complete, which _run_one attaches when an exception ends its wait for a launched leg), so
    a fault report never drops an incomplete cleanup."""
    return "; ".join(["{}: {}".format(type(exc).__name__, exc)] + list(getattr(exc, "__notes__", ())))


def _kill_and_reap(proc, drain_s=_DRAIN_MARGIN_S, killpg=os.killpg):
    """Kill the leg's process GROUP, after its bound or after an exception ended _run_one's wait for it
    (the leg was started as its own session leader, so every descendant that has not itself left the
    group shares it), then drain its output and reap its
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


def _run_one(opf_root, script, args, run_dir, env, bound_s, drain_s=_DRAIN_MARGIN_S, raw=False):
    """Run one subset member isolated against the copied opf/tools/<script>, killed once it has run for
    bound_s seconds. The leg runs as its own session leader (start_new_session), so a timeout kills its
    whole process GROUP and reaps the LEADER: work a hung leg forked does not continue past the kill
    unless the descendant itself left the group with setpgid() or setsid(), the disclosed residual on
    _kill_and_reap (such a survivor is neither killed nor reaped). Returns
    (rc, output, elapsed_s, launched, cleanup): output is the decoded last lines of what the leg wrote,
    or with raw True its WHOLE output (stdout with stderr merged) as the undecoded bytes it wrote, a
    launch failure's text encoded UTF-8. rc is None when the leg was killed at its bound (the bound
    tripped; the leg itself reported nothing), a negative rc is a signal this runner did not send (-N
    for signal N; the leg reported no exit status, and the status cannot tell an outside signal from
    one the leg or its own process group sent itself), launched is False for a harness failure (a
    missing script, an interpreter that could not start) whose rc 2 is this runner's verdict, NEVER an
    outcome the leg reported, whatever text it carries, and cleanup lists the named faults of a kill
    whose cleanup did not complete (_kill_and_reap; empty otherwise). Any OTHER exception that ends
    the wait after the launch (an OSError from the output pipe, a KeyboardInterrupt) is raised as
    itself, never turned into a result, but only after the same kill, reap and pipe close, so a
    launched leg never outlives its runner's error; each cleanup step that did not complete is a note
    on that exception. cwd is run_dir, which is OUTSIDE any git repository and does not contain the
    tools/ tree, so only the copied opf/tools/ is reachable to `python3 -I`."""
    def shaped(stdout):
        return (stdout or b"") if raw else _tail(stdout)

    def harness(message):
        return message.encode("utf-8") if raw else message

    target = opf_root / "tools" / script
    if not target.is_file():
        return 2, harness("missing subset script in the copy: {}".format(target)), 0.0, False, []
    start = time.monotonic()
    try:
        proc = subprocess.Popen(
            [sys.executable, "-I", "-B", str(target), *args],
            cwd=str(run_dir), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    except OSError as exc:
        return (2, harness("could not launch the interpreter: {}".format(exc)), time.monotonic() - start,
                False, [])
    try:
        stdout, _err = proc.communicate(timeout=bound_s)
    except subprocess.TimeoutExpired:
        stdout, cleanup = _kill_and_reap(proc, drain_s)
        return None, shaped(stdout), time.monotonic() - start, True, cleanup
    except BaseException as exc:
        # Any other end of the wait (an OSError reading the pipe, an interrupt) still owns a launched
        # leg: its group is killed, its leader reaped and its pipe closed under the same bounded
        # cleanup, and then the ORIGINAL exception is raised, each cleanup step that did not complete
        # attached to it as a note (_fault_text names them). An exception that is not an Exception
        # (an interrupt) raised by the cleanup itself is never swallowed.
        try:
            _out, cleanup = _kill_and_reap(proc, drain_s)
        except Exception as cleanup_exc:
            cleanup = ["cleanup-error: the cleanup raised {}: {}".format(
                type(cleanup_exc).__name__, cleanup_exc)]
        for fault in cleanup:
            exc.add_note("CLEANUP INCOMPLETE after the leg's {}: {}".format(type(exc).__name__, fault))
        raise
    return proc.returncode, shaped(stdout), time.monotonic() - start, True, []


def _run_subset(opf_root, run_dir, bounds, legs=None):
    """Run every subset member under its own bound; legs restricts the run to those leg names (one
    CI job's part of _CI_JOB_LEGS), None runs them all. Returns a list of
    (name, rc, tail, elapsed_s, launched, bound_s, cleanup). The registration checks have already run
    over the same roster (_run_closure checks them first)."""
    env = _isolated_env()
    results = []
    for name, script, args in _SUBSET:
        if legs is not None and name not in legs:
            continue
        bound = bounds[name][0]
        rc, tail, elapsed, launched, cleanup = _run_one(opf_root, script, args, run_dir, env, bound)
        results.append((name, rc, tail, elapsed, launched, bound, cleanup))
    return results


def _said_cannot_evaluate(row):
    """Whether this result row is a leg's own positive cannot-evaluate declaration: the leg launched,
    exited 2 AND printed the marker in its captured final lines. Anything else a launched leg exits 2
    with is a finding (_report); a row the runner never launched is never the leg's declaration."""
    return bool(row[4]) and row[1] == 2 and _CANNOT_EVALUATE_MARKER in (row[2] or "")


def _report(results):
    """Print one line per leg and the verdict, and return the exit code. A leg that ITSELF exited
    non-zero (a positive status) is a finding (1) unless it exited 2 AND printed CANNOT EVALUATE in its
    final lines (the subset members' positive declaration of a harness error or an expired inner
    bound): exit 2 alone also carries real failures (a helper import failure, a self-check that exits
    2), so an unmarked exit 2 is reported as closure broken, fail-closed. A leg that declared
    cannot-evaluate is reported as LEG CANNOT EVALUATE, and with no finding beside it the gate cannot
    evaluate closure (2), never clean. A leg killed at its bound is reported as the bound
    tripping, a leg ended by a signal this gate did not send (a negative status) by that signal's name,
    and a leg that never launched (launched False: a missing script or an interpreter that could not
    start) as a harness failure, never as a failure the leg reported; in each case, with no finding
    beside it the gate cannot evaluate closure (2), and never reads as clean. A negative status cannot
    tell an outside signal (another process, the OOM killer, an interpreter crash) from one the leg or
    its own process group sent itself (opf.py's self-test kills fixture process groups, so a regression
    there can end its own leg), so the report says so rather than blame the environment. A killed leg
    whose cleanup did not complete (row[6], the named faults from _kill_and_reap: an unreaped leader, a
    pipe still held by an escaped descendant) is reported as CLEANUP INCOMPLETE by name, and never reads
    as clean either. A sweep that ran NO leg proves nothing: an empty results list is refused (2), and
    the OK line is printed only after at least one leg's clean row (the caller separately requires the
    swept legs to be exactly the legs the sweep owes)."""
    if not results:
        print("STANDALONE CLOSURE: NOT EVALUATED (the sweep ran no leg; an empty sweep proves nothing "
              "and never reads as clean)", file=sys.stderr)
        return 2
    failed = [row for row in results
              if row[4] and row[1] is not None and row[1] > 0 and not _said_cannot_evaluate(row)]
    cannot = [row for row in results if _said_cannot_evaluate(row)]
    signalled = [row for row in results if row[4] and row[1] is not None and row[1] < 0]
    unlaunched = [row for row in results if not row[4]]
    timed_out = [row for row in results if row[1] is None]
    uncleaned = [row for row in results if row[6]]
    for row in results:
        name, rc, _tail_text, elapsed, launched, bound, _cleanup = row
        if not launched:
            status = "NOT LAUNCHED (harness failure; the leg reported nothing)"
        elif rc == 0:
            status = "OK ({:.1f}s of its {}s bound)".format(elapsed, bound)
        elif rc is None:
            status = "TIMED OUT (killed at its {}s bound)".format(bound)
        elif rc < 0:
            status = ("ENDED BY {} after {:.1f}s (not this gate's kill; the leg reported no exit "
                      "status, and a negative status cannot tell an outside signal from one the leg or "
                      "its own process group sent itself)".format(_signal_name(rc), elapsed))
        elif _said_cannot_evaluate(row):
            status = "CANNOT EVALUATE rc=2 ({:.1f}s)".format(elapsed)
        else:
            status = "FAILED rc={} ({:.1f}s)".format(rc, elapsed)
        print("  {:38s} {}".format(name, status))
    if failed:
        print("STANDALONE CLOSURE: BROKEN (the isolated opf/ subtree does not verify itself; an exit 2 "
              "without the {} marker counts as a failure, fail-closed):".format(_CANNOT_EVALUATE_MARKER),
              file=sys.stderr)
        for name, rc, tail, _elapsed, _launched, _bound, _cleanup in failed:
            print("  {} (rc={}): {}".format(name, rc, tail.replace("\n", " | ")), file=sys.stderr)
    if cannot:
        print("STANDALONE CLOSURE: LEG CANNOT EVALUATE (each leg below exited 2 and printed the {} "
              "marker: it said it could not evaluate its own checks, so closure was not evaluated "
              "for it):".format(_CANNOT_EVALUATE_MARKER), file=sys.stderr)
        for name, _rc, tail, _elapsed, _launched, _bound, _cleanup in cannot:
            print("  {} (rc=2): {}".format(name, tail.replace("\n", " | ")), file=sys.stderr)
    if unlaunched:
        print("STANDALONE CLOSURE: NOT EVALUATED (a leg below never launched; the failure is the "
              "harness's, never the leg's, whatever text it carries):", file=sys.stderr)
        for name, _rc, tail, _elapsed, _launched, _bound, _cleanup in unlaunched:
            print("  {}: {}".format(name, tail.replace("\n", " | ")), file=sys.stderr)
    if signalled:
        print("STANDALONE CLOSURE: NOT EVALUATED (a signal this gate did not send ended each leg below "
              "before it reported an exit status; the status cannot tell an outside signal, from another "
              "process, the OOM killer or an interpreter crash, from one the leg or its own process group "
              "sent itself; closure was not evaluated for it):", file=sys.stderr)
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
    if cannot or timed_out or signalled or unlaunched or uncleaned:
        return 2
    print("STANDALONE CLOSURE: OK (opf/ verifies itself with no AIQT tree reachable)")
    return 0


def run(root, ceilings=None, legs=None):
    """Materialize opf/ alone and run the subset in isolation. legs restricts the sweep to those leg
    names (one CI job's part of _CI_JOB_LEGS; main() checks the partition before passing one), None
    runs every leg; the registration checks and the bound scan always cover the whole roster. The
    sweep must then have run exactly the legs it owes (legs, or every leg when None): any other swept
    set, an empty one included, is refused (2) before any row is reported, never read as clean.
    Returns 0 (closure holds), 1 (a subset gate
    failed, including a leg that exited 2 without printing CANNOT EVALUATE: closure broken; or
    registration drift, named, found before opf/ is copied, any bound is derived or any leg runs), or 2
    (harness error, no opf/tools/ directory, an opf/tools/ entry that cannot be checked, a temporary
    directory that cannot be created, a copy holding a symlink that resolves outside it, a leg with no
    row in the ceilings passed in, refused before any leg launches, any other exception or a SystemExit
    before, around or after the legs, a leg killed at its bound or ended by a signal, or a leg that
    exited 2 and printed CANNOT EVALUATE and so said it could not evaluate its own checks), fail-closed. ceilings defaults to
    _LEG_CEILING_S (the self-test passes a table missing a row); a leg missing from _LEG_CEILING_S
    itself is registration drift (1), since the roster is held to that table and that table to
    _ROSTER_PIN (_check_roster_registration)."""
    return _run_closure(root, ceilings, legs)[0]


def _run_closure(root, ceilings=None, legs=None):
    """run(), returning (exit code, results, stopped): results is the per-leg rows _report read, or None
    when the run stopped before any leg result, and stopped is then the text naming why (None when the
    legs ran); the self-test's positive leg names each leg it could not evaluate, or the stop, from
    them. The registration checks run FIRST, over the real roster, before opf/ is copied, any bound is
    derived or any leg launches, so every drift shape _check_roster_registration names (a row removed,
    added or repeated, a leg renamed, a row removed or added together with its ceiling row, a script
    that is not a plain file name or that the copy leaves out, a script opf/tools/ does not hold as a
    regular file of its own: missing, a symlink, or resolving outside it) is registration drift (1),
    never a bound that cannot be derived. They read opf/ only to check each such script is a regular
    file in opf/tools/, and they do so whenever opf/tools/ is a directory, so a roster script removed
    from it, opf.py included, is drift; drift in the roster itself is named even when there is no
    opf/tools/, and with a clean roster no opf/tools/ is cannot evaluate (2). Only those checks' own
    assertions (_registration_drift) are a definite finding here. A copy refused for a symlink that
    resolves outside it (_materialize) is cannot evaluate (2), named with each such link. A sweep
    whose swept legs are not exactly the legs it owes (_job_legs over legs; an empty sweep included)
    is refused, cannot evaluate (2), naming the missing and unexpected legs, no row reported. Any other
    exception or a SystemExit, from a registration check (an opf/tools/ entry that cannot be checked
    included), the copy, the bound scan, the legs or the report of their results (a leg result it
    cannot read included), an AssertionError included, is a harness fault: named, cannot evaluate (2),
    never a finding, never a traceback and never an exit the raiser chose; one raised while a leg ran
    is named with each cleanup step _run_one could not complete after it (_fault_text). The routing
    vectors rebind the module names this looks up
    (_harness_routing_self_test), so the path they hold through run() is this one."""
    tmp = None
    try:
        opf_src = Path(root) / "opf"
        tools = opf_src / "tools"
        present = tools.is_dir()
        drift = _registration_drift(_SUBSET, tools if present else None)
        if drift:
            print("STANDALONE CLOSURE: REGISTRATION DRIFT (a definite finding: the closure roster fails "
                  "each registration check below on its own assertion; no leg ran):", file=sys.stderr)
            for name in drift:
                print("  registration drift: {}".format(name), file=sys.stderr)
            return 1, None, "registration drift: {}".format("; ".join(drift))
        if not present:
            stopped = "opf/tools/ not found under {} (cannot evaluate closure)".format(root)
            print("error: " + stopped, file=sys.stderr)
            return 2, None, stopped
        try:
            tmp = Path(tempfile.mkdtemp(prefix="opf-closure-"))
        except OSError as exc:
            stopped = "could not create the temporary directory for the isolated opf/ copy: {}".format(
                exc)
            print("error: {} (cannot evaluate closure)".format(stopped), file=sys.stderr)
            return 2, None, stopped
        try:
            opf_root = _materialize(opf_src, tmp)
        except (OSError, _CopyRefused) as exc:
            stopped = "could not materialize the isolated opf/ copy: {}".format(exc)
            print("error: " + stopped, file=sys.stderr)
            return 2, None, stopped
        try:
            bounds = _leg_bounds(opf_root / "tools", ceilings)
        except _BoundError as exc:
            stopped = "cannot derive the per-leg bounds: {}".format(exc)
            print("error: " + stopped, file=sys.stderr)
            return 2, None, stopped
        results = (_run_subset(opf_root, tmp, bounds) if legs is None
                   else _run_subset(opf_root, tmp, bounds, legs))
        # The sweep must have run exactly the legs it owes, in roster order: a filter that dropped a
        # leg (every leg included) or swept one another job holds would otherwise still reach the OK
        # line. Refused before any row is reported, so no sweep result is trusted (fail-closed).
        expected = _job_legs(legs)
        swept = [row[0] for row in results]
        if swept != expected:
            missing = [name for name in expected if name not in swept]
            unexpected = [name for name in swept if name not in expected]
            detail = ("missing: {}; unexpected: {}".format(
                ", ".join(missing) or "none", ", ".join(unexpected) or "none")
                if missing or unexpected else "the legs were swept repeatedly or out of roster order")
            stopped = ("the sweep ran {} leg(s), not exactly the {} leg(s) it must run ({}); no sweep "
                       "result is trusted (cannot evaluate)".format(len(swept), len(expected), detail))
            print("error: " + stopped, file=sys.stderr)
            return 2, None, stopped
        return _report(results), results, None
    except (Exception, SystemExit) as exc:
        stopped = "harness fault before, around or after the legs ({})".format(_fault_text(exc))
        print("STANDALONE CLOSURE: NOT EVALUATED ({}; never a finding, whatever it carries)".format(
            stopped), file=sys.stderr)
        return 2, None, stopped
    finally:
        if tmp is not None:
            shutil.rmtree(tmp, ignore_errors=True)


def _bounds_self_test(root):
    """The per-leg bounds hold the rule on the real tree, fit the CI job, and every rule here is
    discriminating (each has a RED case beside its PASS). Returns the derived bounds; raises
    _CheckFailure naming the first broken property. Each call into the code under test goes through
    _under_test (a crash there is the named check's failure); derive() lets the bound derivation's own
    refusal, a _BoundError (a source that cannot be read or parsed), through as cannot evaluate (2)
    unless the check expects it."""

    def derive(check, func, *args, **kwargs):
        return _under_test(check, func, *args, refuses=(_BoundError,), **kwargs)

    def ci_limit(check, workflow_text, job):
        return _under_test(check, _ci_job_limit_s, workflow_text, job)

    names = [name for name, _script, _args in _SUBSET]
    if sorted(_LEG_CEILING_S) != sorted(names):
        raise _CheckFailure("closure/leg-ceilings-match-the-subset")
    tools_dir = Path(root) / "opf" / "tools"
    required = derive("leg-bounds-cover-their-work", _leg_bounds, tools_dir)
    live = dict((name, row[0]) for name, row in required.items())
    # The expectation is recombined here from the ceiling table and the wait scan, independently of
    # _leg_bounds, so a _leg_bounds that drops a term (the headroom, the wait sum) is a named fault,
    # not a tautology. Dropping the floor changes no LIVE bound while it equals the launch margin; the
    # lowered-margin vector below is what discriminates the floor term.
    needs, waits = {}, {}
    for name, script, _args in _SUBSET:
        sources = [tools_dir / script]
        if script == "opf.py":
            sources.extend(tools_dir / module for module in derive(
                "leg-bounds-cover-their-work", _self_test_modules, tools_dir / script))
        waits[name] = math.ceil(sum(derive("leg-bounds-cover-their-work", _declared_wait_sites,
                                           sources)))
        needs[name] = max(_MIN_LEG_BOUND_S, _LOAD_HEADROOM * _LEG_CEILING_S[name],
                          waits[name] + _LAUNCH_MARGIN_S, _LEG_BOUND_FLOOR_S.get(name, 0))
    faults = _under_test("leg-bounds-cover-their-work", _bound_faults, live, needs)
    if faults:
        raise _CheckFailure("closure/leg-bounds-cover-their-work: " + "; ".join(faults))
    for name in names:
        bound, ceiling, declared = required[name]
        floor_note = ("; config-lane floor {}s".format(_LEG_BOUND_FLOOR_S[name])
                      if name in _LEG_BOUND_FLOOR_S else "")
        print("  bound {:38s} {}s (ceiling {}s x {}; literal wait sites sum {}s + {}s{})".format(
            name, bound, ceiling, _LOAD_HEADROOM, declared, _LAUNCH_MARGIN_S, floor_note))
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
        got = _under_test("leg-bounds-mutant", _bound_faults, mutated, needs)
        if got != expected:
            raise _CheckFailure("closure/leg-bounds-mutant (need{:+d}): {!r}".format(delta, got))
    print("RED mutant bound (one second below the independent need of {}) -> named fault; at and one "
          "above the need -> clean".format(leg))
    # The floor term, discriminated. The launch margin passed to _leg_bounds is LOWERED to one second
    # below _MIN_LEG_BOUND_S (the module constant untouched): every leg whose headroom and wait-sum
    # terms both sit below the floor must then be raised to exactly the floor, so a _leg_bounds that
    # dropped the floor term is a named fault here. At the floor and one second above it the wait-sum
    # term reaches the floor by itself (the max is tested at and beside its boundary). The lowered case
    # must raise at least one leg, else the vector discriminates nothing: a named fault, never a pass.
    # The per-leg floors (_LEG_BOUND_FLOOR_S) are left out here, an empty table, since they would
    # mask this term for every floored leg; their own vectors below discriminate them.
    floored = []
    for margin in (_MIN_LEG_BOUND_S - 1, _MIN_LEG_BOUND_S, _MIN_LEG_BOUND_S + 1):
        lowered = dict((name, row[0]) for name, row in
                       derive("floor-raises-a-bound", _leg_bounds, tools_dir,
                              launch_margin_s=margin, bound_floors={}).items())
        others = dict((name, max(_LOAD_HEADROOM * _LEG_CEILING_S[name], waits[name] + margin))
                      for name in names)
        want = dict((name, max(_MIN_LEG_BOUND_S, others[name])) for name in names)
        raised = sorted(name for name in names if others[name] < _MIN_LEG_BOUND_S)
        floor_faults = _under_test("floor-raises-a-bound", _bound_faults, lowered, want) + [
            "{}: bound {}s, not raised to exactly the {}s floor".format(name, lowered[name],
                                                                         _MIN_LEG_BOUND_S)
            for name in raised if lowered[name] != _MIN_LEG_BOUND_S]
        if floor_faults:
            raise _CheckFailure("closure/floor-raises-a-bound (launch margin {}s): {}".format(
                margin, "; ".join(floor_faults)))
        if margin < _MIN_LEG_BOUND_S:
            if not raised:
                raise _CheckFailure("closure/floor-not-discriminated: at a launch margin of {}s no "
                                    "leg's other terms sit below the {}s floor".format(
                                        margin, _MIN_LEG_BOUND_S))
            floorless = sorted(fault.split(":", 1)[0] for fault in _under_test(
                "floorless-bounds-not-named", _bound_faults, others, want))
            if floorless != raised:
                raise _CheckFailure("closure/floorless-bounds-not-named: named {!r}, want {!r}".format(
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
    flat_faults = _under_test("flat-600-refusals-do-not-match-the-table", _bound_faults,
                              dict((name, 600) for name in names), needs)
    flat_named = sorted(fault.split(":", 1)[0] for fault in flat_faults)
    if flat_named != under:
        raise _CheckFailure("closure/flat-600-refusals-do-not-match-the-table: named {!r}, "
                            "table-derived {!r}".format(flat_named, under))
    if under:
        print("RED flat-600 -> " + "; ".join(flat_faults))
    else:
        print("PASS closure/flat-600-covers-every-leg (no table-derived need exceeds 600s)")
    # The per-leg floor term, discriminated around its boundary. The control derivation (an empty
    # floor table) reproduces the bounds the three other terms alone give: for every live floor row
    # those must sit BELOW the floor (a floor the other terms already reach is dead weight, refused
    # so the row is re-fitted or removed), and opf-homes-selftest's CI runs tripped exactly such a
    # control bound, so a _leg_bounds that drops the floor term reproduces the tripped bound and
    # fails here. The live bound must be exactly the floor; a doctored floor one second above the
    # control raises the bound to exactly that floor, and at and one below the control it changes
    # nothing (the max is tested at and beside its boundary). Each probe holds every row at once, one
    # derivation per probe, and judges every row's leg on its own (a leg's bound reads only its own
    # row). A row naming no subset leg, a non-int value and a non-positive value are each refused
    # (fail-closed).
    if not _LEG_BOUND_FLOOR_S:
        raise _CheckFailure("closure/bound-floor-not-discriminated: the floor table is empty")
    floorless = derive("bound-floor-raises-the-bound", _leg_bounds, tools_dir, bound_floors={})
    for name in sorted(_LEG_BOUND_FLOOR_S):
        floor, control = _LEG_BOUND_FLOOR_S[name], floorless[name][0]
        if control >= floor:
            raise _CheckFailure("closure/bound-floor-not-discriminated: the other terms of {} "
                                "reach {}s, at or above its {}s floor".format(name, control, floor))
        if live[name] != floor:
            raise _CheckFailure("closure/bound-floor-raises-the-bound: {} bound {}s, want its {}s "
                                "floor".format(name, live[name], floor))
    for delta, raises in ((-1, False), (0, False), (1, True)):
        probes = dict((name, floorless[name][0] + delta) for name in _LEG_BOUND_FLOOR_S)
        probed = derive("bound-floor-raises-the-bound", _leg_bounds, tools_dir, bound_floors=probes)
        for name in sorted(probes):
            got, want = probed[name][0], probes[name] if raises else floorless[name][0]
            if got != want:
                raise _CheckFailure("closure/bound-floor-raises-the-bound ({} floor {}s): bound "
                                    "{}s, want {}s".format(name, probes[name], got, want))
    floor_leg = sorted(_LEG_BOUND_FLOOR_S)[0]
    # Each bad row must be refused AND the refusal must name the row's leg: a _BoundError raised for
    # any other reason would otherwise pass these vectors. The string probe mirrors the live floor so
    # it stays a plausible mistyping; the bool probe holds the value check to type, not isinstance
    # (True would otherwise read as the int 1 and silently stop raising the bound it records).
    for label, table, named in (
            ("no-subset-leg", {"opf-no-such-leg": 100}, "opf-no-such-leg"),
            ("non-int", dict(_LEG_BOUND_FLOOR_S,
                             **{floor_leg: str(_LEG_BOUND_FLOOR_S[floor_leg])}), floor_leg),
            ("bool", dict(_LEG_BOUND_FLOOR_S, **{floor_leg: True}), floor_leg),
            ("non-positive", dict(_LEG_BOUND_FLOOR_S, **{floor_leg: 0}), floor_leg)):
        try:
            derive("bound-floor-bad-row-not-refused", _leg_bounds, tools_dir, bound_floors=table)
        except _BoundError as exc:
            if named not in str(exc):
                raise _CheckFailure("closure/bound-floor-bad-row-not-refused ({}): refused without "
                                    "naming {}: {}".format(label, named, exc))
            continue
        raise _CheckFailure("closure/bound-floor-bad-row-not-refused ({})".format(label))
    print("RED an empty floor table -> the bounds the other terms alone give ({}), each below its "
          "leg's floor (opf-homes-selftest's the tripped shape); a floor one second above them -> "
          "raises the bound to "
          "exactly that floor; at and one below them -> unchanged; a row naming no subset leg, a "
          "non-int, a bool and a non-positive value -> each refused naming the row".format(
              ", ".join("{} {}s".format(name, floorless[name][0])
                        for name in sorted(_LEG_BOUND_FLOOR_S))))
    ceilings = dict(_LEG_CEILING_S)
    del ceilings[leg]
    try:
        derive("missing-ceiling-not-refused", _leg_bounds, tools_dir, ceilings)
    except _BoundError:
        print("RED missing-ceiling -> cannot derive the bound (fail-closed)")
    else:
        raise _CheckFailure("closure/missing-ceiling-not-refused")
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
            code = _under_test("missing-row-refused-by-name-before-any-launch", run, root, ceilings)
    finally:
        subprocess.Popen = real_popen
    said = out.getvalue()
    if code != 2 or "leg {} has no ceiling in _LEG_CEILING_S".format(last) not in said or launches:
        raise _CheckFailure("closure/missing-row-refused-by-name-before-any-launch (exit {}, {} "
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
            got = _under_test("declared-wait-scan", _declared_wait_s, [probe])
            if got != want:
                raise _CheckFailure("closure/declared-wait-scan: {!r} -> {!r}, want {!r}".format(
                    source, got, want))
        probe.write_text("def f(:\n", encoding="utf-8")
        try:
            derive("declared-wait-scan-unparseable-not-refused", _declared_wait_s, [probe])
        except _BoundError:
            pass
        else:
            raise _CheckFailure("closure/declared-wait-scan-unparseable-not-refused")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("PASS closure/declared-wait-scan (literal SITES sum: a repeated site counts once and "
          "arithmetic is mis-read in either direction, so the sum is a deliberate deadline, never an "
          "end-to-end budget; an under-read deadline fails closed)")
    # The partition of the legs across the CI jobs, fail-closed BEFORE any per-job fit is read: a
    # leg in no job, a leg in more than one, and a job entry naming no subset leg are each a named
    # fault here (and refuse a --ci-job run in main() before any leg launches).
    partition_faults = _under_test("ci-jobs-partition-the-subset", _partition_fault)
    if partition_faults:
        raise _CheckFailure("closure/ci-jobs-partition-the-subset: " + "; ".join(partition_faults))
    print("PASS closure/ci-jobs-partition-the-subset: every subset leg is in exactly one CI job ({})"
          .format(", ".join("{} holds {}".format(job, len(_CI_JOB_LEGS[job]))
                            for job in _CI_JOBS)))
    # The partition check is discriminating: each fault shape is named on a doctored table (a leg
    # in 0 holders and in 2, each side of the exactly-one boundary, and a name that is no leg), and
    # the live table yields none, so a _partition_fault that stops naming a shape is a named fault.
    first_leg = _SUBSET[0][0]
    holder = next(job for job in _CI_JOBS if first_leg in _CI_JOB_LEGS[job])
    for label, table, want in (
            ("live-table", dict(_CI_JOB_LEGS), []),
            ("leg-in-no-job",
             {job: _CI_JOB_LEGS[job] - {first_leg} for job in _CI_JOBS},
             ["{}: in 0 CI jobs (none)".format(first_leg)]),
            ("leg-in-both-jobs",
             {job: _CI_JOB_LEGS[job] | {first_leg} for job in _CI_JOBS},
             ["{}: in {} CI jobs ({})".format(first_leg, len(_CI_JOBS), ", ".join(_CI_JOBS))]),
            ("name-that-is-no-leg",
             {job: (_CI_JOB_LEGS[job] | {"no-such-leg"}) if job == holder else _CI_JOB_LEGS[job]
              for job in _CI_JOBS},
             ["{}: no-such-leg is not a subset leg".format(holder)])):
        got = _under_test("partition-check-discriminates", _partition_fault, table)
        if got != want:
            raise _CheckFailure("closure/partition-check-discriminates ({}): {!r}, want {!r}".format(
                label, got, want))
    print("RED a leg in no CI job, one in both, and a job entry naming no subset leg -> each a named "
          "partition fault; the live table -> none")
    # The worst case of EACH closure CI job must fit that job's own time limit: a job runs the
    # --self-test step (one sweep over its own legs in the positive leg, the fast vectors, and, only
    # in the job that holds the flip's leg, a negative leg bounded by the opf-tooling bound) and
    # then the live gate step (one more sweep over the same legs).
    text = (Path(root) / _CI_WORKFLOW).read_text(encoding="utf-8")
    job_totals, job_limits = {}, {}
    for job in _CI_JOBS:
        job_set = _CI_JOB_LEGS[job]
        in_job = [name for name in names if name in job_set]
        limit, reason = ci_limit("ci-job-limit-cannot-evaluate", text, job)
        if limit is None:
            raise _CannotEvaluate("closure/ci-job-limit-cannot-evaluate: " + reason)
        oversize = _under_test("leg-bound-exceeds-the-ci-job", _oversize_legs, live, limit, job_set)
        if oversize:
            raise _CheckFailure("closure/leg-bound-exceeds-the-ci-job ({}): ".format(job)
                                + "; ".join(oversize))
        # The expectation is recombined here, independently of _job_worst_case_s: every leg of both
        # sweeps and (where it runs) the negative leg can be killed, and each kill pays ONE cleanup
        # deadline (_DRAIN_MARGIN_S, shared by the drain and the leader reap in _kill_and_reap), so
        # a model that drops or halves the cleanup term is a named fault, not a tautology.
        subset_sum = sum(live[name] for name in in_job)
        negative = required[_FLIP_LEG][0] if _FLIP_LEG in job_set else 0
        kills = 2 * len(in_job) + (1 if _FLIP_LEG in job_set else 0)
        cleanup_free = 2 * subset_sum + negative + _VECTOR_ALLOWANCE_S + _CI_SETUP_ALLOWANCE_S
        want_total = cleanup_free + kills * _DRAIN_MARGIN_S
        total = _under_test("fit-model-counts-one-cleanup-deadline-per-kill", _job_worst_case_s,
                            live, negative, job_set)
        if total != want_total:
            raise _CheckFailure("closure/fit-model-counts-one-cleanup-deadline-per-kill ({}): {}s, "
                                "recombined {}s".format(job, total, want_total))
        fault = _under_test("bounds-fit-the-ci-job", _fit_fault, live, negative, limit, job_set, job)
        if fault is not None:
            raise _CheckFailure("closure/bounds-fit-the-ci-job: " + fault)
        print("PASS closure/bounds-fit-the-ci-job: 2 x subset sweep {}s over {} legs + negative leg "
              "{}s + {} cleanup deadlines x {}s + vectors {}s + setup {}s = {}s <= {}s ({} job)".format(
                  subset_sum, len(in_job), negative, kills, _DRAIN_MARGIN_S, _VECTOR_ALLOWANCE_S,
                  _CI_SETUP_ALLOWANCE_S, total, limit, job))
        # The fit check's cleanup term is discriminating per job: a job limit that covers every bound
        # but not the cleanup deadlines is refused, as is one second below the worst case; at the
        # worst case it fits.
        for probe_limit, fits in ((want_total, True), (want_total - 1, False),
                                  (cleanup_free, False)):
            if (_under_test("fit-check-cleanup-term", _fit_fault, live, negative, probe_limit,
                            job_set, job) is None) != fits:
                raise _CheckFailure("closure/fit-check-cleanup-term ({}, limit {}s, worst case "
                                    "{}s)".format(job, probe_limit, want_total))
        job_totals[job], job_limits[job] = total, limit
    print("RED for each job: a limit covering every bound but not its cleanup deadlines -> refused; "
          "one second below its worst case -> refused; at it -> fits")
    # The parser vectors below exercise one job's header and limit; every job runs through the same
    # reader above.
    limit = job_limits[_CI_JOBS[0]]
    total = job_totals[_CI_JOBS[0]]
    # A single leg bound over the job limit is refused by name, never shrunk: one second over is named,
    # at and one under the limit are not (each side of its boundary tested).
    for delta, want_named in ((-1, False), (0, False), (1, True)):
        probe_bounds = dict(live)
        probe_bounds[leg] = limit + delta
        named = [entry for entry in _under_test("oversize-leg-refusal", _oversize_legs, probe_bounds,
                                                limit)
                 if entry.startswith(leg + ":")]
        if bool(named) != want_named:
            raise _CheckFailure("closure/oversize-leg-refusal (limit{:+d}): {!r}".format(delta, named))
    print("RED a leg bound one second over the job limit -> refused by name, never shrunk; at and one "
          "under the limit -> not oversize")
    header = "  {}:\n".format(_CI_JOBS[0])
    tight = text.replace(header, header + "    timeout-minutes: 30\n", 1)
    if (tight == text or ci_limit("ci-job-30-minutes-not-refused", tight, _CI_JOBS[0]) != (1800, None)
            or total <= 1800):
        raise _CheckFailure("closure/ci-job-30-minutes-not-refused")
    absent_limit, absent_reason = ci_limit("ci-job-absent-not-refused-by-name",
                                           text.replace(header, "  renamed-job:\n", 1), _CI_JOBS[0])
    if absent_limit is not None or _CI_JOBS[0] not in (absent_reason or ""):
        raise _CheckFailure("closure/ci-job-absent-not-refused-by-name")
    bad_limit, bad_reason = ci_limit(
        "ci-job-non-integer-not-refused-by-name",
        text.replace(header, header + "    timeout-minutes: thirty\n", 1), _CI_JOBS[0])
    if bad_limit is not None or "thirty" not in (bad_reason or ""):
        raise _CheckFailure("closure/ci-job-non-integer-not-refused-by-name")
    # Job and step limits are DISTINCT: a step's own timeout-minutes (first key of its `- ` line or
    # not) is parsed and validated, kills only that step in CI, and never caps the budget the job
    # gives the gate steps, so a short setup-step timeout cannot make this fit check reject the job.
    default_s = 60 * _CI_DEFAULT_JOB_MINUTES
    step = "      - name: OPF standalone-closure gate\n"
    reordered = text.replace(
        step, "      - timeout-minutes: 1\n        name: OPF standalone-closure gate\n", 1)
    if reordered == text or ci_limit("ci-step-timeout-capped-the-job-budget",
                                     reordered, _CI_JOBS[0]) != (default_s, None):
        raise _CheckFailure("closure/ci-step-timeout-capped-the-job-budget (first key)")
    stepwise = text.replace(step, step + "        timeout-minutes: 1\n", 1)
    if stepwise == text or ci_limit("ci-step-timeout-capped-the-job-budget",
                                    stepwise, _CI_JOBS[0]) != (default_s, None):
        raise _CheckFailure("closure/ci-step-timeout-capped-the-job-budget")
    # The fail-closed line scan: a key or step this parser does not evaluate but the YAML loader
    # GitHub runs would (a quoted key, an escape-carrying key, an alias step) is refused by name,
    # never scanned around to the default; block-scalar CONTENT is data, not keys, and is skipped.
    quoted = text.replace(header, header + '    "timeout-minutes": 1\n', 1)
    quoted_limit, quoted_reason = ci_limit("ci-quoted-timeout-key-not-refused-by-name", quoted,
                                           _CI_JOBS[0])
    if quoted_limit is not None or "timeout-minutes" not in (quoted_reason or ""):
        raise _CheckFailure("closure/ci-quoted-timeout-key-not-refused-by-name")
    escaped = text.replace(header, header + '    "timeout\\u002dminutes": 1\n', 1)
    escaped_limit, escaped_reason = ci_limit("ci-escaped-timeout-key-not-refused-by-name", escaped,
                                             _CI_JOBS[0])
    if escaped_limit is not None or "u002dminutes" not in (escaped_reason or ""):
        raise _CheckFailure("closure/ci-escaped-timeout-key-not-refused-by-name")
    aliased = text.replace(step, "      - *closure_gate\n" + step, 1)
    alias_limit, alias_reason = ci_limit("ci-alias-step-not-refused-by-name", aliased, _CI_JOBS[0])
    if aliased == text or alias_limit is not None or "*closure_gate" not in (alias_reason or ""):
        raise _CheckFailure("closure/ci-alias-step-not-refused-by-name")
    blocky = text.replace(
        step, "      - run: |\n          echo widen\n          \"timeout-minutes\": 1\n" + step, 1)
    if blocky == text or ci_limit("ci-block-scalar-content-misread-as-a-key",
                                  blocky, _CI_JOBS[0]) != (default_s, None):
        raise _CheckFailure("closure/ci-block-scalar-content-misread-as-a-key")
    # The job header is placed structurally: a line that reads like the header but is not a direct
    # key of the top-level jobs: mapping declares no job, so with the real job renamed away it must be
    # refused naming the job, never read for a limit. Each entry is (check, workflow text, the text
    # its refusal must name). The header text inside a top-level block scalar (the real workflow, and
    # the bare reproduction) or under another top-level key, a jobs mapping indented one column so the
    # header line sits one level deeper (inside another job) and one indented three columns so it sits
    # between jobs: and its keys, and a header-shaped line inside a multi-line flow, quoted or plain
    # scalar, or split off by a line break a YAML loader need not read as one, are each refused.
    renamed = text.replace(header, "  renamed-job:\n", 1)
    if renamed == text:
        raise _CheckFailure("closure/ci-header-outside-jobs-not-refused: the real workflow has no {} "
                            "header to rename".format(_CI_JOBS[0]))
    body = header + "    timeout-minutes: 30\n"
    for check, placed_text, want_named in (
            ("ci-header-in-a-top-level-block-scalar-read-as-a-job",
             "x-note: |\n" + body + renamed, _CI_JOBS[0]),
            ("ci-header-in-a-top-level-block-scalar-read-as-a-job",
             "name: |\n" + header + "    timeout-minutes: 360\n", "no top-level jobs: key"),
            ("ci-header-under-a-non-jobs-key-read-as-a-job", "env:\n" + body + renamed, _CI_JOBS[0]),
            ("ci-header-nested-deeper-in-jobs-read-as-a-job",
             "jobs:\n other:\n" + header + "   timeout-minutes: 30\n", _CI_JOBS[0]),
            ("ci-header-nested-deeper-in-jobs-read-as-a-job",
             "jobs:\n   other:\n     runs-on: ubuntu-latest\n" + body, "neither a key of the jobs"),
            ("ci-header-in-a-multi-line-flow-scalar-read-as-a-job",
             "x-note: [\n" + header + "  ]\n" + renamed, "x-note: ["),
            ("ci-header-in-a-multi-line-quoted-scalar-read-as-a-job",
             'x-note: "a\n' + header + '  b"\n' + renamed, 'x-note: "a'),
            ("ci-header-in-a-continued-plain-scalar-read-as-a-job",
             "x-note: a\n" + body + renamed, "deeper than the single-line value"),
            ("ci-header-after-a-non-yaml-line-break-read-as-a-job",
             "x-note: a\u2028" + body + renamed, "'\\u2028'")):
        got_limit, got_reason = ci_limit(check, placed_text, _CI_JOBS[0])
        if got_limit is not None or want_named not in (got_reason or ""):
            raise _CheckFailure("closure/{}: {!r}".format(check, (got_limit, got_reason)))
    # The same header as a direct jobs: key is read, whatever the jobs mapping's own indentation, and
    # a comment after its colon is no value.
    for check, placed_text in (
            ("ci-direct-jobs-key-not-read",
             "jobs:\n other:\n  runs-on: ubuntu-latest\n {}:\n  timeout-minutes: 30\n".format(
                 _CI_JOBS[0])),
            ("ci-direct-jobs-key-not-read", "jobs:\n  {}: # gate\n    timeout-minutes: 30\n".format(
                _CI_JOBS[0]))):
        got = ci_limit(check, placed_text, _CI_JOBS[0])
        if got != (1800, None):
            raise _CheckFailure("closure/{}: {!r}".format(check, got))
    print("RED the {} header only inside a top-level block scalar, only under a non-jobs top-level "
          "key, nested deeper inside jobs:, inside a multi-line flow, quoted or plain scalar, or after "
          "a line break a YAML loader need not read -> each refused by name, never read for a limit; "
          "the same header as a direct jobs: key (one-column jobs mapping, trailing comment) -> read; "
          "the real workflow's six jobs -> read above".format(_CI_JOBS[0]))
    # Each refusal of the header reader, by name, on an input it alone stops: each entry is (check,
    # workflow text, the text its refusal must name). With that refusal removed the input reads a
    # limit or reaches another refusal, so each entry fails without it. Whitespace is YAML's: only
    # spaces indent and only spaces and tabs separate, so a no-break or ideographic space next to a
    # job header is part of the line's text, and the line is no key. A job is a mapping: a sequence
    # (of mappings or of plain items) or no keys at all as its value is refused. Every key of a
    # mapping and every entry of a sequence sits at its first one's indentation: a job key over- or
    # under-indented against its siblings, a step key under-indented against the step's first key,
    # and a sequence entry under-indented against the first entry are each refused (each of these
    # read the default limit, or the job's own timeout, before).
    minimal = "jobs:\n  {}:\n    runs-on: ubuntu-latest\n".format(_CI_JOBS[0])
    for check, placed_text, want_named in (
            ("ci-tab-before-content-read", "jobs:\n  {}:\n\t  timeout-minutes: 30\n".format(_CI_JOBS[0]),
             "has a tab before its content"),
            ("ci-column-0-sequence-read", "- x\n" + minimal, "opens a sequence at column 0"),
            ("ci-line-indented-before-any-top-level-key-read", "  x: 1\n" + minimal,
             "is indented before any top-level key"),
            ("ci-second-top-level-jobs-key-read",
             minimal + "jobs:\n  other:\n    runs-on: ubuntu-latest\n", "is a second top-level jobs: key"),
            ("ci-jobs-value-not-a-mapping-read", "jobs: |\n" + minimal[len("jobs:\n"):],
             "gives jobs: a value that is not a mapping"),
            ("ci-job-value-not-a-mapping-read", "jobs:\n  {}: x\n".format(_CI_JOBS[0]),
             "gives the {} job a value that is not a mapping".format(_CI_JOBS[0])),
            ("ci-duplicate-job-header-read",
             minimal + "  {}:\n    runs-on: ubuntu-latest\n".format(_CI_JOBS[0]),
             "appears 2 times as a key of the top-level jobs: mapping"),
            ("ci-sequence-entry-in-the-jobs-mapping-read", minimal + "  - x\n",
             "neither a key of the jobs"),
            ("ci-unicode-space-before-the-header-read",
             "jobs:\n  \xa0{}:\n    timeout-minutes: 30\n".format(_CI_JOBS[0]),
             "is not a form this parser places"),
            ("ci-unicode-space-before-the-header-read",
             "jobs:\n  \u3000{}:\n    timeout-minutes: 30\n".format(_CI_JOBS[0]),
             "is not a form this parser places"),
            ("ci-unicode-space-after-the-header-read",
             "jobs:\n  {}:\xa0\n    timeout-minutes: 30\n".format(_CI_JOBS[0]),
             "is not a form this parser places"),
            ("ci-unicode-space-line-read-as-blank", minimal + "\xa0\n",
             "is not a form this parser places"),
            ("ci-sequence-as-the-job-value-read",
             "jobs:\n  {}:\n    - timeout-minutes: 30\n      runs-on: ubuntu-latest\n".format(
                 _CI_JOBS[0]),
             "gives the {} job a sequence, not a mapping".format(_CI_JOBS[0])),
            ("ci-sequence-as-the-job-value-read", "jobs:\n  {}:\n    - lint\n".format(_CI_JOBS[0]),
             "gives the {} job a sequence, not a mapping".format(_CI_JOBS[0])),
            ("ci-empty-job-value-read",
             "jobs:\n  {}:\n  other:\n    runs-on: ubuntu-latest\n".format(_CI_JOBS[0]),
             "the {} job has no keys".format(_CI_JOBS[0])),
            ("ci-empty-job-value-read", "jobs:\n  {}:\n".format(_CI_JOBS[0]),
             "the {} job has no keys".format(_CI_JOBS[0])),
            ("ci-over-indented-key-read", minimal + "    env:\n      A: x\n     timeout-minutes: 30\n",
             "is at no indentation an open mapping or sequence gives"),
            ("ci-under-indented-key-read",
             "jobs:\n  {}:\n      runs-on: ubuntu-latest\n    timeout-minutes: 30\n".format(
                 _CI_JOBS[0]),
             "is at no indentation an open mapping or sequence gives"),
            ("ci-under-indented-key-read",
             minimal + "    steps:\n      - run: echo ok\n       name: x\n    timeout-minutes: 30\n",
             "is at no indentation an open mapping or sequence gives"),
            ("ci-under-indented-entry-read", minimal + "    needs:\n      - lint\n     - test\n",
             "is at no indentation an open mapping or sequence gives")):
        got_limit, got_reason = ci_limit(check, placed_text, _CI_JOBS[0])
        if got_limit is not None or want_named not in (got_reason or ""):
            raise _CheckFailure("closure/{}: {!r}".format(check, (got_limit, got_reason)))
    print("RED a tab before a line's content, a sequence at column 0, a line indented before any "
          "top-level key, a second top-level jobs: key, a value of jobs: or of the {} job that is not "
          "a mapping (a value on its line, a sequence, no keys), the {} header twice, a sequence "
          "entry in the jobs mapping, a no-break or ideographic space before or after the header, a "
          "line holding only a no-break space, and a key over- or under-indented against its "
          "siblings or an entry under-indented against its first -> each refused by its own "
          "name".format(_CI_JOBS[0], _CI_JOBS[0]))
    # Value forms, on a minimal job: each entry is (check, job body, the limit it must read, or None
    # with the text its refusal must name). A timeout-minutes value is validated before any
    # block-scalar skip; a value that opens structure this parser does not read (an alias, a flow
    # collection) is refused by name; a comment begins only at whitespace then `#`; a leading zero is
    # refused; a quoted list item is not a plain-scalar item, an unquoted one is; and an indentless
    # step sequence (its `- ` at the job keys' own column) keeps a step's timeout off the job budget,
    # and a job key after it ends it and is read.
    # The allowlist holds each form to its own grammar: a double-quoted scalar (quality.yml uses none,
    # so `\q`, which YAML defines no meaning for, is never read as an escape), a malformed block-scalar
    # header (`|++`, `|0`), one with an indicator (`|-`, `>`), a control character in a plain or
    # single-quoted scalar,
    # a sequence entry not written `- ` then content (`-  key`, `- - item`), a flow sequence with an
    # empty item, a no-break space before a key, after its colon or after a `|` header, and a
    # no-break or ideographic space ending a value or right after the space that follows its colon
    # (only spaces and tabs are stripped from a value, so the space stays in it) are each refused
    # by name; a bare `|` with a comment, the `''` escape of a single-quoted
    # scalar, a tab separating a value and its comment, and a one-line flow sequence of plain items
    # are read.
    for check, body, want_limit, want_named in (
            ("ci-double-quoted-invalid-escape-read", '    steps:\n      - run: "echo \\q"\n', None,
             "carries the value " + repr('"echo \\q"')),
            ("ci-double-quoted-scalar-read", '    steps:\n      - run: "echo ok"\n', None,
             "carries the value " + repr('"echo ok"')),
            ("ci-malformed-block-header-read", "    steps:\n      - run: |++\n          echo ok\n",
             None, "carries the value '|++'"),
            ("ci-malformed-block-header-read", "    steps:\n      - run: |0\n          echo ok\n",
             None, "carries the value '|0'"),
            ("ci-indicated-block-header-read", "    steps:\n      - run: |-\n          echo ok\n",
             None, "carries the value '|-'"),
            ("ci-indicated-block-header-read", "    steps:\n      - run: >\n          echo ok\n",
             None, "carries the value '>'"),
            ("ci-control-character-value-read", "    steps:\n      - run: echo \x01\n", None,
             "carries the value " + repr("echo \x01")),
            ("ci-control-character-value-read", "    steps:\n      - run: 'echo \x01'\n", None,
             "carries the value " + repr("'echo \x01'")),
            ("ci-sequence-entry-spacing-read", "    steps:\n      -  run: echo ok\n", None,
             "is not a form this parser places"),
            ("ci-nested-sequence-entry-read", "    needs:\n      - - lint\n", None,
             "is not a form this parser places"),
            ("ci-malformed-flow-sequence-read", "    needs: [lint,,test]\n", None,
             "carries the value '[lint,,test]'"),
            ("ci-unicode-space-after-a-block-header-read",
             "    steps:\n      - run: |\xa0# script\n          echo ok\n", None,
             "carries the value " + repr("|\xa0# script")),
            ("ci-unicode-space-before-a-key-read", "    \xa0timeout-minutes: 30\n", None,
             "is not a form this parser places"),
            ("ci-unicode-space-after-a-colon-read", "    timeout-minutes:\xa030\n", None,
             "is not a form this parser places"),
            ("ci-unicode-space-ending-a-value-read", "    timeout-minutes: 30\xa0\n", None,
             "timeout-minutes value " + repr("30\xa0")),
            ("ci-unicode-space-ending-a-value-read", "    timeout-minutes: 30\u3000\n", None,
             "timeout-minutes value " + repr("30\u3000")),
            ("ci-unicode-space-after-the-separating-space-read", "    timeout-minutes: \xa030\n",
             None, "timeout-minutes value " + repr("\xa030")),
            ("ci-unicode-space-after-the-separating-space-read", "    timeout-minutes: \u300030\n",
             None, "timeout-minutes value " + repr("\u300030")),
            ("ci-bare-block-header-not-read", "    steps:\n      - run: | # script\n          echo ok\n",
             default_s, None),
            ("ci-single-quote-escape-not-read", "    steps:\n      - run: 'it''s'\n", default_s, None),
            ("ci-tab-separation-not-read", "    timeout-minutes:\t30\t# half an hour\n", 1800, None),
            ("ci-flow-sequence-value-not-read", "    needs: [lint, test]\n    timeout-minutes: 30\n",
             1800, None),
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
             "    steps:\n    - timeout-minutes: 1\n      run: echo ok\n", default_s, None),
            ("ci-key-after-an-indentless-sequence-refused",
             "    steps:\n    - run: echo ok\n    timeout-minutes: 30\n", 1800, None)):
        got_limit, got_reason = ci_limit(
            check, "jobs:\n  {}:\n    runs-on: ubuntu-latest\n{}".format(_CI_JOBS[0], body),
            _CI_JOBS[0])
        if want_limit is not None:
            if (got_limit, got_reason) != (want_limit, None):
                raise _CheckFailure("closure/{}: {!r}".format(check, (got_limit, got_reason)))
        elif got_limit is not None or want_named not in (got_reason or ""):
            raise _CheckFailure("closure/{}: {!r}".format(check, (got_limit, got_reason)))
    print("RED ci-job-30-minutes -> does not fit; ci-job-absent, a non-integer value, a quoted key, an "
          "escaped key, an alias step, a block-scalar timeout-minutes, an alias or flow steps value, "
          "timeout-minutes 30#x and 030, a quoted list item, a double-quoted scalar (a `\\q` escape "
          "included), a block-scalar header |++, |0, |- or >, a control character in a plain or "
          "single-quoted scalar, "
          "a sequence entry `-  key` or `- - item`, a flow sequence [lint,,test], a no-break space "
          "before a key, after its colon or after a | header, and a no-break or ideographic space "
          "ending a value or after the space that follows its colon -> each refused by name; a step's "
          "own timeout-minutes: 1 (first key or not, and in an indentless step sequence) -> parsed, the job budget stays {}s; a job key after an indentless step "
          "sequence -> read; `30 # comment` and a tab-separated "
          "`30<TAB># comment` -> 30 minutes; an unquoted list item, a bare | with a comment, a "
          "single-quoted '' escape and a one-line flow sequence -> accepted; quoted text inside a "
          "block scalar -> content, not a key".format(default_s))
    # The config-lane deadline, read from the lane runner on every run. EVERY leg whose command is a
    # member of the runner's own roster (_registered_selftests over root's registries, never a list
    # kept here) must reach the deadline the config lanes give that command, plus the launch margin,
    # so the closure never kills such a leg before the lane running the same command would. The
    # deadlines are read from the runner's source (_lane_budgets) and must agree, member by member,
    # with what its own _config_member_bound looks up (a lookup that changed shape is cannot
    # evaluate, never read around). For EACH member leg, a bounds table one second short of its need
    # is a fault naming exactly that leg, and at the need and one above it is clean; one derivation
    # per probe, with every floored member's floor one second short, at and one above its need,
    # shows the floor term carrying each such bound through _leg_bounds. Membership follows the
    # roster: a roster without a member's command drops that leg, one registering a bare command
    # makes its leg a member and names its short bound, and a ./ spelling still matches. A lane
    # source whose deadline for the members' commands is one second higher (a raised default, or
    # new entries) is a named fault against the live bounds, so the deadlines are read from the
    # runner, never copied here; a roster or a deadline that cannot be read is refused by name
    # (cannot evaluate, never a pass).
    lane_path = Path(root) / _LANE_RUNNER
    try:
        lane_text = lane_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise _CannotEvaluate("closure/lane-deadline-cannot-evaluate: {}: {}".format(lane_path, exc))
    parsed, reason = _under_test("leg-bound-reaches-the-lane-deadline", _lane_budgets, lane_text)
    if parsed is None:
        raise _CannotEvaluate("closure/lane-deadline-cannot-evaluate: " + reason)
    lane_default, lane_table = parsed
    try:
        runner = _lane_runner()
        roster = tuple(runner._registered_selftests(Path(root)))
        identity, bound_of = runner._command_identity, runner._config_member_bound
    except (Exception, SystemExit) as exc:
        raise _CannotEvaluate("closure/lane-deadline-cannot-evaluate: the lane runner's roster cannot "
                              "be read: {}: {}".format(type(exc).__name__, exc))
    members = _under_test("leg-bound-reaches-the-lane-deadline", _lane_member_legs, roster, identity)
    if not members:
        raise _CheckFailure("closure/lane-deadline-not-discriminated: no subset leg's command is in "
                            "the lane runner's roster of {} commands".format(len(roster)))
    deadlines = _under_test("leg-bound-reaches-the-lane-deadline", _lane_deadlines, members,
                            lane_default, lane_table)
    disagreements = _under_test("lane-deadline-matches-the-runner", _lane_deadline_disagreements,
                                members, deadlines, bound_of)
    if disagreements:
        raise _CannotEvaluate("closure/lane-deadline-cannot-evaluate: the deadlines read from the lane "
                              "runner's source are not the ones its _config_member_bound gives: "
                              + "; ".join(disagreements))
    lane_faults = _under_test("leg-bound-reaches-the-lane-deadline", _lane_budget_faults, live,
                              deadlines, members)
    if lane_faults:
        raise _CheckFailure("closure/leg-bound-reaches-the-lane-deadline: " + "; ".join(lane_faults))
    print("PASS closure/leg-bound-reaches-the-lane-deadline: {} of {} legs run a command in the lane "
          "runner's roster of {} and each reaches its lane deadline plus the {}s launch margin ({}); "
          "no lane member: {}".format(
              len(members), len(names), len(roster), _LAUNCH_MARGIN_S,
              "; ".join("{} {}s >= {}s".format(name, live[name], deadlines[name] + _LAUNCH_MARGIN_S)
                        for name in sorted(members)),
              ", ".join(name for name in names if name not in members) or "none"))
    # One second short, per member leg, against the live bounds of every other leg.
    for name in sorted(members):
        need = deadlines[name] + _LAUNCH_MARGIN_S
        for delta, want in ((-1, [name]), (0, []), (1, [])):
            got = [fault.split(":", 1)[0] for fault in _under_test(
                "lane-deadline-one-second-short", _lane_budget_faults, dict(live, **{name: need + delta}),
                deadlines, members)]
            if got != want:
                raise _CheckFailure("closure/lane-deadline-one-second-short ({} bound {}s, need {}s): "
                                    "named {!r}, want {!r}".format(name, need + delta, need, got, want))
    # The floor term carries each floored member's bound: every such floor one second short, at and
    # one above its need, one derivation each.
    floored = sorted(name for name in members
                     if name in _LEG_BOUND_FLOOR_S and live[name] == _LEG_BOUND_FLOOR_S[name])
    for delta, short in ((-1, True), (0, False), (1, False)):
        probes = dict(_LEG_BOUND_FLOOR_S, **dict(
            (name, deadlines[name] + _LAUNCH_MARGIN_S + delta) for name in floored))
        flipped = dict((leg, row[0]) for leg, row in derive(
            "lane-deadline-flip", _leg_bounds, tools_dir, bound_floors=probes).items())
        for name in floored:
            if flipped[name] != probes[name]:
                raise _CheckFailure("closure/lane-deadline-flip-not-discriminated: a {}s floor gives {} "
                                    "a {}s bound".format(probes[name], name, flipped[name]))
        got = sorted(fault.split(":", 1)[0] for fault in _under_test(
            "lane-deadline-flip", _lane_budget_faults, flipped, deadlines, members))
        if got != (floored if short else []):
            raise _CheckFailure("closure/lane-deadline-flip (every floored member's floor {:+d}s from "
                                "its need): named {!r}".format(delta, got))
    # Membership is the roster's, never a list kept here. The vectors below use two fixed commands, so
    # they need the homes self-test in the roster, the bare homes command outside it, and the bare
    # command's leg bounded below the lane default plus the margin. RESIDUAL: three legitimate future
    # changes break those preconditions (registering the bare opf/tools/check_opf_homes.py as a lane
    # member, dropping the homes self-test from the registries, raising opf-homes-contract's bound to
    # the lane default plus the margin or more), and each turns every closure self-test into
    # cannot-evaluate (exit 2) until these vectors are updated to other commands.
    home_name, home_argv = "opf-homes-selftest", ("opf/tools/check_opf_homes.py", "--self-test")
    bare_name, bare_argv = "opf-homes-contract", ("opf/tools/check_opf_homes.py",)
    if (home_argv not in roster or bare_argv in roster or home_name not in members
            or live[bare_name] >= lane_default + _LAUNCH_MARGIN_S):
        raise _CannotEvaluate("closure/lane-deadline-cannot-evaluate: the roster vectors need {} in the "
                              "lane runner's roster, {} outside it, and {}'s bound below the lane "
                              "default plus the launch margin".format(
                                  " ".join(home_argv), " ".join(bare_argv), bare_name))
    without = _under_test("lane-deadline-membership-from-the-roster", _lane_member_legs,
                          tuple(argv for argv in roster if argv != home_argv), identity)
    widened = _under_test("lane-deadline-membership-from-the-roster", _lane_member_legs,
                          roster + (bare_argv,), identity)
    dotted = _under_test("lane-deadline-membership-from-the-roster", _lane_member_legs,
                         tuple(("./" + argv[0],) + argv[1:] if argv == home_argv else argv
                               for argv in roster), identity)
    widened_deadlines = _under_test("lane-deadline-membership-from-the-roster", _lane_deadlines,
                                    widened, lane_default, lane_table)
    widened_faults = [fault.split(":", 1)[0] for fault in _under_test(
        "lane-deadline-membership-from-the-roster", _lane_budget_faults, live, widened_deadlines,
        widened)]
    if (sorted(without) != sorted(set(members) - {home_name})
            or sorted(widened) != sorted(set(members) | {bare_name})
            or widened_faults != [bare_name]
            or sorted(dotted) != sorted(members)
            or dotted.get(home_name) != (("./" + home_argv[0],) + home_argv[1:],)):
        raise _CheckFailure("closure/lane-deadline-membership-from-the-roster: without {} -> {!r}; "
                            "with {} -> {!r}, named {!r}; ./ spelling -> {!r}".format(
                                " ".join(home_argv), sorted(without), " ".join(bare_argv),
                                sorted(widened), widened_faults, dotted.get(home_name)))
    # The source read must say what the runner looks up: a lookup one second higher for every member
    # disagrees for every member; the runner's own lookup agrees.
    skewed = _under_test("lane-deadline-matches-the-runner", _lane_deadline_disagreements, members,
                         deadlines, lambda argv: bound_of(argv) + 1)
    if [entry.split(":", 1)[0] for entry in skewed] != sorted(members):
        raise _CheckFailure("closure/lane-deadline-matches-the-runner (a lookup one second higher): "
                            "{!r}".format(skewed))
    default_line = re.compile(r"^CONFIG_MEMBER_BOUND_DEFAULT = ([0-9]+)$", re.MULTILINE)
    table_header = "CONFIG_MEMBER_BOUNDS = {\n"
    if len(default_line.findall(lane_text)) != 1 or lane_text.count(table_header) != 1:
        raise _CannotEvaluate("closure/lane-deadline-cannot-evaluate: the lane runner does not hold "
                              "exactly one `CONFIG_MEMBER_BOUND_DEFAULT = <int>` line and one "
                              "`CONFIG_MEMBER_BOUNDS = {` line to doctor")
    entered = sorted(argv for argvs in members.values() for argv in argvs if argv in lane_table)
    unentered = sorted(argv for argvs in members.values() for argv in argvs if argv not in lane_table)
    raised_default = default_line.sub("CONFIG_MEMBER_BOUND_DEFAULT = {}".format(lane_default + 1),
                                      lane_text, count=1)
    new_entries = lane_text.replace(table_header, table_header + "".join(
        "    {!r}: {},\n".format(argv, lane_default + 1) for argv in unentered), 1)
    want_deadlines = dict((name, max(lane_table.get(argv, lane_default + 1) for argv in argvs))
                          for name, argvs in members.items())
    want_named = sorted(name for name in members
                        if live[name] < want_deadlines[name] + _LAUNCH_MARGIN_S)
    if not want_named:
        raise _CheckFailure("closure/lane-deadline-read-from-the-runner: a deadline one second higher "
                            "names no leg, so the vector discriminates nothing")
    for label, text in (("a default one second higher", raised_default),
                        ("an entry one second higher for every member the default bounds",
                         new_entries)):
        doctored, reason = _under_test("lane-deadline-read-from-the-runner", _lane_budgets, text)
        if doctored is None:
            raise _CheckFailure("closure/lane-deadline-read-from-the-runner ({}): {}".format(
                label, reason))
        got_deadlines = _under_test("lane-deadline-read-from-the-runner", _lane_deadlines, members,
                                    doctored[0], doctored[1])
        got = sorted(fault.split(":", 1)[0] for fault in _under_test(
            "lane-deadline-read-from-the-runner", _lane_budget_faults, live, got_deadlines, members))
        if got_deadlines != want_deadlines or got != want_named:
            raise _CheckFailure("closure/lane-deadline-read-from-the-runner ({}): deadlines {!r}, want "
                                "{!r}; named {!r}, want {!r}".format(label, got_deadlines,
                                                                     want_deadlines, got, want_named))
    if entered:
        repeated = lane_text.replace(table_header, "{}    {!r}: {},\n".format(
            table_header, entered[0], lane_table[entered[0]] + 1), 1)
        doctored, reason = _under_test("lane-deadline-read-from-the-runner", _lane_budgets, repeated)
        if doctored is not None or "repeated" not in (reason or ""):
            raise _CheckFailure("closure/lane-deadline-read-from-the-runner (an entry repeated): "
                                "{!r}".format((doctored, reason)))
    # The reader is fail-closed: each shape it does not evaluate is refused naming what it met, and a
    # plain table is read exactly.
    plain = "CONFIG_MEMBER_BOUNDS = {('a.py', '--self-test'): 1800}\n"
    for label, text, want in (
            ("plain", "CONFIG_MEMBER_BOUND_DEFAULT = 1200\n" + plain,
             (1200, {("a.py", "--self-test"): 1800})),
            ("default absent", plain, "CONFIG_MEMBER_BOUND_DEFAULT is assigned 0 times"),
            ("default twice", "CONFIG_MEMBER_BOUND_DEFAULT = 1200\nCONFIG_MEMBER_BOUND_DEFAULT = 1500\n"
             + plain, "CONFIG_MEMBER_BOUND_DEFAULT is assigned 2 times"),
            ("default augmented",
             "CONFIG_MEMBER_BOUND_DEFAULT = 1200\nCONFIG_MEMBER_BOUND_DEFAULT += 1\n" + plain,
             "CONFIG_MEMBER_BOUND_DEFAULT is assigned 2 times"),
            ("default arithmetic", "CONFIG_MEMBER_BOUND_DEFAULT = 60 * 20\n" + plain,
             "CONFIG_MEMBER_BOUND_DEFAULT is not a positive int literal"),
            ("default bool", "CONFIG_MEMBER_BOUND_DEFAULT = True\n" + plain,
             "CONFIG_MEMBER_BOUND_DEFAULT is not a positive int literal"),
            ("table call", "CONFIG_MEMBER_BOUND_DEFAULT = 1200\nCONFIG_MEMBER_BOUNDS = dict()\n",
             "CONFIG_MEMBER_BOUNDS is not a dict literal"),
            ("table key repeated", "CONFIG_MEMBER_BOUND_DEFAULT = 1200\nCONFIG_MEMBER_BOUNDS = "
             "{('a.py',): 1800, ('a.py',): 600}\n", "key ('a.py',) is repeated"),
            ("table value zero", "CONFIG_MEMBER_BOUND_DEFAULT = 1200\nCONFIG_MEMBER_BOUNDS = "
             "{('a.py',): 0}\n", "value for ('a.py',) is not a positive int"),
            ("table key not a tuple", "CONFIG_MEMBER_BOUND_DEFAULT = 1200\nCONFIG_MEMBER_BOUNDS = "
             "{'a.py': 1800}\n", "key 'a.py' is not a tuple of str")):
        got, reason = _under_test("lane-deadline-reader-fail-closed", _lane_budgets, text)
        if type(want) is tuple:
            wrong = got != want
        else:
            wrong = got is not None or want not in (reason or "")
        if wrong:
            raise _CheckFailure("closure/lane-deadline-reader-fail-closed ({}): {!r}".format(
                label, (got, reason)))
    print("RED each of the {} lane-member legs one second short of its lane deadline plus the launch "
          "margin -> a fault naming exactly that leg; at and one above it -> clean; every floored "
          "member's floor ({} legs) one second short through _leg_bounds -> each named; at and one "
          "above -> clean; a roster without {} -> that leg no lane member; a roster registering {} "
          "-> {} a member, its {}s bound named; a ./ spelling -> still a member; a lookup one second "
          "higher than the source read -> every member disagrees; a lane runner giving the members "
          "one second more (a raised default, or new entries) -> named faults against the live "
          "bounds ({} legs); a repeated entry, a lane deadline absent, assigned twice or augmented, "
          "arithmetic, a bool, a table that is not a dict literal, a repeated key, a zero value and "
          "a key that is not a tuple -> each refused by name".format(
              len(members), len(floored), " ".join(home_argv), " ".join(bare_argv), bare_name,
              live[bare_name], len(want_named)))
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
    written file included) raises _CheckFailure naming `check`: a named self-test failure, never a
    traceback and never cleanup credit. The fixtures write the file aside and rename it into place, so
    a reader sees it whole or not at all; this check is the backstop."""
    text = path.read_text(encoding="utf-8")
    parts = text.split()
    if len(parts) != count or not all(re.fullmatch(r"[1-9][0-9]*", part) for part in parts):
        raise _CheckFailure("closure/{}-readiness-malformed: {!r} (want {} pid(s))".format(
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
        except (Exception, SystemExit) as exc:
            raise _CheckFailure("closure/{}: _kill_and_reap raised {!r} instead of naming the outcome "
                                "(pipe closed: {})".format(check, exc, double.stdout.closed))
        named = [fault.split(":", 1)[0] for fault in faults]
        reaps = [timeout for call, timeout in double.calls if call == "reap"]
        leader_kills = [call for call, _timeout in double.calls if call == "kill"]
        if (named != want_faults or out != want_out or not double.stdout.closed
                or (double.returncode is not None) != want_reaped
                or group_kills != [(double.pid, signal.SIGKILL)]
                or leader_kills != (["kill"] if double.group_kill_fails else [])
                or any(timeout > drain_s - used_s + 1e-6 for timeout in reaps)):
            raise _CheckFailure("closure/{}: faults {!r}, output {!r}, pipe closed {}, returncode {!r}, "
                                "group kills {!r}, calls {!r}".format(
                                    check, faults, out, double.stdout.closed, double.returncode,
                                    group_kills, double.calls))
        if "leader-unreaped" in named and "leader pid {}".format(double.pid) not in faults[-1]:
            raise _CheckFailure("closure/{}: the unreaped leader is not named by pid: {!r}".format(
                check, faults))
        for name, cause in (("group-kill-error", "scripted group-kill failure"),
                            ("reap-error", "scripted reap failure")):
            if name in named and cause not in faults[named.index(name)]:
                raise _CheckFailure("closure/{}: {} does not carry its error: {!r}".format(
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
    run then waits until it is gone. The report vectors also hold the exit mapping of a leg's own exit
    2: WITH the printed CANNOT EVALUATE marker it is LEG CANNOT EVALUATE, exit 2 alone and exit 1 beside
    a real failure, never clean; WITHOUT the marker (a bootstrap import failure's lowercase "(cannot
    evaluate)" does not count, nor does a conformance self-check failure) it is closure broken, exit 1;
    and the marker in the text of a row the runner never launched is never the leg's declaration."""
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
        env = _under_test("half-ceiling-bound-did-not-kill-the-ceiling-run", _isolated_env)
        rc, _tail_text, _elapsed, _launched, _cleanup = _under_test(
            "half-ceiling-bound-did-not-kill-the-ceiling-run", _run_one, stub_root, "stub_sleep.py",
            [slow], tmp, env, undersized)
        if rc is not None:
            raise _CheckFailure(
                "closure/half-ceiling-bound-did-not-kill-the-ceiling-run (rc={!r})".format(rc))
        print("RED half-ceiling flat bound -> a correct run at the recorded {}s {} ceiling is killed "
              "(time-scaled 1:{:g})".format(ceiling, leg, scale))
        rc, _tail_text, _elapsed, _launched, _cleanup = _under_test(
            "derived-bound-killed-the-ceiling-run", _run_one, stub_root, "stub_sleep.py", [slow], tmp,
            env, scaled_bound)
        if rc != 0:
            raise _CheckFailure("closure/derived-bound-killed-the-ceiling-run (rc={!r})".format(rc))
        print("PASS closure/derived-bound-lets-the-ceiling-run-finish ({}s bound over the {}s "
              "ceiling)".format(bound, ceiling))
        rc, tail, elapsed, _launched, cleanup = _under_test(
            "hang-not-cut-off", _run_one, stub_root, "stub_sleep.py", [hang], tmp, env, scaled_bound)
        if rc is not None or not scaled_bound <= elapsed < scaled_bound + 30 or cleanup:
            raise _CheckFailure("closure/hang-not-cut-off (rc={!r}, {:.1f}s, cleanup {!r})".format(
                rc, elapsed, cleanup))
        unreaped = "leader-unreaped: leader pid 4242 was still unreaped at the 10s cleanup deadline"
        apply_leg = "opf-adopt-apply-selftest"
        apply_bound = required[apply_leg][0]
        cannot_tail = "OPF-ADOPT-APPLY SELF-TEST: CANNOT EVALUATE (1 bound(s) expired)"
        # The unmarked exit-2 tails below are real shapes a launched leg's own exit 2 carries: a
        # bootstrap import failure (its lowercase "(cannot evaluate)" must NOT count as the marker) and
        # a conformance self-check failure. The runner's own missing-script row is launched False, a
        # harness failure (NOT EVALUATED), and the marker in its text never counts.
        bootstrap_tail = "opf: cannot bootstrap: check_versions (cannot evaluate)"
        conformance_tail = "CONFORMANCE SELF-CHECK FAIL: the verdict did not reject a duplicate example id"
        missing_tail = "missing subset script in the copy: opf/tools/check_opf_homes.py"
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
                # A leg ended by a signal this gate did not send (a negative status: from outside, or
                # from the leg or its own process group) reported no exit status: cannot evaluate,
                # naming the signal and saying the status cannot tell the two apart, never BROKEN.
                ([("opf-tooling-selftest", -9, "partial", 12.0, True, bound, [])], 2,
                 ("ENDED BY SIGKILL (signal 9)", "NOT EVALUATED",
                  "opf-tooling-selftest: ended by SIGKILL (signal 9)",
                  "cannot tell an outside signal from one the leg or its own process group sent itself",
                  "from one the leg or its own process group sent itself; closure was not evaluated"),
                 ("BROKEN", "CLOSURE: OK", "FAILED rc=-9", "the leg reported nothing")),
                ([("opf-tooling-selftest", -9, "partial", 12.0, True, bound, []),
                  ("opf-homes-contract", 1, "boom", 1.0, True, 120, [])], 1,
                 ("BROKEN", "opf-homes-contract (rc=1): boom",
                  "opf-tooling-selftest: ended by SIGKILL (signal 9)"),
                 ("CLOSURE: OK", "opf-tooling-selftest (rc=-9)")),
                # A leg's own exit 2 WITH the printed marker said it could not evaluate its own checks:
                # LEG CANNOT EVALUATE, exit 2 alone and exit 1 beside a real failure, never clean.
                ([(apply_leg, 2, cannot_tail, 354.2, True, apply_bound, [])], 2,
                 ("CANNOT EVALUATE rc=2 (354.2s)", "LEG CANNOT EVALUATE",
                  "{} (rc=2): {}".format(apply_leg, cannot_tail), "closure was not evaluated"),
                 ("BROKEN", "CLOSURE: OK")),
                ([(apply_leg, 2, cannot_tail, 354.2, True, apply_bound, []),
                  ("opf-homes-contract", 1, "boom", 1.0, True, 120, [])], 1,
                 ("BROKEN", "opf-homes-contract (rc=1): boom", "LEG CANNOT EVALUATE",
                  "{} (rc=2): {}".format(apply_leg, cannot_tail)),
                 ("CLOSURE: OK",)),
                # A leg's own exit 2 WITHOUT the marker is closure broken (exit 1), never
                # closure-not-evaluated.
                ([("opf-tooling-selftest", 2, bootstrap_tail, 1.2, True, bound, [])], 1,
                 ("FAILED rc=2 (1.2s)", "BROKEN",
                  "opf-tooling-selftest (rc=2): {}".format(bootstrap_tail)),
                 ("LEG CANNOT EVALUATE", "CLOSURE: OK")),
                # Beside it, the runner's own missing-script row (launched False) stays a harness
                # failure, never the leg's report and never read as its declaration.
                ([("commonmark-conformance", 2, conformance_tail, 0.3, True, 120, []),
                  ("opf-homes-selftest", 2, missing_tail, 0.0, False, 1260, [])], 1,
                 ("BROKEN", "commonmark-conformance (rc=2): {}".format(conformance_tail),
                  "NOT LAUNCHED", "opf-homes-selftest: {}".format(missing_tail)),
                 ("LEG CANNOT EVALUATE", "CLOSURE: OK", "opf-homes-selftest (rc=2)")),
                ([(apply_leg, 2, cannot_tail, 354.2, True, apply_bound, []),
                  ("opf-tooling-selftest", 2, bootstrap_tail, 1.2, True, bound, [])], 1,
                 ("BROKEN", "opf-tooling-selftest (rc=2): {}".format(bootstrap_tail),
                  "LEG CANNOT EVALUATE", "{} (rc=2): {}".format(apply_leg, cannot_tail)),
                 ("CLOSURE: OK",)),
                # The marker in the text of a row the runner never launched is not the leg's
                # declaration: NOT EVALUATED as a harness failure, never LEG CANNOT EVALUATE.
                ([("opf-homes-selftest", 2, "could not launch the interpreter: /x/CANNOT EVALUATE/py",
                   0.0, False, 1260, [])], 2,
                 ("NOT LAUNCHED", "NOT EVALUATED", "never the leg's"),
                 ("LEG CANNOT EVALUATE", "BROKEN", "CLOSURE: OK"))):
            out = io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
                code = _under_test("timeout-report", _report, rows)
            said = out.getvalue()
            if code != want_code or not all(w in said for w in want) or any(r in said for r in refused):
                raise _CheckFailure("closure/timeout-report (exit {}): {!r}".format(code, said))
        unnamed = _under_test("unnamed-signal-not-named-by-number", _signal_name, -200)
        if unnamed != "signal 200":
            raise _CheckFailure("closure/unnamed-signal-not-named-by-number: {!r}".format(unnamed))
        print("RED hang -> cut off at its bound and reported as LEG BOUND EXCEEDED (exit 2; exit 1 "
              "beside a real failure; a leg that never launched -> NOT EVALUATED, exit 2, never "
              "BROKEN; a killed leg whose leader is unreaped -> CLEANUP INCOMPLETE naming it, exit 2; "
              "a leg ended by a signal this gate did not send -> NOT EVALUATED naming the signal and "
              "saying the status cannot tell an outside signal from the leg's own, exit 2, never "
              "BROKEN, and exit 1 beside a real failure); a leg's own exit 2 with the printed CANNOT "
              "EVALUATE marker -> LEG CANNOT EVALUATE (exit 2; exit 1 beside a real failure); a leg's "
              "own exit 2 without the marker (a bootstrap import failure, a conformance self-check "
              "failure) -> BROKEN (exit 1); the marker in a never-launched row's text -> NOT "
              "EVALUATED, never LEG CANNOT EVALUATE")
        # A readiness file holding anything but whole pids (a partly written file included) is a
        # named self-test failure, never a traceback.
        partly = tmp / "partly-written.pid"
        for text, count in (("", 1), ("4242 ", 2), ("42x", 1)):
            partly.write_text(text, encoding="utf-8")
            try:
                _readiness_pids(partly, count, "partly-written")
            except _CheckFailure as exc:
                if "closure/partly-written-readiness-malformed" not in str(exc):
                    raise
            else:
                raise _CheckFailure("closure/partly-written-readiness-not-refused: {!r}".format(text))
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
                raise _CheckFailure("closure/descendant-readiness-missing: the fixture leg never "
                                    "spawned its descendant inside its own 30s readiness deadline (a "
                                    "self-test failure, never cleanup credit)")
            descendant, = _readiness_pids(ready, 1, "descendant")
            try:
                proc.communicate(timeout=2.0)
            except subprocess.TimeoutExpired:
                pass  # the hang the deadline cuts off
            else:
                raise _CheckFailure("closure/descendant-fixture-shape: the leader exited (rc={!r}) "
                                    "inside its 2.0s hang deadline".format(proc.returncode))
        finally:
            _out, cleanup = _under_test("descendant-kill-cleanup-incomplete", _kill_and_reap, proc)
        if cleanup:
            raise _CheckFailure("closure/descendant-kill-cleanup-incomplete: " + "; ".join(cleanup))
        if not _pid_gone(descendant, 10.0):
            raise _CheckFailure("closure/descendant-survived-the-kill: descendant pid {} still "
                                "exists after the group kill".format(descendant))
        while time.monotonic() < ready_at + holder_delay_s + marker_margin_s and not marker.exists():
            time.sleep(0.1)
        if marker.exists():
            raise _CheckFailure("closure/descendant-survived-the-kill: the descendant wrote its "
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
                        raise _CheckFailure("closure/{}-fixture-shape: no readiness inside 30s (a "
                                            "self-test failure, never credit)".format(check))
                    escapee, = _readiness_pids(pids, 1, check)
                finally:
                    out, faults = _under_test(check, _kill_and_reap, proc, drain_s=2.0)
                if [fault.split(":", 1)[0] for fault in faults] != ["drain-timeout"]:
                    raise _CheckFailure("closure/{}-drain-did-not-time-out: {!r} (the branch under "
                                        "test never ran)".format(check, faults))
                if check == "leader-reaped-after-a-drain-timeout":
                    if proc.returncode is None or not proc.stdout.closed:
                        raise _CheckFailure("closure/leader-not-reaped-or-pipe-left-open after the "
                                            "drain (returncode={!r})".format(proc.returncode))
                elif "partial output before the hang" not in out.decode("utf-8", "replace"):
                    raise _CheckFailure(
                        "closure/partial-output-lost-on-a-drain-timeout: {!r}".format(out))
            finally:
                release.write_text("released", encoding="utf-8")
            if not _pid_gone(escapee, 20.0):
                raise _CheckFailure("closure/escape-fixture-descendant-still-running: pid {} (it "
                                    "exits once released)".format(escapee))
            print("PASS closure/{} (the drain timed out on escapee pid {}, released after the "
                  "assertions)".format(check, escapee))
        pids = tmp / "runner-cleanup-report.pid"
        release = tmp / "runner-cleanup-report.release"
        try:
            rc, tail, elapsed, _launched, cleanup = _under_test(
                "runner-cleanup-not-reported", _run_one, stub_root, "stub_escape.py",
                [str(pids), str(release)], tmp, env, 5.0, drain_s=2.0)
        finally:
            release.write_text("released", encoding="utf-8")
        if (rc is not None or [fault.split(":", 1)[0] for fault in cleanup] != ["drain-timeout"]
                or "partial output before the hang" not in tail):
            raise _CheckFailure("closure/runner-cleanup-not-reported (rc={!r}, cleanup {!r}, tail "
                                "{!r})".format(rc, cleanup, tail))
        if _await_file(pids, 30.0) is None:
            raise _CheckFailure("closure/runner-cleanup-report-fixture-shape: no readiness inside 30s")
        escapee, = _readiness_pids(pids, 1, "runner-cleanup-report")
        if not _pid_gone(escapee, 20.0):
            raise _CheckFailure("closure/escape-fixture-descendant-still-running: pid {} (it exits "
                                "once released)".format(escapee))
        print("PASS closure/runner-reports-an-incomplete-cleanup (_run_one returned the drain-timeout "
              "fault by name, with the partial output, after {:.1f}s)".format(elapsed))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _after_launch_fault_self_test():
    """An exception that ends _run_one's wait AFTER a successful launch (here an OSError from the first
    output read, injected through a real Popen subclass) leaves no leg running, on real processes, and
    reaches the caller as ITSELF. TERMINATION: the leg writes its pid to a readiness file (aside, then
    renamed into place), and the read fails only once that file is seen under its OWN 30 s deadline; the
    same exception object must escape with no note, the leader (the pid the leg wrote) killed by
    SIGKILL and reaped, the output pipe closed, and the marker the leg would write
    after_launch_delay_s after its readiness still absent after_launch_margin_s past that moment.
    CLEANUP FAULTS: with the real cleanup run and one fault added to its result, or the real cleanup
    run and then an exception raised from it, the original exception still escapes, carrying the fault
    or the cleanup's exception as a note, the leader still reaped and the pipe closed; and through
    _run_closure over a throwaway opf/, that note is in the harness fault it names (exit 2). No readiness
    is a self-test failure, never cleanup credit. Every leg this vector launches that is left unreaped
    (by a runner that raised without cleaning up) is killed and reaped here after the assertions."""
    after_launch_delay_s, after_launch_margin_s = 3.0, 1.0
    injected_text = "injected output-pipe read failure after the launch"
    scripted = "leader-unreaped: scripted fault added to the real cleanup"
    real_popen = subprocess.Popen
    real_cleanup = _kill_and_reap
    launched = []

    class FailingRead(real_popen):
        """A real launch whose first output read raises the injected OSError, once ready is seen."""
        ready = None
        injected = None

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.ready_at, self.failed = None, False
            launched.append(self)

        def communicate(self, input=None, timeout=None):
            if self.failed:
                return super().communicate(input, timeout)
            self.failed = True
            if FailingRead.ready is not None:
                self.ready_at = _await_file(FailingRead.ready, 30.0)
            raise FailingRead.injected

    # The real cleanup, under at most a 2 s deadline, with a fault added to its result or followed by
    # an exception of its own.
    def with_fault(proc, drain_s=_DRAIN_MARGIN_S):
        out, faults = real_cleanup(proc, min(drain_s, 2.0))
        return out, faults + [scripted]

    def raising_after(proc, drain_s=_DRAIN_MARGIN_S):
        real_cleanup(proc, min(drain_s, 2.0))
        raise RuntimeError("scripted cleanup failure")

    def run_failing(check, call, ready, rebound):
        """call() with the first output read failing and the module names in rebound swapped; returns
        (the OSError that escaped, or else what call returned; the injected OSError; the one leg)."""
        injected = OSError(injected_text)
        FailingRead.ready, FailingRead.injected = ready, injected
        before = len(launched)
        subprocess.Popen = FailingRead
        try:
            with _swapped(**rebound):
                try:
                    got = call()
                except OSError as exc:
                    escaped = exc
                except (Exception, SystemExit) as exc:
                    raise _CheckFailure("closure/{}: the code under test raised {}: {} instead of the "
                                        "injected OSError".format(check, type(exc).__name__, exc)) from exc
                else:
                    escaped = got
        finally:
            subprocess.Popen = real_popen
        legs = launched[before:]
        if len(legs) != 1:
            raise _CheckFailure("closure/{}-fixture-shape: {} launch(es), want 1".format(check, len(legs)))
        return escaped, injected, legs[0]

    def left_no_leg_running(check, leg):
        if leg.returncode is None or leg.stdout is None or not leg.stdout.closed:
            raise _CheckFailure("closure/{}: the launched leg was left running or its pipe open "
                                "(returncode {!r}, pipe closed {})".format(
                                    check, leg.returncode, leg.stdout is not None and leg.stdout.closed))

    tmp = Path(tempfile.mkdtemp(prefix="opf-closure-after-launch-"))
    try:
        stub_root = tmp / "opf"
        os.makedirs(stub_root / "tools")
        (stub_root / "tools" / "stub_after_launch.py").write_text(
            "import os, sys, time\n"
            "with open(sys.argv[1] + '.tmp', 'w') as f:\n"
            "    f.write(str(os.getpid()))\n"
            "os.replace(sys.argv[1] + '.tmp', sys.argv[1])\n"
            "time.sleep(float(sys.argv[3]))\n"
            "with open(sys.argv[2], 'w') as f:\n"
            "    f.write('ran on after the runner raised')\n", encoding="utf-8")
        env = _under_test("after-launch-fault-cleans-up", _isolated_env)
        ready, marker = tmp / "after-launch.pid", tmp / "after-launch-marker.txt"
        check = "after-launch-fault-cleans-up"
        escaped, injected, leg = run_failing(check, lambda: _run_one(
            stub_root, "stub_after_launch.py", [str(ready), str(marker), repr(after_launch_delay_s)],
            tmp, env, 30.0, drain_s=2.0), ready, {})
        if escaped is not injected:
            raise _CheckFailure("closure/{}: _run_one returned {!r} instead of raising the injected "
                                "OSError".format(check, escaped))
        if leg.ready_at is None:
            raise _CheckFailure("closure/{}-readiness-missing: the leg never wrote its readiness file "
                                "inside its own 30s deadline (a self-test failure, never cleanup "
                                "credit)".format(check))
        pid, = _readiness_pids(ready, 1, check)
        notes = getattr(escaped, "__notes__", [])
        if (pid != leg.pid or leg.returncode != -signal.SIGKILL or leg.stdout is None
                or not leg.stdout.closed or notes):
            raise _CheckFailure("closure/{}: leader pid {} (readiness {}), returncode {!r} (want {}), "
                                "pipe closed {}, notes {!r}".format(
                                    check, leg.pid, pid, leg.returncode, -signal.SIGKILL,
                                    leg.stdout is not None and leg.stdout.closed, notes))
        while (time.monotonic() < leg.ready_at + after_launch_delay_s + after_launch_margin_s
               and not marker.exists()):
            time.sleep(0.1)
        if marker.exists():
            raise _CheckFailure("closure/{}: the leg wrote its marker after _run_one raised (it outlived "
                                "the runner's error)".format(check))
        print("PASS closure/{} (the injected OSError escaped as itself; leader pid {} killed by SIGKILL "
              "and reaped, its pipe closed, and no marker {:g}s past the moment it would have been "
              "written)".format(check, pid, after_launch_margin_s))
        for check, rebound, want_note in (
                ("after-launch-cleanup-fault-named", {"_kill_and_reap": with_fault},
                 "CLEANUP INCOMPLETE after the leg's OSError: " + scripted),
                ("after-launch-cleanup-error-keeps-the-original", {"_kill_and_reap": raising_after},
                 "CLEANUP INCOMPLETE after the leg's OSError: cleanup-error: the cleanup raised "
                 "RuntimeError: scripted cleanup failure")):
            ready = tmp / (check + ".pid")
            escaped, injected, leg = run_failing(check, lambda ready=ready, check=check: _run_one(
                stub_root, "stub_after_launch.py",
                [str(ready), str(tmp / (check + ".marker")), repr(after_launch_delay_s)],
                tmp, env, 30.0, drain_s=2.0), None, rebound)
            notes = getattr(escaped, "__notes__", [])
            if escaped is not injected or notes != [want_note]:
                raise _CheckFailure("closure/{}: escaped {!r} (the injected one: {}), notes {!r}, want "
                                    "[{!r}]".format(check, escaped, escaped is injected, notes, want_note))
            left_no_leg_running(check, leg)
            if _fault_text(escaped) != "OSError: {}; {}".format(injected_text, want_note):
                raise _CheckFailure("closure/{}: the fault text drops the note: {!r}".format(
                    check, _fault_text(escaped)))
        check = "after-launch-cleanup-fault-reported-by-the-run"
        # The first leg's script sleeps, so the leg is still running when the cleanup kills it.
        base = tmp / "throwaway"
        (_throwaway_opf(base) / _SUBSET[0][1]).write_text(
            "import time\ntime.sleep({!r})\n".format(after_launch_delay_s), encoding="utf-8")
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            result, _injected, leg = run_failing(
                check, lambda: _run_closure(base), None, {"_kill_and_reap": with_fault})
        said = out.getvalue()
        want_stop = ("harness fault before, around or after the legs (OSError: {}; CLEANUP INCOMPLETE "
                     "after the leg's OSError: {})".format(injected_text, scripted))
        if not isinstance(result, tuple) or result[:3] != (2, None, want_stop) or want_stop not in said:
            raise _CheckFailure("closure/{}: {!r}, want (2, None, {!r}): {!r}".format(
                check, result, want_stop, said))
        left_no_leg_running(check, leg)
        print("RED an exception after the launch whose cleanup did not complete (a fault added to the "
              "real cleanup's result, or the cleanup raising) -> the original exception escapes, the "
              "fault a note on it, the leader reaped and the pipe closed; through _run_closure the "
              "note is in the harness fault it names, exit 2")
    finally:
        subprocess.Popen = real_popen
        for leg in launched:
            if leg.returncode is None or (leg.stdout is not None and not leg.stdout.closed):
                real_cleanup(leg, 2.0)
        shutil.rmtree(tmp, ignore_errors=True)


_INJECTED_EDGE = "check_versions"
# The one outcome the flipped copy produces: opf.py's guarded bootstrap (_bootstrap) maps the injected
# module's ImportError to its exit 2 and prints this one line, newline included, before any self-test
# runs. Observed on 2026-10-15 at this change's opf/ tree as it then was: the flipped opf.py
# --self-test exited 2, printing exactly this line and nothing else; observed again on 2026-10-16 at
# the same opf/ tree as the raw bytes of its whole output, stderr merged into stdout as the negative
# leg runs it: exit 2 and exactly _FLIP_BYTES (56 bytes, ending in one newline). Both were recorded at
# a revision whose opf/ subtree is the tree the ceiling table names (git tree
# 7bf8107f4c6d23f5c89298ef121af566f5e48614); at a later opf/ tree these bytes are INFERRED until the
# negative leg, which requires them exactly on every run, has run there.
_FLIP_EXIT = 2
_FLIP_OUTPUT = "opf: cannot bootstrap: {} (cannot evaluate)".format(_INJECTED_EDGE)
_FLIP_BYTES = (_FLIP_OUTPUT + "\n").encode("utf-8")


def _negative_verdict(rc, output, bound, launched):
    """The negative leg's verdict: None when the re-introduced upward edge was caught, else (exit code,
    text). output is the leg's WHOLE raw output (stdout with stderr merged, the undecoded bytes
    _run_one returns with raw True). Caught requires EXACTLY the outcome the flipped import produces:
    the leg launched, exited _FLIP_EXIT, and its output is byte for byte _FLIP_BYTES, the one line and
    the newline the flipped import writes; nothing is decoded, split or stripped first, so a carriage
    return in place of the newline, a missing newline, trailing whitespace or another line is never
    credited. A clean pass (rc 0) is the flip NOT caught, a definite failure (1). Every other outcome
    leaves the flip unevaluated (2), never coverage credit: launched False (the runner's STRUCTURAL
    harness-failure flag, rejected whatever text it carries: a launch error under a path that contains
    the token names the token without evaluating the flip), rc None (the bound tripped), a negative rc
    (a signal the runner did not send, from outside or from the leg's own process group: the status
    cannot tell them apart), and any other status or output, the token included (an unrelated failure
    that mentions it, another cannot-bootstrap line, the right line under another status, beside other
    output or with other line endings or whitespace)."""
    shown = _shown_bytes(output)
    if not launched:
        return 2, ("negative: the flipped leg never launched (a harness failure is never coverage, "
                   "whatever its text; the flip was not evaluated): {}".format(shown))
    if rc is None:
        return 2, ("negative: the flipped opf.py --self-test was killed at its {}s bound "
                   "(the bound tripped; the flip was not evaluated)".format(bound))
    if rc < 0:
        return 2, ("negative: the flipped opf.py --self-test was ended by {}, a signal this gate did "
                   "not send (from outside or from the leg's own process group: the status cannot tell "
                   "them apart; the flip was not evaluated): {}".format(_signal_name(rc), shown))
    if rc == 0:
        return 1, ("negative: a re-introduced upward edge (import check_versions) was NOT "
                   "caught in isolation (the gate would give no coverage)")
    if rc != _FLIP_EXIT or output != _FLIP_BYTES:
        return 2, ("negative: the leg exited rc={} with output other than the flipped import's exact "
                   "failure (exit {} and exactly the bytes {!r}), so the flip was not evaluated (a "
                   "failure that merely mentions {!r} is never coverage): {}".format(
                       rc, _FLIP_EXIT, _FLIP_BYTES, _INJECTED_EDGE, shown))
    return None


def _negative_verdict_self_test():
    """The negative-leg verdict is discriminating: only the flipped import's exact failure (exit
    _FLIP_EXIT, raw output exactly _FLIP_BYTES) counts as caught; a clean pass is a definite failure
    (1); a bound kill, a signal, a harness failure (launched False: a missing script or an interpreter
    that could not start), an unrelated failure, every non-zero exit that carries the token but is not
    that failure, and the exact line with another line ending (a carriage return, CRLF, a form feed, no
    newline) or with trailing whitespace each leave the flip unevaluated (2). The harness-failure
    rejection is STRUCTURAL: a real launch failure whose pathname contains the token, so that its text
    names the token, is still rejected."""
    caught = _under_test("negative-verdict-rejects-a-genuine-catch", _negative_verdict, _FLIP_EXIT,
                         _FLIP_BYTES, 600, True)
    if caught is not None:
        raise _CheckFailure("closure/negative-verdict-rejects-a-genuine-catch: {!r}".format(caught))
    print("PASS closure/negative-verdict-accepts-the-exact-flipped-import-failure")
    line = _FLIP_OUTPUT.encode("utf-8")
    for rc, output, launched, label, want_code in (
            (None, b"", True, "bound-kill", 2),
            (0, b"SELF-TEST PASS\n", True, "clean-pass", 1),
            (2, b"could not launch the interpreter: [Errno 2] No such file or directory", False,
             "launch-error", 2),
            (2, b"could not launch the interpreter: [Errno 2] No such file or directory: "
                b"'/sandbox/check_versions/bin/python3'", False, "launch-error-naming-the-token", 2),
            (1, b"SELF-TEST FAIL: unrelated\n", True, "unrelated-failure", 2),
            # A non-zero exit that carries the token but is not the flipped import's failure: an
            # unrelated failure whose text mentions it, another cannot-bootstrap line naming it, the
            # exact line under another status or beside other output, the bare import traceback (not
            # the shape the guarded bootstrap prints), and a signal after the exact line.
            (1, b"unrelated assertion failed; debug path /tmp/check_versions/cache\n", True,
             "unrelated-failure-naming-the-token", 2),
            (2, b"opf: cannot bootstrap: a helper module could not be read (PermissionError: "
                b"/x/check_versions.py) (cannot evaluate)\n", True,
             "other-bootstrap-line-naming-the-token", 2),
            (1, _FLIP_BYTES, True, "exact-line-under-exit-1", 2),
            (3, _FLIP_BYTES, True, "exact-line-under-exit-3", 2),
            (2, b"SELF-TEST FAIL: opf-store\n" + _FLIP_BYTES, True, "exact-line-beside-other-output", 2),
            (1, b"ModuleNotFoundError: No module named 'check_versions'\n", True,
             "bare-import-traceback", 2),
            (-9, _FLIP_BYTES, True, "signal-after-the-exact-line", 2),
            # The exact line under the exact exit, with any other bytes after it: the comparison is of
            # the raw output, never of a decoded, split or stripped form of it, so a line ending other
            # than the one newline the flipped import writes, or trailing whitespace, is not its output.
            (2, line + b"\r", True, "carriage-return-in-place-of-the-newline", 2),
            (2, line + b"\r\n", True, "carriage-return-before-the-newline", 2),
            (2, line + b"\x0c", True, "form-feed-in-place-of-the-newline", 2),
            (2, line, True, "no-newline", 2),
            (2, line + b" \n", True, "trailing-space-before-the-newline", 2),
            (2, _FLIP_BYTES + b" ", True, "trailing-space-after-the-newline", 2),
            (2, _FLIP_BYTES + b"\n", True, "trailing-blank-line", 2)):
        verdict = _under_test("negative-verdict-not-red", _negative_verdict, rc, output, 600, launched)
        if verdict is None or verdict[0] != want_code:
            raise _CheckFailure("closure/negative-verdict-not-red: {} (rc={!r}): {!r}, want exit "
                                "{}".format(label, rc, verdict, want_code))
    signalled = _under_test("negative-signal-not-named", _negative_verdict, -9, _FLIP_BYTES, 600, True)
    if "SIGKILL (signal 9)" not in signalled[1] or "own process group" not in signalled[1]:
        raise _CheckFailure("closure/negative-signal-not-named: {!r}".format(signalled))
    returned = _under_test("negative-output-not-shown-byte-for-byte", _negative_verdict, 2,
                           line + b"\r", 600, True)
    if repr(line + b"\r") not in returned[1]:
        raise _CheckFailure("closure/negative-output-not-shown-byte-for-byte: {!r}".format(returned))
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
        env = _under_test("negative-launch-fixture-shape", _isolated_env)
        for label, script, interpreter, want_text in (
                ("missing-script", "missing.py", real_interpreter, "missing subset script"),
                ("interpreter-launch", "present.py", str(token_dir / "bin" / "python3"),
                 "could not launch the interpreter")):
            sys.executable = interpreter
            try:
                rc, output, _elapsed, launched, _cleanup = _under_test(
                    "negative-launch-fixture-shape", _run_one, fake_root, script, [], tmp, env, 10,
                    raw=True)
            finally:
                sys.executable = real_interpreter
            if (launched is not False or rc != 2 or want_text.encode("utf-8") not in output
                    or _INJECTED_EDGE.encode("utf-8") not in output):
                raise _CheckFailure("closure/negative-launch-fixture-shape: {} (rc={!r}, launched={!r}, "
                                    "output={!r})".format(label, rc, launched, output))
            verdict = _under_test("negative-verdict-not-red", _negative_verdict, rc, output, 600,
                                  launched)
            if verdict is None or verdict[0] != 2:
                raise _CheckFailure("closure/negative-verdict-not-red: a real {} failure whose path "
                                    "contains {!r}: {!r}".format(label, _INJECTED_EDGE, verdict))
    finally:
        sys.executable = real_interpreter
        shutil.rmtree(tmp, ignore_errors=True)
    print("RED negative-leg clean-pass -> a definite failure (exit 1); bound-kill, a signal, "
          "unrelated-failure, launch-error, every non-zero exit carrying {!r} that is not the flipped "
          "import's exact failure (exit {} and exactly {!r}), the exact line ending in a carriage "
          "return, CRLF, a form feed or no newline, or carrying trailing whitespace, and a real "
          "missing-script failure and a real interpreter-launch failure under a path containing it -> "
          "each not evaluated (exit 2), never coverage credit".format(
              _INJECTED_EDGE, _FLIP_EXIT, _FLIP_BYTES))


def _positive_verdict(code, results, stopped=None):
    """The positive leg's verdict from _run_closure's (exit code, results, stopped): None when closure
    holds (0); (1, text) when a leg failed in isolation, or, with no results, for the definite finding
    the run stopped on (registration drift), named and never read as the real opf/ failing to verify
    (1); and (2, text) for every other code, naming each leg that was not evaluated (killed at its
    bound, ended by a signal, never launched, exited 2 printing CANNOT EVALUATE, or its cleanup
    incomplete) or, with no results, why the
    run stopped (a refusal or a harness fault), so a bound kill reads as the bound tripping, never as
    the real opf/ failing to verify."""
    if code == 0:
        return None
    if code == 1:
        if results is None:
            return 1, ("positive: {} (a definite finding before any leg ran, never the real opf/ failing "
                       "to verify in isolation)".format(stopped or "run() exit 1 named no finding"))
        return 1, "positive: the real opf/ subtree did not verify in isolation (expected closure)"
    named = []
    for name, rc, _tail_text, _elapsed, launched, bound, cleanup in results or ():
        if not launched:
            named.append("{} never launched (a harness failure)".format(name))
        elif rc is None:
            named.append("{} killed at its {}s bound (the bound tripped)".format(name, bound))
        elif rc < 0:
            named.append("{} ended by {}".format(name, _signal_name(rc)))
        elif _said_cannot_evaluate((name, rc, _tail_text, _elapsed, launched, bound, cleanup)):
            named.append("{} exited 2 and printed {} (it said it could not evaluate its own "
                         "checks)".format(name, _CANNOT_EVALUATE_MARKER))
        if cleanup:
            named.append("{} cleanup incomplete: {}".format(name, "; ".join(cleanup)))
    reason = "; ".join(named) or "stopped before any leg result: {}".format(
        stopped or "its reason is printed above")
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


def _closure_legs(root, required, run_closure=None, run_one=None, legs=None):
    """The self-test's positive and negative legs over root's opf/, returning the self-test's exit code
    (_self_test_outcome). legs restricts the positive leg's sweep to those leg names (one CI job's
    part of _CI_JOB_LEGS), None sweeps every leg; the negative leg (the flip, which runs opf.py
    --self-test under the opf-tooling-selftest bound) runs only when legs is None or holds that leg,
    since the flip is that leg's work and budget, so every other job's self-test judges its positive
    leg alone and says so. Any exception or SystemExit running the positive leg or judging it (from
    run_closure or _positive_verdict) leaves it unevaluated (2), named by its type, and the negative leg
    still runs (where this job runs it), so a flip not caught beside it is still a failure (1). The
    negative leg's output is compared as raw bytes (run_one with raw True), and a flip caught that way
    must also come back from _report as closure BROKEN with exit 1, or it is a failure (1): a gate that
    reported the break as not evaluated would provide no coverage.
    A temporary directory that cannot be created, fixture drift in the flip, any exception or SystemExit
    building the flipped copy (a failed copy, or a store that cannot be read or decoded), any exception
    or SystemExit running or judging the flipped leg, and an incomplete cleanup after the flipped leg's
    kill each leave the flip unevaluated (2), named (an exception by its type), never a traceback and
    never an exit the raiser chose. run_closure and run_one default to _run_closure and _run_one; the
    routing vectors pass stubs (_verdict_routing_self_test), so the wiring they hold is this one."""
    run_closure = _run_closure if run_closure is None else run_closure
    run_one = _run_one if run_one is None else run_one

    # Positive: the real opf/ subtree verifies itself in isolation (this job's legs).
    try:
        verdicts = [_positive_verdict(*(run_closure(root) if legs is None
                                        else run_closure(root, None, legs)))]
    except (Exception, SystemExit) as exc:
        verdicts = [(2, "positive: harness fault running or judging the positive leg (closure was not "
                        "evaluated for the real opf/): {}: {}".format(type(exc).__name__, exc))]

    if legs is not None and _FLIP_LEG not in legs:
        print("NEGATIVE LEG NOT RUN HERE: the deliberate flip runs opf.py --self-test under the {} "
              "bound, so the CI job whose legs hold that leg runs it".format(_FLIP_LEG))
        return _self_test_outcome(verdicts)

    # Negative (deliberate flip): a surviving upward edge must be caught.
    opf_src = Path(root) / "opf"
    try:
        tmp = Path(tempfile.mkdtemp(prefix="opf-closure-neg-"))
    except (Exception, SystemExit) as exc:
        verdicts.append((2, "negative: could not create the temporary directory for the flipped copy "
                            "(the flip was not evaluated): {}: {}".format(type(exc).__name__, exc)))
        return _self_test_outcome(verdicts)
    try:
        try:
            opf_root = _materialize(opf_src, tmp)
            store = opf_root / "tools" / "_opf_store.py"
            text = store.read_text(encoding="utf-8")
            flipped = text.replace("from _semver import _parse",
                                   "from check_versions import _parse", 1)
            if flipped != text:
                store.write_text(flipped, encoding="utf-8")
        except (Exception, SystemExit) as exc:
            verdicts.append((2, "negative: harness error building the broken copy (the flip was not "
                                "evaluated): {}: {}".format(type(exc).__name__, exc)))
        else:
            if flipped == text:
                verdicts.append((2, "negative: could not locate the _semver import to flip (fixture "
                                    "drift; the flip was not evaluated)"))
            else:
                try:
                    bound = required["opf-tooling-selftest"][0]
                    rc, output, elapsed, launched, cleanup = run_one(
                        opf_root, "opf.py", ["--self-test"], tmp, _isolated_env(), bound, raw=True)
                    found = [_negative_verdict(rc, output, bound, launched)]
                    if found[0] is None:
                        # The caught flip's REAL result also goes through _report: the upward edge must
                        # be reported as closure BROKEN with exit 1, never as closure-not-evaluated
                        # (the flip's bootstrap failure exits 2 with a lowercase "(cannot evaluate)",
                        # which is not the marker).
                        out = io.StringIO()
                        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
                            code = _report([(_FLIP_LEG, rc, _tail(output), elapsed, launched, bound,
                                             cleanup)])
                        said = out.getvalue()
                        if code != 1 or "STANDALONE CLOSURE: BROKEN" not in said:
                            found = [(1, "negative: a re-introduced upward edge (import check_versions) "
                                         "was NOT reported as closure broken (the gate would give no "
                                         "coverage): _report returned {} on the leg's rc={!r} and said "
                                         "{!r}".format(code, rc, said))]
                    if cleanup:
                        found.append((2, "negative: the cleanup after the flipped leg's kill did not "
                                         "complete: {}".format("; ".join(cleanup))))
                except (Exception, SystemExit) as exc:
                    found = [(2, "negative: harness fault running or judging the flipped leg (the flip "
                                 "was not evaluated): {}".format(_fault_text(exc)))]
                verdicts.extend(found)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return _self_test_outcome(verdicts)


def _verdict_routing_self_test():
    """The self-test's own exit follows the gate's convention. run() exit 2 (a leg killed at its bound,
    ended by a signal, or never launched, or a run stopped before any leg result: a refusal or a
    harness fault) makes the positive leg NOT EVALUATED, naming each such leg with its bound or signal,
    or the stop, and the self-test exit 2, never the positive-failure text or exit 1; run() exit 1 is
    the real opf/ failing to verify (1), or, with no leg result, the registration drift it names (1),
    never read as the real opf/ failing to verify; 0 is clean; a flip outcome that is not the exact
    failure leaves the self-test at 2, as do fixture drift in the flip, a failed copy, a SystemExit
    building the flipped copy, a store whose bytes are not UTF-8, a runner that raises (a SystemExit
    included, and an exception carrying an incomplete cleanup as a note, which the text keeps), a
    temporary directory that cannot be created (an OSError, any other exception or a SystemExit from
    mkdtemp) and an incomplete cleanup after the flipped leg's kill, each named, never
    the exit a SystemExit carries; a SystemExit or another exception running the positive leg, and a
    SystemExit judging it (_positive_verdict), leave the positive leg unevaluated (2), named, with the
    flip still run; a flip caught as the exact bytes that _report gives exit 2 (even saying BROKEN),
    or exit 1 without the BROKEN verdict, is a failure (1); and a definite failure outranks a leg that
    could not be evaluated, a flip not caught beside such a positive fault included.
    The wiring is held through _closure_legs, the legs self_test_main runs, with both runners stubbed
    over a throwaway tree that holds only the import the flip rewrites: the copy and the flip are real
    (but in the one case that replaces the copy with a SystemExit), and nothing is launched."""
    killed = ("opf-doctor-selftest", None, "partial", 1140.3, True, 1140, [])
    clean = ("opf-homes-contract", 0, "", 0.4, True, 120, [])
    failed = ("opf-homes-contract", 1, "boom", 1.0, True, 120, [])
    drift = "registration drift: closure/import-batch-registration"
    fault = "harness fault before, around or after the legs (AssertionError: unrelated)"
    for code, rows, stopped, want_code, want in (
            (2, [killed, clean], None, 2, "opf-doctor-selftest killed at its 1140s bound"),
            (2, [("opf-tooling-selftest", -9, "", 12.0, True, 1458, []), clean], None, 2,
             "opf-tooling-selftest ended by SIGKILL (signal 9)"),
            (2, [("opf-homes-contract", 2, "could not launch the interpreter: boom", 0.0, False, 120,
                  [])], None, 2, "opf-homes-contract never launched"),
            (2, [killed[:6] + (["leader-unreaped: leader pid 4242"],)], None, 2,
             "opf-doctor-selftest cleanup incomplete: leader-unreaped"),
            (2, [("opf-adopt-apply-selftest", 2,
                  "OPF-ADOPT-APPLY SELF-TEST: CANNOT EVALUATE (1 bound(s) expired)", 354.2, True, 4086, []),
                 clean], None, 2,
             "opf-adopt-apply-selftest exited 2 and printed CANNOT EVALUATE"),
            (2, None, None, 2, "stopped before any leg result: its reason is printed above"),
            (2, None, fault, 2, "stopped before any leg result: " + fault),
            (1, None, drift, 1, drift),
            (1, [failed], None, 1, "did not verify")):
        verdict = _under_test("positive-verdict", _positive_verdict, code, rows, stopped)
        if (verdict is None or verdict[0] != want_code or want not in verdict[1]
                or ((want_code == 2 or rows is None) and "did not verify" in verdict[1])):
            raise _CheckFailure("closure/positive-verdict (run() exit {}): {!r}, want exit {} naming "
                                "{!r}".format(code, verdict, want_code, want))
    if _under_test("positive-verdict-rejects-a-clean-run", _positive_verdict, 0, [clean],
                   None) is not None:
        raise _CheckFailure("closure/positive-verdict-rejects-a-clean-run")
    caught = (_FLIP_EXIT, _FLIP_BYTES, 0.1, True, [])
    flips = []

    def positive_runner(result):
        def run_closure(_root):
            if isinstance(result, BaseException):
                raise result
            return result
        return run_closure

    def flip_runner(result):
        def run_one(opf_root, script, args, _run_dir, _env, bound, raw=False):
            flips.append(((opf_root / "tools" / "_opf_store.py").read_text(encoding="utf-8"), script,
                          args, bound, raw))
            if isinstance(result, BaseException):
                raise result
            return result
        return run_one

    def full_disk(*_args, **_kwargs):
        raise OSError(errno.ENOSPC, "No space left on device (injected)")

    def mkdtemp_fault(*_args, **_kwargs):
        raise RuntimeError("mkdtemp fault (injected)")

    def exits(*_args, **_kwargs):
        raise SystemExit(0)

    tmp = Path(tempfile.mkdtemp(prefix="opf-closure-routing-"))
    try:
        # flip/ holds the import the flip rewrites; drift/ holds a store without it (fixture drift);
        # undecodable/ holds the import beside a byte that is not UTF-8, so reading the store fails (a
        # UnicodeDecodeError, a ValueError); absent/ holds no opf/ at all, so the copy itself fails (an
        # OSError).
        root, drifted, absent = tmp / "flip", tmp / "drift", tmp / "absent"
        undecodable = tmp / "undecodable"
        for base, store in ((root, b"from _semver import _parse\n"), (drifted, b"import os\n"),
                            (undecodable, b"from _semver import _parse\n\xff\n")):
            os.makedirs(base / "opf" / "tools")
            (base / "opf" / "tools" / "_opf_store.py").write_bytes(store)
        os.makedirs(absent)
        unreaped = "leader-unreaped: leader pid 4242 was still unreaped at the 10s cleanup deadline"
        # The note _run_one attaches when an exception ends its wait and the cleanup after it did not
        # complete.
        noted = OSError("read failure in the flipped leg (injected)")
        noted.add_note("CLEANUP INCOMPLETE after the leg's OSError: " + unreaped)
        flipped = [("from check_versions import _parse\n", "opf.py", ["--self-test"], 1458, True)]
        positive_fault = ("SELF-TEST CANNOT EVALUATE: positive: harness fault running or judging the "
                          "positive leg (closure was not evaluated for the real opf/): ")
        for label, base, mkdtemp, positive, negative, want_code, want, refused, want_flips in (
                ("bound-kill", root, None, (2, [killed, clean], None), caught, 2,
                 ("SELF-TEST CANNOT EVALUATE: positive", "opf-doctor-selftest killed at its 1140s bound"),
                 ("SELF-TEST FAIL", "did not verify"), flipped),
                ("refused-before-any-leg", root, None, (2, None, None), caught, 2,
                 ("stopped before any leg result",), ("SELF-TEST FAIL",), flipped),
                ("harness-fault-before-the-legs", root, None, (2, None, fault), caught, 2,
                 ("SELF-TEST CANNOT EVALUATE: positive", fault), ("SELF-TEST FAIL",), flipped),
                ("registration-drift", root, None, (1, None, drift), caught, 1,
                 ("SELF-TEST FAIL: positive: " + drift,), ("did not verify",), flipped),
                ("unrelated-flip-failure-naming-the-token", root, None, (0, [clean], None),
                 (1, b"unrelated assertion failed; debug path /tmp/check_versions/cache\n", 0.1, True,
                  []), 2, ("SELF-TEST CANNOT EVALUATE: negative",), ("SELF-TEST FAIL",), flipped),
                ("flip-not-caught-beside-a-bound-kill", root, None, (2, [killed], None),
                 (0, b"SELF-TEST PASS\n", 0.1, True, []), 1,
                 ("SELF-TEST FAIL: negative", "SELF-TEST CANNOT EVALUATE: positive"), (), flipped),
                ("positive-failure", root, None, (1, [failed], None), caught, 1,
                 ("SELF-TEST FAIL: positive", "did not verify"), (), flipped),
                # The flipped leg killed at its bound with its cleanup incomplete: the cleanup verdict
                # is kept beside the bound kill, naming what did not complete.
                ("negative-cleanup-incomplete", root, None, (0, [clean], None),
                 (None, b"partial", 1458.2, True, [unreaped]), 2,
                 ("killed at its 1458s bound",
                  "the cleanup after the flipped leg's kill did not complete: " + unreaped),
                 ("SELF-TEST FAIL",), flipped),
                ("fixture-drift", drifted, None, (0, [clean], None), caught, 2,
                 ("SELF-TEST CANNOT EVALUATE: negative: could not locate the _semver import to flip "
                  "(fixture drift",), ("SELF-TEST FAIL",), []),
                ("copy-oserror", absent, None, (0, [clean], None), caught, 2,
                 ("SELF-TEST CANNOT EVALUATE: negative: harness error building the broken copy",),
                 ("SELF-TEST FAIL",), []),
                ("invalid-source-bytes", undecodable, None, (0, [clean], None), caught, 2,
                 ("SELF-TEST CANNOT EVALUATE: negative: harness error building the broken copy (the "
                  "flip was not evaluated): UnicodeDecodeError:",), ("SELF-TEST FAIL",), []),
                ("runner-fault", root, None, (0, [clean], None),
                 RuntimeError("runner fault in the flipped leg"), 2,
                 ("SELF-TEST CANNOT EVALUATE: negative: harness fault running or judging the flipped "
                  "leg (the flip was not evaluated): RuntimeError: runner fault in the flipped leg",),
                 ("SELF-TEST FAIL",), flipped),
                ("runner-fault-with-an-incomplete-cleanup", root, None, (0, [clean], None), noted, 2,
                 ("SELF-TEST CANNOT EVALUATE: negative: harness fault running or judging the flipped "
                  "leg (the flip was not evaluated): OSError: read failure in the flipped leg "
                  "(injected); CLEANUP INCOMPLETE after the leg's OSError: " + unreaped,),
                 ("SELF-TEST FAIL",), flipped),
                ("temporary-directory-oserror", root, full_disk, (0, [clean], None), caught, 2,
                 ("SELF-TEST CANNOT EVALUATE: negative: could not create the temporary directory for "
                  "the flipped copy", "[Errno {}] No space left on device (injected)".format(
                      errno.ENOSPC)), ("SELF-TEST FAIL",), []),
                # REGRESSION COVERAGE for the handler that takes every exception creating the flipped
                # copy's temporary directory: an exception other than an OSError is named, exit 2, and
                # a handler narrowed back to OSError lets it escape _closure_legs (a named check
                # failure here).
                ("temporary-directory-other-exception", root, mkdtemp_fault, (0, [clean], None),
                 caught, 2,
                 ("SELF-TEST CANNOT EVALUATE: negative: could not create the temporary directory for "
                  "the flipped copy (the flip was not evaluated): RuntimeError: mkdtemp fault "
                  "(injected)",), ("SELF-TEST FAIL",), []),
                # A SystemExit creating the flipped copy's temporary directory, or raised by the
                # flipped leg's runner, is named, exit 2, never the exit 0 it carries.
                ("temporary-directory-system-exit", root, exits, (0, [clean], None), caught, 2,
                 ("SELF-TEST CANNOT EVALUATE: negative: could not create the temporary directory for "
                  "the flipped copy (the flip was not evaluated): SystemExit: 0",),
                 ("SELF-TEST FAIL",), []),
                ("runner-system-exit", root, None, (0, [clean], None), SystemExit(0), 2,
                 ("SELF-TEST CANNOT EVALUATE: negative: harness fault running or judging the flipped "
                  "leg (the flip was not evaluated): SystemExit: 0",), ("SELF-TEST FAIL",), flipped),
                # A SystemExit or another exception from the positive leg's run is named, exit 2, never
                # the exit 0 it carries and never a traceback; the negative leg still runs, so a flip
                # not caught beside it is still a failure (1).
                ("positive-system-exit", root, None, SystemExit(0), caught, 2,
                 (positive_fault + "SystemExit: 0",), ("SELF-TEST FAIL",), flipped),
                ("positive-runner-fault", root, None, RuntimeError("positive runner fault"), caught, 2,
                 (positive_fault + "RuntimeError: positive runner fault",), ("SELF-TEST FAIL",),
                 flipped),
                ("positive-system-exit-beside-a-flip-not-caught", root, None, SystemExit(0),
                 (0, b"SELF-TEST PASS\n", 0.1, True, []), 1,
                 ("SELF-TEST FAIL: negative", positive_fault + "SystemExit: 0"), (), flipped),
                # REGRESSION COVERAGE: a clean run passes whether or not any routing above is in place.
                ("clean", root, None, (0, [clean], None), caught, 0, (), ("SELF-TEST",), flipped)):
            del flips[:]
            out = io.StringIO()
            real_mkdtemp = tempfile.mkdtemp
            if mkdtemp is not None:
                tempfile.mkdtemp = mkdtemp
            try:
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
                    code = _under_test("self-test-routing: " + label, _closure_legs, base,
                                       {"opf-tooling-selftest": (1458, 486, 0)},
                                       run_closure=positive_runner(positive),
                                       run_one=flip_runner(negative))
            finally:
                tempfile.mkdtemp = real_mkdtemp
            said = out.getvalue()
            if (code != want_code or not all(w in said for w in want) or any(r in said for r in refused)
                    or flips != want_flips):
                raise _CheckFailure("closure/self-test-routing: {} (exit {}, want {}; flips {!r}): "
                                    "{!r}".format(label, code, want_code, flips, said))
        # A SystemExit building the flipped copy (here from the copy itself, the module name
        # _closure_legs looks up rebound) is named, exit 2, never the exit 0 it carries; nothing is
        # launched.
        del flips[:]
        out = io.StringIO()
        with _swapped(_materialize=exits):
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
                code = _under_test("self-test-routing: copy-system-exit", _closure_legs, root,
                                   {"opf-tooling-selftest": (1458, 486, 0)},
                                   run_closure=lambda _root: (0, [clean], None),
                                   run_one=flip_runner(caught))
        said = out.getvalue()
        if (code != 2 or "SELF-TEST FAIL" in said or flips
                or "SELF-TEST CANNOT EVALUATE: negative: harness error building the broken copy (the "
                   "flip was not evaluated): SystemExit: 0" not in said):
            raise _CheckFailure("closure/self-test-routing: copy-system-exit (exit {}, want 2; flips "
                                "{!r}): {!r}".format(code, flips, said))
        # A SystemExit judging the positive leg (here from _positive_verdict, the module name
        # _closure_legs looks up rebound) is named, exit 2, never the exit 0 it carries; the flip
        # still runs.
        del flips[:]
        out = io.StringIO()
        with _swapped(_positive_verdict=exits):
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
                code = _under_test("self-test-routing: positive-verdict-system-exit", _closure_legs, root,
                                   {"opf-tooling-selftest": (1458, 486, 0)},
                                   run_closure=lambda _root: (0, [clean], None),
                                   run_one=flip_runner(caught))
        said = out.getvalue()
        if (code != 2 or "SELF-TEST FAIL" in said or flips != flipped
                or positive_fault + "SystemExit: 0" not in said):
            raise _CheckFailure("closure/self-test-routing: positive-verdict-system-exit (exit {}, want "
                                "2; flips {!r}): {!r}".format(code, flips, said))
        # A flip caught as the exact bytes is also judged by _report (the module name _closure_legs
        # looks up rebound): a report of exit 2 (even one that says BROKEN) or of exit 1 without the
        # BROKEN verdict is a failure (1), and _report must be given the flipped leg's own row.
        for label, report_code, report_said in (
                ("flip-reported-as-exit-2", 2, "STANDALONE CLOSURE: BROKEN (injected)"),
                ("flip-reported-without-broken", 1,
                 "STANDALONE CLOSURE: LEG CANNOT EVALUATE (injected)")):
            del flips[:]
            reported = []

            def report(results, report_code=report_code, report_said=report_said):
                reported.append([tuple(row[:6]) for row in results])
                print(report_said)
                return report_code

            out = io.StringIO()
            with _swapped(_report=report):
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
                    code = _under_test("self-test-routing: " + label, _closure_legs, root,
                                       {"opf-tooling-selftest": (1458, 486, 0)},
                                       run_closure=lambda _root: (0, [clean], None),
                                       run_one=flip_runner(caught))
            said = out.getvalue()
            if (code != 1 or flips != flipped
                    or reported != [[(_FLIP_LEG, _FLIP_EXIT, _tail(_FLIP_BYTES), 0.1, True, 1458)]]
                    or "SELF-TEST FAIL: negative: a re-introduced upward edge (import check_versions) "
                       "was NOT reported as closure broken" not in said
                    or "_report returned {} on the leg's rc={!r}".format(report_code, _FLIP_EXIT)
                    not in said):
                raise _CheckFailure("closure/self-test-routing: {} (exit {}, want 1; flips {!r}; "
                                    "reported {!r}): {!r}".format(label, code, flips, reported, said))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("RED run() exit 2 (a leg killed at its bound, a signal, a leg never launched, an incomplete "
          "cleanup, or a run stopped before any leg result by a refusal or a harness fault) -> the "
          "positive leg CANNOT EVALUATE naming each leg and its bound or signal, or the stop, self-test "
          "exit 2, never the positive-failure text; run() exit 1 -> exit 1, and registration drift is "
          "named as itself, never as the real opf/ failing to verify; an unrelated flip failure naming "
          "the token -> exit 2; the flip not caught beside a bound kill -> exit 1; the flipped leg's "
          "incomplete cleanup -> named beside its bound kill, exit 2; fixture drift in the flip, a "
          "failed copy, a store whose bytes are not UTF-8 and a temporary directory that cannot be "
          "created (an OSError, another exception or a SystemExit) -> each named, exit 2, nothing "
          "launched; a runner that raises (an exception or a SystemExit, or one carrying an incomplete "
          "cleanup as a note, kept in the text) and a SystemExit building the "
          "flipped copy -> named, exit 2; a SystemExit or another exception running the positive leg, "
          "and a SystemExit judging it -> the positive leg CANNOT EVALUATE, named, exit 2, the flip "
          "still run, and exit 1 beside a flip not caught; a caught flip that _report gives exit 2 "
          "(even saying BROKEN) or exit 1 without the BROKEN verdict -> exit 1")


def _self_test_phase(phase):
    """Run the self-test's fast phase, phase(), returning (None, its result), or (exit code, None) with
    the reason printed. The exception's TYPE routes it, never its message. A _CheckFailure, a check
    failing on its own assertion, is a self-test failure (1); that includes a crash or a SystemExit in
    the code under test a check calls, which the check converts into one (_under_test). A
    _CannotEvaluate, a check that cannot evaluate what it checks (the CI job limit cannot be read, or
    there is no opf/tools/), is
    cannot evaluate (2), named by its message. Every other exception, and a SystemExit raised outside
    such a call, is a harness fault, cannot evaluate (2), named by its type, never a self-test failure,
    never a traceback and never the exit a SystemExit carries: a _BoundError (a bound that cannot be
    derived), an OSError, a RuntimeError, a ValueError (a UnicodeDecodeError included) and an
    AssertionError of any other type, whatever its message ("closure/..." included). A
    KeyboardInterrupt is not caught (an operator's interrupt ends the run). _harness_routing_self_test
    and _check_routing_self_test hold this routing, and self_test_main's wiring to it."""
    try:
        return None, phase()
    except _CheckFailure as exc:
        print("SELF-TEST FAIL:", str(exc), file=sys.stderr)
        return 1, None
    except _CannotEvaluate as exc:
        print("SELF-TEST CANNOT EVALUATE: {} (a check that cannot evaluate what it checks, never a "
              "self-test failure)".format(exc), file=sys.stderr)
        return 2, None
    except (Exception, SystemExit) as exc:
        print("SELF-TEST CANNOT EVALUATE: {}: {} (a harness fault in the fast phase, never a self-test "
              "failure)".format(type(exc).__name__, exc), file=sys.stderr)
        return 2, None


@contextlib.contextmanager
def _swapped(**values):
    """Each named global of this module rebound to its value for the block and restored after it on
    every path. The routing vectors' stand-ins (a drifted roster, a registration check, a runner, a
    fast phase) reach run() and self_test_main only where those look the names up, so the path held is
    the one the gate and CI run."""
    namespace = globals()
    saved = dict((name, namespace[name]) for name in values)
    namespace.update(values)
    try:
        yield
    finally:
        namespace.update(saved)


def _throwaway_opf(base):
    """Write a throwaway opf/ under base for the routing vectors: every subset script as an empty
    source, and an opf.py whose _self_tests() registry names one helper self-test, so the copy, the
    roster's script check and the bound scan over it are real. Returns the opf/tools/ directory."""
    tools = Path(base) / "opf" / "tools"
    os.makedirs(tools)
    for _name, script, _args in _SUBSET:
        (tools / script).write_text("", encoding="utf-8")
    (tools / "opf.py").write_text("def _self_tests():\n    return [_helper.self_test]\n",
                                  encoding="utf-8")
    (tools / "_helper.py").write_text("", encoding="utf-8")
    return tools


def _harness_routing_self_test():
    """What the gate meets is routed by what it is, through run() and _run_closure alike, each case
    reaching them only by rebinding the module names they look up (_swapped). REGISTRATION DRIFT (a
    registration check failing on its OWN assertion) is a definite finding: exit 1, each failing check
    named with what it found and no other, no leg launched and nothing read as a bound that cannot be
    derived, and the self-test's positive leg names it, never as the real opf/ failing to verify. The
    drift shapes: a row the tuple checks do not pin removed, a row added, a row repeated, such a leg
    renamed, its script renamed to a file opf/tools/ does not hold, its ceiling row removed, the row
    removed together with its ceiling row (only the pin names it), its script an absolute path or a
    '..' path to a file that exists outside opf/tools/, opf.py removed from an opf/tools/ that is
    otherwise whole, a roster script that is a symlink (to a file outside opf/tools/, to a file beside
    it in opf/tools/, or to itself, a loop), every roster script reached through an opf/tools/ or an
    opf/ that is itself a symlink, a script the copy leaves out (a *.pyc file opf/tools/ holds), and
    roster drift and an absolute script path each beside a missing opf/ (named before the missing
    opf/ is); then, for the rows the tuple checks pin, two rows removed, a row's
    arguments changed, its name renamed and its script renamed to a missing file. A runner that passes
    every leg stands in for the legs in each drift case, so a drift the checks missed would run clean
    (exit 0) or stop at the bound scan or at the missing opf/ (exit 2), never exit 1. The renamed
    shapes also discriminate the order: a gate that derived the bounds first would stop there (exit 2)
    and never name the drift. Any other exception is a HARNESS FAULT, cannot evaluate (exit 2), named:
    a registration check raising an unrelated AssertionError, a _CheckFailure carrying another check's
    message, or an AssertionError of another type carrying its OWN message (the type routes it: never
    read as drift, and never as no drift), an opf/tools/ entry whose presence cannot be checked (os.lstat
    raising ELOOP for opf.py, injected: never read as a missing script), an unrelated AssertionError or
    a SystemExit raised before the first leg, a _CheckFailure carrying a registration check's message
    that a launch raised rather than the check, an exception raised as the first leg starts, an OSError
    creating the temporary directory (as on a full disk), two symlinks in opf/ that are not roster
    scripts and resolve outside the copy (one climbing out through '..', one absolute: the copy keeps
    each as a link and is refused), and a leg result the report cannot read (an empty row). A missing
    opf/ with a clean roster is cannot evaluate (2), never drift: a plain script's presence is not
    checked when there is no opf/tools/. A run whose every leg passes is 0, beside a symlink in
    opf/tools/ that resolves inside the copy too.
    Each run is over a throwaway opf/ (_throwaway_opf); every launch is recorded and refused (the clean
    runner records each leg and reports it passed, launching nothing).
    The self-test's fast phase routes by type the same way (_self_test_phase): a _CheckFailure is a
    self-test failure (1); a _CannotEvaluate, a _BoundError, an OSError, a RuntimeError, a
    UnicodeDecodeError, an AssertionError of another type, a "closure/" message included, and a
    SystemExit are cannot evaluate (2), named, and none escapes. self_test_main is held on that wiring:
    a fast phase that raises (a SystemExit included) returns the phase's code before any closure leg
    runs, and one that returns hands the repository root and its bounds to the closure legs, whose code
    self_test_main returns, printing PASS only on 0; a SystemExit or another exception escaping the
    closure legs is a harness fault, cannot evaluate (2), named."""
    launches = []
    before = "unrelated assertion before the first leg"
    starts = "runner fault as the first leg starts"

    def launch_raising(exc):
        def popen(argv, **_kwargs):
            launches.append(Path(argv[3]).name)
            raise exc
        return popen

    refuse = launch_raising(OSError("launch refused by the harness-routing vector"))

    def clean_leg(_opf_root, script, *_args, **_kwargs):
        launches.append(script)
        return 0, "", 0.1, True, []

    def before_the_first_leg(_opf_root, _run_dir, _bounds):
        raise AssertionError(before)

    def full_disk(*_args, **_kwargs):
        raise OSError(errno.ENOSPC, "No space left on device (injected)")

    def exits(*_args, **_kwargs):
        raise SystemExit(0)

    real_lstat = os.lstat

    def lstat_fault(path, *args, **kwargs):
        if Path(path).name == "opf.py":
            raise OSError(errno.ELOOP, "Too many levels of symbolic links (injected)", str(path))
        return real_lstat(path, *args, **kwargs)

    def replaced(name, row):
        return [row if entry[0] == name else entry for entry in _SUBSET]

    def contract_script(script):
        return replaced("opf-homes-contract", ("opf-homes-contract", script, []))

    def checks_with(name, exc):
        def check(_subset, _tools_dir):
            raise exc
        return tuple((entry, check if entry == name else real)
                     for entry, real in _REGISTRATION_CHECKS)

    names = [name for name, _check in _REGISTRATION_CHECKS]
    first = _SUBSET[0][1]
    every = [script for _name, script, _args in _SUBSET]
    legs = len(_SUBSET)
    fault = "harness fault before, around or after the legs ("
    full = "[Errno {}] No space left on device (injected)".format(errno.ENOSPC)
    roster = "closure/roster-registration: "
    no_contract = [row for row in _SUBSET if row[0] != "opf-homes-contract"]
    ceilings = dict(_LEG_CEILING_S)
    del ceilings["opf-homes-contract"]
    tmp = Path(tempfile.mkdtemp(prefix="opf-closure-harness-"))
    try:
        _throwaway_opf(tmp)
        absent = tmp / "no-opf"
        os.makedirs(absent)
        # outside.py exists beside the throwaway opf/, outside its opf/tools/; climb names it through
        # '..' alone, with more '..' than the throwaway's opf/tools/ is deep (the copy's lies as deep,
        # under the same temporary directory), so it reaches the same file from either and a gate
        # that accepted it would launch it; no-opf-py holds every roster script but opf.py;
        # script-loop holds them all, opf.py a symlink to itself (a loop); link-out and link-in each
        # add check_opf_homes_link.py, a symlink to outside.py and to check_opf_homes.py beside it;
        # tools-link's opf/tools/ is itself a symlink to elsewhere's, which holds every roster
        # script, and opf-link's opf/ one to elsewhere's opf/; bytecode adds check_opf_homes.pyc, a
        # file the copy leaves out; leaving-links adds two symlinks that leave opf/, one climbing out
        # through '..' and one absolute, and inner-link one to _helper.py beside it, none of them a
        # roster script.
        outside = tmp / "outside.py"
        outside.write_text("", encoding="utf-8")
        climb = "../" * (len(tmp.resolve().parts) + 4) + str(outside.relative_to(outside.anchor))
        no_opf_py = tmp / "no-opf-py"
        os.remove(_throwaway_opf(no_opf_py) / "opf.py")
        looped = tmp / "script-loop"
        loop = _throwaway_opf(looped) / "opf.py"
        os.remove(loop)
        os.symlink(loop.name, loop)
        link_out, link_in = tmp / "link-out", tmp / "link-in"
        out_tools = _throwaway_opf(link_out)
        out_text = os.path.relpath(outside, out_tools)
        os.symlink(out_text, out_tools / "check_opf_homes_link.py")
        os.symlink("check_opf_homes.py", _throwaway_opf(link_in) / "check_opf_homes_link.py")
        elsewhere = _throwaway_opf(tmp / "elsewhere")
        tools_link, opf_link = tmp / "tools-link", tmp / "opf-link"
        os.makedirs(tools_link / "opf")
        os.symlink(elsewhere, tools_link / "opf" / "tools")
        os.makedirs(opf_link)
        os.symlink(elsewhere.parent, opf_link / "opf")
        relocated = "; ".join(
            "script {} of row {} resolves to {}, outside opf/tools/".format(
                script, name, os.path.join(os.path.realpath(elsewhere), script))
            for name, script, _args in _SUBSET)
        bytecode = tmp / "bytecode"
        (_throwaway_opf(bytecode) / "check_opf_homes.pyc").write_bytes(b"")
        leaving, inner = tmp / "leaving-links", tmp / "inner-link"
        leaving_tools = _throwaway_opf(leaving)
        os.symlink(os.path.relpath(outside, leaving_tools), leaving_tools / "_helper_climbs.py")
        os.symlink(str(outside), leaving_tools / "_helper_absolute.py")
        os.symlink("_helper.py", _throwaway_opf(inner) / "_helper_alias.py")
        copy_refused = ("could not materialize the isolated opf/ copy: the copy holds 2 symlink(s) "
                        "resolving outside its opf/: opf/tools/_helper_absolute.py -> {!r}; "
                        "opf/tools/_helper_climbs.py -> {!r}".format(
                            str(outside), os.path.relpath(outside, leaving_tools)))
        not_plain = "script {!r} of row opf-homes-contract is not a plain file name"
        linked = "script {} of row {} is a symlink in opf/tools/ (to {!r}), not the file itself"
        cases = [(label, base, dict(rebound, _run_one=clean_leg), refuse, None, 1,
                  ("STANDALONE CLOSURE: REGISTRATION DRIFT",)
                  + tuple("  registration drift: " + text for text in drifted),
                  ("NOT EVALUATED", "cannot derive", "CLOSURE: OK")
                  + tuple("registration drift: " + name for name in names
                          if name not in [text.split(": ", 1)[0] for text in drifted]),
                  "registration drift: " + "; ".join(drifted), [])
                 for label, base, rebound, drifted in (
                     # Rows the tuple checks do not pin: only the whole-roster check names these.
                     ("other-row-removed", tmp, {"_SUBSET": no_contract},
                      [roster + "row opf-homes-contract missing"]),
                     ("row-added", tmp,
                      {"_SUBSET": _SUBSET + [("opf-extra-selftest", "check_opf_homes.py",
                                              ["--self-test"])]},
                      [roster + "row opf-extra-selftest not in the ceiling table"]),
                     ("row-repeated", tmp, {"_SUBSET": _SUBSET + [_SUBSET[1]]},
                      [roster + "row opf-homes-contract listed 2 times"]),
                     ("other-leg-renamed", tmp,
                      {"_SUBSET": replaced("opf-homes-selftest",
                                           ("opf-homes-renamed", "check_opf_homes.py",
                                            ["--self-test"]))},
                      [roster + "row opf-homes-selftest missing; row opf-homes-renamed not in the "
                       "ceiling table"]),
                     ("other-script-renamed-to-a-missing-file", tmp,
                      {"_SUBSET": contract_script("check_opf_homes_renamed.py")},
                      [roster + "script check_opf_homes_renamed.py of row opf-homes-contract is not "
                       "a file in opf/tools/"]),
                     ("ceiling-row-removed", tmp, {"_LEG_CEILING_S": ceilings},
                      [roster + "row opf-homes-contract not in the ceiling table; "
                       + _pin_fault(ceilings)]),
                     # The two tables changed together still agree with each other: only the pin
                     # names this.
                     ("row-removed-with-its-ceiling-row", tmp,
                      {"_SUBSET": no_contract, "_LEG_CEILING_S": ceilings},
                      [roster + _pin_fault(ceilings)]),
                     # A script that is not a plain file name, naming a file that exists outside
                     # opf/tools/ (absolute, then through '..'): drift, never launched.
                     ("script-absolute-path", tmp, {"_SUBSET": contract_script(str(outside))},
                      [roster + not_plain.format(str(outside))]),
                     ("script-parent-path", tmp, {"_SUBSET": contract_script(climb)},
                      [roster + not_plain.format(climb)]),
                     # A roster script removed from an opf/tools/ that is otherwise whole, opf.py
                     # itself: drift, never read as a missing opf/.
                     ("opf-py-removed", no_opf_py, {},
                      [roster + "script opf.py of row opf-tooling-selftest is not a file in "
                       "opf/tools/"]),
                     # A roster script that is a symlink, whatever it points at (a file outside
                     # opf/tools/, a file beside it, itself), and every roster script reached through
                     # a symlinked opf/tools/ or opf/: drift, never launched and never read as a
                     # harness fault.
                     ("script-symlink-to-an-outside-file", link_out,
                      {"_SUBSET": contract_script("check_opf_homes_link.py")},
                      [roster + linked.format("check_opf_homes_link.py", "opf-homes-contract",
                                              out_text)]),
                     ("script-symlink-inside-opf-tools", link_in,
                      {"_SUBSET": contract_script("check_opf_homes_link.py")},
                      [roster + linked.format("check_opf_homes_link.py", "opf-homes-contract",
                                              "check_opf_homes.py")]),
                     ("script-symlink-loop", looped, {},
                      [roster + linked.format("opf.py", "opf-tooling-selftest", "opf.py")]),
                     ("opf-tools-a-symlink", tools_link, {}, [roster + relocated]),
                     ("opf-a-symlink", opf_link, {}, [roster + relocated]),
                     # A script the copy leaves out, though opf/tools/ holds it: drift, never a bound
                     # the copy cannot derive.
                     ("script-the-copy-leaves-out", bytecode,
                      {"_SUBSET": contract_script("check_opf_homes.pyc")},
                      [roster + "script 'check_opf_homes.pyc' of row opf-homes-contract is a name the "
                       "isolated copy leaves out (__pycache__, *.pyc)"]),
                     ("roster-drift-beside-a-missing-opf", absent, {"_SUBSET": no_contract},
                      [roster + "row opf-homes-contract missing"]),
                     ("script-path-beside-a-missing-opf", absent,
                      {"_SUBSET": contract_script(str(outside))},
                      [roster + not_plain.format(str(outside))]),
                     # Rows the tuple checks pin: the whole-roster check names a removal or a rename
                     # beside them.
                     ("two-rows-removed", tmp,
                      {"_SUBSET": [row for row in _SUBSET if row[0] not in (
                          "opf-adopt-observe-selftest", "opf-import-batch-selftest")]},
                      [roster + "row opf-import-batch-selftest missing; row opf-adopt-observe-selftest "
                       "missing", "closure/adopt-observe-registration",
                       "closure/import-batch-registration"]),
                     ("name-renamed", tmp,
                      {"_SUBSET": replaced("opf-pack-manifest-selftest",
                                           ("opf-pack-manifest-renamed", "_opf_pack_manifest.py",
                                            ["--self-test"]))},
                      [roster + "row opf-pack-manifest-selftest missing; row opf-pack-manifest-renamed "
                       "not in the ceiling table", "closure/pack-manifest-registration"]),
                     ("script-renamed-to-a-missing-file", tmp,
                      {"_SUBSET": replaced("opf-adopt-observe-selftest",
                                           ("opf-adopt-observe-selftest",
                                            "_opf_adopt_observe_renamed.py", ["--self-test"]))},
                      [roster + "script _opf_adopt_observe_renamed.py of row opf-adopt-observe-selftest "
                       "is not a file in opf/tools/", "closure/adopt-observe-registration"]),
                     # REGRESSION COVERAGE: the tuple check named this before the whole-roster check
                     # existed, and the whole-roster check does not hold arguments.
                     ("arguments-changed", tmp,
                      {"_SUBSET": replaced("opf-import-batch-selftest",
                                           ("opf-import-batch-selftest", "_opf_import_batch.py",
                                            ["--self-test", "--changed"]))},
                      ["closure/import-batch-registration"]))]
        for label, exc in (("unrelated-assertion-in-a-registration-check",
                            AssertionError("unrelated assertion in a registration check")),
                           ("another-checks-message-from-a-registration-check",
                            _CheckFailure("closure/pack-manifest-registration")),
                           ("own-message-as-another-assertion-type",
                            AssertionError("closure/import-batch-registration"))):
            raised = "{}: {}".format(type(exc).__name__, exc)
            cases.append((label, tmp, {"_REGISTRATION_CHECKS": checks_with(
                              "closure/import-batch-registration", exc)}, refuse, None, 2,
                          ("STANDALONE CLOSURE: NOT EVALUATED", raised), ("REGISTRATION DRIFT",),
                          fault + raised + ")", []))
        cases.extend((
            ("unrelated-assertion-before-the-first-leg", tmp, {"_run_subset": before_the_first_leg},
             refuse, None, 2, ("STANDALONE CLOSURE: NOT EVALUATED", "AssertionError: " + before),
             ("REGISTRATION DRIFT",), fault + "AssertionError: " + before + ")", []),
            # A SystemExit is a harness fault too, never the exit 0 it carries.
            ("system-exit-before-the-first-leg", tmp, {"_run_subset": exits}, refuse, None, 2,
             ("STANDALONE CLOSURE: NOT EVALUATED", "SystemExit: 0"), ("REGISTRATION DRIFT",),
             fault + "SystemExit: 0)", []),
            # An opf/tools/ entry whose presence cannot be checked (here os.lstat raising ELOOP for
            # opf.py, injected) is a harness fault, never drift: a presence check that read it as a
            # missing script would name drift (exit 1).
            ("script-entry-cannot-be-checked", tmp, {"_run_one": clean_leg}, refuse,
             ((os, "lstat", lstat_fault),), 2,
             ("STANDALONE CLOSURE: NOT EVALUATED", "OSError: [Errno {}]".format(errno.ELOOP)),
             ("REGISTRATION DRIFT", "CLOSURE: OK"),
             fault + "OSError: [Errno {}]".format(errno.ELOOP), []),
            # A symlink in opf/ that is not a roster script and resolves outside it (one climbing out
            # through '..', one absolute) is copied as the link itself, then refused as a harness
            # fault before any bound is derived or any leg launches: a copy that dereferenced it would
            # run clean (exit 0) with the outside file's code in it.
            ("symlinks-leaving-the-copy", leaving, {"_run_one": clean_leg}, refuse, None, 2,
             ("error: " + copy_refused,), ("REGISTRATION DRIFT", "CLOSURE: OK"), copy_refused, []),
            ("registration-message-from-a-launch", tmp, {},
             launch_raising(_CheckFailure("closure/import-batch-registration")), None, 2,
             ("STANDALONE CLOSURE: NOT EVALUATED", "_CheckFailure: closure/import-batch-registration"),
             ("REGISTRATION DRIFT",), fault + "_CheckFailure: closure/import-batch-registration)",
             [first]),
            ("exception-as-the-first-leg-starts", tmp, {}, launch_raising(RuntimeError(starts)), None,
             2, ("STANDALONE CLOSURE: NOT EVALUATED", "RuntimeError: " + starts),
             ("REGISTRATION DRIFT",), fault + "RuntimeError: " + starts + ")", [first]),
            ("temporary-directory-oserror", tmp, {}, refuse, ((tempfile, "mkdtemp", full_disk),), 2,
             ("could not create the temporary directory for the isolated opf/ copy", full),
             ("REGISTRATION DRIFT",),
             "could not create the temporary directory for the isolated opf/ copy: " + full, []),
            ("malformed-leg-result", tmp, {"_run_subset": lambda _opf_root, _run_dir, _bounds: [()]},
             refuse, None, 2, ("STANDALONE CLOSURE: NOT EVALUATED", "IndexError"),
             ("REGISTRATION DRIFT", "CLOSURE: OK", "BROKEN"), fault + "IndexError: ", []),
            # REGRESSION COVERAGE: a missing opf/ read as cannot evaluate before the whole-roster check
            # existed; this holds that check's skip of a plain script's presence with no opf/tools/
            # to check (a presence check run anyway would read every script as missing, drift, exit
            # 1).
            ("missing-opf-with-a-clean-roster", absent, {"_run_one": clean_leg}, refuse, None, 2,
             ("opf/tools/ not found under",), ("REGISTRATION DRIFT", "CLOSURE: OK"),
             "opf/tools/ not found under", []),
            # REGRESSION COVERAGE: a clean run passes whether or not any routing above is in place,
            # and so does one beside a symlink in opf/tools/ that resolves inside the copy (a copy
            # that refused every symlink would exit 2).
            ("clean", tmp, {"_run_one": clean_leg}, refuse, None, 0, ("STANDALONE CLOSURE: OK",),
             ("NOT EVALUATED", "REGISTRATION DRIFT", "BROKEN"), None, every),
            ("clean-beside-a-symlink-inside-the-copy", inner, {"_run_one": clean_leg}, refuse, None, 0,
             ("STANDALONE CLOSURE: OK",), ("NOT EVALUATED", "REGISTRATION DRIFT", "BROKEN"), None,
             every)))
        # patched is None, or the (module, attribute, value) triples set for the case's run and
        # restored after it on every path, as subprocess.Popen is.
        for (label, base, rebound, popen, patched, want_code, want, refused, want_stop,
             want_launches) in cases:
            for entry, call in (("run", run), ("_run_closure", _run_closure)):
                del launches[:]
                out = io.StringIO()
                saved = [(subprocess, "Popen", subprocess.Popen)] + [
                    (module, attribute, getattr(module, attribute))
                    for module, attribute, _value in patched or ()]
                subprocess.Popen = popen
                for module, attribute, value in patched or ():
                    setattr(module, attribute, value)
                try:
                    with _swapped(**rebound):
                        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
                            try:
                                got = call(base)
                            except (Exception, SystemExit) as exc:
                                raise _CheckFailure("closure/harness-routing: {} escaped {}: {}: "
                                                    "{}".format(label, entry, type(exc).__name__,
                                                                exc)) from exc
                finally:
                    for module, attribute, value in saved:
                        setattr(module, attribute, value)
                code, result = (got, None) if entry == "run" else (got[0], got)
                said = out.getvalue()
                wrong = (code != want_code or not all(w in said for w in want)
                         or any(r in said for r in refused) or launches != want_launches)
                if result is not None:
                    verdict = _under_test("harness-routing: " + label, _positive_verdict, *result)
                    if want_stop is None:
                        wrong = (wrong or verdict is not None or result[2] is not None
                                 or result[1] is None or len(result[1]) != legs)
                    else:
                        wrong = (wrong or result[1] is not None
                                 or not (result[2] or "").startswith(want_stop)
                                 or verdict is None or verdict[0] != want_code
                                 or result[2] not in verdict[1] or "did not verify" in verdict[1])
                if wrong:
                    raise _CheckFailure("closure/harness-routing: {} through {} (exit {}, want {}; "
                                        "launches {!r}; result {!r}): {!r}".format(
                                            label, entry, code, want_code, launches,
                                            result and (result[0], result[2]), said))
        print("RED registration drift through run(): a row the tuple checks do not pin removed, a row "
              "added or repeated, such a leg renamed, its script renamed to a missing file, its ceiling "
              "row removed, the row removed together with its ceiling row, its script an absolute or a "
              "'..' path to a file outside opf/tools/, opf.py removed from opf/tools/, a roster script "
              "that is a symlink (to a file outside opf/tools/, to one beside it, or to itself), every "
              "script reached through a symlinked opf/tools/ or opf/, a script the copy leaves out "
              "(*.pyc), the drift and an absolute script path beside a missing opf/, and for the pinned "
              "rows two rows removed, arguments changed, a name renamed and a script renamed to a "
              "missing file -> each failing "
              "check named with what it found and no other, exit 1, never a bound that cannot be "
              "derived, no leg launched, and the positive leg names the drift, never the real opf/ "
              "failing to verify; a registration check raising an unrelated AssertionError, another "
              "check's message or its own message as another type, an opf/tools/ entry that cannot be "
              "checked, an unrelated AssertionError or a SystemExit before the first leg, a "
              "registration message raised by a launch, an exception as the first leg starts, an "
              "OSError creating the temporary directory, symlinks in opf/ that resolve outside the copy "
              "(climbing out through '..', or absolute) and a leg result the report cannot read -> each "
              "a named harness fault, exit 2; a missing opf/ with a clean roster -> exit 2, never drift; "
              "every leg passing -> exit 0, a symlink resolving inside the copy beside them included")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    for label, phase, want, said_want in (
            # REGRESSION COVERAGE: these two read as cannot evaluate before the phase routed by type.
            ("bound-error", _raiser(_BoundError("leg x has no ceiling in _LEG_CEILING_S")), (2, None),
             "SELF-TEST CANNOT EVALUATE: _BoundError: leg x has no ceiling"),
            ("oserror", _raiser(OSError(errno.ENOSPC, "No space left on device (injected)")), (2, None),
             "SELF-TEST CANNOT EVALUATE: OSError: [Errno {}]".format(errno.ENOSPC)),
            ("runtime-error", _raiser(RuntimeError("harness fault in the fast phase")), (2, None),
             "SELF-TEST CANNOT EVALUATE: RuntimeError: harness fault in the fast phase"),
            ("unicode-decode-error",
             _raiser(UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")), (2, None),
             "SELF-TEST CANNOT EVALUATE: UnicodeDecodeError: "),
            ("assertion-no-check-raised", _raiser(AssertionError("unrelated assertion")), (2, None),
             "SELF-TEST CANNOT EVALUATE: AssertionError: unrelated assertion"),
            # A check's own "closure/" name on an AssertionError of another type: the type routes it,
            # never the message.
            ("closure-named-assertion-no-check-raised",
             _raiser(AssertionError("closure/foreign-harness")), (2, None),
             "SELF-TEST CANNOT EVALUATE: AssertionError: closure/foreign-harness"),
            ("cannot-evaluate",
             _raiser(_CannotEvaluate("closure/ci-job-limit-cannot-evaluate: job opf-closure-a appears "
                                     "0 times in the workflow (need exactly one)")), (2, None),
             "SELF-TEST CANNOT EVALUATE: closure/ci-job-limit-cannot-evaluate: job opf-closure-a "
             "appears 0 times"),
            # A SystemExit raised in the fast phase outside a check's call into the code under test is
            # a harness fault, never the exit 0 it carries.
            ("system-exit", _raiser(SystemExit(0)), (2, None),
             "SELF-TEST CANNOT EVALUATE: SystemExit: 0"),
            # REGRESSION COVERAGE: a check's failure and a clean phase read this way before the phase
            # routed by type.
            ("check-failure", _raiser(_CheckFailure("closure/a-vector")), (1, None),
             "SELF-TEST FAIL: closure/a-vector"),
            ("clean", lambda: "bounds", (None, "bounds"), None)):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            try:
                got = _self_test_phase(phase)
            except (Exception, SystemExit) as exc:
                raise _CheckFailure("closure/self-test-phase-routing: {} escaped the phase: {}: "
                                    "{}".format(label, type(exc).__name__, exc)) from exc
        said = out.getvalue()
        if got != want or (said_want not in said if said_want else said):
            raise _CheckFailure("closure/self-test-phase-routing: {} -> {!r}, want {!r}: {!r}".format(
                label, got, want, said))
    stand_in = Path("repository-root-stand-in")
    for label, phase, legs_code, want_code, want_said, refused in (
            ("fast-phase-runtime-error", _raiser(RuntimeError("harness fault in the fast phase")), None,
             2, "SELF-TEST CANNOT EVALUATE: RuntimeError: harness fault in the fast phase",
             "SELF-TEST PASS"),
            ("fast-phase-system-exit", _raiser(SystemExit(0)), None, 2,
             "SELF-TEST CANNOT EVALUATE: SystemExit: 0", "SELF-TEST PASS"),
            # REGRESSION COVERAGE: self_test_main stopped at a failing fast phase, and returned the
            # closure legs' code, printing PASS only on 0, before the fast phase routed by type.
            ("fast-phase-check-failure", _raiser(_CheckFailure("closure/a-vector")), None, 1,
             "SELF-TEST FAIL: closure/a-vector", "SELF-TEST PASS"),
            ("legs-cannot-evaluate", lambda: "bounds", 2, 2, None, "SELF-TEST PASS"),
            ("legs-failure", lambda: "bounds", 1, 1, None, "SELF-TEST PASS"),
            # Anything that escapes the closure legs is a harness fault, exit 2, never the exit 0 a
            # SystemExit carries and never a traceback (exit 1).
            ("legs-system-exit", lambda: "bounds", SystemExit(0), 2,
             "SELF-TEST CANNOT EVALUATE: SystemExit: 0 (a harness fault in the closure legs",
             "SELF-TEST PASS"),
            ("legs-runtime-error", lambda: "bounds", RuntimeError("closure legs fault"), 2,
             "SELF-TEST CANNOT EVALUATE: RuntimeError: closure legs fault (a harness fault in the "
             "closure legs", "SELF-TEST PASS"),
            ("clean", lambda: "bounds", 0, 0, "SELF-TEST PASS", None)):
        calls = []

        def fast_vectors(root, phase=phase):
            calls.append(("fast", root))
            return phase()

        def closure_legs(root, required, legs_code=legs_code):
            calls.append(("legs", root, required))
            if isinstance(legs_code, BaseException):
                raise legs_code
            return legs_code

        out = io.StringIO()
        with _swapped(repo_root=lambda: stand_in, _fast_vectors=fast_vectors,
                      _closure_legs=closure_legs):
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
                try:
                    code = self_test_main()
                except (Exception, SystemExit) as exc:
                    raise _CheckFailure("closure/self-test-main-wiring: {} escaped self_test_main: {}: "
                                        "{}".format(label, type(exc).__name__, exc)) from exc
        said = out.getvalue()
        want_calls = [("fast", stand_in)]
        if legs_code is not None:
            want_calls.append(("legs", stand_in, "bounds"))
        if (code != want_code or calls != want_calls or (want_said and want_said not in said)
                or (refused and refused in said)):
            raise _CheckFailure("closure/self-test-main-wiring: {} (exit {}, want {}; calls {!r}): "
                                "{!r}".format(label, code, want_code, calls, said))
    print("RED the self-test's fast phase, routed by type: a bound that cannot be derived, an OSError, "
          "a RuntimeError, a UnicodeDecodeError, an AssertionError of another type (a closure/ message "
          "included), a SystemExit and a check that cannot evaluate -> CANNOT EVALUATE, named, exit 2; "
          "a check's failure -> FAIL, exit 1; none escapes; self_test_main stops at the phase's code "
          "before any closure leg (exit 2 for a SystemExit, never the 0 it carries), otherwise "
          "returns the closure legs' code, printing PASS only on 0, and names a SystemExit or another "
          "exception escaping the closure legs as a harness fault, exit 2")


def _ci_job_sweep_self_test():
    """A --ci-job sweep runs exactly its job's legs, and anything else is refused, never the OK line.
    For each CI job, run() with that job's legs and main() with its --ci-job argument sweep each of
    the job's legs once, in roster order, and exit 0 (the control showing the strict check passes a
    correct sweep); a filter that drops every leg and one that ignores the restriction (the two
    mutant filters a sweep check must catch) are each refused (exit 2) with the missing or unexpected
    legs named and no OK line; a report over no rows is refused the same way (_report); and main()
    refuses an unrecognized argument (--ci-job=VALUE), a value naming no job, a repeated --ci-job and
    a partition holding a leg in no job, each before any leg runs. Every leg runs as a recording stub
    launching nothing (subprocess.Popen is a raiser throughout), so the wiring held is run()'s and
    main()'s own."""
    launches = []

    def clean_leg(_opf_root, script, *_args, **_kwargs):
        launches.append(script)
        return 0, "", 0.1, True, []

    real_run_subset = _run_subset

    def drop_every_leg(_opf_root, _run_dir, _bounds, _legs=None):
        return []

    def ignore_the_job(opf_root, run_dir, bounds, _legs=None):
        return real_run_subset(opf_root, run_dir, bounds)

    def refuse(_argv, **_kwargs):
        raise OSError("launch refused by the ci-job sweep vector")

    broken = dict(_CI_JOB_LEGS)
    broken["opf-closure-b"] = frozenset(broken["opf-closure-b"] - {"opf-prompt-pack"})
    scripts = dict((job, [script for name, script, _args in _SUBSET if name in _CI_JOB_LEGS[job]])
                   for job in _CI_JOBS)
    every_script = [script for _name, script, _args in _SUBSET]
    ok_line = "STANDALONE CLOSURE: OK"
    tmp = Path(tempfile.mkdtemp(prefix="opf-closure-jobs-"))
    real_popen = subprocess.Popen
    subprocess.Popen = refuse
    try:
        _throwaway_opf(tmp)
        cases = []
        for job in _CI_JOBS:
            job_legs = _CI_JOB_LEGS[job]
            unexpected = ", ".join(name for name, _script, _args in _SUBSET
                                   if name not in job_legs)
            cases.extend((
                ("run-" + job, {"_run_one": clean_leg},
                 lambda legs=job_legs: run(tmp, None, legs), None, 0, (ok_line,), (), scripts[job]),
                ("run-{}-swept-nothing".format(job),
                 {"_run_one": clean_leg, "_run_subset": drop_every_leg},
                 lambda legs=job_legs: run(tmp, None, legs), None, 2,
                 ("ran 0 leg(s), not exactly the {} leg(s)".format(len(job_legs)),
                  "missing: " + ", ".join(_job_legs(job_legs))), (ok_line,), []),
                ("run-{}-ignored-the-job".format(job),
                 {"_run_one": clean_leg, "_run_subset": ignore_the_job},
                 lambda legs=job_legs: run(tmp, None, legs), None, 2,
                 ("not exactly the {} leg(s)".format(len(job_legs)), "unexpected: " + unexpected),
                 (ok_line,), every_script),
                ("main-" + job, {"_run_one": clean_leg, "repo_root": lambda: tmp}, main,
                 ["--ci-job", job], 0, (ok_line,), (), scripts[job])))
        cases.extend((
            ("main-broken-partition",
             {"_run_one": clean_leg, "repo_root": lambda: tmp, "_CI_JOB_LEGS": broken}, main,
             ["--ci-job", "opf-closure-b"], 2,
             ("the CI job partition is broken", "opf-prompt-pack: in 0 CI jobs"), (ok_line,), []),
            ("main-unrecognized-argument", {"_run_one": clean_leg, "repo_root": lambda: tmp}, main,
             ["--ci-job=opf-closure-a"], 2,
             ("unrecognized argument '--ci-job=opf-closure-a'",), (ok_line,), []),
            ("main-value-naming-no-job", {"_run_one": clean_leg, "repo_root": lambda: tmp}, main,
             ["--ci-job", "opf-closure-z"], 2, ("--ci-job must name one of",), (ok_line,), []),
            ("main-repeated-ci-job", {"_run_one": clean_leg, "repo_root": lambda: tmp}, main,
             ["--ci-job", "opf-closure-a", "--ci-job", "opf-closure-b"], 2,
             ("--ci-job given more than once",), (ok_line,), [])))
        for label, rebound, call, argv, want_code, want, refused, want_launches in cases:
            del launches[:]
            out = io.StringIO()
            saved_argv = sys.argv
            if argv is not None:
                sys.argv = [sys.argv[0]] + argv
            try:
                with _swapped(**rebound):
                    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
                        code = _under_test("ci-job-sweep: " + label, call)
            finally:
                sys.argv = saved_argv
            said = out.getvalue()
            if (code != want_code or not all(w in said for w in want)
                    or any(r in said for r in refused) or launches != want_launches):
                raise _CheckFailure("closure/ci-job-sweep: {} (exit {}, want {}; launches {!r}): "
                                    "{!r}".format(label, code, want_code, launches, said[-2000:]))
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = _under_test("ci-job-sweep: empty-report", _report, [])
        said = out.getvalue()
        if code != 2 or ok_line in said or "the sweep ran no leg" not in said:
            raise _CheckFailure("closure/ci-job-sweep: empty-report (exit {}, want 2): {!r}".format(
                code, said))
    finally:
        subprocess.Popen = real_popen
        shutil.rmtree(tmp, ignore_errors=True)
    print("RED a --ci-job sweep through run() and main(): each job's sweep runs exactly its legs in "
          "roster order and exits 0; a filter that drops every leg or ignores the job's legs -> "
          "refused, exit 2, the missing or unexpected legs named, never the OK line; a report over no "
          "rows -> refused, exit 2, never the OK line; an unrecognized argument (--ci-job=VALUE), a "
          "value naming no job, a repeated --ci-job and a partition holding a leg in no job -> each "
          "refused by main(), exit 2, no leg run")


def _raiser(exc):
    """A phase that raises exc (the fast-phase routing vectors)."""
    def phase():
        raise exc
    return phase


def _check_routing_self_test():
    """The real checks, run through the fast phase as CI runs them (_self_test_phase), are routed by
    what they meet. A CRASH IN THE CODE UNDER TEST is the calling check's failure (1), named with the
    check, the code and the exception, never a harness fault: the code is rebound (_swapped) to a
    stand-in that raises, for _negative_verdict, _leg_bounds, _positive_verdict, a registration check,
    the whole-roster check, _run_one and _kill_and_reap, each under the check that calls it; a
    SystemExit the code under test raises (_negative_verdict, _kill_and_reap) is that check's failure
    too, never the exit 0 it carries. A check that CANNOT EVALUATE what it checks is 2, named: the real
    bounds check over a throwaway tree whose workflow has no opf-closure-a job cannot read the CI job
    limit. A FAULT IN THE CHECK'S OWN WORK outside its calls into the code under test (its temporary
    directory, as on a full disk) stays a harness fault (2), so the conversion covers those calls and
    nothing more; a check's own assertions, those about its fixtures included (a readiness file that
    never appears), are its failures (1) like any other. self_test_main from a root with no
    opf/tools/ (its closure legs stubbed to pass) cannot evaluate (2), never a failure (1). Each case
    launches no process."""
    def crashing(name, exc):
        def crashed(*_args, **_kwargs):
            raise exc
        crashed.__name__ = name
        return crashed

    def full_disk(*_args, **_kwargs):
        raise OSError(errno.ENOSPC, "No space left on device (injected)")

    defect = "injected defect"
    code_under_test = "the code under test, {}, raised {}: "
    tmp = Path(tempfile.mkdtemp(prefix="opf-closure-checks-"))
    try:
        _throwaway_opf(tmp)
        workflow = tmp / _CI_WORKFLOW
        os.makedirs(workflow.parent)
        workflow.write_text("jobs:\n  other-job:\n    runs-on: ubuntu-latest\n", encoding="utf-8")
        for label, phase, rebound, mkdtemp, want, said_want in (
                ("negative-verdict-crash", _negative_verdict_self_test,
                 {"_negative_verdict": crashing("_negative_verdict", TypeError(defect))}, None, (1, None),
                 "SELF-TEST FAIL: closure/negative-verdict-rejects-a-genuine-catch: "
                 + code_under_test.format("_negative_verdict", "TypeError") + defect),
                ("leg-bounds-crash", lambda: _bounds_self_test(tmp),
                 {"_leg_bounds": crashing("_leg_bounds", KeyError(defect))}, None, (1, None),
                 "SELF-TEST FAIL: closure/leg-bounds-cover-their-work: "
                 + code_under_test.format("_leg_bounds", "KeyError") + repr(defect)),
                ("positive-verdict-crash", _verdict_routing_self_test,
                 {"_positive_verdict": crashing("_positive_verdict", TypeError(defect))}, None, (1, None),
                 "SELF-TEST FAIL: closure/positive-verdict: "
                 + code_under_test.format("_positive_verdict", "TypeError") + defect),
                ("registration-check-crash", _import_batch_registration_self_test,
                 {"_check_import_batch_registration": crashing("_check_import_batch_registration",
                                                               TypeError(defect))}, None, (1, None),
                 "SELF-TEST FAIL: closure/import-batch-registration: "
                 + code_under_test.format("_check_import_batch_registration", "TypeError") + defect),
                ("roster-check-crash", lambda: _roster_registration_self_test(tmp),
                 {"_check_roster_registration": crashing("_check_roster_registration",
                                                         TypeError(defect))}, None, (1, None),
                 "SELF-TEST FAIL: closure/roster-registration: "
                 + code_under_test.format("_check_roster_registration", "TypeError") + defect),
                ("runner-crash",
                 lambda: _timeout_vectors_self_test(dict.fromkeys(_LEG_CEILING_S, (1458, 486, 0))),
                 {"_run_one": crashing("_run_one", RuntimeError(defect))}, None, (1, None),
                 "SELF-TEST FAIL: closure/half-ceiling-bound-did-not-kill-the-ceiling-run: "
                 + code_under_test.format("_run_one", "RuntimeError") + defect),
                # REGRESSION COVERAGE: this check named an exception _kill_and_reap let escape as its
                # own failure before the other checks did.
                ("cleanup-crash", _cleanup_doubles_self_test,
                 {"_kill_and_reap": crashing("_kill_and_reap", RuntimeError(defect))}, None, (1, None),
                 "SELF-TEST FAIL: closure/cleanup-normal-drain: _kill_and_reap raised"),
                # A SystemExit the code under test raises is the calling check's failure too, never the
                # exit 0 it carries.
                ("negative-verdict-system-exit", _negative_verdict_self_test,
                 {"_negative_verdict": crashing("_negative_verdict", SystemExit(0))}, None, (1, None),
                 "SELF-TEST FAIL: closure/negative-verdict-rejects-a-genuine-catch: "
                 + code_under_test.format("_negative_verdict", "SystemExit") + "0"),
                ("cleanup-system-exit", _cleanup_doubles_self_test,
                 {"_kill_and_reap": crashing("_kill_and_reap", SystemExit(0))}, None, (1, None),
                 "SELF-TEST FAIL: closure/cleanup-normal-drain: _kill_and_reap raised SystemExit(0)"),
                # REGRESSION COVERAGE: a wrong answer from the code under test (no crash) is the
                # check's own failure, as before the phase routed by type; it also shows the check
                # raises the failure type, since an AssertionError of another type would read as 2.
                ("negative-verdict-wrong-answer", _negative_verdict_self_test,
                 {"_negative_verdict": lambda *_args: None}, None, (1, None),
                 "SELF-TEST FAIL: closure/negative-verdict-not-red: bound-kill"),
                ("ci-job-limit-cannot-evaluate", lambda: _bounds_self_test(tmp), {}, None, (2, None),
                 "SELF-TEST CANNOT EVALUATE: closure/ci-job-limit-cannot-evaluate: job opf-closure-a "
                 "appears 0 times as a key of the top-level jobs: mapping (need exactly one)"),
                # REGRESSION COVERAGE: a fault creating the check's own temporary directory read as a
                # harness fault before; it fails if the conversion were widened past the calls into
                # the code under test.
                ("check-fixture-fault", _negative_verdict_self_test, {}, full_disk, (2, None),
                 "SELF-TEST CANNOT EVALUATE: OSError: [Errno {}] No space left on device "
                 "(injected)".format(errno.ENOSPC))):
            out = io.StringIO()
            real_mkdtemp = tempfile.mkdtemp
            if mkdtemp is not None:
                tempfile.mkdtemp = mkdtemp
            try:
                with _swapped(**rebound):
                    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
                        try:
                            got = _self_test_phase(phase)
                        except (Exception, SystemExit) as exc:
                            raise _CheckFailure("closure/check-routing: {} escaped the phase: {}: "
                                                "{}".format(label, type(exc).__name__, exc)) from exc
            finally:
                tempfile.mkdtemp = real_mkdtemp
            said = out.getvalue()
            if got != want or said_want not in said:
                raise _CheckFailure("closure/check-routing: {} -> {!r}, want {!r} naming {!r}: "
                                    "{!r}".format(label, got, want, said_want, said[-2000:]))
        # --self-test from a root with no opf/tools/ cannot evaluate (2), never a failure (1): the
        # roster's scripts are absent, not drifted. The closure legs are stubbed to pass, so a fast
        # phase that let the missing tree through would read 0 here.
        bare = tmp / "no-opf"
        os.makedirs(bare)
        out = io.StringIO()
        with _swapped(repo_root=lambda: bare, _closure_legs=lambda *_args, **_kwargs: 0):
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
                got = self_test_main()
        said = out.getvalue()
        said_want = ("SELF-TEST CANNOT EVALUATE: closure/roster-registration-cannot-evaluate: "
                     "opf/tools/ not found under {}".format(bare))
        if got != 2 or said_want not in said:
            raise _CheckFailure("closure/check-routing: no-opf-tools -> {!r}, want 2 naming {!r}: "
                                "{!r}".format(got, said_want, said[-2000:]))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("RED a crash in the code under test (_negative_verdict, _leg_bounds, _positive_verdict, a "
          "registration check, the whole-roster check, _run_one, _kill_and_reap) and a SystemExit it "
          "raises (_negative_verdict, _kill_and_reap) -> the calling check's FAIL naming it, exit 1; "
          "a wrong answer -> FAIL, exit 1; the CI job limit that cannot be read -> CANNOT EVALUATE, "
          "exit 2; a fault in a check's own temporary directory -> CANNOT EVALUATE, exit 2; "
          "--self-test from a root with no opf/tools/ -> CANNOT EVALUATE, exit 2")


def _fast_vectors(root):
    """The self-test's fast phase, run before the closure legs; returns the derived per-leg bounds."""
    _roster_registration_self_test(root)
    _pack_manifest_registration_self_test()
    _adopt_observe_registration_self_test()
    _import_batch_registration_self_test()
    _import_complete_registration_self_test()
    _negative_verdict_self_test()
    _verdict_routing_self_test()
    _harness_routing_self_test()
    _ci_job_sweep_self_test()
    _check_routing_self_test()
    _cleanup_doubles_self_test()
    required = _bounds_self_test(root)
    _timeout_vectors_self_test(required)
    _after_launch_fault_self_test()
    return required


def self_test_main(job=None):
    """Prove the gate fails without the self-containment property (change-carries-check). job (a
    _CI_JOB_LEGS key, already validated by main()) restricts the positive leg's sweep to that job's
    legs and runs the negative leg only in the job that holds opf-tooling-selftest (_closure_legs);
    None sweeps every leg and runs the flip. The fast phase always covers the whole roster, the
    partition and every job's fit, and holds each job's sweep through run() and main() to exactly
    that job's legs over stubbed leg runners (_ci_job_sweep_self_test). The bound and
    verdict legs run first and fast: the per-leg bounds cover each leg's table-derived need (the floor
    term included, shown raising bounds under a launch margin lowered below it) and fit the CI job,
    time-scaled stub legs show a flat bound below the recorded work killing a correct leg while
    a hang is still cut off and named (descendants that stayed in the leg's process group included),
    the post-kill cleanup closes the pipe and names what it could not complete on every path the
    cleanup doubles script (a failed group kill, whether or not the leader kill was delivered; a drain
    timeout or OSError; a reap timeout or OSError), an exception that ends the runner's wait after a
    launch leaves no leg running and escapes as itself, any cleanup it could not complete a note on
    it (_after_launch_fault_self_test), and the negative-leg verdict, the gate's routing
    through run() of what stops a run before, around or after the legs (registration drift, harness
    faults) and the self-test's own exit routing are shown discriminating. That fast phase runs through
    _self_test_phase, which routes by the exception's type: a check's failure (_CheckFailure, a crash or
    a SystemExit in the code under test a check calls included) is a failure (1), and any other
    exception or SystemExit, a check that cannot evaluate what it checks and a bound that cannot be
    derived included, is CANNOT EVALUATE (2), named (_harness_routing_self_test holds this wiring with
    the phase and the legs stubbed, and _check_routing_self_test holds it over the real checks). The
    positive leg runs the gate over the real opf/ and requires 0: exit 1 is
    a failure (registration drift named as itself), and exit 2 (a leg killed at its bound or ended by a
    signal, a leg never launched, a leg that exited 2 printing CANNOT EVALUATE, or a run stopped before any leg result by a refusal or a harness
    fault) is CANNOT EVALUATE naming each such leg or the stop (_positive_verdict). The negative leg
    materializes opf/ alone, then RE-INTRODUCES the pre-move upward edge (opf/tools/_opf_store.py
    importing `_parse` from AIQT `check_versions` instead of the extracted `_semver`); with no AIQT tree
    reachable that import fails, so the leg must produce exactly the failure the flipped import
    produces, its raw output byte for byte (a clean pass is a failure; a leg killed at its bound, a
    signal, a harness or launch failure (rejected structurally, whatever its text), and any other
    failure or output, one naming the injected import or differing only in a line ending or trailing
    whitespace included, leave the flip unevaluated; see _negative_verdict), and that caught result
    must then go through _report and come back as closure BROKEN with exit 1 (the flip exits 2 with the
    lowercase bootstrap message, a real break, not a declared cannot-evaluate). A gate that passed the
    deliberately-broken copy, or that reported the break as not evaluated, would provide no coverage.
    Exit 1 when anything definite failed, else 2
    when anything could not be evaluated (_self_test_outcome); an exception or a SystemExit that
    escapes the closure legs is a harness fault, cannot evaluate (2), named by its type, never a
    traceback and never the exit a SystemExit carries."""
    root = repo_root()
    code, required = _self_test_phase(lambda: _fast_vectors(root))
    if code is not None:
        return code
    legs = None if job is None else _CI_JOB_LEGS[job]
    try:
        code = (_closure_legs(root, required) if legs is None
                else _closure_legs(root, required, legs=legs))
    except (Exception, SystemExit) as exc:
        print("SELF-TEST CANNOT EVALUATE: {}: {} (a harness fault in the closure legs, never a self-test "
              "failure)".format(type(exc).__name__, exc), file=sys.stderr)
        return 2
    if code == 0:
        if legs is None or _FLIP_LEG in legs:
            print("SELF-TEST PASS: standalone closure holds for opf/, and a re-introduced upward edge "
                  "is caught.")
        else:
            print("SELF-TEST PASS: standalone closure holds for the {} legs (the re-introduced upward "
                  "edge is exercised by the CI job whose legs hold {}).".format(job, _FLIP_LEG))
    return code


def main():
    # Every argument must be recognised and consumed: an unknown one (--ci-job=VALUE included, since
    # the job name follows --ci-job as its own argument) is refused with nothing run, never silently
    # ignored, because an ignored --ci-job spelling would sweep every leg where CI meant one job's.
    argv = sys.argv[1:]
    job = None
    self_test = False
    index = 0
    while index < len(argv):
        argument = argv[index]
        if argument == "--self-test":
            self_test = True
        elif argument == "--ci-job":
            value = argv[index + 1] if index + 1 < len(argv) else None
            if job is not None:
                print("error: --ci-job given more than once; nothing was run (cannot evaluate)",
                      file=sys.stderr)
                return 2
            if value not in _CI_JOB_LEGS:
                print("error: --ci-job must name one of {} (got {!r}); nothing was run (cannot "
                      "evaluate)".format(", ".join(_CI_JOBS), value), file=sys.stderr)
                return 2
            job = value
            index += 1
        else:
            print("error: unrecognized argument {!r} (this gate takes --self-test and --ci-job "
                  "<job>); nothing was run (cannot evaluate)".format(argument), file=sys.stderr)
            return 2
        index += 1
    if self_test:
        return self_test_main(job)
    if job is None:
        return run(repo_root())
    faults = _partition_fault()
    if faults:
        print("error: the CI job partition is broken, so the {} legs cannot be trusted (cannot "
              "evaluate; no leg was run): {}".format(job, "; ".join(faults)), file=sys.stderr)
        return 2
    return run(repo_root(), legs=_CI_JOB_LEGS[job])


if __name__ == "__main__":
    sys.exit(main())
