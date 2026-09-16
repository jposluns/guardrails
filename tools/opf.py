#!/usr/bin/env python3
"""opf: the DevProcess (OPF) reference-tooling dispatcher (OPF core-tooling, skeleton from U1).

  opf.py --self-test                run every registered OPF helper self-test (the CI leg)
  opf.py <verb> [--root DIR] ...    a store verb (default --root: the cwd product repository root)

This is the dispatcher the OPF core-tooling units grow into. U1 lands it with the store-side helper
self-test wired in; store verbs that have not yet landed are recognized names that report
NOT-YET-IMPLEMENTED and fail closed (exit 2) until their unit lands, so a stub can never read as a
passing operation. `render` HAS landed (PR-A): the `opf render` CLI requires exactly one of
`--check | --write` (a bare `render` is a usage error, exit 2); `--check` is the read-only drift check
(forwarding to the U4 engine) and `--write` (VC-4/PR-C) is the mutating half: it gathers the inert git-derived
observations caller-side (_opf_observe.gather) and hands them to the U4 engine, which composes the EXISTING U6
`validate_store` store-integrity gate and permits the write only on a VALID verdict, refusing an INVALID or
CANNOT-EVALUATE store with exit 2 and writing nothing. `doctor` HAS landed (PR-B): `opf doctor
[--root DIR]` RESOLVES the store, gathers the inert git-derived observations (_opf_observe.gather: tracked,
actual_remote, prior), and runs the U6 `validate_store` store-integrity engine over them, returning that
engine's 0/1/2 contract (a NOT-ADOPTED root reports NOT APPLICABLE and exits 0). Doctor is read-only; its
observation gather is the caller-side git seam validate_store itself never touches. `upgrade` HAS landed
(spec 9.2): `opf upgrade [--root DIR]` is the in-place, additive, idempotent 1.0.0 -> 1.1.0 store-schema
upgrade. It refuses fail-closed on a store above the tooling spec or on a non-canonical manifest/counters,
applies exactly the allowed delta as a canonical model regeneration (bump spec_version; retire the
decision_support module; add the contribution/maintainer_decision/preference_pattern type rows and the two
new view rows; extend counters with CN/MD/PP preserving existing high-waters; create the three missing empty
indexes, skipping any that already exist), renders the declared views, and requires a full doctor VALID
before offering the staged change; it never commits (the adopter reviews and merges). A store already at the
tooling spec_version is a byte no-op; a NOT-ADOPTED root reports NOT APPLICABLE and exits 0.

Adopter-rooted, like doctor.py/migrate.py/conformance.py: an OPF verb operates on a PRODUCT repository
root named by --root (default: the cwd), never on this pack's own tree via `_gen_common.repo_root()`.
The pack is a readable non-adopter root, so a live `opf.py render --root . --check` here reports NOT
APPLICABLE; the assurance rides the `--self-test` leg over synthetic stores (spec-honest, mirroring the
crosswalk/doctor/migrate legs in run_all_checks.sh).

Deliberately NOT named tools/gen_*.py: the generated-source registry (gen_gensrc.py) discovers gen_*.py
and validates fixed repo-relative targets, but OPF renders into an adopter --root with no fixed
repo-relative target, so this family gates as self-tests instead (the U1 build-plan section 5 rule).

Launched isolated (-I -B) per the Python-launcher-isolation gate; sibling helpers are imported through
the sys.path insert idiom the repo's tools share.
"""
import json
import os
import stat
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # for the guarded _opf_* helper bootstrap below

EXIT_OK = 0
EXIT_FINDING = 1
EXIT_MALFORMED = 2


def _bootstrap():
    """Import the non-stdlib _opf_* helper modules the dispatch and self-test legs use, binding each to a
    module global. Called FIRST in main() so a broken or PARTIAL install -- a helper that cannot be imported
    (ImportError) or read (OSError) -- maps to a located cannot-evaluate (EXIT_MALFORMED / 2), never an
    uncaught ImportError that Python would surface as its default exit 1 and that a direct `opf render
    --check` would then read as a false DRIFT (the exit-1=drift conflation, reached here BEFORE the render
    dispatcher's own fail-closed handler). Only ImportError and OSError (the broken/partial-install signals)
    are caught; a broader error propagates rather than being masked as bootstrap. The imports moved OFF module
    top for exactly this reason -- an eager top-level import failed before main()'s contract could apply.
    Idempotent: a re-import of an already-loaded module is a cheap no-op, so main() may call it on every
    invocation. Returns EXIT_OK on success, or EXIT_MALFORMED with a located diagnostic naming the helper
    that could not be brought in."""
    global _opf_store, _opf_schema, _opf_release, _opf_changelog, _opf_check
    global _opf_emit, _opf_views, _opf_fuzz, _opf_import, _opf_observe
    try:
        import _opf_store       # U1: store resolution + discovery + manifest base/profile schema
        import _opf_schema      # U2: record envelope + baseline type schemas + status/transition + counters
        import _opf_release     # U3: version.toml + worklog.toml + span tiling + coverage digests + release cut
        import _opf_changelog   # U5: changelog range-coverage + freeze gates over version.toml + CHANGELOG.md
        import _opf_check       # U6: store-level integrity validator (validate_store; engine for opf doctor)
        import _opf_emit        # U8: the constrained-subset TOML emitter (canonical, byte-canon-clean)
        import _opf_views       # U4: deterministic view generators + the closed transform vocabulary
        import _opf_fuzz        # adversarial input-hardening proof (membership/type-guard class closure)
        import _opf_import      # U7: import staging (module + self-test; the live import verb stays unwired)
        import _opf_observe     # PR-B: caller-side git-derived observations for the doctor verb (validate_store)
    except ImportError as exc:
        print("opf: cannot bootstrap: {} (cannot evaluate)".format(exc.name or exc), file=sys.stderr)
        return EXIT_MALFORMED
    except OSError as exc:
        print("opf: cannot bootstrap: a helper module could not be read ({!r}) (cannot evaluate)".format(
            exc), file=sys.stderr)
        return EXIT_MALFORMED
    return EXIT_OK

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
        # Bounded delivery-grace for the last iteration's clamped (1e-6) re-armed SIGALRM under load: a flaky-observation stabilization, not a correctness change (SIGALRM is unblocked here, so the pending timer delivers during the wait).
        _grace = _time.monotonic() + 2.0
        while not _fired and _time.monotonic() < _grace:
            _time.sleep(0.005)
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


def _cmd_render(rest):
    """`opf render [--root DIR] (--check | --write)`: the store render verb.

    The READ-ONLY `--check` half forwards to the U4 engine `_opf_views.render`, whose 0/1/2 contract is
    exactly the required one (0 clean, 1 drift, 2 cannot-evaluate; a NOT-ADOPTED root reports NOT APPLICABLE
    and exits 0, the pack's own `--root .` case). The mutating `--write` half (VC-4/PR-C) gathers the inert
    git-derived observations caller-side (_opf_observe.gather over the RESOLVED store, exactly as doctor does)
    and hands them to the same engine, which composes the U6 store-integrity gate and permits the write only
    on a VALID verdict, printing the findings/cannot-evaluates and returning 2 (writing nothing) otherwise, so
    a write can never read as a silent no-op. Exactly one of `--check`/`--write` is required: a bare
    `opf render` is a usage error (a preview never defaults into a write). The parser is the house fail-closed
    idiom (unknown token, an empty or option-looking or duplicate --root value -> exit 2), matching
    _opf_views.render's own parser."""
    root = None
    mode = None
    i = 0
    while i < len(rest):
        tok = rest[i]
        if tok in ("--check", "--write"):
            if mode is not None:
                print("opf render: give exactly one of --check / --write", file=sys.stderr)
                return EXIT_MALFORMED
            mode = "check" if tok == "--check" else "write"
            i += 1
        elif tok == "--root":
            if i + 1 >= len(rest):
                print("opf render: --root requires a directory argument", file=sys.stderr)
                return EXIT_MALFORMED
            if root is not None:
                print("opf render: --root given more than once", file=sys.stderr)
                return EXIT_MALFORMED
            val = rest[i + 1]
            if val == "" or val.startswith("-"):
                print("opf render: --root requires a non-empty directory argument, not {!r}".format(val),
                      file=sys.stderr)
                return EXIT_MALFORMED
            root = val
            i += 2
        else:
            print("opf render: unrecognized argument {!r}".format(tok), file=sys.stderr)
            return EXIT_MALFORMED
    if mode is None:
        print("opf render: give exactly one of --check / --write", file=sys.stderr)
        return EXIT_MALFORMED
    if mode == "write":
        # The mutating --write half composes the U6 store-integrity gate in the U4 engine. Resolve here to
        # gather the inert git-derived observations (tracked, actual_remote, prior) the gate consumes; the
        # engine re-resolves (idempotent) and owns the NOT-ADOPTED (0) / non-resolved (2) messaging and the
        # gate itself, so a NOT-ADOPTED or unresolved root needs no observations and never writes.
        argv = ["--write"] if root is None else ["--root", root, "--write"]
        try:
            res = _opf_store.resolve_store(Path(os.path.abspath(root if root is not None else ".")))
        except Exception as exc:  # noqa: BLE001  a resolver escape is cannot-evaluate, never a write
            print("opf render: cannot evaluate: unexpected error resolving the store ({!r}); failing closed "
                  "to exit 2".format(exc), file=sys.stderr)
            return EXIT_MALFORMED
        obs = None
        if res.status == _opf_store.RESOLVED:
            try:
                obs, notes = _opf_observe.gather(res)
            except Exception as exc:  # noqa: BLE001  a gather escape must not become a silent write; fail closed
                print("opf render: cannot evaluate: unexpected error gathering git observations ({!r}); "
                      "failing closed to exit 2".format(exc), file=sys.stderr)
                return EXIT_MALFORMED
            for note in notes:
                # Surface each honest observation gap so the gate's cannot-evaluate reads as an explained
                # disclosure, not silent store corruption (the doctor idiom).
                print("opf render: note: {}".format(note))
        try:
            return _opf_views.render(argv, observations=obs)
        except Exception as exc:  # noqa: BLE001  fail-closed backstop, never a false verdict or uncaught exit-1
            print("opf render: cannot evaluate: unexpected error in the render write ({!r}); failing closed "
                  "to exit 2".format(exc), file=sys.stderr)
            return EXIT_MALFORMED
    argv = ["--check"] if root is None else ["--root", root, "--check"]
    # Class-width backstop: the render dispatch forwards the U4 engine's defined 0/1/2 contract unchanged;
    # any residual, unforeseen error from it routes to a located cannot-evaluate (exit 2), never an uncaught
    # exit-1 escape. KeyboardInterrupt/SystemExit are BaseException and stay uncaught.
    try:
        return _opf_views.render(argv)
    except Exception as exc:  # noqa: BLE001  fail-closed backstop, never a false verdict or uncaught exit-1
        print("opf render: cannot evaluate: unexpected error in the render check ({!r}); failing closed to "
              "exit 2".format(exc), file=sys.stderr)
        return EXIT_MALFORMED


def _doctor_report(result):
    """Print a CONCISE doctor report: the ordered per-check verdict map, then the findings and
    cannot-evaluates, then a one-line residual count. NO diff-style dump (no-console-diff-dumps): this is a
    structured verdict list, not a wall of before/after lines."""
    print("opf doctor: store integrity: {}".format(result.status))
    for cid, verdict in result.checks.items():
        print("  {}: {}".format(cid, verdict))
    for f in result.findings:
        print("  FINDING: {}".format(f))
    for c in result.cannot_evaluate:
        print("  CANNOT-EVALUATE: {}".format(c))
    if result.triage:
        print("  partial-import triage entries: {}".format(len(result.triage)))
    print("  residuals (disclosed by-design, not gradeable): {}".format(len(result.residuals)))


def _cmd_doctor(rest):
    """`opf doctor [--root DIR]`: the store-integrity verb.

    Doctor RESOLVES the store at --root (default: the cwd product repository root), gathers the inert
    git-derived observations (_opf_observe.gather: tracked, actual_remote, prior), and runs the U6 whole-store
    integrity engine (_opf_check.validate_store) over them, returning that engine's own 0/1/2 contract via
    _opf_check.exit_code (0 VALID, 1 INVALID, 2 CANNOT-EVALUATE). A NOT-ADOPTED root reports NOT APPLICABLE
    and exits 0 (the pack's own `--root .` case, mirroring render); any other non-RESOLVED status is a located
    cannot-evaluate (exit 2). Doctor is READ-ONLY: it makes no store change (SECI-preview-has-no-side-effects);
    the observation gather is git reads only. The parser is the house fail-closed idiom (unknown token, an
    empty or option-looking or duplicate --root value -> exit 2), matching _cmd_render's --root loop. Every
    residual escape from the resolver, the git gather, or the engine fails closed to exit 2 (never a false
    verdict), the same class-width backstop the render dispatch carries."""
    root = None
    i = 0
    while i < len(rest):
        tok = rest[i]
        if tok == "--root":
            if i + 1 >= len(rest):
                print("opf doctor: --root requires a directory argument", file=sys.stderr)
                return EXIT_MALFORMED
            if root is not None:
                print("opf doctor: --root given more than once", file=sys.stderr)
                return EXIT_MALFORMED
            val = rest[i + 1]
            if val == "" or val.startswith("-"):
                print("opf doctor: --root requires a non-empty directory argument, not {!r}".format(val),
                      file=sys.stderr)
                return EXIT_MALFORMED
            root = val
            i += 2
        else:
            print("opf doctor: unrecognized argument {!r}".format(tok), file=sys.stderr)
            return EXIT_MALFORMED
    root = root if root is not None else "."
    try:
        res = _opf_store.resolve_store(Path(os.path.abspath(root)))
    except Exception as exc:  # noqa: BLE001  fail-closed: a resolver escape is cannot-evaluate, never a verdict
        print("opf doctor: cannot evaluate: unexpected error resolving the store at {!r} ({!r}); failing "
              "closed to exit 2".format(root, exc), file=sys.stderr)
        return EXIT_MALFORMED
    if res.status == _opf_store.NOT_ADOPTED:
        print("opf doctor: NOT APPLICABLE ({})".format(res.detail))
        return EXIT_OK
    if res.status != _opf_store.RESOLVED:
        print("opf doctor: cannot evaluate: {}".format(res.detail), file=sys.stderr)
        return EXIT_MALFORMED
    try:
        obs, notes = _opf_observe.gather(res)
    except Exception as exc:  # noqa: BLE001  a gather escape must not become a false verdict; fail closed
        print("opf doctor: cannot evaluate: unexpected error gathering git observations ({!r}); failing "
              "closed to exit 2".format(exc), file=sys.stderr)
        return EXIT_MALFORMED
    for note in notes:
        # Surface each honest observation gap so an adopter's CI does not misread it as store corruption.
        print("opf doctor: note: {}".format(note))
    try:
        result = _opf_check.validate_store(res, observations=obs)
    except Exception as exc:  # noqa: BLE001  the engine contracts never to raise; a residual escape fails closed
        print("opf doctor: cannot evaluate: unexpected error validating the store ({!r}); failing closed to "
              "exit 2".format(exc), file=sys.stderr)
        return EXIT_MALFORMED
    _doctor_report(result)
    return _opf_check.exit_code(result)


