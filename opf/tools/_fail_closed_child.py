#!/usr/bin/env python3
"""The shared fail-closed child contract, first slice: code that a gate or OPF command must load is run
in a CHILD process, and the parent reads a pass only from the child's settled, recorded verdict, never
from an exit status alone, so an os._exit, a SystemExit, or a fault in a thread, destructor, atexit
callback or the exit flush cannot read as a pass, except through loaded code that alters any object
the reporting machinery reads on its way to the record or to fd 2 (a hook, a stream object, a buffer
or raw layer below one or a write or flush member at any layer, the thread-run machinery, a value its
closures or threading's globals carry, or the file on fd 2) and restores or stands in for it between
the checks that observe it, or a fault after the record exists whose report does not reach fd 2, the
residuals disclosed in NOT YET COVERED below.

THE CHILD SIDE (ported from tools/_selftest_exit_report.py): the parent launches
[python, -I, -B, -c, _CHILD_SHIM, <this module's directory>, <payload>, <record>, <nonce>], and
_child_main installs the observation hooks BEFORE any loaded code runs, arms the finalizer as the
process's FIRST atexit registration (atexit runs last-registered first, so it runs after every other
callback), and only then runs the payload as __main__. The hooks: threading.excepthook is wrapped so a
thread ending in any exception other than a clean SystemExit is a fault, the SystemExit's code read
from SystemExit's own code slot (SystemExit.__dict__["code"]), never through getattr, so a subclass
whose code property lies about the slot cannot pass; an audit hook (which cannot be removed) records
as a fault every thread started through _thread directly, outside threading (a start is threading's
own only when its target is exactly a bound method of threading.Thread._bootstrap on a real
threading.Thread, read through the method type's own slots), every unraisable exception (a destructor's
or an atexit callback's; while a hook is installed CPython raises the sys.unraisablehook event before
it calls whichever hook is installed, so a replaced hook, a no-op or a collecting one, still leaves the
fault recorded; a DELETED hook, del sys.unraisablehook, instead sends the report to CPython's default
printer and raises NO event, measured on CPython 3.14.4, so a fault while the hook is absent is
recorded nowhere, the finalizer refuses a hook that is absent at exit, and a deletion restored between
checks is the disclosed replaced-reporting-machinery residual), the
creation of another interpreter
where its documented event arrives (measured on CPython 3.14.4, _interpreters.create() delivered no
cpython.PyInterpreterState_New event to an installed hook, so interpreter creation from Python is
actually caught by the _interpreters refusals and the import fault, and the event leg is a backstop
for deliveries that do occur) and the import of an interpreter-creating module; and _install
replaces every non-query function of _interpreters with a refusal that records a fault first, so code
in another interpreter, which no hook of this interpreter can observe, is refused at the attempt.
sys.unraisablehook is wrapped too, and the finalizer records as a fault a hook that is absent or no
longer that wrapper, so a hook deleted, or replaced with one that reports nothing, is a fault even when
nothing raised; the finalizer likewise requires back, by identity, the threading.excepthook wrapper, the
sys.stderr and sys.__stderr__ objects present at install, and (by fstat, device and inode) the file that
was open on fd 2 at install, so a reporting channel still diverted at exit is itself a recorded fault.
threading.Thread._bootstrap_inner is wrapped as well, and a thread observed starting or ending with an
_invoke_excepthook that is not the one threading set (the per-thread slot _bootstrap_inner calls instead
of threading.excepthook, so a replacement there swallows a report the excepthook wrapper never sees) is
a fault; "the one threading set" is required to be a plain function with threading's own code object and
module globals AND, cell by cell and by identity, the closure values a thread constructed after install
captures (so a sys.exc_info swapped only while Thread() ran, or a closure cell rebound afterwards, each
faking a clean SystemExit, is a fault, not a pass; the two fallback cells holding the hooks that stood
at the thread's construction are exempt, since each is read only when the live hook is already absent,
replaced or raising, itself a recorded fault), and threading.ExceptHookArgs (the constructor the genuine
_invoke_excepthook reads from threading's globals to build what the excepthook receives) is required
back by identity at each thread's start and end and at exit, so a replacement there left in place, or
restored only after the join, is a fault; the _bootstrap_inner wrapper is required back by identity at
exit like the hooks; and the finalizer refuses a recorded sys.stderr or sys.__stderr__ object that no
longer resolves write or flush to the type-level method it resolved at install, at the object itself
and at each layer below it reached through .buffer and then .raw (an instance attribute shadowing
either name at any recorded layer, a replaced type-level method, a changed type, or a layer object no
longer the one recorded), so a report silently swallowed on the unreplaced top object, in its buffer
or in its raw file is a recorded fault too, not only a replaced stream.

THE RECORD: the parent passes the record path and a fresh nonce on the child's argv. The loaded code
settles its verdict only through exit_with(code), which records the decided code (an int in 0 to 255;
None is 0) and raises the exit that must end the process; a decided code outside 0 to 255 is a fault
and settles nothing. At interpreter exit, after the other atexit callbacks and a bounded garbage
collection, the finalizer refuses outright in any process other than the one arm() ran in (a forked
process inherits the armed finalizer and the nonce, and its record would otherwise pass as the
original's), creates the record EXCLUSIVELY (open "x", so a record already present is a refusal,
never overwritten) as JSON {nonce, decided_code, check_ids, faults} and then RETURNS, so the process
ends with the interpreter's OWN exit status, the one carried by the exception that really ended it:
the parent refuses a record whose decided code differs from that real status, so a confirmation
spoofed by loaded code (a frameless main-thread read it arranges, as from an atexit callback through
a C caller) produces a record that stands or falls with the real exit status: the spoof is refused
when that status differs from the decided code, while a spoof followed by an exit carrying the
MATCHING status still reads as the decided code (the spoofed-confirmation residual disclosed in NOT
YET COVERED below, pinned by its vector). Any exit that settled nothing (a raw SystemExit, 0
included; an uncaught exception; an os._exit before the finalizer runs, which skips it and so leaves
NO record) writes no record (an os._exit during interpreter teardown, after the finalizer returned,
instead leaves the record in place and sets the real status the parent compares against it), and the
finalizer's refusing paths end with os._exit(2), which drops unflushed buffers on file objects other
than sys.stdout, sys.stderr, sys.__stdout__ and sys.__stderr__ (those four the finalizer flushes
after its own-process check; every refusing path, the forked-process refusal and a raising flush
included, flushes sys.stderr with its refusal note, while unflushed sys.stdout text may be lost); a
refusing path writes no record and exits 2, so an os._exit there can only lose refusal diagnostics,
never turn into a pass, while the reporting path defers to the interpreter, whose own teardown
flushes what remains.

THE PARENT (minimal, this slice): run_child first calls the canonical child-status guard, carried
below as the inline copy tools/check_child_status_coverage.py pins by AST to opf/tools/_child_status.py,
as every parent must (with SIGCHLD ignored a failed child reads as exit 0), launches the child, captures
both streams (each retained only up to _STREAM_CAP bytes; a stream past it is drained, discarded and
refused), opens the record without following a symlink or blocking (O_NOFOLLOW, O_NONBLOCK) and reads
it only when fstat shows a regular file, at most _RECORD_CAP plus one bytes (a FIFO, a device or a
symlink at the record path is refused, never waited on), and passes ONLY when both streams are
byte-empty (the "empty" streams mode of the
existing contract), the record exists, parses, carries exactly the expected keys and the launch's
nonce, lists no fault, its decided code is an int in 0 to 255 equal to the child's exit status, and
that code is 0 or 1; a recorded 1 with exit 1 stays 1, and anything else is 2.

NOT YET COVERED, LATER SLICES (stated so no reader takes this slice for the whole contract): the
settle terms of opf/tools/check_opf_init_contract.py (the thread census, the low-level worker start
flags and _thread._count, the /proc/self/task wait, the survivors-of-session census and the
in-its-own-session launch); the stream-forwarding mode (this slice requires byte-empty streams only);
the extension-file identity layer and the trace/profile/monitoring audit events of
tools/_selftest_exit_report.py; its bytecode-cache-refusing runner loader and its exit-time-join
(threading._shutdown) wrap, so a thread still running at exit and an atexit registration made during
the exit callbacks are not yet observed here. The reporting path RETURNS instead of ending with
os._exit(decided_code), trading away the no-loaded-code-runs-after-the-record property: interpreter
teardown (destructors of objects still alive) runs after the record exists, and deliberately hostile
code there could still end the process with a matching os._exit, or fault there silently: ANY fault
after the record exists reaches no record and is refused only where its report reaches fd 2, so code
that first replaces the reporting machinery during teardown (sys.unraisablehook, or the sys.stderr and
sys.__stderr__ objects the finalizer's identity checks saw only before the record was written) can
still fault unreported; in exchange the record no longer
fixes the exit status by construction, so a spoofed confirmation is contradicted by the real exit
status at the parent whenever that status differs from the decided code, which the os._exit form
could not refuse. A spoofed confirmation whose later exit carries the MATCHING status (a caught
exit_with(0), a frameless read of that exception's code arranged from atexit, then a raw sys.exit(0)
or falling off the end) still reads as the decided code; that residual is not closed here, and its
vector pins the disclosed behaviour. Deliberately hostile loaded code
forging the record (the nonce is readable in process) or replacing this module's state in the child
is the reporting-machinery residual the stronger contracts disclose; it is not closed here either.
Neither, in general, is loaded code that alters any object the reporting machinery reads on its way
to the record or to fd 2 and restores or stands in for it between the checks that observe it: the
checks pin each named object by identity at fixed moments (install, each thread's start and end, and
exit), never continuously, so a diversion confined to the window between two checks still makes a
fault read as a pass in process. The channels checked, each coverable only at those moments, are
sys.unraisablehook (a deletion raises no sys.unraisablehook event, so a fault while it is absent is
recorded nowhere and reported only to sys.stderr), threading.excepthook,
threading.Thread._bootstrap_inner, a thread's own _invoke_excepthook slot with its closure cells,
threading.ExceptHookArgs, sys.stderr, sys.__stderr__, the write or flush member resolved at any
recorded layer of either stream (the object, its buffer, its raw file), or the file open on fd 2;
what remains of the class after this slice is a diversion of any of these restored, from another
thread or a C caller, inside the window between the checks that bracket it (the finalizer's identity
and member checks see only the state at exit, and a thread's _invoke_excepthook, its cells and
threading.ExceptHookArgs are read only as the thread starts and again after it ran), the two exempt
fallback cells named above, and any mutable state these functions read that the list above does not
pin (state of the io objects below the recorded members, or the C-level printers the default hooks
fall back to, which no Python-level identity pin can bracket): every such restored channel, and loaded
code standing in for the thread-run machinery so no installed check runs at all (a threading.Thread
subclass that overrides _bootstrap_inner, the run leg below the _bootstrap start target the audit
hook verifies, so a _bootstrap override is already a recorded start fault while a _bootstrap_inner
override is not), is the same
loaded-code-replacing-the-reporting-machinery residual; the deleted-hook-restored
and subclassed-bootstrap-inner vectors pin the two disclosed passes.

  _fail_closed_child.py --self-test   the contract's real-child vectors, each paired with one flip of
                                      this module observed red (the flipped copy runs real children
                                      from its own scratch directory)

Exit: 0 a passing self-test; 1 a failing one; 2 usage or the child-status refusal.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: _fail_closed_child.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import atexit
import gc
import importlib.machinery
import importlib.util
import json
import os
import secrets
import selectors
import stat
import subprocess
import tempfile
import threading
import time
import types

NAME = "_fail_closed_child.py"
CHILD_TIMEOUT = 120
_RECORD_CAP = 1 << 20    # bytes; a genuine record is small, so anything larger is malformed
_STREAM_CAP = 1 << 16    # bytes retained per child stream; any byte already refuses, so this is diagnostics
_UNRAISABLE_EVENT = "sys.unraisablehook"   # raised before CPython calls the installed hook, any hook
_GC_PASSES = 3           # the bounded exit-time collection: stop early once a pass frees nothing
_THREAD_START_EVENTS = ("_thread.start_new_thread", "_thread.start_joinable_thread")
# The audit event CPython documents for the creation of an interpreter; receiving it is a fault. The
# stdlib modules able to create or drive one from Python, whose import after installation is a fault
# (for _interpreters, which _install imports first, only a fresh load raises the event); and the
# read-only _interpreters queries left in place when every other function is replaced by a refusal.
_INTERP_NEW_EVENT = "cpython.PyInterpreterState_New"
_SUBINTERP_MODULES = frozenset(
    ("_interpreters", "_xxsubinterpreters", "_testcapi", "_testinternalcapi"))
_INTERP_QUERIES = frozenset(("get_config", "get_current", "get_main", "is_running", "is_shareable",
                             "list_all", "new_config", "whence"))
_BOOTSTRAP = threading.Thread._bootstrap   # every thread threading starts runs this, bound
_BOOTSTRAP_INNER = threading.Thread._bootstrap_inner   # the per-thread run leg _install wraps
# Every _invoke_excepthook threading itself sets is a closure of one inner function sharing one code
# object and the threading module's own globals, so those of a throwaway (never started) Thread
# identify them all; a per-thread replacement is what _bootstrap_inner calls INSTEAD of
# threading.excepthook, so it must be refused at the thread, not at the module-level hook. The
# closure's cells are what the function READS (sys.exc_info among them), so _install records a
# reference thread's cell values and _genuine_invoke requires them back by identity, except the two
# fallback cells holding the hooks that stood at the thread's construction: each of those is read
# only when the live hook is already absent, replaced or raising, itself a recorded fault, and an
# honestly replaced sys.excepthook would otherwise read as a fault here.
_INVOKE_CODE = threading.Thread(target=None)._invoke_excepthook.__code__
_INVOKE_CELL_EXEMPT = frozenset(("old_excepthook", "old_sys_excepthook"))
_INVOKE_CHECKED = tuple(name not in _INVOKE_CELL_EXEMPT for name in _INVOKE_CODE.co_freevars)
_THREADING_DICT = vars(threading)
# The ExceptHookArgs constructor threading set at import: the genuine _invoke_excepthook reads this
# name from threading's globals to build what the excepthook receives, so a replacement there can
# disguise any thread exception as a clean SystemExit while the function object itself stays genuine.
_EXCEPT_HOOK_ARGS = threading.ExceptHookArgs
# SystemExit's own code slot, read through the base descriptor so that a subclass's code property
# (code the exception controls) never runs while a thread's end is classified.
_SYSTEM_EXIT_CODE = SystemExit.__dict__["code"]
_STATE = {"installed": False, "armed": False, "armed_pid": None, "decided": None, "raised": None,
          "confirmed": None, "main_ident": None, "record_path": None, "nonce": None, "check_ids": [],
          "faults": [], "thread_hook": None, "unraisable_hook": None, "start_stderr": None,
          "start_dunder_stderr": None, "start_fd2": None, "start_stderr_members": None,
          "start_dunder_stderr_members": None, "invoke_cells": None}
# The child bootstrap the parent launches: sys.argv is [-c, <module dir>, <payload>, <record>, <nonce>].
_CHILD_SHIM = ("import sys; sys.path.insert(0, sys.argv[1]); "
               "import _fail_closed_child; _fail_closed_child._child_main()")

# -------------------------------------------------------- the canonical child-status guard, inline
# The inline copy of opf/tools/_child_status.py's guard, which tools/check_child_status_coverage.py
# pins to the canonical text by AST, so this entry point refuses a direct launch from an ignoring
# parent itself and both its children and its self-test need no sibling import.

_CHILD_STATUS_PROBE_CODE = 77


def _child_status_refusal():
    """None when this process can read a child's exit status, otherwise the reason it cannot."""
    import os
    import signal
    if not hasattr(signal, "SIGCHLD"):
        return None
    disposition = signal.getsignal(signal.SIGCHLD)
    if disposition is not signal.SIG_DFL:
        return ("SIGCHLD is ignored (SIG_IGN) in this process" if disposition is signal.SIG_IGN
                else "SIGCHLD is not at its default disposition in this process")
    if not hasattr(os, "fork"):
        return None
    try:
        pid = os.fork()
    except OSError as exc:
        return "the child-status probe could not fork ({})".format(type(exc).__name__)
    if pid == 0:
        os._exit(_CHILD_STATUS_PROBE_CODE)
    try:
        waited, status = os.waitpid(pid, 0)
    except ChildProcessError:
        return "the probe child was reaped before its exit status could be read (ECHILD)"
    except OSError as exc:
        return "the probe child's exit status could not be read ({})".format(type(exc).__name__)
    if waited != pid or os.waitstatus_to_exitcode(status) != _CHILD_STATUS_PROBE_CODE:
        return "a probe child that exited {} was read back as wait status {}".format(
            _CHILD_STATUS_PROBE_CODE, status)
    return None


