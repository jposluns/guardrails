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
    """Guard F2 (round-10, class-width): the FIFO-probe watchdogs in _opf_changelog / _opf_views /
    _opf_store must survive a HOSTILE ambient SIGALRM state and leave it exactly as they found it. The
    hostile ambient is SIGALRM BLOCKED with an already-fired (PENDING) alarm AND an armed ITIMER_REAL -
    the exact "timer already fired" state. Pre-fix each watchdog unblocked SIGALRM OUTSIDE its try/finally,
    so the pending alarm was delivered the instant SIGALRM unblocked, raised out of the self-test uncaught,
    AND left SIGALRM unblocked (a corrupted caller mask). This runs each affected self-test under that exact
    ambient and asserts a clean, state-restored outcome (rc 0; caller mask, SIGALRM disposition, and interval
    timer all restored); reverting any one site's try/finally (or its pending-drain) re-reds it. Returns 0
    clean, 1 on a failure. On a platform without POSIX SIGALRM/itimer the watchdogs no-op, so this SKIPS
    clean. Runs the affected self-tests a second time (once here under the hostile ambient, once in the
    registry under the default ambient), the price of exercising the real sites rather than a copy."""
    import os as _os
    import io as _io
    import signal as _signal
    import contextlib as _ctx
    if not (hasattr(_signal, "pthread_sigmask") and hasattr(_signal, "setitimer")
            and hasattr(_signal, "SIGALRM") and hasattr(_signal, "ITIMER_REAL")):
        print("opf watchdog hostile-ambient self-test: SKIP (no POSIX SIGALRM/itimer on this platform)")
        return EXIT_OK
    affected = (("opf-changelog", _opf_changelog.self_test),
                ("opf-views", _opf_views.self_test),
                ("opf-store", _opf_store.self_test))

    def _benign(_s, _f):                                          # a caller handler the watchdog must restore
        pass

    ok = True
    for label, fn in affected:
        _prev_disp = _signal.getsignal(_signal.SIGALRM)
        _prev_mask = _signal.pthread_sigmask(_signal.SIG_BLOCK, set())
        _prev_val, _prev_int = _signal.getitimer(_signal.ITIMER_REAL)
        try:
            # Hostile ambient: install a benign handler, ARM a long ITIMER the watchdog must preserve, BLOCK
            # SIGALRM, then self-signal so a SIGALRM is left PENDING-and-BLOCKED (timer already fired).
            _signal.signal(_signal.SIGALRM, _benign)
            _signal.setitimer(_signal.ITIMER_REAL, 3600.0)
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
            _val_after, _ = _signal.getitimer(_signal.ITIMER_REAL)
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
            if _val_after <= 0.0:
                print("opf watchdog self-test: {}: did not restore the caller's armed ITIMER_REAL".format(
                    label), file=sys.stderr)
                ok = False
        finally:
            _signal.setitimer(_signal.ITIMER_REAL, 0)             # disarm the fixture timer
            _signal.signal(_signal.SIGALRM, _signal.SIG_IGN)     # discard any still-pending SIGALRM
            _signal.signal(_signal.SIGALRM, _prev_disp)           # restore the real caller disposition
            _signal.pthread_sigmask(_signal.SIG_SETMASK, _prev_mask)
            if _prev_val > 0.0:
                _signal.setitimer(_signal.ITIMER_REAL, _prev_val, _prev_int)
    if not ok:
        print("opf watchdog hostile-ambient self-test: FAIL (a FIFO-probe watchdog did not survive a "
              "blocked+pending ambient SIGALRM with state restored)", file=sys.stderr)
        return EXIT_FINDING
    print("opf watchdog hostile-ambient self-test: PASS (changelog/views/store watchdogs survive a "
          "blocked+pending SIGALRM with mask, disposition, and timer restored)")
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
