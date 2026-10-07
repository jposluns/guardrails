#!/usr/bin/env python3
"""Launcher for the preview hooks: the file every preview hook registration runs. Stdlib only, offline.

USAGE. python3 -I -S -B /ABSOLUTE/PATH/TO/preview-launch.py MODE [ARG ...], where MODE names one
preview hook in PREVIEW_HOOKS (its file name with each hyphen written as an underscore and no .py:
stamp_truth_stop runs stamp-truth-stop.py). The hook file must sit beside this launcher. The launcher
acquires that hook file ONCE (opened without following a symbolic link, required to be a regular
file, read, compiled) and runs the acquired bytes in this same process as __main__, with argv
[<hook path>, ARG ...], so the hook behaves as when launched directly (MODE --self-test runs the
hook's own self-test); an exception the hook does not catch keeps its direct-launch exit semantics
(the interpreter prints the traceback and exits 1, and a SystemExit passes through unchanged).
  preview-launch.py --self-test   this launcher's own self-test

WHY. Several preview hooks use syntax newer than some Python 3 interpreters can compile, and Python
compiles a whole file before its first statement runs, so on such an interpreter a hook would stop
with a SyntaxError and exit 1, which Claude Code reads as a non-blocking error: a PreToolUse call would
proceed unchecked. This launcher is written in the LAUNCHER SUBSET: a closed, minimal list of Python
3.4 grammar forms (held by launcher_subset_findings below, which its self-test and
tools/check_python_floor.py both run), so every Python 3 that accepts -I compiles it. Below the floor
it refuses here, in the hook form of the canonical guard: a mode in FLOOR_FAIL_OPEN_MODES
(clock_inject on PostToolUse and PostToolUseFailure, where the tool has already run, and
stamp_truth_stop on Stop, where a block would hold every stop with no block cap;
tools/check_python_floor.py pins this literal and probes every other mode for exit 2) warns on exit
0, and every other argv (the four PreToolUse hooks, an unknown mode, no mode) refuses with exit 2,
which denies the call. At or above the floor, the hook file is acquired once (_acquire_hook: opened
with O_NOFOLLOW relative to a file descriptor of this launcher's own directory, fstat-checked to be
a regular file, read, compiled with the hook's path as file name) and ONLY that acquired content is
run, so removing or replacing the file after any check cannot divert what runs; a failure to open,
fstat, read or compile is refused by the same mode rule above (exit 0 with a warning for a fail-open
mode, exit 2 otherwise), never by an unhandled exception's exit 1, which would let a PreToolUse call
proceed unchecked. RESIDUAL: on a platform without O_NOFOLLOW the open follows a symbolic link, and
the regular-file and compile checks still hold.
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

import ast
import io
import itertools
import json
import os
import stat
import tokenize
import warnings

PREVIEW_HOOKS = ("clock_inject", "future_stamp_write", "record_remove_check", "stamp_truth_stop",
                 "unbounded_wait", "ungated_record")
HERE = os.path.dirname(os.path.abspath(__file__))


def hook_path(mode):
    return os.path.join(HERE, mode.replace("_", "-") + ".py")


# BEGIN LAUNCHER-SUBSET ALLOWLIST. tools/check_python_floor.py and .preview/preview-launch.py carry
# this block byte-identical (the floor gate's self-test launcher/allowlist-parity holds the two
# equal; the preview launcher is self-contained, so it cannot import the gate's copy). The block
# itself stays inside the subset it enforces (list(map(...)) and list(filter(...)) stand in for
# loops), and assumes module-level `import ast`, `import io`, `import itertools`, `import tokenize`
# and `import warnings` in its host file.
SUBSET_VERSION = (3, 4)
# The CLOSED, MINIMAL subset of node types a launcher may use, in today's AST spelling. Source: the
# Parser/Python.asdl and Grammar/Grammar files of CPython 3.4 (3.4 is the launcher floor: the first
# Python that accepts -I). Every name listed here is a statement, expression, operator or context
# form the Python 3.4 grammar already holds in the same source spelling with the same meaning (each
# has been in the grammar unchanged since Python 3.0), and _subset_node below refuses by form every
# spelling of a listed node that a modern parser accepts and Python 3.4 does not (enumerated there).
# Every node name outside this tuple is refused by name, whether it is newer than 3.4 (JoinedStr,
# NamedExpr, AnnAssign, Match, TryStar, the Async nodes, ...) or merely not needed by a launcher
# (For, While, With, Lambda, ClassDef, the comprehensions, Starred, Dict, Set, IfExp, Delete,
# AugAssign, ImportFrom, Yield, Global, Nonlocal, Assert, Break, Continue, ...).
SUBSET_NODES = (
    "Add", "And", "Assign", "Attribute", "BinOp", "BitOr", "BoolOp", "Call", "Compare", "Constant",
    "Eq", "ExceptHandler", "Expr", "FunctionDef", "Gt", "GtE", "If", "Import", "In", "Is", "IsNot",
    "List", "Load", "Lt", "LtE", "Mod", "Module", "Name", "Not", "NotEq", "NotIn", "Or", "Pass",
    "Raise", "Return", "Slice", "Store", "Subscript", "Try", "Tuple", "UnaryOp", "alias", "arg",
    "arguments", "keyword")
# The only modules a launcher may import: one per statement, unaliased, each in the Python 3.4
# standard library.
SUBSET_IMPORTS = ("ast", "io", "itertools", "json", "os", "stat", "sys", "tokenize", "warnings")


def _subset_node(found, rel, node):
    """One finding per construct of node outside the launcher subset. The residual of the closed
    list, a LISTED node type carrying a form Python 3.4 does not compile, is enumerated and refused
    here: a Constant from an f- or t-string or with a numeric underscore (_subset_token; the value
    types here are 3.0 forms), every PEP 448 call shape (Starred and dict unpacking are refused by
    name above, a ** keyword by arg None here), every decorator, annotation, default and non-plain
    parameter on FunctionDef/arguments/arg (so no 3.9 decorator grammar and no 3.8 parameter forms
    are reachable), try else/finally (so no continue-through-finally grammar change is reachable),
    a non-Load list or tuple context (no unpacking targets), a bare except, a bare or chained
    raise, and any import outside SUBSET_IMPORTS. A 3.14 identifier is never a Python 3.4 keyword
    (3.14's keyword list adds to 3.4's and removes nothing), so every Name is safe."""
    name = type(node).__name__
    line = getattr(node, "lineno", 0)
    if name not in SUBSET_NODES:
        found.append("%s:%d: a %s node is outside the launcher subset of the Python %d.%d grammar"
                     % ((rel, line, name) + SUBSET_VERSION))
        return
    if name == "Constant" and not (node.value is None or type(node.value) in (bool, int, str)):
        found.append("%s:%d: only a str, int, bool or None constant is in the launcher subset"
                     % (rel, line))
    if name == "FunctionDef" and (node.decorator_list or node.returns
                                  or getattr(node, "type_params", None)):
        found.append("%s:%d: a decorated, annotated or type-parameterized function is outside "
                     "the launcher subset" % (rel, line))
    if name == "arguments" and (getattr(node, "posonlyargs", None) or node.vararg
                                or node.kwonlyargs or node.kw_defaults or node.kwarg
                                or node.defaults):
        found.append("%s:%d: only plain positional parameters (no *, **, defaults, keyword-only "
                     "or positional-only parameters) are in the launcher subset" % (rel, line))
    if name == "arg" and node.annotation is not None:
        found.append("%s:%d: an annotated parameter is outside the launcher subset" % (rel, line))
    if name == "keyword" and node.arg is None:
        found.append("%s:%d: ** argument unpacking is outside the launcher subset" % (rel, line))
    if name == "Assign" and (len(node.targets) != 1
                             or type(node.targets[0]).__name__ not in ("Attribute", "Name",
                                                                       "Subscript")):
        found.append("%s:%d: only an assignment to one name, attribute or subscript is in the "
                     "launcher subset" % (rel, line))
    if name == "Try" and (node.finalbody or node.orelse or not node.handlers):
        found.append("%s:%d: only plain try/except (no else, no finally) is in the launcher "
                     "subset" % (rel, line))
    if name == "ExceptHandler" and node.type is None:
        found.append("%s:%d: a bare except clause is outside the launcher subset" % (rel, line))
    if name == "Raise" and (node.exc is None or node.cause is not None):
        found.append("%s:%d: only `raise <exception>` (no bare raise, no `from`) is in the "
                     "launcher subset" % (rel, line))
    if name == "Import" and (len(node.names) != 1 or node.names[0].asname is not None
                             or node.names[0].name not in SUBSET_IMPORTS):
        found.append("%s:%d: only `import <module>`, one unaliased module per statement, of %s "
                     "is in the launcher subset" % (rel, line, ", ".join(SUBSET_IMPORTS)))
    if name in ("List", "Tuple") and type(node.ctx).__name__ != "Load":
        found.append("%s:%d: a list or tuple outside a load context (an unpacking target) is "
                     "outside the launcher subset" % (rel, line))


def _subset_token(found, rel, token):
    """The newer spellings the AST cannot show on a listed node: an f- or t-string opener (a
    placeholder-free one can parse to a plain Constant) and a numeric underscore (PEP 515, 3.6)
    both read back as a Constant the walk accepts."""
    kind = tokenize.tok_name[token.type]
    if kind in ("FSTRING_START", "TSTRING_START") \
            or (token.type == tokenize.NUMBER and "_" in token.string):
        found.append("%s:%d: %r is newer than Python %d.%d"
                     % ((rel, token.start[0], token.string) + SUBSET_VERSION))


def launcher_subset_findings(rel, text):
    """Each construct in text outside the LAUNCHER SUBSET (SUBSET_NODES with the per-form checks of
    _subset_node and the token scan of _subset_token), as "rel:line: message" strings; [] accepts.
    This is a CLOSED ALLOWLIST, never a list of known-newer constructs: only the listed node types
    and forms pass, so a construct this check has never heard of is refused by name.
    ast.parse(feature_version=...) is only best-effort below its documented lowest supported
    version, so it is kept as a belt, never as the guarantee. Raises SyntaxError,
    tokenize.TokenError or ValueError when text does not parse under THIS interpreter."""
    found = []
    tree = ast.parse(text)
    tokens = list(tokenize.generate_tokens(io.StringIO(text).readline))
    saved_filters = warnings.filters[:]
    warnings.simplefilter("ignore")
    try:
        ast.parse(text, feature_version=SUBSET_VERSION)
    except SyntaxError as exc:
        found.append("%s:%s: does not compile under the Python %d.%d grammar: %s"
                     % ((rel, exc.lineno) + SUBSET_VERSION + (exc.msg,)))
    warnings.filters = saved_filters
    list(map(_subset_node, itertools.repeat(found), itertools.repeat(rel), list(ast.walk(tree))))
    list(map(_subset_token, itertools.repeat(found), itertools.repeat(rel), tokens))
    return found
# END LAUNCHER-SUBSET ALLOWLIST


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


def _missing_sibling(mode):
    if os.path.isfile(hook_path(mode)):
        return ""
    return "missing sibling hook %s" % hook_path(mode)


def _stderr_failure(failure):
    sys.stderr.write("SELF-TEST FAIL: %s\n" % failure)


def self_test():
    """The launcher's own invariants: its whole source stays inside the launcher subset
    (launcher_subset_findings above, byte-identical to the copy in tools/check_python_floor.py,
    whose self-test holds the two equal), every fail-open mode is a known hook, and each hook named
    in PREVIEW_HOOKS sits beside it (required when AIQT_HOOKS_REQUIRE_SIBLINGS is 1)."""
    failures = []
    source = ""
    try:
        handle = open(__file__, "rb")
        source = handle.read().decode("utf-8")
        handle.close()
    except (OSError, ValueError) as exc:
        failures.append("cannot read this launcher: %s" % exc)
    if source:
        try:
            failures.extend(launcher_subset_findings(os.path.basename(__file__), source))
        except (SyntaxError, tokenize.TokenError, ValueError) as exc:
            failures.append("cannot tokenize or parse this launcher: %s" % exc)
    if not set(FLOOR_FAIL_OPEN_MODES) <= set(PREVIEW_HOOKS) or list(PREVIEW_HOOKS) != sorted(set(PREVIEW_HOOKS)):
        failures.append("FLOOR_FAIL_OPEN_MODES must name known hooks; PREVIEW_HOOKS must be sorted, unique")
    if os.environ.get("AIQT_HOOKS_REQUIRE_SIBLINGS") == "1":
        failures.extend(list(filter(None, list(map(_missing_sibling, PREVIEW_HOOKS)))))
    list(map(_stderr_failure, failures))
    verdict = "PASS"
    if failures:
        verdict = "FAIL"
    sys.stdout.write("SELF-TEST %s\n" % verdict)
    if failures:
        return 1
    return 0


if len(sys.argv) == 2 and sys.argv[1] == "--self-test":
    raise SystemExit(self_test())
if len(sys.argv) < 2 or sys.argv[1] not in PREVIEW_HOOKS:
    sys.stderr.write("error: preview-launch.py: the first argument must name one of %s. Nothing was run.\n"
                     % ", ".join(PREVIEW_HOOKS))
    raise SystemExit(2)
_hook = hook_path(sys.argv[1])
# The same mode rule as the floor guard: a hook file that cannot be acquired (missing, a symbolic
# link, not a regular file, unreadable, uncompilable) must not surface as an unhandled exception's
# exit 1 (a non-blocking error that lets a PreToolUse call proceed). Only the acquired content runs:
# removing or replacing the file after the open changes nothing.
_got = _acquire_hook(_hook)
if isinstance(_got, str):
    _missing = ("error: preview-launch.py: cannot acquire the hook file %s (%s). "
                "Nothing was run (cannot evaluate).\n" % (_hook, _got))
    sys.stderr.write(_missing)
    if sys.argv[1] in FLOOR_FAIL_OPEN_MODES:
        sys.stdout.write(json.dumps(dict(systemMessage=(
            "AIQT guardrail: the %s check could not run (%s); surfacing a warning rather than blocking "
            "(non-blocking by design on this event)." % (sys.argv[1], _missing.strip())))) + "\n")
        raise SystemExit(0)
    raise SystemExit(2)
sys.argv = [_hook] + sys.argv[2:]
# An exception the hook raises and does not catch propagates out of this statement: the traceback
# prints and the process exits 1, exactly as when the hook file is launched directly (a direct
# `python3 -I` launch does not put the hook's directory on sys.path, and neither does this).
exec(_got, dict(__name__="__main__", __file__=_hook, __spec__=None, __loader__=None,
                __package__="", __cached__=None))
