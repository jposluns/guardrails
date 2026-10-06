#!/usr/bin/env python3
"""The child side of the selftest-execution gate's fail-closed contract: a registered suite's execution
report is FINALIZED at interpreter exit, after the child's own cleanup, never written in band.

A report written in band, before the interpreter shuts down, cannot see a fault that happens later
(an atexit callback, a destructor run by the shutdown collection, a thread joined at shutdown), so a
run that faults after its last check() still delivered a passing report. The gate
(tools/check_selftest_execution.py) instead requires the finalized report, a child exit status in
{0, 1} that the report's exit_code must equal, and an empty error stream; this module produces the
finalized report:

  _bootstrap_main()                      the entry point of every gate launch ([python, -I, -B, -c,
                                         <shim>, <this module's directory>, <runner>, <report>]):
                                         install the observation hooks below, then run the
                                         registered runner as __main__ with [<runner>,
                                         --execution-report, <report>] as its argv; NO runner
                                         statement, import-time included, precedes the hooks, so
                                         there is no pre-arm window under the gate; the
                                         runner's module is the real sys.modules["__main__"]
                                         for the whole run, exit handlers included, compiled
                                         from its source as a direct launch compiles it
  arm(report_path, suite_id, executed)   register the finalizer; it must be the FIRST atexit
                                         registration of the process (atexit runs last-registered
                                         first, so the first registration runs after every other
                                         callback); a later arm, one made after any other
                                         registration, or one made in a process whose observation
                                         hooks were not installed at start (a launch that bypassed
                                         _bootstrap_main) is a harness error (exit 2), so a
                                         finalized report always attests whole-process observation
  exit_with(code)                        record the suite's intended exit status and raise it as
                                         the process's exit; the status stands only if that very
                                         exception ends the process: the interpreter reads its code
                                         on the main thread, outside every Python frame, before the
                                         exit-time thread join, and only such a read of the most
                                         recent exit_with exception confirms it; the confirmation
                                         gates the WRITING of the report, never the exit status
                                         itself, which the finalizer leaves to the interpreter (see
                                         below), so mimicking that read (a C callable run with no
                                         frame on the stack, for example a weakref callback) at
                                         most produces a report whose exit_code the child's real
                                         exit status then contradicts at the gate

THE OBSERVATION HOOKS, installed by _bootstrap_main before any suite code: threading.excepthook is
wrapped so that a thread ending in any exception other than a clean SystemExit (code None or 0),
including a silent SystemExit(1), is recorded as a fault whenever it ends, before or after arm; an
audit hook (which cannot be removed) records as a fault every thread started through _thread
directly, outside threading, at any moment from installation on (by any name and with any target,
including a C callable: _thread ignores such a thread's SystemExit silently, so its exit status
cannot be observed; a start is threading's own only when its target is exactly a bound method
whose function is threading.Thread._bootstrap and whose instance is a threading.Thread, read through
the method type's own slots, so no attribute the target supplies is trusted), every thread started
after the exit-time thread join (it is never joined), and every import of an interpreter-creating
module (_interpreters, _xxsubinterpreters, _testcapi, _testinternalcapi; the name is compared as an
exact str, so a str subclass's own hashing or equality cannot hide it): every hook here is
per-interpreter, so code in another interpreter is invisible to all of them, and the import of the
machinery that could create one is therefore itself the fault; and threading._shutdown, the
exit-time join, is wrapped to mark when it has finished. A hook that meets a fault records it FIRST,
with a fixed message that runs no code the suite controls, and only then attempts a richer
description (an exception's repr, a thread's name) inside a guard whose own failure is recorded
too, so a raising __repr__ or code property cannot erase the fault; a thread's end is classified
without running such code (by the exception's real type and SystemExit's own code slot).

THE HOOKS OUTLIVE THE MODULE'S GLOBALS: interpreter teardown clears module globals (setting them
to None) while the audit hook is still installed and late destructors still run, so a hook that
looked a global up at call time would itself fault there, and its "Exception ignored" would refuse
a run whose late code was clean (for example a destructor that opens a file or audits an event).
Every hook and the finalizer (_audit, _genuine_bootstrap, _thread_hook, _clean_status,
_SuiteExit.code, the _shutdown wrapper, _harness_error, _finalize) therefore binds, when it is
defined, every object it reads, builtins included, as a keyword-only default (the _shutdown wrapper
reads its factory's through its closure), and looks up no global or builtin name and imports
nothing at call time (an import at teardown fails once sys.meta_path is cleared). A faulting late
destructor reaches stderr through sys.unraisablehook, and is refused, only while sys.stderr still
exists: CPython drops sys.stderr (and sys.__stderr__) before it clears some interpreter-level state,
so the destructor of an object whose last reference that state holds runs and raises at late teardown
with nothing written to stderr, and the run passes (DISCLOSED RESIDUAL, below).

At exit the interpreter joins every non-daemon thread (a thread fault reaches stderr through
threading.excepthook, and the wrapper above records it), runs every atexit callback registered after
arm (a callback fault reaches stderr as an ignored exception), and only then runs the finalizer, which
records as a fault an atexit callback registered after the exit-time join (one registered while the
exit callbacks run is never run, so its fault could never surface; the test compares the callback
COUNT with the one taken at the join, because atexit exposes only the count, atexit._ncallbacks,
never the registered callables, and a wrapped atexit.register is no sound identity record, since a
fresh import of atexit returns the unwrapped one; so an atexit.unregister made after the join masks
an equal number of later registrations, disclosed below), runs a bounded garbage collection
(at most GC_PASSES passes, stopping once a pass frees nothing; a destructor fault in garbage those
passes free reaches stderr through sys.unraisablehook, while a chain
whose destructors keep creating new cyclic garbage beyond the bound is never collected, as at plain
interpreter shutdown, where such a chain is equally silent), flushes stdout and stderr, and writes the
report with exclusive creation (a report already present, for example one written in band, is
refused), and then drops its references to the exit exception (whose traceback would otherwise
keep the runner's frame and module globals alive past module teardown, so that a faulting cycle
anchored on a module global or class attribute was freed only after sys.stderr is dropped, and
passed silently). Every REFUSING path ends the process immediately with os._exit(2) and writes NO report:
when exit_with never recorded a status (an uncaught exception, a sys.exit that bypassed it), when
the recorded status is not the one the process exited with, when a thread, import, or registration
fault was recorded, when the exit-time thread join was never observed, or when anything inside the
finalizer fails. The REPORTING path instead RETURNS after writing the report, so the process ends
with the interpreter's OWN exit status, the one carried by the exception that really ended the
process; the gate refuses a report whose exit_code differs from the child's real exit status, so a
forged confirmation changes nothing unless the process also really exits with the recorded status,
in which case no failure was hidden. Those refusals cover what happens BEFORE the finalizer runs: a
fault the hooks record after it (a teardown destructor's import of an interpreter-creating module or
atexit registration) refuses nothing, disclosed below.
The report is {"format_version": 2, "suite": ..., "check_ids": [...], "exit_code": <the recorded
status>, "finalized": true}.

DISCLOSED RESIDUAL: a daemon thread still running at exit is killed, and its pending fault or
message can be lost. Interpreter teardown after the report is written still runs destructors, and a
destructor fault reaches stderr through sys.unraisablehook, refusing the verdict, only while
sys.stderr still exists. The residual is a CLASS, not a list of placements: a faulting destructor
of an object whose last reference is held by interpreter-level state that CPython clears after it
has dropped sys.stderr (for example the codec search registry (codecs.register), audit hooks
(sys.addaudithook), and a cycle anchored on a sys attribute) runs and raises at late teardown with
an empty error stream, and the run passes; such a fault is silent under the gate exactly as under a
direct launch. The examples are not exhaustive and no further placement is enumerated. No
in-process fix is known (the report is already written); the gate's self-test pins the three named
examples as a residual witness, so a change in this behaviour for any of them fails it. A fault
the observation hooks record after the finalizer has run (a teardown destructor's import of an
interpreter-creating module or atexit registration) refuses nothing, and the
finalizer's count comparison lets an atexit.unregister made after the exit-time join mask a later
registration (above). A silent effect of such late code (rewriting the report, calling os._exit
itself) is the same tier as loaded code replacing the reporting machinery; and an execution context
created below the audited Python surface (a C extension or ctypes creating an interpreter or an OS
thread directly) is that same tier. Loaded code replacing the reporting machinery (including
this module's state, threading._shutdown, threading.Thread._bootstrap, or the private per-thread
run machinery _bootstrap calls, such as a threading.Thread subclass overriding _bootstrap_inner or a
replaced _invoke_excepthook) is the gate's loaded-code residual, disclosed there.
"""
import atexit
import gc
import importlib.machinery
import json
import os
import sys
import threading
import types

