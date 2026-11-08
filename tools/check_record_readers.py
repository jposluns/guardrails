#!/usr/bin/env python3
"""Record-reader inventory gate: every tool under tools/ that this static scan detects as reading this
project's records is on the consumer-switch list (.aiqt/opf-consumer-switch.toml) with a disposition, so a
later switch flips one listed entry and a detected reader nobody listed fails here. A read in a form the
scan cannot see (listed below) is not established by the list.

  check_record_readers.py              exit 0 clean, 1 finding, 2 cannot evaluate
  check_record_readers.py --self-test  assert every detection rule, every refusal and a pass over fixtures
  check_record_readers.py [--self-test] --execution-report ABS_PATH
  any other argument list is refused with exit 2

Detection is static. Every .py and .sh file under tools/ is read (any directory named _vendor is skipped,
since vendored code reads no project record). opf/tools/ is not scanned: it is the store's own toolkit,
the place readers switch to, not a reader of the records being switched. The switch list's [detect] table declares what to
look for, and a file is a detected reader when any rule below matches:
  name    a Python string constant equal to a declared record file name, or ending in that name after "/",
          ":" or "}" (a joined path, a format template such as "{}:changelog.toml" for git show REV:PATH,
          or the literal tail of an f-string or a concatenation)
  key     a Python subscript (x["k"]) or .get("k", ...) read of a declared registry key, or a declared key
          as an element of a literal tuple, list or set that a for loop or comprehension iterates
  flag    a Python string constant equal to a declared command-line flag that takes a record path
  module  a Python import of a declared record-reading module (import m, from m import x,
          importlib.import_module("m") or __import__("m")), or a string constant equal to m.py or ending
          in "/m.py", "m.py" after ":" or "}" (the module loaded by file path): an indirect reader
  shell   a .sh line holding a declared record file name or flag as a whole word
The key vocabulary is checked against its authoritative source: the registry keys the hook library
(.aiqt/core/hooks/scripts/aiqt_hooks.py) reads from the registry (reg, reg_data) or its record table (rec)
and resolves to a record path through its _orch_path(root, value) helper. A key is derived when a read of
the registry, of a receiver alias of it (r = reg) or of an alias of its bound .get (g = reg.get, by plain
or annotated assignment, or getattr(reg, "get")) sits inside an _orch_path value argument (positional or
keyword), or reaches one through same-scope bindings followed transitively: plain, annotated and augmented
assignments, walrus bindings, tuple and list targets (element-wise over a same-shape literal, else each
name takes the whole right side's reads), for, with and comprehension targets, and parameter defaults.
A resolvable key is a string constant, or a loop variable over a literal tuple, list or set of string
constants only that nothing else in the scope rebinds. A traced read whose key resolves to neither form
is cannot-evaluate naming a line, never a silent pass; so is a traced name bound by a nested def, class
or parameter that mentions the registry, a traced name bound only in an enclosing scope whose binding
involves the registry, a lambda mentioning the registry inside a traced expression, and a getattr on the
registry other than its bound .get. A key the derivation yields that [detect] keys lacks, a hook library
that cannot be read or parsed, or one that yields no key, is cannot-evaluate.
What the scan cannot see: a name, key or module file name built at run time from parts ("changelog" +
".toml", "aiqt_hooks" + ".py"), a record name in the middle of a longer string that is not a path ending, a
key held in a named collection before a loop iterates it, a module loaded through a variable name, a path
given only on a command line whose flag is not declared, a .sh file that runs a record-reading module, paths
kept in the untracked orchestration registry, readers outside tools/, and a read that never names a file,
key, flag or module. The residual the key derivation does not see: a helper handed the registry itself
(helper(reg)) or its bare bound .get may read any key behind the call, and a module-level helper name is
never followed into; a registry read through an attribute of another object, or made in another module, is
not derived; a record path the hook library resolves through any helper other than _orch_path (for example
a declared sentinel path it joins onto an anchor with os.path.join) is not seen and its key is not derived;
a read that reaches no _orch_path argument derives nothing and is not refused; and an _orch_path call whose
arguments are all *-splatted carries no readable value expression.
A self-test that writes record fixtures is detected like a reader; the
fixture-only disposition records it. A tool that imports a declared module for some other part of it is
detected too; the not-a-reader disposition records it, with the reason.

Each [[reader]] entry carries path, records (a non-empty list of names), disposition and reason. The
disposition is one of switch, stays-outside-store, fixture-only, not-a-reader, or indirect-via:<path>,
where <path> is another listed entry. Exit 1 (finding): a detected reader that is not listed; a listed file that is gone;
a listed file the scan no longer detects. Exit 2 (cannot evaluate): a missing, unreadable, non-UTF-8,
malformed, too deeply nested or schema-invalid switch list, a duplicate entry, an unknown disposition, an
indirect-via target that is not listed or an indirect-via cycle; a [detect] keys list that lacks a key the
hook library derives; a missing scan root, a symbolic link to a directory or a directory that cannot be
listed; a scanned source larger than MAX_BYTES or that is unreadable, not a regular file (a FIFO is
refused by the opened descriptor's type, never a blocked read), a symbolic link, not UTF-8 or not parseable
Python. A run with any cannot-evaluate cause exits 2 even when it also has findings.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: check_record_readers.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import ast
import os
import re
import stat
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SWITCH_LIST = ".aiqt/opf-consumer-switch.toml"
SCAN_ROOTS = ("tools",)
SKIP_DIRS = frozenset({"_vendor"})
SUFFIXES = (".py", ".sh")
MAX_BYTES = 8 * 1024 * 1024
FIXED_DISPOSITIONS = frozenset({"switch", "stays-outside-store", "fixture-only", "not-a-reader"})
VIA_PREFIX = "indirect-via:"
DETECT_FIELDS = ("names", "keys", "flags", "modules")
READER_FIELDS = frozenset({"path", "records", "disposition", "reason"})
TOP_FIELDS = frozenset({"format-version", "detect", "reader"})
# The authoritative source of registry keys: the keys the hook library reads from the registry (reg,
# reg_data) or its record table (rec) and resolves to a record path through its _orch_path(root, value)
# helper. Derived: a string-constant key (or a loop variable over a literal collection of string constants
# only, rebound nowhere else in its scope) read from the registry, a receiver alias of it or an alias of
# its bound .get, inside an _orch_path value argument or reaching one through same-scope bindings followed
# transitively. Refused (cannot-evaluate): a traced read whose key resolves to no string constant, and a
# traced name the trace cannot follow (a nested def, class, lambda, closure, global or registry attribute)
# where the registry is involved. Not seen: a helper handed the registry itself, a read in another module,
# a record path the hook library resolves through any helper other than _orch_path (one it joins onto an
# anchor itself), and registry reads that reach no _orch_path argument. [detect] keys must hold each
# derived key; a key the hook library gains and the list lacks is cannot-evaluate.
HOOK_LIBRARY = ".aiqt/core/hooks/scripts/aiqt_hooks.py"
HOOK_PATH_RESOLVER = "_orch_path"
HOOK_REGISTRY_NAMES = frozenset({"reg", "reg_data", "rec"})
# A record name ending a string after one of these is a path built by formatting or joining: "{}:name"
# (a git show REV:PATH argument), "{}/name", f"{d}/name" or rev + ":name".
NAME_JOINERS = ("/", ":", "}")


class CannotEvaluate(Exception):
    """An input that cannot be judged: unreadable, not a regular file, not UTF-8, unparseable or malformed."""


def _read_regular(path, what):
    """The bytes of path, opened non-blocking without following a final symbolic link, and refused unless
    the opened descriptor is a regular file of at most MAX_BYTES."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC)
    except FileNotFoundError:
        raise CannotEvaluate("{}: missing".format(what)) from None
    except OSError as exc:
        if os.path.islink(path):
            raise CannotEvaluate("{}: a symbolic link, not a regular file".format(what)) from None
        raise CannotEvaluate("{}: cannot be opened ({})".format(what, exc)) from None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            kind = "a FIFO" if stat.S_ISFIFO(info.st_mode) else "not a regular file"
            raise CannotEvaluate("{}: {}, not a regular file".format(what, kind))
        chunks, total = [], 0
        while True:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_BYTES:
                raise CannotEvaluate("{}: larger than {} bytes".format(what, MAX_BYTES))
            chunks.append(chunk)
        return b"".join(chunks)
    except OSError as exc:
        raise CannotEvaluate("{}: unreadable ({})".format(what, exc)) from None
    finally:
        os.close(fd)


def _parse_switch_text(text):
    """(data, error): the parsed switch list, or None and the reason it cannot be parsed. Every failure
    tomllib can raise on hostile input is handled here, at the parse site."""
    try:
        return tomllib.loads(text), None
    except tomllib.TOMLDecodeError as exc:
        return None, "not valid TOML ({})".format(exc)
    except RecursionError:
        return None, "nested too deeply to parse"
    except MemoryError:
        return None, "out of memory while parsing"
    except ValueError as exc:
        return None, "not valid TOML ({})".format(exc)


