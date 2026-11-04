#!/usr/bin/env python3
"""The shared fail-closed child contract, first slice: code that a gate or OPF command must load is run
in a CHILD process, and the parent reads a pass only from the child's settled, recorded verdict, never
from an exit status alone, so an os._exit, a SystemExit, or a fault in a thread, destructor, atexit
callback or the exit flush cannot read as a pass.

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
threading.Thread, read through the method type's own slots), the creation of another interpreter
where its documented event arrives (measured on CPython 3.14.4, _interpreters.create() delivered no
cpython.PyInterpreterState_New event to an installed hook, so interpreter creation from Python is
actually caught by the _interpreters refusals and the import fault, and the event leg is a backstop
for deliveries that do occur) and the import of an interpreter-creating module; and _install
replaces every non-query function of _interpreters with a refusal that records a fault first, so code
in another interpreter, which no hook of this interpreter can observe, is refused at the attempt.

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
both streams, and passes ONLY when both streams are byte-empty (the "empty" streams mode of the
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
code there could still end the process with a matching os._exit; in exchange the record no longer
fixes the exit status by construction, so a spoofed confirmation is contradicted by the real exit
status at the parent whenever that status differs from the decided code, which the os._exit form
could not refuse. A spoofed confirmation whose later exit carries the MATCHING status (a caught
exit_with(0), a frameless read of that exception's code arranged from atexit, then a raw sys.exit(0)
or falling off the end) still reads as the decided code; that residual is not closed here, and its
vector pins the disclosed behaviour. Deliberately hostile loaded code
forging the record (the nonce is readable in process) or replacing this module's state in the child
is the reporting-machinery residual the stronger contracts disclose; it is not closed here either.

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
import subprocess
import tempfile
import threading
import types

NAME = "_fail_closed_child.py"
CHILD_TIMEOUT = 120
_RECORD_CAP = 1 << 20    # bytes; a genuine record is small, so anything larger is malformed
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
# SystemExit's own code slot, read through the base descriptor so that a subclass's code property
# (code the exception controls) never runs while a thread's end is classified.
_SYSTEM_EXIT_CODE = SystemExit.__dict__["code"]
_STATE = {"installed": False, "armed": False, "armed_pid": None, "decided": None, "raised": None,
          "confirmed": None, "main_ident": None, "record_path": None, "nonce": None, "check_ids": [],
          "faults": [], "thread_hook": None}
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


def _genuine_bootstrap(target, *, _BOOTSTRAP=_BOOTSTRAP, _MethodType=types.MethodType,
                       _Thread=threading.Thread, issubclass=issubclass, type=type):
    """Whether a thread-start target is threading's own: exactly a bound method (its type, never an
    attribute the target supplies) whose function is threading.Thread._bootstrap itself and whose
    instance's real type is a threading.Thread, so no code the target controls runs."""
    return (type(target) is _MethodType and target.__func__ is _BOOTSTRAP
            and issubclass(type(target.__self__), _Thread))


def _audit(event, args, *, _STATE=_STATE, _THREAD_START_EVENTS=_THREAD_START_EVENTS,
           _SUBINTERP_MODULES=_SUBINTERP_MODULES, _INTERP_NEW_EVENT=_INTERP_NEW_EVENT,
           _genuine_bootstrap=_genuine_bootstrap, _exact_str=str.__str__,
           BaseException=BaseException):
    """The audit hook from installation on: record, as a fault, a thread started through _thread
    directly, outside threading, the creation of another interpreter when its event is delivered, and
    the import of an interpreter-creating module by its exact plain name (a str subclass's own hashing
    or equality decides nothing: the name is read through str's own __str__ first, and one that is not
    a str is unclassifiable, so a fault). It never raises (a raising hook would abort the audited
    operation instead of recording it)."""
    try:
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
    """Install the observation hooks; idempotent. _child_main calls this before any loaded code runs,
    so nothing the payload executes precedes them. The audit hook cannot be removed; replacing the
    excepthook or this module's state afterwards is loaded code replacing the reporting machinery, the
    disclosed residual."""
    if _STATE["installed"]:
        return
    _refuse_interp_entries()
    _STATE["installed"] = True
    _STATE["main_ident"] = threading.main_thread().ident
    _STATE["thread_hook"] = threading.excepthook
    threading.excepthook = _thread_hook
    sys.addaudithook(_audit)


def _finalize(record_path, nonce, executed, *, _STATE=_STATE, _collect=gc.collect, _dump=json.dump,
              _exit=os._exit, _getpid=os.getpid, _note=_note, _GC_PASSES=_GC_PASSES, sys=sys,
              open=open, list=list, range=range, BaseException=BaseException):
    """The exit handler, the process's first atexit registration so it runs after every other exit
    callback: refuse outright in any process other than the one that armed (a forked process inherits
    this registration and the nonce, and its record would pass as the original's), run the bounded
    collection, flush the four stream objects, and, ONLY when a decided code was settled through
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
        settled = (_STATE["decided"] is not None and _STATE["raised"] is not None
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
    """(parsed record or None, whether anything exists at the path). A record that exists but cannot
    be read, decoded or parsed is (None, True), never a pass and never a raise: the read is bounded
    to _RECORD_CAP plus one byte, so a record over the cap is malformed like any other, where an
    unbounded read of an oversized record could raise under memory pressure instead of refusing."""
    try:
        with open(record_path, "rb") as handle:
            raw = handle.read(_RECORD_CAP + 1)
    except FileNotFoundError:
        return None, False
    except (OSError, MemoryError):
        return None, True
    if len(raw) > _RECORD_CAP:
        return None, True
    try:
        record = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError, MemoryError):
        # RecursionError: json's refusal of deep nesting; MemoryError: a record too large to parse
        # under pressure: such a record is malformed like any other, never a pass and never a raise
        return None, True
    return record, True


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
    streams, the parsed record (or None) and whether anything exists at the record path."""
    _child_status_refusal_exit(NAME)
    nonce = secrets.token_hex(16)
    directory = os.path.dirname(os.path.abspath(__file__)) if module_dir is None else str(module_dir)
    command = [sys.executable, "-I", "-B", "-c", _CHILD_SHIM, directory, str(payload),
               str(record_path), nonce]
    try:
        result = subprocess.run(command, capture_output=True, timeout=timeout, env=_child_env())
    except (OSError, subprocess.SubprocessError) as exc:
        return 2, {"status": None, "stdout": b"", "stderr": b"", "record": None,
                   "record_present": False, "launch": "launch failed ({})".format(type(exc).__name__)}
    record, record_present = _read_record(record_path)
    verdict = _verdict(result.returncode, result.stdout, result.stderr, record, nonce)
    return verdict, {"status": result.returncode, "stdout": result.stdout, "stderr": result.stderr,
                     "record": record, "record_present": record_present, "launch": None}


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
}