FORMAT_VERSION = 2
GC_PASSES = 3            # the bounded collection: stop early once a pass frees nothing
HARNESS_ERROR = 2
_THREAD_START_EVENTS = ("_thread.start_new_thread", "_thread.start_joinable_thread")
# The stdlib modules able to create or drive another interpreter from Python: _interpreters (3.13+)
# and _xxsubinterpreters (its older name), and the C-API test modules whose run_in_subinterp does
# the same. Importing one is itself a fault: every hook this module installs is per-interpreter, so
# code in another interpreter is unobservable from this one.
_SUBINTERP_MODULES = frozenset(
    ("_interpreters", "_xxsubinterpreters", "_testcapi", "_testinternalcapi"))
_BOOTSTRAP = threading.Thread._bootstrap   # every thread threading starts runs this, bound
# SystemExit's own code slot, read through the base descriptor so that a subclass's code property
# (code the exception controls) never runs while a thread's end is classified
_SYSTEM_EXIT_CODE = SystemExit.__dict__["code"]
_STATE = {"installed": False, "armed": False, "code": None, "raised": None, "confirmed": None,
          "joined": False, "callbacks_at_join": None, "main_ident": None, "faults": [],
          "thread_hook": None}


class _SuiteExit(SystemExit):
    """The SystemExit that exit_with raises. When it ends the process the interpreter reads its code on
    the main thread with no Python frame on the stack, before the exit-time thread join; only such a
    read confirms this instance. The finalizer writes the report only when the confirmed instance is
    the one the most recent exit_with raised; the exit status itself is the interpreter's own (the
    reporting path never overrides it), so a mimicked frameless read cannot make a different real
    exit pass the gate's exit_code reconcile."""

    @property
    def code(self, *, _getframe=sys._getframe, _get_ident=threading.get_ident, _STATE=_STATE,
             _SYSTEM_EXIT_CODE=_SYSTEM_EXIT_CODE, SystemExit=SystemExit, ValueError=ValueError):
        try:
            _getframe(1)
        except ValueError:
            if not _STATE["joined"] and _get_ident() == _STATE["main_ident"]:
                _STATE["confirmed"] = self
        return _SYSTEM_EXIT_CODE.__get__(self, SystemExit)