_INIT_MAX_ENTRIES = 4096
_INIT_MAX_DEPTH = 32
_INIT_MAX_NAME_BYTES = 1 << 20


def _init_kind(st):
    if stat.S_ISDIR(st.st_mode):
        return "directory"
    if stat.S_ISREG(st.st_mode):
        return "file"
    if stat.S_ISLNK(st.st_mode):
        return "symlink"
    return "special"


def _init_inventory(root_fd):
    """Inventory .working without following links, including hidden entries and empty directories.

    Bounds cover entry count, accumulated path bytes, and directory depth. An exceeded bound or
    unreadable entry produces an explicitly incomplete report and refuses initialization. This is
    an observation, not a filesystem snapshot: concurrent changes after enumeration remain possible.
    """
    journal = _opf_store._journal
    report = {"entries": [], "complete": False}
    name_bytes = 0

    def add(relpath, st):
        nonlocal name_bytes
        name_bytes += len(os.fsencode(relpath))
        if (len(report["entries"]) >= _INIT_MAX_ENTRIES
                or name_bytes > _INIT_MAX_NAME_BYTES):
            raise RuntimeError("foreign inventory exceeds entry/path-byte bounds")
        report["entries"].append({"path": relpath, "kind": _init_kind(st)})

    def walk(fd, prefix, depth):
        with os.scandir(fd) as entries:
            for entry in entries:
                relpath = prefix + "/" + entry.name
                st = os.stat(entry.name, dir_fd=fd, follow_symlinks=False)
                add(relpath, st)
                if stat.S_ISDIR(st.st_mode):
                    if depth >= _INIT_MAX_DEPTH:
                        raise RuntimeError("foreign inventory exceeds directory-depth bound")
                    child_fd = os.open(
                        entry.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                    try:
                        walk(child_fd, relpath, depth + 1)
                    finally:
                        os.close(child_fd)

    try:
        working = _opf_store.WORKING_DIRNAME
        st = journal._lstat_at(root_fd, working)
        if st is not None:
            if not stat.S_ISDIR(st.st_mode):
                add(working, st)
            else:
                working_fd = os.open(
                    working, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd)
                try:
                    walk(working_fd, working, 0)
                finally:
                    os.close(working_fd)
        report["complete"] = True
    except Exception as exc:  # noqa: BLE001  an incomplete inventory never licenses a write
        report["error"] = ascii(exc)
    report["entries"].sort(key=lambda row: row["path"])
    return report


def _init_git(git, root, args):
    """Reuse the existing explicit -C, scrubbed-environment, timeout-bounded git boundary."""
    result = _opf_observe._run_git(git, root, args)
    if not result.completed or result.rc != 0:
        raise RuntimeError("git preflight/read failed at {!r}: {}".format(
            str(root), result.err))
    return result.out


def _init_repo(root):
    """Confirm a real non-bare worktree at or above root, without requiring a commit."""
    git = _opf_observe._git_path()
    if git is None:
        raise RuntimeError("git preflight: git not found on PATH")
    args = ["rev-parse", "--is-inside-work-tree", "--is-bare-repository", "--show-toplevel"]
    raw = _init_git(git, root, args)
    lines = raw.split(b"\n")
    if (len(lines) != 4 or lines[:2] != [b"true", b"false"]
            or lines[-1] != b"" or not os.path.isabs(os.fsdecode(lines[2]))):
        raise RuntimeError("git preflight: root is not a confirmed non-bare worktree")
    repo = Path(os.path.abspath(os.fsdecode(lines[2])))
    if root != repo and repo not in root.parents:
        raise RuntimeError("git preflight: reported repository does not contain root")
    repo_fd = _opf_store._open_dir_nofollow(repo)
    os.close(repo_fd)
    if _init_git(git, repo, args) != raw:
        raise RuntimeError("git preflight: repository identity changed during confirmation")
    return git, repo


def _init_untracked(git, repo, root, paths):
    """Read the index, refusing unreadable state or already-tracked planned destinations.

    Checking .working also detects tracked sources whose working-tree copies were deleted. Those
    paths cannot honestly be described as newly untracked sources. No HEAD observation is needed.
    """
    prefix = root.relative_to(repo)
    scoped = sorted(str(prefix / path) for path in paths)
    tracked = _init_git(
        git, repo, ["--literal-pathspecs", "ls-files", "--cached", "-z", "--"] + scoped)
    if tracked:
        raise RuntimeError("planned destination already git-tracked: {!r}".format(
            os.fsdecode(tracked)))


def _init_unignored(git, repo, root, paths):
    """Refuse a planned destination git would ignore: an ignored store cannot be staged or discovered.

    check-ignore reports at least one ignored path with rc 0, none ignored with rc 1, and an error
    with rc >= 128. The rc drives the three-way decision; the output is only for the diagnostic. Paths
    are passed as arguments (git check-ignore accepts neither -z without --stdin, which the run helper has
    no channel for, nor --literal-pathspecs, which it rejects as unsupported magic). Each scoped path is
    given a literal "./" prefix so a leading colon or other pathspec-magic sigil in an adopter-supplied
    --root prefix is read as a path, not as magic: without it a ":name/..." prefix has its colon consumed
    as an empty magic signature and the check silently bypasses (rc 1, the store looks unignored), while a
    recognized short-magic letter over-refuses (rc >= 128). The prefix is a plain string because Path
    would normalize "./x" back to "x". The output is then one path per line over the controlled,
    newline-free internal destinations.

    OPF-D2B: the probe runs under _opf_observe._run_git_config_discovery, which PRESERVES git's global and
    system configuration DISCOVERY (unlike the default scrubbed observer environment that pins
    GIT_CONFIG_GLOBAL/SYSTEM to os.devnull), so a store ignored ONLY by the adopter's global or system
    core.excludesFile is now caught here, matching what the adopter's own `git add` would honour
    (guard-input-soundness). The environment still drops every redirect / object / trace variable, and an
    unsupported inherited runtime-config context fails closed. Called directly (not via _init_git, which
    raises on rc != 0) because rc 1 is the success case here; a timeout, launch failure, unexpected rc, OR a
    not-ignored (rc 1) result accompanied by any git diagnostic (an unverifiable "unignored" verdict) fails
    closed and refuses.
    """
    prefix = root.relative_to(repo)
    scoped = sorted("./" + str(prefix / path) for path in paths)
    result = _opf_observe._run_git_config_discovery(
        git, repo, ["check-ignore", "--"] + scoped)
    if not result.completed:
        raise RuntimeError(
            "git preflight: could not evaluate ignore status for planned destinations ({})".format(
                result.err))
    if result.rc == 0:
        ignored = [p for p in os.fsdecode(result.out).splitlines() if p]
        raise RuntimeError("planned destination is git-ignored: {!r}".format(ignored))
    if result.rc == 1 and result.err:
        raise RuntimeError(
            "git preflight: check-ignore reported not-ignored but emitted a diagnostic, so the ignore "
            "status is unverifiable ({})".format(result.err))
    if result.rc != 1:
        raise RuntimeError("git preflight: check-ignore failed (rc={}): {}".format(
            result.rc, result.err))


def _init_same_root(root, root_fd):
    check_fd = _opf_store._open_dir_nofollow(root)
    try:
        current = os.fstat(check_fd)
        opened = os.fstat(root_fd)
        if (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino):
            raise RuntimeError("root changed since its contained directory was opened")
    finally:
        os.close(check_fd)


def _init_create(root_fd, relpath, data):
    """Create through the existing shared O_EXCL primitive; never replace or remove an entry.

    _journal._recreate_file uses descriptor-relative O_CREAT|O_EXCL|O_NOFOLLOW, writes and fsyncs
    the new inode, and performs no rollback. A write failure can therefore leave a partial file.
    Parent handles prevent symlink redirection; concurrent directory renames are not serialized.
    """
    journal = _opf_store._journal
    pfd, name = journal._open_parent(root_fd, relpath)
    try:
        try:
            journal._recreate_file(pfd, name, data, 0o644)
            os.fsync(pfd)
        except Exception as exc:
            raise RuntimeError("create-only publication refused {!r}: {!r}".format(
                relpath, exc)) from exc
    finally:
        os.close(pfd)


def _init_observed(root_fd, directories, payloads):
    """Report the planned paths beneath the opened root, without inferring entry ownership."""
    journal = _opf_store._journal
    rows = []
    for relpath in list(directories) + list(payloads):
        row = {"path": relpath}
        try:
            st = journal._lstat_contained(root_fd, relpath)
            if st is None:
                row["state"] = "absent"
            elif relpath in directories:
                row["state"] = _init_kind(st)
            elif not stat.S_ISREG(st.st_mode):
                row["state"] = _init_kind(st)
            elif st.st_size != len(payloads[relpath]):
                row["state"] = "different-size"
                row["bytes"] = st.st_size
            else:
                pfd, name = journal._open_parent(root_fd, relpath)
                try:
                    data, opened = journal._read_at(
                        pfd, name, relpath, cap=len(payloads[relpath]) + 1)
                finally:
                    os.close(pfd)
                row["state"] = (
                    "matches-payload" if opened.st_nlink == 1 and data == payloads[relpath]
                    else "different-content-or-link-count")
        except Exception as exc:  # noqa: BLE001  unknown is never reported as absent or complete
            row["state"] = "cannot-evaluate"
            row["error"] = ascii(exc)
        rows.append(row)
    return rows


def _cmd_init(rest):
    """Create validated store sources and a pointer, without git writes or rendering.

    Preflight is read-only. Publication is create-only and deliberately not transactional: a
    later failure reports observed planned paths and leaves them for review. Enumeration, root
    identity checks, and final rereads do not serialize concurrent writers or directory renames.
    No lock, lease, rollback, adoption policy, or whole-store success verdict is supplied here.
    """
    root = None
    i = 0
    while i < len(rest):
        tok = rest[i]
        if tok == "--root":
            if i + 1 >= len(rest):
                print("opf init: --root requires a directory argument", file=sys.stderr)
                return EXIT_MALFORMED
            if root is not None:
                print("opf init: --root given more than once", file=sys.stderr)
                return EXIT_MALFORMED
            val = rest[i + 1]
            if val == "" or val.startswith("-"):
                print("opf init: --root requires a non-empty directory argument, not {!r}".format(val),
                      file=sys.stderr)
                return EXIT_MALFORMED
            root = val
            i += 2
        else:
            print("opf init: unrecognized argument {!r}".format(tok), file=sys.stderr)
            return EXIT_MALFORMED
    root = root if root is not None else "."
    root_fd = None
    directories = []
    payloads = {}
    publishing = False
    stage = "preflight"
    try:
        import shlex
        import _opf_init

        journal = _opf_store._journal
        journal.require_containment()
        root = Path(os.path.abspath(root))
        root_fd = _opf_store._open_dir_nofollow(root)
        git, repo = _init_repo(root)

        inventory = _init_inventory(root_fd)
        if inventory["entries"] or not inventory["complete"]:
            print(json.dumps(dict(inventory, event="foreign-content", root=str(root)),
                             sort_keys=True))
        if not inventory["complete"]:
            raise RuntimeError("foreign .working inventory incomplete; refusing initialization")

        # Resolution detects stores through either pointer and through default discovery. Inventory
        # remains independent so malformed stores and foreign content also receive a concrete report.
        res = _opf_store.resolve_store(root)
        pointers = [
            name for name in (_opf_store.POINTER_REL, _opf_store.LOCAL_POINTER_REL)
            if journal._lstat_at(root_fd, name) is not None
        ]
        if pointers:
            raise RuntimeError("existing pointer(s): {}".format(", ".join(pointers)))
        if res.status == _opf_store.RESOLVED:
            raise RuntimeError("existing store: {}".format(res.detail))
        if inventory["entries"]:
            raise RuntimeError("foreign .working content; {}; refusing initialization".format(
                res.detail))
        if res.status != _opf_store.NOT_ADOPTED:
            # The resolver also refuses an existing, empty .working directory. Do not reinterpret a
            # CANNOT-EVALUATE result as permission to initialize a partial store.
            raise RuntimeError("store resolution refused: {}".format(res.detail))

        stage = "building and validating source payloads"
        working = _opf_store.WORKING_DIRNAME
        machine = working + "/" + _opf_store.DEFAULT_MACHINE_SUBDIR
        documents = [
            (_opf_store.MANIFEST_NAME, _opf_init.build_manifest()),
            (_opf_check.COUNTERS_NAME, _opf_init.build_counters()),
            (_opf_check.VERSION_NAME, _opf_init.build_version()),
            (_opf_check.WORKLOG_NAME, _opf_init.build_worklog()),
        ]
        documents.extend(
            (name + _opf_check.INDEX_SUFFIX, _opf_init.build_index(name))
            for name in _opf_init.INDEX_TYPES)
        payloads = {
            machine + "/" + name: text.encode("utf-8") for name, text in documents
        }
        payloads[_opf_store.POINTER_REL] = _opf_emit.emit_checked(
            {"store": {"target": "dir:."}}).encode("utf-8")
        if journal._lstat_at(root_fd, "CHANGELOG.md") is None:
            payloads["CHANGELOG.md"] = b"# Changelog\n"
        directories = [working, machine]

        stage = "checking the destination inventory"
        for relpath in directories + list(payloads):
            journal._check_rel(relpath)
            if journal._lstat_contained(root_fd, relpath) is not None:
                raise RuntimeError("destination already exists: {!r}".format(relpath))
        if journal._lstat_at(root_fd, _opf_store.LOCAL_POINTER_REL) is not None:
            raise RuntimeError("existing pointer: " + _opf_store.LOCAL_POINTER_REL)
        index_paths = set(payloads) | {working, _opf_store.LOCAL_POINTER_REL}
        _init_untracked(git, repo, root, index_paths)
        # The ignore check is scoped to the destinations init actually CREATES (store dirs plus the
        # committed pointer, the machine sources, and the optional CHANGELOG); the local pointer that
        # index_paths carries for _init_untracked's prior-state detection is a path init never creates
        # and adopters legitimately gitignore, so it must not make init refuse.
        _init_unignored(git, repo, root, set(directories) | set(payloads))
        _init_same_root(root, root_fd)

        publishing = True
        for relpath in directories:
            stage = "creating directory " + relpath
            pfd, name = journal._open_parent(root_fd, relpath)
            try:
                os.mkdir(name, 0o755, dir_fd=pfd)
                os.fsync(pfd)
            finally:
                os.close(pfd)
        for relpath, data in payloads.items():
            stage = "creating " + relpath
            _init_same_root(root, root_fd)
            _init_create(root_fd, relpath, data)

        stage = "observing published source paths"
        observed = _init_observed(root_fd, directories, payloads)
        if any(row["state"] != (
                "directory" if row["path"] in directories else "matches-payload")
               for row in observed):
            raise RuntimeError("published paths do not match the validated payloads")
        final_inventory = _init_inventory(root_fd)
        expected_working = {machine} | {
            path for path in payloads if path.startswith(working + "/")
        }
        if (not final_inventory["complete"]
                or {row["path"] for row in final_inventory["entries"]} != expected_working):
            print(json.dumps(dict(final_inventory, event="post-publish-inventory", root=str(root)),
                             sort_keys=True))
            raise RuntimeError("working inventory changed during publication")
        if journal._lstat_at(root_fd, _opf_store.LOCAL_POINTER_REL) is not None:
            raise RuntimeError("local pointer appeared during publication")
        _init_same_root(root, root_fd)
        _init_untracked(git, repo, root, index_paths)

        print("opf init: store SOURCES created and {} pointer written.".format(
            _opf_store.POINTER_REL))
        print(json.dumps({"event": "created", "root": str(root), "paths": list(payloads)},
                         sort_keys=True))
        print("opf init: these created paths are NOT yet git-tracked (final index read).")
        print("Review the created files, then stage the reviewed paths:")
        print("  git -C {} --literal-pathspecs add -- {}".format(
            shlex.quote(str(root)), " ".join(shlex.quote(path) for path in payloads)))
        print("opf init: ignore eligibility, including the global and system core.excludesFile, was checked")
        print("  before creation; a later ignore or config change can still affect staging (git add -f).")
        print("Commit the reviewed init paths, then materialize the Markdown views:")
        print("  opf render --write --root {}".format(shlex.quote(str(root))))
        print("opf init: exit 0 means valid sources were created; tracking and rendering are pending.")
        return EXIT_OK
    except Exception as exc:  # noqa: BLE001  includes InitError and residual I/O/import errors
        print("opf init: cannot evaluate at {} during {}: {}; exit 2".format(
            ascii(str(root)), stage, ascii(exc)), file=sys.stderr)
        if publishing and root_fd is not None:
            try:
                _init_same_root(root, root_fd)
                binding = "same-root"
            except Exception as binding_exc:
                binding = "cannot-confirm-root: " + ascii(binding_exc)
            print(json.dumps({
                "event": "partial-publication",
                "root": str(root),
                "scope": "opened-root-descriptor",
                "root_binding": binding,
                "paths": _init_observed(root_fd, directories, payloads),
            }, sort_keys=True), file=sys.stderr)
            print("opf init: publication may be partial; review the observed state. No rollback performed.",
                  file=sys.stderr)
        else:
            print("opf init: preflight refused; no publication attempted.", file=sys.stderr)
        return EXIT_MALFORMED
    finally:
        if root_fd is not None:
            _opf_store._journal._close_fd_quietly(root_fd)


# --- opf upgrade: the 1.0.0 -> 1.1.0 store-schema upgrade (spec 9.2) ---------------------------------

# The single 1.0.0 -> 1.1.0 upgrade this build implements. maintainer_decision and preference_pattern
# baseline (they were module-tier in 1.0.0); contribution is net-new; the decision_support module is
# retired (spec 8.1 note, spec 9.2). The delta is enumerated so the postcondition can assert the model
# diff equals EXACTLY it and nothing else (fail-closed on any stray change).
# _UPGRADE_TO is the literal the tooling implements; _cmd_upgrade asserts it equals the live
# _opf_store.SUPPORTED_SPEC_VERSION (bound only after _bootstrap), so a future spec bump cannot let this
# constant silently drift from the roster.
_UPGRADE_FROM = "1.0.0"
_UPGRADE_TO = "1.1.0"
_UPGRADE_NEW_TYPES = ("contribution", "maintainer_decision", "preference_pattern")
_UPGRADE_RETIRED_MODULE = "decision_support"
_UPGRADE_NEW_VIEWS = ("CONTRIBUTIONS.md", "DECISIONS.toml")
# The 1.0.0 -> 1.1.0 delta also WIDENS the existing DECISIONS.md composed view's source set: the two new
# baseline decision types join the two 1.0.0 sources, so the migrated view matches the 1.1.0 required set
# (spec 9.2). Without this, a genuine 1.0.0 store's 2-source DECISIONS.md stays 2-source after the delta and
# fails the render/doctor gate. DECISIONS.toml is a NET-NEW view (in _UPGRADE_NEW_VIEWS) and already carries
# the full four-source set from NAMED_VIEWS.
_UPGRADE_WIDENED_VIEW = "DECISIONS.md"
_UPGRADE_VIEW_FROM_SOURCES = ("pending_decision", "autonomous_decision")

# FIX5 forward-drift PIN (guard-input-soundness): the 1.0.0-valid [modules] vocabulary is the four 1.0.0
# baseline modules PLUS the one module this 1.0.0 -> 1.1.0 delta retires (decision_support); their union is
# exactly the merge-base (1c90fbb) _opf_store.KNOWN_MODULES. _upgrade_plan DERIVES that set from the LIVE
# _opf_store.KNOWN_MODULES at the point of use, which is correct today but UNPINNED: a future KNOWN_MODULES
# edit would silently shift what counts as a valid 1.0.0 module. This FROZEN set pins the intended 1.0.0
# vocabulary (a plain literal, since _opf_store is bound only after _bootstrap); _upgrade_plan reconciles
# the live derivation against it and fails closed on any divergence, and check_opf_upgrade.py binds the two,
# so a future drift fails a gate rather than re-vocabularying the check unseen.
_VALID_1_0_0_MODULES = frozenset({
    "governance", "delivery_assurance", "operational_policy", "concurrent_operation", "decision_support"})


class _UpgradeError(Exception):
    """A fail-closed upgrade refusal carrying the operator-facing reason (mapped to exit 2)."""


def _upgrade_read_bytes(root_fd, relpath, control=False):
    """Read a contained store file's raw bytes no-follow; None when the path is absent. A control file
    (the manifest) is read singly-linked (a hardlink to an out-of-tree victim is refused)."""
    journal = _opf_store._journal
    try:
        data, _st = journal._read_contained(root_fd, relpath, require_single_link=control)
    except journal.JournalError as exc:
        text = str(exc)
        if "cannot read contained file" in text and ("No such file" in text or "FileNotFound" in text):
            return None
        raise _UpgradeError("cannot read {!r} ({})".format(relpath, exc))
    return data


def _upgrade_replace(root_fd, relpath, data):
    """Atomically replace an EXISTING contained regular file with `data` (bytes), no-follow, preserving
    the destination's mode: a fresh O_EXCL temp beneath the same parent fd is written and atomically
    renamed over the entry (never an O_TRUNC of the name), the same reopen-TOCTOU-safe idiom as the U4
    view writer, minus its render write-gate flag (this is the schema-upgrade writer, not a render)."""
    journal = _opf_store._journal
    pfd, name = journal._open_parent(root_fd, relpath)
    try:
        st = journal._lstat_at(pfd, name)
        if st is None or not stat.S_ISREG(st.st_mode):
            raise _UpgradeError("refusing to rewrite {!r}: destination is not an existing regular file "
                                "(a symlink or special file is never followed; fail-closed)".format(relpath))
        tmpname = ".{}.opf-upgrade.{}.{}".format(name, os.getpid(), os.urandom(8).hex())
        fd = os.open(tmpname, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600, dir_fd=pfd)
        renamed = False
        try:
            try:
                os.fchmod(fd, stat.S_IMODE(st.st_mode))
                journal._write_all(fd, data)
                os.fsync(fd)
            finally:
                os.close(fd)
            os.rename(tmpname, name, src_dir_fd=pfd, dst_dir_fd=pfd)
            renamed = True
            os.fsync(pfd)
        finally:
            if not renamed:
                try:
                    os.unlink(tmpname, dir_fd=pfd)
                except OSError:
                    pass
    finally:
        os.close(pfd)


def _upgrade_create_index(root_fd, relpath, data):
    """Create a missing empty index file create-only (O_EXCL, no-follow); an already-present index is
    LEFT untouched (governance-enabled MD/PP whose records are preserved byte-for-byte, spec 9.2).
    Returns True when this call created the file, False when it already existed."""
    journal = _opf_store._journal
    pfd, name = journal._open_parent(root_fd, relpath)
    try:
        if journal._lstat_at(pfd, name) is not None:
            return False
        journal._recreate_file(pfd, name, data, 0o644)
        os.fsync(pfd)
        return True
    finally:
        os.close(pfd)


def _upgrade_plan(manifest_model, counters_model):
    """Apply EXACTLY the spec-9.2 1.0.0 -> 1.1.0 allowed delta to the parsed manifest and counters models,
    returning (new_manifest, new_counters, added_namespaces, origin). It validates UP FRONT the 1.0.0-shape
    preconditions it can cheaply check on the PARSED models -- the required/optional table shapes, the base
    table token and spec_version, KNOWN module keys with boolean values, the module<->baseline-type coupling,
    and the pre-declared type/view rows -- and REFUSES any that fail (a _UpgradeError, fail-closed). It is NOT
    a complete 1.0.0 doctor: a 1.0.0 store invalid in a way these preconditions do not inspect (e.g. a missing
    type row, or a type carrying a wrong or non-normative namespace) is caught AFTER mutation by the render /
    final-doctor gate, recovering through the step-3 subtree-scoped restore -- the no-complete-pre-doctor
    residual disclosed on _cmd_upgrade. It is ORIGIN-AWARE: a governance-
    or decision_support-enabled 1.0.0 store already declares maintainer_decision / preference_pattern as a
    module-tier row (G1/G2), so the delta ADDS only the now-baseline types not already present and preserves
    a pre-declared row untouched; DECISIONS.md and the decision_support [modules] key are both OPTIONAL at
    1.0.0 (G3/G4), so an origin that omits them is migrated without inventing them. `_upgrade_postcondition`
    asserts the manifest AND counters model diffs equal EXACTLY the allowed delta (value-exact), so a stray
    mutation, a flipped retained boolean, a mutated retained row, or a lost high-water can never slip
    through."""
    import copy
    # The 1.0.0 input carries the RETIRED base table [devprocess] (PRIOR_STANDARD_TOKEN); the OPFiles
    # rebrand (1.1.0) renames it to [opf] (STANDARD_TOKEN) as part of this same allowed delta (spec 9.2).
    _prior_base = _opf_store.PRIOR_STANDARD_TOKEN
    # [devprocess] and [types] are REQUIRED for a migratable 1.0.0 store. [modules] and [views] are
    # OPTIONAL at 1.0.0: the merge-base doctor grades an ABSENT table valid-empty (_validate_modules(None)
    # / _validate_views(None) return CLEAN), so an origin that omits either migrates AS IF EMPTY rather
    # than being refused; a PRESENT-but-not-a-dict table stays a malformed refusal.
    for table in (_prior_base, "types"):
        if not isinstance(manifest_model.get(table), dict):
            raise _UpgradeError("manifest [{}] table is missing or malformed; not a store this "
                                "upgrade can migrate (fail-closed)".format(table))
    for table in ("modules", "views"):
        present = manifest_model.get(table)
        if present is not None and not isinstance(present, dict):
            raise _UpgradeError("manifest [{}] table is present but malformed (not a table); not a store "
                                "this upgrade can migrate (fail-closed)".format(table))
    if _opf_store.STANDARD_TOKEN in manifest_model:
        raise _UpgradeError("manifest already carries the renamed base table [{}]; store is not a clean "
                            "1.0.0 baseline (fail-closed)".format(_opf_store.STANDARD_TOKEN))
    if manifest_model[_prior_base].get("spec_version") != _UPGRADE_FROM:
        raise _UpgradeError("manifest spec_version is not {!r}; no known upgrade path".format(_UPGRADE_FROM))
    if manifest_model[_prior_base].get("standard") != _prior_base:
        raise _UpgradeError("manifest [{}].standard is not {!r}; not a store this upgrade migrates "
                            "(fail-closed)".format(_prior_base, _prior_base))
    if not isinstance(counters_model.get("counters"), dict):
        raise _UpgradeError("counters.toml [counters] table is missing or malformed (fail-closed)")

    modules = manifest_model.get("modules") or {}
    types = manifest_model["types"]
    views = manifest_model.get("views") or {}

    # --- ORIGIN FACTS (G1-G4): what this specific 1.0.0 origin family already carries. ---------------
    pre_declared = frozenset(t for t in _UPGRADE_NEW_TYPES if t in types)
    ds_key_present = _UPGRADE_RETIRED_MODULE in modules
    decisions_declared = _UPGRADE_WIDENED_VIEW in views
    origin = {"pre_declared": pre_declared, "ds_key_present": ds_key_present,
              "decisions_declared": decisions_declared}

    # --- PRECONDITIONS: refuse only a genuinely INVALID 1.0.0 input, fail-closed. --------------------
    # [modules] keys are OPTIONAL at 1.0.0 (default off, G3), so decision_support MAY be absent. Every
    # PRESENT module value must be a boolean: the merge-base doctor's _validate_modules grades a non-boolean
    # module value 1.0.0-INVALID, so a non-boolean on ANY module (not only the retired decision_support --
    # e.g. governance = "x") is refused upfront, fail-closed, keeping the docstring's up-front-validation
    # claim honest rather than silently carrying it through to a post-mutation
    # doctor failure. A doctor-VALID 1.0.0 store has only boolean module values, so this never rejects valid
    # input; every module key otherwise rides through untouched (the postcondition asserts value-exact).
    # Every [modules] KEY must be a KNOWN 1.0.0 module name (matching the merge-base _validate_modules, which
    # grades an unknown module key 1.0.0-INVALID). The 1.0.0-valid module vocabulary is the CURRENT baseline
    # module set PLUS the one module this delta RETIRES (decision_support), whose union is exactly the merge-
    # base KNOWN_MODULES; derived from the authoritative KNOWN_MODULES (guard-input-soundness), never a hard-
    # coded list. An unknown key (e.g. [modules].unknown_present) is refused UPFRONT, fail-closed, rather than
    # carried through to a post-mutation doctor failure -- keeping the docstring's up-front-validation claim
    # honest (a MISSING type row or WRONG namespace stays the render/doctor gate's job, disclosed on _cmd_upgrade).
    # FIX5: reconcile the LIVE derivation against the frozen pin (forward-drift guard). A KNOWN_MODULES edit
    # that shifts the 1.0.0 module vocabulary fails HERE (fail-closed), rather than silently re-scoping this
    # 1.0.0-validity check; the pin, not the live union, is then the vocabulary the check uses.
    _derived_1_0_0_modules = frozenset(_opf_store.KNOWN_MODULES) | {_UPGRADE_RETIRED_MODULE}
    if _derived_1_0_0_modules != _VALID_1_0_0_MODULES:
        raise _UpgradeError(
            "the derived 1.0.0 module vocabulary {} drifted from the pinned set {}; reconcile "
            "_VALID_1_0_0_MODULES with _opf_store.KNOWN_MODULES before upgrading (forward-drift pin, "
            "fail-closed)".format(sorted(_derived_1_0_0_modules), sorted(_VALID_1_0_0_MODULES)))
    _valid_1_0_0_modules = _VALID_1_0_0_MODULES
    for _mname in sorted(modules):
        if _mname not in _valid_1_0_0_modules:
            raise _UpgradeError("manifest [modules].{} is not a known 1.0.0 module (known: {}); not a valid "
                                "1.0.0 store (fail-closed)".format(
                                    _mname, ", ".join(sorted(_valid_1_0_0_modules))))
        if not isinstance(modules[_mname], bool):
            raise _UpgradeError("manifest [modules].{} is not a boolean; not a valid 1.0.0 store "
                                "(fail-closed)".format(_mname))
    # A now-baseline type PRE-DECLARED by a 1.0.0 module tier (G1/G2). contribution: no 1.0.0 tier
    # introduced it, so its presence is an impossible 1.0.0 shape -> always refuse. maintainer_decision:
    # only a governance-enabled store carried it; preference_pattern: only a decision_support-enabled
    # store; a pre-declared row whose gating module is not enabled is a module-inconsistent (1.0.0-INVALID)
    # shape, so refusing it keeps fail-closed on genuinely-invalid input. Each pre-declared row must be
    # EXACTLY {namespace = <normative>} (the 1.0.0 closed TYPE_KEYS + normative-namespace rule, G2).
    _pre_gate = {"contribution": None, "maintainer_decision": "governance",
                 "preference_pattern": _UPGRADE_RETIRED_MODULE}
    for tname in sorted(pre_declared):
        gate = _pre_gate[tname]
        if gate is None:
            raise _UpgradeError("manifest pre-declares baseline type {!r}, which no 1.0.0 module tier "
                                "introduced; an impossible 1.0.0 shape (fail-closed)".format(tname))
        if modules.get(gate) is not True:
            raise _UpgradeError("manifest declares type {!r} while its 1.0.0 module {!r} is not enabled; "
                                "a module-inconsistent 1.0.0 shape (fail-closed)".format(tname, gate))
        if types.get(tname) != {"namespace": _opf_store.BASELINE_TYPES[tname]}:
            raise _UpgradeError("manifest [types.{}] is not the exact 1.0.0 baseline row (namespace = "
                                "{!r}); not a clean 1.0.0 shape (fail-closed)".format(
                                    tname, _opf_store.BASELINE_TYPES[tname]))
    # SYMMETRIC module-coupling precondition (C-ROSTER, mirroring the merge-base 1.0.0 doctor). The loop
    # above refuses a DECLARED now-baseline type whose gating 1.0.0 module is not enabled; this refuses the
    # CONVERSE -- a 1.0.0 module that gates a now-baseline type (maintainer_decision<-governance,
    # preference_pattern<-decision_support) is ENABLED while that type's [types] row is ABSENT. Such an
    # origin is module-inconsistent, so the merge-base 1.0.0 doctor grades it NOT-VALID; without this the
    # delta would SILENTLY CURE it by adding the now-baseline row and exit 0, contradicting the docstring's
    # up-front-validation claim (this coupling IS one of the cheap parsed-model checks it promises). Refuse
    # it upfront, unmutated.
    for tname, gate in sorted(_pre_gate.items()):
        if gate is not None and modules.get(gate) is True and tname not in pre_declared:
            raise _UpgradeError("manifest enables 1.0.0 module {!r} but omits its required type {!r}; a "
                                "module-inconsistent 1.0.0 shape the delta must not silently cure "
                                "(fail-closed)".format(gate, tname))
    # The two NET-NEW 1.1.0 views could not exist at 1.0.0 (no 1.0.0 view renderer), so a store
    # pre-declaring either is 1.0.0-INVALID; refuse.
    for vname in _UPGRADE_NEW_VIEWS:
        if vname in views:
            raise _UpgradeError("manifest already declares view {!r}; store is not a clean 1.0.0 "
                                "baseline (fail-closed)".format(vname))
    # DECISIONS.md is OPTIONAL at 1.0.0 (G4): a store may omit it and stay valid, so an absent view is
    # neither widened nor created (M2). When DECLARED, its sources must be EXACTLY the two 1.0.0 decision
    # sources as a LIST with no duplicates and all strings (a doctor-VALID 1.0.0 store may order the pair
    # either way but never duplicates it, G4).
    if decisions_declared:
        _dv_old = views.get(_UPGRADE_WIDENED_VIEW)
        _srcs = _dv_old.get("sources") if isinstance(_dv_old, dict) else None
        if not (isinstance(_dv_old, dict) and isinstance(_srcs, list)
                and all(isinstance(s, str) for s in _srcs)
                and len(_srcs) == len(set(_srcs))
                and set(_srcs) == set(_UPGRADE_VIEW_FROM_SOURCES)):
            raise _UpgradeError("manifest [views.{!r}] is not the 1.0.0 baseline composed view (sources "
                                "must be the two 1.0.0 decision sources {}, no duplicates); not a clean "
                                "1.0.0 baseline (fail-closed)".format(
                                    _UPGRADE_WIDENED_VIEW, sorted(_UPGRADE_VIEW_FROM_SOURCES)))

    # --- DELTA APPLICATION (spec 9.2). --------------------------------------------------------------
    new_manifest = copy.deepcopy(manifest_model)
    # Rename the base table [devprocess] -> [opf] and its discovery token, and bump spec_version, all in
    # the one allowed delta (spec 9.2). The table body is otherwise carried over unchanged.
    _base = new_manifest.pop(_prior_base)
    _base["standard"] = _opf_store.STANDARD_TOKEN
    _base["spec_version"] = _UPGRADE_TO
    new_manifest[_opf_store.STANDARD_TOKEN] = _base
    if ds_key_present:
        del new_manifest["modules"][_UPGRADE_RETIRED_MODULE]
    for tname in _UPGRADE_NEW_TYPES:
        if tname not in pre_declared:
            new_manifest["types"][tname] = {"namespace": _opf_store.BASELINE_TYPES[tname]}
    # An origin that OMITTED [views] entirely (R2) migrates AS IF EMPTY: the table is created here to
    # carry the two net-new 1.1.0 views (an absent [modules] stays absent -- the delta only ever REMOVES a
    # modules key, so migrate-as-empty is a no-op there and no empty table is invented).
    new_manifest.setdefault("views", {})
    for vname in _UPGRADE_NEW_VIEWS:
        kind, sources, _renderer = _opf_views.NAMED_VIEWS[vname]
        new_manifest["views"][vname] = {
            "kind": kind,
            "sources": list(sources),
            "target": "{}/{}".format(_opf_store.WORKING_DIRNAME, vname),
        }
    # Widen the existing DECISIONS.md composed view to the 1.1.0 required source set ONLY when it is
    # declared (spec 9.2 widens "the existing DECISIONS.md composed view"; an absent view has no existence
    # to widen, G4). Set `sources` to the canonical ordered required set from NAMED_VIEWS so the migrated
    # row is byte-identical to a freshly initialized 1.1.0 store's row.
    if decisions_declared:
        new_manifest["views"][_UPGRADE_WIDENED_VIEW]["sources"] = list(
            _opf_views.NAMED_VIEWS[_UPGRADE_WIDENED_VIEW][1])

    new_counters = copy.deepcopy(counters_model)
    added = []
    for tname in _UPGRADE_NEW_TYPES:
        ns = _opf_store.BASELINE_TYPES[tname]
        if ns not in new_counters["counters"]:
            new_counters["counters"][ns] = 0
            added.append(ns)

    # POSTCONDITION (spec 9.2), value-exact: the manifest AND counters model diffs equal EXACTLY the
    # allowed delta and nothing else. Factored pure so it is directly unit-testable (m1).
    _upgrade_postcondition(manifest_model, new_manifest, counters_model, new_counters, origin)
    return new_manifest, new_counters, added, origin


def _upgrade_postcondition(old_manifest, new_manifest, old_counters, new_counters, origin):
    """The spec-9.2 upgrade postcondition, factored PURE so it is directly unit-testable (m1). It recomputes
    the EXPECTED new models from the OLD models plus the origin facts and compares wholesale, value-exact,
    with a per-table failure message for diagnosability. Raises _UpgradeError (fail-closed) on any
    divergence, so a stray mutation, a flipped retained module boolean, a mutated retained [types] row, a
    reordered or duplicated view source, or a lowered/raised/lost counter high-water can never slip through
    (fixes the set-only / key-set-only comparisons this replaced)."""
    import copy
    _prior_base = _opf_store.PRIOR_STANDARD_TOKEN
    _tok = _opf_store.STANDARD_TOKEN
    pre_declared = origin["pre_declared"]
    ds_key_present = origin["ds_key_present"]
    decisions_declared = origin["decisions_declared"]

    # Base table: renamed [devprocess] -> [opf], standard token flipped and spec_version bumped, every
    # other base key carried over value-exact.
    if _prior_base in new_manifest or _tok not in new_manifest:
        raise _UpgradeError("upgrade postcondition failed: base table not renamed [{}] -> [{}]".format(
            _prior_base, _tok))
    expected_base = dict(old_manifest[_prior_base])
    expected_base["standard"] = _tok
    expected_base["spec_version"] = _UPGRADE_TO
    if new_manifest.get(_tok) != expected_base:
        raise _UpgradeError("upgrade postcondition failed: base table [{}] changed beyond the standard-"
                            "token rename and spec_version bump".format(_tok))

    # [modules]: exactly the retired decision_support key removed when it was present, every other key
    # (name AND boolean value) carried over value-exact -- so a flipped retained boolean now refuses.
    expected_modules = {k: v for k, v in (old_manifest.get("modules") or {}).items()
                        if not (ds_key_present and k == _UPGRADE_RETIRED_MODULE)}
    if new_manifest.get("modules", {}) != expected_modules:
        raise _UpgradeError("upgrade postcondition failed: [modules] is not exactly the origin table with "
                            "the retired {!r} key removed".format(_UPGRADE_RETIRED_MODULE))

    # [types]: exactly the origin rows plus the now-baseline rows NOT already pre-declared, value-exact --
    # so a mutated namespace or a stray key in a retained row now refuses, and a pre-declared row is
    # admitted exactly.
    expected_types = dict(old_manifest["types"])
    for tname in _UPGRADE_NEW_TYPES:
        if tname not in pre_declared:
            expected_types[tname] = {"namespace": _opf_store.BASELINE_TYPES[tname]}
    if new_manifest["types"] != expected_types:
        raise _UpgradeError("upgrade postcondition failed: [types] is not exactly the origin rows plus the "
                            "added baseline rows")

    # [views]: origin rows, plus the two constructed new rows, plus (when declared) DECISIONS.md with its
    # sources replaced by the exact canonical ordered required list; value-exact, so the sources assertion
    # is order- AND duplicate-exact.
    expected_views = copy.deepcopy(old_manifest.get("views") or {})
    for vname in _UPGRADE_NEW_VIEWS:
        kind, sources, _renderer = _opf_views.NAMED_VIEWS[vname]
        expected_views[vname] = {
            "kind": kind,
            "sources": list(sources),
            "target": "{}/{}".format(_opf_store.WORKING_DIRNAME, vname),
        }
    if decisions_declared:
        expected_views[_UPGRADE_WIDENED_VIEW] = dict(expected_views[_UPGRADE_WIDENED_VIEW])
        expected_views[_UPGRADE_WIDENED_VIEW]["sources"] = list(
            _opf_views.NAMED_VIEWS[_UPGRADE_WIDENED_VIEW][1])
    if new_manifest["views"] != expected_views:
        raise _UpgradeError("upgrade postcondition failed: [views] is not exactly the origin rows plus the "
                            "two new views and the widened DECISIONS.md")

    # Every OTHER top-level table (store, providers, deliverables, archive, unmanaged, vendors, profiles,
    # ...) is byte-identical: value-exact deep equality over the whole remaining table set.
    _handled = {"modules", "types", "views", _prior_base, _tok}
    for table in set(old_manifest) | set(new_manifest):
        if table in _handled:
            continue
        if old_manifest.get(table) != new_manifest.get(table):
            raise _UpgradeError("upgrade postcondition failed: table [{}] changed but is not in the allowed "
                                "delta (fail-closed)".format(table))

    # Counters (m1): every existing high-water preserved value-exact, EXACTLY the CN/MD/PP namespaces
    # ABSENT from the origin added as zeros, the schema key and any other content untouched.
    if not isinstance(old_counters.get("counters"), dict):
        raise _UpgradeError("upgrade postcondition failed: origin counters [counters] table malformed")
    expected_counters = copy.deepcopy(old_counters)
    for tname in _UPGRADE_NEW_TYPES:
        ns = _opf_store.BASELINE_TYPES[tname]
        if ns not in expected_counters["counters"]:
            expected_counters["counters"][ns] = 0
    if new_counters != expected_counters:
        raise _UpgradeError("upgrade postcondition failed: counters is not exactly the origin high-waters "
                            "with the missing CN/MD/PP namespaces added as zeros")


def _cmd_upgrade(rest):
    """`opf upgrade [--root DIR]`: the in-place, additive, idempotent 1.0.0 -> 1.1.0 store-schema upgrade
    (spec 9.2). It RESOLVES the store at --root, refuses fail-closed on a store above the tooling spec or on
    a non-canonical (hand-edited/comment-bearing) manifest or counters, applies EXACTLY the allowed delta as
    a model regeneration through the canonical new-document emitter (bump spec_version; drop the retired
    decision_support module WHERE PRESENT; add each contribution/maintainer_decision/preference_pattern type
    row NOT already declared by an enabled 1.0.0 module; add the two new view rows; WIDEN the DECISIONS.md
    composed view WHERE DECLARED; extend counters with the CN/MD/PP zeros preserving existing high-waters;
    create the missing empty indexes, skipping any that already exist), RENDERS the declared views, and
    requires a full doctor VALID before offering the staged change. It is ORIGIN-AWARE: a governance- or
    decision_support-enabled 1.0.0 store, and a store that omits the optional decision_support key or the
    DECISIONS.md view, each migrate correctly (spec 9.2, G1-G4). Before ANY write it enforces two fail-closed
    preconditions: STORE-PATH CLEANLINESS over exactly the paths it writes (HEAD is a verified restore path,
    SECA-verified-restore-path) and a SINGLE-WRITER LEASE it claims atomically and holds across the mutation,
    render, and final doctor (spec 5.7). It NEVER commits: the adopter reviews and merges. A store already at
    {to} is a byte no-op (idempotent, still requiring doctor-VALID); a NOT-ADOPTED root is NOT APPLICABLE
    (exit 0), any other non-resolved status a located cannot-evaluate (exit 2). Two disclosed residuals: a
    killed run leaves the lease, which is spec-conformant (present only while held; a leftover is released
    through operator reconciliation, spec 5.7) and is what the EEXIST refusal covers; and the lease is not
    made observable at a sync target before writes (spec 5.7) because this build has no sync runtime, so the
    guarantee is single-host single-writer. A third disclosed residual: this build has no 1.0.0 pre-doctor,
    so a 1.0.0 store invalid in a way the origin preconditions do not inspect fails only AFTER mutation (at
    the render or the final doctor), recovering through the step-3 subtree-scoped restore; the committed HEAD
    stays a verified restore path for the whole blast radius, so no owner work is lost.""".format(
        to=_UPGRADE_TO)
    root = None
    i = 0
    while i < len(rest):
        tok = rest[i]
        if tok == "--root":
            if i + 1 >= len(rest):
                print("opf upgrade: --root requires a directory argument", file=sys.stderr)
                return EXIT_MALFORMED
            if root is not None:
                print("opf upgrade: --root given more than once", file=sys.stderr)
                return EXIT_MALFORMED
            val = rest[i + 1]
            if val == "" or val.startswith("-"):
                print("opf upgrade: --root requires a non-empty directory argument, not {!r}".format(val),
                      file=sys.stderr)
                return EXIT_MALFORMED
            root = val
            i += 2
        else:
            print("opf upgrade: unrecognized argument {!r}".format(tok), file=sys.stderr)
            return EXIT_MALFORMED
    root = root if root is not None else "."
    try:
        return _upgrade_run(root)
    except _UpgradeError as exc:
        print("opf upgrade: refused: {}; exit 2".format(exc), file=sys.stderr)
        return EXIT_MALFORMED
    except Exception as exc:  # noqa: BLE001  class-width fail-closed backstop, never a false success
        print("opf upgrade: cannot evaluate: unexpected error ({!r}); failing closed to exit 2".format(exc),
              file=sys.stderr)
        return EXIT_MALFORMED


def _upgrade_doctor(root):
    """Resolve the store at `root`, gather its git-derived observations, and run the full offline doctor
    (`validate_store`). Returns the _opf_check validate result (its `.status` is `_opf_store.VALID` on a
    clean store). Raises _UpgradeError, fail-closed, when the store no longer resolves."""
    dres = _opf_store.resolve_store(Path(os.path.abspath(root)))
    if dres.status != _opf_store.RESOLVED:
        raise _UpgradeError("the store at {!r} no longer resolves ({}); fail-closed".format(root, dres.detail))
    dobs, _dnotes = _opf_observe.gather(dres)
    return _opf_check.validate_store(dres, observations=dobs)


# --- STEP 3/4 upgrade preconditions: store cleanliness and the single-writer lease -------------------

_UPGRADE_NO_WHOLE_TREE = ("Never run a whole-tree restore (git restore . / git reset --hard): it would "
                          "destroy unrelated uncommitted work. Scope every recovery to the store subtree.")


def _upgrade_partial_recovery_text(store_root):
    """Recovery advice for the read-only F2 triage (a store declares the target spec_version but is NOT
    doctor-VALID: a previously interrupted run). This path cannot know what that run touched, so the advice
    stays SUBTREE-SCOPED and review-first: inspect, then restore tracked store paths and remove upgrade-
    created untracked files, all under `.working`, never a whole-tree restore (preserve-uncommitted-work).
    The advice names the resolved STORE root (where `.working` lives), NOT the CLI product root: for a
    RELOCATED store the two differ, and the CLI root would aim the `.working` restore at the wrong
    repository (explicit-binding-over-ambient-context)."""
    import shlex
    r = shlex.quote(str(store_root))
    w = shlex.quote(_opf_store.WORKING_DIRNAME)
    return ("Inspect the store subtree (git -C {r} --literal-pathspecs status -- {w}); restore ONLY its "
            "tracked paths (git -C {r} --literal-pathspecs restore --staged --worktree -- {w}) and remove "
            "the upgrade-created untracked files it lists, then re-run. {no}".format(r=r, w=w,
                                                                                     no=_UPGRADE_NO_WHOLE_TREE))


def _upgrade_recovery_text(store_root, product_root, created_relpaths, product_relpaths):
    """Recovery advice for a post-mutation failure (render or doctor): the touched set is KNOWN, and with the
    step-3 cleanliness precondition in force everything dirty under the probe scope after a failed run is
    upgrade-written by construction, so this enumerated, subtree-scoped remedy is COMPLETE for the run that
    printed it. The DISTINCT roots are threaded (explicit-binding-over-ambient-context): tracked store paths
    under `.working`, and the upgrade-created untracked files, are recovered under the STORE root (where
    `.working` lives); a declared product-scope target rendered this run is recovered under the PRODUCT root.
    The two roots differ for a RELOCATED store (the pointer resolves `.working` to a store separate from the
    product tree), where using one root for both would aim the `.working` restore at the wrong repository.
    Never a whole-tree restore."""
    import shlex
    r = shlex.quote(str(store_root))
    w = shlex.quote(_opf_store.WORKING_DIRNAME)
    lines = ["opf upgrade: the staged change is left for review; recover it scoped to the paths this run "
             "wrote (the cleanliness precondition proved nothing else is in the blast radius):",
             "  restore tracked store paths: git -C {} --literal-pathspecs restore --staged --worktree "
             "-- {}".format(r, w)]
    if created_relpaths:
        lines.append("  remove upgrade-created files: rm -- " + " ".join(
            shlex.quote(os.path.join(str(store_root), p)) for p in sorted(created_relpaths)))
    pr = shlex.quote(str(product_root))
    for p in sorted(product_relpaths):
        lines.append("  product target rendered: git -C {} --literal-pathspecs restore --staged --worktree "
                     "-- {} (or remove it if this run created it)".format(pr, shlex.quote(p)))
    lines.append("  inspect first: git -C {} --literal-pathspecs status -- {}".format(r, w))
    lines.append("  " + _UPGRADE_NO_WHOLE_TREE)
    return "\n".join(lines)


def _upgrade_product_render_targets(manifest_model):
    """The product-scope (VERSION) destinations among the manifest's declared views, each a store-tree
    relpath derived from the view's OWN identity via _opf_views._spec_destination (guard-input-soundness:
    the pathspec set is derived from the authoritative declaration at the point of use, never hardcoded). A
    view name outside the closed render vocabulary is skipped here; the render/doctor path grades it."""
    targets = []
    for vname in (manifest_model.get("views") or {}):
        try:
            _opf_views._resolve_view(vname)
        except Exception:  # noqa: BLE001  an unrenderable declared view is graded by render/doctor, not here
            continue
        scope, relpath = _opf_views._spec_destination(vname)
        if scope == "product":
            targets.append(relpath)
    return targets


# The EXACT set of valid `git status --porcelain=v1 -z --untracked-files=all --no-renames` XY status PAIRS,
# enumerated precisely from the git-status(1) "Short Format" table for the installed git (git 2.53.0 in this
# env; the table is stable across modern git) and EMPIRICALLY cross-checked by driving real index/worktree
# states and observing the emitted XY. Validating the whole PAIR (not each char independently) closes the
# fail-open where an IMPOSSIBLE pair whose two chars each sit in a per-char set passes a char check and, for
# the lease path, matches the exclusion and is SILENTLY DROPPED -- the M3 cleanliness guard then reads dirt
# as clean. This is the EXACT man-page enumeration, NOT the {space,M,T,A,D} cartesian, which is a strict
# SUPERSET that would admit impossible ordinary pairs: git emits X=D ONLY with a space Y, and Y=A ONLY with a
# space X, so DM DT DA (and MA TA) are unemittable and MUST be refused, not silently accepted as dirt.
# Composition:
#   - "??" untracked (--untracked-files=all is passed).
#   - ORDINARY (non-unmerged) changed entries, per the first table section with rename R and copy C EXCLUDED
#     (--no-renames guarantees git emits neither) and U reserved to the unmerged tier. For each index letter X
#     the EXACT set of worktree letters Y git can pair it with, straight off the man-page rows:
#         X=' ' (index clean):        Y in {A, M, T, D}   (' A' intent-to-add is VALID and accepted)
#         X in {M, T, A}:             Y in {' ', M, T, D}  (staged change, worktree clean/modified/typechg/del)
#         X='D' (deleted from index): Y in {' '}           (a deleted-in-index path pairs ONLY with space)
#   - the seven UNMERGED pairs, verbatim from git-status(1): DD AU UD UA DU AA UU.
# "!!" (ignored) is deliberately ABSENT: --ignored is not passed, so it cannot appear and is treated as an
# unexpected/malformed pair -> fail-closed. The per-X worktree sets are enumerated here, not the whole valid
# set hardcoded flat, so each line stays auditable against the man-page table. Bytes throughout (2-byte keys).
_PORCELAIN_ORDINARY_YSET = {
    0x20:     b"AMTD",   # X=' ': worktree added(intent-to-add)/modified/type-changed/deleted
    ord("M"): b" MTD",   # X='M' (updated in index): worktree unmodified/modified/type-changed/deleted
    ord("T"): b" MTD",   # X='T' (type changed in index): worktree unmodified/modified/type-changed/deleted
    ord("A"): b" MTD",   # X='A' (added to index): worktree unmodified/modified/type-changed/deleted
    ord("D"): b" ",      # X='D' (deleted from index): worktree unmodified only
}
_PORCELAIN_UNMERGED_PAIRS = (b"DD", b"AU", b"UD", b"UA", b"DU", b"AA", b"UU")
_PORCELAIN_VALID_PAIRS = frozenset(
    [b"??"]
    + [bytes((x, y)) for x, ys in _PORCELAIN_ORDINARY_YSET.items() for y in ys]
    + list(_PORCELAIN_UNMERGED_PAIRS))


def _upgrade_parse_porcelain(raw, prefix, lease_excl):
    """Parse a `git status --porcelain=v1 -z --untracked-files=all --no-renames` payload into the list of
    dirty paths, each normalized `root`-relative (the `prefix`, the store's repo-root-relative path with a
    trailing '/', is stripped from every repository-root-relative porcelain path) and EXCLUDING `lease_excl`
    (a byte-literal store-relative path, when given). Factored PURE so the grammar refusal is directly unit-
    testable. The -z grammar is VALIDATED (guard-input-soundness): a non-empty payload is a run of
    NUL-TERMINATED records, each `XY<space>PATH` (two status chars, a space, then >=1 path byte); --no-renames
    means there is no second NUL-separated origin-path field. A payload that is not NUL-terminated, that
    carries a record shorter than `XY PATH` or lacking the status/space framing, or whose XY status is
    outside the porcelain v1 vocabulary (a bogus pair, a blank pair, or a rename/copy the --no-renames probe
    cannot emit), is MALFORMED and refuses fail-closed -- never a clean empty result on an unparseable
    payload (e.g. a lone NUL, which a naive split would read as clean), and never a silent lease-exclusion
    drop of a malformed-status record (check-fails-closed-on-unreadable)."""
    if not raw:
        return []
    parts = raw.split(b"\x00")
    if parts[-1] != b"":
        raise _UpgradeError("git status returned a porcelain payload that is not NUL-terminated; the store "
                            "cleanliness cannot be verified (fail-closed)")
    prefix_b = prefix.encode("utf-8")
    lease_b = lease_excl.encode("utf-8") if lease_excl is not None else None
    dirty = []
    for rec in parts[:-1]:
        # porcelain v1 -z: two status chars, a space, then the path bytes (verbatim under -z, no quoting).
        if len(rec) < 4 or rec[2:3] != b" ":
            raise _UpgradeError("git status returned a malformed porcelain record ({!r}); the store "
                                "cleanliness cannot be verified (fail-closed)".format(rec[:16]))
        # Validate the whole XY status PAIR against the enumerated valid-pair set BEFORE the lease exclusion
        # below (guard-input-soundness): a per-CHAR check accepts an IMPOSSIBLE pair (UT, ZZ, a blank pair, a
        # rename R, a copy C) whose chars each sit in a per-char set, and for the lease path that bogus record
        # would match the exclusion and be SILENTLY DROPPED -- a fail-open in the M3 cleanliness guard. The
        # pair is validated whole against _PORCELAIN_VALID_PAIRS (?? plus the EXACT man-page ordinary
        # enumeration, plus the seven unmerged pairs; !!, rename, copy, an impossible ordinary pair such as DM
        # or MA, and any U in a non-unmerged position all excluded).
        # Anything outside that set is MALFORMED -> fail-closed, never a drop.
        if rec[:2] not in _PORCELAIN_VALID_PAIRS:
            raise _UpgradeError("git status returned a porcelain record with an out-of-vocabulary "
                                "status pair ({!r}); the store cleanliness cannot be verified "
                                "(fail-closed)".format(rec[:16]))
        pbytes = rec[3:]
        if prefix_b and pbytes.startswith(prefix_b):
            pbytes = pbytes[len(prefix_b):]
        if lease_b is not None and pbytes == lease_b:
            # The lease path. ONLY a well-formed UNTRACKED ("??") lease is the legitimate held-lease case
            # that step 4 handles as its never-seize refusal, so it is EXCLUDED here. Any OTHER (tracked)
            # status on the lease path -- " D", "D ", " M", "MM", ... -- means the lease is COMMITTED or
            # otherwise version-controlled, which VIOLATES spec 5.7 (a lease is present only while held): the
            # store is anomalous. That is REFUSED fail-closed and NAMED DISTINCTLY here, never silently
            # excluded. A silent drop of a " D" (a committed lease deleted in the worktree) would let step 4's
            # O_EXCL acquire succeed on the now-absent file and sweep the tracked lease's DELETION into the
            # upgrade's staged change set (outside the spec-9.2 delta), while a post-mutation failure's
            # recovery text (git restore --staged --worktree -- .working) would RESURRECT the committed lease
            # from HEAD, which the next run then refuses on EEXIST until manual reconciliation.
            if rec[:2] == b"??":
                continue
            raise _UpgradeError(
                "the single-writer lease {!r} is TRACKED in git (porcelain status {!r}, not untracked "
                "'??'); a committed or otherwise version-controlled lease violates spec 5.7 (a lease is "
                "present only while held) and leaves the store in an anomalous state. Reconcile the store "
                "(remove the lease from version control) before re-running opf upgrade (fail-closed)".format(
                    lease_excl, rec[:2].decode("ascii", "replace")))
        dirty.append(pbytes.decode("utf-8", "replace"))
    return dirty


def _upgrade_probe_dirty(git, root, pathspecs, lease_excl):
    """Run ONE hardened `git status --porcelain=v1 -z --untracked-files=all --no-renames` over `pathspecs`
    beneath `root`, returning the list of dirty paths, each normalized to `root`-relative (excluding
    `lease_excl`, a byte-literal store-relative path, when given). The porcelain paths are REPOSITORY-root-
    relative, so the store's path within the repository (git rev-parse --show-prefix) is stripped, which is
    what lets `lease_excl` be excluded even when the store root lies BELOW the git repository root (a NESTED
    store, where the un-normalized compare missed and drew the step-3 dirty-store refusal in place of step
    4's held-lease message). Reuses _opf_observe's scrubbed-env, --no-replace-objects, -C-bound, timeout-
    bounded boundary (G6). Fail-closed: a non-completed probe, a not-a-repository, a nonzero exit, an
    undeterminable store prefix, or a malformed payload refuses; never a clean pass on an unreadable probe
    (check-fails-closed-on-unreadable, SECA-verified-restore-path)."""
    args = (["--literal-pathspecs", "status", "--porcelain=v1", "-z", "--untracked-files=all",
             "--no-renames", "--"] + list(pathspecs))
    out = _opf_observe._run_git(git, root, args)
    if not out.completed:
        raise _UpgradeError("could not verify the store is clean before the upgrade ({}); refusing the "
                            "destructive rewrite (fail-closed)".format(out.err.strip()))
    if out.rc != 0:
        if _opf_observe._is_no_repo(out):
            raise _UpgradeError("the store at {!r} is not a git repository, so HEAD is not a verified "
                                "restore path for the in-place rewrite (spec 5.1); refusing "
                                "(fail-closed)".format(str(root)))
        raise _UpgradeError("git could not verify the store is clean (rc {}); refusing the destructive "
                            "rewrite (fail-closed)".format(out.rc))
    pfx = _opf_observe._run_git(git, root, ["rev-parse", "--show-prefix"])
    if not pfx.completed or pfx.rc != 0:
        raise _UpgradeError("could not determine the store's path within its git repository; without it a "
                            "nested store's clean probe cannot be trusted, so the rewrite is refused "
                            "(fail-closed)")
    # Strip ONLY the trailing newline git appends, NEVER leading whitespace: a store dir whose name begins
    # with a space (" leading/") would lose that space under .strip(), breaking the prefix match and the
    # lease exclusion. "" at the repo toplevel, else "<dir>/" (trailing /).
    prefix = pfx.out.decode("utf-8", "replace").rstrip("\n")
    return _upgrade_parse_porcelain(out.out, prefix, lease_excl)


def _upgrade_check_clean(res, manifest_model):
    """STEP 3 (M3): store-cleanliness precondition, run AFTER the read-only triage/plan and immediately
    BEFORE lease acquisition and the first write. Prove over EXACTLY the paths this upgrade can write (the
    `.working` subtree at the store root, plus any declared product-scope render target) that the git index
    and working tree equal HEAD, so the committed HEAD is a verified restore path for the precise destruction
    surface (SECA-verified-restore-path). Only the UNTRACKED lease path is EXCLUDED (byte-literal): a held
    (untracked "??") lease is step 4's own specific never-seize refusal, not generic dirt; a TRACKED lease on
    that path is instead refused DISTINCTLY as a spec-5.7 violation (in _upgrade_parse_porcelain), never
    excluded. Refuses fail-closed (exit 2) on any dirt, naming up to 10 paths plus the total, advising
    commit-your-changes and NEVER a restore (the dirt is the owner's own work, preserve-uncommitted-work).

    Residual (disclose-guard-residuals): the probe uses --untracked-files=all, which does NOT surface a
    git-IGNORED file under the scope; a conforming store has no ignored render targets, so an ignored file
    there would neither block the run nor be restorable from HEAD. The alternative (--ignored) would over-
    fire on ordinary build detritus, so this build accepts and discloses the narrower scope. A committed or
    otherwise TRACKED lease.toml (itself a spec-5.7 violation: the lease should be present only while held) is
    NOT part of that residual and is NOT silently excluded like the legitimate untracked held lease: it
    surfaces on the lease path as a tracked porcelain status (e.g. " D" for a committed lease deleted in the
    worktree) and is REFUSED fail-closed by _upgrade_parse_porcelain, naming the spec-5.7 tracked-lease
    violation, so that corner is handled here rather than slipping past the lease exclusion into step 4."""
    git = _opf_observe._git_path()
    if git is None:
        raise _UpgradeError("cannot locate git to verify the store is clean before the upgrade; without a "
                            "verified restore path the destructive rewrite is refused (spec 5.1, fail-closed)")
    store_root = res.store_root
    product_root = res.product_root if res.product_root is not None else store_root
    lease_excl = "{}/{}".format(res.machine_rel, _opf_check.LEASE_NAME)
    store_specs = [_opf_store.WORKING_DIRNAME]
    product_specs = []
    same_root = os.path.abspath(str(product_root)) == os.path.abspath(str(store_root))
    for relpath in _upgrade_product_render_targets(manifest_model):
        (store_specs if same_root else product_specs).append(relpath)

    dirty = _upgrade_probe_dirty(git, store_root, store_specs, lease_excl)
    if product_specs:
        dirty += _upgrade_probe_dirty(git, product_root, product_specs, None)
    if dirty:
        shown = sorted(set(dirty))
        head = shown[:10]
        raise _UpgradeError(
            "the store working tree is not clean over the paths this upgrade writes: {} dirty path(s), "
            "showing {}: {}. Commit your store changes (or move them aside), then re-run opf upgrade; the "
            "uncommitted work is yours and the upgrade never restores or discards it.".format(
                len(shown), len(head), ", ".join(head)))


def _upgrade_lease_held_message(pfd, name, lease_rel):
    """Compose the EEXIST held-lease refusal, best-effort naming the existing holder/operation/acquired_at.
    A present-but-unreadable or malformed payload STILL refuses (present-is-held, matching C-LEASE); the
    lease is never seized or overwritten (spec 5.7). Names the manual reconciliation remedy the tool never
    performs itself."""
    import tomllib
    detail = "a present lease with an unreadable payload"
    try:
        # O_NONBLOCK so a FIFO (or other special file) planted at lease.toml cannot BLOCK the open (an
        # O_RDONLY open of a writer-less FIFO would hang indefinitely); fstat the opened fd and, for any
        # NON-REGULAR file, refuse WITHOUT reading (presence is refusal, matching C-LEASE; never block).
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=pfd)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                detail = "a present non-regular lease (held)"
            else:
                raw = os.read(fd, 65536)
                data = tomllib.loads(raw.decode("utf-8"))
                if isinstance(data, dict):
                    detail = "held by {!r} (operation {!r}, acquired_at {!r})".format(
                        data.get("holder"), data.get("operation"), data.get("acquired_at"))
        finally:
            os.close(fd)
    except Exception:  # noqa: BLE001  a present-but-unreadable lease still refuses (present is held)
        pass
    return ("another opf run holds the single-writer lease {}: {}. The lease is never seized (spec 5.7). "
            "If you have confirmed NO opf run is live, release the leftover lease as your own reconciliation "
            "step (the tool never removes a foreign lease), then re-run opf upgrade.".format(lease_rel, detail))