def _child_status_refusal_exit(name):
    """0 when a child's exit status is observable; otherwise write the named refusal and exit 2 at once
    (os._exit, so no exit-time stream flush can replace the status)."""
    import os
    reason = _child_status_refusal()
    if reason is None:
        return 0
    try:
        sys.stderr.write("error: {} refuses to run: {}, so a failed child would read as a success. Nothing "
                         "was run (cannot evaluate). Start it from a parent that leaves SIGCHLD at "
                         "SIG_DFL.\n".format(name, reason))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)


class _ChildExit(SystemExit):
    """The SystemExit that exit_with raises. When it ends the process the interpreter reads its code on
    the main thread with no Python frame on the stack; only such a read confirms this instance, and the
    finalizer writes the record only when the confirmed instance is the one the most recent exit_with
    raised, so an exit_with whose exception was caught settles nothing unless loaded code itself
    arranges such a frameless main-thread read of that instance afterwards (the disclosed
    spoofed-confirmation residual, which the real exit status refuses only where it differs)."""

    @property
    def code(self, *, _getframe=sys._getframe, _get_ident=threading.get_ident, _STATE=_STATE,
             _SYSTEM_EXIT_CODE=_SYSTEM_EXIT_CODE, SystemExit=SystemExit, ValueError=ValueError):
        try:
            _getframe(1)
        except ValueError:
            if _get_ident() == _STATE["main_ident"]:
                _STATE["confirmed"] = self
        return _SYSTEM_EXIT_CODE.__get__(self, SystemExit)


def _note(message, *, print=print, sys=sys, BaseException=BaseException):
    """One refusal line on stderr; a failing stream still leaves the exit status to carry the refusal."""
    try:
        print("FAIL-CLOSED-CHILD: {}".format(message), file=sys.stderr)
        sys.stderr.flush()
    except BaseException:
        pass


def _clean_status(code, *, type=type, int=int):
    """Whether a SystemExit status read from the slot is clean: None, or exactly the int 0."""
    return code is None or (type(code) is int and code == 0)


def _thread_hook(args, *, _STATE=_STATE, _SYSTEM_EXIT_CODE=_SYSTEM_EXIT_CODE,
                 _clean_status=_clean_status, issubclass=issubclass, type=type,
                 SystemExit=SystemExit, BaseException=BaseException):
    """threading.excepthook while installed: record every thread exception except a clean SystemExit,
    then defer to the hook it replaced. The end is classified by the exception's real type and
    SystemExit's own code slot, so no code the exception controls runs (a failure to classify is a
    fault); the fault is recorded FIRST with a fixed message."""
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


def _unraisable_fault(message, *, _STATE=_STATE):
    """Record one unraisable-exception fault: the audit leg's and the finalizer's hook check's."""
    _STATE["faults"].append(message)  # recorded before any hook runs


def _unraisable_hook(unraisable, *, _STATE=_STATE):
    """sys.unraisablehook while installed: defer to the hook it replaced. The fault itself is recorded
    FIRST by the audit hook's sys.unraisablehook leg, which CPython raises before it calls whichever
    hook is installed, so a replaced hook cannot drop it; this wrapper's identity is what the finalizer
    requires back, so a hook replaced and left in place is a fault even when nothing raised."""
    _STATE["unraisable_hook"](unraisable)


def _genuine_invoke(thread, *, _INVOKE_CODE=_INVOKE_CODE, _THREADING_DICT=_THREADING_DICT,
                    _INVOKE_CHECKED=_INVOKE_CHECKED, _STATE=_STATE,
                    _FunctionType=type(_clean_status), type=type, len=len, zip=zip,
                    BaseException=BaseException):
    """Whether the thread runs with the _invoke_excepthook threading itself set: a plain function
    sharing the code object and module globals of the closure threading makes per thread, each
    non-exempt closure cell still holding, by identity, the value the reference thread _install
    constructed captured (so a sys.exc_info swapped only while Thread() ran, or a cell rebound
    afterwards, is not genuine), so a thread exception really reaches the installed excepthook with
    the real exception. One that cannot be read is not genuine."""
    try:
        invoke = thread._invoke_excepthook
        if not (type(invoke) is _FunctionType and invoke.__code__ is _INVOKE_CODE
                and invoke.__globals__ is _THREADING_DICT):
            return False
        cells, recorded = invoke.__closure__, _STATE["invoke_cells"]
        if recorded is None or cells is None or len(cells) != len(recorded):
            return False
        for checked, cell, captured in zip(_INVOKE_CHECKED, cells, recorded):
            if checked and cell.cell_contents is not captured:
                return False
        return True
    except BaseException:
        return False


def _invoke_hook_fault(thread, moment, *, _STATE=_STATE, _genuine_invoke=_genuine_invoke):
    """Record the fault for a thread observed running with an _invoke_excepthook other than the one
    threading set (checked as the thread starts and again after it ran, so only a replacement
    diverted and restored between the two checks remains the disclosed restored-channel residual)."""
    if _genuine_invoke(thread):
        return
    _STATE["faults"].append("a thread {} with an _invoke_excepthook that is not the one threading "
                            "set, so a thread fault could have gone unreported".format(moment))


def _except_hook_args_fault(moment, *, _STATE=_STATE, _THREADING_DICT=_THREADING_DICT,
                            _EXCEPT_HOOK_ARGS=_EXCEPT_HOOK_ARGS):
    """Record the fault for threading.ExceptHookArgs observed, at a thread's start or end or at exit,
    no longer the constructor threading set: the genuine _invoke_excepthook reads that name from
    threading's globals to build what the excepthook receives, so a replacement there can hand every
    hook a fake that disguises any thread exception as a clean SystemExit."""
    if _THREADING_DICT.get("ExceptHookArgs") is _EXCEPT_HOOK_ARGS:
        return
    _STATE["faults"].append("threading.ExceptHookArgs ({}) is not the constructor threading set, so "
                            "a thread fault could have been disguised as a clean exit".format(moment))


def _bootstrap_inner_hook(self, *, _BOOTSTRAP_INNER=_BOOTSTRAP_INNER,
                          _invoke_hook_fault=_invoke_hook_fault,
                          _except_hook_args_fault=_except_hook_args_fault):
    """threading.Thread._bootstrap_inner while installed: require the per-thread _invoke_excepthook
    to be the one threading set and threading.ExceptHookArgs to be the constructor it reads, before
    the thread's own code runs and again after it ended (the second check runs after any exception
    was already handed to whatever sat in the slot, so a replacement left in place is recorded even
    though it swallowed the report); the finalizer requires this wrapper back by identity, like the
    hooks."""
    _invoke_hook_fault(self, "started")
    _except_hook_args_fault("as a thread started")
    try:
        _BOOTSTRAP_INNER(self)
    finally:
        _invoke_hook_fault(self, "ended")
        _except_hook_args_fault("as a thread ended")


def _stream_layer_below(layer, *, getattr=getattr):
    """The next layer a report passes through below this one: the .buffer of a text stream, else the
    .raw of a buffered one, else None; the callers bound the descent."""
    below = getattr(layer, "buffer", None)
    if below is None:
        below = getattr(layer, "raw", None)
    return below