def _harness_error(message, *, print=print, sys=sys, BaseException=BaseException):
    try:
        print("SELF-TEST HARNESS ERROR: {}".format(message), file=sys.stderr)
        sys.stderr.flush()
    except BaseException:  # the exit status still carries the error
        pass


def _clean_status(code, *, type=type, int=int):
    return code is None or (type(code) is int and code == 0)


def _thread_hook(args, *, _STATE=_STATE, _SYSTEM_EXIT_CODE=_SYSTEM_EXIT_CODE,
                 _clean_status=_clean_status, issubclass=issubclass, type=type,
                 SystemExit=SystemExit, BaseException=BaseException):
    """threading.excepthook while installed: record every thread exception except a clean SystemExit,
    then defer to the hook it replaced (which still writes the traceback to stderr). The end is
    classified by the exception's real type and SystemExit's own code slot, so no code the exception
    controls runs (a failure to classify is a fault); a fault is recorded FIRST with a fixed message,
    and only then are the thread's name and the exception's repr (both code the suite controls)
    described, inside a guard whose own failure is recorded too."""
    try:
        exc = args.exc_value
        clean = (issubclass(type(exc), SystemExit)
                 and _clean_status(_SYSTEM_EXIT_CODE.__get__(exc, SystemExit)))
    except BaseException:
        clean = False
    if not clean:
        _STATE["faults"].append("a thread ended with an exception other than a clean SystemExit")
        try:
            name = args.thread.name if args.thread is not None else "<unknown>"
            _STATE["faults"].append("thread {} ended with {!r}".format(name, args.exc_value))
        except BaseException:
            _STATE["faults"].append("the exception a thread ended with could not be described")
    _STATE["thread_hook"](args)