def _string_list(value, where):
    if not isinstance(value, list) or not value or not all(isinstance(v, str) and v for v in value):
        raise CannotEvaluate("{} must be a non-empty list of non-empty strings".format(where))
    if len(set(value)) != len(value):
        raise CannotEvaluate("{} repeats an element".format(where))
    return tuple(value)


def _valid_rel(path):
    parts = path.split("/")
    return (not path.startswith("/") and "" not in parts and "." not in parts and ".." not in parts
            and any(path.startswith(r + "/") for r in SCAN_ROOTS) and path.endswith(SUFFIXES))


def load_switch_list(root):
    """(detect, readers): the validated switch list. detect maps each DETECT_FIELDS name to a tuple;
    readers maps a repository-relative path to its entry. CannotEvaluate on any defect."""
    raw = _read_regular(os.path.join(root, SWITCH_LIST), SWITCH_LIST)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CannotEvaluate("{}: not UTF-8 ({})".format(SWITCH_LIST, exc)) from None
    data, error = _parse_switch_text(text)
    if error is not None:
        raise CannotEvaluate("{}: {}".format(SWITCH_LIST, error))
    unknown = sorted(set(data) - TOP_FIELDS)
    if unknown:
        raise CannotEvaluate("{}: unknown top-level field(s) {}".format(SWITCH_LIST, ", ".join(unknown)))
    if type(data.get("format-version")) is not int or data.get("format-version") != 1:
        raise CannotEvaluate("{}: format-version must be the integer 1".format(SWITCH_LIST))
    block = data.get("detect")
    if not isinstance(block, dict) or set(block) != set(DETECT_FIELDS):
        raise CannotEvaluate("{}: [detect] must hold exactly {}".format(SWITCH_LIST, ", ".join(DETECT_FIELDS)))
    detect = {field: _string_list(block[field], "[detect] " + field) for field in DETECT_FIELDS}
    rows = data.get("reader")
    if not isinstance(rows, list) or not rows:
        raise CannotEvaluate("{}: needs at least one [[reader]] entry".format(SWITCH_LIST))
    readers = {}
    for number, row in enumerate(rows, 1):
        where = "{} [[reader]] #{}".format(SWITCH_LIST, number)
        if not isinstance(row, dict) or set(row) != READER_FIELDS:
            raise CannotEvaluate("{}: must hold exactly {}".format(where, ", ".join(sorted(READER_FIELDS))))
        path, disposition, reason = row["path"], row["disposition"], row["reason"]
        if not isinstance(path, str) or not _valid_rel(path):
            raise CannotEvaluate("{}: path {!r} is not a .py or .sh path under {}".format(
                where, path, " or ".join(SCAN_ROOTS)))
        if path in readers:
            raise CannotEvaluate("{}: duplicate entry for {}".format(where, path))
        _string_list(row["records"], where + " records")
        if not isinstance(reason, str) or not reason.strip():
            raise CannotEvaluate("{}: reason must be a non-empty string".format(where))
        if not isinstance(disposition, str) or not (
                disposition in FIXED_DISPOSITIONS
                or (disposition.startswith(VIA_PREFIX) and _valid_rel(disposition[len(VIA_PREFIX):]))):
            raise CannotEvaluate("{}: unknown disposition {!r}".format(where, disposition))
        readers[path] = row
    for path, row in readers.items():
        disposition = row["disposition"]
        if disposition.startswith(VIA_PREFIX):
            target = disposition[len(VIA_PREFIX):]
            if target == path or target not in readers:
                raise CannotEvaluate("{}: {} names {}, which is not another listed entry".format(
                    SWITCH_LIST, path, disposition))
    # An indirect-via chain must end at an entry that is not itself indirect: a cycle (A via B, B via A, or
    # longer) names no reader the switch would flip. Each step visits a new entry, so the walk is bounded.
    for path in readers:
        seen, current = [path], path
        while readers[current]["disposition"].startswith(VIA_PREFIX):
            current = readers[current]["disposition"][len(VIA_PREFIX):]
            if current in seen:
                raise CannotEvaluate("{}: indirect-via cycle {}".format(
                    SWITCH_LIST, " -> ".join(seen + [current])))
            seen.append(current)
    return detect, readers


def _parse_python(path, rel):
    """The parsed module at path, or CannotEvaluate naming rel when it cannot be read, decoded or parsed."""
    text = _read_regular(path, rel)
    try:
        text = text.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CannotEvaluate("{}: not UTF-8 ({})".format(rel, exc)) from None
    try:
        return ast.parse(text, filename=rel)
    except (SyntaxError, ValueError) as exc:
        raise CannotEvaluate("{}: not parseable Python ({})".format(rel, exc)) from None
    except RecursionError:
        raise CannotEvaluate("{}: nested too deeply to parse".format(rel)) from None


def _is_registry(node, extra=frozenset()):
    """True for a registry receiver: a name in HOOK_REGISTRY_NAMES or in extra (a scope's receiver
    aliases, r = reg), or an `or` whose first operand is one: (reg or {})."""
    if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or) and node.values:
        node = node.values[0]
    return isinstance(node, ast.Name) and (node.id in HOOK_REGISTRY_NAMES or node.id in extra)


def _scope_walk(scope):
    """Every node of one function or module body, not descending into a nested function, so a loop
    variable is resolved only within the scope that binds it."""
    stack = list(ast.iter_child_nodes(scope))
    while stack:
        node = stack.pop()
        yield node
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            stack.extend(ast.iter_child_nodes(node))


class _Scope:
    """One name-binding region (the module, a def, an async def or a lambda): its binding records by
    name, the names it declares global or nonlocal, its receiver aliases (names holding the registry
    itself) and .get aliases (names holding a registry's bound .get), and its enclosing scope."""

    def __init__(self, node, parent):
        self.node = node
        self.parent = parent
        self.bindings = {}
        self.global_names = set()
        self.nonlocal_names = set()
        self.receivers = set()
        self.get_aliases = set()

    def bind(self, name, kind, lineno, payload):
        """One binding record. kind "expr": payload is the bound expression, whose registry reads the
        name carries. kind "loop": payload is a frozenset of the literal loop strings, or None for a
        literal collection holding any non-string element (deriving only the literal part of a mixed
        collection would silently drop the rest). kind "def" or "param": payload is the AST the trace
        cannot follow (a nested def, async def or class; a parameter's default), or None. kind
        "import": an imported name, which cannot hold this process's registry."""
        self.bindings.setdefault(name, []).append((kind, lineno, payload))


def _target_names(target):
    """Every plain name a target binds, however nested the tuple, list or starred pattern."""
    if isinstance(target, ast.Name):
        yield target
    elif isinstance(target, (ast.Tuple, ast.List)):
        for element in target.elts:
            yield from _target_names(element)
    elif isinstance(target, ast.Starred):
        yield from _target_names(target.value)


def _bind_target(scope, target, value):
    """One assignment of value to target. A Tuple or List target over a same-length Tuple or List
    literal with no starred element binds element-wise; any other shape gives every bound name the
    whole right side's reads (the element came out of it, so this is a superset, never a loss)."""
    if (isinstance(target, (ast.Tuple, ast.List)) and isinstance(value, (ast.Tuple, ast.List))
            and len(target.elts) == len(value.elts)
            and not any(isinstance(e, ast.Starred) for e in list(target.elts) + list(value.elts))):
        for element, part in zip(target.elts, value.elts):
            _bind_target(scope, element, part)
        return
    for name in _target_names(target):
        scope.bind(name.id, "expr", getattr(value, "lineno", name.lineno), value)


def _bind_assign(scope, node):
    for target in node.targets:
        _bind_target(scope, target, node.value)


def _bind_annassign(scope, node):
    if node.value is not None:
        _bind_target(scope, node.target, node.value)


def _bind_augassign(scope, node):
    if isinstance(node.target, ast.Name):
        scope.bind(node.target.id, "expr", node.value.lineno, node.value)


def _bind_walrus(scope, node):
    if isinstance(node.target, ast.Name):
        scope.bind(node.target.id, "expr", node.value.lineno, node.value)


def _bind_loop(scope, node):
    """A For, AsyncFor or comprehension target: a plain name over a literal collection of string
    constants only is a resolvable key source; every other shape gives each bound name the iterated
    expression's reads."""
    if isinstance(node.target, ast.Name) and isinstance(node.iter, (ast.Tuple, ast.List, ast.Set)):
        values = [e.value for e in node.iter.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)]
        payload = frozenset(values) if len(values) == len(node.iter.elts) else None
        scope.bind(node.target.id, "loop", node.iter.lineno, payload)
        return
    for name in _target_names(node.target):
        scope.bind(name.id, "expr", node.iter.lineno, node.iter)


def _bind_with(scope, node):
    for item in node.items:
        if item.optional_vars is not None:
            _bind_target(scope, item.optional_vars, item.context_expr)


def _bind_scope_name(scope, node):
    # A nested def, async def or class binds its name; its body runs later, so the trace records
    # only the node, and refuses a later use when the body mentions the registry.
    scope.bind(node.name, "def", node.lineno, node)


def _bind_import(scope, node):
    for alias in node.names:
        scope.bind(alias.asname or alias.name.split(".")[0], "import", node.lineno, None)


def _bind_global(scope, node):
    scope.global_names.update(node.names)