# (check id, payload source, sidecar file or None, expected summary, flip key). A summary is
# (verdict, whether anything exists at the record path, whether a parsed record lists a fault), so a
# flip whose verdict agrees for another reason (the default excepthook's traceback already dirties
# stderr for a SystemExit subclass) is still observed red on the record it wrote.
VECTORS = (
    ("contract/clean-run-passes", _CLEAN, None, (0, True, False), "record-write-broken"),
    ("contract/recorded-one-stays-one", _ONE, None, (1, True, False), "nonzero-mapped-to-two"),
    ("contract/raw-systemexit-zero-refused", _RAW_EXIT, None, (2, False, False),
     "unsettled-defaults-to-zero"),
    ("contract/os-exit-at-import-refused", _EXIT_AT_IMPORT, _EXIT_AT_IMPORT_SIDECAR, (2, False, False),
     "exit-zero-without-record-accepted"),
    ("contract/os-exit-in-main-refused", _EXIT_IN_MAIN, None, (2, False, False),
     "exit-zero-without-record-accepted"),
    ("contract/os-exit-in-atexit-refused", _EXIT_IN_ATEXIT, None, (2, False, False),
     "exit-zero-without-record-accepted"),
    ("contract/thread-systemexit-seven-refused", _THREAD_SEVEN, None, (2, True, True),
     "any-thread-systemexit-clean"),
    ("contract/thread-clean-systemexit-passes", _THREAD_CLEAN, None, (0, True, False),
     "any-thread-systemexit-fault"),
    ("contract/lying-code-property-refused", _LYING_SUBCLASS, None, (2, True, True),
     "code-read-through-getattr"),
    ("contract/raw-thread-start-refused", _RAW_THREAD, None, (2, True, True),
     "audit-ignores-thread-starts"),
    ("contract/decided-256-refused", _OUT_OF_RANGE, None, (2, False, False), "decided-code-unmapped"),
    ("contract/wrong-nonce-refused", _WRONG_NONCE, None, (2, True, False), "nonce-check-skipped"),
    ("contract/preexisting-record-refused", _PREEXISTING, None, (2, True, False),
     "record-create-not-exclusive"),
    ("contract/stderr-noise-refused", _NOISE, None, (2, True, False), "stream-rule-ignored"),
    ("contract/fork-settle-reaped-refused", _FORK_WAITED, None, (2, False, False),
     "fork-settle-accepted"),
    ("contract/fork-settle-unreaped-refused", _FORK_UNWAITED, None, (2, False, False),
     "fork-settle-accepted"),
    ("contract/spoofed-confirm-raw-exit-refused", _CONFIRM_FROM_ATEXIT, None, (2, True, False),
     "status-decided-mismatch-ignored"),
    ("contract/record-defers-to-real-status", _CONFIRM_FROM_ATEXIT, None, (2, True, False),
     "record-then-forced-exit"),
    ("contract/settled-three-refused", _SETTLED_THREE, None, (2, True, False),
     "any-decided-code-passes"),
    ("contract/caught-exit-settles-nothing", _CAUGHT_EXIT, None, (2, False, False),
     "caught-exit-still-settles"),
    ("contract/thread-settle-rethrown-refused", _THREAD_SETTLE, None, (2, False, False),
     "thread-settle-accepted"),
    ("contract/deep-record-refused", _DEEP_RECORD, None, (2, True, False), "deep-record-raises"),
    # the disclosed spoofed-confirmation residual: a spoof whose later exit carries the matching
    # status reads as the decided code; this vector pins that disclosure, it does not endorse it
    ("contract/spoofed-confirm-matching-exit-disclosed", _CONFIRM_MATCHING_EXIT, None,
     (0, True, False), "record-write-broken"),
    ("contract/oversized-record-refused", _OVERSIZED_RECORD, None, (2, True, False),
     "oversized-record-accepted"),
)


def _summary(verdict, detail):
    """(verdict, record present, record lists a fault): the shape every vector is compared on."""
    record = detail["record"]
    listed = type(record) is dict and bool(record.get("faults"))
    return verdict, detail["record_present"], listed


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
            verdict, detail = run_child(payload, os.path.join(vector_dir, "real.json"))
            got = _summary(verdict, detail)
            out.append((check_id, got == expected, (got, expected, detail)))
            mutant, reason = mutants[flip_key]
            if mutant is None:
                out.append((check_id + "-flip-red", False, reason))
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
          "rethrown from main, a record too deep to parse and a record over the size cap each read "
          "2; a spoofed confirmation whose later exit carries the matching status reads the decided "
          "code, the disclosed residual this suite pins; every paired flip of this module is "
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
