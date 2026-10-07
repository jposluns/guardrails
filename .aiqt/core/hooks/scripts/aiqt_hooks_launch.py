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
the hook's path as file name) and runs ONLY that acquired content in this same process as __main__,
with the same argv and argv[0] set to the hook's path, so the hook behaves as when launched
directly: an exception the hook does not catch keeps its direct-launch exit semantics (the
interpreter prints the traceback and exits 1, and a SystemExit passes through unchanged). An
aiqt_hooks.py that cannot be acquired (missing, a symbolic link, never followed, not a regular
file, unreadable, or uncompilable) is refused by the same mode rule as the floor guard - warn on
exit 0 for a fail-open mode, exit 2 otherwise - never by an unhandled exception's exit 1, which
would let a PreToolUse call proceed unchecked; because only the acquired content runs, removing or
replacing the file after the open changes nothing. RESIDUAL: an interpreter that predates -I
(Python 2, or Python 3 before 3.4) rejects that option before it reads this file and exits 2 on
every event, so it blocks each UserPromptSubmit and Stop as well as each PreToolUse call, and on
TeammateIdle the exit 2 keeps the teammate working (orch_teammate_idle is a registered handler);
and on a platform without O_NOFOLLOW the open follows a symbolic link, while the regular-file and
compile checks still hold.

SOURCE tree copy: tools/gen_hooks.py copies this file byte-identical into the plugin surface beside
the dispatcher; edit the source, never the generated copy.
"""
import sys

FLOOR_FAIL_OPEN_MODES = ("diff_wall_stop", "orch_dispatch_ledger", "orch_prompt_stamp", "orch_resume_audit",
                         "orch_stop_guard", "orch_teammate_idle")

if tuple(sys.version_info[:2]) < (3, 14):
    _floor_refusal = (
        "error: aiqt_hooks_launch.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
        "Nothing was run (cannot evaluate).\n"
        % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
    sys.stderr.write(_floor_refusal)
    if len(sys.argv) > 1 and sys.argv[1] in FLOOR_FAIL_OPEN_MODES:
        import json
        sys.stdout.write(json.dumps(dict(systemMessage=(
            "AIQT guardrail: the %s check could not run (%s); surfacing a warning rather than blocking "
            "(non-blocking by design on this event)." % (sys.argv[1], _floor_refusal.strip())))) + "\n")
        raise SystemExit(0)
    raise SystemExit(2)

import json
import os
import stat

_hook = os.path.join(os.path.dirname(os.path.abspath(__file__)), "aiqt_hooks.py")


def _acquire_hook(path):
    """Acquire the hook file ONCE: open it without following a symbolic link (O_NOFOLLOW, relative
    to a file descriptor of its directory where the platform supports dir_fd; O_NONBLOCK so a FIFO
    cannot hold the open), require a regular file of the OPENED descriptor (fstat, so no rename,
    removal or replacement after the open can swap what was judged), read that descriptor's bytes
    and compile them with path as the file name. Returns the compiled code object, or the failure
    reason as a string: the dispatch below routes every acquisition failure through the mode rule,
    so no failure here surfaces as an unhandled exception's exit 1 (a non-blocking error to a
    PreToolUse call)."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        if os.open in os.supports_dir_fd:
            directory_fd = os.open(os.path.dirname(path) or ".", os.O_RDONLY)
            fd = os.open(os.path.basename(path), flags, dir_fd=directory_fd)
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
    try:
        return compile(data, path, "exec")
    except (SyntaxError, ValueError, MemoryError, RecursionError) as exc:
        return "cannot compile it: %s" % exc


# The same mode rule as the floor guard: a dispatcher that cannot be acquired (missing, a symbolic
# link, not a regular file, unreadable, uncompilable) must not surface as an unhandled exception's
# exit 1 (a non-blocking error that lets a PreToolUse call proceed). Only the acquired content runs:
# removing or replacing the file after the open changes nothing.
_got = _acquire_hook(_hook)
if isinstance(_got, str):
    _missing = ("error: aiqt_hooks_launch.py: cannot acquire the hook file %s (%s). "
                "Nothing was run (cannot evaluate).\n" % (_hook, _got))
    sys.stderr.write(_missing)
    if len(sys.argv) > 1 and sys.argv[1] in FLOOR_FAIL_OPEN_MODES:
        sys.stdout.write(json.dumps(dict(systemMessage=(
            "AIQT guardrail: the %s check could not run (%s); surfacing a warning rather than blocking "
            "(non-blocking by design on this event)." % (sys.argv[1], _missing.strip())))) + "\n")
        raise SystemExit(0)
    raise SystemExit(2)
sys.argv[0] = _hook
# An exception the dispatcher raises and does not catch propagates out of this statement: the
# traceback prints and the process exits 1, exactly as when aiqt_hooks.py is launched directly (a
# direct `python3 -I` launch does not put the hook's directory on sys.path, and neither does this).
exec(_got, dict(__name__="__main__", __file__=_hook, __spec__=None, __loader__=None,
                __package__="", __cached__=None))
