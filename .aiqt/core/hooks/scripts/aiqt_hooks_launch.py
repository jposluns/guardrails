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
call proceed unchecked. Every refusal here (the floor guard included) delivers its diagnostic
best-effort with an unbuffered os.write and then ends with its required exit status, whatever the
state of stdout and stderr (closed before Python starts, so the sys stream is None, closed after,
or a pipe whose reader is gone; a buffered sys-stream write could also fail only at the
interpreter's shutdown flush, which replaces the exit status with 120). Because only the acquired
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

SOURCE tree copy: tools/gen_hooks.py copies this file byte-identical into the plugin surface beside
the dispatcher; edit the source, never the generated copy.
"""
import sys

FLOOR_FAIL_OPEN_MODES = ("diff_wall_stop", "orch_dispatch_ledger", "orch_prompt_stamp", "orch_resume_audit",
                         "orch_stop_guard", "orch_teammate_idle")

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    _floor_refusal = (
        "error: aiqt_hooks_launch.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
        "Nothing was run (cannot evaluate).\n"
        % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
    try:
        os.write(2, _floor_refusal.encode("utf-8", "backslashreplace"))
    except (OSError, ValueError, MemoryError):
        pass
    if len(sys.argv) > 1 and sys.argv[1] in FLOOR_FAIL_OPEN_MODES:
        import json
        try:
            os.write(1, (json.dumps(dict(systemMessage=(
                "AIQT guardrail: the %s check could not run (%s); surfacing a warning rather than blocking "
                "(non-blocking by design on this event)." % (sys.argv[1], _floor_refusal.strip())))) + "\n"
                ).encode("utf-8", "backslashreplace"))
        except (OSError, ValueError, MemoryError):
            pass
        raise SystemExit(0)
    raise SystemExit(2)

import json
import os
import stat
import types


def _deliver(number, text):
    """Best-effort diagnostic delivery for a refusal. os.write is unbuffered, so no byte can wait
    in a stream buffer whose failed flush at interpreter shutdown would replace the refusal's exit
    status with 120, and a failed delivery (a descriptor closed before Python started, closed or
    broken later, or out of memory) is swallowed: every refusal must end with its required exit
    status, whatever the state of stdout and stderr (with descriptor 2 closed before Python
    starts, sys.stderr is None, so a sys.stderr.write here would raise and exit 1, which does not
    block a PreToolUse call). The floor guard above cannot call a helper (it runs first, and
    tools/check_python_floor.py pins it by AST to HOOK_GUARD_TEMPLATE), so it carries the same
    try/except os.write form inline."""
    try:
        os.write(number, text.encode("utf-8", "backslashreplace"))
    except (OSError, ValueError, MemoryError):
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
    Returns the compiled code object, or the failure reason as a string: the dispatch below routes
    every acquisition failure through the mode rule, so no failure here surfaces as an unhandled
    exception's exit 1 (a non-blocking error to a PreToolUse call)."""
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
            return "it is not a regular file"
        handle = os.fdopen(fd, "rb")
        data = handle.read()
        handle.close()
    except (OSError, ValueError, MemoryError) as exc:
        return "cannot open or read it without following a symbolic link: %s" % exc
    if not data:
        return "it is empty (zero bytes were read)"
    try:
        return compile(data, path, "exec")
    except (SyntaxError, ValueError, MemoryError, RecursionError) as exc:
        return "cannot compile it: %s" % exc


# The same mode rule as the floor guard: a dispatcher that cannot be acquired (missing, a symbolic
# link, not a regular file, empty, unreadable, uncompilable) must not surface as an unhandled
# exception's exit 1 (a non-blocking error that lets a PreToolUse call proceed). Only the acquired
# content runs: removing, renaming or replacing the file after the open changes nothing; an
# in-place rewrite of the same inode still in progress at the read is the module docstring's
# stated residual. The refusal's diagnostic is best-effort (_deliver) and its exit status fixed,
# whatever the state of stdout and stderr.
_got = _acquire_hook(_hook)
if isinstance(_got, str):
    _missing = ("error: aiqt_hooks_launch.py: cannot acquire the hook file %s (%s). "
                "Nothing was run (cannot evaluate).\n" % (_hook, _got))
    _deliver(2, _missing)
    if len(sys.argv) > 1 and sys.argv[1] in FLOOR_FAIL_OPEN_MODES:
        _deliver(1, json.dumps(dict(systemMessage=(
            "AIQT guardrail: the %s check could not run (%s); surfacing a warning rather than blocking "
            "(non-blocking by design on this event)." % (sys.argv[1], _missing.strip())))) + "\n")
        raise SystemExit(0)
    raise SystemExit(2)
sys.argv[0] = _hook
# The acquired content runs in a NEW module installed as sys.modules["__main__"] (as a direct
# launch presents it), so code that resolves names through the main module (a dataclass string
# annotation, for example) sees the hook's globals, never this launcher's. An exception the
# dispatcher raises and does not catch propagates out of the exec statement: the traceback prints
# and the process exits 1, exactly as when aiqt_hooks.py is launched directly (a direct
# `python3 -I` launch does not put the hook's directory on sys.path, and neither does this).
_module = types.ModuleType("__main__")
_module.__file__ = _hook
_module.__package__ = ""
_module.__cached__ = None
sys.modules["__main__"] = _module
exec(_got, _module.__dict__)
