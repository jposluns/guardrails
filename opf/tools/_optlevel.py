#!/usr/bin/env python3
"""Interpreter optimization-level independence for the self-tests, the ONE shared source (OPF-SELF-CONTAIN).

python -O strips assert statements and python -OO also strips docstrings, so a self-test that indexes,
splices or recompiles a parsed tree, reads a docstring, or takes its verdict from an assert can follow the
interpreter's level instead of the source it checks. The helpers here keep those checks at level 0 or make
them refuse. The OPF tooling under `opf/tools/` imports this module as a sibling, and AIQT's `tools/` reaches
it the way it reaches `_semver` and `_journal` (with `opf/tools/` on sys.path), so `opf/` stays
dependency-closed (it imports nothing upward into `tools/`). Offline, stdlib only, pure.
"""
import ast
import sys

# ast.parse gained its optimize parameter in Python 3.13; the tools declare Python 3.11+.
_PARSE_TAKES_OPTIMIZE = sys.version_info >= (3, 13)


def parse(source, filename="<unknown>", mode="exec"):
    """ast.parse at optimization level 0, whatever level the interpreter runs at, so the tree keeps its
    docstrings and asserts under -O and -OO. On Python 3.13 and later this passes optimize=0. Python 3.11 and
    3.12 have no such parameter (passing it raises TypeError), and there ast.parse never strips docstrings or
    asserts (only the compiler does, after the tree is built), so the plain call returns the same tree."""
    if _PARSE_TAKES_OPTIMIZE:
        return ast.parse(source, filename, mode, optimize=0)
    return ast.parse(source, filename, mode)


def source_docstring(path, name=None):
    """The docstring of the top-level def or class `name` in the Python source at `path` (the module's
    own when `name` is None), parsed from the file at level 0. python -OO strips docstrings and leaves
    __doc__ None, so a check reading __doc__ would follow the interpreter level, not the source."""
    with open(path, encoding="utf-8") as fh:
        node = parse(fh.read(), path)
    if name is not None:
        node = {n.name: n for n in node.body
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}[name]
    return ast.get_docstring(node)


def assert_verdict_refusal(label):
    """None at optimization level 0. Under -O or -OO, the one-line cannot-evaluate reason for a self-test
    whose verdicts are assert statements: the interpreter strips them, so the run would pass vacuously or
    break its own fixtures. The caller prints the reason and exits 2, never a pass."""
    if sys.flags.optimize <= 0:
        return None
    return ("{}: CANNOT EVALUATE: its verdicts are assert statements, which python -O and -OO strip "
            "(sys.flags.optimize={}); rerun without -O; exit 2".format(label, sys.flags.optimize))