def _stream_members(stream, *, _stream_layer_below=_stream_layer_below, type=type, getattr=getattr,
                    range=range, tuple=tuple):
    """A tuple, for the stream and each layer a report passes through below it (its .buffer, then
    that object's .raw, the walk bounded), of (the layer object, its type, its type-level write, its
    type-level flush), recorded at install and required back by _diverted_member at exit, or None for
    an absent stream."""
    if stream is None:
        return None
    layers = []
    layer = stream
    for _ in range(4):
        kind = type(layer)
        layers.append((layer, kind, getattr(kind, "write", None), getattr(kind, "flush", None)))
        layer = _stream_layer_below(layer)
        if layer is None:
            break
    return tuple(layers)


def _diverted_member(stream, recorded, *, _stream_layer_below=_stream_layer_below, type=type,
                     getattr=getattr, vars=vars, enumerate=enumerate, BaseException=BaseException):
    """How a reporting stream no longer resolves write or flush to the type-level method recorded at
    install, at the stream itself or at any recorded layer below it (None when every recorded layer
    still does): an instance attribute shadowing either name at any layer, a replaced type-level
    method, a changed type, or a layer object no longer the one recorded each swallow a report while
    the top object's identity still holds, so each is a fault; a stream whose layers cannot be
    examined is a fault too."""
    try:
        if recorded is None:
            return None if stream is None else "appeared after install"
        layer = stream
        for depth, (obj, kind, write, flush) in enumerate(recorded):
            where = (" at its buffer layer" if depth == 1 else
                     " at its raw layer" if depth == 2 else
                     " at a layer below its raw layer" if depth else "")
            if layer is not obj:
                return "no longer reaches the layer object recorded at install{}".format(where)
            if type(layer) is not kind:
                return "is no longer of the type recorded at install{}".format(where)
            members = vars(layer)
            if "write" in members or "flush" in members:
                return "carries an instance attribute shadowing write or flush{}".format(where)
            if getattr(kind, "write", None) is not write or getattr(kind, "flush", None) is not flush:
                return "had a type-level write or flush replaced{}".format(where)
            layer = _stream_layer_below(layer)
        return None
    except BaseException:
        return "could not have its write and flush members examined"


def _genuine_bootstrap(target, *, _BOOTSTRAP=_BOOTSTRAP, _MethodType=types.MethodType,
                       _Thread=threading.Thread, issubclass=issubclass, type=type):
    """Whether a thread-start target is threading's own: exactly a bound method (its type, never an
    attribute the target supplies) whose function is threading.Thread._bootstrap itself and whose
    instance's real type is a threading.Thread, so no code the target controls runs."""
    return (type(target) is _MethodType and target.__func__ is _BOOTSTRAP
            and issubclass(type(target.__self__), _Thread))


def _audit(event, args, *, _STATE=_STATE, _THREAD_START_EVENTS=_THREAD_START_EVENTS,
           _SUBINTERP_MODULES=_SUBINTERP_MODULES, _INTERP_NEW_EVENT=_INTERP_NEW_EVENT,
           _UNRAISABLE_EVENT=_UNRAISABLE_EVENT, _unraisable_fault=_unraisable_fault,
           _genuine_bootstrap=_genuine_bootstrap, _exact_str=str.__str__,
           BaseException=BaseException):
    """The audit hook from installation on: record, as a fault, a thread started through _thread
    directly, outside threading, every unraisable exception (its event precedes the call of whichever
    hook is installed), the creation of another interpreter when its event is delivered, and
    the import of an interpreter-creating module by its exact plain name (a str subclass's own hashing
    or equality decides nothing: the name is read through str's own __str__ first, and one that is not
    a str is unclassifiable, so a fault). It never raises (a raising hook would abort the audited
    operation instead of recording it)."""
    try:
        if event == _UNRAISABLE_EVENT:
            _unraisable_fault("an unraisable exception was reported (a destructor, an atexit callback "
                              "or another callback whose exception cannot propagate raised)")
            return
        if event == "import":
            name = _exact_str(args[0])
            if name in _SUBINTERP_MODULES:
                _STATE["faults"].append(
                    "the interpreter-creating module {} was imported; code in another interpreter "
                    "cannot be observed from this one".format(name))
            return
        if event == _INTERP_NEW_EVENT:
            _STATE["faults"].append("another interpreter was created; code in another interpreter "
                                    "cannot be observed from this one")
            return
        if event not in _THREAD_START_EVENTS:
            return
        if not _genuine_bootstrap(args[0]):
            _STATE["faults"].append("a thread was started through {} directly, outside threading, "
                                    "so its exit status cannot be observed".format(event))
    except BaseException:
        _STATE["faults"].append("an audited event ({}) could not be classified".format(event))


def _refuse_interp_entry(name, *, _STATE=_STATE, RuntimeError=RuntimeError):
    """The replacement for an _interpreters function that creates, runs code in, or destroys an
    interpreter: record the fault FIRST (a caught RuntimeError still refuses the run), then raise."""
    message = ("_interpreters.{} was called; code in another interpreter cannot be observed from "
               "this one".format(name))

    def _refused(*args, **kwargs):
        _STATE["faults"].append(message)
        raise RuntimeError(message)
    return _refused


def _refuse_interp_entries():
    """Import _interpreters (before the audit hook is added, so neither this import nor a later one
    that finds sys.modules raises an event) and replace, on the module object every importer then
    receives, each of its functions outside _INTERP_QUERIES with a refusal. The originals are not
    kept. Absent the module (a build without it), an attempt to import it stays a fault (_audit)."""
    try:
        import _interpreters
    except ImportError:
        return
    for name in sorted(vars(_interpreters)):
        if (not name.startswith("_") and name not in _INTERP_QUERIES
                and isinstance(getattr(_interpreters, name), types.BuiltinFunctionType)):
            setattr(_interpreters, name, _refuse_interp_entry(name))


def _install():
    """Install the observation hooks, wrap the per-thread run leg, and record the reporting channels;
    idempotent. _child_main calls this before any loaded code runs, so nothing the payload executes
    precedes them. The audit hook cannot be removed; replacing this module's state afterwards is
    loaded code replacing the reporting machinery, the disclosed residual. A sys.unraisablehook or
    threading.excepthook replaced or deleted, a sys.stderr or sys.__stderr__ replaced, a write or
    flush shadowed or replaced on either recorded stream object or on a buffer or raw layer below it,
    a thread run with an _invoke_excepthook other than the one threading set (its non-exempt closure
    cells required back by identity), a replaced threading.ExceptHookArgs, a replaced
    Thread._bootstrap_inner wrapper,
    or an fd 2 re-opened and still so at exit is not: the finalizer (and, for the per-thread slot,
    the wrapper at the thread's start and end) requires each back (fd 2 by fstat device and inode,
    so a process whose fd 2 cannot be identified here is refused there too) and records a fault
    otherwise; only a channel diverted AND restored before the check that observes it runs remains
    the disclosed residual. The reference closure cells are read from a throwaway thread constructed
    HERE, after this module's excepthook wrapper is in place, so every thread the loaded code
    constructs captures the same values by identity."""
    if _STATE["installed"]:
        return
    _refuse_interp_entries()
    _STATE["installed"] = True
    _STATE["main_ident"] = threading.main_thread().ident
    _STATE["thread_hook"] = threading.excepthook
    threading.excepthook = _thread_hook
    threading.Thread._bootstrap_inner = _bootstrap_inner_hook
    _STATE["unraisable_hook"] = sys.unraisablehook
    sys.unraisablehook = _unraisable_hook
    _STATE["invoke_cells"] = tuple(
        cell.cell_contents for cell in threading.Thread(target=None)._invoke_excepthook.__closure__)
    _STATE["start_stderr"] = sys.stderr
    _STATE["start_dunder_stderr"] = sys.__stderr__
    _STATE["start_stderr_members"] = _stream_members(sys.stderr)
    _STATE["start_dunder_stderr_members"] = _stream_members(sys.__stderr__)
    try:
        fd2 = os.fstat(2)
        _STATE["start_fd2"] = (fd2.st_dev, fd2.st_ino)
    except OSError:
        _STATE["start_fd2"] = None
    sys.addaudithook(_audit)


def _finalize(record_path, nonce, executed, *, _STATE=_STATE, _collect=gc.collect, _dump=json.dump,
              _exit=os._exit, _getpid=os.getpid, _fstat=os.fstat, _note=_note, _GC_PASSES=_GC_PASSES,
              sys=sys, threading=threading, _wrapper=_unraisable_hook, _thread_wrapper=_thread_hook,
              _bootstrap_hook=_bootstrap_inner_hook, _diverted_member=_diverted_member,
              _except_hook_args_fault=_except_hook_args_fault,
              _unraisable_fault=_unraisable_fault, getattr=getattr, open=open, list=list, range=range,
              OSError=OSError, BaseException=BaseException):
    """The exit handler, the process's first atexit registration so it runs after every other exit
    callback: refuse outright in any process other than the one that armed (a forked process inherits
    this registration and the nonce, and its record would pass as the original's), run the bounded
    collection, flush the four stream objects, require back by identity the reporting channels
    recorded at install (both hook wrappers and the _bootstrap_inner wrapper, where an absent hook is
    the same fault as a replaced one; threading.ExceptHookArgs; the sys.stderr and sys.__stderr__
    objects, each also required to still resolve write and flush to the type-level methods recorded
    at install at the object and at each recorded layer below it; and, by fstat
    device and inode, the file open on fd 2, so a channel still diverted here is a fault the record
    carries), and, ONLY when a decided
    code was settled through
    exit_with and that very exception ended the process, create the record exclusively and RETURN,
    deferring to the interpreter's own exit status so a record whose decided code the real status
    contradicts is refused at the parent. Every refusing path (another process, nothing settled, a
    record already present, anything in here failing) ends with os._exit(2) and writes no record."""
    status = 2
    try:
        if _getpid() != _STATE["armed_pid"]:
            _note("the finalizer ran in a process other than the one that armed; no record is written")
            _exit(2)
        for _ in range(_GC_PASSES):
            if _collect() == 0:
                break
        for stream in (sys.stdout, sys.stderr, sys.__stdout__, sys.__stderr__):
            if stream is not None:
                stream.flush()
        if getattr(sys, "unraisablehook", None) is not _wrapper:
            _unraisable_fault("sys.unraisablehook is no longer the installed wrapper (replaced, or "
                              "deleted so faults went to the default printer and raised no event), so "
                              "a destructor or atexit fault could have gone unreported")
        if getattr(threading, "excepthook", None) is not _thread_wrapper:
            _STATE["faults"].append("threading.excepthook is no longer the installed wrapper, so a "
                                    "thread fault could have gone unreported")
        bootstrap_now = getattr(getattr(threading, "Thread", None), "_bootstrap_inner", None)
        if bootstrap_now is not _bootstrap_hook:
            _STATE["faults"].append("threading.Thread._bootstrap_inner is no longer the installed "
                                    "wrapper, so a thread's _invoke_excepthook could have gone "
                                    "unchecked")
        _except_hook_args_fault("at exit")
        if sys.stderr is not _STATE["start_stderr"]:
            _STATE["faults"].append("sys.stderr is no longer the stream recorded at install, so a "
                                    "fault report could have been diverted from fd 2")
        diverted = _diverted_member(_STATE["start_stderr"], _STATE["start_stderr_members"])
        if diverted is not None:
            _STATE["faults"].append("the sys.stderr recorded at install {}, so a fault report "
                                    "could have been silently swallowed".format(diverted))
        if sys.__stderr__ is not _STATE["start_dunder_stderr"]:
            _STATE["faults"].append("sys.__stderr__ is no longer the stream recorded at install, so "
                                    "a fault report could have been diverted from fd 2")
        diverted = _diverted_member(_STATE["start_dunder_stderr"], _STATE["start_dunder_stderr_members"])
        if diverted is not None:
            _STATE["faults"].append("the sys.__stderr__ recorded at install {}, so a fault report "
                                    "could have been silently swallowed".format(diverted))
        try:
            fd2 = _fstat(2)
            fd2_same = (_STATE["start_fd2"] is not None
                        and (fd2.st_dev, fd2.st_ino) == _STATE["start_fd2"])
        except OSError:
            fd2_same = False
        if not fd2_same:
            _STATE["faults"].append("fd 2 is no longer the file that was open at install, so a fault "
                                    "report could not have reached the parent's stderr capture")
        settled =(_STATE["decided"] is not None and _STATE["raised"] is not None
                   and _STATE["confirmed"] is _STATE["raised"])
        decided = _STATE["decided"] if settled else None
        if decided is None:
            _note("no decided code was settled through exit_with(); no record is written")
        else:
            with open(record_path, "x", encoding="utf-8") as handle:
                _dump({"nonce": nonce, "decided_code": decided, "check_ids": list(executed),
                       "faults": list(_STATE["faults"])}, handle)
                handle.write("\n")
            status = None   # defer to the interpreter's own exit status
            # drop the exit exception: its traceback holds the payload's frames, which must not
            # outlive the record as cyclic garbage destructed after the verdict
            _STATE["raised"] = _STATE["confirmed"] = None
    except BaseException:
        status = 2
        _note("cannot finalize the fail-closed-child record; no record is written")
    finally:
        if status is not None:
            _exit(status)


