#!/usr/bin/env python3
"""Read-and-launch inventory for the run: steps of .github/workflows/quality.yml.

  ci_inputs.py --json              one JSON record per step, computed fresh from the tree
  ci_inputs.py --explain JOB STEP  the one record for that job id and step name, indented
  ci_inputs.py --self-test         run the inventory checks registered under the gate's suite
  any other argument list is refused with exit 2

For each step this tool computes the step's static import closure (the gate's own definition,
imported from tools/check_ci_selection.py beside this file, so the two can never drift) and sorts
every read and launch call site in that closure into four classes:

  1   a literal path: a string constant, with no cwd= keyword, no dir_fd= keyword and no os.chdir
      anywhere in the module (any of those makes a relative literal class 3b, because the path is
      then resolved against something other than the repository root)
  2   an anchored path: built from __file__ or a single-bound module constant derived from it
      (Path(__file__).resolve().parents[n], os.path.dirname chains, .parent chains, os.path.join
      and / joins with string literals) and resolved to a repository-relative path; a directory
      listing (os.listdir, os.scandir, os.walk, .iterdir, .glob, .rglob, glob.glob, glob.iglob)
      of an anchored directory D contributes the glob D/**
  3a  a scratch path: derived from tempfile (mkdtemp, mkstemp, TemporaryDirectory, gettempdir,
      NamedTemporaryFile), including a with ... as name binding
  3b  unresolved: everything else. A parameter resolves only when every in-module caller passes a
      resolvable value and the function is never passed as a value; a loop variable resolves only
      over a literal tuple or list whose every element resolves; a name bound more than once, bound
      through global, or named in a setattr or patch call stays unresolved. Any unknown call shape
      that takes a path (a starred argument, a **kwargs call, a keyword the model does not read)
      and any API outside the detector lists is 3b, never class 1, and an unparseable source or an
      unreadable or invalid dispositions file is cannot-evaluate (exit 2), never a pass.

A launch of [sys.executable, ..., <repository .py path>] is followed: the launched script's own
closure and inventory join the step's, with cycle detection. A launched repository file that is not
Python is a data path and one opaque unresolved site. git -C <repository root> contributes the
whole-repo input **; git -C <scratch> is a scratch site; any other program is unresolved.

Reviewed per-site decisions live in tools/ci_inputs_dispositions.toml, keyed by (file, qualname,
sha256 of ast.dump of the call node), so a line shift keeps a disposition current and any edit to
the expression makes it stale. Kinds: scratch, repo-paths (its paths join the data), whole-repo
(forces **), not-a-read. The declaration gate applies the inventory to reviewed = true registry
entries only (rules input-not-triggered and unresolved-input in tools/check_ci_selection.py).

This tool launches no child of its own; the one child it can start is the gate's pinned tracked-file
listing (check_ci_selection.tracked_files: git with the GIT_* environment dropped, core.fsmonitor
off and protocol.allow never), which is why the child-status guard below runs at the entry line.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: ci_inputs.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import ast
import hashlib
import json
import posixpath
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # not a version problem: every Python 3.14 ships tomllib
    import os
    try:
        sys.stderr.write(
            "error: ci_inputs.py cannot import tomllib, part of the Python standard library; "
            "this installation is incomplete. Nothing was run (cannot evaluate).\n")
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_ci_selection import (  # noqa: E402  (one closure and glob definition, never a second)
    WORKFLOW, CannotEvaluate, glob_match, import_closure, read_text, run_scripts, tracked_files,
    workflow_run_steps)

ROOT = Path(__file__).resolve().parents[1]
DISPOSITIONS = "tools/ci_inputs_dispositions.toml"
DISPOSITION_KINDS = ("scratch", "repo-paths", "whole-repo", "not-a-read")
DISPOSITION_REQUIRED = {"file": str, "qualname": str, "fingerprint": str, "kind": str, "rationale": str}
FINGERPRINT_LEN = 64

# The call keywords that re-anchor a relative path away from the repository root. A classifier rule
# lives in a named constant so the self-test can disable it in process and show the covering check
# turn red; the live classifier always reads the constants below as published here.
CWD_KWARGS = frozenset(("cwd",))
DIR_FD_KWARGS = frozenset(("dir_fd",))
# Call names whose string arguments rebind a module name behind the resolver's back.
REBIND_CALLS = frozenset(("setattr", "patch"))

# Names that are modules in this detector's vocabulary: an attribute call on one is an API call,
# never a method on a path-like receiver.
MODULE_NAMES = frozenset(("ast", "glob", "hashlib", "importlib", "io", "json", "os", "posixpath",
                          "re", "runpy", "shutil", "stat", "subprocess", "sys", "tempfile", "tomllib"))
READ_MODULE_CALLS = frozenset((("io", "open"), ("os", "open"), ("os", "listdir"), ("os", "scandir"),
                               ("os", "walk"), ("glob", "glob"), ("glob", "iglob"), ("shutil", "copy"),
                               ("shutil", "copy2"), ("shutil", "copyfile"), ("shutil", "copytree")))
READ_METHODS = frozenset(("read_text", "read_bytes", "open", "iterdir", "glob", "rglob"))
LISTING_APIS = frozenset(("listdir", "scandir", "walk", "iterdir", "glob", "rglob", "iglob"))
SUBPROCESS_CALLS = frozenset(("run", "Popen", "call", "check_call", "check_output"))
INTERPRETER_WORDS = frozenset(("python", "python3"))
MAX_RESOLVE_DEPTH = 40


def _fingerprint(node):
    """The disposition key of one call node: sha256 of its ast.dump, line numbers excluded."""
    return hashlib.sha256(ast.dump(node).encode("utf-8")).hexdigest()


class _Facts:
    """What one module's source establishes for the resolver: single-value bindings, loop and with
    bindings, functions and their in-module call sites, and the rebinding and chdir signals."""

    def __init__(self, rel):
        self.rel = rel
        self.bind_count = {}
        self.assigns = {}
        self.fortargets = {}
        self.withas = {}
        self.funcs = {}
        self.func_value_use = set()
        self.calls_by_func = {}
        self.rebound = set()
        self.has_chdir = False

    def bind(self, name, times=1):
        self.bind_count[name] = self.bind_count.get(name, 0) + times


def _bind_target(facts, target, value=None, kind="assign"):
    """Record one binding target; only a lone Name target keeps its value expression."""
    if isinstance(target, ast.Name):
        facts.bind(target.id)
        if kind == "assign" and value is not None:
            facts.assigns[target.id] = value
        elif kind == "for" and value is not None:
            facts.fortargets[target.id] = value
        elif kind == "with" and value is not None:
            facts.withas[target.id] = value
    elif isinstance(target, (ast.Tuple, ast.List)):
        for element in target.elts:
            _bind_target(facts, element, None, "unpacked")
    elif isinstance(target, ast.Starred):
        _bind_target(facts, target.value, None, "unpacked")
    # An attribute or subscript store binds no module name this resolver reads.


def _collect_facts(rel, tree):
    """One walk over the module: every binding, function, call-by-name, rebinding and chdir."""
    facts = _Facts(rel)
    call_func_nodes = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            call_func_nodes.add(id(node.func))
            if isinstance(node.func, ast.Name):
                facts.calls_by_func.setdefault(node.func.id, []).append(node)
                callee = node.func.id
            elif isinstance(node.func, ast.Attribute):
                callee = node.func.attr
            else:
                callee = None
            if callee == "chdir":
                facts.has_chdir = True
            if callee in REBIND_CALLS:
                for arg in list(node.args) + [kw.value for kw in node.keywords]:
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        facts.rebound.add(arg.value.split(".")[-1])
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                _bind_target(facts, target, node.value if len(node.targets) == 1 else None)
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            _bind_target(facts, node.target, node.value)
        elif isinstance(node, ast.AugAssign):
            if isinstance(node.target, ast.Name):
                facts.bind(node.target.id, 2)
        elif isinstance(node, ast.NamedExpr):
            _bind_target(facts, node.target, None, "walrus")
        elif isinstance(node, ast.For):
            _bind_target(facts, node.target, node.iter, "for")
        elif isinstance(node, ast.With):
            for item in node.items:
                if item.optional_vars is not None:
                    _bind_target(facts, item.optional_vars, item.context_expr, "with")
        elif isinstance(node, ast.comprehension):
            _bind_target(facts, node.target, None, "unpacked")
        elif isinstance(node, ast.ExceptHandler) and node.name:
            facts.bind(node.name)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                facts.bind((alias.asname or alias.name).split(".")[0])
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            facts.bind(node.name)
            facts.funcs[node.name] = node
        elif isinstance(node, ast.ClassDef):
            facts.bind(node.name)
        elif isinstance(node, ast.Global):
            for name in node.names:
                facts.bind(name, 2)
        elif isinstance(node, ast.Delete):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    facts.bind(target.id)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            if id(node) not in call_func_nodes:
                facts.func_value_use.add(node.id)
    return facts


def _rebound(facts, name):
    """Whether the resolver must refuse this name: multiply bound, global-stored, or named in a
    setattr or patch call. The flip self-test replaces this function to show its checks turn red."""
    return facts.bind_count.get(name, 0) > 1 or name in facts.rebound


def _up(path):
    """One directory up from a repository-relative path; None past the root."""
    if path in ("", "."):
        return None
    return posixpath.dirname(path)


def _params(fn):
    """The positional parameter names of one function definition, in order."""
    return [arg.arg for arg in fn.args.posonlyargs + fn.args.args]


def _resolve(node, facts, fn, seen):
    """Every resolution of one path expression: a list of (kind, payload) with kind literal,
    anchored, scratch or unresolved. Fail closed: anything not modelled is unresolved."""
    if len(seen) > MAX_RESOLVE_DEPTH:
        return [("unresolved", "resolution too deep")]
    if isinstance(node, ast.Constant):
        if isinstance(node.value, str):
            return [("literal", node.value)]
        return [("unresolved", "constant {!r} is not a path".format(node.value))]
    if isinstance(node, ast.Name):
        return _resolve_name(node.id, facts, fn, seen)
    if isinstance(node, ast.Attribute):
        if node.attr == "parent":
            return _apply_up(_resolve(node.value, facts, fn, seen), 1)
        if isinstance(node.value, ast.Name) and node.value.id == "sys" and node.attr == "executable":
            return [("literal", "python3")]
        return [("unresolved", "attribute .{} is not modelled".format(node.attr))]
    if isinstance(node, ast.Subscript):
        base = node.value
        if isinstance(base, ast.Attribute) and base.attr == "parents" \
                and isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, int):
            return _apply_up(_resolve(base.value, facts, fn, seen), node.slice.value + 1)
        return [("unresolved", "subscript is not modelled")]
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        return _join(_resolve(node.left, facts, fn, seen), node.right, facts, fn, seen)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _resolve(node.left, facts, fn, seen)
        if all(kind == "scratch" for kind, _payload in left):
            return [("scratch", None)]
        right = _resolve(node.right, facts, fn, seen)
        if all(k == "literal" for k, _p in left) and all(k == "literal" for k, _p in right):
            return [("literal", lp + rp) for _k, lp in left for _rk, rp in right]
        return [("unresolved", "string concatenation is not resolved")]
    if isinstance(node, ast.Call):
        return _resolve_call(node, facts, fn, seen)
    if isinstance(node, ast.JoinedStr):
        return [("unresolved", "f-string")]
    if isinstance(node, ast.Starred):
        return [("unresolved", "starred argument")]
    return [("unresolved", "{} is not modelled".format(type(node).__name__))]


def _resolve_name(name, facts, fn, seen):
    if name == "__file__":
        return [("anchored", facts.rel)]
    key = ("name", name)
    if key in seen:
        return [("unresolved", "name {} resolves through itself".format(name))]
    seen = seen | {key}
    if _rebound(facts, name):
        return [("unresolved", "name {} is rebound".format(name))]
    if fn is not None and name in _params(fn):
        return _resolve_param(name, facts, fn, seen)
    if fn is not None and (name == getattr(fn.args.vararg, "arg", None)
                           or name == getattr(fn.args.kwarg, "arg", None)
                           or name in [arg.arg for arg in fn.args.kwonlyargs]):
        return [("unresolved", "parameter {} is not positional".format(name))]
    if name in facts.assigns:
        return _resolve(facts.assigns[name], facts, None, seen)
    if name in facts.fortargets:
        iterable = facts.fortargets[name]
        if isinstance(iterable, (ast.Tuple, ast.List)):
            out = []
            for element in iterable.elts:
                got = _resolve(element, facts, None, seen)
                if any(kind == "unresolved" for kind, _payload in got):
                    return [("unresolved", "a loop element over {} does not resolve".format(name))]
                out.extend(got)
            return out
        return [("unresolved", "loop variable {} iterates a non-literal sequence".format(name))]
    if name in facts.withas:
        return _resolve(facts.withas[name], facts, None, seen)
    return [("unresolved", "name {} is not resolvable".format(name))]


def _resolve_param(name, facts, fn, seen):
    if fn.name in facts.func_value_use:
        return [("unresolved", "function {} is passed as a value".format(fn.name))]
    calls = facts.calls_by_func.get(fn.name, [])
    if not calls:
        return [("unresolved", "parameter {} has no in-module caller".format(name))]
    index = _params(fn).index(name)
    out = []
    for call in calls:
        expr = None
        if len(call.args) > index and not any(isinstance(a, ast.Starred) for a in call.args[:index + 1]):
            expr = call.args[index]
        else:
            for kw in call.keywords:
                if kw.arg == name:
                    expr = kw.value
        if expr is None:
            return [("unresolved", "a caller omits parameter {}".format(name))]
        got = _resolve(expr, facts, None, seen)
        if any(kind == "unresolved" for kind, _payload in got):
            return [("unresolved", "a caller's value for parameter {} is itself unresolved".format(name))]
        out.extend(got)
    return out


def _apply_up(resolutions, count):
    out = []
    for kind, payload in resolutions:
        if kind == "anchored":
            for _step in range(count):
                payload = _up(payload)
                if payload is None:
                    return [("unresolved", "a parent chain escapes the repository")]
            out.append(("anchored", payload))
        elif kind == "scratch":
            out.append(("scratch", None))
        else:
            out.append(("unresolved", "a parent chain needs an anchored base"))
    return out


def _join(left, right_node, facts, fn, seen):
    out = []
    right = _resolve(right_node, facts, fn, seen)
    for kind, payload in left:
        if kind == "scratch":
            out.append(("scratch", None))
            continue
        if kind not in ("anchored", "literal"):
            out.append(("unresolved", "a path join needs a resolvable left side"))
            continue
        for rkind, rpayload in right:
            if rkind != "literal":
                out.append(("unresolved", "a path join needs a literal right side"))
                continue
            joined = posixpath.normpath(posixpath.join(payload if payload else ".", rpayload))
            if joined.startswith("..") or rpayload.startswith("/"):
                out.append(("unresolved", "a join escapes the repository"))
            else:
                out.append((kind, "" if joined == "." else joined))
    return out


def _resolve_call(node, facts, fn, seen):
    func = node.func
    if isinstance(func, ast.Attribute):
        base = func.value
        if isinstance(base, ast.Name) and base.id == "tempfile":
            return [("scratch", None)]
        if isinstance(base, ast.Attribute) and isinstance(base.value, ast.Name) \
                and base.value.id == "os" and base.attr == "path":
            if func.attr == "join" and node.args and not any(isinstance(a, ast.Starred) for a in node.args):
                res = _resolve(node.args[0], facts, fn, seen)
                for extra in node.args[1:]:
                    res = _join(res, extra, facts, fn, seen)
                return res
            if func.attr == "dirname" and len(node.args) == 1:
                return _apply_up(_resolve(node.args[0], facts, fn, seen), 1)
            if func.attr in ("abspath", "realpath") and len(node.args) == 1:
                return _resolve(node.args[0], facts, fn, seen)
            return [("unresolved", "os.path.{} is not modelled".format(func.attr))]
        if func.attr in ("resolve", "absolute", "expanduser") and not node.args and not node.keywords:
            return _resolve(func.value, facts, fn, seen)
        return [("unresolved", "call .{}() is not modelled".format(func.attr))]
    if isinstance(func, ast.Name):
        if func.id in ("str", "Path", "PurePath", "PurePosixPath", "fspath"):
            if len(node.args) == 1 and not node.keywords and not isinstance(node.args[0], ast.Starred):
                return _resolve(node.args[0], facts, fn, seen)
            if len(node.args) > 1 and not any(isinstance(a, ast.Starred) for a in node.args):
                res = _resolve(node.args[0], facts, fn, seen)
                for extra in node.args[1:]:
                    res = _join(res, extra, facts, fn, seen)
                return res
        return [("unresolved", "call {}() is not modelled".format(func.id))]
    return [("unresolved", "call shape is not modelled")]


def _write_mode(node):
    """Whether an open-style call's literal mode writes and never reads; a non-literal mode stays a
    read site, fail closed."""
    mode = node.args[1] if len(node.args) > 1 else None
    for kw in node.keywords:
        if kw.arg == "mode":
            mode = kw.value
    if isinstance(mode, ast.Constant) and isinstance(mode.value, str):
        return bool(set(mode.value) & set("wax")) and "r" not in mode.value and "+" not in mode.value
    return False


def _call_kind(node):
    """(kind, api, path_arg_index) for a recognized read or launch call, else None."""
    func = node.func
    if isinstance(func, ast.Name):
        if func.id == "open":
            return ("read", "open", 0)
        return None
    if not isinstance(func, ast.Attribute):
        return None
    base = func.value
    if isinstance(base, ast.Name) and base.id in MODULE_NAMES:
        key = (base.id, func.attr)
        if key in READ_MODULE_CALLS:
            return ("read", func.attr, 0)
        if base.id == "subprocess" and func.attr in SUBPROCESS_CALLS:
            return ("launch", "subprocess", 0)
        if base.id == "os" and (func.attr in ("system", "fork", "posix_spawn")
                                or func.attr.startswith("exec") or func.attr.startswith("spawn")):
            return ("launch", "os." + func.attr, 0)
        if base.id == "runpy" and func.attr == "run_path":
            return ("launch", "run_path", 0)
        if base.id == "importlib" and func.attr == "import_module":
            return ("launch", "import_module", 0)
        return None
    if isinstance(base, ast.Attribute) and isinstance(getattr(base, "value", None), ast.Name) \
            and base.value.id == "importlib" and base.attr == "util" \
            and func.attr == "spec_from_file_location":
        return ("launch", "spec_from_file_location", 1)
    if func.attr in READ_METHODS:
        return ("read", "." + func.attr, -1)
    return None


def _unknown_shape(node, index):
    """Whether the call hides its path: **kwargs, or a starred or missing argument at the path slot."""
    if any(kw.arg is None for kw in node.keywords):
        return True
    if index < 0:
        return False
    if len(node.args) <= index:
        return not any(kw.arg for kw in node.keywords)
    return any(isinstance(arg, ast.Starred) for arg in node.args[:index + 1])


def classify_module(rel, source):
    """Every read and launch site of one module, each a dict with kind, site, qualname, fingerprint,
    class (1, 2, 3a or 3b), paths (repository data paths or globs), scripts (candidate launched
    repository paths) and reason. CannotEvaluate on a source that does not parse."""
    try:
        tree = ast.parse(source, filename=rel)
    except (SyntaxError, ValueError, RecursionError, MemoryError) as exc:
        raise CannotEvaluate("{}: does not parse ({}: {})".format(rel, type(exc).__name__, exc)) from None
    facts = _collect_facts(rel, tree)
    sites = []

    def visit(node, qualname, fn):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            inner = qualname + "." + node.name if qualname != "<module>" else node.name
            for child in ast.iter_child_nodes(node):
                visit(child, inner, node if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) else fn)
            return
        if isinstance(node, ast.Call):
            found = _call_kind(node)
            if found is not None:
                sites.append(_classify_site(node, found, facts, fn, qualname))
        for child in ast.iter_child_nodes(node):
            visit(child, qualname, fn)

    visit(tree, "<module>", None)
    return [site for site in sites if site is not None]


def _site_base(node, kind, facts, qualname):
    return {"kind": kind, "site": "{}:{}".format(facts.rel, node.lineno), "qualname": qualname,
            "fingerprint": _fingerprint(node), "paths": [], "scripts": [], "reason": ""}


def _unresolved_site(base, reason):
    base["class"] = "3b"
    base["reason"] = reason
    return base


def _cwd_resolution(node, facts, fn):
    """None when no cwd-style keyword re-anchors the call, else that keyword's resolution list."""
    for kw in node.keywords:
        if kw.arg in CWD_KWARGS:
            return _resolve(kw.value, facts, fn, frozenset())
    return None


