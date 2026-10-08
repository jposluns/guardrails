#!/usr/bin/env python3
"""Launcher for aiqt_hooks.py: the file every hook registration runs. Stdlib only, offline.

aiqt_hooks.py uses syntax newer than some Python 3 interpreters can compile, and Python compiles a
whole file before its first statement runs, so on such an interpreter the dispatcher would stop with
a SyntaxError and exit 1, which Claude Code reads as a non-blocking error: a PreToolUse call would
proceed unchecked. This launcher is written in the LAUNCHER SUBSET, a closed, minimal list of Python
3.4 grammar forms every Python 3 that accepts -I compiles (held by launcher_subset_findings in
tools/check_python_floor.py). Below the floor it refuses here, in the hook form of the canonical
guard: a mode in FLOOR_FAIL_OPEN_MODES (a Stop, SessionStart, TeammateIdle, UserPromptSubmit or
PostToolUse handler, kept equal to aiqt_hooks.py's own literal) warns on exit 0, and every other
argv (each PreToolUse handler) refuses with exit 2, which blocks the call. At or above the floor it
acquires aiqt_hooks.py beside it ONCE (_acquire_hook: opened with O_NOFOLLOW relative to a file
descriptor of this launcher's own directory, fstat-checked to be a regular file, read, compiled with
the hook's path as file name) and runs ONLY that acquired content in this same process as __main__
(a new module installed as sys.modules["__main__"], so code that resolves names through the main
module sees the hook's globals, as a direct launch presents them; three attributes still differ
from a direct launch: __package__ is '' rather than None, __loader__ is None rather than a
SourceFileLoader, and __builtins__ is the builtins dict rather than the module, so only a hook
that reads one of those behaves differently), with the same argv and argv[0] set to the hook's
path, so the hook otherwise behaves as when launched directly: an exception the hook does
not catch keeps its direct-launch exit semantics (the interpreter prints the traceback and exits 1,
and a SystemExit passes through unchanged). An aiqt_hooks.py that cannot be acquired (missing, a
symbolic link, never followed, not a regular file, a FIFO or device, empty, unreadable, or
uncompilable) is refused by the same mode rule as the floor guard - warn on exit 0 for a fail-open
mode, exit 2 otherwise - never by an unhandled exception's exit 1, which would let a PreToolUse
call proceed unchecked. Every refusal here (the floor guard included) decides
its exit status FIRST, runs ALL its diagnostic work (imports, string formatting, serialization,
encoding, unbuffered os.write delivery with a bounded retry of a partial write) inside one
try/except BaseException that swallows everything, and ends with os._exit(status), never
SystemExit: no diagnostic fault its own code raises (a MemoryError included; FAULTS below states
what an injected fault does), no state of stdout or stderr (closed before Python starts, so the sys
stream is None, closed after, or a pipe whose reader is gone), no bytes already waiting in a
stream buffer (os._exit skips the interpreter's shutdown flush, so a refusal discards such bytes
rather than letting a failed flush replace the status with 120) and no atexit handler can change
that status. Two bounds hold, stated exactly: a FULL BLOCKING PIPE
on the diagnostic stream can block the os.write until the reader drains it (O_NONBLOCK is never
set on a shared descriptor, so the exit is delayed, never changed), and an EXTERNAL SIGNAL that
terminates the process ends it outside any exit-status guarantee. A successful hook run is
untouched: it keeps the interpreter's normal stream flushing and exit semantics. Because only the acquired
content runs, removing, renaming or replacing the file after the open changes nothing.
RESIDUAL: an interpreter that predates -I (Python 2, or
Python 3 before 3.4) rejects that option before it reads this file and exits 2 on every event, so
it blocks each UserPromptSubmit and Stop as well as each PreToolUse call, and on TeammateIdle the
exit 2 keeps the teammate working (orch_teammate_idle is a registered handler); on a platform
without O_NOFOLLOW the open follows a symbolic link, while the regular-file and compile checks
still hold; the open of the hook's DIRECTORY resolves its path following symbolic links (a
symlinked install must keep working, and the launcher runs with the calling user's own privilege),
with O_NOFOLLOW kept on the final component, and where the platform does not support dir_fd for
os.open the hook is opened by its full name, still with O_NOFOLLOW on the final component; and a
writer whose in-place rewrite of the SAME inode is still in progress when the launcher reads it
(never a rename, removal or replacement, which the descriptor acquisition covers) can expose a
partial hook: an empty or uncompilable prefix is refused, a prefix that still compiles runs.
FAULTS. The protected blocks are the floor guard's diagnostic try and the dispatch's one outer
try/except BaseException, whose handler is the acquisition refusal (it applies the same mode rule,
runs its diagnostic work in its own try and retries its closing os._exit once under another): the
`_got = ("the acquisition raised", None)` sentinel assignment, the acquisition, the __main__ setup
(the sys.argv[0] rewrite, the module allocation, its __file__, __package__ and __cached__
assignments and its sys.modules install), the `isinstance(_got, tuple)` test and the raise that
selects the refusal run inside that outer try. Outside the protected blocks the launcher keeps the
mode rule against the ORDINARY faults its own code can raise: each operation that can fail on what
it is given (opening, checking, reading and compiling the hook file, formatting an acquisition error
whose __str__ raises, writing to a closed or broken descriptor) runs inside a protected block, and
the statements between those blocks (the imports, the literal assignments, the version and mode
tests, the def statements and the path computation) read nothing but the registered argv, the
interpreter's version and the launcher's own path. An INJECTED fault is outside that rule: a
line-trace exception or an asynchronous exception (the KeyboardInterrupt a delivered SIGINT raises;
the launcher does not ignore SIGINT) can arrive at ANY line, the clause lines (`try:`, `except
BaseException:`), the floor guard, the module docstring and the `pass` of each diagnostic handler
included, and where no protected block catches it the process ends with that fault's own outcome: an
uncaught exception prints its traceback and exits 1, SystemExit(n) exits n, and KeyboardInterrupt
ends the process by SIGINT. That outcome may not block: exit 1, exit 0 and a SIGINT death do not
block a PreToolUse call (SystemExit(2) does). A MemoryError from real memory exhaustion can likewise
arise at any allocating line outside the protected blocks, and then exits 1. That channel is not
reachable from adopter input (the repository content and the tool-call payload a hook judges): a
line trace needs code already running in this interpreter, and the launcher is started with -I, so
no PYTHON* environment variable (PYTHONSTARTUP and PYTHONPATH included), no user site directory and
no module from the script's or the current directory runs before its first line (the only earlier
code is the interpreter installation's own: its standard library, its site module and the .pth files
of its system site-packages, which belong to whoever installed that interpreter); an asynchronous
exception needs a sender with the right to signal the hook process (its own user, a privileged user,
or the terminal of its process group); and the launcher reads no adopter input before the hook runs,
so none can drive its memory use. tools/check_python_floor.py faults every line a blocking-mode run
of the launcher executes (launcher/line-fault-sweep) and accepts only the mode rule or the injected
fault's own outcome, so an ordinary fault that escapes the mode rule there fails it. The
hook's own execution, the exec statement (its evaluation of exec, _got and
_module.__dict__ included) and the hook's code, keeps the platform's exit semantics as any hook
does: an exception it does not catch exits 1, as when aiqt_hooks.py is launched directly. Each
fail-open refusal (the floor guard's and the acquisition refusal's) delivers its stderr diagnostic
FIRST and imports json only after it, inside the protected block: if that `import json` fails (a
MemoryError, for example), the stderr diagnostic has already been delivered and the exit status
still holds, but the JSON systemMessage warning is not written to stdout, so the platform shows no
warning for that refusal.

SOURCE tree copy: tools/gen_hooks.py copies this file byte-identical into the plugin surface beside
the dispatcher; edit the source, never the generated copy.
"""
import sys