def _genuine_bootstrap(target, *, _BOOTSTRAP=_BOOTSTRAP, _MethodType=types.MethodType,
                       _Thread=threading.Thread, issubclass=issubclass, type=type):
    """Whether a thread-start target is threading's own: exactly a bound method (its type, never an
    attribute the target supplies) whose function is threading.Thread._bootstrap itself and whose
    instance's real type is a threading.Thread. Every read is a slot of the exact method type or a
    real type, so no code the target controls runs."""
    return (type(target) is _MethodType and target.__func__ is _BOOTSTRAP
            and issubclass(type(target.__self__), _Thread))


def _audit(event, args, *, _STATE=_STATE, _THREAD_START_EVENTS=_THREAD_START_EVENTS,
           _SUBINTERP_MODULES=_SUBINTERP_MODULES, _genuine_bootstrap=_genuine_bootstrap,
           _exact_str=str.__str__, BaseException=BaseException):
    """The audit hook from installation on: record, as a fault, a thread started outside threading
    or after the exit-time join, and an import of a module able to create another interpreter
    (whose code no hook of this interpreter can see; the event fires for the import attempt, so a
    probing failed import is recorded the same way). It never raises (a raising hook would abort
    the audited operation instead)."""
    try:
        if event == "import":
            # the exact str of the name: a str subclass's own __hash__ or __eq__ would otherwise
            # decide the membership test (a name that is not a str is unclassifiable, so a fault)
            name = _exact_str(args[0])
            if name in _SUBINTERP_MODULES:
                _STATE["faults"].append(
                    "the interpreter-creating module {} was imported; code in another interpreter "
                    "cannot be observed from this one".format(name))
            return
        if event not in _THREAD_START_EVENTS:
            return
        if _STATE["joined"]:
            _STATE["faults"].append("a thread was started after the exit-time thread join, so it is "
                                    "never joined ({})".format(event))
        elif not _genuine_bootstrap(args[0]):
            _STATE["faults"].append("a thread was started through {} directly, outside threading, so "
                                    "its exit status cannot be observed".format(event))
    except BaseException:
        _STATE["faults"].append("an audited event ({}) could not be classified".format(event))


def _wrap_shutdown(original, *, _STATE=_STATE, _ncallbacks=atexit._ncallbacks):
    """threading._shutdown while installed: the interpreter's exit-time join of non-daemon threads.
    Once it has finished, a status read confirms nothing and a new thread or atexit registration is a
    fault."""
    def _shutdown():
        try:
            return original()
        finally:
            _STATE["joined"] = True
            _STATE["callbacks_at_join"] = _ncallbacks()
    return _shutdown


def _install():
    """Install the observation hooks; idempotent. _bootstrap_main calls this before any suite code
    runs, so under the gate nothing a runner executes precedes them. The audit hook cannot be
    removed; replacing the excepthook or _shutdown wrapper afterwards is loaded code replacing the
    reporting machinery, the disclosed residual."""
    if _STATE["installed"]:
        return
    _STATE["installed"] = True
    _STATE["main_ident"] = threading.main_thread().ident
    _STATE["thread_hook"] = threading.excepthook
    threading.excepthook = _thread_hook
    threading._shutdown = _wrap_shutdown(threading._shutdown)
    sys.addaudithook(_audit)