def _classify_site(node, found, facts, fn, qualname):
    kind, api, index = found
    base = _site_base(node, kind, facts, qualname)
    if any(kw.arg in DIR_FD_KWARGS for kw in node.keywords):
        return _unresolved_site(base, "a dir_fd keyword re-anchors the path")
    if _unknown_shape(node, index):
        return _unresolved_site(base, "unknown call shape ({})".format(api))
    if kind == "read":
        if api in ("open", ".open") and _write_mode(node):
            return None
        if index >= 0:
            if len(node.args) <= index:
                return _unresolved_site(base, "no path argument read ({})".format(api))
            path_node = node.args[index]
        else:
            path_node = node.func.value
        return _finish_read(base, _resolve(path_node, facts, fn, frozenset()), node, facts, fn,
                            api in LISTING_APIS)
    return _classify_launch(base, node, api, index, facts, fn)


def _finish_read(base, resolutions, node, facts, fn, listing):
    classes, paths = set(), []
    for kind, payload in resolutions:
        if kind == "unresolved":
            return _unresolved_site(base, payload)
        if kind == "scratch":
            classes.add("3a")
            continue
        if kind == "literal":
            if payload.startswith("/"):
                classes.add("1")
                continue
            adjusted = _relative_literal(node, facts, fn)
            if adjusted is not None:
                return _unresolved_site(base, adjusted) if adjusted != "3a" else _scratch(base)
            classes.add("1")
            paths.append(posixpath.normpath(payload))
            continue
        classes.add("2")
        if listing:
            paths.append("{}/**".format(payload) if payload else "**")
        else:
            paths.append(payload if payload else "**")
    if classes == {"3a"}:
        return _scratch(base)
    base["class"] = "2" if "2" in classes else ("1" if "1" in classes else "3a")
    base["paths"] = sorted(set(paths))
    return base


