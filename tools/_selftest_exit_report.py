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
                                         registration, is a harness error (exit 2). It also wraps
                                         threading.excepthook so that a thread ending in any
                                         exception other than a clean SystemExit (code None or 0),
                                         including a silent SystemExit(1), is recorded as a fault;
                                         installs an audit hook (which cannot be removed) that
                                         records as a fault every thread started through _thread
                                         directly, outside threading (by any name, including an
                                         alias bound before arm, and with any target, including a C
                                         callable: _thread ignores such a thread's SystemExit
                                         silently, so its exit status cannot be observed), and every
                                         thread started after the exit-time thread join (it is never
                                         joined); and wraps threading._shutdown, the exit-time
                                         join, to mark when it has finished
  exit_with(code)                        record the suite's intended exit status and raise it as
                                         the process's exit; the status stands only if that very
                                         exception ends the process: the interpreter reads its code
                                         on the main thread, outside every Python frame, before the
                                         exit-time thread join, and only such a read of the most
                                         recent exit_with exception confirms it (a read inside a
                                         frame, on another thread, or after the join, for example
                                         by a C callable run as an atexit callback or as a _thread
                                         target, confirms nothing), so a caught exit_with followed
                                         by any other exit, and an earlier exit_with whose
                                         exception outlives a later caught one, are harness errors

At exit the interpreter joins every non-daemon thread (a thread fault reaches stderr through
threading.excepthook, and the wrapper above records it), runs every atexit callback registered after
arm (a callback fault reaches stderr as an ignored exception), and only then runs the finalizer, which
records as a fault an atexit callback registered after the exit-time join (one registered while the
exit callbacks run is never run, so its fault could never surface), runs a bounded garbage collection
(at most GC_PASSES passes, stopping once a pass frees nothing; a destructor fault in garbage those
passes free reaches stderr through sys.unraisablehook, while a chain
whose destructors keep creating new cyclic garbage beyond the bound is never collected, as at plain
interpreter shutdown, where such a chain is equally silent), flushes stdout and stderr, writes the
report with exclusive creation (a report already present, for example one written in band, is
refused), and ends the process with os._exit, so no later interpreter teardown can run code after the
report exists. NO report is written, and the process exits 2, when exit_with never recorded a status
(an uncaught exception, a sys.exit that bypassed it), when the recorded status is not the one the
process exited with, when a thread or registration fault was recorded, when the exit-time thread join
was never observed, or when anything inside the finalizer fails.
The report is {"format_version": 2, "suite": ..., "check_ids": [...], "exit_code": <the os._exit
status>, "finalized": true}.