def arm(record_path, nonce, executed):
    """Register the finalizer as the process's FIRST atexit callback, or exit 2 at once, recording the
    arming process id, which the finalizer requires back (a forked process inherits the registration
    and settles nothing). Arming twice, after any other registration, or in a process whose
    observation hooks were not installed before the loaded code ran, is refused before anything is
    registered."""
    if _STATE["armed"] or atexit._ncallbacks() != 0:
        _note("the record finalizer must be the first atexit registration")
        sys.exit(2)
    if not _STATE["installed"]:
        _note("the observation hooks were not installed before any loaded code ran")
        sys.exit(2)
    _STATE["armed"] = True
    _STATE["armed_pid"] = os.getpid()
    _STATE["record_path"] = record_path
    _STATE["nonce"] = nonce
    _STATE["check_ids"] = executed
    atexit.register(_finalize, record_path, nonce, executed)


def check(check_id):
    """Record one executed check id into the armed record's check_ids."""
    _STATE["check_ids"].append(check_id)


def exit_with(code):
    """Settle the child's decided code (an int in 0 to 255; None is 0, as for sys.exit) and raise the
    exit that must end the process; the code stands only if that very exception ends it. A code
    outside 0 to 255, or a call outside the main thread, is a fault and settles nothing, so an armed
    run exits 2 with no record. Unarmed, this is sys.exit."""
    if code is None:
        code = 0
    if not _STATE["armed"]:
        sys.exit(code)
    _STATE["decided"] = None
    _STATE["raised"] = None
    if threading.current_thread() is not threading.main_thread():
        _STATE["faults"].append("exit_with() was called outside the main thread")
        sys.exit(code)
    if type(code) is int and 0 <= code <= 255:
        exc = _ChildExit(code)
        _STATE["decided"] = code
        _STATE["raised"] = exc
        raise exc
    _STATE["faults"].append("the decided code is not an int in 0 to 255 and settles nothing")
    sys.exit(code)


def _child_main():
    """The child entry point under _CHILD_SHIM (sys.argv is [-c, <module dir>, <payload>, <record>,
    <nonce>]): install the observation hooks, arm the finalizer, then run the payload as __main__
    from its source; no payload statement, import-time included, precedes the hooks."""
    payload, record_path, nonce = sys.argv[2], sys.argv[3], sys.argv[4]
    _install()
    arm(record_path, nonce, [])
    sys.argv[:] = [payload]
    loader = importlib.machinery.SourceFileLoader("__main__", payload)
    main_module = types.ModuleType("__main__")
    main_module.__file__ = payload
    main_module.__loader__ = loader
    sys.modules["__main__"] = main_module
    loader.exec_module(main_module)


# ------------------------------------------------------------------------------------- the parent

def _child_env():
    """The child's environment: the launcher's PATH and a fixed locale, nothing else."""
    return dict(PATH=os.environ.get("PATH", "/usr/bin:/bin"), LC_ALL="C.UTF-8")


def _read_record(record_path):
    """(parsed record or None, whether anything exists at the path, the refusal or None). A record
    that exists but cannot be opened, read, decoded or parsed is (None, True, its refusal), never a
    pass and never a raise. The open never follows a symlink and never blocks (a FIFO left at the
    path would otherwise hold the parent with no limit), and only a regular file, by fstat, is read;
    the read is bounded to _RECORD_CAP plus one byte, so a record over the cap is refused, where an
    unbounded read of an oversized record could raise under memory pressure instead of refusing."""
    try:
        fd = os.open(record_path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC)
    except FileNotFoundError:
        return None, False, None
    except (OSError, MemoryError):
        return None, True, "record-unreadable"
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None, True, "record-not-regular"
        with os.fdopen(fd, "rb", closefd=False) as handle:
            raw = handle.read(_RECORD_CAP + 1)
    except (OSError, MemoryError):
        return None, True, "record-unreadable"
    finally:
        os.close(fd)
    if len(raw) > _RECORD_CAP:
        return None, True, "record-over-cap"
    try:
        record = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError, MemoryError):
        # RecursionError: json's refusal of deep nesting; MemoryError: a record too large to parse
        # under pressure: such a record is malformed like any other, never a pass and never a raise
        return None, True, "record-malformed"
    return record, True, None


def _capture(directory, payload, record_path, nonce, timeout):
    """Launch the child under _CHILD_SHIM and return (status, stdout, stderr, whether a stream went over
    the cap). Each stream
    keeps at most _STREAM_CAP plus one bytes, so a child flooding either one cannot exhaust the
    parent's memory; past the cap the stream is drained and discarded to its end, so the child still
    finishes and its record is read as usual. A child outliving timeout is killed and reaped and
    subprocess.TimeoutExpired raised; a launch failure raises OSError."""
    command = [sys.executable, "-I", "-B", "-c", _CHILD_SHIM, directory, str(payload),
               str(record_path), nonce]
    deadline = time.monotonic() + timeout
    proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=_child_env())
    out_fd, err_fd = proc.stdout.fileno(), proc.stderr.fileno()
    kept = {out_fd: bytearray(), err_fd: bytearray()}
    over = False
    try:
        with selectors.DefaultSelector() as selector:
            for stream_fd in kept:
                selector.register(stream_fd, selectors.EVENT_READ)
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(command, timeout)
                for key, _ in selector.select(remaining):
                    chunk = os.read(key.fd, 1 << 16)
                    if not chunk:
                        selector.unregister(key.fd)
                        continue
                    buffer = kept[key.fd]
                    if len(buffer) <= _STREAM_CAP:
                        buffer += chunk[:_STREAM_CAP + 1 - len(buffer)]
                    if len(buffer) > _STREAM_CAP:
                        over = True
        status = proc.wait(timeout=max(0.0, deadline - time.monotonic()))
    except BaseException:
        proc.kill()
        proc.wait()
        raise
    finally:
        proc.stdout.close()
        proc.stderr.close()
    return status, bytes(kept[out_fd]), bytes(kept[err_fd]), over


def _verdict(status, stdout, stderr, record, nonce):
    """The parent's pass rule over one child run: 2 unless every leg of the contract holds (both
    streams byte-empty; the record present, well formed, carrying this launch's nonce and no fault;
    its decided code an int in 0 to 255 equal to the child's exit status), and then the decided code
    itself, only where it is 0 or 1; a recorded 1 with exit 1 stays 1, anything else is 2."""
    if stdout or stderr:
        return 2
    if record is None:
        return 2
    if type(record) is not dict or sorted(record) != ["check_ids", "decided_code", "faults", "nonce"]:
        return 2
    if record["nonce"] != nonce:
        return 2
    faults = record["faults"]
    if type(faults) is not list or faults:
        return 2
    check_ids = record["check_ids"]
    if type(check_ids) is not list or any(type(entry) is not str for entry in check_ids):
        return 2
    decided = record["decided_code"]
    if type(decided) is not int or not 0 <= decided <= 255:
        return 2
    if status != decided:
        return 2
    if decided in (0, 1):
        return decided
    return 2


