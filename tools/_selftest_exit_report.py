#!/usr/bin/env python3
"""The child side of the selftest-execution gate's fail-closed contract: a registered suite's execution
report is FINALIZED at interpreter exit, after the child's own cleanup, never written in band.

A report written in band, before the interpreter shuts down, cannot see a fault that happens later
(an atexit callback, a destructor run by the shutdown collection, a thread joined at shutdown), so a
run that faults after its last check() still delivered a passing report. The gate
(tools/check_selftest_execution.py) instead requires a zero exit, the finalized report, and an empty
error stream; this module produces the finalized report:

  arm(report_path, suite_id, executed)   register the finalizer; it must be the FIRST atexit
                                         registration of the process (atexit runs last-registered
                                         first, so the first registration runs after every other
                                         callback); a later arm, or one made after any other
                                         registration, is a harness error (exit 2)
  exit_with(code)                        record the suite's intended exit status, then sys.exit(code)

At exit the interpreter joins every non-daemon thread (a thread fault reaches stderr through
threading.excepthook), runs every atexit callback registered after arm (a callback fault reaches
stderr as an ignored exception), and only then runs the finalizer, which runs a bounded garbage
collection (a destructor fault in collectable garbage reaches stderr through sys.unraisablehook),
flushes stdout and stderr, writes the report with exclusive creation (a report already present,
for example one written in band, is refused), and ends the process with os._exit, so no later
interpreter teardown can run code after the report exists. A status that exit_with never recorded
(an uncaught exception, a sys.exit that bypassed it) writes NO report and exits 2; any failure inside
the finalizer exits 2. The report is
{"format_version": 2, "suite": ..., "check_ids": [...], "exit_code": <the os._exit status>,
"finalized": true}.

DISCLOSED RESIDUAL: os._exit skips interpreter teardown, so a destructor of an object still reachable
at exit never runs (its fault never happens, and neither does its effect); a daemon thread still
running is killed, and one that faults between the finalizer's flush and os._exit can lose its
message; and code running inside the child can replace the reporting machinery itself (sys.stderr,
sys.unraisablehook, threading.excepthook, os._exit, or this module's state), which the gate cannot
see from outside: that channel is closed only by the suite's own source being reviewed and pinned.
"""
import atexit
import gc
import json
import os
import sys

FORMAT_VERSION = 2
GC_PASSES = 3            # the bounded collection: stop early once a pass frees nothing
HARNESS_ERROR = 2
_STATE = {"armed": False, "code": None}


def _harness_error(message):
    try:
        print("SELF-TEST HARNESS ERROR: {}".format(message), file=sys.stderr)
        sys.stderr.flush()
    except BaseException:  # the exit status still carries the error
        pass


def _finalize(report_path, suite_id, executed):
    """The exit handler: never returns. Every path ends in os._exit."""
    status = HARNESS_ERROR
    try:
        for _ in range(GC_PASSES):
            if gc.collect() == 0:
                break
        code = _STATE["code"]
        sys.stdout.flush()
        sys.stderr.flush()
        if code is None:
            _harness_error("no exit status was recorded through exit_with(); no execution report "
                           "is written")
        else:
            with open(report_path, "x", encoding="utf-8") as handle:
                json.dump({"format_version": FORMAT_VERSION, "suite": suite_id,
                           "check_ids": list(executed), "exit_code": code, "finalized": True},
                          handle)
                handle.write("\n")
            status = code
    except BaseException as exc:
        status = HARNESS_ERROR
        _harness_error("cannot finalize execution report {}: {!r}".format(report_path, exc))
    finally:
        os._exit(status)


def arm(report_path, suite_id, executed):
    """Register the finalizer as the process's first atexit callback, or exit 2."""
    if _STATE["armed"] or atexit._ncallbacks() != 0:
        _harness_error("the execution-report finalizer must be the first atexit registration")
        sys.exit(HARNESS_ERROR)
    _STATE["armed"] = True
    atexit.register(_finalize, report_path, suite_id, executed)


def exit_with(code):
    """Record the intended exit status (an int in 0..255; None is 0, as for sys.exit) for the
    finalizer, then sys.exit with it. Any other value records nothing, so an armed run exits 2."""
    if code is None:
        code = 0
    if type(code) is int and 0 <= code <= 255:
        _STATE["code"] = code
    sys.exit(code)