def _scratch(base):
    base["class"] = "3a"
    return base


def _relative_literal(node, facts, fn):
    """Why a relative literal cannot anchor to the repository root here: an os.chdir in the module,
    or a cwd keyword that is not the repository root. None when it stays class 1; the string 3a
    when the cwd keyword resolves to scratch."""
    if facts.has_chdir:
        return "a relative literal after os.chdir in this module"
    cwd = _cwd_resolution(node, facts, fn)
    if cwd is None:
        return None
    if all(kind == "scratch" for kind, _payload in cwd):
        return "3a"
    if all(kind == "anchored" and payload == "" for kind, payload in cwd):
        return None
    return "a relative literal under a cwd= keyword that is not the repository root"


def _classify_launch(base, node, api, index, facts, fn):
    if api == "os.fork":
        base["class"] = "1"
        return base
    if api in ("run_path", "spec_from_file_location"):
        if len(node.args) <= index:
            return _unresolved_site(base, "no path argument read ({})".format(api))
        return _finish_script(base, _resolve(node.args[index], facts, fn, frozenset()), node, facts, fn)
    if api == "import_module":
        return _unresolved_site(base, "importlib.import_module is not followed in this slice")
    argv = node.args[0] if node.args else None
    if not isinstance(argv, (ast.Tuple, ast.List)):
        return _unresolved_site(base, "launch argv is not a literal sequence")
    elements = argv.elts
    if not elements or any(isinstance(element, ast.Starred) for element in elements):
        return _unresolved_site(base, "launch argv holds a starred or empty element list")
    program = _resolve(elements[0], facts, fn, frozenset())
    if len(program) == 1 and program[0][0] == "literal" and program[0][1] == "git":
        return _classify_git(base, elements, node, facts, fn)
    if len(program) == 1 and program[0][0] == "literal" \
            and program[0][1].rpartition("/")[2] in INTERPRETER_WORDS:
        for element in elements[1:]:
            if isinstance(element, ast.Constant) and isinstance(element.value, str):
                if element.value == "-c":
                    base["class"] = "1"
                    return base
                if element.value.startswith("-"):
                    continue
            return _finish_script(base, _resolve(element, facts, fn, frozenset()), node, facts, fn)
        return _unresolved_site(base, "an interpreter launch names no script")
    if len(program) == 1 and program[0][0] in ("anchored", "literal"):
        return _finish_script(base, program, node, facts, fn)
    if len(program) == 1 and program[0][0] == "scratch":
        return _scratch(base)
    return _unresolved_site(base, "launch program is not resolved")


