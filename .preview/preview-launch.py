#!/usr/bin/env python3
"""Launcher for the preview hooks: the file every preview hook registration runs. Stdlib only, offline.

USAGE. python3 -I -S -B /ABSOLUTE/PATH/TO/preview-launch.py MODE [ARG ...], where MODE names one
preview hook in PREVIEW_HOOKS (its file name with each hyphen written as an underscore and no .py:
stamp_truth_stop runs stamp-truth-stop.py). The hook file must sit beside this launcher. The launcher
runs that hook in this same process as __main__, with argv [<hook path>, ARG ...], so the hook behaves
as when launched directly (MODE --self-test runs the hook's own self-test); an exception the hook does
not catch keeps its direct-launch exit semantics (the interpreter prints the traceback and exits 1,
and a SystemExit passes through unchanged).
  preview-launch.py --self-test   this launcher's own self-test

WHY. Several preview hooks use syntax newer than some Python 3 interpreters can compile, and Python
compiles a whole file before its first statement runs, so on such an interpreter a hook would stop
with a SyntaxError and exit 1, which Claude Code reads as a non-blocking error: a PreToolUse call would
proceed unchecked. This launcher is written in syntax every Python 3 that accepts -I compiles (held to
an explicit allowlist of the Python 3.4 grammar by old_grammar_findings below, which its self-test and
tools/check_python_floor.py both run). Below the floor it refuses here, in the hook form of the
canonical guard: a mode in FLOOR_FAIL_OPEN_MODES (clock_inject on PostToolUse and PostToolUseFailure,
where the tool has already run, and stamp_truth_stop on Stop, where a block would hold every stop with
no block cap; tools/check_python_floor.py pins this literal and probes every other mode for exit 2)
warns on exit 0, and every other argv (the four PreToolUse hooks, an unknown mode, no mode) refuses
with exit 2, which denies the call. At or above the floor, a hook file that is missing or not a
readable regular file (a symbolic link included, never followed) is refused the same way - the mode
rule above decides exit 0 or exit 2 - rather than reaching runpy, whose FileNotFoundError would exit 1
and let a PreToolUse call proceed unchecked.
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
import os
import runpy

PREVIEW_HOOKS = ("clock_inject", "future_stamp_write", "record_remove_check", "stamp_truth_stop",
                 "unbounded_wait", "ungated_record")
HERE = os.path.dirname(__file__)


def hook_path(mode):
    return os.path.join(HERE, mode.replace("_", "-") + ".py")


# BEGIN OLD-GRAMMAR ALLOWLIST. tools/check_python_floor.py and .preview/preview-launch.py carry this
# block byte-identical (the floor gate's self-test launcher/allowlist-parity holds the two equal; the
# preview launcher is self-contained, so it cannot import the gate's copy). The block itself stays
# inside the allowlist it enforces, and assumes a module-level `import ast` in its host file.
OLD_GRAMMAR_VERSION = (3, 4)
# Node types the Python 3.4 compiler accepts, in today's AST spelling (Num, Str, Bytes, NameConstant
# and Ellipsis are Constant now; Index and ExtSlice fold into the subscript value). Source: the
# Parser/Python.asdl and Grammar/Grammar files of CPython 3.4. Every other node name (MatMult 3.5,
# the Async nodes and Await 3.5, JoinedStr and FormattedValue 3.6, AnnAssign 3.6, NamedExpr 3.8,
# the Match nodes 3.10, TryStar 3.11, TypeAlias and the type-parameter nodes 3.12, TemplateStr and
# Interpolation 3.14, and whatever comes later) is rejected by this allowlist without being named.
OLD_GRAMMAR_NODES = frozenset((
    "Module", "FunctionDef", "ClassDef", "Return", "Delete", "Assign", "AugAssign", "For", "While",
    "If", "With", "Raise", "Try", "Assert", "Import", "ImportFrom", "Global", "Nonlocal", "Expr",
    "Pass", "Break", "Continue", "BoolOp", "BinOp", "UnaryOp", "Lambda", "IfExp", "Dict", "Set",
    "ListComp", "SetComp", "DictComp", "GeneratorExp", "Yield", "YieldFrom", "Compare", "Call",
    "Constant", "Attribute", "Subscript", "Starred", "Name", "List", "Tuple", "Slice", "Load",
    "Store", "Del", "And", "Or", "Add", "Sub", "Mult", "Div", "Mod", "Pow", "LShift", "RShift",
    "BitOr", "BitXor", "BitAnd", "FloorDiv", "Invert", "Not", "UAdd", "USub", "Eq", "NotEq", "Lt",
    "LtE", "Gt", "GtE", "Is", "IsNot", "In", "NotIn", "comprehension", "ExceptHandler", "arguments",
    "arg", "keyword", "alias", "withitem"))
# The __future__ names Python 3.4 ships (its Lib/__future__.py); generator_stop is 3.5 and
# annotations is 3.7, so importing either is a SyntaxError there.
OLD_GRAMMAR_FUTURES = frozenset((
    "nested_scopes", "generators", "division", "absolute_import", "with_statement", "print_function",
    "unicode_literals", "barry_as_FLUFL"))


def _old_grammar_calls(found, where, rel, node, args, keywords):
    # A Python 3.4 call (and class statement) takes at most one iterable * (the last positional
    # argument) and at most one mapping ** (the last keyword); PEP 448 (3.5) lifted that.
    stars = [index for index, value in enumerate(args) if isinstance(value, ast.Starred)]
    if len(stars) > 1 or (stars and stars[0] != len(args) - 1):
        found.append("%s:%d: a %s with a * argument before another argument is newer than "
                     "Python %d.%d" % ((rel, node.lineno, where) + OLD_GRAMMAR_VERSION))
    doubles = [index for index, value in enumerate(keywords) if value.arg is None]
    if len(doubles) > 1 or (doubles and doubles[0] != len(keywords) - 1):
        found.append("%s:%d: a %s with ** before another keyword is newer than Python %d.%d"
                     % ((rel, node.lineno, where) + OLD_GRAMMAR_VERSION))


def _old_grammar_finally(found, rel, statements):
    # `continue` lexically in a finally body is a SyntaxError before Python 3.8 unless a loop the
    # body itself opens holds it, so a For or While is not entered; a nested def or class cannot
    # hold a loose `continue` (the modern parse refused it already).
    for node in statements:
        if isinstance(node, ast.Continue):
            found.append("%s:%d: continue inside finally is newer than Python %d.%d"
                         % ((rel, node.lineno) + OLD_GRAMMAR_VERSION))
        elif isinstance(node, (ast.If, ast.With, ast.Try)):
            for field in ("body", "orelse", "finalbody"):
                _old_grammar_finally(found, rel, getattr(node, field, None) or [])
            for handler in getattr(node, "handlers", None) or []:
                _old_grammar_finally(found, rel, handler.body)


def _old_grammar_decorator(node):
    # The Python 3.4 decorator grammar: a dotted name, optionally called once; PEP 614 (3.9)
    # lifted that.
    if isinstance(node, ast.Call):
        node = node.func
    while isinstance(node, ast.Attribute):
        node = node.value
    return isinstance(node, ast.Name)


def old_grammar_findings(rel, text):
    """Each construct in text outside the Python 3.4 grammar (the launcher floor: 3.4 is the first
    Python that accepts -I), as "rel:line: message" strings; [] accepts. This is an ALLOWLIST, not
    a list of known-newer constructs: the AST walk accepts only the node types, call and unpacking
    shapes, decorator forms and __future__ names that Python 3.4 compiles and rejects every other
    node by name, and a token scan rejects the few newer forms the AST cannot show (a numeric
    underscore, an f- or t-string opener, `with` followed by `(`, and a trailing comma closing a
    bracket group that holds a * or ** token). Two disclosed over-rejections, both failing closed:
    every `with (` is refused (Python 3.4 reads `with (a, b):` as one tuple context manager, so no
    parenthesised form is safe to pass), and every `,)` closing a group holding a * token is
    refused (`f(a * b,)` is 3.4-legal but indistinguishable here from `f(*b,)`).
    ast.parse(feature_version=...) is only best-effort below its documented lowest supported
    version, so it is kept as a belt, never as the guarantee. Raises SyntaxError,
    tokenize.TokenError or ValueError when text does not parse under THIS interpreter."""
    import io
    import tokenize
    import warnings
    found = []
    tree = ast.parse(text)
    tokens = list(tokenize.generate_tokens(io.StringIO(text).readline))
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            ast.parse(text, feature_version=OLD_GRAMMAR_VERSION)
    except SyntaxError as exc:
        found.append("%s:%s: does not compile under the Python %d.%d grammar: %s"
                     % ((rel, exc.lineno) + OLD_GRAMMAR_VERSION + (exc.msg,)))
    parents = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    for node in ast.walk(tree):
        line = getattr(node, "lineno", 0)
        name = type(node).__name__
        if name not in OLD_GRAMMAR_NODES:
            found.append("%s:%d: %s is newer than Python %d.%d"
                         % ((rel, line, name) + OLD_GRAMMAR_VERSION))
            continue
        if isinstance(node, ast.Call):
            _old_grammar_calls(found, "call", rel, node, node.args, node.keywords)
        elif isinstance(node, ast.ClassDef):
            _old_grammar_calls(found, "class statement", rel, node, node.bases, node.keywords)
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            if getattr(node, "type_params", None):
                found.append("%s:%d: a type parameter list is newer than Python %d.%d"
                             % ((rel, line) + OLD_GRAMMAR_VERSION))
            for decorator in node.decorator_list:
                if not _old_grammar_decorator(decorator):
                    found.append("%s:%d: a decorator beyond a dotted name or one call on it is "
                                 "newer than Python %d.%d" % ((rel, line) + OLD_GRAMMAR_VERSION))
        if isinstance(node, ast.arguments) and getattr(node, "posonlyargs", None):
            found.append("%s:%d: a positional-only parameter is newer than Python %d.%d"
                         % ((rel, getattr(parents.get(node), "lineno", 0)) + OLD_GRAMMAR_VERSION))
        if isinstance(node, ast.Dict) and any(key is None for key in node.keys):
            found.append("%s:%d: dict unpacking is newer than Python %d.%d"
                         % ((rel, line) + OLD_GRAMMAR_VERSION))
        if isinstance(node, ast.Starred) and not isinstance(node.ctx, ast.Store):
            parent = parents.get(node)
            in_call = isinstance(parent, ast.Call) and any(value is node for value in parent.args)
            in_class = isinstance(parent, ast.ClassDef) \
                and any(value is node for value in parent.bases)
            if not (in_call or in_class):
                found.append("%s:%d: a * expression outside an assignment target or call argument "
                             "is newer than Python %d.%d" % ((rel, line) + OLD_GRAMMAR_VERSION))
        if isinstance(node, ast.ImportFrom) and node.module == "__future__":
            for entry in node.names:
                if entry.name not in OLD_GRAMMAR_FUTURES:
                    found.append("%s:%d: from __future__ import %s is newer than Python %d.%d"
                                 % ((rel, line, entry.name) + OLD_GRAMMAR_VERSION))
        if isinstance(node, ast.Try):
            _old_grammar_finally(found, rel, node.finalbody)
    group_stars = []
    previous = None
    for token in tokens:
        kind = tokenize.tok_name[token.type]
        if kind in ("FSTRING_START", "TSTRING_START") \
                or (token.type == tokenize.NUMBER and "_" in token.string):
            found.append("%s:%d: %r is newer than Python %d.%d"
                         % ((rel, token.start[0], token.string) + OLD_GRAMMAR_VERSION))
        if kind in ("COMMENT", "NL", "NEWLINE", "INDENT", "DEDENT"):
            continue
        if token.type == tokenize.OP:
            if token.string == "(" and previous is not None \
                    and previous.type == tokenize.NAME and previous.string == "with":
                found.append("%s:%d: `with (` is refused (Python %d.%d reads `with (a, b):` as one "
                             "tuple context manager)" % ((rel, token.start[0]) + OLD_GRAMMAR_VERSION))
            if token.string in ("(", "[", "{"):
                group_stars.append(False)
            elif token.string in ("*", "**") and group_stars:
                group_stars[-1] = True
            elif token.string in (")", "]", "}") and group_stars:
                if group_stars.pop() and token.string == ")" and previous is not None \
                        and previous.type == tokenize.OP and previous.string == ",":
                    found.append("%s:%d: a trailing comma closing a group that holds * or ** is "
                                 "newer than Python %d.%d (no trailing comma after *args or **kw)"
                                 % ((rel, token.start[0]) + OLD_GRAMMAR_VERSION))
        previous = token
    return found
# END OLD-GRAMMAR ALLOWLIST


def self_test():
    """The launcher's own invariants: its whole source stays inside the Python 3.4 grammar allowlist
    (old_grammar_findings above, byte-identical to the copy in tools/check_python_floor.py, whose
    self-test holds the two equal), every fail-open mode is a known hook, and each hook named in
    PREVIEW_HOOKS sits beside it (required when AIQT_HOOKS_REQUIRE_SIBLINGS is 1)."""
    import tokenize
    failures = []
    with open(__file__, encoding="utf-8") as handle:
        source = handle.read()
    try:
        failures.extend(old_grammar_findings(os.path.basename(__file__), source))
    except (SyntaxError, tokenize.TokenError, ValueError) as exc:
        failures.append("cannot tokenize or parse this launcher: %s" % exc)
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
_hook = hook_path(sys.argv[1])
# The same mode rule as the floor guard: a missing or unreadable hook file must not fall through to
# runpy, whose FileNotFoundError would exit 1 (a non-blocking error that lets a PreToolUse call
# proceed). The file is judged without following a symbolic link.
if os.path.islink(_hook) or not os.path.isfile(_hook) or not os.access(_hook, os.R_OK):
    _missing = ("error: preview-launch.py: the hook file %s is missing or not a readable regular "
                "file. Nothing was run (cannot evaluate).\n" % _hook)
    sys.stderr.write(_missing)
    if sys.argv[1] in FLOOR_FAIL_OPEN_MODES:
        import json
        sys.stdout.write(json.dumps(dict(systemMessage=(
            "AIQT guardrail: the %s check could not run (%s); surfacing a warning rather than blocking "
            "(non-blocking by design on this event)." % (sys.argv[1], _missing.strip())))) + "\n")
        raise SystemExit(0)
    raise SystemExit(2)
sys.argv[0:2] = [_hook]
# An exception the hook raises and does not catch propagates out of run_path: the traceback prints
# and the process exits 1, exactly as when the hook file is launched directly.
runpy.run_path(sys.argv[0], run_name="__main__")