def run_child(payload, record_path, module_dir=None, timeout=CHILD_TIMEOUT):
    """Launch the payload in a child under the contract and return (verdict, detail). The canonical
    child-status guard runs FIRST, as it must in every parent (with SIGCHLD ignored a failed child
    reads as exit 0, so nothing here could be trusted). The detail carries the raw status, both
    streams (each cut at _STREAM_CAP plus one bytes), the parsed record (or None), whether anything
    exists at the record path, and the refusal the bounds or the record read made (or None)."""
    _child_status_refusal_exit(NAME)
    nonce = secrets.token_hex(16)
    directory = os.path.dirname(os.path.abspath(__file__)) if module_dir is None else str(module_dir)
    try:
        status, stdout, stderr, over = _capture(directory, payload, record_path, nonce, timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        return 2, {"status": None, "stdout": b"", "stderr": b"", "record": None,
                   "record_present": False, "problem": None,
                   "launch": "launch failed ({})".format(type(exc).__name__)}
    record, record_present, problem = _read_record(record_path)
    verdict = _verdict(status, stdout, stderr, record, nonce)
    if over:
        verdict, problem = 2, "stream-over-cap"
    return verdict, {"status": status, "stdout": stdout, "stderr": stderr, "record": record,
                     "record_present": record_present, "problem": problem, "launch": None}


# ----------------------------------------------------------------------------------- the self-test

_CLEAN = """import _fail_closed_child as fcc
fcc.check("clean/ran")
fcc.exit_with(0)
"""
_ONE = """import _fail_closed_child as fcc
fcc.check("one/ran")
fcc.exit_with(1)
"""
_RAW_EXIT = """raise SystemExit(0)
"""
_EXIT_AT_IMPORT = """import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fcc_exits_at_import  # noqa: F401  (it calls os._exit(0) at import)
"""
_EXIT_AT_IMPORT_SIDECAR = ("fcc_exits_at_import.py", "import os\nos._exit(0)\n")
_EXIT_IN_MAIN = """import os


def main():
    os._exit(0)


main()
"""
_EXIT_IN_ATEXIT = """import atexit
import os
import _fail_closed_child as fcc
atexit.register(lambda: os._exit(0))
fcc.exit_with(0)
"""
_THREAD_SEVEN = """import sys
import threading
import _fail_closed_child as fcc
worker = threading.Thread(target=lambda: sys.exit(7))
worker.start()
worker.join()
fcc.exit_with(0)
"""
_THREAD_CLEAN = """import sys
import threading
import _fail_closed_child as fcc
first = threading.Thread(target=lambda: sys.exit(0))
first.start()
first.join()
second = threading.Thread(target=sys.exit)
second.start()
second.join()
fcc.exit_with(0)
"""
_LYING_SUBCLASS = """import threading
import _fail_closed_child as fcc


class Lying(SystemExit):
    @property
    def code(self):
        return 0


def run():
    raise Lying(7)


worker = threading.Thread(target=run)
worker.start()
worker.join()
fcc.exit_with(0)
"""
_RAW_THREAD = """import time
import _thread
import _fail_closed_child as fcc
_thread.start_new_thread(lambda: None, ())
time.sleep(0.5)
fcc.exit_with(0)
"""
_OUT_OF_RANGE = """import _fail_closed_child as fcc
fcc.exit_with(256)
"""
_WRONG_NONCE = """import json
import os
import _fail_closed_child as fcc
with open(fcc._STATE["record_path"], "x", encoding="utf-8") as handle:
    json.dump({"nonce": "forged", "decided_code": 0, "check_ids": [], "faults": []}, handle)
os._exit(0)
"""
_PREEXISTING = """import json
import _fail_closed_child as fcc
with open(fcc._STATE["record_path"], "x", encoding="utf-8") as handle:
    json.dump({"nonce": fcc._STATE["nonce"], "decided_code": 0, "check_ids": [], "faults": []}, handle)
fcc.exit_with(0)
"""
_NOISE = """import sys
import _fail_closed_child as fcc
sys.stderr.write("noise\\n")
fcc.exit_with(0)
"""
_FORK_WAITED = """import os
import _fail_closed_child as fcc
pid = os.fork()
if pid == 0:
    fcc.exit_with(0)
os.waitpid(pid, 0)
os._exit(0)
"""
_FORK_UNWAITED = """import os
import time
import _fail_closed_child as fcc
pid = os.fork()
if pid == 0:
    fcc.exit_with(0)
deadline = time.monotonic() + 5
while time.monotonic() < deadline:
    if os.path.exists(fcc._STATE["record_path"]):
        break
    time.sleep(0.05)
os._exit(0)
"""
_CONFIRM_FROM_ATEXIT = """import atexit
import sys
import _fail_closed_child as fcc
try:
    fcc.exit_with(0)
except SystemExit as exc:
    saved = exc
atexit.register(getattr, saved, "code")
sys.exit(1)
"""
_SETTLED_THREE = """import _fail_closed_child as fcc
fcc.exit_with(3)
"""
_CAUGHT_EXIT = """import _fail_closed_child as fcc
try:
    fcc.exit_with(0)
except SystemExit:
    pass
"""
_THREAD_SETTLE = """import threading
import _fail_closed_child as fcc
box = []


def run():
    try:
        fcc.exit_with(0)
    except SystemExit as exc:
        box.append(exc)


worker = threading.Thread(target=run)
worker.start()
worker.join()
raise box[0]
"""
_DEEP_RECORD = """import _fail_closed_child as fcc
with open(fcc._STATE["record_path"], "x", encoding="utf-8") as handle:
    handle.write("[" * 100000)
    handle.write("]" * 100000)
import os
os._exit(0)
"""
_CONFIRM_MATCHING_EXIT = """import atexit
import sys
import _fail_closed_child as fcc
try:
    fcc.exit_with(0)
except SystemExit as exc:
    saved = exc
atexit.register(getattr, saved, "code")
sys.exit(0)
"""
_OVERSIZED_RECORD = """import json
import os
import _fail_closed_child as fcc
with open(fcc._STATE["record_path"], "x", encoding="utf-8") as handle:
    json.dump({"nonce": fcc._STATE["nonce"], "decided_code": 0, "check_ids": [], "faults": []}, handle)
    handle.write(" " * (1 << 20))
os._exit(0)
"""
_UNRAISABLE_PREFIX = """import atexit
import gc
import sys
import _fail_closed_child as fcc


class Boom:
    def __del__(self):
        raise RuntimeError("destructor fault")


def boom():
    raise RuntimeError("atexit fault")


"""
_NOOP_HOOK_ATEXIT = _UNRAISABLE_PREFIX + """sys.unraisablehook = lambda unraisable: None
atexit.register(boom)
fcc.exit_with(0)
"""
_COLLECTING_HOOK_ATEXIT = _UNRAISABLE_PREFIX + """seen = []
sys.unraisablehook = seen.append
atexit.register(boom)
fcc.exit_with(0)
"""
_NOOP_HOOK_DESTRUCTOR = _UNRAISABLE_PREFIX + """sys.unraisablehook = lambda unraisable: None
obj = Boom()
obj.cycle = obj
del obj
fcc.exit_with(0)
"""
_COLLECTING_HOOK_DESTRUCTOR = _UNRAISABLE_PREFIX + """seen = []
sys.unraisablehook = seen.append
obj = Boom()
obj.cycle = obj
del obj
fcc.exit_with(0)
"""
_HOOK_REPLACED_ONLY = _UNRAISABLE_PREFIX + """sys.unraisablehook = lambda unraisable: None
fcc.exit_with(0)
"""
_HOOK_RESTORED = _UNRAISABLE_PREFIX + """installed = sys.unraisablehook
sys.unraisablehook = lambda unraisable: None
obj = Boom()
obj.cycle = obj
del obj
gc.collect()
sys.unraisablehook = installed
fcc.exit_with(0)
"""
_DELETED_HOOK = _UNRAISABLE_PREFIX + """import io
real = sys.stderr
sys.stderr = io.StringIO()
del sys.unraisablehook
obj = Boom()
obj.cycle = obj
del obj
gc.collect()
sys.stderr = real
fcc.exit_with(0)
"""
_DELETED_HOOK_RESTORED = _UNRAISABLE_PREFIX + """import io
real = sys.stderr
saved = sys.unraisablehook
sys.stderr = io.StringIO()
del sys.unraisablehook
obj = Boom()
obj.cycle = obj
del obj
gc.collect()
sys.unraisablehook = saved
sys.stderr = real
fcc.exit_with(0)
"""
_THREAD_HOOK_REPLACED = """import threading
import _fail_closed_child as fcc
threading.excepthook = lambda args: None
worker = threading.Thread(target=lambda: 1 / 0)
worker.start()
worker.join()
fcc.exit_with(0)
"""
_STDERR_REPLACED = """import io
import sys
import _fail_closed_child as fcc
sys.stderr = io.StringIO()
fcc.exit_with(0)
"""
_DUNDER_STDERR_REPLACED = """import io
import sys
import _fail_closed_child as fcc
sys.__stderr__ = io.StringIO()
fcc.exit_with(0)
"""
_FD2_REDIRECTED = """import os
import _fail_closed_child as fcc
fd = os.open(os.devnull, os.O_WRONLY)
os.dup2(fd, 2)
os.close(fd)
fcc.exit_with(0)
"""
_STDERR_WRITE_SHADOWED = """import sys
import _fail_closed_child as fcc


class Boom:
    def __del__(self):
        raise RuntimeError("teardown fault")


keeper = Boom()
sys.stderr.write = lambda text: len(text)
fcc.exit_with(0)
"""
_STDERR_FLUSH_SHADOWED = """import sys
import _fail_closed_child as fcc
sys.stderr.flush = lambda: None
fcc.exit_with(0)
"""
_DUNDER_STDERR_WRITE_SHADOWED = """import sys
import _fail_closed_child as fcc
sys.__stderr__.write = lambda text: len(text)
fcc.exit_with(0)
"""
_STDERR_PLAIN_ATTR = """import sys
import _fail_closed_child as fcc
sys.stderr.plain_note = "kept"
fcc.exit_with(0)
"""
_BUFFER_WRITE_SHADOWED = """import sys
import _fail_closed_child as fcc


class Boom:
    def __del__(self):
        raise RuntimeError("teardown fault")


keeper = Boom()
sys.stderr.buffer.write = lambda data: len(data)
fcc.exit_with(0)
"""
_RAW_WRITE_SHADOWED = """import sys
import _fail_closed_child as fcc


class Boom:
    def __del__(self):
        raise RuntimeError("teardown fault")


keeper = Boom()
sys.stderr.buffer.raw.write = lambda data: len(data)
fcc.exit_with(0)
"""
_STDERR_EMPTY_WRITE = """import sys
import _fail_closed_child as fcc
sys.stderr.write("")
sys.stderr.flush()
fcc.exit_with(0)
"""
_EXECUTOR_HONEST = """import concurrent.futures
import _fail_closed_child as fcc
with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
    futures = [pool.submit(sum, range(100)), pool.submit(sum, range(10))]
    if [item.result() for item in futures] != [4950, 45]:
        raise RuntimeError("the executor results are wrong")
fcc.exit_with(0)
"""
_INVOKE_REPLACED_AT_START = """import threading
import _fail_closed_child as fcc
worker = threading.Thread(target=lambda: 1 / 0)
worker._invoke_excepthook = lambda thread: None
worker.start()
worker.join()
fcc.exit_with(0)
"""
_INVOKE_REPLACED_IN_RUN = """import threading
import _fail_closed_child as fcc


def run():
    threading.current_thread()._invoke_excepthook = lambda thread: None
    raise RuntimeError("swallowed")


worker = threading.Thread(target=run)
worker.start()
worker.join()
fcc.exit_with(0)
"""
# The disguise is built with the real constructor so the args stay a genuine ExceptHookArgs: the
# excepthook the genuine _invoke_excepthook calls accepts it and, seeing a SystemExit, silently
# ignores it, so the thread fault is swallowed without the excepthook raising. A fake of the wrong
# type would instead make that excepthook raise, and the thread machinery would then hand the raise
# to whatever sys.excepthook the host installed, so the vector's verdict would turn on the host, not
# on the detection under test. The sequence the genuine _invoke_excepthook passes is
# [exc_type, exc_value, exc_traceback, thread]; seq[3] keeps the real thread so the hook can name it.
_EXCEPTHOOKARGS_REPLACED = """import threading
import _fail_closed_child as fcc
real = threading.ExceptHookArgs
threading.ExceptHookArgs = lambda seq: real([SystemExit, SystemExit(0), None, seq[3]])
worker = threading.Thread(target=lambda: 1 / 0)
worker.start()
worker.join()
fcc.exit_with(0)
"""
_EXCEPTHOOKARGS_RESTORED = """import threading
import _fail_closed_child as fcc
real = threading.ExceptHookArgs
threading.ExceptHookArgs = lambda seq: real([SystemExit, SystemExit(0), None, seq[3]])
worker = threading.Thread(target=lambda: 1 / 0)
worker.start()
worker.join()
threading.ExceptHookArgs = real
fcc.exit_with(0)
"""
_EXC_INFO_CAPTURED = """import sys
import threading
import _fail_closed_child as fcc
real = sys.exc_info
sys.exc_info = lambda: (SystemExit, SystemExit(0), None)
worker = threading.Thread(target=lambda: 1 / 0)
sys.exc_info = real
worker.start()
worker.join()
fcc.exit_with(0)
"""
_INVOKE_CELL_REBOUND = """import sys
import threading
import _fail_closed_child as fcc
worker = threading.Thread(target=lambda: 1 / 0)
for cell in worker._invoke_excepthook.__closure__:
    try:
        held = cell.cell_contents
    except ValueError:
        continue
    if held is sys.exc_info:
        cell.cell_contents = lambda: (SystemExit, SystemExit(0), None)
worker.start()
worker.join()
fcc.exit_with(0)
"""
_BOOTSTRAP_INNER_REPLACED = """import threading
import _fail_closed_child as fcc
threading.Thread._bootstrap_inner = lambda self: None
fcc.exit_with(0)
"""
_BOOTSTRAP_INNER_SUBCLASSED = """import threading
import _fail_closed_child as fcc


class Quiet(threading.Thread):
    def _bootstrap_inner(self):
        self._started.set()
        try:
            self.run()
        except BaseException:
            pass


worker = Quiet(target=lambda: 1 / 0)
worker.start()
worker.join()
fcc.exit_with(0)
"""
_ITIMER_ARMED_HONEST = """import signal
import threading
import time
import _fail_closed_child as fcc
fired = []
signal.signal(signal.SIGPROF, lambda sig, frame: fired.append(sig))
signal.signal(signal.SIGVTALRM, lambda sig, frame: fired.append(sig))
signal.setitimer(signal.ITIMER_PROF, 3600.0, 0.0)
signal.setitimer(signal.ITIMER_VIRTUAL, 3600.0, 0.0)


def work():
    deadline = time.process_time() + 0.05
    while time.process_time() < deadline:
        sum(range(512))


worker = threading.Thread(target=work)
worker.start()
worker.join()
work()
if signal.getitimer(signal.ITIMER_PROF)[0] <= 0.0:
    raise RuntimeError("the armed profiling timer did not stay armed")
if signal.getitimer(signal.ITIMER_VIRTUAL)[0] <= 0.0:
    raise RuntimeError("the armed virtual timer did not stay armed")
if fired:
    raise RuntimeError("a 3600 s CPU timer cannot have expired during this run")
fcc.exit_with(0)
"""
_ITIMER_SIGNALS_DURING_RUN = """import signal
import threading
import time
import _fail_closed_child as fcc
fired = []
signal.signal(signal.SIGPROF, lambda sig, frame: fired.append(sig))
signal.signal(signal.SIGVTALRM, lambda sig, frame: fired.append(sig))
signal.setitimer(signal.ITIMER_PROF, 0.02, 0.02)
signal.setitimer(signal.ITIMER_VIRTUAL, 0.025, 0.025)


def work():
    deadline = time.process_time() + 0.3
    while time.process_time() < deadline:
        sum(range(512))


worker = threading.Thread(target=work)
worker.start()
worker.join()
work()
signal.setitimer(signal.ITIMER_PROF, 0.0, 0.0)
signal.setitimer(signal.ITIMER_VIRTUAL, 0.0, 0.0)
if not fired:
    raise RuntimeError("no CPU-timer signal was delivered during the run")
fcc.exit_with(0)
"""
_FORGED_SYMLINK_RECORD = """import json
import os
import _fail_closed_child as fcc
target = fcc._STATE["record_path"] + ".target"
with open(target, "x", encoding="utf-8") as handle:
    json.dump({"nonce": fcc._STATE["nonce"], "decided_code": 0, "check_ids": [], "faults": []}, handle)
os.symlink(target, fcc._STATE["record_path"])
os._exit(0)
"""


def _sized_record(total):
    """A payload writing a genuine record padded with spaces to exactly total bytes, then os._exit(0)."""
    return ("import json\nimport os\nimport _fail_closed_child as fcc\n"
            "body = json.dumps({{'nonce': fcc._STATE['nonce'], 'decided_code': 0, 'check_ids': [], "
            "'faults': []}})\n"
            "with open(fcc._STATE['record_path'], 'x', encoding='utf-8') as handle:\n"
            "    handle.write(body + ' ' * ({} - len(body)))\n"
            "os._exit(0)\n".format(total))


def _sized_stdout(total):
    """A payload writing exactly total bytes to stdout, then settling 0."""
    return ("import sys\nimport _fail_closed_child as fcc\n"
            "sys.stdout.buffer.write(b'x' * {})\nsys.stdout.flush()\nfcc.exit_with(0)\n".format(total))


_RECORD_AT_CAP = _sized_record(1 << 20)
_RECORD_CAP_PLUS_ONE = _sized_record((1 << 20) + 1)
_STDOUT_AT_CAP = _sized_stdout(1 << 16)
_STDOUT_CAP_PLUS_ONE = _sized_stdout((1 << 16) + 1)
_SPARSE_RECORD = """import os
import _fail_closed_child as fcc
with open(fcc._STATE["record_path"], "xb") as handle:
    handle.truncate(4 << 30)
os._exit(0)
"""
_FIFO_RECORD = """import os
import _fail_closed_child as fcc
os.mkfifo(fcc._STATE["record_path"])
os._exit(0)
"""
_DEV_ZERO_SYMLINK = """import os
import _fail_closed_child as fcc
os.symlink("/dev/zero", fcc._STATE["record_path"])
os._exit(0)
"""

# The isolated parent: an ISOLATED vector runs run_child itself in a separate process under its own
# address-space limit and a wall-clock timeout, so a flip that reads without a bound or blocks on a
# FIFO is observed red as a refusal or a timeout, never by exhausting or holding the self-test.
_ISOLATED_AS_LIMIT = 1 << 30
_ISOLATED_TIMEOUT = 60
_ISOLATED_SHIM = ("import json, resource, sys; "
                  "resource.setrlimit(resource.RLIMIT_AS, (int(sys.argv[4]), int(sys.argv[4]))); "
                  "sys.path.insert(0, sys.argv[1]); import _fail_closed_child as m; "
                  "v, d = m.run_child(sys.argv[2], sys.argv[3], timeout=30); "
                  "print(json.dumps(list(m._summary(v, d))))")


def _run_isolated(module_dir, payload, record_path):
    """The summary of run_child run in an isolated parent process (a list from its JSON), or a
    one-element tuple naming why none was read (a timeout, a nonzero exit, unparsable output). The
    isolated parent leads its own session and its whole group is killed when it ends or times out."""
    import signal
    command = [sys.executable, "-I", "-B", "-c", _ISOLATED_SHIM, str(module_dir), str(payload),
               str(record_path), str(_ISOLATED_AS_LIMIT)]
    proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            env=_child_env(), start_new_session=True)
    try:
        out, err = proc.communicate(timeout=_ISOLATED_TIMEOUT)
    except subprocess.TimeoutExpired:
        out, err = None, None
    finally:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        if proc.returncode is None:
            proc.communicate()
    if out is None:
        return ("isolated parent timed out",)
    if proc.returncode != 0:
        return ("isolated parent exited {}: {!r}".format(proc.returncode, err[-400:]),)
    try:
        return tuple(json.loads(out.decode("utf-8")))
    except (UnicodeDecodeError, ValueError):
        return ("isolated parent output unparsable",)