def _classify_git(base, elements, node, facts, fn):
    for position, element in enumerate(elements[1:-1], start=1):
        if isinstance(element, ast.Constant) and element.value == "-C":
            target = _resolve(elements[position + 1], facts, fn, frozenset())
            if all(kind == "anchored" and payload == "" for kind, payload in target):
                base["class"] = "2"
                base["paths"] = ["**"]
                return base
            if all(kind == "scratch" for kind, _payload in target):
                return _scratch(base)
            return _unresolved_site(base, "git -C target is not the repository root or scratch")
    return _unresolved_site(base, "git launch carries no resolvable -C target")


def _finish_script(base, resolutions, node, facts, fn):
    paths = []
    for kind, payload in resolutions:
        if kind == "scratch":
            return _scratch(base)
        if kind == "unresolved":
            return _unresolved_site(base, payload)
        if kind == "literal":
            if payload.startswith("/") or payload.rpartition("/")[2] in INTERPRETER_WORDS:
                base["class"] = "1"
                return base
            adjusted = _relative_literal(node, facts, fn)
            if adjusted == "3a":
                return _scratch(base)
            if adjusted is not None:
                return _unresolved_site(base, adjusted)
            paths.append(posixpath.normpath(payload))
            base["class"] = "1"
        else:
            paths.append(payload)
            base["class"] = "2"
    base["scripts"] = sorted(set(paths))
    return base


