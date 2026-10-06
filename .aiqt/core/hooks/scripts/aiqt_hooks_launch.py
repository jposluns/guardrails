#!/usr/bin/env python3
"""Launcher for aiqt_hooks.py: the file every hook registration runs. Stdlib only, offline.

aiqt_hooks.py uses syntax newer than some Python 3 interpreters can compile, and Python compiles a
whole file before its first statement runs, so on such an interpreter the dispatcher would stop with
a SyntaxError and exit 1, which Claude Code reads as a non-blocking error: a PreToolUse call would
proceed unchecked. This launcher is written in syntax every Python 3 that accepts -I compiles (no
f-string, numeric underscore, assignment expression, annotation or async syntax; tools/
check_python_floor.py holds it to that). Below the floor it refuses here, in the hook form of the
canonical guard: a mode in FLOOR_FAIL_OPEN_MODES (a Stop, SessionStart, TeammateIdle,
UserPromptSubmit or PostToolUse handler, kept equal to aiqt_hooks.py's own literal) warns on exit 0,
and every other argv (each PreToolUse handler) refuses with exit 2, which blocks the call. At or
above the floor it runs aiqt_hooks.py beside it in this same process as __main__, with the same
argv, so the hook behaves as when launched directly. RESIDUAL: an interpreter that predates -I (Python 2,
or Python 3 before 3.4) rejects that option before it reads this file and exits 2 on every event, so it
blocks each UserPromptSubmit and Stop as well as each PreToolUse call.

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

import os
import runpy

runpy.run_path(os.path.join(os.path.dirname(__file__), "aiqt_hooks.py"), run_name="__main__")