class _RunnerLoader(importlib.machinery.SourceFileLoader):
    """Loads the registered runner from its source only, as a direct launch does: no bytecode cache
    is read (path_stats is what the source loader consults before reading one)."""

    def path_stats(self, path):
        raise OSError("the runner is compiled from its source, never from a bytecode cache")


def _bootstrap_main():
    """The gate's child entry point: install the observation hooks, then run the registered runner
    as __main__ with the argv contract it expects ([<runner>, --execution-report, <report>]). The
    gate launches [python, -I, -B, -c, <import this module and call _bootstrap_main>, <this
    module's directory>, <runner>, <report>], so sys.argv here is [-c, <dir>, <runner>, <report>].
    The runner's module REPLACES sys.modules["__main__"] and is never restored, so an exit handler
    or destructor that imports __main__ sees the runner's module, as under a direct launch (a
    runpy.run_path run restores the bootstrap's __main__ as the runner's exit unwinds)."""
    runner, report = sys.argv[2], sys.argv[3]
    _install()
    sys.argv[:] = [runner, "--execution-report", report]
    loader = _RunnerLoader("__main__", runner)
    main = types.ModuleType("__main__")
    main.__file__ = runner
    main.__loader__ = loader
    sys.modules["__main__"] = main
    loader.exec_module(main)


def _finalize(report_path, suite_id, executed, *, _STATE=_STATE, _collect=gc.collect,
              _ncallbacks=atexit._ncallbacks, _dump=json.dump, _exit=os._exit, sys=sys,
              _harness_error=_harness_error, open=open, list=list, range=range,
              BaseException=BaseException, GC_PASSES=GC_PASSES, HARNESS_ERROR=HARNESS_ERROR,
              FORMAT_VERSION=FORMAT_VERSION):
    """The exit handler: every refusing path ends the process immediately with os._exit(2) and no
    report; the reporting path writes the report and RETURNS, deferring to the interpreter's own
    exit status, which the gate reconciles against the report's exit_code."""
    status = HARNESS_ERROR
    try:
        for _ in range(GC_PASSES):
            if _collect() == 0:
                break
        code = _STATE["code"]
        if _STATE["joined"] and _ncallbacks() > _STATE["callbacks_at_join"]:
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
                _dump({"format_version": FORMAT_VERSION, "suite": suite_id,
                       "check_ids": list(executed), "exit_code": code, "finalized": True},
                      handle)
                handle.write("\n")
            status = None   # defer to the interpreter's own exit status
            # drop the exit exception: its traceback holds the runner's frame and so its module
            # globals, which would otherwise outlive module teardown as cyclic garbage freed only
            # after sys.stderr is dropped, silencing a destructor fault in a cycle they anchor
            _STATE["raised"] = _STATE["confirmed"] = None
    except BaseException as exc:
        status = HARNESS_ERROR
        # the fixed message first: the report path and the exception are the suite's objects, and
        # describing them runs its code, whose own failure is recorded rather than erasing this one
        _harness_error("cannot finalize execution report; no execution report is written")
        try:
            _harness_error("the finalizer failed for {}: {!r}".format(report_path, exc))
        except BaseException:
            _harness_error("the finalizer's failure could not be described")
    finally:
        if status is not None:
            _exit(status)


def arm(report_path, suite_id, executed):
    """Register the finalizer as the process's first atexit callback, or exit 2. Arming twice, after
    any other registration, or in a process whose observation hooks were not installed at start is
    refused before anything is registered."""
    if _STATE["armed"] or atexit._ncallbacks() != 0:
        _harness_error("the execution-report finalizer must be the first atexit registration")
        sys.exit(HARNESS_ERROR)
    if not _STATE["installed"]:
        _harness_error("the observation hooks were not installed before the suite's code ran; a "
                       "run that finalizes an execution report must be launched through "
                       "tools/check_selftest_execution.py, whose bootstrap installs them at "
                       "process start")
        sys.exit(HARNESS_ERROR)
    _STATE["armed"] = True
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