def load_dispositions(text, where=DISPOSITIONS):
    """The validated disposition rows, or CannotEvaluate; an invalid file never reads as empty."""
    # ValueError and RecursionError too: tomllib raises a BARE ValueError on an oversized integer
    # literal and a RecursionError on deep nesting; neither is a TOMLDecodeError.
    try:
        data = tomllib.loads(text)
    except (tomllib.TOMLDecodeError, ValueError, RecursionError, MemoryError) as exc:
        raise CannotEvaluate("{}: is not valid TOML: {}: {}".format(where, type(exc).__name__, exc)) from None
    if type(data.get("format-version")) is not int or data["format-version"] != 1:
        raise CannotEvaluate("{}: format-version is not 1".format(where))
    unknown = sorted(set(data) - {"format-version", "disposition"})
    if unknown:
        raise CannotEvaluate("{}: unknown top-level keys {}".format(where, unknown))
    rows = data.get("disposition", [])
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise CannotEvaluate("{}: disposition is not an array of tables".format(where))
    for position, row in enumerate(rows):
        label = "{}: disposition #{}".format(where, position + 1)
        for key, kind in DISPOSITION_REQUIRED.items():
            if not isinstance(row.get(key), kind) or not row.get(key):
                raise CannotEvaluate("{}: field {} is missing or not a nonempty string".format(label, key))
        if row["kind"] not in DISPOSITION_KINDS:
            raise CannotEvaluate("{}: kind {!r} is outside {}".format(label, row["kind"], DISPOSITION_KINDS))
        fingerprint = row["fingerprint"]
        if len(fingerprint) != FINGERPRINT_LEN or any(c not in "0123456789abcdef" for c in fingerprint):
            raise CannotEvaluate("{}: fingerprint is not {} lowercase hex characters".format(
                label, FINGERPRINT_LEN))
        extra = sorted(set(row) - set(DISPOSITION_REQUIRED) - {"paths"})
        if extra:
            raise CannotEvaluate("{}: unknown fields {}".format(label, extra))
        if row["kind"] == "repo-paths":
            paths = row.get("paths")
            if not isinstance(paths, list) or not paths \
                    or not all(isinstance(p, str) and p and not p.startswith("/") for p in paths):
                raise CannotEvaluate("{}: repo-paths needs a nonempty paths array of relative globs"
                                     .format(label))
        elif "paths" in row:
            raise CannotEvaluate("{}: paths belongs to kind repo-paths only".format(label))
    return rows