def _upgrade_lease_holder():
    """The holder identity THIS run stamps on the lease it creates: "opf-upgrade:<host>:<pid>". Single-
    sourced so the acquire WRITE and the release OWNERSHIP-CHECK reason about the same identity (the release
    proves ownership by a full-payload byte compare, which subsumes this holder, and names a foreign holder
    only in its diagnostic)."""
    import socket
    return "opf-upgrade:{}:{}".format(socket.gethostname(), os.getpid())


def _upgrade_acquire_lease(root_fd, machine_rel):
    """STEP 4 (M4): claim the single-writer lease ATOMICALLY (atomic-claim-from-pool). The claim IS the
    create: open machine_rel/lease.toml O_CREAT|O_EXCL|O_WRONLY|O_NOFOLLOW beneath the parent fd (the
    _upgrade_replace idiom), so there is no check-then-create gap and EEXIST IS the held-lease refusal. The
    closed payload (schema/holder/operation/acquired_at read from the clock, G5) satisfies C-LEASE exactly,
    so the mid-run doctor stays VALID with the lease held (containment-clean, spec 5.7/11). RETURNS the exact
    payload bytes written, so the ownership-verified release can prove the lease it removes is still THIS
    run's own (never-seize, spec 5.7)."""
    import datetime
    journal = _opf_store._journal
    lease_rel = "{}/{}".format(machine_rel, _opf_check.LEASE_NAME)
    payload = _opf_emit.emit_checked({
        "schema": _opf_schema.SUPPORTED_SCHEMA,
        "holder": _upgrade_lease_holder(),
        "operation": "upgrade",
        "acquired_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }).encode("utf-8")
    pfd, name = journal._open_parent(root_fd, lease_rel)
    try:
        try:
            fd = os.open(name, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o644, dir_fd=pfd)
        except FileExistsError:
            raise _UpgradeError(_upgrade_lease_held_message(pfd, name, lease_rel))
        try:
            try:
                journal._write_all(fd, payload)
                os.fsync(fd)
            finally:
                os.close(fd)
            os.fsync(pfd)
        except BaseException as exc:
            # Any failure AFTER the O_EXCL create but BEFORE successful acquisition (a failed payload write,
            # fsync, or the durability fsync of the parent) LEAVES the lease in place: it is a leftover from
            # THIS failed upgrade, released through operator reconciliation exactly like a lease from a dead
            # run (spec 5.7), never removed here. A by-name unlink would be OWNERSHIP-BLIND: if a peer replaced
            # the lease in the failure window (A-create / B-replace / A-fail), the unlink would delete the
            # REPLACEMENT holder's lease, a never-seize violation (spec 5.7). This code never removes a lease
            # it cannot prove is still the one it created, so it leaves-and-reconciles rather than racing an
            # unlink. A KeyboardInterrupt/SystemExit propagates untouched (the lease is still left); an
            # ordinary failure is surfaced as a reconcilable _UpgradeError so the operator gets clear advice.
            if isinstance(exc, _UpgradeError) or not isinstance(exc, Exception):
                raise
            raise _UpgradeError(
                "upgrade lease acquisition failed after the lease {} was created ({}); the lease is LEFT in "
                "place as a leftover from this failed upgrade and is never seized (spec 5.7). If you have "
                "confirmed NO opf run is live, release the leftover lease as your own reconciliation step "
                "(the tool never removes it), then re-run opf upgrade.".format(lease_rel, exc)) from exc
    finally:
        os.close(pfd)
    return payload


def _upgrade_read_lease_payload(pfd, name):
    """Read the lease at (pfd, name) SAFELY for an ownership compare, reusing the round-2 R3 pattern:
    O_RDONLY|O_NOFOLLOW|O_NONBLOCK (a planted FIFO or other special file cannot BLOCK the open), then fstat
    the opened fd and, for any NON-REGULAR file, return None WITHOUT reading. Returns the file's raw bytes,
    or None when it is absent, non-regular, or unreadable. Bounded read (never trusts the on-disk size)."""
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=pfd)
    except OSError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        return os.read(fd, 65536)
    except OSError:
        return None
    finally:
        os.close(fd)


def _upgrade_lease_holder_of(raw):
    """Best-effort holder identity from raw lease bytes, for a DIAGNOSTIC message only (never raises, never
    load-bearing: the ownership decision is the full-payload byte compare in _upgrade_unlink_owned_lease)."""
    if raw is None:
        return "absent or a non-regular entry"
    import tomllib
    try:
        data = tomllib.loads(raw.decode("utf-8"))
        if isinstance(data, dict) and data.get("holder") is not None:
            return "holder {!r}".format(data.get("holder"))
    except Exception:  # noqa: BLE001  diagnostic only; an unreadable replacement still refuses above
        pass
    return "an unreadable or malformed replacement lease"


def _upgrade_unlink_owned_lease(pfd, name, lease_rel, expected_payload):
    """The SINGLE ownership-verified lease-removal site (class-width never-seize, spec 5.7, OPF-SPEC.md:376
    "never seized from a live holder"). Unlink the lease at (pfd, name) ONLY when the file still there is
    byte-for-byte the exact lease THIS run created (`expected_payload`, the bytes _upgrade_acquire_lease
    returned; a full-payload proof that subsumes a holder-only compare). If it does NOT match -- a peer
    replaced the lease in the interval -- it is NEVER unlinked (never-seize) and the replacement is LEFT in
    place for operator reconciliation, surfaced as a fail-closed _UpgradeError naming the replacement's
    holder where legible. A failed unlink is likewise SURFACED, never swallowed (no-concealed-failure). No
    lease this run cannot prove is its own is ever removed here.

    DISCLOSED RESIDUAL (disclose-guard-residuals): a tiny TOCTOU window remains between the ownership read and
    the unlink -- a swap in exactly that window could still unlink a replacement, and because that unlink then
    succeeds the run also reports exit-0 SUCCESS (a false "released") over the deleted peer lease rather than
    the never-seize refusal. This is inherent to unlink-by-name (there is no unlink-this-exact-inode primitive
    available here) and cannot be eliminated, only disclosed; it is reachable ONLY when an operator or peer
    violates the documented release-only-when-no-run-is-live reconciliation (spec 5.7). It is vastly smaller
    than the prior ownership-blind unlink and never-seizes under any non-adversarial-mid-window sequence."""
    on_disk = _upgrade_read_lease_payload(pfd, name)
    if on_disk != expected_payload:
        # FIX3: distinguish a genuine ABSENCE (the lease was deleted, not replaced) from a REPLACEMENT (a
        # present-but-different payload). Both stay fail-closed / never-seize; only the operator-facing
        # wording differs. _upgrade_read_lease_payload returns None for absent, non-regular, or unreadable.
        if on_disk is None:
            detail = ("the lease is ABSENT at release (already removed, or present as a non-regular entry); "
                      "this run's own lease is gone")
        else:
            detail = ("the lease was REPLACED by another holder ({}) before release; this run's own lease "
                      "is gone".format(_upgrade_lease_holder_of(on_disk)))
        raise _UpgradeError(
            "the upgrade lease {} could not be released as this run's own: {}. It is NEVER seized (spec 5.7) "
            "and is LEFT in place for operator reconciliation, so no lease is removed here "
            "(fail-closed).".format(lease_rel, detail))
    try:
        os.unlink(name, dir_fd=pfd)
    except OSError as exc:
        raise _UpgradeError("could not release the upgrade lease {} ({}); surfaced, never swallowed "
                            "(fail-closed)".format(lease_rel, exc))


def _upgrade_release_lease(root_fd, machine_rel, expected_payload):
    """Release the lease created by _upgrade_acquire_lease: OWNERSHIP-VERIFIED unlink of machine_rel/
    lease.toml no-follow via the parent fd, then fsync the parent. The removal routes through the SINGLE
    ownership-verified site (_upgrade_unlink_owned_lease): the lease is removed ONLY when it is still THIS
    run's own (`expected_payload`), never seized from a peer that replaced it (spec 5.7); a replaced lease is
    LEFT and surfaced (exit 2). A failed unlink is SURFACED (exit 2), never swallowed (no-concealed-failure).
    A killed run leaves the lease, which is spec-conformant (present only while held; a leftover is released
    through operator reconciliation, spec 5.7) and is what the EEXIST refusal covers."""
    journal = _opf_store._journal
    lease_rel = "{}/{}".format(machine_rel, _opf_check.LEASE_NAME)
    pfd, name = journal._open_parent(root_fd, lease_rel)
    try:
        _upgrade_unlink_owned_lease(pfd, name, lease_rel, expected_payload)
        os.fsync(pfd)
    finally:
        os.close(pfd)


def _upgrade_run(root):
    import shlex
    import tomllib
    if _UPGRADE_TO != _opf_store.SUPPORTED_SPEC_VERSION:
        raise _UpgradeError("upgrade target {!r} does not match the tooling spec_version {!r}; refusing to "
                            "run a stale upgrade path (fail-closed)".format(
                                _UPGRADE_TO, _opf_store.SUPPORTED_SPEC_VERSION))
    try:
        # `opf upgrade` is the ONE caller that also accepts the retired 1.0.0 discovery token, so a legacy
        # [devprocess] store still resolves for migration (spec 9.2); every other tool keeps the sole
        # current-token discovery.
        res = _opf_store.resolve_store(
            Path(os.path.abspath(root)),
            accept_tokens=(_opf_store.STANDARD_TOKEN, _opf_store.PRIOR_STANDARD_TOKEN))
    except Exception as exc:  # noqa: BLE001  a resolver escape is cannot-evaluate, never a mutation
        raise _UpgradeError("unexpected error resolving the store at {!r} ({!r})".format(root, exc))
    if res.status == _opf_store.NOT_ADOPTED:
        print("opf upgrade: NOT APPLICABLE ({})".format(res.detail))
        return EXIT_OK
    if res.status != _opf_store.RESOLVED:
        print("opf upgrade: cannot evaluate: {}".format(res.detail), file=sys.stderr)
        return EXIT_MALFORMED

    machine_rel = res.machine_rel
    manifest_rel = "{}/{}".format(machine_rel, _opf_store.MANIFEST_NAME)
    counters_rel = "{}/{}".format(machine_rel, _opf_check.COUNTERS_NAME)
    root_fd = _opf_store._open_dir_nofollow(res.store_root)
    try:
        manifest_bytes = _upgrade_read_bytes(root_fd, manifest_rel, control=True)
        counters_bytes = _upgrade_read_bytes(root_fd, counters_rel)
        if manifest_bytes is None or counters_bytes is None:
            raise _UpgradeError("store manifest or counters is absent; not a resolvable store to upgrade")
        try:
            manifest_model = tomllib.loads(manifest_bytes.decode("utf-8"))
            counters_model = tomllib.loads(counters_bytes.decode("utf-8"))
        except (UnicodeError, tomllib.TOMLDecodeError) as exc:
            raise _UpgradeError("store manifest or counters does not parse as TOML ({})".format(exc))

        # spec_version triage: current is an idempotent byte no-op; above-tooling fails closed; only the
        # single known 1.0.0 origin proceeds. The base table is [opf] on an already-migrated store and the
        # retired [devprocess] on a legacy 1.0.0 store, so read whichever the manifest carries.
        _base_model = manifest_model.get(_opf_store.STANDARD_TOKEN)
        if not isinstance(_base_model, dict):
            _base_model = manifest_model.get(_opf_store.PRIOR_STANDARD_TOKEN)
        sv = _base_model.get("spec_version") if isinstance(_base_model, dict) else None
        if sv == _UPGRADE_TO:
            # F2: a store already at the target is a no-op ONLY when it is genuinely doctor-VALID at 1.1.0.
            # A partial or interrupted migration leaves spec_version == 1.1.0 with an incomplete delta;
            # trusting the version marker alone would report false success over a broken store. Re-validate
            # and fail closed on anything short of VALID, so a partial migration is never reported complete.
            result = _upgrade_doctor(root)
            if result.status == _opf_store.VALID:
                print("opf upgrade: store is already at spec_version {} and doctor-VALID; nothing to "
                      "upgrade (no-op).".format(_UPGRADE_TO))
                return EXIT_OK
            print("opf upgrade: store declares spec_version {} but is NOT doctor-VALID: a partial or "
                  "interrupted migration is never reported complete (fail-closed, spec 9.2). {} Run "
                  "`opf doctor --root {}` for the findings, exit 2.".format(
                      _UPGRADE_TO, _upgrade_partial_recovery_text(res.store_root), root), file=sys.stderr)
            _doctor_report(result)
            return EXIT_MALFORMED
        try:
            sv_tuple = tuple(int(p) for p in sv.split(".")) if isinstance(sv, str) else None
        except ValueError:
            sv_tuple = None
        if sv_tuple is not None and sv_tuple > tuple(int(p) for p in _UPGRADE_TO.split(".")):
            raise _UpgradeError("store declares spec_version {!r} ABOVE the {} this tooling implements; "
                                "a newer store is never downgraded (fail-closed)".format(sv, _UPGRADE_TO))

        # PRECONDITION (spec 9.2): re-emitting the UNCHANGED parsed model reproduces the on-disk bytes
        # exactly, proving the file is canonical and comment-free so the bounded rewrite loses nothing.
        if _opf_emit.emit_checked(manifest_model).encode("utf-8") != manifest_bytes:
            raise _UpgradeError("manifest is not in canonical new-document form (hand-edited or comment-"
                                "bearing); refusing a model rewrite that could lose content (fail-closed)")
        if _opf_emit.emit_checked(counters_model).encode("utf-8") != counters_bytes:
            raise _UpgradeError("counters.toml is not in canonical new-document form; refusing (fail-closed)")

        new_manifest, new_counters, added_ns, origin = _upgrade_plan(manifest_model, counters_model)
        new_manifest_bytes = _opf_emit.emit_checked(new_manifest).encode("utf-8")
        new_counters_bytes = _opf_emit.emit_checked(new_counters).encode("utf-8")

        # STEP 3 (M3): the LAST read-only gate. Prove HEAD is a verified restore path over exactly the
        # blast radius before any write; a dirty store refuses fail-closed with commit-your-changes advice,
        # never a restore (the dirt is the owner's work). The lease path is excluded (step 4's own refusal).
        _upgrade_check_clean(res, manifest_model)

        product_targets = _upgrade_product_render_targets(manifest_model)
        # DISTINCT roots for the recovery/staging advice (R1): `.working` lives under the STORE root, product-
        # scope targets under the PRODUCT root; the two differ for a RELOCATED store.
        recovery_store_root = res.store_root
        recovery_product_root = res.product_root if res.product_root is not None else res.store_root
        # STEP 4 (M4): claim the single-writer lease atomically, then hold it across mutation, render, and
        # the final doctor; release it in the finally covering every exit after acquisition, EXCEPT the
        # success path releases FIRST (R5) so no success is reported over a still-held / failed-to-release
        # lease. `released` records that the success path already released, so the finally does not re-release.
        lease_payload = _upgrade_acquire_lease(root_fd, machine_rel)
        released = False
        try:
            # Apply: rewrite manifest + counters (canonical bytes), create the missing empty indexes.
            _upgrade_replace(root_fd, manifest_rel, new_manifest_bytes)
            _upgrade_replace(root_fd, counters_rel, new_counters_bytes)
            empty_index = _opf_emit.emit_checked(
                {"schema": _opf_schema.SUPPORTED_SCHEMA, "record": []}).encode("utf-8")
            created_indexes = []
            created_relpaths = []
            for tname in _UPGRADE_NEW_TYPES:
                idx_rel = "{}/{}{}".format(machine_rel, tname, _opf_check.INDEX_SUFFIX)
                if _upgrade_create_index(root_fd, idx_rel, empty_index):
                    created_indexes.append(tname)
                    created_relpaths.append(idx_rel)
            # The two NET-NEW view targets are created-untracked this run (their store-relative destinations,
            # for the enumerated recovery); the re-rendered pre-existing views live under the same `.working`
            # subtree the scoped restore covers.
            for vname in _UPGRADE_NEW_VIEWS:
                _scope, _relpath = _opf_views._spec_destination(vname)
                if _scope == "store":
                    created_relpaths.append(_relpath)

            # Render the declared views (materializes the two new views and re-renders DECISIONS.md), then
            # require a full doctor VALID before offering the staged change. Both run over the mutated
            # (uncommitted) tree, under the held lease (containment-clean; the mid-run doctor stays VALID).
            render_argv = ["--root", root, "--write"]
            try:
                rres = _opf_store.resolve_store(Path(os.path.abspath(root)))
                robs, _notes = _opf_observe.gather(rres) if rres.status == _opf_store.RESOLVED else (None, [])
                rc = _opf_views.render(render_argv, observations=robs)
            except Exception as exc:  # noqa: BLE001  a render escape must not read as a clean upgrade
                raise _UpgradeError("view render after the schema delta failed ({!r}); the staged change is "
                                    "left for review".format(exc))
            if rc != EXIT_OK:
                print("opf upgrade: cannot evaluate: view render after the schema delta did not complete "
                      "cleanly (rc={}); exit 2.".format(rc), file=sys.stderr)
                print(_upgrade_recovery_text(recovery_store_root, recovery_product_root, created_relpaths,
                                             product_targets), file=sys.stderr)
                return EXIT_MALFORMED

            result = _upgrade_doctor(root)
            if result.status != _opf_store.VALID:
                print("opf upgrade: the upgraded store is NOT doctor-VALID; refusing to offer the change "
                      "(fail-closed, spec 9.2). Run `opf doctor --root {}` for the findings, exit 2.".format(
                          root), file=sys.stderr)
                print(_upgrade_recovery_text(recovery_store_root, recovery_product_root, created_relpaths,
                                             product_targets), file=sys.stderr)
                _doctor_report(result)
                return EXIT_MALFORMED

            # R5: release the single-writer lease BEFORE emitting the success report. A release failure
            # surfaces as exit 2 (never swallowed), and success is never printed over a still-held lease.
            # `released` is set FIRST so the finally never double-releases (a failed release legitimately
            # leaves the lease as a spec-conformant leftover for operator reconciliation).
            released = True
            _upgrade_release_lease(root_fd, machine_rel, lease_payload)
            print("opf upgrade: store schema upgraded {} -> {} and doctor-VALID (staged, NOT committed)."
                  .format(_UPGRADE_FROM, _UPGRADE_TO))
            print(json.dumps({
                "event": "upgraded", "root": str(root), "from": _UPGRADE_FROM, "to": _UPGRADE_TO,
                "created_indexes": sorted(created_indexes), "added_counters": sorted(added_ns),
                "pre_declared_types": sorted(origin["pre_declared"]),
                "decisions_view": "widened" if origin["decisions_declared"] else "not-declared"},
                sort_keys=True))
            print("opf upgrade: review the staged changes, then stage and commit them (scope the add to the "
                  "store subtree, never `add -A`, which would sweep in unrelated product work):")
            print("  git -C {} --literal-pathspecs add -- {}".format(
                shlex.quote(str(recovery_store_root)), shlex.quote(_opf_store.WORKING_DIRNAME)))
            for _pt in product_targets:
                print("  git -C {} --literal-pathspecs add -- {}".format(
                    shlex.quote(str(recovery_product_root)), shlex.quote(_pt)))
            print("opf upgrade: exit 0 means the store is valid at {}; committing is the adopter's own "
                  "step.".format(_UPGRADE_TO))
            return EXIT_OK
        finally:
            if not released:
                # R5/FIX1: release the lease on every non-success exit. When a mid-run failure is ALREADY
                # propagating (a render/doctor escape after the manifest+counters were rewritten, which
                # carries its own "staged change is left for review" recovery advice) and the release then
                # ALSO fails (its lease-replaced never-seize _UpgradeError), the release error must NOT
                # DISPLACE that original exception: the operator still needs the mid-run recovery advice, so
                # the lease-replaced note is surfaced ALONGSIDE it, never in place of it (exit 2 preserved,
                # peer lease left, never seized). A propagating KeyboardInterrupt/SystemExit is likewise not
                # masked by a release failure.
                pending = sys.exc_info()[1]
                if pending is None:
                    # A `return` (or normal fall-through) is passing through with no in-flight exception: a
                    # release failure legitimately becomes the surfaced outcome (exit 2), exactly as before.
                    _upgrade_release_lease(root_fd, machine_rel, lease_payload)
                else:
                    try:
                        _upgrade_release_lease(root_fd, machine_rel, lease_payload)
                    except (KeyboardInterrupt, SystemExit):
                        raise
                    except Exception as rel_exc:  # noqa: BLE001  surfaced, never displaces the original
                        print("opf upgrade: additionally, releasing the upgrade lease failed ({}); the peer "
                              "lease is LEFT in place (never seized, spec 5.7) and the original failure above "
                              "still governs (exit 2).".format(rel_exc), file=sys.stderr)
                        # returning from the except lets `pending` resume propagating (the finally completes
                        # without raising a new exception), so _cmd_upgrade surfaces the original refusal.
    finally:
        os.close(root_fd)


def _cli_self_test():
    """Guard the dispatcher's render, doctor, and source-only init routes.
    Render/doctor cases below judge return codes; init also checks payload validation, refusal reasons,
    and preservation through check_opf_init._suite(main). Each case captures stdout and stderr.
    Cases: an unknown verb, no args, and every not-yet-wired KNOWN_VERB fail closed (exit 2); a bare `render`,
    both flags together, an unrecognized render flag, a `--root` with no value, and an empty `--root` are usage
    errors (exit 2); `render --check` and `render --write` FORWARD to the U4 engine -- a NOT-ADOPTED root
    returns 0 for each (the wiring discriminator: reverting the render wiring routes it to the fail-closed
    KNOWN_VERBS branch and returns 2, failing this case; render --write is never run against a mutating
    adopter store here, only NOT-ADOPTED / garbage synthetic roots) and a garbage store returns 2. For
    `doctor`: bad-flag / usage
    cases (a `--root` with no value, an unknown flag) fail closed (exit 2); a NOT-ADOPTED root returns 0 (the
    doctor wiring discriminator: reverting the doctor route routes `doctor` to the fail-closed KNOWN_VERBS
    branch and returns 2, failing this case); a garbage store returns 2. The render clean/drift 0/1
    discrimination rides check_opf_drift.py --self-test, and the doctor clean(0)/mutation(1) discrimination
    over a validate_store-VALID COMMITTED store rides check_opf_doctor.py --self-test, each driving the same
    wiring end to end. Returns 0 clean, 1 on a failure, 2 on a harness error.

    HARNESS fail-close (FIX 2): the fixture SETUP (tempfile.mkdtemp) and the fixture I/O (directory creation
    and writes) are the harness surface; an OSError from any of them is caught and returned as a located
    cannot-evaluate (exit 2), never allowed to escape uncaught (which Python would surface as exit 1). A final
    broad backstop routes any other residual error to exit 2 as well. Discriminating coverage injects an
    OSError at mkdtemp and at directory creation and asserts each routes to exit 2 (change-carries-check)."""
    import io
    import shutil
    import tempfile
    import contextlib

    try:
        failures = []

        def expect(argv, want):
            buf = io.StringIO()
            try:
                with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
                    got = main(list(argv))
            except BaseException as exc:                # a dispatcher crash is itself a failure
                failures.append("{!r} raised {!r}".format(argv, exc))
                return
            if got != want:
                failures.append("{!r} returned {!r} (expected {})".format(argv, got, want))

        # Routing cases that need no store on disk.
        expect([], EXIT_MALFORMED)
        expect(["frobnicate"], EXIT_MALFORMED)
        for verb in KNOWN_VERBS:
            if verb not in ("init", "render", "doctor", "upgrade"):
                expect([verb], EXIT_MALFORMED)          # a known but not-yet-wired verb fails closed
        expect(["render"], EXIT_MALFORMED)              # bare: exactly one of --check/--write required
        expect(["render", "--check", "--write"], EXIT_MALFORMED)   # both flags refused
        expect(["render", "--bogus"], EXIT_MALFORMED)   # unknown render flag
        expect(["render", "--root"], EXIT_MALFORMED)    # --root needs a value
        expect(["render", "--check", "--root", ""], EXIT_MALFORMED)   # empty root refused

        # doctor verb ROUTING (PR-B), judged on exit code only. Bad-flag / usage cases need no store on disk.
        expect(["doctor", "--root"], EXIT_MALFORMED)    # --root needs a value
        expect(["doctor", "--check", "--root", ""], EXIT_MALFORMED)   # unknown doctor flag (and empty root)
        expect(["doctor", "--bogus"], EXIT_MALFORMED)   # unknown doctor flag

        def _fixture_leg():
            """Build the on-disk fixtures and drive render --check over them. Assertion outcomes are recorded
            in `failures`; returns None on success or EXIT_MALFORMED on a HARNESS error. The tempdir creation
            and every fixture directory/file write are the harness surface: an OSError from any of them is a
            located cannot-evaluate (exit 2), never an uncaught escape that Python would surface as exit 1."""
            try:
                base = tempfile.mkdtemp(prefix="opf-cli-selftest-")
            except OSError as exc:
                print("opf cli self-test: harness error: could not create the fixture tempdir ({})".format(
                    exc), file=sys.stderr)
                return EXIT_MALFORMED
            try:
                try:
                    # A NOT-ADOPTED root (no .working/): render --check FORWARDS to the U4 engine and returns
                    # 0 (NOT APPLICABLE) -- the wiring discriminator (an unwired render verb returns 2 here).
                    not_adopted = os.path.join(base, "not-adopted")
                    os.mkdir(not_adopted)
                    # A garbage store (a discovered but unparseable manifest): render --check fails closed
                    # (exit 2).
                    broken = os.path.join(base, "broken")
                    os.makedirs(os.path.join(broken, ".working", "toml"))
                    with open(os.path.join(broken, ".working", "toml", "manifest.toml"),
                              "w", encoding="utf-8") as fh:
                        fh.write("this is not valid toml {{{\n")
                except OSError as exc:
                    print("opf cli self-test: harness error: could not build a fixture store ({})".format(
                        exc), file=sys.stderr)
                    return EXIT_MALFORMED
                expect(["render", "--check", "--root", not_adopted], EXIT_OK)
                expect(["render", "--check", "--root", broken], EXIT_MALFORMED)
                # render --write over synthetic roots ONLY (never a mutating adopter store, per the no-live-
                # write-in-CI posture): a NOT-ADOPTED root is NOT APPLICABLE and returns 0 writing nothing
                # (the write-wiring discriminator; an unwired --write routes to the fail-closed KNOWN_VERBS
                # branch and returns 2), and a garbage store fails closed (exit 2: gather + the U6 gate
                # refuse). The VALID/INVALID gate discrimination over a whole-store-valid fixture with
                # injected observations rides _opf_views.self_test end to end.
                expect(["render", "--write", "--root", not_adopted], EXIT_OK)
                expect(["render", "--write", "--root", broken], EXIT_MALFORMED)
                # doctor over the same synthetic roots: a NOT-ADOPTED root reports NOT APPLICABLE and returns
                # 0 -- the wiring discriminator (reverting the doctor route sends `doctor` to the fail-closed
                # KNOWN_VERBS branch, which returns 2 here, failing this case); a garbage store fails closed
                # (exit 2). The clean(0)/mutation(1) discrimination over a validate_store-VALID COMMITTED store
                # rides check_opf_doctor.py --self-test end to end (a committed HEAD is needed for the prior
                # observation), mirroring how the render clean/drift 0/1 rides check_opf_drift.py --self-test.
                expect(["doctor", "--root", not_adopted], EXIT_OK)
                expect(["doctor", "--root", broken], EXIT_MALFORMED)
                # upgrade over the same synthetic roots: a NOT-ADOPTED root reports NOT APPLICABLE and
                # returns 0 -- the wiring discriminator (reverting the upgrade route sends `upgrade` to the
                # fail-closed KNOWN_VERBS branch, which returns 2 here, failing this case); a garbage store
                # fails closed (exit 2). The full 1.0.0 -> 1.1.0 migration discrimination (schema delta,
                # canonical-bytes precondition, doctor-VALID gate, idempotence, and the above-tooling refusal)
                # rides check_opf_upgrade.py --self-test end to end over a byte-pinned committed 1.0.0 store.
                expect(["upgrade", "--root", not_adopted], EXIT_OK)
                expect(["upgrade", "--root", broken], EXIT_MALFORMED)
            finally:
                shutil.rmtree(base, ignore_errors=True)
            return None

        harness_rc = _fixture_leg()
        if harness_rc is not None:
            return harness_rc

        # Discriminating harness-path coverage (FIX 2): an injected OSError at fixture SETUP (mkdtemp) and at
        # fixture I/O (directory creation) must each route to the located cannot-evaluate (exit 2), never
        # escape uncaught (which Python surfaces as exit 1). Judged on the returned code only; each probe
        # restores the patched callable in a finally so no later leg runs under the injection.
        def _refuse(*_a, **_k):
            raise OSError("simulated harness I/O refusal")

        def _expect_harness(label, obj, attr):
            real = getattr(obj, attr)
            setattr(obj, attr, _refuse)
            try:
                with contextlib.redirect_stderr(io.StringIO()):
                    rc = _fixture_leg()
            finally:
                setattr(obj, attr, real)
            if rc != EXIT_MALFORMED:
                failures.append("harness {}: _fixture_leg returned {!r} (expected {})".format(
                    label, rc, EXIT_MALFORMED))

        _expect_harness("mkdtemp-oserror", tempfile, "mkdtemp")
        _expect_harness("makedirs-oserror", os, "makedirs")

        # The same fixture vectors drive this in-process dispatcher and the dedicated gate's
        # isolated child. A refusal must name its reason and preserve the fixture, not merely return 2.
        import check_opf_init
        init_rc = check_opf_init._suite(main)
        if init_rc == EXIT_MALFORMED:
            return EXIT_MALFORMED
        if init_rc != EXIT_OK:
            failures.append("source-only init fixture vectors failed")

        if failures:
            for f in failures:
                print("opf cli self-test: FAIL: {}".format(f), file=sys.stderr)
            return EXIT_FINDING
        print("opf cli self-test: PASS (verb routing: unknown/unwired verbs and render/doctor usage errors "
              "fail closed; render --check forwards to the U4 engine; doctor resolves + validates a store, "
              "NOT-ADOPTED -> 0 and a garbage store -> 2; fixture-setup and fixture-I/O OSError fail closed "
              "to exit 2)")
        return EXIT_OK
    except Exception as exc:  # noqa: BLE001  final fail-closed backstop, never an uncaught exit-1 escape
        print("opf cli self-test: harness error: unexpected error ({!r}); failing closed to exit 2".format(
            exc), file=sys.stderr)
        return EXIT_MALFORMED


# Registered helper self-tests, run by `opf.py --self-test`. Each is (label, callable) returning a
# 0/1/2 exit code (0 clean, 1 finding, 2 cannot-evaluate). Later units append their own helper here.
# Built by a function rather than a module-level tuple because the _opf_* helpers it references are bound by
# _bootstrap() inside main(), not at module import; it is called after _bootstrap() has run.
def _self_tests():
    """Return the registered (label, callable) helper self-tests run by `opf.py --self-test`. Called after
    _bootstrap() has bound the _opf_* helpers, so every referenced helper is present."""
    return (
    ("opf-store", _opf_store.self_test),
    ("opf-schema", _opf_schema.self_test),
    ("opf-release", _opf_release.self_test),
    ("opf-changelog", _opf_changelog.self_test),
    ("opf-emit", _opf_emit.self_test),
    ("opf-views", _opf_views.self_test),
    ("opf-import", _opf_import.self_test),
    ("opf-observe", _opf_observe.self_test),
    ("opf-fuzz", _opf_fuzz.self_test),
    ("opf-check", _opf_check.self_test),
    ("opf-watchdog-hostile-ambient", _watchdog_hostile_ambient_self_test),
    ("opf-watchdog-wrapper-deadline", _watchdog_wrapper_caller_deadline_self_test),
    ("opf-watchdog-shared-restore", _watchdog_shared_restore_self_test),
    ("opf-watchdog-shared-restore-deadline", _watchdog_shared_restore_deadline_self_test),
    ("opf-aggregator", _aggregator_self_test),
    ("opf-cli", _cli_self_test),
)

# The spec's command vocabulary (spec 1). Each lands in its own unit; until then a verb fails closed.
KNOWN_VERBS = ("init", "import", "doctor", "render", "migrate", "sync", "upgrade")


# Helper self-tests that pin sys.set_int_max_str_digits(4300) inside a fixture and MUST restore the ambient
# value in a finally (the round-7 int-limit hermeticity work). run_self_tests guards that RESTORE half below.
_INT_LIMIT_SELF_TESTS = frozenset({"opf-release", "opf-emit", "opf-schema", "opf-fuzz", "opf-import"})


def run_self_tests(tests=None):
    """Run every registered helper self-test in order, forwarding each result. The aggregate exit code
    is the WORST outcome (2 cannot-evaluate > 1 finding > 0 clean): one degraded or failing helper fails
    the whole leg, never masked by a later clean one. With no explicit `tests`, the registered set is built
    by _self_tests() at call time (after _bootstrap() has bound the _opf_* helpers), never a module-level
    default that would need those helpers imported at module top.

    Int-limit hermeticity guard (finding 8-4): each helper in _INT_LIMIT_SELF_TESTS pins the int-string
    conversion limit to 4300 inside its fixtures and must RESTORE the ambient value afterward. That restore
    had no fails-if-reverted check: under the DEFAULT ambient (already 4300) a dropped restore leaves 4300
    and is invisible. So around each such helper we set a distinct SENTINEL limit (!= 4300 and != the real
    ambient) and, after it runs, require the limit to STILL be that sentinel before restoring the real
    ambient; a dropped restore in any of those helpers leaves 4300 != sentinel and fails the leg closed.
    These helpers are hermetic w.r.t. the ambient int-limit by construction (they pin their own 4300), so
    running them under the sentinel is exactly the hostile-ambient contract they already satisfy."""
    if tests is None:
        tests = _self_tests()
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


def main(argv=None):
    # Guarded helper bootstrap FIRST: a broken or partial install (an unimportable/unreadable _opf_* helper)
    # maps to a located cannot-evaluate (exit 2), never an uncaught ImportError escaping as Python's default
    # exit 1 that a direct `opf render --check` would read as a false drift. Idempotent, so the repeated
    # main() calls in the CLI self-test cost nothing once the helpers are loaded.
    rc = _bootstrap()
    if rc != EXIT_OK:
        return rc
    args = list(sys.argv[1:] if argv is None else argv)
    if args == ["--self-test"]:
        return run_self_tests()
    if not args or args[0] in ("-h", "--help"):
        print(__doc__, file=sys.stderr)
        return EXIT_MALFORMED
    verb = args[0]
    rest = args[1:]
    if verb == "render":
        return _cmd_render(rest)
    if verb == "doctor":
        return _cmd_doctor(rest)
    if verb == "init":
        return _cmd_init(rest)
    if verb == "upgrade":
        return _cmd_upgrade(rest)
    if verb in KNOWN_VERBS:
        # A recognized verb whose unit has not landed: fail closed (exit 2), never a silent success, so
        # a stub is never mistaken for a completed operation.
        print("opf {}: not yet implemented in this build (fail-closed)".format(verb), file=sys.stderr)
        return EXIT_MALFORMED
    print("opf: unknown verb {!r}; known verbs: {}".format(verb, ", ".join(KNOWN_VERBS)), file=sys.stderr)
    return EXIT_MALFORMED


if __name__ == "__main__":
    sys.exit(main())