FLOOR_FAIL_OPEN_MODES = ("diff_wall_stop", "orch_dispatch_ledger", "orch_prompt_stamp", "orch_resume_audit",
                         "orch_stop_guard", "orch_teammate_idle")

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    _floor_status = 2
    if len(sys.argv) > 1 and sys.argv[1] in FLOOR_FAIL_OPEN_MODES:
        _floor_status = 0

    def _floor_tail(number, data, attempt):
        if data and attempt < 4:
            _floor_tail(number, data[os.write(number, data):], attempt + 1)
    try:
        _floor_refusal = (
            "error: aiqt_hooks_launch.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        try:
            _floor_tail(2, _floor_refusal.encode("utf-8", "backslashreplace"), 1)
        except BaseException:
            pass
        if _floor_status == 0:
            import json
            _floor_tail(1, (json.dumps(dict(systemMessage=(
                "AIQT guardrail: the %s check could not run (%s); surfacing a warning rather than blocking "
                "(non-blocking by design on this event)." % (sys.argv[1], _floor_refusal.strip())))) + "\n"
                ).encode("utf-8", "backslashreplace"), 1)
    except BaseException:
        pass
    os._exit(_floor_status)

import os
import stat
import types


def _deliver_tail(number, data, attempt):
    """Write data to descriptor number with at most three os.write calls in all (the first write
    and at most two retries of a PARTIAL write; os.write may return short when a signal arrives
    mid-write or a pipe is nearly full): the attempts are
    bounded and counted up, never a loop, so the launcher subset holds. A write that BLOCKS (a full
    blocking pipe whose reader has not drained it) blocks here until the reader drains it, delaying
    the refusal's exit, never changing its status: O_NONBLOCK is never set on a shared standard
    descriptor. An external signal that terminates the process ends it outside any exit-status
    guarantee."""
    if data and attempt < 4:
        _deliver_tail(number, data[os.write(number, data):], attempt + 1)


def _deliver(number, text):
    """Best-effort diagnostic delivery for a refusal. os.write is unbuffered, so no byte waits in a
    stream buffer whose failed flush at interpreter shutdown could replace an exit status with 120,
    and the one except BaseException swallows EVERY failure of the delivery (a descriptor closed
    before Python started, closed or broken later, an encoding fault, out of memory, any exception
    at all): the caller decides the refusal's exit status BEFORE calling this and ends with
    os._exit(status), so nothing here can change it (with descriptor 2 closed before Python starts,
    sys.stderr is None, so a sys.stderr.write here would raise and exit 1, which does not block a
    PreToolUse call). The floor guard above cannot call a helper (it runs first, and
    tools/check_python_floor.py pins it by AST to HOOK_GUARD_TEMPLATE), so it carries the same form
    inline: _floor_tail and one try/except BaseException around all its diagnostic work."""
    try:
        _deliver_tail(number, text.encode("utf-8", "backslashreplace"), 1)
    except BaseException:
        pass

_hook = os.path.join(os.path.dirname(os.path.abspath(__file__)), "aiqt_hooks.py")


def _acquire_hook(path):
    """Acquire the hook file ONCE: open it without following a symbolic link on the final component
    (O_NOFOLLOW, relative to a file descriptor of its directory where the platform supports dir_fd
    for os.open, otherwise by its full name; the directory open itself follows symbolic links so a
    symlinked install keeps working, and requires a directory where the platform has O_DIRECTORY;
    O_NONBLOCK so a FIFO cannot hold the open), require a regular file of the OPENED descriptor
    (fstat, so no rename, removal or replacement after the open can swap what was judged), read
    that descriptor's bytes, refuse an empty read, and compile the bytes with path as the file
    name. The directory descriptor is closed on every path, the inner open's failure included.
    Returns the compiled code object, or a failure as a (reason, exception or None) tuple, never
    formatting the exception here: an exception's text is computed by its own __str__, which can
    itself raise (a MemoryError, a SystemExit, anything), so the dispatch below formats it only
    inside the refusal's protected block, after the exit status is decided. The dispatch also
    calls this inside a try/except BaseException, so no failure here, caught or not, surfaces as
    an unhandled exception's exit 1 (a non-blocking error to a PreToolUse call)."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        if os.open in os.supports_dir_fd:
            directory_fd = os.open(os.path.dirname(path) or ".",
                                   os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                fd = os.open(os.path.basename(path), flags, dir_fd=directory_fd)
            except (OSError, ValueError, MemoryError) as exc:
                os.close(directory_fd)
                raise exc
            os.close(directory_fd)
        else:
            fd = os.open(path, flags)
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            os.close(fd)
            return ("it is not a regular file", None)
        handle = os.fdopen(fd, "rb")
        data = handle.read()
        handle.close()
    except (OSError, ValueError, MemoryError) as exc:
        return ("cannot open or read it without following a symbolic link", exc)
    if not data:
        return ("it is empty (zero bytes were read)", None)
    try:
        return compile(data, path, "exec")
    except (SyntaxError, ValueError, MemoryError, RecursionError) as exc:
        return ("cannot compile it", exc)


# The same mode rule as the floor guard: a dispatcher that cannot be acquired (missing, a symbolic
# link, not a regular file, empty, unreadable, uncompilable) must not surface as an unhandled
# exception's exit 1 (a non-blocking error that lets a PreToolUse call proceed). Only the acquired
# content runs: removing, renaming or replacing the file after the open changes nothing; an in-place
# rewrite of the same inode still in progress at the read is the module docstring's stated residual.
# The refusal's exit status is decided FIRST, before the acquisition runs; the acquisition itself
# runs inside a try/except BaseException (an exception it does not map is refused by the mode rule
# too, as "the acquisition raised"), and so does the __main__ setup that follows a successful
# acquisition (the argv rewrite, the module allocation and initialization and its sys.modules
# install: a fault there is refused as "the launcher's __main__ setup for it raised", and only a
# completed setup leaves _got the code object the exec runs); every diagnostic step (formatting the
# acquisition error, whose __str__ can raise anything, a SystemExit included, the message, the
# import of json, serialization, delivery) runs inside one try/except BaseException, with the bare
# reason as the fallback when the error's text cannot be formatted; the refusal ends with os._exit,
# which no shutdown flush, stream-buffer state or atexit handler can change.
_refusal_status = 2
if len(sys.argv) > 1 and sys.argv[1] in FLOOR_FAIL_OPEN_MODES:
    _refusal_status = 0
# One outer try/except BaseException holds every step from the sentinel assignment to the raise
# that selects the refusal, so a fault at the sentinel, at the isinstance test or at that raise is
# refused by the same mode rule (as "the launcher's dispatch raised" when no acquisition reason is
# bound). That outer handler is the only refusal, and its closing os._exit is retried once under its
# own try/except BaseException. Only a completed acquisition and setup leaves the try normally, and
# the exec statement follows it directly.
try:
    _got = ("the acquisition raised", None)
    try:
        _code = _acquire_hook(_hook)
        if not isinstance(_code, tuple):
            # The __main__ setup: the acquired content runs in a NEW module installed as
            # sys.modules["__main__"] (as a direct launch presents it), so code that resolves names
            # through the main module (a dataclass string annotation, for example) sees the hook's
            # globals, never this launcher's. A fault in any setup step leaves _got a refusal reason.
            _got = ("the launcher's __main__ setup for it raised", None)
            sys.argv[0] = _hook
            _module = types.ModuleType("__main__")
            _module.__file__ = _hook
            _module.__package__ = ""
            _module.__cached__ = None
            sys.modules["__main__"] = _module
        _got = _code
    except BaseException:
        pass
    if isinstance(_got, tuple):
        raise LookupError("the hook file was not acquired")
except BaseException:
    try:
        _reason = "the launcher's dispatch raised"
        try:
            _reason = _got[0]
            if _got[1] is not None:
                _reason = "%s: %s" % (_got[0], _got[1])
        except BaseException:
            pass
        _missing = ("error: aiqt_hooks_launch.py: cannot acquire the hook file %s (%s). "
                    "Nothing was run (cannot evaluate).\n" % (_hook, _reason))
        _deliver(2, _missing)
        if _refusal_status == 0:
            import json
            _deliver(1, json.dumps(dict(systemMessage=(
                "AIQT guardrail: the %s check could not run (%s); surfacing a warning rather than blocking "
                "(non-blocking by design on this event)." % (sys.argv[1], _missing.strip())))) + "\n")
    except BaseException:
        pass
    try:
        os._exit(_refusal_status)
    except BaseException:
        os._exit(_refusal_status)
# The exec statement stays outside every protected block: it starts the hook's own execution, so an
# exception the dispatcher raises and does not catch propagates out of it, the traceback prints and
# the process exits 1, exactly as when aiqt_hooks.py is launched directly (a direct `python3 -I`
# launch does not put the hook's directory on sys.path, and neither does this).
exec(_got, _module.__dict__)