def disposition_matches(row, rel, site):
    """Whether one disposition row covers one site: same file, qualname and current fingerprint.
    The flip self-test replaces this function to show the stale-fingerprint check turn red."""
    return row["file"] == rel and row["qualname"] == site["qualname"] \
        and row["fingerprint"] == site["fingerprint"]


def _apply_dispositions(site, rows, rel):
    """The site after its matching disposition, if any: scratch, repo-paths, whole-repo or dropped."""
    if site["class"] != "3b":
        return site
    for row in rows:
        if disposition_matches(row, rel, site):
            if row["kind"] == "not-a-read":
                return None
            adjusted = dict(site)
            if row["kind"] == "scratch":
                adjusted["class"] = "3a"
            else:
                adjusted["class"] = "2"
                adjusted["paths"] = ["**"] if row["kind"] == "whole-repo" else sorted(row["paths"])
            adjusted["reason"] = ""
            return adjusted
    return site


def step_inventory(run_text, tracked, read_source, dispositions, where, cache=None, module_cache=None):
    """One step's inventory: closure, data paths and globs, launched repository scripts, scratch-site
    count and unresolved sites, with launched tracked .py scripts followed transitively."""
    tracked = frozenset(tracked)
    cache = {} if cache is None else cache
    module_cache = {} if module_cache is None else module_cache
    closure, launches = set(), set()
    data, unresolved = {}, []
    scratch = 0
    pending = list(run_scripts(run_text, tracked, where))
    scripts_seen = set(pending)
    while pending:
        script = pending.pop()
        for rel in sorted(import_closure(script, tracked, read_source, cache) - closure):
            closure.add(rel)
            if rel not in module_cache:
                module_cache[rel] = classify_module(rel, read_source(rel))
            for raw in module_cache[rel]:
                site = _apply_dispositions(raw, dispositions, rel)
                if site is None:
                    continue
                if site["class"] == "3a":
                    scratch += 1
                    continue
                if site["class"] == "3b":
                    unresolved.append({"site": site["site"], "qualname": site["qualname"],
                                       "fingerprint": site["fingerprint"], "reason": site["reason"]})
                    continue
                for path in site["paths"]:
                    data.setdefault(path, site["site"])
                for candidate in site["scripts"]:
                    if candidate.endswith(".py") and candidate in tracked:
                        launches.add(candidate)
                        if candidate not in scripts_seen:
                            scripts_seen.add(candidate)
                            pending.append(candidate)
                    elif candidate in tracked:
                        data.setdefault(candidate, site["site"])
                        unresolved.append({"site": site["site"], "qualname": site["qualname"],
                                           "fingerprint": site["fingerprint"],
                                           "reason": "opaque non-Python launch of {}".format(candidate)})
                    else:
                        unresolved.append({"site": site["site"], "qualname": site["qualname"],
                                           "fingerprint": site["fingerprint"],
                                           "reason": "launched path {} is not a tracked file".format(candidate)})
    return {"closure": sorted(closure),
            "data": [{"path": path, "site": data[path]} for path in sorted(data)],
            "launches": sorted(launches),
            "scratch_sites": scratch,
            "unresolved": sorted(unresolved, key=lambda row: (row["site"], row["reason"]))}


