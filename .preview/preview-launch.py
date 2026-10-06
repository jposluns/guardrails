#!/usr/bin/env python3
"""Launcher for the preview hooks: the file every preview hook registration runs. Stdlib only, offline.

USAGE. python3 -I -S -B /ABSOLUTE/PATH/TO/preview-launch.py MODE [ARG ...], where MODE names one
preview hook in PREVIEW_HOOKS (its file name with each hyphen written as an underscore and no .py:
stamp_truth_stop runs stamp-truth-stop.py). The hook file must sit beside this launcher. The launcher
runs that hook in this same process as __main__, with argv [<hook path>, ARG ...], so the hook behaves
as when launched directly (MODE --self-test runs the hook's own self-test).
  preview-launch.py --self-test   this launcher's own self-test

WHY. Several preview hooks use syntax newer than some Python 3 interpreters can compile, and Python
compiles a whole file before its first statement runs, so on such an interpreter a hook would stop
with a SyntaxError and exit 1, which Claude Code reads as a non-blocking error: a PreToolUse call would
proceed unchecked. This launcher is written in syntax every Python 3 that accepts -I compiles (no
f-string, numeric underscore, assignment expression, annotation or async syntax; its self-test and
tools/check_python_floor.py hold it to that). Below the floor it refuses here, in the hook form of the
canonical guard: a mode in FLOOR_FAIL_OPEN_MODES (clock_inject on PostToolUse and PostToolUseFailure,
where the tool has already run, and stamp_truth_stop on Stop, where a block would hold every stop with
no block cap) warns on exit 0, and every other argv (the four PreToolUse hooks, an unknown mode, no
mode) refuses with exit 2, which denies the call.
"""
import sys

FLOOR_FAIL_OPEN_MODES = ("clock_inject", "stamp_truth_stop")

if tuple(sys.version_info[:2]) < (3, 14):
    _floor_refusal = (
        "error: preview-launch.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
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

PREVIEW_HOOKS = ("clock_inject", "future_stamp_write", "record_remove_check", "stamp_truth_stop",
                 "unbounded_wait", "ungated_record")
HERE = os.path.dirname(__file__)


def hook_path(mode):
    return os.path.join(HERE, mode.replace("_", "-") + ".py")


def self_test():
    """The launcher's own invariants: it compiles under the Python 3.4 grammar with none of the newer
    tokens that grammar check does not refuse, every fail-open mode is a known hook, and each hook named
    in PREVIEW_HOOKS sits beside it (required when AIQT_HOOKS_REQUIRE_SIBLINGS is 1)."""
    import ast
    import io
    import tokenize
    import warnings
    failures = []
    with open(__file__, encoding="utf-8") as handle:
        source = handle.read()
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            ast.parse(source, feature_version=(3, 4))
    except SyntaxError as exc:
        failures.append("does not compile under the Python 3.4 grammar: %s" % exc)
    newer = ("FSTRING_START", "TSTRING_START")
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if tokenize.tok_name[token.type] in newer or (token.type == tokenize.NUMBER and "_" in token.string) \
                or (token.type == tokenize.OP and token.string in (":=", "@", "@=")):
            failures.append("line %d: %r is newer than Python 3.4" % (token.start[0], token.string))
    for node in ast.walk(ast.parse(source)):
        if type(node).__name__ in ("AnnAssign", "AsyncFunctionDef", "AsyncFor", "AsyncWith", "Await",
                                   "JoinedStr", "NamedExpr", "Match", "TypeAlias"):
            failures.append("line %d: %s is newer than Python 3.4" % (node.lineno, type(node).__name__))
    if not set(FLOOR_FAIL_OPEN_MODES) <= set(PREVIEW_HOOKS) or list(PREVIEW_HOOKS) != sorted(set(PREVIEW_HOOKS)):
        failures.append("FLOOR_FAIL_OPEN_MODES must name known hooks; PREVIEW_HOOKS must be sorted, unique")
    if os.environ.get("AIQT_HOOKS_REQUIRE_SIBLINGS") == "1":
        failures.extend("missing sibling hook %s" % hook_path(mode)
                        for mode in PREVIEW_HOOKS if not os.path.isfile(hook_path(mode)))
    for failure in failures:
        sys.stderr.write("SELF-TEST FAIL: %s\n" % failure)
    sys.stdout.write("SELF-TEST %s\n" % ("FAIL" if failures else "PASS"))
    return 1 if failures else 0


if len(sys.argv) == 2 and sys.argv[1] == "--self-test":
    raise SystemExit(self_test())
if len(sys.argv) < 2 or sys.argv[1] not in PREVIEW_HOOKS:
    sys.stderr.write("error: preview-launch.py: the first argument must name one of %s. Nothing was run.\n"
                     % ", ".join(PREVIEW_HOOKS))
    raise SystemExit(2)
sys.argv[0:2] = [hook_path(sys.argv[1])]
runpy.run_path(sys.argv[0], run_name="__main__")
