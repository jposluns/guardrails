#!/usr/bin/env python3
"""opf: the OPFiles (OPF) reference-tooling dispatcher (OPF core-tooling, skeleton from U1).

  opf.py --self-test                run every registered OPF helper self-test (the CI leg)
  opf.py <verb> [--root DIR] ...    a store verb (default --root: the cwd product repository root)

This is the dispatcher the OPF core-tooling units grow into. U1 lands it with the store-side helper
self-test wired in; store verbs that have not yet landed are recognized names that report
NOT-YET-IMPLEMENTED and fail closed (exit 2) until their unit lands, so a stub can never read as a
passing operation. `render` HAS landed (PR-A): the `opf render` CLI requires exactly one of
`--check | --write` (a bare `render` is a usage error, exit 2); `--check` is the read-only drift check
(forwarding to the U4 engine) and `--write` (VC-4/PR-C) is the mutating half: it gathers the inert git-derived
observations caller-side (_opf_observe.gather) and hands them to the U4 engine, which composes the EXISTING U6
`validate_store` store-integrity gate and permits writing when source integrity holds. Post-write validation
checks source integrity and regenerated outputs; other deliverable failures can remain. `doctor` HAS landed: `opf doctor
[--root DIR] [--require-store]` RESOLVES the store, gathers the inert git-derived observations
(_opf_observe.gather: tracked, actual_remote, prior), and runs the U6 `validate_store` store-integrity engine
over them, returning that engine's 0/1/2 contract (a NOT-ADOPTED root reports NOT APPLICABLE and exits 0;
with `--require-store`, the enforcement-pack CI floor, it is a cannot-evaluate and exits 2 instead, so a
repository whose store was removed cannot pass CI vacuously). Doctor is read-only; its
observation gather is the caller-side git seam validate_store itself never touches. `upgrade` HAS landed
(spec 9.2): `opf upgrade [--root DIR]` is the in-place, additive, idempotent
upgrade from 1.0.0 or 1.1.0 to 1.2.0. The 1.1.0 schema delta changes only spec_version; declared views
are then regenerated, so a stale committed view can change. The 1.0.0 path also applies the earlier
schema delta
(base-table and discovery-token rename, decision_support retirement, type and view declarations,
DECISIONS.md source widening, counters, and missing indexes). Neither creates init.toml provenance.
It refuses a store above the tooling spec. When migrating a 1.0.0 or 1.1.0 store, its preconditions
include readable, canonical manifest/counters matching a recognised origin shape, cleanliness over
planned schema/render destinations and index collision candidates (including ignored files there),
and acquisition of its lease. An untracked or ignored lease is excepted from the cleanliness check
and handled separately by lease acquisition. It then applies the schema delta,
renders declared views, and requires a full doctor VALID before offering the uncommitted change for
review and merge. A store already at the
tooling spec_version is a byte no-op when doctor-VALID and exits 2 otherwise; a NOT-ADOPTED root reports
NOT APPLICABLE and exits 0. `import`'s former
modes, `opf import [--root DIR] (--scan --set FILE | --plan --set FILE | --review <run-id> --actor NAME
(--decisions FILE | --interactive) | --apply <run-id>)`, are RETIRED (spec 14.1) and their engine is
removed: the verb refuses each mode on every root, a NOT-ADOPTED one included, at exit 2 with a pointer to
adoption and the prompt pack, before reading any input file or writing anything (only its argv usage checks
precede it).

`init` HAS landed: `opf init [--root DIR]` creates validated store sources, a pointer, and a starter
`CHANGELOG.md` when none exists, without git writes or rendering.
`absorb` HAS landed: `opf absorb [--root DIR] [--covers TOKEN] [--freeze-digest]`
prints a changelog draft or freeze digest without writing files.
`record` HAS landed (spec 8.8): `opf record create`, `transition`, `done-with-receipt`, and `worklog-append`
author one change (with its own worklog entry, and for done-with-receipt the one-to-one done receipt)
through one journaled publication, then render and require doctor VALID, leaving the change uncommitted.
The one exception to doctor VALID is a status change (transition or done-with-receipt): doctor may then
report only its cannot-evaluate for exactly that record and from/to pair, never a finding, and it keeps
reporting that cannot-evaluate until the change is committed.
`adopt` HAS landed (OPF-ADOPT K9a, the read-only half): `opf adopt plan --inputs FILE [--root DIR]`
freezes and PRINTS the inert adoption proposal through the adoption planner (_opf_adopt_plan), writing
nothing -- a VALID plan is a digest-bound PROPOSAL, never permission or readiness to apply (the approval
lives in the run's evidence, a later PR) -- and `opf adopt status [--root DIR]` reports the adoption
state read-only (the adoption evidence bundles and the adoption journal; with neither present it reports
that no adoption run exists). The mutating subcommands `approve`, `apply`, `complete`, and `reconcile`
are recognized and refuse (exit 2) until the mutating adoption engine lands in a later PR.

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
    global _opf_emit, _opf_views, _opf_fuzz, _opf_observe, _opf_absorb
    global _opf_write_guard, _opf_record, _opf_adopt_apply, _opf_adopt_plan
    try:
        import _opf_store       # U1: store resolution + discovery + manifest base/profile schema
        import _opf_schema      # U2: record envelope + baseline type schemas + status/transition + counters
        import _opf_release     # U3: version.toml + worklog.toml + span tiling + coverage digests + release cut
        import _opf_changelog   # U5: changelog range-coverage + freeze gates over version.toml + CHANGELOG.md
        import _opf_check       # U6: store-level integrity validator (validate_store; engine for opf doctor)
        import _opf_emit        # U8: the constrained-subset TOML emitter (canonical, byte-canon-clean)
        import _opf_views       # U4: deterministic view generators + the closed transform vocabulary
        import _opf_fuzz        # adversarial input-hardening proof (membership/type-guard class closure)
        import _opf_observe     # PR-B: caller-side git-derived observations for the doctor verb (validate_store)
        import _opf_absorb      # OPF-CHANGELOG-ABSORB: read-only CHANGELOG.md drafter (composes on U5)
        import _opf_write_guard  # the in-place writers' shared cleanliness gate and single-writer lease
        import _opf_record      # OPF-RECORD: the record-authoring verb (spec 8.8)
        import _opf_adopt_apply  # OPF-ADOPT U1: the apply shell (zero executable ops)
        import _opf_adopt_plan   # OPF-ADOPT K9a: read-only investigation + plan freeze (the adopt planner)
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


_REPLAY_CAP = 8192       # max CAPTURED characters re-emitted as failure evidence (head + tail halves)


def _replay_captured(label, text):
    """Re-emit captured inner-run output as failure evidence, BOUNDED and terminal-safe. At most
    _REPLAY_CAP characters of the CAPTURED text are re-emitted: when the capture is longer, the first and
    last _REPLAY_CAP//2 characters are kept, with an explicit elision marker between them naming how much
    was dropped, so the TAIL -- where a failing inner run prints its failure lines and summary -- survives
    the bound instead of being cut away with the head-only clip. The cap applies to the CAPTURED
    characters BEFORE escaping: every non-printable character except the newline, and every backslash, is
    escaped repr-style, so the emitted evidence is bounded by _REPLAY_CAP times the longest
    single-character escape (10 characters, a non-printable astral code point), PLUS up to one terminating
    newline per emitted chunk (appended only when the chunk does not already end in one; one chunk
    unclipped, two -- head and tail -- clipped) and the elision marker line; it is not bounded by
    _REPLAY_CAP itself.
    Escaping the backslash keeps the escapes unambiguous: literal backslash-x-1-b text in the capture no
    longer renders identically to an escaped ESC. A NESTED replay (a capture that itself contains replay
    output, as when the wrapper-deadline test re-emits the hostile test's already-escaped replay) escapes
    the inner replay's backslashes AGAIN, so an inner escape renders with a doubled backslash: still
    unambiguous at a known nesting depth, at a readability cost. Single-pass emission of inner replays is
    not attempted, because the capture does not mark which of its regions are already escaped. `label`
    prefixes the elision marker only; the (escaped)
    captured text itself is re-emitted unprefixed, as before."""
    def _esc(chunk):
        out = "".join(ch if ch == "\n" or (ch.isprintable() and ch != "\\")
                      else repr(ch)[1:-1] for ch in chunk)
        if not out.endswith("\n"):
            out += "\n"
        return out
    total = len(text)
    if total <= _REPLAY_CAP:
        sys.stderr.write(_esc(text))
        return
    _half = _REPLAY_CAP // 2
    sys.stderr.write(_esc(text[:_half]))
    print("{}: captured output truncated: showing the first and last {} of {} characters ({} "
          "elided)".format(label, _half, total, total - 2 * _half), file=sys.stderr)
    sys.stderr.write(_esc(text[-_half:]))


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
            # F3 (load-hardening, start side): the elapsed baseline is sampled BEFORE the fixture timer is
            # armed, so a scheduler preemption between the sample and the arm only WIDENS the accepted band
            # (conservative). The prior order (arm, then sample) excluded timer time consumed by a
            # preemption between arming and the sample from _elapsed_ub, so the band check redded under
            # machine load with the restore itself correct (the start-side twin of the end-side
            # read-ordering defect handled at the _elapsed_ub measurement below).
            _t_arm = _time.monotonic()                            # F3: elapsed baseline for the fixture-value bound
            _signal.setitimer(_signal.ITIMER_REAL, _FIX_VAL, _FIX_INT)
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
            _blocked_after = _signal.SIGALRM in _signal.pthread_sigmask(_signal.SIG_BLOCK, set())
            _disp_after = _signal.getsignal(_signal.SIGALRM)
            _val_after, _int_after = _signal.getitimer(_signal.ITIMER_REAL)
            # F3 (load-hardening, end side): the elapsed upper bound for the fixture-value band spans from
            # BEFORE the fixture timer was armed (_t_arm above) to AFTER the getitimer read, so it BRACKETS
            # everything the fixture timer can have consumed: the elapsed the watchdog subtracted (its
            # snapshot is at or after the arm) PLUS the restored timer's live countdown up to the read. The
            # prior end bound was measured BEFORE the read (at fn() return); a scheduler preemption between
            # that measurement and the read let the restored timer count below the bound, so the band check
            # redded under machine load with the restore itself correct (a test-hermeticity defect: the
            # verdict tracked ambient scheduling, not the code under test; fixed at the test).
            _elapsed_ub = _time.monotonic() - _t_arm
            _pending_after = _signal.SIGALRM in _signal.sigpending()   # F2: caller pending must survive
            if not _pending_ok:
                print("opf watchdog self-test: {}: setup did not leave SIGALRM pending".format(label),
                      file=sys.stderr)
                ok = False
            if _crashed is not None:
                print("opf watchdog self-test: {}: RAISED under blocked+pending SIGALRM ({}); an exception "
                      "escaped the helper under the hostile ambient (F2)".format(label, _crashed), file=sys.stderr)
                ok = False
            elif rc != EXIT_OK:
                print("opf watchdog self-test: {}: returned {!r} under the hostile ambient (expected "
                      "0)".format(label, rc), file=sys.stderr)
                ok = False
            if (_crashed is not None or rc != EXIT_OK) and _buf.getvalue():
                # The redirect above keeps a passing run quiet, but on a crash or nonzero return the
                # captured output IS the failure evidence; re-emit it rather than swallow it -- BOUNDED
                # (capped with an explicit truncation marker, control characters escaped), so a
                # misbehaving inner run cannot flood the log or rewrite the terminal.
                print("opf watchdog self-test: {}: captured output of the failing inner run "
                      "follows".format(label), file=sys.stderr)
                _replay_captured("opf watchdog self-test: {}".format(label), _buf.getvalue())
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
            # [_FIX_VAL - _elapsed_ub, _FIX_VAL]: the watchdog subtracts an elapsed >= 0, and the restored
            # timer keeps counting down until the getitimer read, so the total deficit at the read is at
            # most _elapsed_ub, which is measured AFTER that read and therefore brackets both parts. A
            # restored value below the band means the caller's value was lost or over-reduced; one above
            # _FIX_VAL means time was ADDED to the caller's deadline (an extended or re-armed-to-full
            # restore). A small float slack absorbs monotonic jitter. The interval must be restored exactly
            # to the fixture's _FIX_INT; a dropped interval-restoration leaves 0 and reds this (the mutant
            # a one-shot fixture hid).
            if not (_FIX_VAL - _elapsed_ub - 1e-3 <= _val_after <= _FIX_VAL + 1e-3):
                print("opf watchdog self-test: {}: did not restore the caller's ITIMER_REAL value "
                      "elapsed-aware (got {!r}, expected within [{:.6f}, {:.6f}]; elapsed upper bound "
                      "{:.6f}s measured after the read) (F2/F3)".format(
                          label, _val_after, _FIX_VAL - _elapsed_ub, _FIX_VAL, _elapsed_ub),
                      file=sys.stderr)
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
            # the single elapsed-aware save/restore every opf-side watchdog is meant to route through): value
            # minus the time held (interval preserved) so the 4 helper runs (one per affected label) neither
            # pause nor extend the caller's deadline (a 50ms deadline set before the test still fires, not
            # sitting ~50ms away after this multi-second test; a deadline that expired during the test clamps
            # to a tiny positive so it still fires, never re-armed to its full original value), AND a SIGALRM
            # the caller had pending on entry re-posted so the probe's SIG_IGN does not destroy it
            # (round-15 F2).
            _opf_store.restore_caller_alarm(*_caller_snap)
    if not ok:
        # The summary names no single cause: the failed check above already printed the observed one (a
        # setup that left no pending SIGALRM, an inner crash or nonzero return, or an unrestored mask,
        # disposition, pending state, value, or interval), and this line must not claim a cause a
        # different check found.
        print("opf watchdog hostile-ambient self-test: FAIL (a hostile-ambient check failed; the message "
              "above states the observed cause)", file=sys.stderr)
        return EXIT_FINDING
    print("opf watchdog hostile-ambient self-test: PASS (changelog/views/store/check watchdogs survive a "
          "blocked+pending SIGALRM with mask, disposition, and timer restored)")
    return EXIT_OK


def _watchdog_wrapper_caller_deadline_self_test():
    """Guard F2 (round-12, fix-induced): _watchdog_hostile_ambient_self_test must restore the CALLER's
    ITIMER_REAL with ELAPSED TIME SUBTRACTED, so running it neither PAUSES nor EXTENDS a caller's deadline
    (the pre-fix wrapper restored the caller value VERBATIM, so a short deadline was still its full value
    away after the multi-second test and never fired). This covers the WRAPPER's OWN caller-timer restore
    site, which the default self-test never exercises because it arms no caller deadline.

    Arm a short (0.5s) caller deadline with a firing-recorder handler, run the wrapper (which holds the
    caller timer across its four helper self-test runs; the guard below confirms the run outlasted the
    deadline), and assert two OBSERVATIONS: a SIGALRM was DELIVERED (the recorder saw it, during the run
    or the bounded grace wait), AND the caller's ITIMER_REAL read back BELOW HALF the deadline afterwards.
    With an elapsed-aware restore the deadline expires during the run and the timer reads ~0. A verbatim
    re-arm at the wrapper's own restore site reads back the full deadline minus only the time spent
    between that re-arm and the read, so it reds the below-half check unless scheduling stalls totalling
    more than half the deadline (0.25s) land in that short window: an executed observation with a wide
    margin, not a load-proof discriminator. Disclosed residual: detection of a hand-rolled (verbatim or
    otherwise divergent) or DROPPED restore at a call site is BEST-EFFORT through these behavioural
    checks; the miss direction is a long scheduling stall landing between a restore and the read that
    observes it, which shrinks a defective reading toward the elapsed-aware one. A structural call-site
    guard is tracked separately. The helper-module restore sites exercised inside
    the wrapper (_opf_changelog / _opf_views / _opf_store / _opf_check) hold the borrowed timer for well
    under a millisecond, so neither this below-half reading nor the hostile test's fixture band can
    tell a verbatim restore from an elapsed-aware one THERE; that limit is part of the same disclosed
    residual. (A verbatim re-arm still fires, but only after the run, inside the grace wait, so the
    delivery check alone cannot make the distinction.)
    Returns 0 clean, 1 on failure; SKIPS clean on a platform without POSIX SIGALRM/itimer.

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
    _deadline = 0.5                                              # a 0.5s caller deadline the wrapper's multi-second run outlasts (guarded below)
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
        try:
            with _ctx.redirect_stdout(_buf), _ctx.redirect_stderr(_buf):
                _rc = _watchdog_hostile_ambient_self_test()      # the wrapper under test
        except BaseException:
            if _buf.getvalue():                                  # a raise is failure evidence too
                print("opf watchdog wrapper-deadline self-test: captured output of the raising inner "
                      "wrapper follows", file=sys.stderr)
                _replay_captured("opf watchdog wrapper-deadline self-test", _buf.getvalue())
            raise
        _elapsed = _time.monotonic() - _t0
        _val_after, _ = _signal.getitimer(_signal.ITIMER_REAL)
        if _rc != EXIT_OK:
            print("opf watchdog wrapper-deadline self-test: the inner hostile-ambient wrapper returned {!r} "
                  "(expected 0)".format(_rc), file=sys.stderr)
            if _buf.getvalue():
                # The redirect keeps a passing run quiet, but on a nonzero inner return the captured
                # output IS the failure evidence; re-emit it rather than swallow it -- BOUNDED (capped
                # with an explicit truncation marker, control characters escaped), so a misbehaving
                # inner run cannot flood the log or rewrite the terminal.
                print("opf watchdog wrapper-deadline self-test: captured output of the failing inner "
                      "wrapper follows", file=sys.stderr)
                _replay_captured("opf watchdog wrapper-deadline self-test", _buf.getvalue())
            ok = False
        # The wrapper ran longer than the deadline (guarded here), so an elapsed-aware restore leaves
        # the caller timer EXPIRED: it fires during the run and getitimer reads ~0 afterwards. A verbatim
        # re-arm at the wrapper's OWN restore site reads back the full deadline minus only the time spent
        # between that re-arm and the read above, and can still fire LATE, inside the grace wait below, so
        # neither the firing check nor a below-full read can catch it; the below-half check further down
        # separates the two readings (see its comment for the margin that separation relies on).
        if _elapsed <= _deadline:
            print("opf watchdog wrapper-deadline self-test: the wrapper ran {:.4f}s, not longer than the {}s "
                  "deadline; the fixture cannot discriminate".format(_elapsed, _deadline), file=sys.stderr)
            ok = False
        # Bounded delivery-grace for the last iteration's clamped (1e-6) re-armed SIGALRM under load: a flaky-observation stabilization, not a correctness change (SIGALRM is unblocked here, so the pending timer delivers during the wait).
        _grace = _time.monotonic() + 2.0
        while not _fired and _time.monotonic() < _grace:
            _time.sleep(0.005)
        if not _fired:
            # State only the observation: no SIGALRM delivery was recorded within the run plus the grace
            # wait. WHICH defect (or other cause) suppressed delivery is not observed here; the
            # _val_after check below reports the restored value it actually read.
            print("opf watchdog wrapper-deadline self-test: the caller's {}s deadline never FIRED across a "
                  "{:.4f}s run plus the bounded delivery-grace wait; no SIGALRM delivery was observed "
                  "(F2)".format(_deadline, _elapsed), file=sys.stderr)
            ok = False
        # Require a REAL elapsed deduction, not merely a value below the armed one: the _elapsed >
        # _deadline guard above means an elapsed-aware restore reads ~0 here, while a verbatim re-arm at
        # the wrapper's own restore site reads the full deadline minus the time spent between that re-arm
        # and the read above. Half the deadline (0.25s) separates those readings unless scheduling stalls
        # totalling more than 0.25s land in that short window, so this is a wide-margin executed
        # observation, not a deterministic guard: a verbatim re-arm hidden by such a stall is the
        # disclosed best-effort miss; a structural call-site guard is tracked separately (F1).
        if _val_after >= _deadline / 2:
            print("opf watchdog wrapper-deadline self-test: the caller's ITIMER_REAL read {!r} after the "
                  "{:.4f}s run, not below half its {}s deadline; a restore that deducted the real elapsed "
                  "would read ~0 (F2)".format(_val_after, _elapsed, _deadline), file=sys.stderr)
            ok = False
    finally:
        _signal.setitimer(_signal.ITIMER_REAL, 0)                # disarm before restoring the caller state
        _signal.signal(_signal.SIGALRM, _signal.SIG_IGN)         # discard any still-pending SIGALRM
        _signal.signal(_signal.SIGALRM, _prev_disp)
        if _have_mask:
            _signal.pthread_sigmask(_signal.SIG_SETMASK, _prev_mask)   # restore the caller's exact mask
        _opf_store.restore_caller_alarm(*_caller_snap)          # shared elapsed-aware timer + pending restore (F1)
    if not ok:
        # The summary names no single cause: the failed check above already printed the observed one (an
        # inner-wrapper failure, a non-discriminating fixture, an unfired deadline, or an unsubtracted
        # elapsed), and this line must not claim a cause a different check found.
        print("opf watchdog wrapper-deadline self-test: FAIL (a wrapper-deadline check failed; the message "
              "above states the observed cause)", file=sys.stderr)
        return EXIT_FINDING
    print("opf watchdog wrapper-deadline self-test: PASS (a SIGALRM was delivered within the run plus "
          "the bounded grace wait, and the caller's ITIMER_REAL read {!r} afterwards, below half its {}s "
          "deadline)".format(_val_after, _deadline))
    return EXIT_OK


def _watchdog_shared_restore_self_test(_hold_s=0.0):
    """Guard F1 (round-15, break the watchdog-timer re-induction loop): every opf-side watchdog is meant to
    restore a borrowed caller ITIMER_REAL through the ONE shared _opf_store.restore_caller_alarm helper, the
    single source of truth, so a divergent restore has one body to diverge in rather than one per site. This
    exercises that HELPER directly (its own body, not who calls it): a caller
    timer restored after a KNOWN elapsed must come back reduced by that elapsed (elapsed-aware), its repeating
    interval preserved, never re-armed to its full original value. Reverting the helper to a verbatim restore
    (value re-armed to its original) reds this. A SECOND probe restores the same snapshot with was_pending
    True (under a blocked SIGALRM, so the helper's re-post PENDS rather than delivers), so a restore that
    goes verbatim exactly when an alarm was pending -- a branch the fixture-band tests admit and the direct
    probe above (was_pending False) never reaches -- reds here too, and the re-posted pending SIGALRM is
    asserted observable. SKIPS clean on a platform without POSIX SIGALRM/itimer.

    Margins, not determinism: the elapsed is a fixed baseline in the past (monotonic() - 5s), so the
    expected restored value is ~95s for a 100s caller value while a verbatim restore yields the full
    100s -- a ~5s separation, far above scheduling jitter, though still one a comparably long stall
    between the restore call and the getitimer read would erode. Each probe's accepted band is bracketed
    by a MEASURED probe-to-read elapsed (baseline sampled before the restore call, upper bound after the
    read, the hostile test's conservative ordering), so a scheduling stall in that window only WIDENS the
    band's floor and a correct restore stays green; the prior FIXED +-0.5s band redded a correct restore
    under a >0.5s stall there. Detection of a hand-rolled, dropped, or restore-time-t0 restore at a CALL
    SITE is best-effort through the behavioural watchdog tests (the miss direction is a long stall between
    a restore and the read that observes it); a structural call-site guard is tracked separately.

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
        # The accepted band is bracketed by a MEASURED probe-to-read elapsed (baseline sampled BEFORE the
        # restore call, upper bound AFTER the getitimer read, the hostile test's conservative ordering), so
        # a scheduling stall between the restore and the read only WIDENS the band's floor (the restored
        # timer counts down until the read; the bracket bounds that countdown plus any extra elapsed the
        # helper subtracted); the prior FIXED +-0.5s band redded a CORRECT restore under a >0.5s stall in
        # that window.
        _t_probe = _time.monotonic()
        _opf_store.restore_caller_alarm(_known_val, _known_int, _t_probe - _elapsed, False)
        _val_after, _int_after = _signal.getitimer(_signal.ITIMER_REAL)
        _probe_ub = _time.monotonic() - _t_probe                # brackets extra subtraction + countdown
        _signal.setitimer(_signal.ITIMER_REAL, 0)               # disarm the probe timer
        if not (_known_val - _elapsed - _probe_ub - 1e-3 <= _val_after <= _known_val - _elapsed + 1e-3):
            print("opf watchdog shared-restore self-test: restored ITIMER value {!r}; expected within "
                  "[{:.6f}, {:.6f}] (elapsed {}s subtracted from {}s, then at most the bracketed {:.6f}s "
                  "probe-to-read elapsed); a verbatim restore would leave ~{} (F1)".format(
                      _val_after, _known_val - _elapsed - _probe_ub, _known_val - _elapsed, _elapsed,
                      _known_val, _probe_ub, _known_val), file=sys.stderr)
            ok = False
        if abs(_int_after - _known_int) > 1e-6:
            print("opf watchdog shared-restore self-test: restored ITIMER interval {!r}; expected {!r} "
                  "(F1)".format(_int_after, _known_int), file=sys.stderr)
            ok = False
        # Pending-branch probe (fix round 2): exercise the helper's was_pending=True path DIRECTLY. A
        # restore defect that goes verbatim exactly when a SIGALRM was pending (elapsed-aware otherwise)
        # passes every fixture-band watchdog test -- the band's upper edge admits a verbatim restore, and
        # the probe above passes was_pending False -- so this probe is the red for that mutant. SIGALRM is
        # BLOCKED around the call so the helper's re-post PENDS rather than delivers (deterministic); a
        # SIGALRM the CALLER already had pending is discarded under SIG_IGN BEFORE the restore call, so
        # the pending assertion below can only be satisfied by the helper's OWN re-post (an inherited
        # caller pending SIGALRM would otherwise stand in for a missing re-post); the probe's re-posted
        # SIGALRM is discarded under SIG_IGN before the probe-local disposition and mask are restored.
        # Hermetic: a SIGALRM the CALLER had pending on entry is re-posted by the outer finally's
        # restore_caller_alarm(*_caller_snap), from the top-of-function snapshot.
        if (hasattr(_signal, "pthread_sigmask") and hasattr(_signal, "sigpending")
                and hasattr(_signal, "SIGALRM")):
            def _benign(_s, _f):                                  # a throwaway probe-local disposition
                pass
            _p_disp = _signal.getsignal(_signal.SIGALRM)
            _p_mask = _signal.pthread_sigmask(_signal.SIG_BLOCK, {_signal.SIGALRM})
            try:
                _signal.signal(_signal.SIGALRM, _signal.SIG_IGN)  # discard an INHERITED caller pending SIGALRM
                _signal.signal(_signal.SIGALRM, _benign)
                # The accepted band is bracketed by a measured probe-to-read elapsed, exactly as the
                # was_pending False probe above (a stall between the restore and the read widens the
                # band's floor instead of false-failing a correct restore).
                _t_probe_p = _time.monotonic()
                _opf_store.restore_caller_alarm(_known_val, _known_int, _t_probe_p - _elapsed, True)
                _val_p, _int_p = _signal.getitimer(_signal.ITIMER_REAL)
                _probe_ub_p = _time.monotonic() - _t_probe_p      # brackets extra subtraction + countdown
                _pend_p = _signal.SIGALRM in _signal.sigpending()
                _signal.setitimer(_signal.ITIMER_REAL, 0)         # disarm the probe timer
                if not (_known_val - _elapsed - _probe_ub_p - 1e-3 <= _val_p
                        <= _known_val - _elapsed + 1e-3):
                    print("opf watchdog shared-restore self-test: with was_pending True the restored "
                          "ITIMER value is {!r}; expected within [{:.6f}, {:.6f}] (elapsed {}s subtracted "
                          "from {}s, then at most the bracketed {:.6f}s probe-to-read elapsed); a "
                          "pending-only verbatim restore would leave ~{} (F1/F2)".format(
                              _val_p, _known_val - _elapsed - _probe_ub_p, _known_val - _elapsed,
                              _elapsed, _known_val, _probe_ub_p, _known_val),
                          file=sys.stderr)
                    ok = False
                if abs(_int_p - _known_int) > 1e-6:
                    print("opf watchdog shared-restore self-test: with was_pending True the restored "
                          "ITIMER interval is {!r}; expected {!r} (F1/F2)".format(_int_p, _known_int),
                          file=sys.stderr)
                    ok = False
                if not _pend_p:
                    # State only the observation: no pending SIGALRM after the restore call. Whether the
                    # helper never re-posted it, or re-posted it and something else consumed it, is not
                    # observed here.
                    print("opf watchdog shared-restore self-test: with was_pending True no pending "
                          "SIGALRM was observed after the restore (F2)", file=sys.stderr)
                    ok = False
            finally:
                _signal.setitimer(_signal.ITIMER_REAL, 0)
                _signal.signal(_signal.SIGALRM, _signal.SIG_IGN)  # discard the probe's re-posted SIGALRM
                _signal.signal(_signal.SIGALRM, _p_disp)
                _signal.pthread_sigmask(_signal.SIG_SETMASK, _p_mask)
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
        # The summary names no single cause: the failed check above already printed the observed one (a
        # value or interval outside the elapsed-aware band, or a missing pending re-post), and this line
        # must not claim a cause a different check found.
        print("opf watchdog shared-restore self-test: FAIL (a shared-restore check failed; the message "
              "above states the observed cause)", file=sys.stderr)
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
    the restored ITIMER value, so a restore-time-t0 / verbatim revert (which subtracts ~0) reds (see the
    margin note below for the stall this can miss). SKIPS clean without POSIX itimer.

    F-R18-C2TEST: the discrimination is driven by an explicit `_hold_s` the wrapped call sleeps while it
    holds the borrowed caller timer, NOT by the wrapped call's own (microsecond) wall time. The prior form
    measured the wrapped call's `_dur` and asserted `_val_after <= _armed - _dur*0.5`; because `_dur` was
    sub-millisecond, that band was measurement noise and a restore-time-t0 / verbatim revert stayed green.
    Here the held interval is a controlled ~0.2s, well above scheduling jitter, and the assertion requires
    that at least half of it was deducted -- the exact PARENT-FUNCTION revert (restore-time-t0 / verbatim)
    deducts ~0 and reds, unless a scheduling stall longer than half the hold (~0.1s) lands between the
    wrapped test's final restore and the read here: a wide-margin executed observation, best-effort at the
    call site, not a deterministic guard (a structural call-site guard is tracked separately).

    Fix round 4: the assertion is a BAND, not a one-sided ceiling. The prior form required only
    `_val_after <= _armed - _hold_s*0.5`, which a DROPPED caller timer also satisfies: with the wrapped
    test's final restore removed, ITIMER_REAL is left disarmed and getitimer reads 0, well under the
    ceiling. The band's floor is _armed minus the bracketed arm-to-read elapsed (measured AFTER the
    getitimer read, so it bounds everything the caller timer can legitimately have lost: the elapsed the
    restore subtracted plus the restored timer's countdown up to the read); a reading below the floor is
    one no elapsed-aware restore of the armed timer can produce, so a dropped or over-reduced restore
    reds."""
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
        # Fix round 4: the floor baseline is sampled BEFORE the arm (and the elapsed upper bound AFTER the
        # read below), so a preemption anywhere in between only WIDENS the accepted band (conservative).
        _t_arm = _time.monotonic()
        _signal.setitimer(_signal.ITIMER_REAL, _armed, 0.0)
        _buf = _io.StringIO()
        with _ctx.redirect_stdout(_buf), _ctx.redirect_stderr(_buf):
            _rc = _watchdog_shared_restore_self_test(_hold_s=_hold_s)
        _val_after, _ = _signal.getitimer(_signal.ITIMER_REAL)
        _elapsed_ub = _time.monotonic() - _t_arm                # brackets the subtracted elapsed + countdown
        if _rc != EXIT_OK:
            print("opf watchdog shared-restore deadline self-test: inner self-test returned {!r} (expected "
                  "0)".format(_rc), file=sys.stderr)
            ok = False
        # An elapsed-aware restore subtracts ~the whole held interval (>= _hold_s); a restore-time-t0 /
        # verbatim revert subtracts ~0 and leaves the deadline near its full armed value. Require at least
        # half the controlled hold to have been deducted, a ceiling well clear of scheduling jitter.
        if not (_val_after <= _armed - _hold_s * 0.5):
            print("opf watchdog shared-restore deadline self-test: caller ITIMER read {!r} after a "
                  "controlled {:.3f}s hold; that elapsed was not subtracted (a restore-time-t0 / verbatim "
                  "restore extends a caller deadline, F-R17-C2 / F-R18-C2TEST)".format(_val_after, _hold_s),
                  file=sys.stderr)
            ok = False
        # Fix round 4, the band's FLOOR: the deduction can be at most the bracketed arm-to-read elapsed,
        # so a reading below _armed - _elapsed_ub (minus a small float slack) is one no elapsed-aware
        # restore of the armed timer can produce. A DROPPED restore (the wrapped test's final
        # restore_caller_alarm removed) leaves ITIMER_REAL disarmed, reads 0.0, and reds here; the
        # one-sided ceiling above accepts that reading.
        if not (_armed - _elapsed_ub - 1e-3 <= _val_after):
            print("opf watchdog shared-restore deadline self-test: caller ITIMER read {!r} after the "
                  "wrapped run, below the elapsed-aware floor {:.6f} (armed {} minus the bracketed "
                  "{:.6f}s arm-to-read elapsed); no elapsed-aware restore of the armed timer can produce "
                  "that reading (F1)".format(_val_after, _armed - _elapsed_ub, _armed, _elapsed_ub),
                  file=sys.stderr)
            ok = False
    finally:
        _signal.setitimer(_signal.ITIMER_REAL, 0)
        _signal.signal(_signal.SIGALRM, _prev_disp)
        _opf_store.restore_caller_alarm(*_caller_snap)
    if not ok:
        print("opf watchdog shared-restore deadline self-test: FAIL", file=sys.stderr)
        return EXIT_FINDING
    print("opf watchdog shared-restore deadline self-test: PASS (the caller ITIMER_REAL read back inside "
          "the elapsed-aware band: reduced by at least half the controlled {:.1f}s hold and by no more "
          "than the bracketed arm-to-read elapsed)".format(_hold_s))
    return EXIT_OK


def _cmd_render(rest):
    """`opf render [--root DIR] (--check | --write)`: the store render verb.

    The READ-ONLY `--check` half forwards to the U4 engine `_opf_views.render`, whose 0/1/2 contract is
    exactly the required one (0 clean, 1 drift, 2 cannot-evaluate; a NOT-ADOPTED root reports NOT APPLICABLE
    and exits 0, the pack's own `--root .` case). The mutating `--write` half (VC-4/PR-C) gathers the inert
    git-derived observations caller-side (_opf_observe.gather over the RESOLVED store, exactly as doctor does)
    and hands them to the same engine, which composes the store-integrity gate and permits the write only
    when source integrity is sound, printing the findings/cannot-evaluates and returning 2 (writing nothing)
    otherwise, so
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


def _cmd_absorb(rest):
    """`opf absorb [--root DIR] [--covers TOKEN] [--freeze-digest]`: the read-only CHANGELOG.md drafter.

    Forwards to the OPF-CHANGELOG-ABSORB engine `_opf_absorb.run`, whose 0/2 contract is exactly the
    required one (0 draft or NOT APPLICABLE, 2 cannot-evaluate; a NOT-ADOPTED root reports NOT APPLICABLE and
    exits 0, the pack's own `--root .` case). The verb is READ-ONLY: the draft (or, with `--freeze-digest`,
    the freeze digest of the curated entry) goes to stdout and the framing to stderr, and the engine writes
    nothing, so there is no stage-then-promote surface. The parser is the house fail-closed idiom (unknown
    token, an empty or option-looking or duplicate --root/--covers value -> exit 2), matching _cmd_render."""
    root = None
    covers = None
    freeze = False
    i = 0
    while i < len(rest):
        tok = rest[i]
        if tok == "--freeze-digest":
            freeze = True
            i += 1
        elif tok == "--root":
            if i + 1 >= len(rest):
                print("opf absorb: --root requires a directory argument", file=sys.stderr)
                return EXIT_MALFORMED
            if root is not None:
                print("opf absorb: --root given more than once", file=sys.stderr)
                return EXIT_MALFORMED
            val = rest[i + 1]
            if val == "" or val.startswith("-"):
                print("opf absorb: --root requires a non-empty directory argument, not {!r}".format(val),
                      file=sys.stderr)
                return EXIT_MALFORMED
            root = val
            i += 2
        elif tok == "--covers":
            if i + 1 >= len(rest):
                print("opf absorb: --covers requires a value", file=sys.stderr)
                return EXIT_MALFORMED
            if covers is not None:
                print("opf absorb: --covers given more than once", file=sys.stderr)
                return EXIT_MALFORMED
            val = rest[i + 1]
            if val == "" or val.startswith("-"):
                print("opf absorb: --covers requires a non-empty value, not {!r}".format(val),
                      file=sys.stderr)
                return EXIT_MALFORMED
            covers = val
            i += 2
        else:
            print("opf absorb: unrecognized argument {!r}".format(tok), file=sys.stderr)
            return EXIT_MALFORMED
    covers_val = covers if covers is not None else _opf_absorb.UNRELEASED
    mode = "freeze-digest" if freeze else "draft"
    # Class-width backstop: the dispatch forwards the engine's defined 0/2 contract unchanged; any residual
    # error routes to a located cannot-evaluate (exit 2), never an uncaught exit-1 escape.
    try:
        return _opf_absorb.run(root if root is not None else ".", covers_val, mode)
    except Exception as exc:  # noqa: BLE001  fail-closed backstop, never a false verdict or uncaught exit-1
        print("opf absorb: cannot evaluate: unexpected error in the drafter ({!r}); failing closed to "
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
    """`opf doctor [--root DIR] [--require-store]`: the store-integrity verb.

    Doctor RESOLVES the store at --root (default: the cwd product repository root), gathers the inert
    git-derived observations (_opf_observe.gather: tracked, actual_remote, prior), and runs the U6 whole-store
    integrity engine (_opf_check.validate_store) over them, returning that engine's own 0/1/2 contract via
    _opf_check.exit_code (0 VALID, 1 INVALID, 2 CANNOT-EVALUATE). A NOT-ADOPTED root reports NOT APPLICABLE
    and exits 0 (the pack's own `--root .` case, mirroring render); any other non-RESOLVED status is a located
    cannot-evaluate (exit 2). `--require-store` is the enforcement-pack CI floor (spec 1.3.0 14.1: the pack
    MUST provide CI checks): with it, a NOT-ADOPTED root is a located cannot-evaluate (exit 2) instead of NOT
    APPLICABLE, so a repository whose store was removed cannot pass CI vacuously; every other status keeps
    its unflagged outcome. Doctor is READ-ONLY: it makes no store change (SECI-preview-has-no-side-effects);
    the observation gather is git reads only. The parser is the house fail-closed idiom (unknown token, an
    empty or option-looking or duplicate --root value, a duplicate --require-store -> exit 2), matching
    _cmd_render's --root loop. Every
    residual escape from the resolver, the git gather, or the engine fails closed to exit 2 (never a false
    verdict), the same class-width backstop the render dispatch carries."""
    root = None
    require_store = False
    i = 0
    while i < len(rest):
        tok = rest[i]
        if tok == "--require-store":
            if require_store:
                print("opf doctor: --require-store given more than once", file=sys.stderr)
                return EXIT_MALFORMED
            require_store = True
            i += 1
        elif tok == "--root":
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
        if require_store:
            # The CI floor: an absent store is a cannot-evaluate, never a vacuous pass.
            print("opf doctor: cannot evaluate: --require-store was given but no OPF store was found ({}); "
                  "a repository with no store cannot pass the CI floor".format(res.detail), file=sys.stderr)
            return EXIT_MALFORMED
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

    OPF-D2B: the probe runs under _opf_observe._run_git_config_discovery, which lets git DISCOVER the
    adopter's real global and system configuration through its default locations (unlike the default scrubbed
    observer environment, which neutralizes it), so a store ignored ONLY by the adopter's global or system
    core.excludesFile is caught here, matching what the adopter's own `git add` would honour
    (guard-input-soundness). Env-based config overrides are dropped and trace/fsmonitor are forced off, so no
    reachable configuration can turn the read-only probe into a write or a launched process. Lazy fetching is
    forced off so a missing indexed ignore blob cannot trigger a promisor fetch that would reach
    core.sshCommand. DISCLOSED RESIDUAL (disclose-guard-residuals): this rc-only check reads ignore rules
    exactly as git add does when git add can read them too. For an ignore input git cannot READ (a
    permission-denied core.excludesFile, ~/.config/git/ignore, or .git/info/exclude), git add is equally unable
    to read it and stages the destination, so reading not-ignored here matches git add and the store is never
    silently ignored. The one input where that parity BREAKS is a skip-worktree (or otherwise unmaterialized)
    .gitignore whose blob is absent in a partial clone: this probe forces lazy fetch off and reads no-rule, but
    the adopter's own git add fetches the blob and can then ignore the store. That case is NOT left as a
    residual: after an rc-1 result the availability of every applicable indexed .gitignore blob is checked
    (_opf_observe.indexed_ignore_availability), and an unavailable one is a cannot-evaluate that REFUSES rather
    than passing (guard-input-soundness). A fail-closed treatment of the unreadable-FILE arms above is
    deliberately not attempted, because git and the caller resolve config paths differently and refusing there
    would over-refuse a case git add itself skips. Called directly
    (not via _init_git, which raises on rc != 0) because rc 1 is the success case here; a timeout, launch
    failure, or unexpected rc fails closed and refuses. Matching `git add`, a benign git diagnostic on an
    rc-1 (not-ignored) result is not itself a refusal.
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
    if result.rc != 1:
        raise RuntimeError("git preflight: check-ignore failed (rc={}): {}".format(
            result.rc, result.err))
    # rc 1 (not ignored) is sound only when every applicable .gitignore is answerable WITHOUT a promisor
    # fetch. In a partial clone a skip-worktree (or otherwise unmaterialized) .gitignore whose blob is absent
    # reads as no-rule here (lazy fetch is forced off), yet the adopter's own `git add` fetches that blob and
    # can then silently ignore the store; an available blob is read identically by both (parity) and a
    # checked-out .gitignore is read from disk by both. So a missing indexed ignore blob is a cannot-evaluate
    # we refuse, not a clean pass (guard-input-soundness).
    unavailable = _opf_observe.indexed_ignore_availability(
        git, repo, _opf_write_guard._ignore_file_candidates(root.relative_to(repo), paths))
    if unavailable:
        raise RuntimeError(
            "git preflight: an indexed .gitignore blob is unavailable in this partial clone, so ignore "
            "status cannot be determined; `git add` would fetch it and could silently ignore the store "
            "({}). Check out or fetch the blob and retry.".format(sorted(unavailable)))


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


# --- opf upgrade: the store-schema upgrade to the tooling spec_version (spec 9.2) ---------------------

# The single 1.0.0 -> 1.1.0 upgrade this build implements. maintainer_decision and preference_pattern
# baseline (they were module-tier in 1.0.0); contribution is net-new; the decision_support module is
# retired (spec 8.1 note, spec 9.2). The delta is enumerated so the postcondition can assert the model
# diff equals EXACTLY it and nothing else (fail-closed on any stray change).
# _UPGRADE_TO is the literal the tooling implements; _cmd_upgrade asserts it equals the live
# _opf_store.SUPPORTED_SPEC_VERSION (bound only after _bootstrap), so a future spec bump cannot let this
# constant silently drift from the roster.
_UPGRADE_FROM = "1.0.0"
# OPF-D2B PR3a (PD-D2B-PR3-SCHEMA decision 5): base spec 1.2.0 admits the managed bootstrap provenance
# `.working/toml/init.toml` a coupled init writes. The 1.1.0 -> 1.2.0 schema delta is the spec_version
# bump ALONE: a 1.1.0 (D2a) store gains no init.toml (no provenance is ever fabricated for it), and a 1.0.0
# store takes the full 1.0.0 schema delta straight to 1.2.0. Declared views are then regenerated.
_UPGRADE_MID = "1.1.0"
_UPGRADE_TO = "1.2.0"
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
    final-doctor gate; recovery restores tracked paths from HEAD and removes upgrade-created files
    within the checked scope -- the no-complete-pre-doctor
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


def _upgrade_plan_minor(manifest_model, counters_model):
    """The 1.1.0 -> 1.2.0 allowed delta (spec 9.2, OPF-D2B PR3a): the [opf].spec_version bump ALONE.
    Preconditions (fail-closed): the current [opf] base table (never the retired [devprocess]) declaring
    standard "opf" and spec_version 1.1.0, and a [counters] table. No init.toml provenance is created
    (none is ever fabricated for an existing store), no type, view, module, index, or counter changes,
    and the postcondition asserts the manifest diff is EXACTLY the version field and the counters model
    is unchanged. Returns (new_manifest, new_counters, added_namespaces, origin) like _upgrade_plan."""
    import copy
    base = manifest_model.get(_opf_store.STANDARD_TOKEN)
    if not isinstance(base, dict) or _opf_store.PRIOR_STANDARD_TOKEN in manifest_model:
        raise _UpgradeError("manifest carries no [{}] base table (or still carries the retired [{}]); "
                            "not a {} store this upgrade migrates (fail-closed)".format(
                                _opf_store.STANDARD_TOKEN, _opf_store.PRIOR_STANDARD_TOKEN,
                                _UPGRADE_MID))
    if base.get("standard") != _opf_store.STANDARD_TOKEN or base.get("spec_version") != _UPGRADE_MID:
        raise _UpgradeError("manifest [{}] is not a {} base; no known upgrade path (fail-closed)".format(
            _opf_store.STANDARD_TOKEN, _UPGRADE_MID))
    if not isinstance(counters_model.get("counters"), dict):
        raise _UpgradeError("counters.toml [counters] table is missing or malformed (fail-closed)")
    new_manifest = copy.deepcopy(manifest_model)
    new_manifest[_opf_store.STANDARD_TOKEN]["spec_version"] = _UPGRADE_TO
    new_counters = copy.deepcopy(counters_model)
    expected = copy.deepcopy(manifest_model)
    expected[_opf_store.STANDARD_TOKEN] = dict(expected[_opf_store.STANDARD_TOKEN],
                                               spec_version=_UPGRADE_TO)
    if new_manifest != expected or new_counters != counters_model:
        raise _UpgradeError("upgrade postcondition failed: the {} -> {} delta is the spec_version bump "
                            "alone".format(_UPGRADE_MID, _UPGRADE_TO))
    origin = {"pre_declared": frozenset(), "ds_key_present": False, "decisions_declared": None,
              "from": _UPGRADE_MID}
    return new_manifest, new_counters, [], origin


def _cmd_upgrade(rest):
    """`opf upgrade [--root DIR]`: the in-place, additive, idempotent store-schema upgrade to the tooling
    spec_version {to} (spec 9.2). Two origins are supported: a 1.1.0 store takes the 1.1.0 -> {to} schema
    delta, changing only spec_version (no init.toml provenance is fabricated); a 1.0.0 store takes the
    full schema delta below, straight to {to}. Declared views are then regenerated, so a stale
    committed view can change.
    It RESOLVES the store at --root and refuses a store above the tooling spec. When migrating a 1.0.0
    or 1.1.0 store, its preconditions include readable, canonical manifest/counters matching a recognised
    origin shape, cleanliness over planned schema/render destinations and index collision candidates
    (including ignored files there), and acquisition of its lease. An untracked or ignored lease is
    excepted from the cleanliness check and handled separately by lease acquisition.
    It then applies EXACTLY the allowed delta as
    a model regeneration through the canonical new-document emitter (bump spec_version; drop the retired
    decision_support module WHERE PRESENT; add each contribution/maintainer_decision/preference_pattern type
    row NOT already declared by an enabled 1.0.0 module; add the two new view rows; WIDEN the DECISIONS.md
    composed view WHERE DECLARED; extend counters with zeros for any missing CN/MD/PP namespace,
    preserving existing high-waters; create the missing empty indexes, skipping any that already exist),
    RENDERS the declared views, and requires a full doctor VALID before offering the uncommitted change. It is
    ORIGIN-AWARE: a governance- or decision_support-enabled 1.0.0 store, and a store that omits the
    optional decision_support key or the DECISIONS.md view, each migrate correctly (spec 9.2, G1-G4).
    Before ANY write it enforces two fail-closed preconditions:
    PLANNED-DESTINATION CLEANLINESS (store and product roots), including ignored files, over planned
    destinations and collisions (HEAD preserves pre-existing tracked content,
    SECA-verified-restore-path), with an untracked or ignored lease handled separately, and a
    SINGLE-WRITER LEASE it claims atomically and holds across the mutation, render, and final doctor (spec
    5.7). It NEVER commits: the adopter reviews and merges. A store already at {to} returns without those
    migration preconditions: doctor-VALID yields a byte no-op (exit 0), otherwise it exits 2 without
    writing. A NOT-ADOPTED root is NOT APPLICABLE (exit 0), any other non-resolved status a located
    cannot-evaluate (exit 2). Two disclosed residuals: a killed run leaves the lease, which is
    spec-conformant (present only while held; a leftover is released through operator reconciliation, spec
    5.7) and is what the EEXIST refusal covers; and the lease is not made observable at a sync target
    before writes (spec 5.7) because this build has no sync runtime, so the guarantee is single-host
    single-writer. A third disclosed residual: this build has no pre-doctor for a 1.0.0 or 1.1.0 origin,
    so an older store invalid in a way the origin preconditions do not inspect fails only AFTER mutation
    (at the render or the final doctor). Under the single-writer contract, recovery restores pre-existing
    tracked content from HEAD and removes upgrade-created files within the checked store/product scope;
    pre-existing untracked or ignored content in that scope refuses before mutation.""".format(
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
    except (_UpgradeError, _opf_write_guard.WriteGuardError) as exc:
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
                          "destroy unrelated uncommitted work. Scope every recovery to the affected "
                          "store and product paths.")
# The planned scope the shared cleanliness gate names in its dirty-tree refusal (_opf_write_guard.check_clean).
_UPGRADE_CLEAN_SCOPE = "schema and render destinations and index collision candidates"


def _upgrade_partial_recovery_text(res, manifest_model):
    """A previous interrupted run has no trustworthy write plan in this process. Inspect under the
    resolved store root, but do not prescribe a subtree restore: it could discard unmanaged owner work."""
    import shlex
    r = shlex.quote(str(res.store_root))
    w = shlex.quote(_opf_store.WORKING_DIRNAME)
    try:
        scope = _upgrade_write_scope(res.machine_rel, manifest_model, True, _UPGRADE_NEW_TYPES)
        candidates = "Candidate store destinations under {} (current manifest; inspect only): {}.".format(
            r, " ".join(shlex.quote(p) for p in scope["store"]))
        if scope["product"]:
            product_root = res.product_root if res.product_root is not None else res.store_root
            candidates += "\nCandidate product destinations under {} (inspect only): {}.".format(
                shlex.quote(str(product_root)), " ".join(shlex.quote(p) for p in scope["product"]))
    except Exception as exc:  # Candidate advice must not hide the doctor findings.
        candidates = "Cannot derive candidate destinations from the current manifest: {}.".format(exc)
    return ("Inspect the store subtree (git -C {r} --literal-pathspecs status --ignored=matching "
            "--untracked-files=all -- {w}). Identify the earlier run's planned destinations and reconcile "
            "intervening owner edits before restoring individual tracked paths or removing files proven "
            "upgrade-created. Exclude unmanaged paths and the lease; reconcile a leftover lease only after "
            "confirming no run is live (spec 5.7). Reconcile product targets separately, then re-run. "
            "{no}\n{candidates} These candidates do not prove the earlier write scope or which files were "
            "created; reconcile them against the earlier run and owner edits before recovery.".format(
                r=r, w=w, no=_UPGRADE_NO_WHOLE_TREE, candidates=candidates))


def _upgrade_recovery_text(store_root, product_root, created_relpaths, product_relpaths, store_relpaths):
    """Recovery advice for a post-mutation failure (render or doctor): the planned set is KNOWN, and with the
    step-3 cleanliness precondition (including ignored files) and the single-writer contract, HEAD
    preserves pre-existing tracked content in the checked scope; remove upgrade-created files to recover
    previously absent paths. Inspect first for intervening owner edits. The DISTINCT roots are threaded
    (explicit-binding-over-ambient-context): tracked store paths
    in the explicit plan, and the upgrade-created untracked files, are recovered under the STORE root (where
    `.working` lives); a declared product-scope target rendered this run is recovered under the PRODUCT root.
    The two roots differ for a RELOCATED store (the pointer resolves `.working` to a store separate from the
    product tree), where using one root for both would aim the `.working` restore at the wrong repository.
    Never a whole-tree restore."""
    import shlex
    r = shlex.quote(str(store_root))
    w = " ".join(shlex.quote(p) for p in sorted(store_relpaths))
    tracked = " ".join(shlex.quote(p) for p in sorted(set(store_relpaths) - set(created_relpaths)))
    lines = ["opf upgrade: the uncommitted change is left for review; recover it scoped to the paths this run "
             "planned (tracked content was clean against HEAD; untracked and ignored content was refused). "
             "Confirm no opf run is live (spec 5.7), then inspect for intervening owner edits:"]
    if tracked:
        lines.append("  restore tracked store paths: git -C {} --literal-pathspecs restore --staged "
                     "--worktree -- {}".format(r, tracked))
    if created_relpaths:
        lines.append("  remove only these previously absent files if this run created them: rm -- " + " ".join(
            shlex.quote(os.path.join(str(store_root), p)) for p in sorted(created_relpaths)))
    pr = shlex.quote(str(product_root))
    for p in sorted(product_relpaths):
        lines.append("  product target rendered: git -C {} --literal-pathspecs restore --staged --worktree "
                     "-- {} (or remove it if this run created it)".format(pr, shlex.quote(p)))
    lines.append("  inspect first: git -C {} --literal-pathspecs status --ignored=matching "
                 "--untracked-files=all -- {}".format(r, w))
    lines.append("  " + _UPGRADE_NO_WHOLE_TREE)
    return "\n".join(lines)


def _upgrade_write_scope(machine_rel, manifest_model, counters_changed, index_types):
    """Plan the destinations of the schema delta and the subsequent declared-view render.
    Index candidates are included for collision checks even when create-only will leave them alone.
    Use the POST-delta manifest: it includes newly introduced views. The shared planner
    (_opf_write_guard.plan_write_scope) adds every declared render destination and refuses a declaration
    colliding with a managed destination before mutation, rather than licensing an overwrite. The lease
    is checked separately and is never a restore/staging target."""
    store = ["{}/{}".format(machine_rel, _opf_store.MANIFEST_NAME)]
    if counters_changed:
        store.append("{}/{}".format(machine_rel, _opf_check.COUNTERS_NAME))
    indexes = tuple("{}/{}{}".format(machine_rel, t, _opf_check.INDEX_SUFFIX) for t in index_types)
    store.extend(indexes)
    scope = _opf_write_guard.plan_write_scope(machine_rel, manifest_model, store, "upgrade")
    scope["indexes"] = indexes
    return scope


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
    recovery = None
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
                      _UPGRADE_TO, _upgrade_partial_recovery_text(res, manifest_model), root), file=sys.stderr)
            _doctor_report(result)
            return EXIT_MALFORMED
        try:
            sv_tuple = tuple(int(p) for p in sv.split(".")) if isinstance(sv, str) else None
        except ValueError:
            sv_tuple = None
        if sv_tuple is not None and sv_tuple > tuple(int(p) for p in _UPGRADE_TO.split(".")):
            raise _UpgradeError("store declares spec_version {!r} ABOVE the {} this tooling implements; "
                                "a newer store is never downgraded (fail-closed)".format(sv, _UPGRADE_TO))
        minor = sv == _UPGRADE_MID

        # PRECONDITION (spec 9.2): re-emitting the UNCHANGED parsed model reproduces the on-disk bytes
        # exactly, proving the file is canonical and comment-free so the bounded rewrite loses nothing.
        if _opf_emit.emit_checked(manifest_model).encode("utf-8") != manifest_bytes:
            raise _UpgradeError("manifest is not in canonical new-document form (hand-edited or comment-"
                                "bearing); refusing a model rewrite that could lose content (fail-closed)")
        if _opf_emit.emit_checked(counters_model).encode("utf-8") != counters_bytes:
            raise _UpgradeError("counters.toml is not in canonical new-document form; refusing (fail-closed)")

        new_manifest, new_counters, added_ns, origin = (
            _upgrade_plan_minor if minor else _upgrade_plan)(manifest_model, counters_model)
        origin_version = _UPGRADE_MID if minor else _UPGRADE_FROM
        new_manifest_bytes = _opf_emit.emit_checked(new_manifest).encode("utf-8")
        new_counters_bytes = _opf_emit.emit_checked(new_counters).encode("utf-8")

        # STEP 3: derive the scope from this delta and its POST-delta render declarations. The same
        # destinations drive the cleanliness gate, index creation, recovery, and staging advice.
        write_scope = _upgrade_write_scope(
            machine_rel, new_manifest, new_counters_bytes != counters_bytes,
            () if minor else _UPGRADE_NEW_TYPES)
        _opf_write_guard.check_clean(res, write_scope, "upgrade", _UPGRADE_CLEAN_SCOPE)
        product_targets = write_scope["product"]
        created_relpaths = []
        for relpath in write_scope["store"]:
            pfd, name = _opf_store._journal._open_parent(root_fd, relpath)
            try:
                entry = _opf_store._journal._lstat_at(pfd, name)
                if entry is None:
                    created_relpaths.append(relpath)
                elif not stat.S_ISREG(entry.st_mode):
                    raise _UpgradeError("upgrade destination {!r} is not a regular file "
                                        "(fail-closed)".format(relpath))
            finally:
                os.close(pfd)
        # DISTINCT roots for the recovery/staging advice (R1): `.working` lives under the STORE root, product-
        # scope targets under the PRODUCT root; the two differ for a RELOCATED store.
        recovery_store_root = res.store_root
        recovery_product_root = res.product_root if res.product_root is not None else res.store_root
        absent_product_targets = []
        for relpath in product_targets:
            try:
                os.lstat(os.path.join(str(recovery_product_root), relpath))
            except FileNotFoundError:
                absent_product_targets.append(relpath)
        _opf_write_guard.check_ignored(recovery_store_root, created_relpaths, "upgrade")
        _opf_write_guard.check_ignored(recovery_product_root, absent_product_targets, "upgrade")
        # STEP 4 (M4): claim the single-writer lease atomically, then hold it across mutation, render, and
        # the final doctor; release it in the finally covering every exit after acquisition, EXCEPT the
        # success path releases FIRST (R5) so no success is reported over a still-held / failed-to-release
        # lease. `released` records that the success path already released, so the finally does not re-release.
        lease_payload = _opf_write_guard.acquire_lease(root_fd, machine_rel, "upgrade")
        recovery = (recovery_store_root, recovery_product_root, created_relpaths,
                    product_targets, write_scope["store"])
        released = False
        try:
            # Apply: rewrite manifest + counters (canonical bytes), create the missing empty indexes. The
            # 1.1.0 origin rewrites the manifest alone (its delta is the spec_version bump).
            _upgrade_replace(root_fd, manifest_rel, new_manifest_bytes)
            if new_counters_bytes != counters_bytes:
                _upgrade_replace(root_fd, counters_rel, new_counters_bytes)
            empty_index = _opf_emit.emit_checked(
                {"schema": _opf_schema.SUPPORTED_SCHEMA, "record": []}).encode("utf-8")
            created_indexes = []
            for idx_rel in write_scope["indexes"]:
                if _upgrade_create_index(root_fd, idx_rel, empty_index):
                    created_indexes.append(Path(idx_rel).name[:-len(_opf_check.INDEX_SUFFIX)])

            # Render the declared views (materializes the two new views and re-renders DECISIONS.md), then
            # require a full doctor VALID before offering the uncommitted change. Both run over the mutated
            # (uncommitted) tree, under the held lease (containment-clean; the mid-run doctor stays VALID).
            render_argv = ["--root", root, "--write"]
            try:
                rres = _opf_store.resolve_store(Path(os.path.abspath(root)))
                robs, _notes = _opf_observe.gather(rres) if rres.status == _opf_store.RESOLVED else (None, [])
                rc = _opf_views.render(render_argv, observations=robs)
            except Exception as exc:  # noqa: BLE001  a render escape must not read as a clean upgrade
                raise _UpgradeError("view render after the schema delta failed ({!r}); the uncommitted change is "
                                    "left for review".format(exc))
            if rc != EXIT_OK:
                print("opf upgrade: cannot evaluate: view render after the schema delta did not complete "
                      "cleanly (rc={}); exit 2.".format(rc), file=sys.stderr)
                print(_upgrade_recovery_text(recovery_store_root, recovery_product_root, created_relpaths,
                                             product_targets, write_scope["store"]), file=sys.stderr)
                recovery = None  # Already printed, even if the finally's lease release fails.
                return EXIT_MALFORMED

            result = _upgrade_doctor(root)
            if result.status != _opf_store.VALID:
                print("opf upgrade: the upgraded store is NOT doctor-VALID; refusing to offer the change "
                      "(fail-closed, spec 9.2). Run `opf doctor --root {}` for the findings, exit 2.".format(
                          root), file=sys.stderr)
                print(_upgrade_recovery_text(recovery_store_root, recovery_product_root, created_relpaths,
                                             product_targets, write_scope["store"]), file=sys.stderr)
                recovery = None  # Already printed, even if the finally's lease release fails.
                _doctor_report(result)
                return EXIT_MALFORMED

            # R5: release the single-writer lease BEFORE emitting the success report. A release failure
            # surfaces as exit 2 (never swallowed), and success is never printed over a still-held lease.
            # `released` is set FIRST so the finally never double-releases (a failed release legitimately
            # leaves the lease as a spec-conformant leftover for operator reconciliation).
            released = True
            # Doctor has validated the payload. A failed release can mean another holder is live;
            # automatic rollback advice is unsafe and is not required to make this payload valid.
            recovery = None
            try:
                _opf_write_guard.release_lease(root_fd, machine_rel, lease_payload, "upgrade")
            except BaseException:
                print("opf upgrade: the store reached doctor-VALID before lease release, but release "
                      "failed. Confirm no opf run is live (spec 5.7) and reconcile the lease before "
                      "any further action; no restore/removal commands are offered.", file=sys.stderr)
                raise
            print("opf upgrade: store schema upgraded {} -> {} and doctor-VALID (uncommitted, NOT staged or committed)."
                  .format(origin_version, _UPGRADE_TO))
            print(json.dumps({
                "event": "upgraded", "root": str(root), "from": origin_version, "to": _UPGRADE_TO,
                "created_indexes": sorted(created_indexes), "added_counters": sorted(added_ns),
                "pre_declared_types": sorted(origin["pre_declared"]),
                "decisions_view": "unchanged" if minor else (
                    "widened" if origin["decisions_declared"] else "not-declared")},
                sort_keys=True))
            print("opf upgrade: regenerated views reflect working-tree store content, including uncommitted "
                  "source edits outside the cleanliness scope. Before committing, review those edits and "
                  "commit the intended sources with their views, or set them aside and regenerate the views.")
            print("opf upgrade: review the uncommitted changes, then stage and commit the planned destinations "
                  "(never `add -A`, which would sweep in unrelated work). The commands use -f for "
                  "these named destinations because the safety probes neutralize global/system config "
                  "and core.excludesFile (including default HOME/XDG global ignores). They read "
                  "working-tree .gitignore files and .git/info/exclude; the absent-path probe does "
                  "not read indexed ignore rules:")
            print("  git -C {} --literal-pathspecs add -f -- {}".format(
                shlex.quote(str(recovery_store_root)),
                " ".join(shlex.quote(p) for p in write_scope["store"])))
            for _pt in product_targets:
                print("  git -C {} --literal-pathspecs add -f -- {}".format(
                    shlex.quote(str(recovery_product_root)), shlex.quote(_pt)))
            print("opf upgrade: exit 0 means the store is valid at {}; committing is the adopter's own "
                  "step.".format(_UPGRADE_TO))
            return EXIT_OK
        finally:
            if not released:
                # R5/FIX1: release the lease on every non-success exit. When a mid-run failure is ALREADY
                # propagating (a render/doctor escape after the manifest+counters were rewritten, which
                # carries its own "uncommitted change is left for review" recovery advice) and the release then
                # ALSO fails (its lease-replaced never-seize WriteGuardError), the release error must NOT
                # DISPLACE that original exception: the operator still needs the mid-run recovery advice, so
                # the lease-replaced note is surfaced ALONGSIDE it, never in place of it (exit 2 preserved,
                # peer lease left, never seized). A propagating KeyboardInterrupt/SystemExit is likewise not
                # masked by a release failure.
                pending = sys.exc_info()[1]
                if pending is None:
                    # A `return` (or normal fall-through) is passing through with no in-flight exception: a
                    # release failure legitimately becomes the surfaced outcome (exit 2), exactly as before.
                    _opf_write_guard.release_lease(root_fd, machine_rel, lease_payload, "upgrade")
                else:
                    try:
                        _opf_write_guard.release_lease(root_fd, machine_rel, lease_payload, "upgrade")
                    except (KeyboardInterrupt, SystemExit):
                        raise
                    except Exception as rel_exc:  # noqa: BLE001  surfaced, never displaces the original
                        print("opf upgrade: additionally, releasing the upgrade lease failed ({}); the peer "
                              "lease is LEFT in place (never seized, spec 5.7) and the original failure above "
                              "still governs (exit 2).".format(rel_exc), file=sys.stderr)
                        # returning from the except lets `pending` resume propagating (the finally completes
                        # without raising a new exception), so _cmd_upgrade surfaces the original refusal.
    except BaseException:
        # Cover every escape after acquisition, including writes, render/doctor and lease release.
        # Preserve the original exception and the existing never-seize release handling.
        if recovery is not None:
            print(_upgrade_recovery_text(*recovery), file=sys.stderr)
        raise
    finally:
        os.close(root_fd)


# Spec 14.1: the former --scan, --plan, --review and --apply modes refuse with a pointer to adoption and the
# prompt pack. The pointer is in words only; it names no command this build lacks.
IMPORT_RETIRED = (
    "the ordinary import modes (scan, plan, review and apply) are retired: clean-start adoption (OPF spec "
    "14.1) is the only intake, and post-adoption import uses the approved prompt pack; nothing was written")


def _cmd_import(rest):
    """`opf import [--root DIR] (--scan --set FILE | --plan --set FILE | --review <run-id> --actor NAME
    (--decisions FILE | --interactive) | --apply <run-id>)`: the retired store import verb.

    RETIRED (spec 14.1), its engine removed: every mode refuses with IMPORT_RETIRED at exit 2, on every
    root, before the clock, any input file or any write, and BEFORE any mode-specific argv validation, so a
    missing or extra companion flag or a run-id outside the former grammar meets the retirement pointer,
    never a usage error for a mode this build refuses. Only the token parser (an unknown flag, a duplicate,
    an empty or missing value) and the exactly-one-mode rule precede it: a bare `opf import` or two modes is
    a usage error (exit 2) without the pointer. The parser is the house fail-closed idiom, matching
    _cmd_render's --root loop. The root is never resolved, so an unresolved / NOT-ADOPTED root refuses the
    same way (D7: import is a REQUESTED operation, so its refusal is a cannot-evaluate, never the
    NOT-APPLICABLE exit 0 that doctor/render/upgrade report on a non-adopter root)."""
    root = None
    mode = None
    set_file = None
    actor = None
    decisions_file = None
    interactive = False

    def _need_value(flag, idx):
        if idx + 1 >= len(rest):
            print("opf import: {} requires an argument".format(flag), file=sys.stderr)
            return None
        val = rest[idx + 1]
        if val == "" or val.startswith("-"):
            print("opf import: {} requires a non-empty argument, not {!r}".format(flag, val),
                  file=sys.stderr)
            return None
        return val

    i = 0
    while i < len(rest):
        tok = rest[i]
        if tok in ("--scan", "--plan"):
            if mode is not None:
                print("opf import: give exactly one mode (--scan / --plan / --review / --apply)",
                      file=sys.stderr)
                return EXIT_MALFORMED
            mode = tok[2:]
            i += 1
        elif tok in ("--review", "--apply"):
            if mode is not None:
                print("opf import: give exactly one mode (--scan / --plan / --review / --apply)",
                      file=sys.stderr)
                return EXIT_MALFORMED
            if _need_value(tok, i) is None:
                return EXIT_MALFORMED
            mode = tok[2:]
            i += 2
        elif tok == "--root":
            if root is not None:
                print("opf import: --root given more than once", file=sys.stderr)
                return EXIT_MALFORMED
            root = _need_value(tok, i)
            if root is None:
                return EXIT_MALFORMED
            i += 2
        elif tok == "--set":
            if set_file is not None:
                print("opf import: --set given more than once", file=sys.stderr)
                return EXIT_MALFORMED
            set_file = _need_value(tok, i)
            if set_file is None:
                return EXIT_MALFORMED
            i += 2
        elif tok == "--actor":
            if actor is not None:
                print("opf import: --actor given more than once", file=sys.stderr)
                return EXIT_MALFORMED
            actor = _need_value(tok, i)
            if actor is None:
                return EXIT_MALFORMED
            i += 2
        elif tok == "--decisions":
            if decisions_file is not None:
                print("opf import: --decisions given more than once", file=sys.stderr)
                return EXIT_MALFORMED
            decisions_file = _need_value(tok, i)
            if decisions_file is None:
                return EXIT_MALFORMED
            i += 2
        elif tok == "--interactive":
            if interactive:
                print("opf import: --interactive given more than once", file=sys.stderr)
                return EXIT_MALFORMED
            interactive = True
            i += 1
        else:
            print("opf import: unrecognized argument {!r}".format(tok), file=sys.stderr)
            return EXIT_MALFORMED

    if mode is None:
        print("opf import: give exactly one mode (--scan / --plan / --review / --apply)", file=sys.stderr)
        return EXIT_MALFORMED
    print("opf import: {}".format(IMPORT_RETIRED), file=sys.stderr)
    return EXIT_MALFORMED


def _cmd_record(rest):
    """`opf record <subcommand> ...` (spec 8.8): the record-authoring verb. `create`, `transition`,
    `done-with-receipt`, and `worklog-append` run the shared journaled operation sequence in _opf_record
    (byte-reproduction precondition, one id claim through the allocation seam, allowed-delta postcondition,
    cleanliness gate and lease, one journaled publication, render, final doctor VALID, lease release before
    the report). The final doctor's one exception is a status change (transition or done-with-receipt),
    which may leave only doctor's cannot-evaluate for exactly that record and from/to pair, never a finding;
    doctor keeps reporting it until the change is committed. Exit 0 recorded (left uncommitted), exit 2
    every refusal or cannot-evaluate; exit 1 is not used. Unlike the applicability-probe siblings a
    NOT-ADOPTED root is a cannot-evaluate (a requested operation, like import)."""
    try:
        return _opf_record.cli(rest)
    except Exception as exc:  # noqa: BLE001  class-width fail-closed backstop, never a false success
        print("opf record: cannot evaluate: unexpected error ({!r}); failing closed to exit 2".format(exc),
              file=sys.stderr)
        return EXIT_MALFORMED


def _adopt_exit(status):
    """Map a planning-layer status (an _opf_adopt_plan.AdoptResult or _opf_adopt.AdoptValidation carries
    the _opf_store VALID / INVALID / CANNOT-EVALUATE vocabulary) to the CLI 0/1/2 exit contract,
    fail-closed: a status outside the vocabulary (a first-party contract violation) is exit 2, never a
    false clean (the _import_exit idiom)."""
    if status == _opf_store.VALID:
        return EXIT_OK
    if status == _opf_store.INVALID:
        return EXIT_FINDING
    return EXIT_MALFORMED   # CANNOT-EVALUATE, or any unexpected value, fails closed


def _adopt_read_inputs(path):
    """Read the `--inputs` adoption planning worksheet (a TOML file) for `opf adopt plan`, fail-closed.
    The worksheet is CALLER input, not a store artefact, so it may live outside the store and is read
    directly; a missing, unreadable, or malformed worksheet is a ValueError (the caller maps it to a
    cannot-evaluate exit 2, never a silent nothing-to-do), mirroring `_import_read_set`'s read-boundary
    discipline. Shape: `schema = 1`, `product`, `expected_observation_digest`, a `bindings` table, and
    the optional `sources` / `targets` (arrays of relative path strings) and `decisions` / `ops` (arrays
    of tables). This reader validates STRUCTURE only, as a closed keyset; the planner and the schema
    layer own the SEMANTICS (_opf_adopt_plan.plan: the digest grammar and inventory binding, the exact
    _opf_adopt.PLAN_BINDING_INPUTS bindings keyset, per-row decision and op validation, the frozen-plan
    validation), never duplicated here. The read is BOUNDED, the planner's own read discipline: a
    no-follow, nonblocking open (every component is walked from the filesystem root with O_NOFOLLOW, the
    store root's _opf_store._open_dir_nofollow walk, so a symlinked parent, ancestor or final component
    refuses; and a FIFO returns at once rather than blocking), a SINGLY-LINKED (st_nlink == 1) regular file
    only (a hardlinked worksheet is a second name for another inode, refused class-consistent with the
    engine's own single-link control reads), and the planner's per-file byte bound
    (_opf_adopt_plan.MAX_FILE_BYTES) checked on the opened fd and again by the journal's capped reader, so
    a FIFO, device, directory, symlink, multiply-linked or oversized worksheet refuses. Returns the parsed
    table with the
    optional keys defaulted."""
    import tomllib
    cap = _opf_adopt_plan.MAX_FILE_BYTES
    try:
        parent, name = os.path.split(path if os.path.isabs(path) else os.path.join(os.getcwd(), path))
        pfd = _opf_store._open_dir_nofollow(parent)
        fd = None
        try:
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=pfd)
        finally:
            try:
                _opf_adopt_apply._journal._close_fd_propagating(pfd)
            except OSError:
                # A failing parent close must not leak the just-opened worksheet fd (round-5 defect 2)
                # or the parent fd itself (round 7: both closes run through the journal engine's
                # confirm-then-release guards, so a close that raises with its number retained still
                # releases it, never via a blind double close); the propagating error still fails the
                # read closed below.
                if fd is not None:
                    _opf_adopt_apply._journal._close_fd_quietly(fd)
                raise
    except FileNotFoundError:
        raise ValueError("--inputs worksheet not found: {}".format(path))
    except (OSError, ValueError) as exc:   # ELOOP/ENOTDIR: a symlinked worksheet or ancestor, never followed
        raise ValueError("--inputs worksheet unreadable ({}): {}".format(path, exc))
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise ValueError("--inputs worksheet is not a regular file (a FIFO, device or directory is "
                             "refused): {}".format(path))
        if st.st_nlink != 1:
            raise ValueError("--inputs worksheet has {} hard links; a multiply-linked worksheet (a "
                             "hardlink whose other name may be an out-of-tree victim) is refused, "
                             "class-consistent with the engine's singly-linked control reads: "
                             "{}".format(st.st_nlink, path))
        if st.st_size > cap:
            raise ValueError("--inputs worksheet exceeds the {}-byte bound: {}".format(cap, path))
        raw = _opf_adopt_apply._journal._read_fd(fd, cap=cap)
    except (OSError, _opf_adopt_apply._journal.JournalError) as exc:
        raise ValueError("--inputs worksheet unreadable ({}): {}".format(path, exc))
    finally:
        _opf_adopt_apply._journal._close_fd_propagating(fd)
    try:
        doc = tomllib.loads(raw.decode("utf-8"))
    except (ValueError, RecursionError) as exc:   # UnicodeDecodeError and TOMLDecodeError are ValueErrors
        raise ValueError("--inputs worksheet unreadable or malformed ({}): {}".format(path, exc))
    if not (isinstance(doc, dict) and type(doc.get("schema")) is int and doc.get("schema") == 1):
        raise ValueError("--inputs worksheet must be a TOML table carrying `schema = 1` (an integer 1, "
                         "not a bool or float)")
    allowed = frozenset(("schema", "product", "expected_observation_digest", "sources", "targets",
                         "decisions", "ops", "bindings"))
    extra = set(doc) - allowed
    if extra:
        raise ValueError("--inputs worksheet carries unknown key(s): {} (the worksheet keyset is closed: "
                         "schema, product, expected_observation_digest, sources, targets, decisions, "
                         "ops, bindings)".format(", ".join(sorted(extra))))
    if not (isinstance(doc.get("product"), str) and doc["product"]):
        raise ValueError("--inputs worksheet `product` must be a non-empty string")
    if not (isinstance(doc.get("expected_observation_digest"), str)
            and doc["expected_observation_digest"]):
        raise ValueError("--inputs worksheet `expected_observation_digest` must be a non-empty string")
    if not isinstance(doc.get("bindings"), dict):
        raise ValueError("--inputs worksheet `bindings` must be a table (the plan-v2 binding inputs)")
    for key in ("sources", "targets"):
        rows = doc.get(key, [])
        if not (isinstance(rows, list) and all(isinstance(s, str) and s for s in rows)):
            raise ValueError("--inputs worksheet `{}` must be an array of non-empty path strings when "
                             "present".format(key))
        doc[key] = rows
    for key in ("decisions", "ops"):
        rows = doc.get(key, [])
        if not (isinstance(rows, list) and all(isinstance(r, dict) for r in rows)):
            raise ValueError("--inputs worksheet `{}` must be an array of tables when present".format(key))
        doc[key] = rows
    return doc


def _cmd_adopt(rest):
    """`opf adopt <subcommand> ...` (spec 1, 14; OPF-ADOPT K9a): the adoption verb. The subcommand
    vocabulary is `plan`, `approve`, `apply`, `complete`, `reconcile` and `status`; K9a ships ONLY the
    two READ-ONLY subcommands, and every other recognized subcommand refuses fail-closed (exit 2) until
    the mutating adoption engine lands in a later PR, so a stub can never read as a passing operation.

      plan --inputs FILE [--root DIR] : freeze and PRINT the inert adoption proposal through the
          existing planner (_opf_adopt_plan.plan), writing NOTHING. FILE is the caller planning
          worksheet (see _adopt_read_inputs). `now` is read from the clock (timestamp-from-clock) and
          `run_nonce` from os.urandom, both injected into the planner (the deterministic run id composes
          them). Exit 0: VALID -- the frozen plan TOML on stdout (an inert, digest-bound PROPOSAL, never
          permission or readiness to apply; the approval lives in the run evidence, a later PR).
          Exit 1: INVALID (a schema-violating decision, op or plan). Exit 2: cannot-evaluate (an
          unreadable worksheet, a changed inventory, an unresolvable root, an unresolved disposition).
      status [--root DIR] : report the adoption state READ-ONLY, writing nothing: the adoption evidence
          bundles (the _opf_store adoption evidence home, each graded by the engine's own bundle
          validator) and the adoption journal (_opf_adopt_apply.JOURNAL_REL, classified by the engine's
          journal_state), both read contained and no-follow. Exit 0: clean (each verified run listed, or
          "no adoption run exists"). Exit 1: a finding (an open transaction or a held lock in the
          journal, a bundle the validator grades INVALID, or a directory at the evidence home that is not
          a run id). Exit 2: cannot-evaluate (a symlinked, dangling or wrong-type root or home, evidence-
          home entry or journal entry -- a journal entry other than a transaction directory or a
          regular, singly-linked `lock` / `lock.break` -- a root reached through a symlink, `..` included, or an unreadable
          journal or bundle). Like
          `plan`, a NOT-ADOPTED root is fine: adoption is the verb that PRECEDES a store, so neither
          subcommand requires store resolution (unlike import D7).

    The parser is the house fail-closed idiom (_cmd_import): an unknown subcommand or token, an empty or
    option-looking or duplicate value -> exit 2. Every residual escape fails closed to exit 2 (never a
    false 0 or an uncaught exit-1), the same class-width backstop render/doctor/import carry."""
    subcommands = ("plan", "approve", "apply", "complete", "reconcile", "status")
    deferred = ("approve", "apply", "complete", "reconcile")
    if not rest:
        print("opf adopt: give a subcommand ({})".format(" / ".join(subcommands)), file=sys.stderr)
        return EXIT_MALFORMED
    sub, tail = rest[0], rest[1:]
    if sub in deferred:
        # Refused BEFORE any parse or filesystem read, so a deferred subcommand can never write; the
        # message names no command that does not exist (the K9b engine lands these).
        print("opf adopt {}: not yet available in this build (fail-closed); the mutating adoption engine "
              "lands in a later PR".format(sub), file=sys.stderr)
        return EXIT_MALFORMED
    if sub not in subcommands:
        print("opf adopt: unknown subcommand {!r}; subcommands: {}".format(
            sub, ", ".join(subcommands)), file=sys.stderr)
        return EXIT_MALFORMED

    root = None
    inputs_file = None

    def _need_value(flag, idx):
        if idx + 1 >= len(tail):
            print("opf adopt {}: {} requires an argument".format(sub, flag), file=sys.stderr)
            return None
        val = tail[idx + 1]
        if val == "" or val.startswith("-"):
            print("opf adopt {}: {} requires a non-empty argument, not {!r}".format(sub, flag, val),
                  file=sys.stderr)
            return None
        return val

    i = 0
    while i < len(tail):
        tok = tail[i]
        if tok == "--root":
            if root is not None:
                print("opf adopt {}: --root given more than once".format(sub), file=sys.stderr)
                return EXIT_MALFORMED
            val = _need_value(tok, i)
            if val is None:
                return EXIT_MALFORMED
            root = val
            i += 2
        elif tok == "--inputs" and sub == "plan":
            if inputs_file is not None:
                print("opf adopt plan: --inputs given more than once", file=sys.stderr)
                return EXIT_MALFORMED
            val = _need_value(tok, i)
            if val is None:
                return EXIT_MALFORMED
            inputs_file = val
            i += 2
        else:
            print("opf adopt {}: unrecognized argument {!r}".format(sub, tok), file=sys.stderr)
            return EXIT_MALFORMED
    if sub == "plan" and inputs_file is None:
        print("opf adopt plan: --inputs FILE is required (the planning worksheet)", file=sys.stderr)
        return EXIT_MALFORMED
    try:
        # The EFFECTIVE root is what the operator's traversal names: the --root value, or the current
        # directory when --root is omitted. Every refusal below names it (never the absent --root value).
        effective = root if root is not None else os.getcwd()
        root_abs = os.path.abspath(effective)
        # Two guard layers, both cannot-evaluate (exit 2). Layer 1: abspath collapses `..` LEXICALLY,
        # but the kernel resolves `DIR/link/..` to link's target's parent: where the physical resolution
        # differs from the lexical one's, a `..` crossed a symlink, so refuse rather than evaluate a
        # directory the operator did not name.
        physical = os.path.realpath(effective)
        lexical = os.path.realpath(root_abs)
    except (OSError, ValueError) as exc:   # e.g. a deleted current directory: cannot-evaluate, never exit 1
        print("opf adopt {}: cannot evaluate: cannot resolve the product root ({})".format(sub, exc),
              file=sys.stderr)
        return EXIT_MALFORMED
    if physical != lexical:
        print("opf adopt {}: cannot evaluate: the product root {!r} resolves physically to {!r}, not "
              "{!r} (a `..` after a symlink); a symlinked root or ancestor refuses".format(
                  sub, effective, physical, root_abs), file=sys.stderr)
        return EXIT_MALFORMED
    # Layer 2 (K9a round 3): the realpath comparison alone still ADMITS a `..` whose crossing lands back
    # on the collapsed path (R/link/.. with the link resolving inside R) and a `..` after a component
    # that does not exist (R/missing/..), so VALIDATE the ORIGINAL traversal too: every `..` must cross
    # a REAL directory -- present, and neither a symlink nor a non-directory -- or the root refuses.
    # A `..` through a real directory still works.
    at = os.sep
    try:
        walked = effective if os.path.isabs(effective) else os.path.join(os.getcwd(), effective)
        for comp in walked.split(os.sep):
            if comp in ("", "."):
                continue
            if comp != "..":
                at = os.path.join(at, comp)
                continue
            try:
                crossed = os.lstat(at)
            except (OSError, ValueError) as exc:
                print("opf adopt {}: cannot evaluate: the product root {!r} crosses `..` out of {!r}, "
                      "which cannot be read as a real directory ({}); a `..` may cross only a real "
                      "directory".format(sub, effective, at, exc), file=sys.stderr)
                return EXIT_MALFORMED
            if not stat.S_ISDIR(crossed.st_mode):
                print("opf adopt {}: cannot evaluate: the product root {!r} crosses `..` out of {!r}, "
                      "which is a symlink or not a directory; a `..` may cross only a real "
                      "directory".format(sub, effective, at), file=sys.stderr)
                return EXIT_MALFORMED
            at = os.path.dirname(at.rstrip(os.sep)) or os.sep
    except (OSError, ValueError) as exc:   # e.g. a deleted current directory while joining a relative root
        print("opf adopt {}: cannot evaluate: cannot resolve the product root ({})".format(sub, exc),
              file=sys.stderr)
        return EXIT_MALFORMED

    if sub == "plan":
        import datetime
        try:
            doc = _adopt_read_inputs(inputs_file)
            now = datetime.datetime.now(datetime.timezone.utc)
            res = _opf_adopt_plan.plan(
                root_abs, sources=doc["sources"], targets=doc["targets"],
                expected_observation_digest=doc["expected_observation_digest"],
                product=doc["product"], decisions=doc["decisions"], ops=doc["ops"],
                now=now, run_nonce=os.urandom(8).hex(), bindings=doc["bindings"])
            if res.status == _opf_store.VALID:
                sys.stdout.write(res.plan.decode("utf-8"))
                return EXIT_OK
            for f in res.findings:
                print("opf adopt plan: {}".format(f), file=sys.stderr)
            for source in res.unresolved:
                print("opf adopt plan: unresolved source disposition: {}".format(source), file=sys.stderr)
            return _adopt_exit(res.status)
        except ValueError as exc:
            # A fail-closed --inputs read error: cannot-evaluate (exit 2), never a silent skip.
            print("opf adopt plan: cannot evaluate: {}".format(exc), file=sys.stderr)
            return EXIT_MALFORMED
        except Exception as exc:  # noqa: BLE001  fail-closed backstop, never a false verdict or uncaught exit-1
            print("opf adopt plan: cannot evaluate: unexpected error ({!r}); failing closed to exit "
                  "2".format(exc), file=sys.stderr)
            return EXIT_MALFORMED

    # sub == "status": the read-only state report over the two adoption homes; ZERO writes. Both homes
    # are read ONLY through the engine's contained, no-follow primitives bound to ONE product-root fd (a
    # symlinked root or ancestor refuses, a `..` after a symlink included), the same ones verify_bundle
    # and journal_clean_or_refuse use; the journal's lock and entries are read beneath the one journal-
    # root fd opened from it, never by re-resolving a path: a symlinked, dangling or wrong-type home or
    # entry is cannot-evaluate, never followed, skipped or read as absent. A bundle is reported only when
    # the engine's own bundle validator grades it VALID, and the journal is read only through the engine's
    # own classification (journal_state).
    adopt, journal = _opf_adopt_apply, _opf_adopt_apply._journal
    try:
        journal.require_containment()
        root_fd = adopt._open_product_root(root_abs)
        try:
            findings = []
            runs = []
            evidence_rel = _opf_store.IMPORTED_REL + "/" + adopt.KIND
            home = journal._lstat_contained(root_fd, evidence_rel)
            dfd = None                       # no evidence home: no run was ever applied
            if home is not None:
                if not stat.S_ISDIR(home.st_mode):
                    raise adopt.AdoptApplyError("{} is not a directory (a symlinked, dangling or foreign "
                                                "entry at the reserved adoption evidence home)".format(
                                                    evidence_rel))
                dfd = journal._open_dir_contained(root_fd, evidence_rel)
            # K9a fix 4: the home descriptor that produced the listing is HELD through EVERY bundle
            # verification and passed into it, so the listing and each bundle bind to ONE home directory
            # identity: an evidence home swapped onto the pathname after the listing is never
            # re-resolved, and two homes neither of which is clean alone can never combine into one
            # clean report.
            try:
                entries = []
                if dfd is not None:
                    entries = [(name, journal._lstat_at(dfd, name)) for name in sorted(os.listdir(dfd))]
                for name, est in entries:
                    if est is None or not stat.S_ISDIR(est.st_mode):
                        raise adopt.AdoptApplyError("{}/{} is not a directory (a symlinked, dangling or "
                                                    "foreign entry at the reserved adoption evidence "
                                                    "home)".format(evidence_rel, name))
                    if not adopt.is_run_id(name):
                        findings.append("{}/{} does not match the adoption run-id grammar (foreign "
                                        "content at the reserved adoption evidence home)".format(
                                            evidence_rel, name))
                        continue
                    checked = adopt._verify_bundle_at(root_fd, name, adopt.evidence_home_rel(name),
                                                      home_fd=dfd)
                    if checked.status == _opf_store.VALID:
                        runs.append(name)
                    elif checked.status == _opf_store.INVALID:
                        findings.extend("adoption run {}: {}".format(name, f) for f in checked.findings)
                    else:
                        raise adopt.AdoptApplyError("adoption run {}: {}".format(
                            name, "; ".join(checked.findings)))
            finally:
                if dfd is not None:
                    journal._close_fd_propagating(dfd)
            owner, opened = adopt.journal_state(root_fd, adopt._journal_root(root_abs))
        finally:
            journal._close_fd_propagating(root_fd)
        journal_rel = adopt.JOURNAL_REL
        if owner is not None:
            findings.append("the adoption journal lock at {} is held (pid {}); status never seizes it "
                            "(reconcile once no adoption run is live)".format(journal_rel, owner.get("pid")))
        if opened:
            findings.append("the adoption journal at {} holds interrupted transaction(s) {} -- an "
                            "adoption transaction did not complete".format(journal_rel, ", ".join(opened)))
        for run_id in runs:
            print("opf adopt status: adoption run {}: evidence bundle at {}/{}".format(
                run_id, evidence_rel, run_id))
        for f in findings:
            print("opf adopt status: FINDING: {}".format(f), file=sys.stderr)
        if findings:
            return EXIT_FINDING
        if not runs:
            print("opf adopt status: no adoption run exists")
        return EXIT_OK
    except (_opf_adopt_apply.AdoptApplyError, _opf_adopt_apply._journal.JournalError, OSError) as exc:
        print("opf adopt status: cannot evaluate: {}".format(exc), file=sys.stderr)
        return EXIT_MALFORMED
    except Exception as exc:  # noqa: BLE001  fail-closed backstop, never a false verdict or uncaught exit-1
        print("opf adopt status: cannot evaluate: unexpected error ({!r}); failing closed to exit "
              "2".format(exc), file=sys.stderr)
        return EXIT_MALFORMED


def _cli_self_test():
    """Guard the dispatcher's render, doctor, import, record, adopt, and source-only init routes.
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
    branch and returns 2, failing this case); a garbage store returns 2; with `--require-store` (the CI
    floor) the same NOT-ADOPTED root returns 2 with the located flag message, and a duplicate flag is a usage
    error (exit 2). The render clean/drift 0/1
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
            if verb not in ("init", "import", "render", "doctor", "upgrade", "absorb", "record", "adopt"):
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
        expect(["doctor", "--require-store", "--require-store"], EXIT_MALFORMED)   # duplicate CI-floor flag

        # absorb verb ROUTING (OPF-CHANGELOG-ABSORB), judged on exit code only. These grammar cases fail
        # closed in the parser BEFORE any store resolution, so they need no store on disk. The NOT-ADOPTED
        # (0) / garbage (2) discrimination over a real root rides _fixture_leg below (reverting the absorb
        # dispatch routes these to the fail-closed KNOWN_VERBS branch, returning 2 where 0 is expected).
        expect(["absorb", "--root"], EXIT_MALFORMED)             # --root needs a value
        expect(["absorb", "--root", ""], EXIT_MALFORMED)         # empty root refused
        expect(["absorb", "--covers"], EXIT_MALFORMED)           # --covers needs a value
        expect(["absorb", "--covers", ""], EXIT_MALFORMED)       # empty covers refused
        expect(["absorb", "--bogus"], EXIT_MALFORMED)            # unknown arg

        # import verb ROUTING (OPF-IMPORT-VERB), judged on exit code only. These cases fail closed BEFORE
        # any store resolution, so they need no store on disk. A bare `import`, a token-parser error and a
        # duplicate mode are usage errors (exit 2); a well-parsed retired mode, whatever its companion
        # flags, meets the verb's retirement refusal (also exit 2; the refusal/usage split and the pointer
        # text are asserted in _import_leg below, the wiring discriminator).
        _VALID_RID = "imp-20260101T000000Z-0123456789abcdef"   # syntactically valid; names no staged run
        expect(["import"], EXIT_MALFORMED)                       # bare: exactly one mode required
        expect(["import", "--root", "."], EXIT_MALFORMED)        # --root but no mode
        expect(["import", "--set", "s.toml"], EXIT_MALFORMED)    # --set but no mode
        expect(["import", "--scan", "--plan", "--set", "s.toml"], EXIT_MALFORMED)  # two modes
        expect(["import", "--scan"], EXIT_MALFORMED)             # retired --scan: the refusal
        expect(["import", "--plan"], EXIT_MALFORMED)             # retired --plan: the refusal
        expect(["import", "--scan", "--set"], EXIT_MALFORMED)    # --set needs a value
        expect(["import", "--scan", "--set", "s.toml", "--actor", "x"], EXIT_MALFORMED)  # retired --scan: the refusal
        expect(["import", "--review"], EXIT_MALFORMED)           # --review needs a <run-id>
        expect(["import", "--review", _VALID_RID], EXIT_MALFORMED)   # retired --review: the refusal
        expect(["import", "--review", _VALID_RID, "--actor", "x"], EXIT_MALFORMED)  # retired --review: the refusal
        expect(["import", "--review", _VALID_RID, "--actor", "x", "--decisions", "d.json",
                "--interactive"], EXIT_MALFORMED)                # retired --review: the refusal
        expect(["import", "--review", _VALID_RID, "--actor", "", "--interactive"], EXIT_MALFORMED)  # empty actor
        expect(["import", "--review", "not-a-run-id", "--actor", "x", "--interactive"], EXIT_MALFORMED)  # the refusal
        expect(["import", "--apply", _VALID_RID, "--actor", "x"], EXIT_MALFORMED)   # retired --apply: the refusal
        expect(["import", "--apply", "not-a-run-id"], EXIT_MALFORMED)   # retired --apply: the refusal
        expect(["import", "--bogus", "--scan", "--set", "s.toml"], EXIT_MALFORMED)  # unknown arg
        expect(["import", "--root"], EXIT_MALFORMED)             # --root needs a value

        # record verb ROUTING (OPF-RECORD), judged on exit code only. These grammar cases fail closed in the
        # parser BEFORE any store resolution, so they need no store on disk; the recorded 0 / refusal 2
        # discrimination over real stores rides check_opf_record.py --self-test.
        expect(["record"], EXIT_MALFORMED)                       # bare: a subcommand is required
        expect(["record", "frobnicate"], EXIT_MALFORMED)         # unknown subcommand
        expect(["record", "transition", "BI-1", "done"], EXIT_MALFORMED)   # missing --actor
        expect(["record", "transition", "BI-1", "done/proposed", "--actor", "assistant"],
               EXIT_MALFORMED)                                   # the qualifier is derived, never given
        expect(["record", "transition", "PD-1", "decided", "--actor", "maintainer", "--decision", "x"],
               EXIT_MALFORMED)                                   # the bundle options come together
        expect(["record", "done-with-receipt", "BI-1"], EXIT_MALFORMED)    # missing --actor
        expect(["record", "done-with-receipt", "BI-1", "--actor", "assistant"],
               EXIT_MALFORMED)                                   # maintainer-only, refused before the store
        expect(["record", "create"], EXIT_MALFORMED)             # missing --type/--title/--actor
        expect(["record", "create", "--root"], EXIT_MALFORMED)   # --root needs a value
        expect(["record", "worklog-append", "--kind", "added", "--summary", "s", "--actor", "importer"],
               EXIT_MALFORMED)                                   # an importer never authors through record

        # adopt verb ROUTING (OPF-ADOPT K9a), judged on exit code AND, where the code alone would not
        # discriminate, the located message: a bare `adopt` was an UNKNOWN verb before this PR, so it
        # already exited 2 (with an unknown-verb message that even quotes 'adopt'); the usage case
        # therefore asserts the verb's OWN located prefix, which only the wired _cmd_adopt emits (the
        # verb-name keyword assertion -- reverting the dispatch flips it red on the message, not the
        # code). The grammar cases fail closed in the parser BEFORE any store or filesystem read, so they
        # need no store on disk; the read-only plan/status discrimination over real fixtures rides
        # _adopt_leg below.
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            rc = main(["adopt"])
        if rc != EXIT_MALFORMED or "opf adopt: give a subcommand" not in buf.getvalue():
            failures.append("bare adopt: rc={!r} (expected 2 + the located `opf adopt:` usage "
                            "message)".format(rc))
        expect(["adopt", "frobnicate"], EXIT_MALFORMED)             # unknown subcommand
        expect(["adopt", "status", "--root"], EXIT_MALFORMED)       # --root needs a value
        expect(["adopt", "status", "--root", ""], EXIT_MALFORMED)   # empty root refused
        expect(["adopt", "status", "--root", ".", "--root", "."], EXIT_MALFORMED)   # duplicate --root
        expect(["adopt", "status", "--bogus"], EXIT_MALFORMED)      # unknown arg
        expect(["adopt", "status", "--inputs", "w.toml"], EXIT_MALFORMED)   # --inputs is plan-only
        expect(["adopt", "plan"], EXIT_MALFORMED)                   # plan requires --inputs FILE
        expect(["adopt", "plan", "--root", "."], EXIT_MALFORMED)    # --root alone: still no --inputs
        expect(["adopt", "plan", "--inputs"], EXIT_MALFORMED)       # --inputs needs a value
        expect(["adopt", "plan", "--inputs", ""], EXIT_MALFORMED)   # empty inputs refused
        expect(["adopt", "plan", "--inputs", "w.toml", "--inputs", "w.toml"], EXIT_MALFORMED)  # duplicate
        # The four deferred subcommands are RECOGNIZED and refuse fail-closed (exit 2) BEFORE any parse,
        # store resolution or write, each with its own located not-yet-available message (never the
        # unknown-verb or unknown-subcommand message, and naming no command that does not exist); the K9b
        # engine lands them.
        for deferred_sub in ("approve", "apply", "complete", "reconcile"):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
                rc = main(["adopt", deferred_sub])
            if rc != EXIT_MALFORMED or "opf adopt {}: not yet available".format(
                    deferred_sub) not in buf.getvalue():
                failures.append("adopt {}: rc={!r} (expected 2 + the located not-yet-available "
                                "message)".format(deferred_sub, rc))

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
                # The CI floor (enforcement pack, U16): with --require-store the SAME NOT-ADOPTED root is a
                # located cannot-evaluate (exit 2), while the unflagged case just above still returns 0. The
                # message is asserted too, because deleting the flag would also exit 2 (as an unrecognized
                # argument), so the code alone would not discriminate. Reverting the flag's NOT-ADOPTED branch
                # returns 0 here, failing this case. A garbage store keeps its unflagged exit 2.
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
                    rc = main(["doctor", "--require-store", "--root", not_adopted])
                if rc != EXIT_MALFORMED or "--require-store was given but no OPF store" not in buf.getvalue():
                    failures.append("doctor --require-store over a NOT-ADOPTED root: rc={!r} (expected 2 + "
                                    "the located no-store message)".format(rc))
                expect(["doctor", "--require-store", "--root", broken], EXIT_MALFORMED)
                # upgrade over the same synthetic roots: a NOT-ADOPTED root reports NOT APPLICABLE and
                # returns 0 -- the wiring discriminator (reverting the upgrade route sends `upgrade` to the
                # fail-closed KNOWN_VERBS branch, which returns 2 here, failing this case); a garbage store
                # fails closed (exit 2). The full 1.0.0 -> 1.1.0 migration discrimination (schema delta,
                # canonical-bytes precondition, doctor-VALID gate, idempotence, and the above-tooling refusal)
                # rides check_opf_upgrade.py --self-test end to end over a byte-pinned committed 1.0.0 store.
                expect(["upgrade", "--root", not_adopted], EXIT_OK)
                expect(["upgrade", "--root", broken], EXIT_MALFORMED)
                # the upgrade parser accepts only one non-empty --root: the retired --homes-plan, a missing
                # or empty --root, a repeated --root and an unknown option each exit 2, and the directory and
                # file names under the fixture root after the loop match those before it (names only, compared
                # once; the current directory an argument-less run would use is not checked)
                tree_before = sorted((d, sorted(dn), sorted(fn)) for d, dn, fn in os.walk(base))
                for bad in (["--homes-plan"], ["--root"], ["--root", ""],
                            ["--root", not_adopted, "--root", not_adopted], ["--unknown"]):
                    expect(["upgrade"] + bad, EXIT_MALFORMED)
                if sorted((d, sorted(dn), sorted(fn)) for d, dn, fn in os.walk(base)) != tree_before:
                    failures.append("a refused upgrade argument changed the fixture tree")
                # absorb over the same synthetic roots: a NOT-ADOPTED root reports NOT APPLICABLE and returns
                # 0 -- the wiring discriminator (reverting the absorb route sends `absorb` to the fail-closed
                # KNOWN_VERBS branch, which returns 2 here, failing this case); a garbage store fails closed
                # (exit 2). The OK-draft discrimination over a resolved store rides _opf_absorb.self_test end
                # to end (it builds its own valid synthetic stores).
                expect(["absorb", "--root", not_adopted], EXIT_OK)
                expect(["absorb", "--root", broken], EXIT_MALFORMED)
            finally:
                shutil.rmtree(base, ignore_errors=True)
            return None

        def _import_leg():
            """Drive the retired import modes (spec 14.1) over a VALID synthetic store and a NOT-ADOPTED root,
            judged on exit codes, the refusal text AND observable side effects. Returns None on success or
            EXIT_MALFORMED on a harness (fixture I/O) error. Vectors: --scan, --plan, --review (batch and
            --interactive) and --apply each exit 2 with IMPORT_RETIRED over both roots and write nothing; a
            malformed or missing --set / --decisions file meets the same refusal (no input file is read), and
            so does a mode-specific argv violation (a missing or extra companion flag, a run-id outside the
            former grammar). Only a token-parser usage error (a flag missing its value, an unknown flag,
            the retired --show-review) and the exactly-one-mode rule exit 2 before it, without the refusal
            text. The pointer names adoption and the prompt pack in words and no command. Flip: routing
            `import` to the fail-closed KNOWN_VERBS branch, or restoring a mode-specific check or an input
            reader ahead of the refusal, turns rows red."""
            import re

            refusal = IMPORT_RETIRED
            _RID = "imp-20260101T000000Z-0123456789abcdef"   # the former run-id grammar; names no run

            def run_cli(argv):
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
                    rc = main(argv)
                return rc, buf.getvalue()

            def refused(argv, what):
                rc, out = run_cli(argv)
                if rc != EXIT_MALFORMED or refusal not in out:
                    failures.append("import {}: rc={!r} (expected 2 + the retirement refusal)".format(what, rc))

            def usage(argv, needle, what):
                rc, out = run_cli(argv)
                if rc != EXIT_MALFORMED or refusal in out or needle not in out:
                    failures.append("import {}: rc={!r} (expected the usage error {!r} at exit 2, before the "
                                    "refusal)".format(what, rc, needle))

            def tree_snapshot(rootdir):
                snap = {}
                for dirpath, dirs, files in os.walk(rootdir):
                    for name in dirs:
                        snap[os.path.relpath(os.path.join(dirpath, name), rootdir)] = None
                    for name in files:
                        p = os.path.join(dirpath, name)
                        with open(p, "rb") as fh:
                            snap[os.path.relpath(p, rootdir)] = fh.read()
                return snap

            try:
                ibase = tempfile.mkdtemp(prefix="opf-cli-import-")
            except OSError as exc:
                print("opf cli self-test: harness error: could not create the import fixture tempdir "
                      "({})".format(exc), file=sys.stderr)
                return EXIT_MALFORMED
            try:
                try:
                    store = os.path.join(ibase, "store")
                    machine = os.path.join(store, ".working", "toml")
                    os.makedirs(machine)
                    manifest = "\n".join([
                        "[opf]", 'standard = "opf"',
                        'spec_version = "{}"'.format(_opf_store.SUPPORTED_SPEC_VERSION),
                        'layout = "inline"', 'posture = "required"', 'import_status = "none"',
                        "", "[store]", 'sync_target = ""',
                        "", "[modules]", "governance = true",
                        "", "[types.backlog_item]", 'namespace = "BI"',
                        "", "[vendors]", 'registered = []', "",
                    ]) + "\n"
                    with open(os.path.join(machine, "manifest.toml"), "w", encoding="utf-8") as fh:
                        fh.write(manifest)
                    with open(os.path.join(machine, "counters.toml"), "w", encoding="utf-8") as fh:
                        fh.write("schema = 1\n\n[counters]\nBI = 0\nLF = 0\nWL = 0\n")
                    with open(os.path.join(store, "a.txt"), "w", encoding="utf-8") as fh:
                        fh.write("first source body\n")
                    inputs = {
                        "set.toml": 'schema = 1\nsource = ["a.txt"]\n',
                        "set-schema-true.toml": 'schema = true\nsource = ["a.txt"]\n',
                        "set-schema-float.toml": 'schema = 1.0\nsource = ["a.txt"]\n',
                        "set-badprop.toml": 'schema = 1\nsource = ["a.txt"]\nproposal = [5, 7]\n',
                        "not-toml.toml": "option = [\n",
                        "decisions.json": json.dumps({"schema": 1, "run_id": _RID, "decisions": []}),
                        "dec-schema-true.json": json.dumps({"schema": True, "run_id": _RID, "decisions": []}),
                        "not-json.json": "{",
                    }
                    for name, body in inputs.items():
                        with open(os.path.join(ibase, name), "w", encoding="utf-8") as fh:
                            fh.write(body)
                    not_adopted = os.path.join(ibase, "not-adopted")
                    os.mkdir(not_adopted)
                except OSError as exc:
                    print("opf cli self-test: harness error: could not build the import fixture "
                          "({})".format(exc), file=sys.stderr)
                    return EXIT_MALFORMED

                def path(name):
                    return os.path.join(ibase, name)

                before = tree_snapshot(ibase)
                # Every retired mode refuses over the adopted store and over a NOT-ADOPTED root.
                for label, top in (("store", store), ("not adopted", not_adopted)):
                    refused(["import", "--scan", "--set", path("set.toml"), "--root", top],
                            "--scan ({})".format(label))
                    refused(["import", "--plan", "--set", path("set.toml"), "--root", top],
                            "--plan ({})".format(label))
                    refused(["import", "--review", _RID, "--actor", "tester", "--decisions",
                             path("decisions.json"), "--root", top], "--review ({})".format(label))
                    real_stdin = sys.stdin
                    sys.stdin = io.StringIO("accept\n")
                    try:
                        refused(["import", "--review", _RID, "--actor", "tester", "--interactive", "--root", top],
                                "--review --interactive ({})".format(label))
                        consumed = sys.stdin.read() != "accept\n"
                    finally:
                        sys.stdin = real_stdin
                    if consumed:
                        failures.append("a refused import --review --interactive read stdin ({})".format(label))
                    refused(["import", "--apply", _RID, "--root", top], "--apply ({})".format(label))
                # No input file is read: a malformed or missing --set / --decisions file meets the refusal,
                # never a reader error.
                for name in ("set-schema-true.toml", "set-schema-float.toml", "set-badprop.toml",
                             "not-toml.toml", "missing.toml"):
                    refused(["import", "--scan", "--set", path(name), "--root", store], "--scan --set " + name)
                    refused(["import", "--plan", "--set", path(name), "--root", store], "--plan --set " + name)
                for name in ("dec-schema-true.json", "not-json.json", "missing.json"):
                    refused(["import", "--review", _RID, "--actor", "tester", "--decisions", path(name),
                             "--root", store], "--review --decisions " + name)
                # A mode-specific argv violation meets the refusal: the former mode-combination rules and
                # the run-id grammar check are retired with the modes.
                for argv, what in (
                        (["import", "--scan", "--root", store], "--scan without --set"),
                        (["import", "--plan", "--root", store], "--plan without --set"),
                        (["import", "--scan", "--set", path("set.toml"), "--actor", "x", "--root", store],
                         "--scan with --actor"),
                        (["import", "--review", _RID, "--root", store], "--review without --actor"),
                        (["import", "--review", _RID, "--actor", "tester", "--decisions", path("decisions.json"),
                          "--interactive", "--root", store], "--review with both --decisions/--interactive"),
                        (["import", "--apply", _RID, "--set", path("set.toml"), "--root", store],
                         "--apply with --set"),
                        (["import", "--apply", "not-a-run-id", "--root", store],
                         "--apply outside the run-id grammar"),
                ):
                    refused(argv, what)
                if tree_snapshot(ibase) != before:
                    failures.append("a refused import mode changed the fixture tree")
                # Only the token parser and the exactly-one-mode rule precede the refusal.
                usage(["import", "--scan", "--set", "--root", store], "requires a non-empty argument",
                      "--scan with a valueless --set")
                usage(["import", "--root", store], "give exactly one mode", "with no mode")
                usage(["import", "--scan", "--plan", "--set", path("set.toml")], "give exactly one mode",
                      "with two modes")
                usage(["import", "--show-review", "--review", _RID, "--root", store], "unrecognized argument",
                      "--show-review (the retired review aid)")
                usage(["import", "--plan", "--dispositions", path("not-toml.toml"), "--root", store],
                      "unrecognized argument", "--dispositions (the retired root-ingest planner)")
                # The pointer is to adoption and the prompt pack in words, naming no command (spec 14.1).
                if not ("adoption (OPF spec 14.1)" in refusal and "prompt pack" in refusal
                        and not re.search(r"`|\bopf [a-z]", refusal)):
                    failures.append("the import retirement refusal does not point to adoption and the "
                                    "prompt pack in words only")
            finally:
                shutil.rmtree(ibase, ignore_errors=True)
            return None

        def _adopt_leg():
            """Build product-root fixtures and drive the READ-ONLY adopt subcommands end to end, judged
            on exit codes, messages AND snapshot equality (the K9a no-write contract). Returns None on
            success or EXIT_MALFORMED on a harness (fixture I/O) error. Each 0/1 vector is a deliberate
            flip: reverting the adopt dispatch routes it to the fail-closed KNOWN_VERBS branch (returning
            2 where 0/1 is expected -- the wiring discriminator), and a subcommand that writes flips the
            byte-identical snapshot red. Vectors: `status` over a clean root -> 0 + the no-adoption-run
            message, byte-identical; `status` over a root with an OPEN adoption-journal transaction -> 1
            (finding), byte-identical (status reports, never reconciles); `status` over a completed engine
            transaction or a nothing-opened entry -> 0, over a run-id directory the bundle validator grades
            INVALID -> 1, and over a symlinked, dangling or wrong-type root, home or entry (a journal entry
            included), a hardlinked lock.break, or a `--root DIR/link/..` -> 2; with the journal path
            swapped for a symlink after its contained open, the product's own lock state (never the
            decoy's); with the evidence home swapped right after its listing, the ORIGINAL home's findings
            (read through the held home descriptor, K9a fix 4); with a transaction directory swapped for
            an empty decoy after the journal enumeration, still the interrupted-transaction finding
            (classified through the held txn descriptor, K9a fix 4); an unresolvable cwd -> 2;
            `plan` with a missing, FIFO, oversized, symlinked, symlinked-parent or hardlinked
            worksheet -> 2 (the bounded, single-link, fail-closed read boundary); `plan` with a STALE (well-formed, non-matching) observation
            digest -> 2 (the inventory binding refuses BEFORE any op validation), byte-identical; `plan`
            with the FRESH digest and one SCHEMA-VIOLATING op row (a known op missing its required
            inputs) -> 1 (INVALID rides _opf_adopt.validate_op through the wired planner, the 0/1
            discriminator past the digest binding), byte-identical. The VALID freeze discrimination rides
            _opf_adopt_plan.self_test end to end (it builds its own decision-complete fixtures)."""
            import subprocess
            import tomllib
            journal = _opf_adopt_apply._journal
            adopt_j_rel = _opf_adopt_apply.JOURNAL_REL
            adopt_rid = "adopt-20260101T000000Z-0123456789abcdef"
            evidence_rel = _opf_store.IMPORTED_REL + "/" + _opf_adopt_apply.KIND

            def run_adopt(argv):
                buf = io.StringIO()
                try:
                    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
                        return main(list(argv)), buf.getvalue()
                except BaseException as exc:            # a dispatcher crash is itself a failure
                    return "raised {!r}".format(exc), buf.getvalue()

            def tree_snapshot(rootdir):
                snap = dict()
                for dirpath, _dirs, files in os.walk(rootdir):
                    for name in files:
                        p = os.path.join(dirpath, name)
                        with open(p, "rb") as fh:
                            snap[os.path.relpath(p, rootdir)] = fh.read()
                return snap

            try:
                abase = tempfile.mkdtemp(prefix="opf-cli-adopt-")
            except OSError as exc:
                print("opf cli self-test: harness error: could not create the adopt fixture tempdir "
                      "({})".format(exc), file=sys.stderr)
                return EXIT_MALFORMED
            try:
                try:
                    clean = os.path.join(abase, "clean")
                    os.mkdir(clean)
                    with open(os.path.join(clean, "note.txt"), "w", encoding="utf-8") as fh:
                        fh.write("adopter content\n")
                    # debris: an OPEN transaction (INTENT without a terminal frame), published through the
                    # journal's own writer, so the engine's classification reads it as interrupted.
                    debris = os.path.join(abase, "debris")
                    os.makedirs(os.path.join(debris, adopt_j_rel))
                    debris_fd = os.open(debris, os.O_RDONLY | os.O_DIRECTORY)
                    try:
                        jr_fd = journal.open_journal_root_fd(debris_fd, adopt_j_rel)
                        try:
                            os.mkdir("txn", dir_fd=jr_fd)
                            journal.publish(jr_fd, Path(debris, adopt_j_rel, "txn"), journal.F_INTENT,
                                            {"txn": "txn", "ops": []})
                        finally:
                            os.close(jr_fd)
                    finally:
                        os.close(debris_fd)
                    # done: a COMPLETED engine transaction (its journal entry stays, and its bundle is VALID).
                    done = os.path.join(abase, "done")
                    os.mkdir(done)
                    _opf_adopt_apply.run_adopt_transaction(done, adopt_rid, lambda ops: None)
                    # an empty (nothing-opened) journal entry, which the engine classifies as clean.
                    unopened = os.path.join(abase, "unopened")
                    os.makedirs(os.path.join(unopened, adopt_j_rel, "txn"))
                except (OSError, journal.JournalError, _opf_adopt_apply.AdoptApplyError) as exc:
                    print("opf cli self-test: harness error: could not build the adopt fixtures "
                          "({})".format(exc), file=sys.stderr)
                    return EXIT_MALFORMED

                # status over a CLEAN root -> 0 + the no-adoption-run message, and the tree byte-unchanged.
                before = tree_snapshot(clean)
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
                    rc = main(["adopt", "status", "--root", clean])
                if rc != EXIT_OK or "no adoption run exists" not in buf.getvalue():
                    failures.append("adopt status over a clean root: rc={!r} (expected 0 + the "
                                    "no-adoption-run message)".format(rc))
                if tree_snapshot(clean) != before:
                    failures.append("adopt status mutated the product root (status must be a pure read)")

                # status over adoption-journal DEBRIS -> 1 (an interrupted transaction is a finding), and
                # the tree byte-unchanged (status reports, never reconciles).
                before = tree_snapshot(debris)
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
                    rc = main(["adopt", "status", "--root", debris])
                if rc != EXIT_FINDING or "interrupted transaction(s) txn" not in buf.getvalue():
                    failures.append("adopt status over journal debris: rc={!r} (expected 1 + the journal "
                                    "finding)".format(rc))
                if tree_snapshot(debris) != before:
                    failures.append("adopt status (journal debris) mutated the product root")

                # status reads both homes through the ENGINE (K9a fix 1), each vector red on the K9a head:
                # a COMPLETED engine transaction is clean and its bundle verifies (was 1, "any journal
                # entry is interrupted"); a nothing-opened entry is clean (was 1); a run-id directory the
                # bundle validator grades INVALID is a finding (was 0, "evidence bundle"); and a symlinked,
                # dangling or wrong-type root, home or entry is cannot-evaluate, never followed (was 0/1
                # over the followed or absent target) and never read as absent (was 0).
                def fresh_root(name):
                    path = os.path.join(abase, name)
                    os.mkdir(path)
                    with open(os.path.join(path, "note.txt"), "w", encoding="utf-8") as fh:
                        fh.write("adopter content\n")
                    return path

                try:
                    outside = os.path.join(abase, "outside")
                    os.makedirs(os.path.join(outside, "imported", _opf_adopt_apply.KIND, adopt_rid))
                    stray = os.path.join(outside, "stray")
                    os.mkdir(stray)
                    # a foreign tree whose only entries are NOT run ids: an enumeration that followed the
                    # link would report them as findings (exit 1) rather than refuse (exit 2).
                    foreign = os.path.join(abase, "foreign")
                    os.makedirs(os.path.join(foreign, "imported", _opf_adopt_apply.KIND, "stray"))
                    status_vectors = [("completed engine transaction", done, EXIT_OK,
                                       "adoption run {}: evidence bundle at".format(adopt_rid)),
                                      ("nothing-opened journal entry", unopened, EXIT_OK,
                                       "no adoption run exists")]
                    root = fresh_root("nobundle")
                    os.makedirs(os.path.join(root, evidence_rel, adopt_rid))
                    status_vectors.append(("run-id directory with no inventory", root, EXIT_FINDING,
                                           "has no inventory"))
                    root = fresh_root("filebundle")
                    os.makedirs(os.path.join(root, evidence_rel))
                    with open(os.path.join(root, evidence_rel, adopt_rid), "w", encoding="utf-8") as fh:
                        fh.write("not a bundle\n")
                    status_vectors.append(("regular file with a run-id name", root, EXIT_MALFORMED,
                                           "cannot evaluate"))
                    for label, rel, target in (
                            ("symlinked evidence home", evidence_rel, outside),
                            ("symlinked .working ancestor, run-id bundle outside", ".working", outside),
                            ("symlinked .working ancestor, foreign dir outside", ".working", foreign),
                            ("dangling evidence home", evidence_rel, os.path.join(abase, "nowhere")),
                            ("symlinked evidence entry", evidence_rel + "/stray", stray),
                            ("symlinked journal home", adopt_j_rel, outside),
                            ("dangling journal home", adopt_j_rel, os.path.join(abase, "nowhere")),
                            ("symlinked journal entry", adopt_j_rel + "/txn", stray)):
                        root = fresh_root("link-{}".format(len(status_vectors)))
                        os.makedirs(os.path.dirname(os.path.join(root, rel)), exist_ok=True)
                        os.symlink(target, os.path.join(root, rel))
                        status_vectors.append((label, root, EXIT_MALFORMED, "cannot evaluate"))
                    os.symlink(clean, os.path.join(abase, "rootlink"))
                    status_vectors.append(("symlinked --root", os.path.join(abase, "rootlink"),
                                           EXIT_MALFORMED, "cannot evaluate"))
                    # K9a fix 2, each red on the fix-1 head: a wrong-type journal entry is cannot-evaluate
                    # (was 0, skipped by the engine's enumeration) while the engine's own regular
                    # lock.break stays clean; and a `--root DIR/link/..`, which the kernel resolves to
                    # link's target's parent (here the debris root), refuses (was 0, abspath collapsed it
                    # lexically onto DIR) while a `..` through a real directory still resolves.
                    root = fresh_root("filejournalentry")
                    os.makedirs(os.path.join(root, adopt_j_rel))
                    with open(os.path.join(root, adopt_j_rel, "txn"), "w", encoding="utf-8") as fh:
                        fh.write("not a transaction\n")
                    status_vectors.append(("regular-file journal entry", root, EXIT_MALFORMED,
                                           "wrong-type entry is refused"))
                    root = fresh_root("lockbreakdir")
                    os.makedirs(os.path.join(root, adopt_j_rel, "lock.break"))
                    status_vectors.append(("directory named lock.break", root, EXIT_MALFORMED,
                                           "wrong-type entry is refused"))
                    root = fresh_root("lockbreakfile")
                    os.makedirs(os.path.join(root, adopt_j_rel))
                    open(os.path.join(root, adopt_j_rel, "lock.break"), "w", encoding="utf-8").close()
                    status_vectors.append(("regular lock.break arbitration file", root, EXIT_OK,
                                           "no adoption run exists"))
                    # K9a fix 4, red on the fix-3 head (which read this root as 0): a HARDLINKED
                    # lock.break is a second name for a foreign inode, refused by the strict journal
                    # enumeration's nlink==1 identity guard, never accepted as the engine's own file.
                    root = fresh_root("lockbreaklinked")
                    os.makedirs(os.path.join(root, adopt_j_rel))
                    victim = os.path.join(abase, "lockbreak-victim")
                    with open(victim, "w", encoding="utf-8") as fh:
                        fh.write("victim\n")
                    os.link(victim, os.path.join(root, adopt_j_rel, "lock.break"))
                    status_vectors.append(("hardlinked lock.break arbitration file", root, EXIT_MALFORMED,
                                           "hard links"))
                    os.mkdir(os.path.join(debris, "child"))
                    root = fresh_root("dotdot")
                    os.symlink(os.path.join(debris, "child"), os.path.join(root, "link"))
                    status_vectors.append(("--root DIR/link/.. (the debris root physically)",
                                           os.path.join(root, "link", ".."), EXIT_MALFORMED,
                                           "resolves physically"))
                    status_vectors.append(("--root with a .. through a real directory",
                                           os.path.join(clean, "..", "clean"), EXIT_OK,
                                           "no adoption run exists"))
                    # K9a fix 3, each red on the fix-2 head, whose guard compared collapsed REALPATHS
                    # only (physical == lexical admitted both): a `..` whose preceding component is a
                    # symlink is refused even when the link resolves INSIDE the root (the kernel still
                    # crossed a directory the operator never named), and a `..` after a component that
                    # does not exist is refused rather than collapsed away.
                    root = fresh_root("insidelink")
                    os.mkdir(os.path.join(root, "childdir"))
                    os.symlink(os.path.join(root, "childdir"), os.path.join(root, "inlink"))
                    status_vectors.append(("--root DIR/link/.. with the link resolving inside DIR",
                                           os.path.join(root, "inlink", ".."), EXIT_MALFORMED,
                                           "crosses `..` out of"))
                    root = fresh_root("missingdotdot")
                    status_vectors.append(("--root DIR/missing/..",
                                           os.path.join(root, "missing", ".."), EXIT_MALFORMED,
                                           "crosses `..` out of"))
                except OSError as exc:
                    print("opf cli self-test: harness error: could not build the adopt status fixtures "
                          "({})".format(exc), file=sys.stderr)
                    return EXIT_MALFORMED
                for label, root, want, needle in status_vectors:
                    rc, out = run_adopt(["adopt", "status", "--root", root])
                    if rc != want or needle not in out:
                        failures.append("adopt status over a {}: rc={!r} (expected {} + {!r})".format(
                            label, rc, want, needle))

                # the journal lock is read beneath the HELD contained journal-root fd (K9a fix 2), red on
                # the fix-1 head, which re-opened the journal by PATH: the journal path is swapped for a
                # symlink to a decoy right AFTER the contained open (a deterministic stand-in for a
                # concurrent writer), so a held product lock is still reported (was 0, the empty decoy)
                # and a decoy's lock never is (was 1, the decoy's pid).
                def lock_record(pid):
                    owner = dict(uid=os.getuid(), pid=pid, session="self-test", utc="2026-01-01T00:00:00Z")
                    owner["pid-start"] = ""
                    return json.dumps(owner).encode("utf-8")

                real_open_jr = journal.open_journal_root_fd
                race_results = []
                try:
                    for label, product_pid, decoy_pid, want, needle in (
                            ("held product lock, empty decoy", 1111, None, EXIT_FINDING, "(pid 1111)"),
                            ("no product lock, decoy lock", None, 4242, EXIT_OK, "no adoption run exists")):
                        root = fresh_root("race-{}".format(len(race_results)))
                        decoy = os.path.join(abase, "decoy-{}".format(len(race_results)))
                        os.makedirs(os.path.join(root, adopt_j_rel))
                        os.mkdir(decoy)
                        for where, pid in ((os.path.join(root, adopt_j_rel), product_pid), (decoy, decoy_pid)):
                            if pid is not None:
                                with open(os.path.join(where, "lock"), "wb") as fh:
                                    fh.write(lock_record(pid))

                        swap_fired = []

                        def racing_open(root_fd, rel, root=root, decoy=decoy, fired=swap_fired):
                            fd = real_open_jr(root_fd, rel)
                            fired.append(rel)   # the injection provably ran (K9a fix 4)
                            os.rename(os.path.join(root, rel), os.path.join(root, rel) + ".moved")
                            os.symlink(decoy, os.path.join(root, rel))
                            return fd

                        journal.open_journal_root_fd = racing_open
                        try:
                            race_results.append((label, run_adopt(["adopt", "status", "--root", root]), want,
                                                 needle, swap_fired))
                        finally:
                            journal.open_journal_root_fd = real_open_jr
                except OSError as exc:
                    print("opf cli self-test: harness error: could not build the adopt journal-swap fixtures "
                          "({})".format(exc), file=sys.stderr)
                    return EXIT_MALFORMED
                for label, (rc, out), want, needle, fired in race_results:
                    if rc != want or needle not in out or not fired:
                        failures.append("adopt status with the journal swapped after its contained open "
                                        "({}): rc={!r}, fired={!r} (expected {} + {!r} with the swap "
                                        "injected)".format(label, rc, bool(fired), want, needle))

                # K9a fix 4 (round-4 BLOCKER), red on the fix-3 head: the evidence home's directory
                # identity is HELD from the status listing through EVERY bundle verification, so a home
                # swapped onto `.working/imported/adoption` right after its listing is never re-resolved:
                # the report stays the ORIGINAL home's (its payload-drift finding, exit 1), never a
                # combination of the original's listing with the replacement's bundles (the fix-3 head
                # exited 0 here, reporting the replacement bundle as verified while the replacement's
                # own foreign entry went unlisted). Each home alone is a finding (exit 1), asserted
                # around the swap; the hook asserts its swap actually fired.
                try:
                    swaproot = fresh_root("homeswap")
                    home_abs = os.path.join(swaproot, evidence_rel)
                    payload_rel = evidence_rel + "/" + adopt_rid + "/payload.txt"
                    good_inv = _opf_adopt_apply.emit_inventory(
                        adopt_rid, [_opf_adopt_apply.inventory_row(payload_rel, b"GOOD!")])
                    os.makedirs(os.path.join(home_abs, adopt_rid))
                    with open(os.path.join(home_abs, adopt_rid, "inventory.toml"), "wb") as fh:
                        fh.write(good_inv)
                    with open(os.path.join(home_abs, adopt_rid, "payload.txt"), "wb") as fh:
                        fh.write(b"BAD!!")                       # drifted: the original home is exit 1
                    repl_abs = os.path.join(swaproot, ".working", "imported", "adoption-replacement")
                    os.makedirs(os.path.join(repl_abs, adopt_rid))
                    with open(os.path.join(repl_abs, adopt_rid, "inventory.toml"), "wb") as fh:
                        fh.write(good_inv)
                    with open(os.path.join(repl_abs, adopt_rid, "payload.txt"), "wb") as fh:
                        fh.write(b"GOOD!")                       # verifies, but beside a foreign entry
                    os.mkdir(os.path.join(repl_abs, "zzz-foreign"))
                except (OSError, _opf_adopt_apply.AdoptApplyError) as exc:
                    print("opf cli self-test: harness error: could not build the adopt home-swap fixtures "
                          "({})".format(exc), file=sys.stderr)
                    return EXIT_MALFORMED
                orig_rc, orig_out = run_adopt(["adopt", "status", "--root", swaproot])
                if orig_rc != EXIT_FINDING or "payload drift" not in orig_out:
                    failures.append("adopt status over the drifted original home: rc={!r} (expected 1 + "
                                    "the payload-drift finding)".format(orig_rc))
                home_swap_fired = []
                real_listdir = os.listdir

                def swapping_listdir(target):
                    names = real_listdir(target)
                    # the FIRST descriptor listing that surfaces the run id is the status home listing
                    # (bound to the held home descriptor); swap the homes right after it returns.
                    if isinstance(target, int) and adopt_rid in names and not home_swap_fired:
                        home_swap_fired.append(target)
                        os.rename(home_abs, home_abs + ".aside")
                        os.rename(repl_abs, home_abs)
                    return names

                os.listdir = swapping_listdir
                try:
                    swap_rc, swap_out = run_adopt(["adopt", "status", "--root", swaproot])
                finally:
                    os.listdir = real_listdir
                if swap_rc != EXIT_FINDING or "payload drift" not in swap_out or not home_swap_fired:
                    failures.append("adopt status with the evidence home swapped after its listing: "
                                    "rc={!r}, fired={!r} (expected 1 + the ORIGINAL payload-drift "
                                    "finding, read through the held home descriptor)".format(
                                        swap_rc, bool(home_swap_fired)))
                repl_rc, repl_out = run_adopt(["adopt", "status", "--root", swaproot])
                if repl_rc != EXIT_FINDING or "does not match the adoption run-id grammar" not in repl_out:
                    failures.append("adopt status over the replacement home (now at the pathname): "
                                    "rc={!r} (expected 1 + the foreign-entry finding)".format(repl_rc))

                # K9a fix 4 (round-4 BLOCKER), red on the fix-3 head: each journal transaction
                # directory's identity is HELD from the journal enumeration through classification and
                # its frame reads, so an interrupted transaction renamed aside right after the
                # enumeration and replaced by an empty decoy directory of the same name is STILL
                # classified from its own INTENT frames (exit 1, the interrupted-transaction finding),
                # never reopened by name and read as nothing-opened (the fix-3 head exited 0 here,
                # reporting that no adoption run exists). The hook asserts its swap actually fired.
                try:
                    txnswap = fresh_root("txnswap")
                    os.makedirs(os.path.join(txnswap, adopt_j_rel))
                    ts_fd = os.open(txnswap, os.O_RDONLY | os.O_DIRECTORY)
                    try:
                        ts_jr = journal.open_journal_root_fd(ts_fd, adopt_j_rel)
                        try:
                            os.mkdir("txn", dir_fd=ts_jr)
                            journal.publish(ts_jr, Path(txnswap, adopt_j_rel, "txn"), journal.F_INTENT,
                                            {"txn": "txn", "ops": []})
                        finally:
                            os.close(ts_jr)
                    finally:
                        os.close(ts_fd)
                except (OSError, journal.JournalError) as exc:
                    print("opf cli self-test: harness error: could not build the adopt txn-swap fixture "
                          "({})".format(exc), file=sys.stderr)
                    return EXIT_MALFORMED
                txn_swap_fired = []
                real_txn_dirs = journal._journal_txn_dirs

                def swapping_txn_dirs(*args, **kwargs):
                    res = real_txn_dirs(*args, **kwargs)
                    if not txn_swap_fired:
                        txn_swap_fired.append(True)
                        jdir = os.path.join(txnswap, adopt_j_rel)
                        os.rename(os.path.join(jdir, "txn"), os.path.join(txnswap, "txn.aside"))
                        os.mkdir(os.path.join(jdir, "txn"))    # an empty same-name decoy
                    return res

                journal._journal_txn_dirs = swapping_txn_dirs
                try:
                    ts_rc, ts_out = run_adopt(["adopt", "status", "--root", txnswap])
                finally:
                    journal._journal_txn_dirs = real_txn_dirs
                if (ts_rc != EXIT_FINDING or "interrupted transaction(s) txn" not in ts_out
                        or not txn_swap_fired):
                    failures.append("adopt status with the transaction directory swapped after the "
                                    "journal enumeration: rc={!r}, fired={!r} (expected 1 + the "
                                    "interrupted-transaction finding, classified through the held txn "
                                    "descriptor)".format(ts_rc, bool(txn_swap_fired)))

                # root normalization sits INSIDE the fail-closed handling: an unresolvable current
                # directory (os.getcwd raising, as it does once the cwd is deleted) is exit 2, never an
                # uncaught FileNotFoundError (red on the K9a head, where abspath ran outside every handler).
                real_getcwd = os.getcwd

                def _no_cwd():
                    raise FileNotFoundError(2, "simulated deleted current directory")

                os.getcwd = _no_cwd
                try:
                    cwd_results = [run_adopt(argv) for argv in (
                        ["adopt", "status"], ["adopt", "plan", "--inputs", "w.toml"])]
                finally:
                    os.getcwd = real_getcwd
                for rc, out in cwd_results:
                    if rc != EXIT_MALFORMED or "cannot resolve the product root" not in out:
                        failures.append("adopt with an unresolvable cwd: rc={!r} (expected 2 + the "
                                        "cannot-resolve message)".format(rc))

                # the physical-vs-lexical refusal NAMES the effective root (K9a fix 3): with --root
                # omitted the effective root is the current directory, so the refusal must name that
                # directory, never format the absent --root value (the fix-2 head printed 'the product
                # root None'). The cwd is simulated as a `..`-after-symlink path, the deterministic
                # stand-in for a cwd concurrently swapped for a symlink.
                fake_cwd = os.path.join(abase, "dotdot", "link", "..")
                os.getcwd = lambda: fake_cwd
                try:
                    fake_rc, fake_out = run_adopt(["adopt", "status"])
                finally:
                    os.getcwd = real_getcwd
                if fake_rc != EXIT_MALFORMED or fake_cwd not in fake_out or "None" in fake_out:
                    failures.append("adopt status refusal with --root omitted: rc={!r} (expected 2 "
                                    "+ a message naming the effective root {!r} and never None; got "
                                    "{!r})".format(fake_rc, fake_cwd, fake_out.strip()))

                # plan with a MISSING worksheet -> 2 (fail-closed read boundary, the --set class).
                expect(["adopt", "plan", "--inputs", os.path.join(abase, "absent.toml"),
                        "--root", clean], EXIT_MALFORMED)

                bindings_toml = "\n".join([
                    "[bindings]",
                    'revision = "' + "0" * 40 + '"',
                    'skip_policy = "no-skip"',
                    "[bindings.release]",
                    'version = "1.0.0"',
                    'manifest_sha256 = "' + "0" * 64 + '"',
                    'anchor = "https://example.invalid/hashes.txt"',
                    'anchor_sha256 = "' + "0" * 64 + '"',
                    "[bindings.prompt_pack]",
                    'version = "1.0.0"',
                    'digest = "sha256:' + "0" * 64 + '"',
                    "[[bindings.enforcement]]",
                    'platform = "github-actions"',
                    "means = []",
                    "members = []",
                    "residuals = []",
                    "",
                ])
                # plan with a STALE (well-formed, non-matching) digest -> 2: the inventory binding refuses
                # BEFORE any op validation, and the root stays byte-unchanged (plan is a pure read).
                stale = os.path.join(abase, "stale.toml")
                with open(stale, "w", encoding="utf-8") as fh:
                    fh.write('schema = 1\nproduct = "opf"\n'
                             'expected_observation_digest = "sha256:' + "0" * 64 + '"\n' + bindings_toml)
                before = tree_snapshot(clean)
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
                    rc = main(["adopt", "plan", "--inputs", stale, "--root", clean])
                if rc != EXIT_MALFORMED or "inventory changed" not in buf.getvalue():
                    failures.append("adopt plan with a stale observation digest: rc={!r} (expected 2 + "
                                    "the inventory-binding message)".format(rc))
                if tree_snapshot(clean) != before:
                    failures.append("adopt plan (stale digest) mutated the product root")

                # the --inputs read is BOUNDED (K9a fix 1), each vector red on the K9a head: a FIFO refuses
                # at once (the head blocked in open(), so it runs in an isolated child under a timeout and a
                # regression fails rather than hangs), an oversized worksheet refuses at the planner's byte
                # bound (the head read it whole), and a symlinked worksheet is refused, never followed (the
                # head followed it to the stale worksheet and reported that worksheet's digest mismatch).
                try:
                    fifo = os.path.join(abase, "worksheet.fifo")
                    os.mkfifo(fifo)
                    big = os.path.join(abase, "big.toml")
                    with open(big, "w", encoding="utf-8") as fh:
                        fh.write("schema = 1\n#" + "x" * _opf_adopt_plan.MAX_FILE_BYTES + "\n")
                    linked = os.path.join(abase, "linked.toml")
                    os.symlink(stale, linked)
                    linkdir = os.path.join(abase, "linkdir")
                    os.symlink(abase, linkdir)
                    # K9a fix 4: a hardlinked worksheet (two names, one inode) refuses BEFORE a byte is
                    # read; a separate source file so the other fixtures stay singly-linked.
                    hardsrc = os.path.join(abase, "hardlink-src.toml")
                    with open(hardsrc, "w", encoding="utf-8") as fh:
                        fh.write('schema = 1\nproduct = "opf"\n'
                                 'expected_observation_digest = "sha256:' + "0" * 64 + '"\n' + bindings_toml)
                    hardlinked = os.path.join(abase, "hardlinked.toml")
                    os.link(hardsrc, hardlinked)
                except OSError as exc:
                    print("opf cli self-test: harness error: could not build the adopt --inputs fixtures "
                          "({})".format(exc), file=sys.stderr)
                    return EXIT_MALFORMED
                try:
                    child = subprocess.run(
                        [sys.executable, "-I", "-B", os.path.abspath(__file__), "adopt", "plan", "--inputs",
                         fifo, "--root", clean],
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=60)
                    fifo_result = (child.returncode, child.stdout + child.stderr)
                except subprocess.TimeoutExpired:
                    fifo_result = ("timed out (blocked on the FIFO)", "")
                for label, (rc, out), needle in (
                        ("a FIFO", fifo_result, "not a regular file"),
                        ("an oversized", run_adopt(["adopt", "plan", "--inputs", big, "--root", clean]),
                         "-byte bound"),
                        # K9a fix 4, red on the fix-3 head (which read the 2-link worksheet and reported
                        # its stale digest): a multiply-linked worksheet refuses before a byte is read.
                        ("a hardlinked", run_adopt(["adopt", "plan", "--inputs", hardlinked,
                                                    "--root", clean]),
                         "hard links"),
                        ("a symlinked", run_adopt(["adopt", "plan", "--inputs", linked, "--root", clean]),
                         "worksheet unreadable"),
                        # K9a fix 2: a symlinked PARENT is refused too (red on the fix-1 head, whose
                        # final-component O_NOFOLLOW followed it to the stale worksheet's digest mismatch).
                        ("a symlinked-parent", run_adopt(["adopt", "plan", "--inputs",
                                                          os.path.join(linkdir, "stale.toml"), "--root", clean]),
                         "worksheet unreadable")):
                    if rc != EXIT_MALFORMED or needle not in out:
                        failures.append("adopt plan with {} worksheet: rc={!r} (expected 2 + {!r})".format(
                            label, rc, needle))

                # Round-5 defect 2 (K9a fix 5): a parent-directory close that reports an error inside
                # the --inputs open must not leak the just-opened worksheet fd. Inject the failure at
                # the REAL close (the number is still released, as on Linux); the read still fails
                # closed (ValueError -> the cannot-evaluate exit) and the worksheet fd is closed
                # afterwards, proven on the recorded fd itself.
                _wl_real_walk = _opf_store._open_dir_nofollow
                _wl_real_open = os.open
                _wl_real_close = os.close
                _wl_seen = {}

                def _wl_walk(path):
                    fd = _wl_real_walk(path)
                    _wl_seen["pfd"] = fd
                    return fd

                def _wl_open(*a, **kw):
                    fd = _wl_real_open(*a, **kw)
                    if kw.get("dir_fd") is not None and kw.get("dir_fd") == _wl_seen.get("pfd"):
                        _wl_seen["wfd"] = fd
                    return fd

                def _wl_close(fd):
                    _wl_real_close(fd)
                    if fd == _wl_seen.get("pfd") and "fired" not in _wl_seen:
                        _wl_seen["fired"] = True
                        raise OSError(5, "injected close failure")

                _opf_store._open_dir_nofollow = _wl_walk
                os.open = _wl_open
                os.close = _wl_close
                try:
                    try:
                        _adopt_read_inputs(stale)
                        _wl_out = "returned"
                    except ValueError:
                        _wl_out = "valueerror"
                    except OSError:
                        _wl_out = "oserror"
                finally:
                    os.close = _wl_real_close
                    os.open = _wl_real_open
                    _opf_store._open_dir_nofollow = _wl_real_walk
                if "fired" not in _wl_seen or "wfd" not in _wl_seen:
                    failures.append("adopt --inputs close-injection harness did not observe the "
                                    "parent walk, the worksheet open, or the injected close")
                else:
                    if _wl_out != "valueerror":
                        failures.append("adopt --inputs with a failing parent close: expected the "
                                        "fail-closed ValueError, got {}".format(_wl_out))
                    try:
                        os.fstat(_wl_seen["wfd"])
                        failures.append("adopt --inputs leaked the worksheet fd (fd {} still open "
                                        "after the parent close failed)".format(_wl_seen["wfd"]))
                    except OSError:
                        pass

                # plan with the FRESH digest and one SCHEMA-VIOLATING op row (a known op missing its
                # required inputs) -> 1: INVALID rides _opf_adopt.validate_op through the wired planner
                # (the 0/1 wiring discriminator past the digest binding), and the root stays byte-
                # unchanged. The worksheet digest comes from the same read-only investigation the planner
                # re-runs (the observation is deterministic over an unchanged tree).
                obs = _opf_adopt_plan.investigate(os.path.abspath(clean), sources=())
                if obs.status != _opf_store.VALID:
                    print("opf cli self-test: harness error: could not observe the adopt fixture "
                          "({})".format("; ".join(obs.findings)), file=sys.stderr)
                    return EXIT_MALFORMED
                fresh_digest = tomllib.loads(obs.observation.decode("utf-8"))["observation_digest"]
                badop = os.path.join(abase, "badop.toml")
                with open(badop, "w", encoding="utf-8") as fh:
                    fh.write('schema = 1\nproduct = "opf"\n'
                             'expected_observation_digest = "' + fresh_digest + '"\n'
                             '[[ops]]\nop = "init-store"\n' + bindings_toml)
                before = tree_snapshot(clean)
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
                    rc = main(["adopt", "plan", "--inputs", badop, "--root", clean])
                if rc != EXIT_FINDING or "missing required input" not in buf.getvalue():
                    failures.append("adopt plan with a schema-violating op row: rc={!r} (expected 1 + the "
                                    "validate_op finding)".format(rc))
                if tree_snapshot(clean) != before:
                    failures.append("adopt plan (schema-violating op) mutated the product root")
            finally:
                shutil.rmtree(abase, ignore_errors=True)
            return None

        harness_rc = _fixture_leg()
        if harness_rc is not None:
            return harness_rc
        import_rc = _import_leg()
        if import_rc is not None:
            return import_rc
        adopt_rc = _adopt_leg()
        if adopt_rc is not None:
            return adopt_rc

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
        print("opf cli self-test: PASS (verb routing: unknown/unwired verbs and render/doctor/import usage "
              "errors fail closed; render --check forwards to the U4 engine; doctor resolves + validates a "
              "store, NOT-ADOPTED -> 0 (2 with --require-store) and a garbage store -> 2; "
              "import surfaces the retired --scan / --plan / --review / --apply refusal (spec 14.1) at exit 2 "
              "over a NOT-ADOPTED root and over an adopted store, each mutating nothing, before any input "
              "file is read AND before any mode-specific argv validation (a malformed or missing --set / "
              "--decisions file, a missing or extra companion flag, and a run-id outside the former grammar "
              "each meet the refusal; only a token-parser usage error or the exactly-one-mode rule precedes "
              "it); "
              "adopt (K9a) wires the read-only plan/status subcommands onto the "
              "adoption planner -- bare/malformed usage and the deferred approve/apply/complete/reconcile "
              "fail closed to exit 2, status -> 0 no-run or verified run / 1 open-transaction or invalid-"
              "bundle finding / 2 symlinked, dangling or wrong-type home, plan -> 2 missing, FIFO, oversized "
              "or symlinked worksheet or stale digest / 1 schema-violating op, each mutating nothing; an "
              "unresolvable cwd -> 2; fixture-setup and fixture-I/O OSError fail closed to exit 2)")
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
    ("opf-observe", _opf_observe.self_test),
    ("opf-absorb", _opf_absorb.self_test),
    ("opf-record", _opf_record.self_test),
    ("opf-adopt-apply", _opf_adopt_apply.self_test),
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
KNOWN_VERBS = ("init", "adopt", "import", "doctor", "render", "migrate", "sync", "upgrade", "absorb",
               "record")


# Helper self-tests that pin sys.set_int_max_str_digits(4300) inside a fixture and MUST restore the ambient
# value in a finally (the round-7 int-limit hermeticity work). run_self_tests guards that RESTORE half below.
_INT_LIMIT_SELF_TESTS = frozenset({"opf-release", "opf-emit", "opf-schema", "opf-fuzz"})


def run_self_tests(tests=None):
    """Keep caller HOME/XDG out of fixture reads, including in-process production helpers."""
    import tempfile
    from unittest.mock import patch
    with tempfile.TemporaryDirectory(prefix="opf-selftest-home-") as home:
        with patch.dict(os.environ, HOME=home, XDG_CONFIG_HOME=home,
                        GIT_CONFIG_NOSYSTEM="1"):
            return run_self_tests_isolated(tests)


def run_self_tests_isolated(tests=None):
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
    if verb == "import":
        return _cmd_import(rest)
    if verb == "absorb":
        return _cmd_absorb(rest)
    if verb == "record":
        return _cmd_record(rest)
    if verb == "adopt":
        return _cmd_adopt(rest)
    if verb in KNOWN_VERBS:
        # A recognized verb whose unit has not landed: fail closed (exit 2), never a silent success, so
        # a stub is never mistaken for a completed operation.
        print("opf {}: not yet implemented in this build (fail-closed)".format(verb), file=sys.stderr)
        return EXIT_MALFORMED
    print("opf: unknown verb {!r}; known verbs: {}".format(verb, ", ".join(KNOWN_VERBS)), file=sys.stderr)
    return EXIT_MALFORMED


if __name__ == "__main__":
    sys.exit(main())