def _bind_nonlocal(scope, node):
    scope.nonlocal_names.update(node.names)


# Every name-binding statement form the trace resolves, by node type; the self-test removes each in
# turn to prove the rows that cover it depend on it.
_BINDING_FORMS = {
    ast.Assign: _bind_assign, ast.AnnAssign: _bind_annassign, ast.AugAssign: _bind_augassign,
    ast.NamedExpr: _bind_walrus, ast.For: _bind_loop, ast.AsyncFor: _bind_loop,
    ast.comprehension: _bind_loop, ast.With: _bind_with, ast.AsyncWith: _bind_with,
    ast.FunctionDef: _bind_scope_name, ast.AsyncFunctionDef: _bind_scope_name,
    ast.ClassDef: _bind_scope_name, ast.Import: _bind_import, ast.ImportFrom: _bind_import,
    ast.Global: _bind_global, ast.Nonlocal: _bind_nonlocal,
}


def _bind_params(scope, node):
    """The parameters of a def or lambda scope: one whose default is the registry itself (or an `or`
    of it) carries the registry in under the parameter's name; every other parameter is bound by the
    caller, opaque to the trace, with its default kept for the registry-mention check."""
    args = node.args
    positional = list(args.posonlyargs) + list(args.args)
    defaults = {}
    for arg, default in zip(positional[len(positional) - len(args.defaults):], args.defaults):
        defaults[arg.arg] = default
    for arg, default in zip(args.kwonlyargs, args.kw_defaults):
        defaults[arg.arg] = default
    names = [a.arg for a in positional + list(args.kwonlyargs)]
    names.extend(extra.arg for extra in (args.vararg, args.kwarg) if extra is not None)
    for name in names:
        default = defaults.get(name)
        if default is not None and _is_registry(default):
            scope.receivers.add(name)
        else:
            scope.bind(name, "param", node.lineno, default)


def _collect_scopes(node, parent, scopes):
    """The scope of node and, recursively, of every def, async def and lambda inside it, each with
    its binding records collected."""
    scope = _Scope(node, parent)
    scopes.append(scope)
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
        _bind_params(scope, node)
    for child in _scope_walk(node):
        handler = _BINDING_FORMS.get(type(child))
        if handler is not None:
            handler(scope, child)
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            _collect_scopes(child, scope, scopes)
    return scopes


def _share_outer_bindings(scopes):
    """A binding made under a global or nonlocal declaration binds the outer name: its records are
    copied to the module scope (global) or to the nearest enclosing function scope that also binds
    the name, else the immediate enclosing scope (nonlocal), so a registry read written through
    either declaration is seen where the name is used."""
    module_scope = scopes[0]
    for scope in scopes:
        for name in sorted(scope.global_names):
            for record in scope.bindings.get(name, []):
                module_scope.bindings.setdefault(name, []).append(record)
        for name in sorted(scope.nonlocal_names):
            target = scope.parent
            while (target is not None and target.parent is not None
                   and name not in target.bindings):
                target = target.parent
            if target is not None and target.parent is not None:
                for record in scope.bindings.get(name, []):
                    target.bindings.setdefault(name, []).append(record)


def _is_get_alias_expr(expr, scope):
    """True when expr is a registry receiver's bound .get: r.get, (r or {}).get or getattr(r, "get")
    with r the registry or a receiver alias of it."""
    if isinstance(expr, ast.Attribute) and expr.attr == "get" and _is_registry(expr.value, scope.receivers):
        return True
    return (isinstance(expr, ast.Call) and isinstance(expr.func, ast.Name) and expr.func.id == "getattr"
            and len(expr.args) >= 2 and _is_registry(expr.args[0], scope.receivers)
            and isinstance(expr.args[1], ast.Constant) and expr.args[1].value == "get")


def _alias_fixpoint(scope):
    """Grow scope's receiver and .get-alias names to a fixed point: r = reg makes r a receiver,
    g = r.get (by plain or annotated assignment, or getattr(r, "get")) makes g a .get alias, and a
    chain of such bindings is followed. A name with any one such binding counts, whatever its other
    bindings hold: the fail direction is a read seen, never one missed."""
    changed = True
    while changed:
        changed = False
        for name, records in scope.bindings.items():
            for kind, _lineno, payload in records:
                if kind != "expr":
                    continue
                if name not in scope.receivers and _is_registry(payload, scope.receivers):
                    scope.receivers.add(name)
                    changed = True
                if name not in scope.get_aliases and _is_get_alias_expr(payload, scope):
                    scope.get_aliases.add(name)
                    changed = True


def _mentions_registry(node, scope):
    """True when any name under node is the registry, or a receiver or .get alias of scope or of a
    scope enclosing it."""
    known = set(HOOK_REGISTRY_NAMES)
    cursor = scope
    while cursor is not None:
        known |= cursor.receivers | cursor.get_aliases
        cursor = cursor.parent
    return any(isinstance(n, ast.Name) and n.id in known for n in ast.walk(node))


def _resolve_key(key, scope):
    """(strings, ok): the string constants a read's key can hold. ok is False when the scan cannot
    pin them: a missing or non-constant key expression, or a loop variable that anything else in the
    scope rebinds (the literal loop strings would be stale)."""
    if isinstance(key, ast.Constant) and isinstance(key.value, str):
        return {key.value}, True
    if isinstance(key, ast.Name):
        records = scope.bindings.get(key.id, [])
        if records and all(kind == "loop" and payload is not None for kind, _lineno, payload in records):
            strings = set()
            for _kind, _lineno, payload in records:
                strings |= payload
            return strings, True
    return set(), False


def _enclosing_refusal(name, use_lineno, scope):
    """A (lineno, reason) refusal when name is bound in an enclosing scope with the registry
    involved: the trace never follows a value across scopes, so a closure over a registry read or
    over a receiver alias fails closed. A module-level def or class name is the documented helper
    residual, never followed and never refused; any other enclosing binding with no registry
    mention resolves to nothing. None when no enclosing binding involves the registry."""
    cursor = scope.parent
    while cursor is not None:
        if name in cursor.receivers or name in cursor.get_aliases:
            return (use_lineno, "a registry receiver or .get alias reaching in from an enclosing scope")
        records = cursor.bindings.get(name, [])
        for kind, lineno, payload in records:
            if kind == "def" and cursor.parent is None:
                continue
            if kind in ("expr", "def", "param") and payload is not None \
                    and _mentions_registry(payload, cursor):
                return (lineno, "a binding in an enclosing scope that involves the registry")
        if records:
            return None
        cursor = cursor.parent
    return None


def _trace_name(name, use_lineno, scope, visited):
    """(keys, refusals) carried by one name a traced value uses. Each same-scope binding is
    followed: an expression binding contributes its reads; a loop binding holds strings, which
    carry no read; a nested def, class or parameter binding whose kept AST mentions the registry
    is a refusal, and one that does not mentions nothing the trace needs. A name with no same-scope
    binding (or one declared global or nonlocal) is judged by _enclosing_refusal."""
    token = (id(scope), name)
    if token in visited:
        return set(), []
    visited.add(token)
    keys, refusals = set(), []
    records = scope.bindings.get(name, [])
    for kind, lineno, payload in records:
        if kind == "expr":
            got, bad = _expr_reads(payload, scope, visited)
            keys |= got
            refusals.extend(bad)
        elif kind in ("def", "param") and payload is not None and _mentions_registry(payload, scope):
            refusals.append((lineno, "a binding the trace cannot follow that involves the registry"))
    if not records or name in scope.global_names or name in scope.nonlocal_names:
        outer = _enclosing_refusal(name, use_lineno, scope)
        if outer is not None:
            refusals.append(outer)
    return keys, refusals


def _expr_reads(expr, scope, visited):
    """(keys, refusals): every registry read the scan can see in expr, traced through same-scope
    bindings transitively (visited bounds the walk). keys are the resolved string-constant keys;
    refusals are (lineno, reason) rows the caller fails closed on: a read whose key the scan cannot
    pin, a getattr on the registry other than its bound .get, a lambda that mentions the registry
    (its body runs later, where the trace cannot follow), and the per-name refusals of
    _trace_name."""
    keys, refusals = set(), []
    traced = set()
    for node in ast.walk(expr):
        is_read, key = False, None
        if isinstance(node, ast.Subscript) and _is_registry(node.value, scope.receivers):
            is_read, key = True, node.slice
        elif isinstance(node, ast.Call):
            func = node.func
            if ((isinstance(func, ast.Attribute) and func.attr == "get"
                 and _is_registry(func.value, scope.receivers))
                    or (isinstance(func, ast.Name) and func.id in scope.get_aliases)
                    or _is_get_alias_expr(func, scope)):
                is_read, key = True, (node.args[0] if node.args else None)
            elif (isinstance(func, ast.Name) and func.id == "getattr" and node.args
                  and _is_registry(node.args[0], scope.receivers)
                  and not _is_get_alias_expr(node, scope)):
                refusals.append((node.lineno, "a getattr on the registry other than its bound .get"))
                continue
        if is_read:
            strings, ok = _resolve_key(key, scope)
            if ok:
                keys |= strings
            else:
                refusals.append((node.lineno, "a key the scan cannot resolve to a string constant"))
            continue
        if isinstance(node, ast.Lambda) and _mentions_registry(node, scope):
            refusals.append((node.lineno, "a lambda that mentions the registry"))
            continue
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            # Every loaded name is traced, the registry's own names too: rec bound from
            # reg.get("record") carries that read to wherever rec is used.
            traced.add((node.id, node.lineno))
    for name, lineno in sorted(traced):
        got, bad = _trace_name(name, lineno, scope, visited)
        keys |= got
        refusals.extend(bad)
    return keys, refusals