DISCLOSED RESIDUAL: os._exit skips interpreter teardown, so a destructor of an object still reachable
at exit never runs (its fault never happens, and neither does its effect); a daemon thread still
running is killed, and one that faults between the finalizer's flush and os._exit can lose its
message; a thread started before arm is outside the audit hook (arm is the first statement of every
registered runner's entry point, so only import-time code precedes it); and an atexit callback
registered after the exit-time join is counted, not identified, so one that also unregisters another
callback can keep the count unchanged. Loaded code replacing the reporting machinery (including
this module's state, threading._shutdown, or threading.Thread._bootstrap) is the gate's one
in-process residual, disclosed there.
"""
import atexit
import gc
import json
import os
import sys
import threading

FORMAT_VERSION = 2
GC_PASSES = 3            # the bounded collection: stop early once a pass frees nothing
HARNESS_ERROR = 2
_THREAD_START_EVENTS = ("_thread.start_new_thread", "_thread.start_joinable_thread")
_BOOTSTRAP = threading.Thread._bootstrap   # every thread threading starts runs this bound method
_STATE = {"armed": False, "code": None, "raised": None, "confirmed": None, "joined": False,
          "callbacks_at_join": None, "main_ident": None, "faults": [], "thread_hook": None}


class _SuiteExit(SystemExit):
    """The SystemExit that exit_with raises. When it ends the process the interpreter reads its code on
    the main thread with no Python frame on the stack, before the exit-time thread join; only such a
    read confirms this instance (a handler that catches it and reads the code runs inside a frame, and
    a C callable reading it as an atexit callback or as a _thread target runs after the join or on
    another thread, so neither confirms anything). The finalizer accepts the status only when the
    confirmed instance is the one the most recent exit_with raised."""

    @property
    def code(self):
        try:
            sys._getframe(1)
        except ValueError:
            if not _STATE["joined"] and threading.get_ident() == _STATE["main_ident"]:
                _STATE["confirmed"] = self
        return SystemExit.__dict__["code"].__get__(self, SystemExit)


def _harness_error(message):
    try:
        print("SELF-TEST HARNESS ERROR: {}".format(message), file=sys.stderr)
        sys.stderr.flush()
    except BaseException:  # the exit status still carries the error
        pass


def _clean_status(code):
    return code is None or (type(code) is int and code == 0)


def _thread_hook(args):
    """threading.excepthook while armed: record every thread exception except a clean SystemExit, then
    defer to the hook it replaced (which still writes the traceback to stderr)."""
    exc = args.exc_value
    if not (isinstance(exc, SystemExit) and _clean_status(exc.code)):
        name = args.thread.name if args.thread is not None else "<unknown>"
        _STATE["faults"].append("thread {} ended with {!r}".format(name, exc))
    _STATE["thread_hook"](args)


def _audit(event, args):
    """The audit hook while armed: record a thread started outside threading, or after the exit-time
    join, as a fault. It never raises (a raising hook would abort the audited operation instead)."""
    if event not in _THREAD_START_EVENTS or not _STATE["armed"]:
        return
    try:
        if _STATE["joined"]:
            _STATE["faults"].append("a thread was started after the exit-time thread join, so it is "
                                    "never joined ({})".format(event))
        elif getattr(args[0], "__func__", None) is not _BOOTSTRAP:
            _STATE["faults"].append("a thread was started through {} directly, outside threading, so "
                                    "its exit status cannot be observed".format(event))
    except BaseException:
        _STATE["faults"].append("a thread start ({}) could not be classified".format(event))


def _wrap_shutdown(original):
    """threading._shutdown while armed: the interpreter's exit-time join of non-daemon threads. Once it
    has finished, a status read confirms nothing and a new thread or atexit registration is a fault."""
    def _shutdown():
        try:
            return original()
        finally:
            _STATE["joined"] = True
            _STATE["callbacks_at_join"] = atexit._ncallbacks()
    return _shutdown


def _finalize(report_path, suite_id, executed):
    """The exit handler: never returns. Every path ends in os._exit."""
    status = HARNESS_ERROR
    try:
        for _ in range(GC_PASSES):
            if gc.collect() == 0:
                break
        code = _STATE["code"]
        if _STATE["joined"] and atexit._ncallbacks() > _STATE["callbacks_at_join"]:
            _STATE["faults"].append("an atexit callback was registered after the exit-time thread "
                                    "join, so it never runs")
        sys.stdout.flush()
        sys.stderr.flush()
        if _STATE["faults"]:
            _harness_error("{}; no execution report is written".format("; ".join(_STATE["faults"])))
        elif code is None or _STATE["raised"] is None:
            _harness_error("no exit status was recorded through exit_with(); no execution report "
                           "is written")
        elif not _STATE["joined"]:
            _harness_error("the exit-time thread join was never observed; no execution report is "
                           "written")
        elif _STATE["confirmed"] is not _STATE["raised"]:
            _harness_error("the status recorded through exit_with() is not the one the process "
                           "exited with (its exit was caught); no execution report is written")
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
    """Register the finalizer as the process's first atexit callback and record thread faults, or
    exit 2. Arming twice is refused before anything is installed."""
    if _STATE["armed"] or atexit._ncallbacks() != 0:
        _harness_error("the execution-report finalizer must be the first atexit registration")
        sys.exit(HARNESS_ERROR)
    _STATE["armed"] = True
    _STATE["main_ident"] = threading.main_thread().ident
    _STATE["thread_hook"] = threading.excepthook
    threading.excepthook = _thread_hook
    threading._shutdown = _wrap_shutdown(threading._shutdown)
    sys.addaudithook(_audit)
    atexit.register(_finalize, report_path, suite_id, executed)


def exit_with(code):
    """Record the intended exit status (an int in 0..255; None is 0, as for sys.exit) for the
    finalizer, then exit with it; the status stands only if this exit ends the process. Any other
    value, or a call outside the main thread, records nothing, so an armed run exits 2. Unarmed, this
    is sys.exit."""
    if code is None:
        code = 0
    if not _STATE["armed"]:
        sys.exit(code)
    _STATE["code"] = None
    _STATE["raised"] = None
    if threading.current_thread() is not threading.main_thread():
        _STATE["faults"].append("exit_with() was called outside the main thread")
        sys.exit(code)
    if type(code) is int and 0 <= code <= 255:
        exc = _SuiteExit(code)
        _STATE["code"] = code
        _STATE["raised"] = exc
        raise exc
    sys.exit(code)