# The disclosures pinned in the module docstring, compared with its whitespace collapsed.
DISCLOSURES = (
    ("contract/spoof-residual-disclosed-in-record-terms",
     "while a spoof followed by an exit carrying the MATCHING status still reads as the decided code "
     "(the spoofed-confirmation residual disclosed in NOT YET COVERED below, pinned by its vector)",
     "spoof-disclosure-record-removed"),
    ("contract/spoof-residual-disclosed-in-not-yet-covered",
     "A spoofed confirmation whose later exit carries the MATCHING status (a caught exit_with(0), a "
     "frameless read of that exception's code arranged from atexit, then a raw sys.exit(0) or falling "
     "off the end) still reads as the decided code; that residual is not closed here, and its vector "
     "pins the disclosed behaviour.",
     "spoof-disclosure-not-yet-covered-removed"),
    ("contract/residual-class-and-channel-list-disclosed-from-their-start",
     "Neither, in general, is loaded code that alters any object the reporting machinery reads on "
     "its way to the record or to fd 2 and restores or stands in for it between the checks that "
     "observe it: the checks pin each named object by identity at fixed moments (install, each "
     "thread's start and end, and exit), never continuously, so a diversion confined to the window "
     "between two checks still makes a fault read as a pass in process. The channels checked, each "
     "coverable only at those moments, are sys.unraisablehook (a deletion raises no "
     "sys.unraisablehook event, so a fault while it is absent is recorded nowhere and reported only "
     "to sys.stderr), threading.excepthook, threading.Thread._bootstrap_inner, a thread's own "
     "_invoke_excepthook slot with its closure cells, threading.ExceptHookArgs, sys.stderr, "
     "sys.__stderr__, the write or flush member resolved at any recorded layer of either stream "
     "(the object, its buffer, its raw file), or the file open on fd 2;",
     "channel-list-cut"),
    ("contract/restored-channel-residual-disclosed-in-not-yet-covered",
     "every such restored channel, and loaded code standing in for the thread-run machinery so no "
     "installed check runs at all (a threading.Thread subclass that overrides _bootstrap_inner, the "
     "run leg below the _bootstrap start target the audit hook verifies, so a _bootstrap override is "
     "already a recorded start fault while a _bootstrap_inner override is not), is the same "
     "loaded-code-replacing-the-reporting-machinery residual; the deleted-hook-restored and "
     "subclassed-bootstrap-inner vectors pin the two disclosed passes.",
     "restored-channel-disclosure-removed"),
)

# One flip per vector family, each an exact single occurrence in this module's source, each written
# here as adjacent string literals so the table's own text never matches the code it names.
FLIPS = {
    "record-write-broken": (
        '            with open(record_path, "x", enc' 'oding="utf-8") as handle:',
        '            with open(record_path + ".elsewhere", "x", encoding="utf-8") as handle:'),
    "nonzero-mapped-to-two": (
        '    if decided in (0, 1' '):',
        '    if decided == 0:'),
    "unsettled-defaults-to-zero": (
        '        decided = _STATE["decided"] if settl' 'ed else None',
        '        decided = _STATE["decided"] if settled else 0'),
    "exit-zero-without-record-accepted": (
        '    if record is None:\n        ret' 'urn 2',
        '    if record is None:\n        return 0 if status == 0 else 2'),
    "any-thread-systemexit-clean": (
        '        clean = (issubclass(type(exc), SystemExit)\n'
        '                 and _clean_status(_SYSTEM' '_EXIT_CODE.__get__(exc, SystemExit)))',
        '        clean = issubclass(type(exc), SystemExit)'),
    "any-thread-systemexit-fault": (
        '    return code is None or (type(code) is int' ' and code == 0)',
        '    return False'),
    "code-read-through-getattr": (
        '        clean = (issubclass(type(exc), SystemExit)\n'
        '                 and _clean_status(_SYSTEM_EXIT' '_CODE.__get__(exc, SystemExit)))',
        '        clean = (issubclass(type(exc), SystemExit)\n'
        '                 and _clean_status(getattr(exc, "code", None)))'),
    "audit-ignores-thread-starts": (
        '        if not _genuine_bootstrap(arg' 's[0]):',
        '        if False and not _genuine_bootstrap(args[0]):'),
    "decided-code-unmapped": (
        '    if type(code) is int and 0 <= co' 'de <= 255:',
        '    if type(code) is int:'),
    "nonce-check-skipped": (
        '    if record["nonce"] != non' 'ce:\n        return 2',
        '    if False and record["nonce"] != nonce:\n        return 2'),
    "record-create-not-exclusive": (
        '            with open(record_path, "x", encodi' 'ng="utf-8") as handle:',
        '            with open(record_path, "w", encoding="utf-8") as handle:'),
    "stream-rule-ignored": (
        '    if stdout or std' 'err:\n        return 2',
        '    if False and (stdout or stderr):\n        return 2'),
    "fork-settle-accepted": (
        '        if _getpid() != _STATE["armed' '_pid"]:',
        '        if False and _getpid() != _STATE["armed_pid"]:'),
    "status-decided-mismatch-ignored": (
        '    if status != deci' 'ded:\n        return 2',
        '    if False and status != decided:\n        return 2'),
    "record-then-forced-exit": (
        '            status = None   # defer to the inter' "preter's own exit status",
        '            status = decided'),
    "any-decided-code-passes": (
        '    if decided in (0, ' '1):\n        return decided\n    return 2',
        '    return decided'),
    "caught-exit-still-settles": (
        '                   and _STATE["confirmed"] is _STATE["rai' 'sed"])',
        '                   )'),
    "thread-settle-accepted": (
        '    if threading.current_thread() is not threading.main_th' 'read():',
        '    if False and threading.current_thread() is not threading.main_thread():'),
    "deep-record-raises": (
        '    except (UnicodeDecodeError, ValueError, Recursion' 'Error, MemoryError):',
        '    except (UnicodeDecodeError, ValueError):'),
    "oversized-record-accepted": (
        '    if len(raw) > _RECORD' '_CAP:\n        return None, True',
        '    if False and len(raw) > _RECORD_CAP:\n        return None, True'),
    "unraisable-faults-dropped": (
        '    _STATE["faults"].append(message)  # recorded before any hoo' 'k runs',
        '    pass'),
    "unraisable-audit-ignored": (
        '        if event == _UNRAISABLE' '_EVENT:',
        '        if False and event == _UNRAISABLE_EVENT:'),
    "replaced-unraisable-hook-accepted": (
        '        if getattr(sys, "unraisablehook", None) is not _wrap' 'per:',
        '        if False and getattr(sys, "unraisablehook", None) is not _wrapper:'),
    "replaced-thread-excepthook-accepted": (
        '        if getattr(threading, "excepthook", None) is not _thread_wrap' 'per:',
        '        if False and getattr(threading, "excepthook", None) is not _thread_wrapper:'),
    "replaced-stderr-accepted": (
        '        if sys.stderr is not _STATE["start_std' 'err"]:',
        '        if False and sys.stderr is not _STATE["start_stderr"]:'),
    "replaced-dunder-stderr-accepted": (
        '        if sys.__stderr__ is not _STATE["start_dunder_std' 'err"]:',
        '        if False and sys.__stderr__ is not _STATE["start_dunder_stderr"]:'),
    "redirected-fd2-accepted": (
        '        if not fd2_' 'same:',
        '        if False and not fd2_same:'),
    "record-cap-inclusive": (
        '    if len(raw) > _RECORD_C' 'AP:',
        '    if len(raw) >= _RECORD_CAP:'),
    "record-read-unbounded": (
        '            raw = handle.read(_RECORD_CAP' ' + 1)',
        '            raw = handle.read()'),
    "record-open-blocking": (
        '        fd = os.open(record_path, os.O_RDONLY | os.O_NON' 'BLOCK | os.O_NOFOLLOW | os.O_CLOEXEC)',
        '        fd = os.open(record_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)'),
    "record-type-unchecked": (
        '        if not stat.S_ISREG(os.fstat(fd).st_mo' 'de):',
        '        if False and not stat.S_ISREG(os.fstat(fd).st_mode):'),
    "record-open-follows-symlink": (
        '        fd = os.open(record_path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOL' 'LOW | os.O_CLOEXEC)',
        '        fd = os.open(record_path, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)'),
    "stream-cap-inclusive": (
        '                    if len(buffer) > _STREAM' '_CAP:',
        '                    if len(buffer) >= _STREAM_CAP:'),
    "stream-cap-unchecked": (
        '                    if len(buffer) > _STREAM_C' 'AP:\n                        over = True',
        '                    if False:\n                        over = True'),
    "stream-member-divert-ignored": (
        '        if "write" in members or "flu' 'sh" in members:',
        '        if False and ("write" in members or "flush" in members):'),
    "thread-invoke-fault-dropped": (
        '    if _genuine_invoke(thre' 'ad):\n        return',
        '    if True:\n        return'),
    "replaced-bootstrap-inner-accepted": (
        '        if bootstrap_now is not _bootstrap_ho' 'ok:',
        '        if False and bootstrap_now is not _bootstrap_hook:'),
    "restored-channel-disclosure-removed": (
        'residual; the deleted-hook-restored\nand subclassed-bootstrap-inner vectors pin the two '
        'disclosed pas' 'ses.',
        'residual.'),
    "replaced-excepthookargs-accepted": (
        '    if _THREADING_DICT.get("ExceptHookArgs") is _EXCEPT_HOOK' '_ARGS:\n        return',
        '    if True:\n        return'),
    "invoke-closure-cells-unchecked": (
        '            if checked and cell.cell_contents is not cap' 'tured:',
        '            if False and cell.cell_contents is not captured:'),
    "stream-layer-walk-stopped": (
        '    below = getattr(layer, "buf' 'fer", None)\n    if below is None:\n'
        '        below = getattr(layer, "raw", None)\n    return below',
        '    return None'),
    "channel-list-cut": (
        "threading.Thread._bootstrap_inner, a thread's own _invoke_excepthook slot with its closure cells,\n"
        'threading.ExceptHookArgs, sys.stderr, sys.__stderr__, the write or flush member resolved at any\n'
        'recorded layer of either stream (the object, its buffer, its raw fi' 'le), or the file open on fd 2;',
        'threading.Thread._bootstrap_inner, or the file open on fd 2;'),
    "spoof-disclosure-record-removed": (
        'MATCHING status still reads as the decided code (the spoofed-confirmation residual disclosed in NOT\n'
        'YET COVERED below, pinned by its vec' 'tor)',
        'MATCHING status'),
    "spoof-disclosure-not-yet-covered-removed": (
        'or falling off the end) still reads as the decided code; that residual is not closed here, and its\n'
        'vector pins the disclosed behavi' 'our.',
        'or falling off the end).'),
}

