#!/usr/bin/env python3
"""opf: the DevProcess (OPF) reference-tooling dispatcher (OPF core-tooling, skeleton from U1).

  opf.py --self-test                run every registered OPF helper self-test (the CI leg)
  opf.py <verb> [--root DIR] ...    a store verb (default --root: the cwd product repository root)

This is the SKELETON dispatcher the OPF core-tooling units grow into. U1 lands it with the store-side
helper self-test wired in; the store verbs (`render`, `import`, `doctor`, and the rest of the spec's
command vocabulary) are recognized names that report NOT-YET-IMPLEMENTED and fail closed (exit 2) until
their unit lands, so a stub can never read as a passing operation.

Adopter-rooted, like doctor.py/migrate.py/conformance.py: an OPF verb operates on a PRODUCT repository
root named by --root (default: the cwd), never on this pack's own tree via `_gen_common.repo_root()`.
The pack is not a DevProcess adopter, so a live `opf.py <verb> --root .` here reports NOT APPLICABLE
once the verbs land; the assurance rides the `--self-test` leg over synthetic stores (spec-honest,
mirroring the crosswalk/doctor/migrate legs in run_all_checks.sh).

Deliberately NOT named tools/gen_*.py: the generated-source registry (gen_gensrc.py) discovers gen_*.py
and validates fixed repo-relative targets, but OPF renders into an adopter --root with no fixed
repo-relative target, so this family gates as self-tests instead (the U1 build-plan section 5 rule).

Launched isolated (-I -B) per the Python-launcher-isolation gate; sibling helpers are imported through
the sys.path insert idiom the repo's tools share.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _opf_store  # noqa: E402  U1: store resolution + discovery + manifest base/profile schema
import _opf_schema  # noqa: E402  U2: record envelope + baseline type schemas + status/transition + counters
import _opf_release  # noqa: E402  U3: version.toml + worklog.toml + span tiling + coverage digests + release cut
import _opf_changelog  # noqa: E402  U5: changelog range-coverage + freeze gates over version.toml + CHANGELOG.md
import _opf_check  # noqa: E402  U6: store-level integrity validator (validate_store; the opf doctor engine)
import _opf_emit  # noqa: E402  U8: the constrained-subset TOML emitter (canonical, byte-canon-clean)
import _opf_views  # noqa: E402  U4: deterministic view generators + the closed transform vocabulary
import _opf_fuzz  # noqa: E402  adversarial input-hardening proof (membership/type-guard class closure)
import _opf_import  # noqa: E402  U7: import staging (module + self-test; the live import verb stays unwired)

EXIT_OK = 0
EXIT_FINDING = 1
EXIT_MALFORMED = 2

def _aggregator_self_test():
    """Guard the aggregator's fail-closed return-vocabulary check (MAJOR 3). A helper returning a value
    OUTSIDE the {0,1,2} int vocabulary must fail the aggregate CLOSED (a non-zero worst), never be
    admitted as clean because a bool or float compares equal to an allowed int (False == 0, True == 1,
    0.0 == 0). Returns 0 clean, 1 on a failure. Registered below so `opf.py --self-test` exercises it;
    the store legs did not, letting a helper returning False produce an aggregate exit 0."""
    ok = True
    # A helper returning False (bool, == 0) must NOT aggregate to clean.
    if run_self_tests((("synthetic-false", lambda: False),)) == EXIT_OK:
        ok = False
    # A helper returning 0.0 (float, == 0) must NOT aggregate to clean.
    if run_self_tests((("synthetic-zero-float", lambda: 0.0),)) == EXIT_OK:
        ok = False
    # An out-of-range int (3) must NOT aggregate to clean.
    if run_self_tests((("synthetic-three", lambda: 3),)) == EXIT_OK:
        ok = False
    # A genuine clean int (0) still aggregates to clean: the check does not over-reject.
    if run_self_tests((("synthetic-zero", lambda: 0),)) != EXIT_OK:
        ok = False
    if not ok:
        print("opf aggregator self-test: FAIL (fail-closed vocabulary check admitted a bad return)",
              file=sys.stderr)
        return EXIT_FINDING
    print("opf aggregator self-test: PASS (fail-closed on non-int / out-of-range helper returns)")
    return EXIT_OK


def _watchdog_hostile_ambient_self_test():
    """Guard F2 (round-10, class-width; extended round-15): the opf-side self-tests that manipulate SIGALRM
    (the FIFO-probe watchdogs in _opf_changelog / _opf_views / _opf_store, AND _opf_check's run_bounded f7
    fixture that SIG_IGN-discards a pending alarm) must survive a HOSTILE ambient SIGALRM state and leave it
    exactly as they found it. The
    hostile ambient is SIGALRM BLOCKED with an already-fired (PENDING) alarm AND an armed ITIMER_REAL -
    the exact "timer already fired" state. Pre-fix each watchdog unblocked SIGALRM OUTSIDE its try/finally,
    so the pending alarm was delivered the instant SIGALRM unblocked, raised out of the self-test uncaught,
    AND left SIGALRM unblocked (a corrupted caller mask). This runs each affected self-test under that exact
    ambient and asserts a clean, state-restored outcome (rc 0; caller mask, SIGALRM disposition, interval
    timer, AND the blocked+pending SIGALRM itself all restored); reverting any one site's try/finally, its
    pending-drain, or its pending RE-POST (round-15 F2) re-reds it. Returns 0
    clean, 1 on a failure. On a platform without POSIX SIGALRM/itimer the watchdogs no-op, so this SKIPS
    clean. Runs the affected self-tests a second time (once here under the hostile ambient, once in the
    registry under the default ambient), the price of exercising the real sites rather than a copy."""
    import os as _os
    import io as _io
    import signal as _signal
    import contextlib as _ctx
    import time as _time
    if not (hasattr(_signal, "pthread_sigmask") and hasattr(_signal, "setitimer")
            and hasattr(_signal, "SIGALRM") and hasattr(_signal, "ITIMER_REAL")):
        print("opf watchdog hostile-ambient self-test: SKIP (no POSIX SIGALRM/itimer on this platform)")
        return EXIT_OK
    affected = (("opf-changelog", _opf_changelog.self_test),
                ("opf-views", _opf_views.self_test),
                ("opf-store", _opf_store.self_test),
                ("opf-check", _opf_check.self_test))   # round-15 F2: its f7 fixture SIG_IGN-discards pending too

    def _benign(_s, _f):                                          # a caller handler the watchdog must restore
        pass

    # F3 (round-12): the fixture ITIMER installed before each affected self-test carries a KNOWN value AND a
    # NONZERO REPEATING interval, so the discrimination below can assert the watchdog restored the interval
    # AND value (elapsed-aware, per F2), not merely that some positive time remains. A one-shot fixture (zero
    # interval) let a dropped interval-restoration survive: 0 restored == 0 ambient. A distinct nonzero
    # interval makes that mutant observable (restored interval 0 != the fixture interval).
    _FIX_VAL = 3600.0        # the KNOWN fixture ITIMER value the helper watchdog must preserve (elapsed-aware)
    _FIX_INT = 1800.0        # the KNOWN nonzero REPEATING interval the helper watchdog must restore verbatim

    ok = True
    for label, fn in affected:
        _prev_disp = _signal.getsignal(_signal.SIGALRM)
        _prev_mask = _signal.pthread_sigmask(_signal.SIG_BLOCK, set())
        _caller_snap = _opf_store.snapshot_caller_alarm()         # caller ITIMER value/interval + pending (shared)
        try:
            # Hostile ambient: install a benign handler, ARM a long ITIMER the watchdog must preserve (a KNOWN
            # value AND a nonzero repeating interval, F3), BLOCK SIGALRM, then self-signal so a SIGALRM is left
            # PENDING-and-BLOCKED (timer already fired).
            _signal.signal(_signal.SIGALRM, _benign)
            _signal.setitimer(_signal.ITIMER_REAL, _FIX_VAL, _FIX_INT)
            _t_arm = _time.monotonic()                            # F3: elapsed baseline for the fixture-value bound
            _signal.pthread_sigmask(_signal.SIG_BLOCK, {_signal.SIGALRM})
            _os.kill(_os.getpid(), _signal.SIGALRM)
            _pending_ok = _signal.SIGALRM in _signal.sigpending()
            _crashed = None
            _buf = _io.StringIO()
            try:
                with _ctx.redirect_stdout(_buf), _ctx.redirect_stderr(_buf):
                    rc = fn()
            except BaseException as exc:                          # a watchdog crash is the pre-fix failure
                _crashed = repr(exc)
                rc = None
            _fn_elapsed = _time.monotonic() - _t_arm              # upper bound on the elapsed the watchdog subtracts
            _blocked_after = _signal.SIGALRM in _signal.pthread_sigmask(_signal.SIG_BLOCK, set())
            _disp_after = _signal.getsignal(_signal.SIGALRM)
            _val_after, _int_after = _signal.getitimer(_signal.ITIMER_REAL)
            _pending_after = _signal.SIGALRM in _signal.sigpending()   # F2: caller pending must survive
            if not _pending_ok:
                print("opf watchdog self-test: {}: setup did not leave SIGALRM pending".format(label),
                      file=sys.stderr)
                ok = False
            if _crashed is not None:
                print("opf watchdog self-test: {}: RAISED under blocked+pending SIGALRM ({}); the unblock "
                      "escaped its try/finally (F2)".format(label, _crashed), file=sys.stderr)
                ok = False
            elif rc != EXIT_OK:
                print("opf watchdog self-test: {}: returned {!r} under the hostile ambient (expected "
                      "0)".format(label, rc), file=sys.stderr)
                ok = False
            if not _blocked_after:
                print("opf watchdog self-test: {}: left SIGALRM UNBLOCKED; the caller mask was corrupted "
                      "(F2)".format(label), file=sys.stderr)
                ok = False
            if _disp_after is not _benign:
                print("opf watchdog self-test: {}: did not restore the caller SIGALRM disposition".format(
                    label), file=sys.stderr)
                ok = False
            # F2 (round-15): the watchdog must PRESERVE a caller SIGALRM that was blocked-and-pending on
            # entry. The probe SIG_IGN-discards any inherited pending alarm so it cannot fire the watchdog
            # spuriously, but it must RE-POST it on exit (the shared restore_caller_alarm helper), so the
            # caller's pending state is unchanged. Pre-fix the pending alarm was discarded and never
            # re-posted, destroying ambient state the watchdog docstrings promise to leave untouched.
            if _pending_ok and not _pending_after:
                print("opf watchdog self-test: {}: did not PRESERVE the caller's blocked+pending SIGALRM; "
                      "the probe's SIG_IGN discarded it and it was not re-posted (F2)".format(label),
                      file=sys.stderr)
                ok = False
            # F3 (round-12): the watchdog must restore the caller's ITIMER VALUE (elapsed-aware, per F2) AND
            # its REPEATING INTERVAL, not merely leave some positive time. The value lies in
            # (_FIX_VAL - _fn_elapsed, _FIX_VAL]: the watchdog subtracts an elapsed >= 0 and <= the whole
            # fn() run, so a restored value below that band means the value was not preserved and one above
            # _FIX_VAL means the elapsed was not subtracted at all. A small float slack absorbs monotonic
            # jitter. The interval must be restored exactly to the fixture's _FIX_INT; a dropped
            # interval-restoration leaves 0 and reds this (the mutant a one-shot fixture hid).
            if not (_FIX_VAL - _fn_elapsed - 1e-3 <= _val_after <= _FIX_VAL + 1e-3):
                print("opf watchdog self-test: {}: did not restore the caller's ITIMER_REAL value "
                      "elapsed-aware (got {!r}, expected within ({:.6f}, {:.6f}]) (F2/F3)".format(
                          label, _val_after, _FIX_VAL - _fn_elapsed, _FIX_VAL), file=sys.stderr)
                ok = False
            if abs(_int_after - _FIX_INT) > 1e-6:
                print("opf watchdog self-test: {}: did not restore the caller's ITIMER_REAL repeating "
                      "interval (got {!r}, expected {!r}) (F3)".format(label, _int_after, _FIX_INT),
                      file=sys.stderr)
                ok = False
        finally:
            _signal.setitimer(_signal.ITIMER_REAL, 0)             # disarm the fixture timer
            _signal.signal(_signal.SIGALRM, _signal.SIG_IGN)     # discard any still-pending SIGALRM
            _signal.signal(_signal.SIGALRM, _prev_disp)           # restore the real caller disposition
            _signal.pthread_sigmask(_signal.SIG_SETMASK, _prev_mask)
            # Restore the caller's ITIMER_REAL + any pending SIGALRM through the SHARED helper (round-15 F1,
            # the SINGLE elapsed-aware save/restore every opf-side watchdog uses, so no per-site verbatim
            # restore can diverge again): value minus the time held (interval preserved) so the 3 helper runs
            # neither pause nor extend the caller's deadline (a 50ms deadline set before the test still fires,
            # not sitting ~50ms away after ~300ms of test; a deadline that expired during the test clamps to a
            # tiny positive so it still fires, never re-armed to its full original value), AND a SIGALRM the
            # caller had pending on entry re-posted so the probe's SIG_IGN does not destroy it (round-15 F2).
            _opf_store.restore_caller_alarm(*_caller_snap)
    if not ok:
        print("opf watchdog hostile-ambient self-test: FAIL (a FIFO-probe watchdog did not survive a "
              "blocked+pending ambient SIGALRM with state restored)", file=sys.stderr)
        return EXIT_FINDING
    print("opf watchdog hostile-ambient self-test: PASS (changelog/views/store watchdogs survive a "
          "blocked+pending SIGALRM with mask, disposition, and timer restored)")
    return EXIT_OK


def _watchdog_wrapper_caller_deadline_self_test():
    """Guard F2 (round-12, fix-induced): _watchdog_hostile_ambient_self_test must restore the CALLER's
    ITIMER_REAL with ELAPSED TIME SUBTRACTED, so running it neither PAUSES nor EXTENDS a caller's deadline
    (the pre-fix wrapper restored the caller value VERBATIM, so a 50ms deadline was still ~50ms away after
    the ~sub-second test and never fired). The three helper restore sites carry their own elapsed-aware
    discrimination inside the wrapper (the fixture value+interval check); this covers the WRAPPER's own
    caller-timer restore, which the default self-test never exercises because it arms no caller deadline.

    Arm a short (50ms) caller deadline with a firing-recorder handler, run the wrapper (which holds the
    caller timer across its 3 helper self-test runs, well over 50ms in total), and assert the deadline was
    HONOURED: elapsed-aware, the wrapper drives it below zero and it FIRES during the run (the recorder sees
    it) and reads as expired afterwards; the pre-fix verbatim restore leaves it sitting at its full 50ms,
    unfired. Returns 0 clean, 1 on failure; SKIPS clean on a platform without POSIX SIGALRM/itimer.

    Round-15 F1: this guard's OWN caller-timer restore is now the SHARED _opf_store.restore_caller_alarm
    helper (it previously restored the caller value verbatim, re-introducing inside its own guard the exact
    class it guards); _watchdog_shared_restore_self_test covers that shared helper directly."""
    import io as _io
    import signal as _signal
    import contextlib as _ctx
    import time as _time
    if not (hasattr(_signal, "setitimer") and hasattr(_signal, "SIGALRM")
            and hasattr(_signal, "ITIMER_REAL")):
        print("opf watchdog wrapper-deadline self-test: SKIP (no POSIX SIGALRM/itimer on this platform)")
        return EXIT_OK
    _deadline = 0.05                                             # a 50ms caller deadline the wrapper outlasts
    _fired = []

    def _recorder(_s, _f):
        _fired.append(_time.monotonic())

    # This test controls its OWN signal environment (test-hermeticity): it must UNBLOCK SIGALRM so its own
    # deadline can be delivered, and DISCARD any inherited pending SIGALRM under SIG_IGN first so a hostile
    # ambient (SIGALRM blocked with an already-fired alarm pending) cannot pre-fire the recorder or suppress
    # delivery of this test's deadline. The caller's disposition, mask, and timer are captured and restored.
    _have_mask = hasattr(_signal, "pthread_sigmask")
    _prev_disp = _signal.getsignal(_signal.SIGALRM)
    _prev_mask = _signal.pthread_sigmask(_signal.SIG_BLOCK, set()) if _have_mask else None
    _caller_snap = _opf_store.snapshot_caller_alarm()            # caller ITIMER value/interval + pending (shared)
    ok = True
    try:
        _signal.signal(_signal.SIGALRM, _signal.SIG_IGN)         # discard any inherited pending SIGALRM
        _signal.signal(_signal.SIGALRM, _recorder)
        if _have_mask:
            _signal.pthread_sigmask(_signal.SIG_UNBLOCK, {_signal.SIGALRM})
        _signal.setitimer(_signal.ITIMER_REAL, _deadline)
        _t0 = _time.monotonic()
        _buf = _io.StringIO()
        with _ctx.redirect_stdout(_buf), _ctx.redirect_stderr(_buf):
            _rc = _watchdog_hostile_ambient_self_test()          # the wrapper under test
        _elapsed = _time.monotonic() - _t0
        _val_after, _ = _signal.getitimer(_signal.ITIMER_REAL)
        if _rc != EXIT_OK:
            print("opf watchdog wrapper-deadline self-test: the inner hostile-ambient wrapper returned {!r} "
                  "(expected 0)".format(_rc), file=sys.stderr)
            ok = False
        # The wrapper ran far longer than the deadline, so an elapsed-aware restore drove the deadline below
        # zero: it FIRED during the run (recorder saw it) and reads as expired (~0) afterwards. A verbatim
        # restore (the pre-fix bug) leaves it at its full 50ms, unfired: _elapsed > _deadline guards the test.
        if _elapsed <= _deadline:
            print("opf watchdog wrapper-deadline self-test: the wrapper ran {:.4f}s, not longer than the {}s "
                  "deadline; the fixture cannot discriminate".format(_elapsed, _deadline), file=sys.stderr)
            ok = False
        if not _fired:
            print("opf watchdog wrapper-deadline self-test: the caller's {}s deadline never FIRED across a "
                  "{:.4f}s run; the wrapper paused/extended it instead of restoring it elapsed-aware "
                  "(F2)".format(_deadline, _elapsed), file=sys.stderr)
            ok = False
        if _val_after >= _deadline:
            print("opf watchdog wrapper-deadline self-test: the caller's ITIMER_REAL was restored to {!r} "
                  ">= its full {}s value; the elapsed time was not subtracted (F2)".format(
                      _val_after, _deadline), file=sys.stderr)
            ok = False
    finally:
        _signal.setitimer(_signal.ITIMER_REAL, 0)                # disarm before restoring the caller state
        _signal.signal(_signal.SIGALRM, _signal.SIG_IGN)         # discard any still-pending SIGALRM
        _signal.signal(_signal.SIGALRM, _prev_disp)
        if _have_mask:
            _signal.pthread_sigmask(_signal.SIG_SETMASK, _prev_mask)   # restore the caller's exact mask
        _opf_store.restore_caller_alarm(*_caller_snap)          # shared elapsed-aware timer + pending restore (F1)
    if not ok:
        print("opf watchdog wrapper-deadline self-test: FAIL (the wrapper did not restore the caller's "
              "ITIMER_REAL elapsed-aware; a caller deadline was paused/extended)", file=sys.stderr)
        return EXIT_FINDING
    print("opf watchdog wrapper-deadline self-test: PASS (a caller ITIMER_REAL deadline is honoured "
          "elapsed-aware across the hostile-ambient wrapper, not paused or extended)")
    return EXIT_OK


def _watchdog_shared_restore_self_test(_hold_s=0.0):
    """Guard F1 (round-15, break the watchdog-timer re-induction loop): every opf-side watchdog restores a
    borrowed caller ITIMER_REAL through the ONE shared _opf_store.restore_caller_alarm helper, the single
    source of truth, so no per-site verbatim restore can diverge again and re-introduce the watchdog-timer
    class its own guards kept re-inducing (rounds 12->13->14). This exercises that helper DIRECTLY: a caller
    timer restored after a KNOWN elapsed must come back reduced by that elapsed (elapsed-aware), its repeating
    interval preserved, never re-armed to its full original value. Reverting the helper to a verbatim restore
    (value re-armed to its original) reds this. SKIPS clean on a platform without POSIX SIGALRM/itimer.

    Deterministic, not timing-dependent: the elapsed is a fixed baseline in the past (monotonic() - 5s), so
    the restored value is ~95s for a 100s caller value regardless of machine speed; a verbatim restore yields
    the full 100s, far outside the elapsed-aware band.

    F-R18-C2TEST: `_hold_s` (default 0.0) injects a CONTROLLED measurable delay AFTER the top snapshot and
    BEFORE the finally-restore of the caller's borrowed timer, so a caller deadline held across this wrapper
    is restored reduced by ~`_hold_s`. The deadline wrapper below arms a real caller ITIMER, calls this with
    a non-zero `_hold_s`, and asserts that specific elapsed was deducted (elapsed-aware) versus ~0 for a
    restore-time-t0 / verbatim revert. The default 0.0 keeps the standalone registered run unchanged."""
    import signal as _signal
    import time as _time
    if not (hasattr(_signal, "setitimer") and hasattr(_signal, "ITIMER_REAL")):
        print("opf watchdog shared-restore self-test: SKIP (no POSIX itimer on this platform)")
        return EXIT_OK
    # F-R17-C2: capture the caller's alarm (value/interval, t0, pending) at the TOP through the SHARED
    # snapshot helper, for an elapsed-aware restore in the finally. The prior form read getitimer here and
    # passed restore-time monotonic() as t0 at the end, subtracting ~no elapsed and EXTENDING a caller
    # deadline held across this wrapper (the F-R16-1 sibling).
    _caller_snap = _opf_store.snapshot_caller_alarm()
    ok = True
    try:
        _signal.setitimer(_signal.ITIMER_REAL, 0)               # quiet baseline for the probe
        _known_val, _known_int, _elapsed = 100.0, 50.0, 5.0
        # Exercise the shared helper with an explicit snapshot whose baseline is _elapsed seconds in the past,
        # was_pending False (this probe does not manipulate the pending state). Elapsed-aware => ~95s remains.
        _opf_store.restore_caller_alarm(_known_val, _known_int, _time.monotonic() - _elapsed, False)
        _val_after, _int_after = _signal.getitimer(_signal.ITIMER_REAL)
        _signal.setitimer(_signal.ITIMER_REAL, 0)               # disarm the probe timer
        if not (_known_val - _elapsed - 0.5 <= _val_after <= _known_val - _elapsed + 0.5):
            print("opf watchdog shared-restore self-test: restored ITIMER value {!r}; expected ~{} (elapsed "
                  "{}s subtracted from {}s); a verbatim restore would leave {} (F1)".format(
                      _val_after, _known_val - _elapsed, _elapsed, _known_val, _known_val), file=sys.stderr)
            ok = False
        if abs(_int_after - _known_int) > 1e-6:
            print("opf watchdog shared-restore self-test: restored ITIMER interval {!r}; expected {!r} "
                  "(F1)".format(_int_after, _known_int), file=sys.stderr)
            ok = False
        # F-R18-C2TEST: hold the borrowed caller timer for a CONTROLLED, measurable interval before the
        # finally restores it elapsed-aware, so the deadline wrapper can assert this exact elapsed was
        # deducted (a restore-time-t0 / verbatim revert deducts ~0). Default 0.0 => no delay.
        if _hold_s:
            _time.sleep(_hold_s)
    finally:
        # F-R17-C2: disarm any probe timer, then restore the caller's alarm ELAPSED-AWARE from the
        # capture-time snapshot, so a caller deadline held across this wrapper is not extended.
        _signal.setitimer(_signal.ITIMER_REAL, 0)
        _opf_store.restore_caller_alarm(*_caller_snap)
    if not ok:
        print("opf watchdog shared-restore self-test: FAIL (the shared caller-timer restore is not "
              "elapsed-aware; a per-site verbatim restore could re-induce the watchdog-timer class)",
              file=sys.stderr)
        return EXIT_FINDING
    print("opf watchdog shared-restore self-test: PASS (the single shared restore helper is elapsed-aware: "
          "a caller timer comes back reduced by the time held, interval preserved)")
    return EXIT_OK


def _watchdog_shared_restore_deadline_self_test():
    """F-R17-C2 / F-R16-1 (durable executed-proof half): arm a REAL caller ITIMER_REAL across
    _watchdog_shared_restore_self_test and confirm its finally restores that caller timer ELAPSED-AWARE, not
    verbatim. The shared-restore self-test snapshots the caller alarm at the TOP and restores via
    restore_caller_alarm(*snap); a regression that passes RESTORE-time monotonic() as t0 subtracts ~no
    elapsed and EXTENDS the caller's deadline. This arms a large ~10s caller deadline that never fires, runs
    the wrapped self-test with a CONTROLLED hold, and asserts that specific held interval was subtracted from
    the restored ITIMER value, so a restore-time-t0 / verbatim revert (which subtracts ~0) reds. SKIPS clean
    without POSIX itimer.

    F-R18-C2TEST: the discrimination is driven by an explicit `_hold_s` the wrapped call sleeps while it
    holds the borrowed caller timer, NOT by the wrapped call's own (microsecond) wall time. The prior form
    measured the wrapped call's `_dur` and asserted `_val_after <= _armed - _dur*0.5`; because `_dur` was
    sub-millisecond, that band was measurement noise and a restore-time-t0 / verbatim revert stayed green.
    Here the held interval is a controlled ~0.2s, well above scheduling jitter, and the assertion requires
    that at least half of it was deducted -- the exact PARENT-FUNCTION revert (restore-time-t0 / verbatim)
    deducts ~0 and reds."""
    import signal as _signal
    import time as _time
    import io as _io
    import contextlib as _ctx
    if not (hasattr(_signal, "setitimer") and hasattr(_signal, "ITIMER_REAL")):
        print("opf watchdog shared-restore deadline self-test: SKIP (no POSIX itimer on this platform)")
        return EXIT_OK
    _armed = 10.0                                                # large: never fires, reduction is measurable
    _hold_s = 0.2                                                # a controlled, measurable held interval
    _prev_disp = _signal.getsignal(_signal.SIGALRM)
    _caller_snap = _opf_store.snapshot_caller_alarm()
    ok = True
    try:
        _signal.signal(_signal.SIGALRM, _signal.SIG_IGN)        # a fired deadline here is harmless (ignored)
        _signal.setitimer(_signal.ITIMER_REAL, _armed, 0.0)
        _buf = _io.StringIO()
        with _ctx.redirect_stdout(_buf), _ctx.redirect_stderr(_buf):
            _rc = _watchdog_shared_restore_self_test(_hold_s=_hold_s)
        _val_after, _ = _signal.getitimer(_signal.ITIMER_REAL)
        if _rc != EXIT_OK:
            print("opf watchdog shared-restore deadline self-test: inner self-test returned {!r} (expected "
                  "0)".format(_rc), file=sys.stderr)
            ok = False
        # An elapsed-aware restore subtracts ~the whole held interval (>= _hold_s); a restore-time-t0 /
        # verbatim revert subtracts ~0 and leaves the deadline near its full armed value. Require at least
        # half the controlled hold to have been deducted, a band well clear of scheduling jitter.
        if not (_val_after <= _armed - _hold_s * 0.5):
            print("opf watchdog shared-restore deadline self-test: caller ITIMER restored to {!r} after a "
                  "controlled {:.3f}s hold; that elapsed was not subtracted (a restore-time-t0 / verbatim "
                  "restore extends a caller deadline, F-R17-C2 / F-R18-C2TEST)".format(_val_after, _hold_s),
                  file=sys.stderr)
            ok = False
    finally:
        _signal.setitimer(_signal.ITIMER_REAL, 0)
        _signal.signal(_signal.SIGALRM, _prev_disp)
        _opf_store.restore_caller_alarm(*_caller_snap)
    if not ok:
        print("opf watchdog shared-restore deadline self-test: FAIL", file=sys.stderr)
        return EXIT_FINDING
    print("opf watchdog shared-restore deadline self-test: PASS (the shared-restore self-test restores a "
          "caller ITIMER_REAL elapsed-aware, not extended)")
    return EXIT_OK


# Registered helper self-tests, run by `opf.py --self-test`. Each is (label, callable) returning a
# 0/1/2 exit code (0 clean, 1 finding, 2 cannot-evaluate). Later units append their own helper here.
SELF_TESTS = (
    ("opf-store", _opf_store.self_test),
    ("opf-schema", _opf_schema.self_test),
    ("opf-release", _opf_release.self_test),
    ("opf-changelog", _opf_changelog.self_test),
    ("opf-emit", _opf_emit.self_test),
    ("opf-views", _opf_views.self_test),
    ("opf-import", _opf_import.self_test),
    ("opf-fuzz", _opf_fuzz.self_test),
    ("opf-check", _opf_check.self_test),
    ("opf-watchdog-hostile-ambient", _watchdog_hostile_ambient_self_test),
    ("opf-watchdog-wrapper-deadline", _watchdog_wrapper_caller_deadline_self_test),
    ("opf-watchdog-shared-restore", _watchdog_shared_restore_self_test),
    ("opf-watchdog-shared-restore-deadline", _watchdog_shared_restore_deadline_self_test),
    ("opf-aggregator", _aggregator_self_test),
)

# The spec's command vocabulary (spec 1). Each lands in its own unit; until then a verb fails closed.
KNOWN_VERBS = ("init", "import", "doctor", "render", "migrate", "sync")


# Helper self-tests that pin sys.set_int_max_str_digits(4300) inside a fixture and MUST restore the ambient
# value in a finally (the round-7 int-limit hermeticity work). run_self_tests guards that RESTORE half below.
_INT_LIMIT_SELF_TESTS = frozenset({"opf-release", "opf-emit", "opf-schema", "opf-fuzz", "opf-import"})


def run_self_tests(tests=SELF_TESTS):
    """Run every registered helper self-test in order, forwarding each result. The aggregate exit code
    is the WORST outcome (2 cannot-evaluate > 1 finding > 0 clean): one degraded or failing helper fails
    the whole leg, never masked by a later clean one.

    Int-limit hermeticity guard (finding 8-4): each helper in _INT_LIMIT_SELF_TESTS pins the int-string
    conversion limit to 4300 inside its fixtures and must RESTORE the ambient value afterward. That restore
    had no fails-if-reverted check: under the DEFAULT ambient (already 4300) a dropped restore leaves 4300
    and is invisible. So around each such helper we set a distinct SENTINEL limit (!= 4300 and != the real
    ambient) and, after it runs, require the limit to STILL be that sentinel before restoring the real
    ambient; a dropped restore in any of those helpers leaves 4300 != sentinel and fails the leg closed.
    These helpers are hermetic w.r.t. the ambient int-limit by construction (they pin their own 4300), so
    running them under the sentinel is exactly the hostile-ambient contract they already satisfy."""
    worst = EXIT_OK
    _idlimit_orig = sys.get_int_max_str_digits()
    _idlimit_sentinel = 271828 if _idlimit_orig != 271828 else 314159   # distinct from 4300 AND from ambient
    for label, fn in tests:
        print("== opf self-test: {} ==".format(label))
        _guard_idlimit = label in _INT_LIMIT_SELF_TESTS
        if _guard_idlimit:
            sys.set_int_max_str_digits(_idlimit_sentinel)
            try:
                code = fn()
            finally:
                _idlimit_after = sys.get_int_max_str_digits()
                sys.set_int_max_str_digits(_idlimit_orig)   # restore the real ambient regardless of outcome
            if _idlimit_after != _idlimit_sentinel:
                print("opf self-test: {} left sys.get_int_max_str_digits at {} (expected the sentinel {}); a "
                      "dropped int-limit restore is a hermeticity leak, failing closed (finding 8-4)".format(
                          label, _idlimit_after, _idlimit_sentinel), file=sys.stderr)
                worst = EXIT_MALFORMED
        else:
            code = fn()
        if not (type(code) is int and code in (EXIT_OK, EXIT_FINDING, EXIT_MALFORMED)):
            # A helper whose return is not an int of exactly {0,1,2} is itself a fault: fail closed (the
            # worst outcome) rather than letting an unrecognized code read as clean. `type(code) is int`
            # deliberately EXCLUDES bool (a subclass of int, where False == 0 and True == 1) and float
            # (0.0 == 0), so a helper returning False or 0.0 can never be admitted as a clean pass.
            print("opf self-test: {} returned out-of-range code {!r}; failing closed".format(label, code),
                  file=sys.stderr)
            worst = EXIT_MALFORMED
        elif code == EXIT_MALFORMED:
            worst = EXIT_MALFORMED
        elif code == EXIT_FINDING and worst != EXIT_MALFORMED:
            worst = EXIT_FINDING
    return worst


def main():
    args = sys.argv[1:]
    if args == ["--self-test"]:
        return run_self_tests()
    if not args or args[0] in ("-h", "--help"):
        print(__doc__, file=sys.stderr)
        return EXIT_MALFORMED
    verb = args[0]
    if verb in KNOWN_VERBS:
        # A recognized verb whose unit has not landed: fail closed (exit 2), never a silent success, so
        # a stub is never mistaken for a completed operation.
        print("opf {}: not yet implemented in this build (fail-closed)".format(verb), file=sys.stderr)
        return EXIT_MALFORMED
    print("opf: unknown verb {!r}; known verbs: {}".format(verb, ", ".join(KNOWN_VERBS)), file=sys.stderr)
    return EXIT_MALFORMED


if __name__ == "__main__":
    sys.exit(main())