def hook_registry_keys(root):
    """The sorted registry keys the hook library resolves to a record path. Derived: each read
    reg.get(K), rec[K], (reg_data or {}).get(K), a call of a .get alias (g = reg.get, by plain or
    annotated assignment, or g = getattr(reg, "get")) and a read through a receiver alias (r = reg,
    then r.get(K)), where K is a string constant or a loop variable over a literal collection of
    string constants that nothing else in the scope rebinds; found inside a value argument
    (positional or keyword) of _orch_path(root, ...) directly, or reached from one through same-scope
    bindings followed transitively: assignments (tuple and list targets bind element-wise over a
    same-shape literal, else each name takes the whole right side's reads), annotated and augmented
    assignments, walrus bindings, for and with targets, comprehension targets, and a parameter
    default. Refused (CannotEvaluate naming a line): a traced read whose key resolves to no string
    constant (a loop variable rebound anywhere else in the scope is unresolved, not its stale
    literal); a traced name bound by a nested def, class or parameter whose kept source mentions the
    registry; a traced name bound only in an enclosing scope whose binding there involves the
    registry (the trace stays inside one scope); a lambda mentioning the registry inside a traced
    expression; a getattr on the registry other than its bound .get; a hook library that cannot be
    read or parsed, or one that yields no key at all (a derivation that finds nothing would pass
    every list vacuously). The residual the trace does not see: a helper handed the registry itself
    (helper(reg)) or its bare bound .get may read any key behind the call, and a module-level helper
    name is never followed into; a registry read through an attribute of another object, or made in
    another module, is not derived; those reads are refused only where a traced binding or lambda
    names the registry."""
    tree = _parse_python(os.path.join(root, HOOK_LIBRARY), HOOK_LIBRARY)
    keys = set()
    try:
        scopes = _collect_scopes(tree, None, [])
        _share_outer_bindings(scopes)
        for scope in scopes:
            _alias_fixpoint(scope)
        for scope in scopes:
            for call in _scope_walk(scope.node):
                if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
                        and call.func.id == HOOK_PATH_RESOLVER):
                    continue
                values = list(call.args[1:]) + [kw.value for kw in call.keywords if kw.arg != "root"]
                for value in values:
                    got, refusals = _expr_reads(value, scope, set())
                    keys.update(got)
                    if refusals:
                        lineno, reason = sorted(refusals)[0]
                        raise CannotEvaluate(
                            "{}: the registry read at line {} reaching an {} argument has {}, so the "
                            "[detect] keys cannot be checked against it".format(
                                HOOK_LIBRARY, lineno, HOOK_PATH_RESOLVER, reason))
    except RecursionError:
        raise CannotEvaluate("{}: nested too deeply to analyse".format(HOOK_LIBRARY)) from None
    if not keys:
        raise CannotEvaluate("{}: no registry key is read inside a {}(root, ...) argument, so the [detect] "
                             "keys cannot be checked against it".format(HOOK_LIBRARY, HOOK_PATH_RESOLVER))
    return tuple(sorted(keys))


def _ends_in_name(value, names):
    return next((n for n in names if value == n or any(value.endswith(j + n) for j in NAME_JOINERS)), None)


def _rule_name(tree, detect):
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            hit = _ends_in_name(node.value, detect["names"])
            if hit is not None:
                yield node.lineno, "name {!r}".format(hit)


def _rule_key(tree, detect):
    keys = detect["keys"]
    for node in ast.walk(tree):
        key = None
        if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant):
            key = node.slice.value
        elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get"
              and node.args and isinstance(node.args[0], ast.Constant)):
            key = node.args[0].value
        if isinstance(key, str) and key in keys:
            yield node.lineno, "registry key {!r}".format(key)
        # A key iterated from a literal collection: for k in ("findings", "mode"): reg.get(k).
        if (isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension))
                and isinstance(node.iter, (ast.Tuple, ast.List, ast.Set))):
            for element in node.iter.elts:
                if isinstance(element, ast.Constant) and isinstance(element.value, str) and element.value in keys:
                    yield element.lineno, "registry key {!r} iterated".format(element.value)


def _rule_flag(tree, detect):
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value in detect["flags"]:
            yield node.lineno, "flag {!r}".format(node.value)