def trigger_covers(trigger, datum):
    """Whether one trigger glob covers one inventoried datum. A concrete path is matched with
    glob_match; a glob datum (a listing's D/** or the whole-repo **) is covered only by the exact
    same glob, by **, or by a containing E/** whose E holds D."""
    if "*" not in datum and "?" not in datum:
        return glob_match(trigger, datum)
    if trigger == datum or trigger == "**":
        return True
    if trigger.endswith("/**") and datum.endswith("/**"):
        base, inner = trigger[:-3], datum[:-3]
        return inner == base or inner.startswith(base + "/")
    return False


def _tree_records(root):
    """The per-step inventory records of the tree at root, in workflow order."""
    workflow_text = read_text(root / WORKFLOW, WORKFLOW)
    dispositions = load_dispositions(read_text(root / DISPOSITIONS, DISPOSITIONS))
    tracked = tracked_files(root)

    def read_source(rel):
        return read_text(root / rel, rel)

    cache, module_cache = {}, {}
    for job, name, run, line in workflow_run_steps(workflow_text):
        record = step_inventory(run, tracked, read_source, dispositions,
                                "{}:{}".format(WORKFLOW, line), cache, module_cache)
        yield {"job": job, "step": name, **record}


def _print_json():
    try:
        for record in _tree_records(ROOT):
            print(json.dumps(record, sort_keys=True))
    except CannotEvaluate as exc:
        print("CANNOT EVALUATE: {}".format(exc), file=sys.stderr)
        return 2
    return 0