# Vectors whose parent runs isolated (_run_isolated): a flip of theirs can block or exhaust memory.
ISOLATED = frozenset(("contract/sparse-record-read-bounded", "contract/fifo-record-not-waited-on",
                      "contract/fifo-record-not-regular-refused", "contract/dev-zero-symlink-refused"))

# (check id, payload source, sidecar file or None, expected summary, flip key). A summary is
# (verdict, whether anything exists at the record path, whether a parsed record lists a fault), so a
# flip whose verdict agrees for another reason (the default excepthook's traceback already dirties
# stderr for a SystemExit subclass) is still observed red on the record it wrote.
VECTORS = (
    ("contract/clean-run-passes", _CLEAN, None, (0, True, False, None), "record-write-broken"),
    ("contract/recorded-one-stays-one", _ONE, None, (1, True, False, None), "nonzero-mapped-to-two"),
    ("contract/raw-systemexit-zero-refused", _RAW_EXIT, None, (2, False, False, None),
     "unsettled-defaults-to-zero"),
    ("contract/os-exit-at-import-refused", _EXIT_AT_IMPORT, _EXIT_AT_IMPORT_SIDECAR, (2, False, False, None),
     "exit-zero-without-record-accepted"),
    ("contract/os-exit-in-main-refused", _EXIT_IN_MAIN, None, (2, False, False, None),
     "exit-zero-without-record-accepted"),
    ("contract/os-exit-in-atexit-refused", _EXIT_IN_ATEXIT, None, (2, False, False, None),
     "exit-zero-without-record-accepted"),
    ("contract/thread-systemexit-seven-refused", _THREAD_SEVEN, None, (2, True, True, None),
     "any-thread-systemexit-clean"),
    ("contract/thread-clean-systemexit-passes", _THREAD_CLEAN, None, (0, True, False, None),
     "any-thread-systemexit-fault"),
    ("contract/lying-code-property-refused", _LYING_SUBCLASS, None, (2, True, True, None),
     "code-read-through-getattr"),
    ("contract/raw-thread-start-refused", _RAW_THREAD, None, (2, True, True, None),
     "audit-ignores-thread-starts"),
    ("contract/decided-256-refused", _OUT_OF_RANGE, None, (2, False, False, None), "decided-code-unmapped"),
    ("contract/wrong-nonce-refused", _WRONG_NONCE, None, (2, True, False, None), "nonce-check-skipped"),
    ("contract/preexisting-record-refused", _PREEXISTING, None, (2, True, False, None),
     "record-create-not-exclusive"),
    ("contract/stderr-noise-refused", _NOISE, None, (2, True, False, None), "stream-rule-ignored"),
    ("contract/fork-settle-reaped-refused", _FORK_WAITED, None, (2, False, False, None),
     "fork-settle-accepted"),
    ("contract/fork-settle-unreaped-refused", _FORK_UNWAITED, None, (2, False, False, None),
     "fork-settle-accepted"),
    ("contract/spoofed-confirm-raw-exit-refused", _CONFIRM_FROM_ATEXIT, None, (2, True, False, None),
     "status-decided-mismatch-ignored"),
    ("contract/record-defers-to-real-status", _CONFIRM_FROM_ATEXIT, None, (2, True, False, None),
     "record-then-forced-exit"),
    ("contract/settled-three-refused", _SETTLED_THREE, None, (2, True, False, None),
     "any-decided-code-passes"),
    ("contract/caught-exit-settles-nothing", _CAUGHT_EXIT, None, (2, False, False, None),
     "caught-exit-still-settles"),
    ("contract/thread-settle-rethrown-refused", _THREAD_SETTLE, None, (2, False, False, None),
     "thread-settle-accepted"),
    ("contract/deep-record-refused", _DEEP_RECORD, None, (2, True, False, "record-malformed"),
     "deep-record-raises"),
    # the disclosed spoofed-confirmation residual: a spoof whose later exit carries the matching
    # status reads as the decided code; this vector pins that disclosure, it does not endorse it
    ("contract/spoofed-confirm-matching-exit-disclosed", _CONFIRM_MATCHING_EXIT, None,
     (0, True, False, None), "record-write-broken"),
    ("contract/oversized-record-refused", _OVERSIZED_RECORD, None, (2, True, False, "record-over-cap"),
     "oversized-record-accepted"),
    # a fault in an atexit callback or a destructor after exit_with(0), under a replaced hook that
    # reports nothing (a no-op, or a collecting hook as pytest installs)
    ("contract/noop-hook-atexit-fault-refused", _NOOP_HOOK_ATEXIT, None, (2, True, True, None),
     "unraisable-faults-dropped"),
    ("contract/collecting-hook-atexit-fault-refused", _COLLECTING_HOOK_ATEXIT, None,
     (2, True, True, None), "unraisable-faults-dropped"),
    ("contract/noop-hook-destructor-fault-refused", _NOOP_HOOK_DESTRUCTOR, None, (2, True, True, None),
     "unraisable-faults-dropped"),
    ("contract/collecting-hook-destructor-fault-refused", _COLLECTING_HOOK_DESTRUCTOR, None,
     (2, True, True, None), "unraisable-faults-dropped"),
    ("contract/unraisable-hook-replaced-refused", _HOOK_REPLACED_ONLY, None, (2, True, True, None),
     "replaced-unraisable-hook-accepted"),
    ("contract/unraisable-hook-restored-refused", _HOOK_RESTORED, None, (2, True, True, None),
     "unraisable-audit-ignored"),
    # the reporting channels required back by identity at the finalizer: a deleted unraisable hook
    # (its fault raises no event and reaches only the replaced stderr), a replaced threading
    # excepthook, a replaced sys.stderr or sys.__stderr__ and a re-opened fd 2 are each a recorded
    # fault, while the deleted hook RESTORED under a redirected-then-restored stderr is the disclosed
    # replaced-reporting-machinery residual its vector pins (it does not endorse it)
    ("contract/deleted-unraisable-hook-refused", _DELETED_HOOK, None, (2, True, True, None),
     "replaced-unraisable-hook-accepted"),
    ("contract/deleted-hook-restored-disclosed", _DELETED_HOOK_RESTORED, None, (0, True, False, None),
     "record-write-broken"),
    ("contract/replaced-thread-excepthook-refused", _THREAD_HOOK_REPLACED, None, (2, True, True, None),
     "replaced-thread-excepthook-accepted"),
    ("contract/replaced-stderr-refused", _STDERR_REPLACED, None, (2, True, True, None),
     "replaced-stderr-accepted"),
    ("contract/replaced-dunder-stderr-refused", _DUNDER_STDERR_REPLACED, None, (2, True, True, None),
     "replaced-dunder-stderr-accepted"),
    ("contract/redirected-fd2-refused", _FD2_REDIRECTED, None, (2, True, True, None),
     "redirected-fd2-accepted"),
    # the record read bound at each end: exactly the cap is read, one byte over is refused, and a
    # large sparse record is refused by the bound under an address-space limit, not by running out.
    # The sparse, FIFO-type and device-symlink vectors pin the diagnostic label only: their verdict
    # is 2 with or without the flipped guard (such a record never parses as a pass), the no-blocking
    # bound is pinned by the fifo-not-waited-on vector, and the forged-symlink vector pins the
    # verdict itself (its flip reads a forged regular record through the symlink as a pass)
    ("contract/record-at-cap-read", _RECORD_AT_CAP, None, (0, True, False, None),
     "record-cap-inclusive"),
    ("contract/record-cap-plus-one-refused", _RECORD_CAP_PLUS_ONE, None,
     (2, True, False, "record-over-cap"), "oversized-record-accepted"),
    ("contract/sparse-record-read-bounded", _SPARSE_RECORD, None, (2, True, False, "record-over-cap"),
     "record-read-unbounded"),
    ("contract/fifo-record-not-waited-on", _FIFO_RECORD, None, (2, True, False, "record-not-regular"),
     "record-open-blocking"),
    ("contract/fifo-record-not-regular-refused", _FIFO_RECORD, None,
     (2, True, False, "record-not-regular"), "record-type-unchecked"),
    ("contract/dev-zero-symlink-refused", _DEV_ZERO_SYMLINK, None,
     (2, True, False, "record-unreadable"), "record-open-follows-symlink"),
    ("contract/record-symlink-to-forged-refused", _FORGED_SYMLINK_RECORD, None,
     (2, True, False, "record-unreadable"), "record-open-follows-symlink"),
    # the stream capture bound at each end: exactly the cap is kept, one byte over is refused as
    # such; both vectors pin the over-cap diagnostic label and the memory bound only, since in this
    # byte-empty-streams mode any stream byte already reads verdict 2 with or without the cap guard
    ("contract/stdout-at-cap-kept", _STDOUT_AT_CAP, None, (2, True, False, None),
     "stream-cap-inclusive"),
    ("contract/stdout-cap-plus-one-refused", _STDOUT_CAP_PLUS_ONE, None,
     (2, True, False, "stream-over-cap"), "stream-cap-unchecked"),
    # the same stream object with a diverted member: an instance attribute shadowing write or flush
    # on sys.stderr or sys.__stderr__ passes the identity check while swallowing every report (the
    # write-shadow payload also faults in a destructor at teardown, after the record exists, the
    # report the shadow would have silenced), so each is a recorded fault; a plain instance
    # attribute on the same object (as CPython's own startup sets mode) stays a pass
    ("contract/shadowed-stderr-write-refused", _STDERR_WRITE_SHADOWED, None, (2, True, True, None),
     "stream-member-divert-ignored"),
    ("contract/shadowed-stderr-flush-refused", _STDERR_FLUSH_SHADOWED, None, (2, True, True, None),
     "stream-member-divert-ignored"),
    ("contract/shadowed-dunder-stderr-refused", _DUNDER_STDERR_WRITE_SHADOWED, None,
     (2, True, True, None), "stream-member-divert-ignored"),
    ("contract/stderr-plain-attr-passes", _STDERR_PLAIN_ATTR, None, (0, True, False, None),
     "record-write-broken"),
    # the layers below the recorded stream objects: a write shadowed on sys.stderr's buffer or on its
    # raw file swallows a report (here the teardown fault's) while the top object and its own members
    # are untouched, so the finalizer's walk records a fault at the shadowed layer; an empty write
    # pushed through every layer, and a ThreadPoolExecutor run whose worker threads start and end
    # under the per-thread checks, each stay an honest pass
    ("contract/shadowed-buffer-write-refused", _BUFFER_WRITE_SHADOWED, None, (2, True, True, None),
     "stream-layer-walk-stopped"),
    ("contract/shadowed-raw-write-refused", _RAW_WRITE_SHADOWED, None, (2, True, True, None),
     "stream-layer-walk-stopped"),
    ("contract/empty-stderr-write-passes", _STDERR_EMPTY_WRITE, None, (0, True, False, None),
     "record-write-broken"),
    ("contract/thread-pool-executor-passes", _EXECUTOR_HONEST, None, (0, True, False, None),
     "record-write-broken"),
    # what the genuine _invoke_excepthook READS, not the function object: threading.ExceptHookArgs
    # replaced so every hook receives a fake clean SystemExit (left in place, or restored only after
    # the join), a sys.exc_info swapped only while Thread() ran so the closure captured the fake, and
    # the captured cell rebound in place afterwards; each is a recorded fault at the thread's start
    # or end (the constructor at exit too)
    ("contract/replaced-excepthookargs-refused", _EXCEPTHOOKARGS_REPLACED, None, (2, True, True, None),
     "replaced-excepthookargs-accepted"),
    ("contract/restored-excepthookargs-refused", _EXCEPTHOOKARGS_RESTORED, None, (2, True, True, None),
     "replaced-excepthookargs-accepted"),
    ("contract/captured-exc-info-refused", _EXC_INFO_CAPTURED, None, (2, True, True, None),
     "invoke-closure-cells-unchecked"),
    ("contract/rebound-invoke-cell-refused", _INVOKE_CELL_REBOUND, None, (2, True, True, None),
     "invoke-closure-cells-unchecked"),
    # the per-thread _invoke_excepthook (what _bootstrap_inner calls instead of threading.excepthook,
    # so a replacement there never reaches the installed wrapper), replaced before the start or from
    # inside the thread; a replaced Thread._bootstrap_inner wrapper is required back at exit; and a
    # Thread subclass running its own _bootstrap_inner, under which no installed check runs at all,
    # is the disclosed replaced-reporting-machinery residual its vector pins (it does not endorse it)
    ("contract/thread-invoke-replaced-at-start-refused", _INVOKE_REPLACED_AT_START, None,
     (2, True, True, None), "thread-invoke-fault-dropped"),
    ("contract/thread-invoke-replaced-in-run-refused", _INVOKE_REPLACED_IN_RUN, None,
     (2, True, True, None), "thread-invoke-fault-dropped"),
    ("contract/replaced-bootstrap-inner-refused", _BOOTSTRAP_INNER_REPLACED, None,
     (2, True, True, None), "replaced-bootstrap-inner-accepted"),
    ("contract/subclassed-bootstrap-inner-disclosed", _BOOTSTRAP_INNER_SUBCLASSED, None,
     (0, True, False, None), "record-write-broken"),
    # an honest payload under armed CPU timers: a thread run and settled while ITIMER_PROF and
    # ITIMER_VIRTUAL are armed at 3600 s and left armed at the exit (the shape the watchdog timer
    # fixtures leave), and one whose short repeating timers really deliver SIGPROF and SIGVTALRM
    # into the run (so the per-thread checks and the finalizer run under live timer signals, the
    # timers disarmed before the exit so no post-shutdown expiry can end the process), each still
    # read their decided 0 with no recorded fault
    ("contract/armed-cpu-timers-honest-passes", _ITIMER_ARMED_HONEST, None,
     (0, True, False, None), "record-write-broken"),
    ("contract/cpu-timer-signals-during-run-passes", _ITIMER_SIGNALS_DURING_RUN, None,
     (0, True, False, None), "record-write-broken"),
)