def _rule_module(tree, detect):
    modules = detect["modules"]
    for node in ast.walk(tree):
        names = ()
        if isinstance(node, ast.Import):
            names = tuple(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            names = (node.module,)
        elif (isinstance(node, ast.Call) and isinstance(node.func, (ast.Attribute, ast.Name))
              and getattr(node.func, "attr", getattr(node.func, "id", None)) in ("import_module", "__import__")
              and node.args and isinstance(node.args[0], ast.Constant)):
            names = (node.args[0].value,)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            # A module loaded by file path (importlib.util.spec_from_file_location, runpy.run_path, a
            # SourceFileLoader): a string naming the module's own file, whole or as its last component.
            hit = _ends_in_name(node.value, tuple(m + ".py" for m in modules))
            if hit is not None:
                yield node.lineno, "names module file {!r}".format(hit)
        for name in names:
            if isinstance(name, str) and name in modules:
                yield node.lineno, "imports {!r}".format(name)


def _rule_shell(text, detect):
    for number, line in enumerate(text.split("\n"), 1):
        for word in detect["names"] + detect["flags"]:
            if re.search(r"(?<![A-Za-z0-9_.-])" + re.escape(word) + r"(?![A-Za-z0-9_-])", line):
                yield number, "shell word {!r}".format(word)


# Each detection rule, by name; the self-test removes each in turn to prove a vector depends on it.
PY_RULES = {"name": _rule_name, "key": _rule_key, "flag": _rule_flag, "module": _rule_module}
SH_RULES = {"shell": _rule_shell}


def detect_file(path, rel, detect):
    """The sorted (line, evidence) hits for one source file. CannotEvaluate when it cannot be judged."""
    hits = set()
    if rel.endswith(".sh"):
        text = _read_regular(path, rel)
        try:
            text = text.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise CannotEvaluate("{}: not UTF-8 ({})".format(rel, exc)) from None
        for rule in SH_RULES.values():
            hits.update(rule(text, detect))
        return sorted(hits)
    tree = _parse_python(path, rel)
    for rule in PY_RULES.values():
        hits.update(rule(tree, detect))
    return sorted(hits)


def walk_sources(root):
    """(sources, errors): every repository-relative .py and .sh path under the scan roots, outside _vendor;
    a symbolic link to a file or directory is an error, never followed and never skipped."""
    sources, errors = [], []
    for scan_root in SCAN_ROOTS:
        base = os.path.join(root, scan_root)
        if not os.path.isdir(base) or os.path.islink(base):
            errors.append("{}: scan root missing or not a plain directory".format(scan_root))
            continue

        def _walk_error(exc):
            errors.append("cannot list a scanned directory ({})".format(exc))
        for directory, dirnames, filenames in os.walk(base, onerror=_walk_error):
            rel_dir = os.path.relpath(directory, root).replace(os.sep, "/")
            for name in list(dirnames):
                full = os.path.join(directory, name)
                if name in SKIP_DIRS:
                    dirnames.remove(name)
                elif os.path.islink(full):
                    dirnames.remove(name)
                    errors.append("{}/{}: a symbolic link to a directory, not scanned".format(rel_dir, name))
            dirnames.sort()
            for name in sorted(filenames):
                if name.endswith(SUFFIXES):
                    sources.append("{}/{}".format(rel_dir, name))
    return sources, errors


def scan(root):
    """(findings, errors, detected): findings fail the gate, errors make it cannot-evaluate."""
    try:
        detect, readers = load_switch_list(root)
    except CannotEvaluate as exc:
        return [], [str(exc)], {}
    try:
        lacking = [k for k in hook_registry_keys(root) if k not in detect["keys"]]
    except CannotEvaluate as exc:
        return [], [str(exc)], {}
    if lacking:
        return [], ["{}: [detect] keys lacks {}, which {} resolves to a record path through {}".format(
            SWITCH_LIST, ", ".join(repr(k) for k in lacking), HOOK_LIBRARY, HOOK_PATH_RESOLVER)], {}
    sources, errors = walk_sources(root)
    detected = {}
    for rel in sources:
        try:
            hits = detect_file(os.path.join(root, rel), rel, detect)
        except CannotEvaluate as exc:
            errors.append(str(exc))
            continue
        if hits:
            detected[rel] = hits
    findings = []
    for rel in sorted(detected):
        if rel not in readers:
            line, evidence = detected[rel][0]
            findings.append("{}: reads a project record ({} at line {}) but is not on the switch list".format(
                rel, evidence, line))
    present = set(sources)
    for rel in sorted(readers):
        if rel not in present:
            findings.append("{}: listed on the switch list but the file is gone".format(rel))
        elif rel not in detected and not any(e.startswith(rel + ":") for e in errors):
            findings.append("{}: listed on the switch list but no longer detected as a reader".format(rel))
    return findings, errors, detected


def run_gate(root):
    findings, errors, detected = scan(root)
    for line in errors:
        print("CANNOT EVALUATE: " + line, file=sys.stderr)
    for line in findings:
        print("FAIL: " + line)
    if errors:
        return 2
    if findings:
        return 1
    print("PASS: {} record reader(s) under {} are each on {} with a disposition".format(
        len(detected), " and ".join(SCAN_ROOTS), SWITCH_LIST))
    return 0


# The registered execution-suite identity (tools/selftest_checks.toml) and the executed-check-id record the
# instrumented check() helper appends to; the execution gate reconciles the report against the registry.
SUITE_ID = "record-readers-selftest"
EXECUTED = []
FAILURES = []


def check(name, ok, detail=""):
    if name in EXECUTED:
        print("SELF-TEST HARNESS ERROR: duplicate check id {!r}".format(name), file=sys.stderr)
        sys.exit(2)
    EXECUTED.append(name)
    if not ok:
        FAILURES.append("{}: {!r}".format(name, detail))


# Fixture vocabulary: deliberately not this project's record names, so the gate's own source never reads
# as a reader of them.
_FIXTURE_DETECT = (
    "[detect]\n"
    'names = ["ledger.toml"]\n'
    'keys = ["ledger_path"]\n'
    'flags = ["--ledger"]\n'
    'modules = ["ledger_lib"]\n')
_FIXTURE_FILES = {
    "tools/by_name.py": 'P = "data/ledger.toml"\n',
    "tools/by_key.py": 'def f(reg):\n    return reg.get("ledger_path")\n',
    "tools/by_flag.py": 'import sys\nA = sys.argv.index("--ledger")\n',
    "tools/by_module.py": "import ledger_lib\n",
    "tools/by_shell.sh": "#!/bin/bash\ncat ledger.toml\n",
    "tools/plain.py": 'X = "ledger.toml.bak"\nY = "myledger.toml"\n',
    "tools/_vendor/ignored.py": 'P = "ledger.toml"\n',
    # The fixture hook library: the one registry key it resolves through _orch_path is in [detect] keys.
    HOOK_LIBRARY: 'def a(root, reg):\n    return _orch_path(root, reg.get("ledger_path"))\n',
}
_FIXTURE_ENTRIES = (
    ("tools/by_name.py", "switch"),
    ("tools/by_key.py", "stays-outside-store"),
    ("tools/by_flag.py", "fixture-only"),
    ("tools/by_module.py", "indirect-via:tools/by_name.py"),
    ("tools/by_shell.sh", "switch"),
)
# The clean fixture's reader that only each detection rule finds.
_RULE_FIXTURE = {"name": "tools/by_name.py", "key": "tools/by_key.py", "flag": "tools/by_flag.py",
                 "module": "tools/by_module.py", "shell": "tools/by_shell.sh"}


def _entry(path, disposition):
    return ('[[reader]]\npath = "{}"\nrecords = ["ledger"]\ndisposition = "{}"\n'
            'reason = "a fixture reader"\n'.format(path, disposition))


def _switch_text(entries=_FIXTURE_ENTRIES, detect=_FIXTURE_DETECT):
    return "format-version = 1\n" + detect + "".join(_entry(p, d) for p, d in entries)


def _fixture(tmp, files=None, switch=None):
    files = dict(_FIXTURE_FILES if files is None else files)
    for rel, body in files.items():
        full = Path(tmp) / rel
        os.makedirs(full.parent, exist_ok=True)
        full.write_bytes(body if isinstance(body, bytes) else body.encode("utf-8"))
    os.makedirs(Path(tmp) / "tools", exist_ok=True)
    os.makedirs(Path(tmp) / "opf" / "tools", exist_ok=True)
    os.makedirs(Path(tmp) / ".aiqt", exist_ok=True)
    if switch is not None:
        (Path(tmp) / SWITCH_LIST).write_bytes(switch if isinstance(switch, bytes) else switch.encode("utf-8"))
    return tmp


def _gate(files=None, switch=None, prepare=None):
    import contextlib
    import io
    import tempfile
    with tempfile.TemporaryDirectory(prefix="record-readers-selftest-") as tmp:
        _fixture(tmp, files, _switch_text() if switch is None else switch)
        if prepare is not None:
            prepare(tmp)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc, crash = _guarded(run_gate, tmp)
        # An exception the gate fails to handle makes the row fail by its own name, never abort the run.
        return ("uncaught " + crash) if crash is not None else rc, out.getvalue() + err.getvalue()


def _guarded(fn, *args):
    """(fn(*args), None), or (None, the exception's class and text) when fn raises."""
    try:
        return fn(*args), None
    except Exception as exc:
        return None, "{}: {}".format(type(exc).__name__, exc)


def _hook_keys(hook_body):
    """(keys, crash): hook_registry_keys over a fixture tree whose hook library is hook_body."""
    import tempfile
    with tempfile.TemporaryDirectory(prefix="record-readers-selftest-") as tmp:
        _fixture(tmp, {HOOK_LIBRARY: hook_body}, _switch_text())
        return _guarded(hook_registry_keys, tmp)


def _without_rule(table, name, files, switch=None):
    """The gate's exit on a fixture with one detection rule removed."""
    saved = table.pop(name)
    try:
        return _gate(files, switch)
    finally:
        table[name] = saved


def _self_test():
    rc, out = _gate()
    check("pass/clean-fixture", rc == 0 and "PASS: 5 record reader" in out, (rc, out))

    # One unlisted reader per detection rule exits 1, naming the file and the rule that found it.
    for rule, rel, body, evidence, detect_id, nonvacuity_id in (
            ("name", "tools/new_name.py", 'P = "ledger.toml"\n', "name 'ledger.toml'",
             "detect/unlisted-name-reader", "nonvacuity/name-rule-removed-passes"),
            ("key", "tools/new_key.py", 'def f(r):\n    return r["ledger_path"]\n', "registry key 'ledger_path'",
             "detect/unlisted-key-reader", "nonvacuity/key-rule-removed-passes"),
            ("flag", "tools/new_flag.py", 'F = "--ledger"\n', "flag '--ledger'",
             "detect/unlisted-flag-reader", "nonvacuity/flag-rule-removed-passes"),
            ("module", "tools/new_module.py", "from ledger_lib import read\n", "imports 'ledger_lib'",
             "detect/unlisted-module-reader", "nonvacuity/module-rule-removed-passes"),
            ("shell", "tools/new_shell.sh", "#!/bin/bash\npython3 x.py --ledger \"$1\"\n",
             "shell word '--ledger'", "detect/unlisted-shell-reader", "nonvacuity/shell-rule-removed-passes")):
        files = dict(_FIXTURE_FILES, **{rel: body})
        rc, out = _gate(files)
        check(detect_id, rc == 1 and rel in out and evidence in out and "not on the switch list" in out,
              (rc, out))
        # Non-vacuity: with this rule removed the same unlisted reader passes unseen, so the vector above
        # depends on the rule. The clean fixture's own reader for this rule is dropped from the files and
        # the list (it too would go unseen); by_module's indirect-via target is by_name, so when by_name is
        # dropped by_module is listed as a plain switch entry instead.
        own = _RULE_FIXTURE[rule]
        files = {k: v for k, v in _FIXTURE_FILES.items() if k != own}
        files[rel] = body
        entries = tuple((p, "switch" if p == "tools/by_module.py" and own == "tools/by_name.py" else d)
                        for p, d in _FIXTURE_ENTRIES if p != own)
        table = SH_RULES if rule == "shell" else PY_RULES
        rc, out = _without_rule(table, rule, files, _switch_text(entries))
        check(nonvacuity_id, rc == 0, (rc, out))

    rc, out = _gate(dict(_FIXTURE_FILES, **{"opf/tools/store_reader.py": 'P = "ledger.toml"\n'}))
    check("ignore/outside-scan-root", rc == 0 and "store_reader" not in out, (rc, out))

    files = dict(_FIXTURE_FILES)
    del files["tools/by_key.py"]
    rc, out = _gate(files)
    check("refuse/listed-file-gone", rc == 1 and "tools/by_key.py: listed on the switch list but the file is gone"
          in out, (rc, out))

    rc, out = _gate(dict(_FIXTURE_FILES, **{"tools/by_key.py": "def f(reg):\n    return reg\n"}))
    check("refuse/listed-file-no-longer-reads", rc == 1 and "tools/by_key.py: listed on the switch list but no "
          "longer detected" in out, (rc, out))

    check("ignore/vendor-dir", "_vendor" not in _gate()[1] and _gate()[0] == 0, _gate())
    check("ignore/near-miss-strings", "plain.py" not in _gate()[1], _gate())

    def _fifo(tmp):
        os.mkfifo(os.path.join(tmp, "tools", "pipe.py"))
    rc, out = _gate(prepare=_fifo)
    check("refuse/fifo-source-cannot-evaluate", rc == 2 and "tools/pipe.py: a FIFO" in out, (rc, out))

    def _unreadable(tmp):
        os.chmod(os.path.join(tmp, "tools", "by_name.py"), 0)
    if os.geteuid() == 0:
        original = _read_regular

        def _deny(path, what):
            if what == "tools/by_name.py":
                raise CannotEvaluate("{}: cannot be opened (read refused by the self-test)".format(what))
            return original(path, what)
        globals()["_read_regular"] = _deny
        try:
            rc, out = _gate()
        finally:
            globals()["_read_regular"] = original
    else:
        rc, out = _gate(prepare=_unreadable)
    check("refuse/unreadable-source-cannot-evaluate", rc == 2 and "tools/by_name.py: cannot be opened" in out,
          (rc, out))

    def _symlink(tmp):
        os.symlink("by_name.py", os.path.join(tmp, "tools", "alias.py"))
    rc, out = _gate(prepare=_symlink)
    check("refuse/symlinked-source-cannot-evaluate", rc == 2 and "tools/alias.py: a symbolic link" in out,
          (rc, out))

    rc, out = _gate(dict(_FIXTURE_FILES, **{"tools/latin.py": b'X = "caf\xe9"\n'}))
    check("refuse/non-utf8-source-cannot-evaluate", rc == 2 and "tools/latin.py: not UTF-8" in out, (rc, out))

    rc, out = _gate(dict(_FIXTURE_FILES, **{"tools/broken.py": "def f(:\n"}))
    check("refuse/syntax-error-cannot-evaluate", rc == 2 and "tools/broken.py: not parseable Python" in out,
          (rc, out))

    # A listed source that cannot be read is cannot-evaluate, never also reported as no longer reading.
    rc, out = _gate(dict(_FIXTURE_FILES, **{"tools/by_key.py": "def f(:\n"}))
    check("refuse/listed-unparseable-not-stale", rc == 2 and "no longer detected" not in out, (rc, out))

    def _no_list(tmp):
        os.unlink(os.path.join(tmp, SWITCH_LIST))
    rc, out = _gate(prepare=_no_list)
    check("refuse/missing-list-cannot-evaluate", rc == 2 and SWITCH_LIST + ": missing" in out, (rc, out))

    for name, text, needle in (
            ("refuse/malformed-list-not-toml", "format-version = 1\n[detect\n", "not valid TOML"),
            ("refuse/malformed-list-not-utf8", b"format-version = 1\n# \xff\n", "not UTF-8"),
            ("refuse/malformed-list-bad-version", _switch_text().replace("format-version = 1",
                                                                         "format-version = true"),
             "format-version"),
            ("refuse/malformed-list-unknown-field", "extra = 1\n" + _switch_text(), "unknown top-level"),
            ("refuse/malformed-list-entry-field", _switch_text().replace('reason = "a fixture reader"',
                                                                         'why = "x"', 1), "must hold exactly"),
            ("refuse/malformed-list-empty-records", _switch_text().replace('records = ["ledger"]',
                                                                           "records = []", 1), "records"),
            ("refuse/malformed-list-detect-missing", _switch_text(detect=_FIXTURE_DETECT.replace(
                'modules = ["ledger_lib"]\n', "")), "[detect] must hold exactly"),
            ("refuse/malformed-list-path-outside", _switch_text() + _entry("../tools/x.py", "switch"),
             "is not a .py or .sh path"),
            ("refuse/deep-list-recursion", "format-version = 1\nx = " + "[" * 100000 + "]" * 100000 + "\n",
             "nested too deeply"),
            ("refuse/duplicate-entry", _switch_text() + _entry("tools/by_name.py", "switch"),
             "duplicate entry for tools/by_name.py"),
            ("refuse/unknown-disposition", _switch_text((("tools/by_name.py", "switch"),
                                                         ("tools/by_key.py", "moved"),
                                                         ("tools/by_flag.py", "fixture-only"),
                                                         ("tools/by_module.py", "switch"),
                                                         ("tools/by_shell.sh", "switch"))),
             "unknown disposition 'moved'"),
            ("refuse/indirect-via-unlisted", _switch_text((("tools/by_name.py", "switch"),
                                                           ("tools/by_key.py", "switch"),
                                                           ("tools/by_flag.py", "fixture-only"),
                                                           ("tools/by_module.py", "indirect-via:tools/x.py"),
                                                           ("tools/by_shell.sh", "switch"))),
             "not another listed entry"),
            # A path that starts under tools/ and ends in .py but climbs out through a .. component.
            ("refuse/malformed-list-path-dotdot", _switch_text() + _entry("tools/../tools/x.py", "switch"),
             "is not a .py or .sh path"),
            ("refuse/indirect-via-cycle", _switch_text((("tools/by_name.py", "switch"),
                                                        ("tools/by_key.py", "switch"),
                                                        ("tools/by_flag.py", "fixture-only"),
                                                        ("tools/by_module.py", "indirect-via:tools/by_shell.sh"),
                                                        ("tools/by_shell.sh", "indirect-via:tools/by_module.py"))),
             "indirect-via cycle tools/by_module.py -> tools/by_shell.sh -> tools/by_module.py")):
        rc, out = _gate(switch=text)
        check(name, rc == 2 and needle in out, (rc, out))

    # Each read form beyond a plain constant, subscript, .get or import, unlisted, exits 1 naming it.
    for case_id, body, evidence in (
            ("detect/unlisted-iterated-key-reader", 'def f(r):\n    for k in ("ledger_path",):\n        r.get(k)\n',
             "registry key 'ledger_path' iterated"),
            ("detect/unlisted-format-name-reader", 'def f(rev):\n    return "{}:ledger.toml".format(rev)\n',
             "name 'ledger.toml'"),
            ("detect/unlisted-import-module-reader", 'import importlib\nM = importlib.import_module("ledger_lib")\n',
             "imports 'ledger_lib'"),
            ("detect/unlisted-dunder-import-reader", 'M = __import__("ledger_lib")\n', "imports 'ledger_lib'"),
            ("detect/unlisted-path-loaded-module",
             'import importlib.util\nS = importlib.util.spec_from_file_location("l", "/x/ledger_lib.py")\n',
             "names module file 'ledger_lib.py'")):
        rc, out = _gate(dict(_FIXTURE_FILES, **{"tools/new_form.py": body}))
        check(case_id, rc == 1 and "tools/new_form.py" in out and evidence in out, (rc, out))

    # The [detect] keys are checked against the registry keys the hook library resolves through _orch_path.
    for case_id, hook, needle in (
            ("refuse/hook-key-missing-from-detect",
             'def a(root, reg):\n    return _orch_path(root, reg.get("ledger_path")) or '
             '_orch_path(root, reg["ledger_extra"])\n', "[detect] keys lacks 'ledger_extra'"),
            ("refuse/hook-loop-key-missing-from-detect",
             'def a(root, rec):\n    for key in ("ledger_path", "ledger_loop"):\n        _orch_path(root, rec.get(key))\n',
             "[detect] keys lacks 'ledger_loop'"),
            ("refuse/hook-or-key-missing-from-detect",
             'def a(root, reg_data):\n    return _orch_path(root, (reg_data or {}).get("ledger_dir"))\n',
             "[detect] keys lacks 'ledger_dir'"),
            ("refuse/hook-library-no-keys", 'def a(root, reg):\n    return reg\n', "no registry key is read"),
            ("refuse/hook-library-unparseable", "def a(:\n", HOOK_LIBRARY + ": not parseable Python")):
        rc, out = _gate(dict(_FIXTURE_FILES, **{HOOK_LIBRARY: hook}))
        check(case_id, rc == 2 and needle in out, (rc, out))
    # Reads that are not registry record keys derive nothing: a receiver whose bindings never reach
    # the registry is ignored inside an _orch_path argument.
    hook = ('def a(root, reg, lease):\n    return _orch_path(root, reg.get("ledger_path")) or '
            '_orch_path(root, lease.get("ledger_other"))\n')
    rc, out = _gate(dict(_FIXTURE_FILES, **{HOOK_LIBRARY: hook}))
    check("ignore/hook-unrelated-reads", rc == 0, (rc, out))

    # The derivation follows same-scope bindings: x = reg.get("K") (plain, or behind the conditional shape
    # the hook library uses for its lease), then x inside an _orch_path argument, derives K.
    keys, crash = _hook_keys('def a(root, reg):\n    box = reg.get("ledger_box")\n'
                             '    return _orch_path(root, box.get("path")) or '
                             '_orch_path(root, reg.get("ledger_path"))\n')
    check("derive/assigned-key", crash is None and keys == ("ledger_box", "ledger_path"), (keys, crash))

    for case_id, hook, needle in (
            ("refuse/hook-assigned-key-missing-from-detect",
             'def a(root, reg):\n    box = reg.get("ledger_box")\n    return _orch_path(root, box.get("path"))\n',
             "[detect] keys lacks 'ledger_box'"),
            ("refuse/hook-assigned-conditional-key-missing-from-detect",
             'def a(root, reg):\n    box = reg.get("ledger_box") if isinstance(reg.get("ledger_box"), dict) '
             'else None\n    if box:\n        return _orch_path(root, box.get("path"))\n',
             "[detect] keys lacks 'ledger_box'"),
            ("refuse/hook-alias-key-missing-from-detect",
             'def a(root, reg):\n    g = reg.get\n    return _orch_path(root, g("ledger_alias"))\n',
             "[detect] keys lacks 'ledger_alias'"),
            ("refuse/hook-keyword-key-missing-from-detect",
             'def a(root, reg):\n    return _orch_path(root, value=reg.get("ledger_kw"))\n',
             "[detect] keys lacks 'ledger_kw'")):
        rc, out = _gate(dict(_FIXTURE_FILES, **{HOOK_LIBRARY: hook}))
        check(case_id, rc == 2 and needle in out, (rc, out))

    # With the assignment-derived key declared, the same hook library passes.
    detect = _FIXTURE_DETECT.replace('keys = ["ledger_path"]', 'keys = ["ledger_path", "ledger_box"]')
    hook = ('def a(root, reg):\n    box = reg.get("ledger_box")\n'
            '    return _orch_path(root, box.get("path")) or _orch_path(root, reg.get("ledger_path"))\n')
    rc, out = _gate(dict(_FIXTURE_FILES, **{HOOK_LIBRARY: hook}), _switch_text(detect=detect))
    check("pass/hook-assigned-key-listed", rc == 0, (rc, out))

    # A registry read reached from an _orch_path argument (directly or through traced same-scope
    # bindings) whose key the scan cannot resolve to a string constant is cannot-evaluate naming a
    # line, never a silent pass; reads the trace does not reach at all (a helper handed the registry,
    # a read in another module) are the documented residual, outside this claim.
    for case_id, hook in (
            ("refuse/hook-unresolvable-key-format",
             'def a(root, reg, n):\n    return _orch_path(root, reg.get("ledger_{}".format(n)))\n'),
            ("refuse/hook-unresolvable-key-fstring",
             'def a(root, reg, n):\n    return _orch_path(root, reg.get(f"ledger_{n}"))\n'),
            ("refuse/hook-unresolvable-key-parameter",
             'def a(root, reg, key):\n    return _orch_path(root, reg.get(key))\n'),
            ("refuse/hook-unresolvable-key-named-collection-loop",
             'KEYS = ("ledger_path",)\ndef a(root, reg):\n    for key in KEYS:\n'
             '        _orch_path(root, reg.get(key))\n'),
            ("refuse/hook-unresolvable-key-tuple-unpacking-loop",
             'def a(root, reg):\n    for key, _tag in (("ledger_path", 1),):\n'
             '        _orch_path(root, reg.get(key))\n'),
            ("refuse/hook-unresolvable-key-mixed-literal-loop",
             'def a(root, reg, extra):\n    for key in ("ledger_path", extra):\n'
             '        _orch_path(root, reg.get(key))\n'),
            ("refuse/hook-unresolvable-key-other-scope-loop",
             'def a(root, rec):\n    return _orch_path(root, rec.get(key))\n'
             'def b():\n    for key in ("ledger_far",):\n        pass\n'),
            ("refuse/hook-unresolvable-key-alias-call",
             'def a(root, reg, key):\n    g = reg.get\n    return _orch_path(root, g(key))\n'),
            ("refuse/hook-assigned-unresolvable-key",
             'def a(root, reg, key):\n    box = reg.get(key)\n    return _orch_path(root, box.get("path"))\n')):
        rc, out = _gate(dict(_FIXTURE_FILES, **{HOOK_LIBRARY: hook}))
        check(case_id, rc == 2 and "registry read at line" in out
              and "reaching an " + HOOK_PATH_RESOLVER + " argument" in out, (rc, out))

    # Every binding form the trace resolves derives a constant key through it: the key, absent from
    # [detect] keys, is reported.
    for case_id, hook, needle in (
            ("derive/annassign-key",
             'def a(root, reg):\n    box: dict = reg.get("ledger_ann")\n'
             '    return _orch_path(root, box.get("path"))\n', "[detect] keys lacks 'ledger_ann'"),
            ("derive/walrus-key",
             'def a(root, reg):\n    if (box := reg.get("ledger_walrus")):\n'
             '        return _orch_path(root, box.get("path"))\n    return None\n',
             "[detect] keys lacks 'ledger_walrus'"),
            ("derive/augassign-key",
             'def a(root, reg):\n    p = ""\n    p += reg.get("ledger_aug")\n'
             '    return _orch_path(root, p)\n', "[detect] keys lacks 'ledger_aug'"),
            ("derive/tuple-first-element-key",
             'def a(root, reg):\n    box, other = reg.get("ledger_pair"), None\n'
             '    return _orch_path(root, box.get("path"))\n', "[detect] keys lacks 'ledger_pair'"),
            ("derive/tuple-second-element-key",
             'def a(root, reg):\n    status, box = "ok", reg.get("ledger_second")\n'
             '    return _orch_path(root, box.get("path"))\n', "[detect] keys lacks 'ledger_second'"),
            ("derive/tuple-whole-value-key",
             'def a(root, reg, make):\n    box, status = make(reg.get("ledger_whole"))\n'
             '    return _orch_path(root, box)\n', "[detect] keys lacks 'ledger_whole'"),
            ("derive/receiver-alias-key",
             'def a(root, reg):\n    r = reg\n    return _orch_path(root, r.get("ledger_recv"))\n',
             "[detect] keys lacks 'ledger_recv'"),
            ("derive/getattr-alias-key",
             'def a(root, reg):\n    g = getattr(reg, "get")\n'
             '    return _orch_path(root, g("ledger_getattr"))\n',
             "[detect] keys lacks 'ledger_getattr'"),
            ("derive/annassign-alias-key",
             'def a(root, reg):\n    g: object = reg.get\n'
             '    return _orch_path(root, g("ledger_annalias"))\n',
             "[detect] keys lacks 'ledger_annalias'"),
            ("derive/for-target-value-key",
             'def a(root, reg):\n    for p in reg.get("ledger_each"):\n'
             '        _orch_path(root, p)\n', "[detect] keys lacks 'ledger_each'"),
            ("derive/with-target-key",
             'def a(root, reg):\n    with reg.get("ledger_ctx") as c:\n'
             '        _orch_path(root, c)\n', "[detect] keys lacks 'ledger_ctx'"),
            ("derive/lambda-scope-key",
             'def a(root, reg):\n    f = lambda r=reg: _orch_path(root, r.get("ledger_lam"))\n'
             '    return f()\n', "[detect] keys lacks 'ledger_lam'")):
        rc, out = _gate(dict(_FIXTURE_FILES, **{HOOK_LIBRARY: hook}))
        check(case_id, rc == 2 and needle in out, (rc, out))

    # The multi-step chain the hook library itself holds (registry read, then two object reads, then
    # the resolver) derives exactly the registry key at the head of the chain.
    keys, crash = _hook_keys(
        'def a(root, reg):\n'
        '    lease = reg.get("ledger_lease") if isinstance(reg.get("ledger_lease"), dict) else None\n'
        '    block = lease.get("heartbeat")\n'
        '    lock_path = block.get("lock_path")\n'
        '    return _orch_path(root, lock_path)\n')
    check("derive/multi-step-chain-key", crash is None and keys == ("ledger_lease",), (keys, crash))

    # The same binding forms with a key the scan cannot pin are cannot-evaluate, never a silent pass;
    # so are the shapes the trace refuses to follow (a lambda or nested def over the registry, a
    # closure or global binding from another scope, a registry attribute other than .get, and a loop
    # variable rebound in its scope, whose literal would be stale).
    for case_id, hook in (
            ("refuse/annassign-unresolvable-key",
             'def a(root, reg, k):\n    box: dict = reg.get(k)\n'
             '    return _orch_path(root, box.get("path"))\n'),
            ("refuse/walrus-unresolvable-key",
             'def a(root, reg, k):\n    if (box := reg.get(k)):\n'
             '        return _orch_path(root, box.get("path"))\n    return None\n'),
            ("refuse/augassign-unresolvable-key",
             'def a(root, reg, k):\n    p = ""\n    p += reg.get(k)\n'
             '    return _orch_path(root, p)\n'),
            ("refuse/tuple-target-unresolvable-key",
             'def a(root, reg, k):\n    status, box = "ok", reg.get(k)\n'
             '    return _orch_path(root, box)\n'),
            ("refuse/receiver-alias-unresolvable-key",
             'def a(root, reg, k):\n    r = reg\n    return _orch_path(root, r.get(k))\n'),
            ("refuse/getattr-alias-unresolvable-key",
             'def a(root, reg, k):\n    g = getattr(reg, "get")\n    return _orch_path(root, g(k))\n'),
            ("refuse/rebound-loop-variable-key",
             'def a(root, reg, other):\n    for key in ("ledger_path",):\n        key = other\n'
             '        _orch_path(root, reg.get(key))\n'),
            ("refuse/lambda-default-registry",
             'def a(root, reg):\n    f = lambda k, r=reg: r.get(k)\n'
             '    return _orch_path(root, f("ledger_hidden"))\n'),
            ("refuse/lambda-returns-registry",
             'def a(root, reg, k):\n    f = lambda: reg\n'
             '    return _orch_path(root, f().get(k))\n'),
            ("refuse/nested-def-getter",
             'def a(root, reg):\n    def getter():\n        return reg.get("ledger_nested")\n'
             '    return _orch_path(root, getter())\n'),
            ("refuse/getattr-non-get-attribute",
             'def a(root, reg):\n    v = getattr(reg, "values")\n    return _orch_path(root, v)\n'),
            ("refuse/closure-assigned-name",
             'def a(root, reg):\n    box = reg.get("ledger_far")\n    def inner():\n'
             '        return _orch_path(root, box.get("path"))\n    return inner\n'),
            ("refuse/global-bound-registry-read",
             'box = None\ndef setup(reg):\n    global box\n    box = reg.get("ledger_glob")\n'
             'def a(root):\n    return _orch_path(root, box)\n')):
        rc, out = _gate(dict(_FIXTURE_FILES, **{HOOK_LIBRARY: hook}))
        check(case_id, rc == 2 and "registry read at line" in out
              and "reaching an " + HOOK_PATH_RESOLVER + " argument" in out, (rc, out))

    # Bounded refusals: an enclosing-scope binding with no registry in it resolves to nothing, and a
    # helper handed the registry itself is the documented residual, not a refusal (the shape the
    # live library uses for its validated-configuration tuple).
    hook = ('TAIL = "ledger-side.txt"\ndef a(root, reg):\n'
            '    return _orch_path(root, TAIL) or _orch_path(root, reg.get("ledger_path"))\n')
    rc, out = _gate(dict(_FIXTURE_FILES, **{HOOK_LIBRARY: hook}))
    check("ignore/enclosing-plain-constant", rc == 0, (rc, out))
    hook = ('def helper(reg):\n    return "ok", {}\n'
            'def a(root, reg):\n    status, cfg = helper(reg)\n'
            '    return _orch_path(root, cfg.get("lock_path")) or '
            '_orch_path(root, reg.get("ledger_path"))\n')
    rc, out = _gate(dict(_FIXTURE_FILES, **{HOOK_LIBRARY: hook}))
    check("ignore/helper-call-residual", rc == 0, (rc, out))

    def _no_hook(tmp):
        os.unlink(os.path.join(tmp, HOOK_LIBRARY))
    rc, out = _gate(prepare=_no_hook)
    check("refuse/hook-library-missing", rc == 2 and HOOK_LIBRARY + ": missing" in out, (rc, out))

    def _linked_dir(tmp):
        os.symlink(os.path.join("..", "opf", "tools"), os.path.join(tmp, "tools", "linked"))
    rc, out = _gate(prepare=_linked_dir)
    check("refuse/symlinked-directory-cannot-evaluate",
          rc == 2 and "tools/linked: a symbolic link to a directory" in out, (rc, out))

    import shutil
    import unittest.mock

    def _locked_dir(tmp):
        os.makedirs(os.path.join(tmp, "tools", "locked"))
        os.chmod(os.path.join(tmp, "tools", "locked"), 0)
    if os.geteuid() == 0:
        real_scandir = os.scandir

        def _refuse_locked(path="."):
            if str(path).endswith(os.sep + "locked"):
                raise PermissionError(13, "listing refused by the self-test", str(path))
            return real_scandir(path)
        with unittest.mock.patch.object(os, "scandir", _refuse_locked):
            rc, out = _gate(prepare=_locked_dir)
    else:
        rc, out = _gate(prepare=_locked_dir)
    check("refuse/unlistable-directory-cannot-evaluate", rc == 2 and "cannot list a scanned directory" in out,
          (rc, out))

    def _no_scan_root(tmp):
        shutil.rmtree(os.path.join(tmp, "tools"))
    rc, out = _gate(prepare=_no_scan_root)
    check("refuse/missing-scan-root-cannot-evaluate", rc == 2 and "tools: scan root missing" in out, (rc, out))

    # MAX_BYTES, at each side of the bound: one byte over is refused; exactly at it and one under are read.
    for case_id, size, want in (("refuse/source-over-max-bytes", MAX_BYTES + 1, 2),
                                ("pass/source-at-max-bytes", MAX_BYTES, 0),
                                ("pass/source-under-max-bytes", MAX_BYTES - 1, 0)):
        rc, out = _gate(dict(_FIXTURE_FILES, **{"tools/big.py": b"#" + b"x" * (size - 2) + b"\n"}))
        over = "tools/big.py: larger than {} bytes".format(MAX_BYTES) in out
        check(case_id, rc == want and over == (want == 2), (rc, size, out[-300:]))

    # Shell words are whole: a declared name with a word character before it, or a flag with one after it,
    # is not a match.
    rc, out = _gate(dict(_FIXTURE_FILES, **{"tools/near.sh": "#!/bin/bash\ncat myledger.toml\nrun --ledgers\n"}))
    check("ignore/shell-word-boundaries", rc == 0 and "near.sh" not in out, (rc, out))

    # The TOML parse site handles each failure tomllib can raise, by class; a handler that is missing fails
    # the row by name, never by an uncaught exception.
    for exc_class, case_id in ((RecursionError, "parse-site/handles-recursionerror"),
                               (MemoryError, "parse-site/handles-memoryerror"),
                               (ValueError, "parse-site/handles-valueerror")):
        with unittest.mock.patch.object(tomllib, "loads", side_effect=exc_class("raised by the self-test")):
            result, crash = _guarded(_parse_switch_text, "x = 1\n")
        data, error = result if result is not None else (None, None)
        check(case_id, crash is None and data is None and bool(error), (result, crash))

    got = [_guarded(_main_exit, argv) for argv in (["--bogus"], ["--self-test", "--bogus"],
                                                   ["--execution-report", "relative/report"])]
    check("refuse/unrecognized-argument", got == [(2, None)] * 3, got)

    rc, crash = _guarded(run_gate, ROOT)
    check("live/real-tree-passes", crash is None and rc == 0, (rc, crash))

    if FAILURES:
        print("SELF-TEST FAIL: check_record_readers {} of {} check(s) failed".format(len(FAILURES), len(EXECUTED)))
        for failure in FAILURES:
            print("  - " + failure)
        return 1
    print("SELF-TEST PASS: check_record_readers {} checks (a clean fixture passes; an unlisted reader found by "
          "each of the name, key, flag, module and shell rules exits 1, and passes with that rule removed; an "
          "iterated key, a formatted name, import_module, __import__ and a module file path are each detected; "
          "a listed file that is gone or no longer reads exits 1; _vendor, near-miss strings and near-miss "
          "shell words are ignored; a FIFO, unreadable, symlinked, oversized, non-UTF-8 or unparseable source, "
          "a symlinked or unlistable directory, a missing scan root, and a missing, malformed, too deeply "
          "nested, duplicated, unknown-disposition or indirect-via-cycle list exit 2; [detect] keys lacking a "
          "key the hook library resolves inline, through one assignment step, through a .get alias or by "
          "keyword, or a missing, unparseable or keyless hook library, exits 2; a registry read reaching an "
          "_orch_path argument whose key resolves to no string constant (a formatted or f-string key, a "
          "parameter, a named-collection, tuple-unpacking, mixed-literal or other-scope loop, an alias call, "
          "or an unresolved assignment) exits 2 naming its line; every traced binding form (annotated, "
          "augmented, walrus and tuple assignments, for and with targets, a lambda scope, a receiver or "
          "getattr .get alias, and a multi-step chain) derives a constant key and refuses an unpinned "
          "one; a rebound loop variable, a lambda or nested def over the registry, a closure or global "
          "binding from another scope and a non-get registry attribute each refuse; an enclosing plain "
          "constant and a helper handed the registry pass; the parse site handles RecursionError, "
          "MemoryError and ValueError; the live tree passes)".format(len(EXECUTED)))
    return 0


def _main_exit(argv):
    """main()'s exit code for the argument list argv, its output captured."""
    import contextlib
    import io
    saved = sys.argv
    sys.argv = [saved[0]] + list(argv)
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return main()
    finally:
        sys.argv = saved


def main():
    argv = sys.argv[1:]
    # The execution gate appends `--execution-report ABS_PATH` to the self-test launch; accepted only in that
    # trailing two-token form, with an absolute path, alone or after --self-test.
    report_path = None
    if len(argv) >= 2 and argv[-2] == "--execution-report" and os.path.isabs(argv[-1]) \
            and argv[:-2] in ([], ["--self-test"]):
        report_path, argv = argv[-1], ["--self-test"]
    if argv not in ([], ["--self-test"]):
        print("error: unrecognized arguments {!r}; fail closed".format(argv), file=sys.stderr)
        return 2
    if argv == ["--self-test"]:
        if report_path is not None:
            # Finalized at interpreter exit by tools/_selftest_exit_report.py; nothing writes it in band.
            sys.path.append(str(Path(__file__).resolve().parent))
            import _selftest_exit_report
            _selftest_exit_report.arm(report_path, SUITE_ID, EXECUTED)
        return _self_test()
    return run_gate(ROOT)


if __name__ == "__main__":
    _code = main()
    _finalizer = sys.modules.get("_selftest_exit_report")
    (sys.exit if _finalizer is None else _finalizer.exit_with)(_code)