def _explain(job, step):
    try:
        for record in _tree_records(ROOT):
            if record["job"] == job and record["step"] == step:
                print(json.dumps(record, indent=2, sort_keys=True))
                return 0
    except CannotEvaluate as exc:
        print("CANNOT EVALUATE: {}".format(exc), file=sys.stderr)
        return 2
    print("error: no run: step {} / {} in {}; fail closed".format(job, step, WORKFLOW), file=sys.stderr)
    return 2


def _self_test():
    """Run the inventory checks where their ids are registered: the gate's suite owns them, so the
    one id set is declared once (tools/selftest_checks.toml) and this entry stays runnable alone."""
    import check_ci_selection
    check_ci_selection._inventory_checks()
    if check_ci_selection.FAILURES:
        print("SELF-TEST FAIL: ci_inputs {} of {} check(s) failed".format(
            len(check_ci_selection.FAILURES), len(check_ci_selection.EXECUTED)))
        for failure in check_ci_selection.FAILURES:
            print("  - " + failure)
        return 1
    print("SELF-TEST PASS: ci_inputs {} checks (the inventory checks of the ci-selection-selftest "
          "suite, run through the gate beside this tool; the gate's own --self-test runs the same "
          "checks under their registered suite)".format(len(check_ci_selection.EXECUTED)))
    return 0


def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]
    if argv == ["--self-test"]:
        return _self_test()
    if argv == ["--json"]:
        return _print_json()
    if len(argv) == 3 and argv[0] == "--explain":
        return _explain(argv[1], argv[2])
    print("error: unrecognized arguments {!r}; fail closed".format(argv), file=sys.stderr)
    return 2


# The child-status guard: an inline copy of the canonical guard in opf/tools/_child_status.py, which
# tools/check_child_status_coverage.py pins this copy to by AST. The one child this tool can start is
# the gate's tracked-file listing (git), whose exit status decides cannot-evaluate, so under an
# ignored SIGCHLD a failing listing would read as a success; the guard runs at the entry line.
# The status the probe child exits with; any value other than 0 and the refusal exit would do.
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


if __name__ == "__main__":
    sys.exit(_child_status_refusal_exit("ci_inputs.py") or main())