def _summary(verdict, detail):
    """(verdict, record present, record lists a fault, the refusal the bounds or the record read made):
    the shape every vector is compared on."""
    record = detail["record"]
    listed = type(record) is dict and bool(record.get("faults"))
    return verdict, detail["record_present"], listed, detail.get("problem")


def _load_mutant(tmp, key, source, old, new):
    """Load a copy of this module with one flip applied, written to its own scratch directory (the
    copy carries the inline child-status guard, so its children import nothing else from there):
    (module, None), or (None, the reason) when the flip does not match the source exactly once."""
    if source.count(old) != 1:
        return None, "flip {} matches {} times, not once".format(key, source.count(old))
    mutant_dir = os.path.join(tmp, "flip-" + key)
    os.makedirs(mutant_dir)
    path = os.path.join(mutant_dir, "_fail_closed_child.py")
    with open(path, "x", encoding="utf-8") as handle:
        handle.write(source.replace(old, new))
    spec = importlib.util.spec_from_file_location("_fail_closed_child_flip_" + key.replace("-", "_"),
                                                  path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, None


def self_test_results():
    """[(check id, ok, detail)]: each vector's real child run against this module, then the same
    payload under the vector's flipped copy of this module, which must NOT meet the expectation
    (observed red)."""
    out = []
    with open(os.path.abspath(__file__), "r", encoding="utf-8") as handle:
        source = handle.read()
    with tempfile.TemporaryDirectory(prefix="fail-closed-child-selftest-") as tmp:
        mutants = {}
        for key, (old, new) in sorted(FLIPS.items()):
            mutants[key] = _load_mutant(tmp, key, source, old, new)
        for index, (check_id, payload_source, sidecar, expected, flip_key) in enumerate(VECTORS):
            vector_dir = os.path.join(tmp, "vector-{}".format(index))
            os.makedirs(vector_dir)
            payload = os.path.join(vector_dir, "payload.py")
            with open(payload, "x", encoding="utf-8") as handle:
                handle.write(payload_source)
            if sidecar is not None:
                with open(os.path.join(vector_dir, sidecar[0]), "x", encoding="utf-8") as handle:
                    handle.write(sidecar[1])
            if check_id in ISOLATED:
                got = _run_isolated(os.path.dirname(os.path.abspath(__file__)), payload,
                                    os.path.join(vector_dir, "real.json"))
                out.append((check_id, got == expected, (got, expected)))
            else:
                verdict, detail = run_child(payload, os.path.join(vector_dir, "real.json"))
                got = _summary(verdict, detail)
                out.append((check_id, got == expected, (got, expected, detail)))
            mutant, reason = mutants[flip_key]
            if mutant is None:
                out.append((check_id + "-flip-red", False, reason))
                continue
            if check_id in ISOLATED:
                flip_got = _run_isolated(os.path.dirname(mutant.__file__), payload,
                                         os.path.join(vector_dir, "flip.json"))
                out.append((check_id + "-flip-red", flip_got != expected, (flip_key, flip_got, expected)))
                continue
            try:
                flip_verdict, flip_detail = mutant.run_child(payload,
                                                             os.path.join(vector_dir, "flip.json"))
            except (RecursionError, MemoryError) as exc:
                # a flip that makes run_child raise instead of returning a verdict is observed red
                out.append((check_id + "-flip-red", True,
                            (flip_key, "run_child raised " + type(exc).__name__, expected)))
                continue
            flip_got = _summary(flip_verdict, flip_detail)
            out.append((check_id + "-flip-red", flip_got != expected,
                        (flip_key, flip_got, expected, flip_detail)))
        for check_id, phrase, flip_key in DISCLOSURES:
            out.append((check_id, phrase in " ".join(__doc__.split()), phrase))
            mutant, reason = mutants[flip_key]
            if mutant is None:
                out.append((check_id + "-flip-red", False, reason))
                continue
            out.append((check_id + "-flip-red", phrase not in " ".join(mutant.__doc__.split()),
                        (flip_key, phrase)))
    return out


def self_test():
    """The contract's real-child vectors, green against this module and each observed red under its
    paired flip: 0 all hold, 1 otherwise."""
    _child_status_refusal_exit("_fail_closed_child.py")
    results = self_test_results()
    failed = [(check_id, detail) for check_id, ok, detail in results if not ok]
    if failed:
        print("SELF-TEST FAIL: fail-closed-child {} of {} check(s) failed".format(
            len(failed), len(results)))
        for check_id, detail in failed:
            print("  {}: {!r}".format(check_id, detail))
        return 1
    print("SELF-TEST PASS: fail-closed-child {} checks (a clean run passes with a record and a "
          "recorded 1 stays 1; a raw SystemExit, an os._exit at import, in main or in atexit, a "
          "decided code outside 0 to 255, a settled 3, a dirty or lying thread exit, a raw _thread "
          "start, a wrong nonce, a record already present, stderr noise, a settle from a forked "
          "process, a caught exit, a spoofed confirmation before a raw exit 1, a thread's settle "
          "rethrown from main, a record too deep to parse, a record one byte over the size cap, a "
          "large sparse record under an address-space limit, a FIFO, a symlink to /dev/zero or to a "
          "forged record at the record path, stdout one byte over the capture cap, an atexit or "
          "destructor fault under a no-op or collecting unraisable hook, a replaced hook, a fault "
          "under a hook later restored, a deleted unraisable hook, a replaced threading excepthook, "
          "a replaced sys.stderr or sys.__stderr__, a write or flush shadowed on either recorded "
          "stream object or on its buffer or raw layer, a replaced threading.ExceptHookArgs (left "
          "in place or restored only after the join), a sys.exc_info captured by a thread's closure "
          "while swapped, a rebound closure cell, a thread started or ended with an "
          "_invoke_excepthook that is not the one threading set, a replaced "
          "Thread._bootstrap_inner wrapper and a re-opened fd 2 each read "
          "2; a record and stdout exactly at their caps are kept, a plain instance attribute on "
          "sys.stderr still passes, an empty stderr write pushed through every layer and a "
          "ThreadPoolExecutor run each still read 0, and an honest payload under armed ITIMER_PROF "
          "and ITIMER_VIRTUAL timers (left armed at exit, or delivering their signals into the run) "
          "still reads its decided 0; a spoofed confirmation whose later exit carries the matching "
          "status, a destructor fault under a deleted hook with every channel restored before exit, "
          "and a Thread subclass running its own _bootstrap_inner, each read the disclosed verdict "
          "this suite pins along with its docstring text; every paired flip of this module is "
          "red)".format(len(results)))
    return 0


def main():
    """The CLI runs only the self-test; everything else is usage (the parent API is imported)."""
    print("usage: _fail_closed_child.py --self-test", file=sys.stderr)
    return 2


if __name__ == "__main__":
    if sys.argv[1:] == ["--self-test"]:
        sys.exit(self_test())
    sys.exit(_child_status_refusal_exit("_fail_closed_child.py") or main())
