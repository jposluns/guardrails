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
The key vocabulary is checked against its authoritative source: the hook library
(.aiqt/core/hooks/scripts/aiqt_hooks.py), scanned whole (every scope: the module, class bodies,
functions, lambdas, comprehensions and decorators). Every Load-context occurrence of a registry
receiver name (reg, reg_data, rec) must be one of a closed set of accepted access forms, each yielding
its key: R.get(K), R.get(K, default), R[K], (R or {}).get(K) or (R or {})[K], with K a string constant
or a loop variable over a literal tuple, list or set of string constants whose loop target binds in
exactly the scope of the read and that nothing rebinds, including a nested scope declaring it nonlocal
or, for a module-level read, any function declaring it global. Scopes follow run-time evaluation: a
comprehension is its own scope but its first (outermost) iterable belongs to the enclosing scope, a
def's decorators, parameter defaults and annotations and a lambda's defaults belong to the enclosing
scope, and a class body is its own scope, so a pin never crosses into a scope whose run-time binding
it does not govern. The other accepted form is the registry name handed, positionally or by keyword,
by a call that holds no starred or double-starred argument, to a callee that is an undecorated
module-level def of the hook library whose matching parameter is itself a registry name (the hand-off
yields no key; its reads are derived where that parameter is read; any other callee, a decorated def,
a non-registry parameter name, *args or **kwargs, a call holding a starred or double-starred argument,
or a callee that cannot be resolved is refused naming its line). A registry use inside a
type-parameter list or a type-alias statement is refused (both evaluate lazily, so no binding there is
certain). Every pin and hand-off is voided in a module where the scan reads any of a fixed list of
shapes: a bare-name or attribute occurrence, calls and read-only mentions alike, of exec, eval,
compile, setattr, delattr, __import__ or import_module (an alias assignment such as e = exec and a
spelling such as builtins.exec or importlib.import_module are both read); such an occurrence of
globals, locals, vars, modules (sys.modules under any alias), _getframe, f_locals, f_globals,
__dict__, __globals__ or __builtins__; a from-import that imports or binds any of those names, under
its own name or an alias (from builtins import exec as e, from sys import modules as mm); a call of
getattr or __getattribute__ whose attribute-name argument is a string literal naming one of those
callables or namespaces or naming getattr or __getattribute__ itself, or is not a string literal at
all; a star import anywhere; and a store or delete to an attribute whose chain
root is an import-bound name (a module object's namespace). Exactly two forms stop voiding, because
neither can rebind a name or run code in this module: (a) an attribute named compile whose object is
a name bound, at module level and only there, by `import re` (or `import re as <alias>`) and never
rebound anywhere in the module (re.compile builds a regular expression object; it cannot rebind a
name); (b) a call __import__(<one string literal>) or importlib.import_module(<one string literal>)
whose literal names a module in sys.stdlib_module_names, with no other arguments, import_module
reached through a name bound, at module level and only there, by `import importlib` (or an alias of
it) and never rebound (importing a literal standard-library module by name returns a module object
and rebinds nothing in this module's namespace; its result cannot be star-imported, and a star
import anywhere still voids on its own); form (b) accepts __import__ only when neither the name
__import__ nor the name __builtins__ has a binding occurrence anywhere in the module (an
assignment, a deletion, a parameter, a def or class name, an import alias, an except, global,
nonlocal or match binding, or a type-parameter name), so only the builtin can be the callee: a
bound __import__ need not be the builtin, and a bound __builtins__ is the mapping a bare-name
__import__ resolves through, and the module can bind it with no bare-name or attribute occurrence
the scan reads (import shim as __builtins__, a def, class, parameter, except, match or
type-parameter binding of that name), while a stored or deleted bare-name __builtins__ is already
a voiding occurrence of its own. Every
other form voids as before: a bare compile, compile reached through any other object or through a
rebound alias, an uncalled __import__ or import_module, a __import__ or __builtins__ the module
binds, a non-literal or non-stdlib argument, and any extra argument. A
voided module keeps only its constant-key reads and its allowlisted uses. Independently of module voiding, a literal-loop pin or a
hand-off written in a class body whose class has any base, keyword or decorator is refused: the
metaclass's __prepare__ mapping can resolve any name in that body, so no binding there is certain,
while a constant-key read stays accepted.
The derived key set is the union over all accepted reads, plus the
keys-read each [[detect.registry-use]] entry asserts. Every other occurrence (a non-constant key, any
other method or attribute, the receiver passed otherwise as a call argument, assigned, returned,
yielded, unpacked, iterated, compared, formatted or used in any other expression) is a refused use,
keyed by occurrence (line and column) and cannot-evaluate naming both, unless a
[[detect.registry-use]] entry in the switch list excuses it: its line-anchor, one or more complete
consecutive source lines, must match exactly one place in the hook library, must cover at least one
refused use, and carries a reason, the keys-read the author asserts, and uses, the exact source text
of each excused use on the anchored lines, so an extra or changed refused use there fails closed
naming its line and column. Each derived key must be in [detect] keys or, when it names no record
path, in [detect.non-record-keys] with a reason; a derived key in neither list, a key in both lists,
an excluded key no read derives, an anchor matching zero or several places, a stale allowlist entry
or use declaration, a hook library that cannot be read or parsed, or one that yields no key, is
cannot-evaluate.
What the scan cannot see: a name, key or module file name built at run time from parts ("changelog" +
".toml", "aiqt_hooks" + ".py"), a record name in the middle of a longer string that is not a path ending, a
key held in a named collection before a loop iterates it, a module loaded through a variable name, a path
given only on a command line whose flag is not declared, a .sh file that runs a record-reading module, paths
kept in the untracked orchestration registry, readers outside tools/, and a read that never names a file,
key, flag or module. The residual of the key derivation: a registry object reached under a name not in
HOOK_REGISTRY_NAMES is not seen, a key used outside the hook library is not seen, an allowlisted use
is covered only by its asserted keys-read, and any other run-time route that reaches the module
namespace or a voiding callable without a bare-name, attribute, from-import or literal-accessor
occurrence the scan reads is not seen (a getattr bound to another name, an unnamed functools.partial
of getattr, operator.attrgetter, gc.get_referents, a deserializer such as pickle that runs code, or
a rebinding performed by code outside the hook library); the live hook library is clean today by this
gate's own run.
A self-test that writes record fixtures is detected like a reader; the
fixture-only disposition records it. A tool that imports a declared module for some other part of it is
detected too; the not-a-reader disposition records it, with the reason.

Each [[reader]] entry carries path, records (a non-empty list of names), disposition and reason. The
disposition is one of switch, stays-outside-store, fixture-only, not-a-reader, or indirect-via:<path>,
where <path> is another listed entry. Exit 1 (finding): a detected reader that is not listed; a listed file that is gone;
a listed file the scan no longer detects. Exit 2 (cannot evaluate): a missing, unreadable, non-UTF-8,
malformed, too deeply nested or schema-invalid switch list, a duplicate entry, an unknown disposition, an
indirect-via target that is not listed or an indirect-via cycle; a registry use in the hook library outside
the accepted access forms with no registry-use entry anchoring its line; a derived registry key in neither
[detect] keys nor [detect.non-record-keys]; a malformed, ambiguous or stale registry-use or non-record-keys
entry; a missing scan root, a symbolic link to a directory or a directory that cannot be
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
DETECT_OPTIONAL = ("non-record-keys", "registry-use")
REGISTRY_USE_FIELDS = frozenset({"line-anchor", "reason", "keys-read", "uses"})
# The authoritative source of registry keys: the hook library is scanned whole (every scope, class
# bodies, decorators, lambdas and comprehensions included), and every Load-context occurrence of a
# registry receiver name must be an accepted access form, each yielding its key: R.get(K),
# R.get(K, default), R[K], (R or {}).get(K) or (R or {})[K], with K a string constant or a loop
# variable over a literal collection of string constants whose loop target binds in exactly the scope
# of the read and that nothing rebinds, a nested scope's nonlocal and a module-read's global included
# (scopes follow run-time evaluation: a comprehension's first iterable, a def's decorators, defaults
# and annotations and a lambda's defaults belong to the enclosing scope, and a class body is its own
# scope); or the registry name handed, by a call with no starred or double-starred argument, to an
# undecorated module-level def of the hook library whose matching parameter is itself a registry name
# (any other callee, a decorated def, another parameter name, *args, **kwargs, a starred call or an
# unresolvable callee is refused). A use inside a type-parameter list or type-alias statement is
# refused (lazy evaluation). Every pin and hand-off is voided in a module where the scan reads any of
# a fixed list of shapes: a bare-name or attribute occurrence (read-only included) of exec, eval,
# compile, setattr, delattr, __import__ or import_module, such an occurrence of globals, locals,
# vars, modules, _getframe, f_locals, f_globals, __dict__, __globals__ or __builtins__, a from-import
# that imports or binds any of those names under its own name or an alias, a call of
# getattr or __getattribute__ whose attribute-name argument is a string literal naming one of those
# callables or namespaces or naming getattr or __getattribute__ itself, or is not a string literal at
# all, a star import anywhere, or a store or
# delete to an attribute rooted in an import-bound name, excepting exactly two forms that can neither
# rebind a name nor run code here: (a) an attribute named compile whose object is a name bound, at
# module level and only there, by `import re` (or `import re as <alias>`) and never rebound anywhere
# in the module, and (b) a call __import__(<one string literal>) or
# importlib.import_module(<one string literal>) whose literal names a module in
# sys.stdlib_module_names, with no other arguments, import_module reached through a module-level,
# never rebound import of importlib, __import__ only when neither __import__ nor __builtins__ has
# a binding occurrence anywhere in the module; a pin or hand-off
# in the body of a class with
# any base, keyword or decorator is refused too (its metaclass's __prepare__ mapping can resolve any
# name there), while constant-key reads stay accepted. Any other occurrence is a refused use, keyed by
# line and column, and cannot-evaluate unless a [[detect.registry-use]] allowlist entry excuses it (an
# anchor must match exactly one place, cover at least one refused use, and declare each excused use's
# exact source text in uses, so an extra or changed use on an anchored line fails closed; its
# keys-read join the derived set). A derived key must be in [detect] keys or excluded in
# [detect.non-record-keys] with a reason. The residual: a registry object reached under a name not in
# HOOK_REGISTRY_NAMES is not seen, a key used outside the hook library is not seen, an allowlisted
# use is covered only by its asserted keys-read, and any other run-time route that reaches the module
# namespace or a voiding callable without a bare-name, attribute, from-import or literal-accessor
# occurrence the scan reads is not seen (a getattr bound to another name, an unnamed
# functools.partial of getattr, operator.attrgetter, gc.get_referents, a deserializer such as pickle
# that runs code, or a rebinding performed by code outside the hook library); the live hook library
# is clean today by this gate's own run.
HOOK_LIBRARY = ".aiqt/core/hooks/scripts/aiqt_hooks.py"
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
    if not isinstance(block, dict) or set(block) - set(DETECT_OPTIONAL) != set(DETECT_FIELDS):
        raise CannotEvaluate("{}: [detect] must hold exactly {} (plus optional {})".format(
            SWITCH_LIST, ", ".join(DETECT_FIELDS), " and ".join(DETECT_OPTIONAL)))
    detect = {field: _string_list(block[field], "[detect] " + field) for field in DETECT_FIELDS}
    excluded = block.get("non-record-keys", {})
    if not isinstance(excluded, dict) or not all(
            isinstance(k, str) and k and isinstance(v, str) and v.strip() for k, v in excluded.items()):
        raise CannotEvaluate("{}: [detect.non-record-keys] must map each excluded key to a non-empty "
                             "reason".format(SWITCH_LIST))
    detect["non-record-keys"] = dict(excluded)
    raw_uses = block.get("registry-use", [])
    if not isinstance(raw_uses, list):
        raise CannotEvaluate("{}: [[detect.registry-use]] must be an array of tables".format(SWITCH_LIST))
    uses = []
    for number, entry in enumerate(raw_uses, 1):
        where = "{} [[detect.registry-use]] #{}".format(SWITCH_LIST, number)
        if not isinstance(entry, dict) or set(entry) != REGISTRY_USE_FIELDS:
            raise CannotEvaluate("{}: must hold exactly {}".format(
                where, ", ".join(sorted(REGISTRY_USE_FIELDS))))
        anchor, reason, keys_read = entry["line-anchor"], entry["reason"], entry["keys-read"]
        declared_uses = entry["uses"]
        if not isinstance(anchor, str) or not anchor.strip():
            raise CannotEvaluate("{}: line-anchor must be a non-empty string".format(where))
        if not isinstance(reason, str) or not reason.strip():
            raise CannotEvaluate("{}: reason must be a non-empty string".format(where))
        if not isinstance(keys_read, list) or not all(isinstance(k, str) and k for k in keys_read) \
                or len(set(keys_read)) != len(keys_read):
            raise CannotEvaluate("{}: keys-read must be a list of distinct non-empty strings".format(where))
        if not isinstance(declared_uses, list) or not declared_uses \
                or not all(isinstance(u, str) and u for u in declared_uses):
            raise CannotEvaluate("{}: uses must be a non-empty list of non-empty strings, the exact "
                                 "source text of each excused use".format(where))
        uses.append((anchor, tuple(keys_read), tuple(declared_uses)))
    detect["registry-use"] = tuple(uses)
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


def _parse_python_text(path, rel):
    """(tree, text): the parsed module at path and its decoded source, or CannotEvaluate naming rel when
    it cannot be read, decoded or parsed."""
    raw = _read_regular(path, rel)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CannotEvaluate("{}: not UTF-8 ({})".format(rel, exc)) from None
    try:
        return ast.parse(text, filename=rel), text
    except (SyntaxError, ValueError) as exc:
        raise CannotEvaluate("{}: not parseable Python ({})".format(rel, exc)) from None
    except RecursionError:
        raise CannotEvaluate("{}: nested too deeply to parse".format(rel)) from None


def _parse_python(path, rel):
    """The parsed module at path, or CannotEvaluate naming rel when it cannot be read, decoded or parsed."""
    return _parse_python_text(path, rel)[0]


# The nodes that open a new binding scope. A comprehension is its own scope: its targets resolve
# keys only for reads inside it, never for a read in the enclosing scope that at run time sees an
# outer binding of the same name. A class body is its own scope too: a name it binds is a class
# attribute, never a binding of the enclosing function or module.
_SCOPE_NODES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda,
                ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp, ast.ClassDef)
_COMP_NODES = (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)


def _comp_first_iter(node):
    """The first (outermost) iterable of a comprehension, the one part of it Python evaluates in the
    enclosing scope rather than inside the comprehension; None for any other node."""
    if isinstance(node, _COMP_NODES):
        return node.generators[0].iter
    return None


def _all_args(args):
    """Every ast.arg of one ast.arguments node, positional-only, positional, keyword-only, *args and
    **kwargs included."""
    extras = [a for a in (args.vararg, args.kwarg) if a is not None]
    return list(args.posonlyargs) + list(args.args) + list(args.kwonlyargs) + extras


def _outer_parts(node):
    """The parts of one scope-opening node that resolve against the ENCLOSING scope: a def's
    decorators and parameter defaults, a lambda's parameter defaults, a class's decorators, bases and
    keyword arguments, and a comprehension's first (outermost) iterable are evaluated there at the
    point where the node appears; a def's parameter and return annotations are compiled against that
    scope but evaluated only when __annotations__ is accessed, so they are routed here, to the scope
    whose bindings they will then see. A def's or class's type-parameter list is routed here only so
    every node gets an owner; its registry uses are refused outright (they evaluate lazily)."""
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        parts = list(node.decorator_list) + list(node.type_params) + list(node.args.defaults)
        parts += [d for d in node.args.kw_defaults if d is not None]
        parts += [a.annotation for a in _all_args(node.args) if a.annotation is not None]
        if node.returns is not None:
            parts.append(node.returns)
        return parts
    if isinstance(node, ast.Lambda):
        return list(node.args.defaults) + [d for d in node.args.kw_defaults if d is not None]
    if isinstance(node, ast.ClassDef):
        return (list(node.decorator_list) + list(node.type_params) + list(node.bases)
                + [k.value for k in node.keywords])
    return [_comp_first_iter(node)]


def _inner_parts(node):
    """The parts of one scope-opening node that belong to ITS OWN scope: a def's or lambda's body and
    parameter names, a class's body, and a comprehension's element (or key and value), targets,
    conditions and every iterable after the first."""
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return list(node.body) + _all_args(node.args)
    if isinstance(node, ast.Lambda):
        return [node.body] + _all_args(node.args)
    if isinstance(node, ast.ClassDef):
        return list(node.body)
    inner = list(node.generators)
    if isinstance(node, ast.DictComp):
        return inner + [node.key, node.value]
    return inner + [node.elt]


def _scope_walk(scope):
    """Every node Python evaluates in one scope (the module, a def, an async def, a lambda, a
    comprehension or a class body), following run-time evaluation: a nested scope-opening node is
    yielded here (so nested rebinding analysis sees it) together with the parts of it the enclosing
    scope evaluates (a def's decorators, defaults and annotations, a lambda's defaults, a class's
    decorators, bases and keyword arguments, and a comprehension's first iterable), while its own
    parts (a body, parameter names, a comprehension's targets, conditions and later iterables) are
    walked only by its own scope's walk. An ast.arg is yielded without descent: its annotation is
    routed to the scope that evaluates it."""
    first_iter = _comp_first_iter(scope)
    stack = _inner_parts(scope) if isinstance(scope, _SCOPE_NODES) else list(ast.iter_child_nodes(scope))
    while stack:
        node = stack.pop()
        yield node
        if isinstance(node, _SCOPE_NODES):
            stack.extend(_outer_parts(node))
        elif not isinstance(node, ast.arg):
            stack.extend(child for child in ast.iter_child_nodes(node) if child is not first_iter)


def _nested_rebinders(node, at_module):
    """The names a scope nested under the current one can rebind in the current scope: every name a
    nested function or lambda (at any depth) declares nonlocal, every name one declares global when
    the current scope is the module, and, for a nested comprehension, every name it binds in any way
    (a walrus target inside a comprehension binds in the enclosing scope, so every internal binding
    counts, fail closed)."""
    names = set()
    comprehension = isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp))
    for inner in ast.walk(node):
        if isinstance(inner, ast.Nonlocal) or (isinstance(inner, ast.Global) and at_module):
            names.update(inner.names)
        elif comprehension and isinstance(inner, ast.Name) and isinstance(inner.ctx, (ast.Store, ast.Del)):
            names.add(inner.id)
        elif comprehension and isinstance(inner, ast.arg):
            names.add(inner.arg)
    return names


_TYPE_PARAM_NODES = (ast.TypeVar, ast.ParamSpec, ast.TypeVarTuple)


def _type_param_binds(node):
    """The name one type-parameter node binds. A def's type parameter shadows a module-level name of
    the same spelling inside the def it parameterizes, so it counts as a binding wherever bindings are
    counted, fail closed: def a[_take](...) must never resolve its _take call to the module-level
    hand-off callee."""
    return (node.name,) if isinstance(node, _TYPE_PARAM_NODES) else ()


def _loop_key_strings(scope):
    """name -> the frozenset of strings a plain for or async-for target over a literal tuple, list or
    set of string constants can hold within scope (the module, one def, one lambda or one
    comprehension; a comprehension's own generator targets pin keys only for reads inside it). A name
    any other construct also binds (an assignment, a parameter, a non-literal or mixed-element loop,
    an import, a def or class name, an except, match or global/nonlocal binding), a name a nested
    comprehension binds in any way, a name any nested scope declares nonlocal, or a name any function
    declares global when scope is the module, is absent and never resolves a key."""
    nodes = list(_scope_walk(scope))
    loop_targets = set()
    strings = {}
    for node in nodes:
        if (isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension))
                and isinstance(node.target, ast.Name)
                and isinstance(node.iter, (ast.Tuple, ast.List, ast.Set)) and node.iter.elts
                and all(isinstance(e, ast.Constant) and isinstance(e.value, str)
                        for e in node.iter.elts)):
            loop_targets.add(id(node.target))
            strings.setdefault(node.target.id, set()).update(e.value for e in node.iter.elts)
    bound_otherwise = set()
    for node in nodes:
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            if id(node) not in loop_targets:
                bound_otherwise.add(node.id)
        elif isinstance(node, ast.arg):
            bound_otherwise.add(node.arg)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound_otherwise.add(node.name)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            bound_otherwise.update(a.asname or a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            bound_otherwise.add(node.name)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            bound_otherwise.update(node.names)
        elif isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name:
            bound_otherwise.add(node.name)
        elif isinstance(node, ast.MatchMapping) and node.rest:
            bound_otherwise.add(node.rest)
        elif isinstance(node, _TYPE_PARAM_NODES):
            bound_otherwise.update(_type_param_binds(node))
        if isinstance(node, _SCOPE_NODES):
            bound_otherwise |= _nested_rebinders(node, isinstance(scope, ast.Module))
    return {name: frozenset(value) for name, value in strings.items() if name not in bound_otherwise}


def _module_helpers(tree):
    """name -> the arguments of the undecorated module-level def with that name, for names the whole
    module binds exactly once: a name also bound anywhere else (another def, an assignment, an import,
    a class, a parameter, a type-parameter name, a nested rebinding) cannot be resolved to one
    signature and is absent, and a decorated def is absent too (the name is bound to the decorator's
    result, whose parameters are not the def's), so a registry name handed to a call through either
    fails closed."""
    counts = {}
    for node in ast.walk(tree):
        names = ()
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            names = (node.id,)
        elif isinstance(node, ast.arg):
            names = (node.arg,)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names = (node.name,)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            names = tuple(a.asname or a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            names = (node.name,)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            names = tuple(node.names)
        elif isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name:
            names = (node.name,)
        elif isinstance(node, ast.MatchMapping) and node.rest:
            names = (node.rest,)
        elif isinstance(node, _TYPE_PARAM_NODES):
            names = _type_param_binds(node)
        for name in names:
            counts[name] = counts.get(name, 0) + 1
    return {node.name: node.args for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and not node.decorator_list and counts.get(node.name) == 1}


# Callables that can rebind a name or load code: a module reaching any of them, by any spelling, can
# rebind any of its names at run time.
_VOID_CALLABLES = frozenset({"exec", "eval", "compile", "setattr", "delattr",
                             "__import__", "import_module"})
# Namespace objects through which any name of the module can be rebound; a read-only mention already
# places the namespace in the module's hands, so every bare-name or attribute occurrence the scan
# reads counts, calls and read-only mentions alike. __globals__ is
# a function's module namespace reached as an attribute; __builtins__ is the mapping the module's
# builtin lookups (the builtin __import__ included) resolve through.
_VOID_NAMESPACES = frozenset({"globals", "locals", "vars", "modules",
                              "_getframe", "f_locals", "f_globals", "__dict__",
                              "__globals__", "__builtins__"})
_VOID_NAMES = _VOID_CALLABLES | _VOID_NAMESPACES
# Computed-attribute accessors: a call of getattr or __getattribute__ resolves an attribute by a
# run-time string, so the call voids unless its attribute-name argument is a string literal outside
# _VOID_ATTR_LITERALS (getattr(builtins, "exec") reaches a voiding callable by literal; a
# non-literal name can be built to reach one).
_ATTR_ACCESSORS = frozenset({"getattr", "__getattribute__"})
# The attribute-name literals a getattr or __getattribute__ call must not name: the voiding
# callables and namespaces, and the two accessors themselves (getattr(o, "getattr") returns the same
# accessor bound to no name this scan spells, so a later call of it would not be seen; refusing the
# literal closes that route where it is built).
_VOID_ATTR_LITERALS = _VOID_NAMES | _ATTR_ACCESSORS
# The names whose binding anywhere in the module stops the __import__ voiding exception: a bound
# __import__ need not be the builtin, and a bound __builtins__ is the mapping a bare-name
# __import__ resolves through, and the module can bind it with no bare-name or attribute
# occurrence the scan reads (an import alias, a def or class name, a parameter, an except, match
# or type-parameter binding). A stored or deleted bare name is also such a binding, but that
# occurrence already voids on its own.
_DUNDER_IMPORT_EXCEPTION_BLOCKERS = frozenset({"__import__", "__builtins__"})


def _from_import_void_names(alias):
    """The voiding names one from-import alias touches: the imported name and the name it binds,
    each when it is in _VOID_NAMES. `from builtins import exec as e` pulls the voiding callable out
    of its module and binds it to a name no later occurrence spells, so the import itself voids, as
    `import builtins` followed by builtins.exec already does; `from x import y as exec` rebinds the
    name exec. A from-import that binds getattr or __getattribute__ under another name stays part
    of the residual: the accessor-call check matches those two spellings only."""
    names = (alias.name, alias.asname or alias.name)
    return tuple(dict.fromkeys(n for n in names if n in _VOID_NAMES))


# Each binding construct _bound_anywhere counts, one branch per construct. Every branch except
# store-or-delete is guarded by a self-test row that fails with that branch removed; no row can
# guard store-or-delete, because a stored or deleted bare-name __import__ or __builtins__ is
# itself a voiding occurrence, so removing that branch never changes the exit (the branch is kept
# for completeness of _bound_anywhere and is covered by the occurrence check). The branch flips
# the self-test runs remove the except, global-nonlocal, match-capture and type-parameter
# branches, narrow the match-capture branch to drop its MatchStar and its MatchMapping-rest form,
# and drop __builtins__ from the exception condition. A MatchAs or MatchStar name and a
# MatchMapping rest are one branch: each is a match capture.
_BINDING_BRANCHES = {
    "store-or-delete": lambda node: (
        (node.id,) if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del))
        else ()),
    "parameter": lambda node: (node.arg,) if isinstance(node, ast.arg) else (),
    "def-or-class": lambda node: (
        (node.name,)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) else ()),
    "import-alias": lambda node: (
        tuple(a.asname or a.name.split(".")[0] for a in node.names)
        if isinstance(node, (ast.Import, ast.ImportFrom)) else ()),
    "except": lambda node: (
        (node.name,) if isinstance(node, ast.ExceptHandler) and node.name else ()),
    "global-nonlocal": lambda node: (
        tuple(node.names) if isinstance(node, (ast.Global, ast.Nonlocal)) else ()),
    "match-capture": lambda node: (
        (node.name,) if isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name
        else (node.rest,) if isinstance(node, ast.MatchMapping) and node.rest else ()),
    "type-parameter": lambda node: _type_param_binds(node),
}


def _bound_anywhere(tree):
    """Every name with a binding occurrence anywhere in the module, counted branch by branch
    (_BINDING_BRANCHES) over the same binding constructs _certain_module_aliases counts: a Store or
    Del name, a parameter, a def or class name, an import alias (any import, at any level), an
    except, global, nonlocal or match binding, and a type-parameter name."""
    bound = set()
    for node in ast.walk(tree):
        for binds in _BINDING_BRANCHES.values():
            bound.update(binds(node))
    return frozenset(bound)


def _certain_module_aliases(tree, module_name):
    """The names that certainly hold the standard-library module module_name throughout the module:
    every binding occurrence of the name anywhere in the module (an assignment, a deletion, a
    parameter, a def or class name, any other import, an except, global, nonlocal or match binding,
    a type-parameter name) is a module-level `import module_name` or `import module_name as <alias>`
    statement, and at least one such import exists. One rebinding anywhere, or an import of the
    module inside a function or class body, drops the name, so the two voiding exceptions never
    apply to it and the reference voids as before, fail closed."""
    module_imports = set()
    for node in _scope_walk(tree):
        if isinstance(node, ast.Import):
            module_imports.add(id(node))
    accepted, other = set(), set()
    for node in ast.walk(tree):
        names = ()
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            names = (node.id,)
        elif isinstance(node, ast.arg):
            names = (node.arg,)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names = (node.name,)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                bound = alias.asname or alias.name.split(".")[0]
                if (isinstance(node, ast.Import) and alias.name == module_name
                        and id(node) in module_imports):
                    accepted.add(bound)
                else:
                    other.add(bound)
            continue
        elif isinstance(node, ast.ExceptHandler) and node.name:
            names = (node.name,)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            names = tuple(node.names)
        elif isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name:
            names = (node.name,)
        elif isinstance(node, ast.MatchMapping) and node.rest:
            names = (node.rest,)
        elif isinstance(node, _TYPE_PARAM_NODES):
            names = _type_param_binds(node)
        other.update(names)
    return frozenset(name for name in accepted if name not in other)


def _void_exception_ids(tree):
    """The ids of the reference nodes the two narrow voiding exceptions accept; _voiding_constructs
    skips exactly these. Exactly two forms stop voiding, because neither can rebind a name or run
    code in this module: (a) a Load of an attribute named compile whose object is a name bound, at
    module level and only there, by `import re` (or `import re as <alias>`) and never rebound
    anywhere in the module (re.compile builds a regular expression object; it cannot rebind a name);
    (b) a call __import__(<one string literal>) or importlib.import_module(<one string literal>)
    whose literal names a module in sys.stdlib_module_names, with no other arguments, import_module
    reached through a name bound, at module level and only there, by `import importlib` (or an alias
    of it) and never rebound (importing a literal standard-library module by name returns a module
    object and rebinds nothing in this module's namespace; its result cannot be star-imported, and a
    star import anywhere still voids on its own). Form (b) accepts __import__ only when neither the
    name __import__ nor the name __builtins__ has a binding occurrence anywhere in the module
    (_bound_anywhere over _DUNDER_IMPORT_EXCEPTION_BLOCKERS: an assignment, a deletion, a
    parameter, a def or class name, an import alias, an except, global, nonlocal or match binding,
    or a type-parameter name), so only the builtin can be the callee: a bound __import__ need not
    be the builtin, and a bound __builtins__ is the mapping a bare-name __import__ resolves
    through, and the module can bind it with no bare-name or attribute occurrence the scan reads
    (import shim as __builtins__, a def, class, parameter, except, match or type-parameter binding
    of that name), while a stored or deleted bare-name __builtins__ is already a voiding occurrence
    of its own. Every other
    form voids as before: a bare compile, compile reached through any other object or through a
    rebound alias, an uncalled __import__ or import_module, a __import__ or __builtins__ the module
    binds, a non-literal or non-stdlib argument, and any extra argument."""
    re_names = _certain_module_aliases(tree, "re")
    importlib_names = _certain_module_aliases(tree, "importlib")
    dunder_import_certain = not (_DUNDER_IMPORT_EXCEPTION_BLOCKERS & _bound_anywhere(tree))
    excepted = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Attribute) and node.attr == "compile"
                and isinstance(node.ctx, ast.Load) and isinstance(node.value, ast.Name)
                and isinstance(node.value.ctx, ast.Load) and node.value.id in re_names):
            excepted.add(id(node))
        elif (isinstance(node, ast.Call) and not node.keywords and len(node.args) == 1
                and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)
                and node.args[0].value in sys.stdlib_module_names):
            func = node.func
            if (isinstance(func, ast.Name) and func.id == "__import__"
                    and isinstance(func.ctx, ast.Load) and dunder_import_certain):
                excepted.add(id(func))
            elif (isinstance(func, ast.Attribute) and func.attr == "import_module"
                    and isinstance(func.ctx, ast.Load) and isinstance(func.value, ast.Name)
                    and isinstance(func.value.ctx, ast.Load)
                    and func.value.id in importlib_names):
                excepted.add(id(func))
    return excepted


def _voiding_constructs(tree):
    """The sorted (line, description) list of every voiding shape this scan reads in the module,
    after which no literal-loop pin and no hand-off callee binding in it is accepted. The shapes
    are a fixed list, not a completeness proof: a bare-name or attribute occurrence, read-only
    included, of exec, eval, compile,
    setattr, delattr, __import__ or import_module
    (an alias such as e = exec is a bare-name occurrence; builtins.exec and
    importlib.import_module are attribute spellings); such an occurrence of globals, locals, vars,
    modules (sys.modules under any alias), _getframe, f_locals, f_globals, __dict__, __globals__ or
    __builtins__; a from-import that imports or binds any of those names, under its own name or an
    alias (_from_import_void_names: from builtins import exec as e binds the callable to a name no
    later occurrence spells); a call of getattr or __getattribute__ whose attribute-name argument
    is a string
    literal naming one of those callables or namespaces or naming getattr or __getattribute__
    itself (_VOID_ATTR_LITERALS), or is not a string literal at all; a star
    import anywhere; and a store or delete to an attribute whose chain root is an import-bound name
    (a module object's namespace). Exactly two forms are excepted (_void_exception_ids), because
    neither can rebind a name or run code in this module: (a) an attribute named compile whose
    object is a name bound, at module level and only there, by `import re` (or
    `import re as <alias>`) and never rebound anywhere in the module; (b) a call
    __import__(<one string literal>) or importlib.import_module(<one string literal>) whose literal
    names a module in sys.stdlib_module_names, with no other arguments, import_module reached
    through a module-level, never rebound import of importlib, and __import__ only when neither
    __import__ nor __builtins__ has a binding occurrence anywhere in the module.
    The caller refuses every pin and hand-off in a module with any hit; constant-key
    reads and allowlisted uses stay accepted. The residual: any run-time route that reaches the
    module namespace or a voiding callable without a bare-name, attribute, from-import or
    literal-accessor occurrence this scan reads is not seen (a getattr bound to another name, an
    unnamed functools.partial of getattr, operator.attrgetter, gc.get_referents, a deserializer
    such as pickle that runs code, or a rebinding performed by code outside the hook library); the
    live hook library is clean today by this gate's own run."""
    excepted = _void_exception_ids(tree)
    hits = set()
    imported_roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_roots.update(a.asname or a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == "*":
                    hits.add((node.lineno, "a star import"))
                    continue
                for name in _from_import_void_names(alias):
                    hits.add((node.lineno,
                              "a from-import binding the voiding name {!r}".format(name)))
                imported_roots.add(alias.asname or alias.name)
    for node in ast.walk(tree):
        if id(node) in excepted:
            continue
        if isinstance(node, ast.Name) and node.id in _VOID_NAMES:
            hits.add((node.lineno, "a reference to the name {!r}".format(node.id)))
        elif isinstance(node, ast.Call) and (
                (isinstance(node.func, ast.Name) and node.func.id in _ATTR_ACCESSORS)
                or (isinstance(node.func, ast.Attribute) and node.func.attr in _ATTR_ACCESSORS)):
            # Accepted only on the getattr argument shape with a certain attribute name: at least
            # two positional arguments, no starred argument among the first two, and the second a
            # string literal outside the voiding names and the accessor names themselves. A bound
            # call o.__getattribute__(name) passes its attribute name as its first positional
            # argument, a shape this test does not model, so it never meets the two-argument form
            # and always voids. Every other call of getattr or __getattribute__ voids too (a
            # missing or non-literal name can be built at run time to reach a voiding callable or
            # namespace, and a literal "getattr" or "__getattribute__" returns the accessor itself
            # bound to no name this scan spells).
            if not (len(node.args) >= 2
                    and not any(isinstance(a, ast.Starred) for a in node.args[:2])
                    and isinstance(node.args[1], ast.Constant)
                    and isinstance(node.args[1].value, str)
                    and node.args[1].value not in _VOID_ATTR_LITERALS):
                what = node.func.id if isinstance(node.func, ast.Name) else node.func.attr
                hits.add((node.lineno, "a call of {!r} whose attribute name is not a string "
                          "literal outside the voiding and accessor names".format(what)))
        elif isinstance(node, ast.Attribute):
            if node.attr in _VOID_NAMES:
                hits.add((node.lineno, "a reference to the attribute {!r}".format(node.attr)))
            elif isinstance(node.ctx, (ast.Store, ast.Del)):
                root = node.value
                while isinstance(root, ast.Attribute):
                    root = root.value
                if isinstance(root, ast.Name) and root.id in imported_roots:
                    hits.add((node.lineno,
                              "a store or delete to attribute {!r} of the imported name {!r}".format(
                                  node.attr, root.id)))
    return sorted(hits)


def _unvetted_class(scope):
    """True for a class body whose run-time namespace is not a plain dict for certain: any base,
    keyword or decorator lets a metaclass's __prepare__ mapping resolve any name in the body, so no
    literal-loop pin and no hand-off callee binding there is certain, and the caller refuses both;
    a constant-key read in the body stays accepted."""
    return isinstance(scope, ast.ClassDef) and bool(
        scope.bases or scope.keywords or scope.decorator_list)


def _lazy_nodes(tree):
    """The ids of every node inside a type-parameter list or a type-alias statement. Python evaluates
    both lazily, in their own annotation scope, so no binding visible there is certain at the later
    evaluation time; the caller refuses every registry use among them."""
    lazy = set()
    for node in ast.walk(tree):
        parts = list(getattr(node, "type_params", ()) or ())
        if isinstance(node, ast.TypeAlias):
            parts.append(node.value)
        for part in parts:
            lazy.update(id(inner) for inner in ast.walk(part))
    return lazy


def _helper_param(args, position, keyword):
    """The name of the parameter one call argument binds in a module-level def: a positional argument
    counts through posonlyargs then args, a keyword argument matches args or kwonlyargs; None when the
    argument lands in *args or **kwargs or beyond the declared parameters, so the hand-off cannot be
    tied to one named parameter and is refused."""
    if keyword is not None:
        return next((a.arg for a in list(args.args) + list(args.kwonlyargs) if a.arg == keyword), None)
    ordered = list(args.posonlyargs) + list(args.args)
    return ordered[position].arg if 0 <= position < len(ordered) else None


def _use_text(text, node, parents):
    """The exact source text of one refused registry use: the receiver's parent expression, statement
    or keyword where the parser records its extent, otherwise the receiver name itself."""
    parent = parents.get(node)
    target = parent if isinstance(parent, (ast.expr, ast.stmt, ast.keyword)) else node
    segment = ast.get_source_segment(text, target)
    return segment if segment is not None else node.id


def _key_strings(key, scope_loops):
    """The frozenset of string constants one key expression can hold: a string constant, or a name bound
    only as a literal-loop target in its scope (scope_loops); None when the key cannot be pinned."""
    if isinstance(key, ast.Constant) and isinstance(key.value, str):
        return frozenset((key.value,))
    if isinstance(key, ast.Name) and isinstance(key.ctx, ast.Load):
        return scope_loops.get(key.id)
    return None


def _accepted_read(node, parents, scope_loops, helpers):
    """The frozenset of keys one Load-context registry occurrence yields when it is an accepted access
    form: R.get(K), R.get(K, default), R[K], (R or {}).get(K) or (R or {})[K], with K a string constant
    or a literal-loop variable nothing else in its scope rebinds; or the empty frozenset when the
    registry name is itself an argument, positional or keyword, of a call that holds no starred or
    double-starred argument and whose callee is an undecorated module-level def of the hook library
    (helpers) whose matching parameter is itself a registry name in HOOK_REGISTRY_NAMES, so the
    hand-off's reads are derived where that parameter is read. None for every other occurrence (any
    other callee, a decorated def, a non-registry parameter name, an argument landing in *args or
    **kwargs, a call holding any starred or double-starred argument, whose run-time parameter binding
    is not the counted one, or a callee that cannot be resolved): the caller fails closed on it."""
    receiver, parent = node, parents.get(node)
    if (isinstance(parent, ast.BoolOp) and isinstance(parent.op, ast.Or) and len(parent.values) == 2
            and parent.values[0] is node and isinstance(parent.values[1], ast.Dict)
            and not parent.values[1].keys and not parent.values[1].values):
        receiver, parent = parent, parents.get(parent)
    if (isinstance(parent, ast.Subscript) and parent.value is receiver
            and isinstance(parent.ctx, ast.Load)):
        return _key_strings(parent.slice, scope_loops)
    if isinstance(parent, ast.Attribute) and parent.value is receiver and parent.attr == "get":
        call = parents.get(parent)
        if (isinstance(call, ast.Call) and call.func is parent and not call.keywords
                and len(call.args) in (1, 2)
                and not any(isinstance(a, ast.Starred) for a in call.args)):
            return _key_strings(call.args[0], scope_loops)
    direct = parents.get(node)
    call, position, keyword = None, None, None
    if isinstance(direct, ast.Call) and direct.func is not node and any(a is node for a in direct.args):
        call = direct
        position = next(i for i, a in enumerate(direct.args) if a is node)
    elif (isinstance(direct, ast.keyword) and direct.arg is not None and direct.value is node
          and isinstance(parents.get(direct), ast.Call)):
        call, keyword = parents.get(direct), direct.arg
    if (call is not None and isinstance(call.func, ast.Name)
            and not any(isinstance(a, ast.Starred) for a in call.args)
            and all(kw.arg is not None for kw in call.keywords)):
        signature = helpers.get(call.func.id)
        if signature is not None:
            bound = _helper_param(signature, position, keyword)
            if bound is not None and bound in HOOK_REGISTRY_NAMES:
                return frozenset()
    return None


def _anchor_hits(source_lines, anchor):
    """Every (first line number, line count), one-based, where anchor, split on newlines, occurs in
    source_lines as consecutive whole lines."""
    anchor_lines = anchor.split("\n")
    count = len(anchor_lines)
    return [(start + 1, count) for start in range(len(source_lines) - count + 1)
            if source_lines[start:start + count] == anchor_lines]


def hook_registry_keys(root, registry_use=()):
    """The sorted registry keys the hook library reads, derived from every registry access. The whole
    library is scanned, every scope included (the module, class bodies, decorators, lambdas,
    comprehensions and nested defs). Every Load-context occurrence of a registry receiver name (a name
    in HOOK_REGISTRY_NAMES) must be one of the accepted access forms, each yielding its key: R.get(K),
    R.get(K, default), R[K], (R or {}).get(K) or (R or {})[K], with K a string constant or a loop
    variable over a literal tuple, list or set of string constants bound in exactly the scope of the
    read and rebound by nothing, including any nested scope's nonlocal (or global, for a module-level
    read); scopes follow run-time evaluation, so a comprehension is its own scope but its first
    (outermost) iterable belongs to the enclosing scope, a def's decorators, parameter defaults and
    annotations and a lambda's defaults belong to the enclosing scope, and a class body is its own
    scope; or the registry name handed, positionally or by keyword, by a call holding no starred or
    double-starred argument, to an undecorated module-level def whose matching parameter is itself a
    registry name, yielding no key (the hand-off's reads are derived at that parameter's own reads).
    A registry use inside a type-parameter list or a type-alias statement is refused (both evaluate
    lazily, so no binding there is certain). Every pin and hand-off is refused when the
    module holds any voiding shape the scan reads (_voiding_constructs): a bare-name or attribute
    occurrence, read-only included, of exec, eval, compile, setattr, delattr, __import__ or
    import_module, such an occurrence of globals, locals, vars, modules,
    _getframe, f_locals, f_globals, __dict__, __globals__ or __builtins__, a from-import that
    imports or binds any of those names under its own name or an alias, a call of getattr or
    __getattribute__ whose attribute-name argument is a string literal naming one of those callables
    or namespaces or naming getattr or __getattribute__ itself, or is not a string literal at all, a
    star import anywhere, or a store or delete to
    an attribute rooted in an import-bound name, excepting exactly two forms that can neither rebind
    a name nor run code in the module: (a) an attribute named compile whose object is a name bound,
    at module level and only there, by `import re` (or `import re as <alias>`) and never rebound
    anywhere in the module, and (b) a call __import__(<one string literal>) or
    importlib.import_module(<one string literal>) whose literal names a module in
    sys.stdlib_module_names, with no other arguments, import_module reached through a module-level,
    never rebound import of importlib, __import__ only when neither __import__ nor __builtins__
    has a binding occurrence anywhere in the module; and a pin or
    hand-off in the body of a class with any
    base, keyword or decorator is refused (its metaclass's __prepare__ mapping can resolve any name
    there), while constant-key reads stay accepted in both cases.
    The result is the union of those keys and the keys-read asserted by registry_use, the validated
    (line-anchor, keys-read, uses) triples of the [[detect.registry-use]] allowlist. Refused uses are
    keyed by occurrence (line and column). Refused (CannotEvaluate): any other occurrence, naming its
    line and column, unless an allowlist anchor (one or more complete consecutive source lines)
    matches exactly one place in the library, covers that occurrence's line, and declares that
    occurrence's exact source text in its uses (an extra or changed refused use on an anchored line
    fails closed, naming its line and column); an anchor matching zero or several places; an entry
    whose anchor covers no refused use (a stale entry) or whose uses declare a text no covered use
    matches; a hook library that cannot be read or parsed; and one that yields no key at all (a
    derivation that finds nothing would pass every list vacuously). The residual: a registry object
    reached under a name not in HOOK_REGISTRY_NAMES is not seen, a key used outside the hook library
    is not seen, an allowlisted use is covered only by its asserted keys-read, and any other
    run-time route that reaches the module namespace or a voiding callable without a bare-name,
    attribute, from-import or literal-accessor occurrence the scan reads is not seen (a getattr
    bound to another name, an unnamed functools.partial of getattr, operator.attrgetter,
    gc.get_referents, a deserializer such as pickle that runs code, or a rebinding performed by
    code outside the hook library); the live hook library is clean today by this gate's own run."""
    tree, text = _parse_python_text(os.path.join(root, HOOK_LIBRARY), HOOK_LIBRARY)
    keys = set()
    refused = {}
    try:
        parents = {}
        for node in ast.walk(tree):
            for child in ast.iter_child_nodes(node):
                parents[child] = node
        voided = _voiding_constructs(tree)
        lazy = _lazy_nodes(tree)
        helpers = {} if voided else _module_helpers(tree)
        owner, loops = {}, {}
        stack = [tree]
        while stack:
            scope = stack.pop()
            loops[id(scope)] = {} if voided or _unvetted_class(scope) else _loop_key_strings(scope)
            for node in _scope_walk(scope):
                owner[id(node)] = scope
                if isinstance(node, _SCOPE_NODES):
                    stack.append(node)
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Name) and node.id in HOOK_REGISTRY_NAMES
                    and isinstance(node.ctx, ast.Load)):
                continue
            read_scope = owner.get(id(node), tree)
            got = None if id(node) in lazy else _accepted_read(
                node, parents, loops[id(read_scope)], helpers)
            if got is not None and not got and _unvetted_class(read_scope):
                # A hand-off in the body of a class with a base, keyword or decorator: the metaclass's
                # __prepare__ mapping can resolve the callee name there, so the binding is not certain.
                got = None
            if got is None:
                refused[(node.lineno, node.col_offset + 1)] = (node.id, _use_text(text, node, parents))
            else:
                keys |= got
    except RecursionError:
        raise CannotEvaluate("{}: nested too deeply to analyse".format(HOOK_LIBRARY)) from None
    source_lines = text.split("\n")
    excused = set()
    for anchor, keys_read, uses in registry_use:
        hits = _anchor_hits(source_lines, anchor)
        if len(hits) != 1:
            raise CannotEvaluate(
                "{}: [[detect.registry-use]] anchor {!r} matches {} in {}; an anchor must match exactly "
                "one run of whole source lines".format(
                    SWITCH_LIST, anchor,
                    "no line" if not hits else "{} places".format(len(hits)), HOOK_LIBRARY))
        start, count = hits[0]
        lines_covered = set(range(start, start + count))
        covered = sorted(place for place in refused if place[0] in lines_covered)
        if not covered:
            raise CannotEvaluate(
                "{}: [[detect.registry-use]] anchor {!r} excuses no registry use in {}; remove the "
                "stale entry".format(SWITCH_LIST, anchor, HOOK_LIBRARY))
        remaining = list(uses)
        for place in covered:
            use = refused[place][1]
            if use in remaining:
                remaining.remove(use)
            else:
                raise CannotEvaluate(
                    "{}: [[detect.registry-use]] anchor {!r} does not declare the refused use {!r} at "
                    "line {} column {} of {} in its uses; every refused use on an anchored line must "
                    "be declared by its exact source text, so an extra or changed use fails "
                    "closed".format(SWITCH_LIST, anchor, use, place[0], place[1], HOOK_LIBRARY))
        if remaining:
            raise CannotEvaluate(
                "{}: [[detect.registry-use]] anchor {!r} declares the use {!r} in uses but no refused "
                "use on its anchored line(s) in {} matches it; remove or correct the stale "
                "declaration".format(SWITCH_LIST, anchor, remaining[0], HOOK_LIBRARY))
        excused.update(covered)
        keys.update(keys_read)
    leftover = sorted(place for place in refused if place not in excused)
    if leftover:
        line, column = leftover[0]
        voiding_note = ""
        if voided:
            first_line, first_what = voided[0]
            voiding_note = ("; the module holds {} certainty-voiding construct(s), the first being {} "
                            "at line {}, so every literal-loop pin and hand-off in it is refused"
                            .format(len(voided), first_what, first_line))
        raise CannotEvaluate(
            "{}: the registry use of {!r} at line {} column {} is outside the accepted access forms "
            "(R.get(K), R.get(K, default), R[K], (R or {{}}).get(K) or (R or {{}})[K], K a string "
            "constant or a literal-loop variable bound in the read's own scope, or the registry handed, "
            "with no starred or double-starred argument, to an undecorated module-level def whose "
            "matching parameter is a registry name) and no "
            "[[detect.registry-use]] entry excuses it; {} such use(s) in all{}".format(
                HOOK_LIBRARY, refused[leftover[0]][0], line, column, len(leftover), voiding_note))
    if not keys:
        raise CannotEvaluate("{}: no registry key is read in an accepted access form, so the [detect] "
                             "keys cannot be checked against the hook library".format(HOOK_LIBRARY))
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
        derived = hook_registry_keys(root, detect["registry-use"])
    except CannotEvaluate as exc:
        return [], [str(exc)], {}
    declared, excluded = detect["keys"], detect["non-record-keys"]
    problems = []
    for key in sorted(set(declared) & set(excluded)):
        problems.append("{}: [detect] non-record-keys repeats {!r} from [detect] keys; a key names a "
                        "record path or does not, never both".format(SWITCH_LIST, key))
    for key in sorted(k for k in excluded if k not in derived):
        problems.append("{}: [detect] non-record-keys lists {!r} but no registry read in {} derives it; "
                        "remove the stale exclusion".format(SWITCH_LIST, key, HOOK_LIBRARY))
    lacking = [k for k in derived if k not in declared and k not in excluded]
    if lacking:
        problems.append("{}: [detect] keys lacks {}, which {} reads from the registry; a derived key "
                        "that names no record path belongs in [detect] non-record-keys with a "
                        "reason".format(SWITCH_LIST, ", ".join(repr(k) for k in lacking), HOOK_LIBRARY))
    if problems:
        return [], problems, {}
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
    print("PASS: {} record reader(s) under {} are each on {} with a disposition (the residual: a "
          "registry object reached under a name not in HOOK_REGISTRY_NAMES is not seen, a key used "
          "outside the hook library is not seen, an allowlisted use is covered only by its asserted "
          "keys-read, and any other run-time route that reaches the module namespace or a voiding "
          "callable without a bare-name, attribute, from-import or literal-accessor occurrence the "
          "scan reads is not seen: a getattr bound to another name, an unnamed functools.partial "
          "of getattr, operator.attrgetter, gc.get_referents, a deserializer such as pickle that "
          "runs code, or a rebinding by code outside the hook library; the live hook library is "
          "clean today by this gate's own run)".format(
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
    # The fixture hook library: its one accepted registry read derives a key [detect] keys declares.
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


def _gate_without_voiding(files):
    """The gate's exit on files with the module-voiding rule removed: the flip proving a row that
    refuses under the rule is accepted without it."""
    saved = _voiding_constructs
    globals()["_voiding_constructs"] = lambda tree: []
    try:
        return _gate(files)
    finally:
        globals()["_voiding_constructs"] = saved


def _gate_without_void_exceptions(files):
    """The gate's exit on files with the two narrow voiding exceptions removed (no reference is
    excepted): the flip proving each exception row depends on its exception."""
    saved = _void_exception_ids
    globals()["_void_exception_ids"] = lambda tree: set()
    try:
        return _gate(files)
    finally:
        globals()["_void_exception_ids"] = saved


def _gate_every_class_vetted(files):
    """The gate's exit on files with the unvetted-class rule removed: the flip proving a row that
    refuses under the rule is accepted without it."""
    saved = _unvetted_class
    globals()["_unvetted_class"] = lambda scope: False
    try:
        return _gate(files)
    finally:
        globals()["_unvetted_class"] = saved


def _gate_without_type_param_binds(files):
    """The gate's exit on files with type-parameter bindings ignored: the flip proving the shadowed
    hand-off row depends on counting them."""
    saved = _type_param_binds
    globals()["_type_param_binds"] = lambda node: ()
    try:
        return _gate(files)
    finally:
        globals()["_type_param_binds"] = saved


def _gate_without_from_import_voiding(files):
    """The gate's exit on files with the from-import voiding check removed: the flip proving each
    from-import row depends on it."""
    saved = _from_import_void_names
    globals()["_from_import_void_names"] = lambda alias: ()
    try:
        return _gate(files)
    finally:
        globals()["_from_import_void_names"] = saved


def _gate_without_accessor_literal_voiding(files):
    """The gate's exit on files with the accessor names dropped from the refused attribute-name
    literals: the flip proving the accessor-literal row depends on refusing them."""
    saved = _VOID_ATTR_LITERALS
    globals()["_VOID_ATTR_LITERALS"] = _VOID_NAMES
    try:
        return _gate(files)
    finally:
        globals()["_VOID_ATTR_LITERALS"] = saved


def _gate_without_binding_branch(files, branch):
    """The gate's exit on files with one binding branch of _bound_anywhere removed: the flip proving
    each branch-guard row depends on the branch that counts its binding construct."""
    saved = _BINDING_BRANCHES.pop(branch)
    try:
        return _gate(files)
    finally:
        _BINDING_BRANCHES[branch] = saved


def _gate_with_binding_branch(files, branch, binds):
    """The gate's exit on files with one binding branch of _bound_anywhere replaced by a narrower
    form: the flip proving a row depends on the exact form the replacement drops, which a
    whole-branch removal cannot show."""
    saved = _BINDING_BRANCHES[branch]
    _BINDING_BRANCHES[branch] = binds
    try:
        return _gate(files)
    finally:
        _BINDING_BRANCHES[branch] = saved


def _gate_without_builtins_blocker(files):
    """The gate's exit on files with __builtins__ dropped from the names whose binding stops the
    __import__ voiding exception: the flip proving each __builtins__-binding row depends on that
    part of the condition."""
    saved = _DUNDER_IMPORT_EXCEPTION_BLOCKERS
    globals()["_DUNDER_IMPORT_EXCEPTION_BLOCKERS"] = frozenset({"__import__"})
    try:
        return _gate(files)
    finally:
        globals()["_DUNDER_IMPORT_EXCEPTION_BLOCKERS"] = saved


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

    # The [detect] keys are checked as a superset of the keys the whole-library derivation yields
    # (less the excluded non-record keys); the hook library itself must be present and parseable.
    for case_id, hook, needle in (
            ("refuse/hook-key-missing-from-detect",
             'def a(root, reg):\n    return _orch_path(root, reg.get("ledger_path")) or '
             '_orch_path(root, reg["ledger_extra"])\n', "[detect] keys lacks 'ledger_extra'"),
            ("refuse/hook-loop-key-missing-from-detect",
             'def a(root, rec):\n    for key in ("ledger_path", "ledger_loop"):\n'
             '        _orch_path(root, rec.get(key))\n', "[detect] keys lacks 'ledger_loop'"),
            ("refuse/hook-or-key-missing-from-detect",
             'def a(root, reg_data):\n    return _orch_path(root, (reg_data or {}).get("ledger_dir"))\n',
             "[detect] keys lacks 'ledger_dir'"),
            ("refuse/hook-keyword-key-missing-from-detect",
             'def a(root, reg):\n    return _orch_path(root, value=reg.get("ledger_kw"))\n',
             "[detect] keys lacks 'ledger_kw'"),
            ("refuse/hook-library-no-keys", "X = 1\n", "no registry key is read"),
            ("refuse/hook-library-unparseable", "def a(:\n", HOOK_LIBRARY + ": not parseable Python")):
        rc, out = _gate(dict(_FIXTURE_FILES, **{HOOK_LIBRARY: hook}))
        check(case_id, rc == 2 and needle in out, (rc, out))

    # Reads through a receiver that is not a registry name derive nothing and are not refused.
    hook = ('def a(root, reg, lease):\n    return _orch_path(root, reg.get("ledger_path")) or '
            '_orch_path(root, lease.get("ledger_other"))\n')
    rc, out = _gate(dict(_FIXTURE_FILES, **{HOOK_LIBRARY: hook}))
    check("ignore/hook-unrelated-reads", rc == 0, (rc, out))

    # One derive row per accepted access form and per scanned region: the read is accepted and its
    # key, absent from [detect] keys, is reported, so the derived set provably includes that read.
    for case_id, hook, needle in (
            ("derive/get-constant-key",
             'def a(root, reg):\n    return reg.get("ledger_get")\n', "lacks 'ledger_get'"),
            ("derive/get-default-key",
             'def a(root, reg):\n    return reg.get("ledger_dflt", None)\n', "lacks 'ledger_dflt'"),
            ("derive/subscript-key",
             'def a(root, reg):\n    return reg["ledger_subs"]\n', "lacks 'ledger_subs'"),
            ("derive/or-get-key",
             'def a(root, reg_data):\n    return (reg_data or {}).get("ledger_or")\n', "lacks 'ledger_or'"),
            ("derive/or-subscript-key",
             'def a(root, reg_data):\n    return (reg_data or {})["ledger_orsub"]\n', "lacks 'ledger_orsub'"),
            ("derive/literal-loop-key",
             'def a(root, rec):\n    for key in ("ledger_path", "ledger_lkey"):\n        rec.get(key)\n',
             "lacks 'ledger_lkey'"),
            ("derive/comprehension-loop-key",
             'def a(root, rec):\n    return [rec[key] for key in ["ledger_comp"]]\n', "lacks 'ledger_comp'"),
            ("derive/class-body-read-key",
             'class C:\n    V = reg.get("ledger_cls")\n', "lacks 'ledger_cls'"),
            ("derive/decorator-read-key",
             'def d(x):\n    return lambda f: f\n@d(reg.get("ledger_deco"))\ndef f():\n    return None\n',
             "lacks 'ledger_deco'"),
            ("derive/lambda-body-read-key",
             'f = lambda reg: reg.get("ledger_lam")\n', "lacks 'ledger_lam'"),
            ("derive/nested-def-read-key",
             'def a(root, reg):\n    def getter():\n        return reg.get("ledger_nested")\n'
             '    return _orch_path(root, getter())\n', "lacks 'ledger_nested'"),
            ("derive/walrus-value-read-key",
             'def a(root, reg):\n    if (box := reg.get("ledger_wal")):\n        return box\n    return None\n',
             "lacks 'ledger_wal'"),
            ("derive/for-iter-read-key",
             'def a(root, reg):\n    for p in reg.get("ledger_each"):\n        _orch_path(root, p)\n',
             "lacks 'ledger_each'"),
            ("derive/with-context-read-key",
             'def a(root, reg):\n    with reg.get("ledger_ctx") as c:\n        _orch_path(root, c)\n',
             "lacks 'ledger_ctx'"),
            # A comprehension's first (outermost) iterable is evaluated in the enclosing scope, so a
            # key the enclosing loop pins is accepted there.
            ("derive/comprehension-first-iter-outer-pinned-key",
             'def a(root, reg):\n    for k in ("ledger_a",):\n        _ = [x for x in reg.get(k)]\n',
             "lacks 'ledger_a'"),
            # A class body is its own scope: a literal loop in it pins a read in the same class body.
            ("derive/class-body-loop-key",
             'class C:\n    for k in ("ledger_cb",):\n        V = reg.get(k)\n',
             "lacks 'ledger_cb'")):
        rc, out = _gate(dict(_FIXTURE_FILES, **{HOOK_LIBRARY: hook}))
        check(case_id, rc == 2 and needle in out, (rc, out))

    # A chain needs only its head read derived: the later steps go through non-registry names.
    keys, crash = _hook_keys(
        'def a(root, reg):\n'
        '    lease = reg.get("ledger_lease") if isinstance(reg.get("ledger_lease"), dict) else None\n'
        '    block = lease.get("heartbeat")\n'
        '    lock_path = block.get("lock_path")\n'
        '    return _orch_path(root, lock_path)\n')
    check("derive/multi-step-chain-key", crash is None and keys == ("ledger_lease",), (keys, crash))

    # With every derived key declared, the gate passes.
    detect = _FIXTURE_DETECT.replace('keys = ["ledger_path"]', 'keys = ["ledger_path", "ledger_box"]')
    hook = ('def a(root, reg):\n    box = reg.get("ledger_box")\n'
            '    return _orch_path(root, box.get("path")) or _orch_path(root, reg.get("ledger_path"))\n')
    rc, out = _gate(dict(_FIXTURE_FILES, **{HOOK_LIBRARY: hook}), _switch_text(detect=detect))
    check("pass/hook-assigned-key-listed", rc == 0, (rc, out))

    # Every registry use outside the accepted access forms is cannot-evaluate naming its exact line,
    # never a silent pass; one row per shape.
    for case_id, hook, line in (
            ("refuse/for-literal-collection-read",
             'def a(root, reg, k):\n    for v in [reg.get(k)]:\n        _orch_path(root, v)\n', 2),
            ("refuse/comprehension-literal-collection-read",
             'def a(root, reg, k):\n    return [_orch_path(root, v) for v in [reg.get(k)]]\n', 2),
            ("refuse/match-mapping-capture",
             'def a(root, reg):\n    match reg:\n        case {"ledger_m": v}:\n            return v\n'
             '    return None\n', 2),
            ("refuse/match-as-capture",
             'def a(root, reg):\n    match reg:\n        case str() as v:\n            return v\n'
             '    return None\n', 2),
            ("refuse/match-bare-capture",
             'def a(root, reg):\n    match reg:\n        case v:\n            return v\n', 2),
            ("refuse/except-as-binding",
             'def a(root, reg):\n    try:\n        raise ValueError(reg)\n    except ValueError as e:\n'
             '        return _orch_path(root, e.args[0])\n', 3),
            ("refuse/nonlocal-chain",
             'def a(root, reg, k):\n    box = None\n    def mid():\n        def inner():\n'
             '            nonlocal box\n            box = reg.get(k)\n        inner()\n    mid()\n'
             '    return _orch_path(root, box)\n', 6),
            ("refuse/pop-method", 'def a(root, reg):\n    return reg.pop("ledger_pop")\n', 2),
            ("refuse/setdefault-method",
             'def a(root, reg):\n    return reg.setdefault("ledger_sd", "")\n', 2),
            ("refuse/dunder-getitem-method",
             'def a(root, reg):\n    return reg.__getitem__("ledger_dd")\n', 2),
            ("refuse/copy-then-subscript",
             'def a(root, reg):\n    c = reg.copy()\n    return c["ledger_cp"]\n', 2),
            ("refuse/splat-dict-then-subscript",
             'def a(root, reg):\n    d = {**reg}\n    return d["ledger_sp"]\n', 2),
            ("refuse/items-loop",
             'def a(root, reg):\n    for k, v in reg.items():\n        _orch_path(root, v)\n', 2),
            ("refuse/conditional-receiver",
             'def a(root, reg, c):\n    return (reg if c else {}).get("ledger_cond")\n', 2),
            ("refuse/or-second-operand-receiver",
             'def a(root, reg, other):\n    return (other or reg).get("ledger_or2")\n', 2),
            ("refuse/nullcontext-receiver",
             'from contextlib import nullcontext\ndef a(root, reg):\n    with nullcontext(reg) as r:\n'
             '        return r.get("ledger_nc")\n', 3),
            ("refuse/class-body-unpinned-read",
             'KEY = "ledger_ck"\nclass C:\n    V = reg.get(KEY)\n', 3),
            ("refuse/decorator-registry-argument",
             'def d(x):\n    return lambda f: f\n@d(reg)\ndef f():\n    return None\n', 3),
            ("refuse/lambda-handed-registry",
             'def a(root, reg):\n    f = lambda r=reg: r.get("ledger_lh")\n    return f()\n', 2),
            ("refuse/nested-def-handed-registry",
             'def a(root, reg):\n    def g(r=reg):\n        return r.get("ledger_nh")\n    return g()\n', 2),
            ("refuse/starred-resolver-argument",
             'def a(root, reg, k):\n    args = (root, reg.get(k))\n    return _orch_path(*args)\n', 2),
            ("refuse/aliased-resolver-call",
             'def a(root, reg, k):\n    op = _orch_path\n    return op(root, reg.get(k))\n', 3),
            ("refuse/attribute-resolver-call",
             'def a(root, reg, k, helpers):\n    return helpers._orch_path(root, reg.get(k))\n', 2),
            ("refuse/receiver-alias-assignment",
             'def a(root, reg):\n    r = reg\n    return r.get("ledger_ra")\n', 2),
            ("refuse/getattr-call-argument",
             'def a(root, reg):\n    g = getattr(reg, "get")\n    return g("ledger_ga")\n', 2),
            ("refuse/hook-unresolvable-key-format",
             'def a(root, reg, n):\n    return reg.get("ledger_{}".format(n))\n', 2),
            ("refuse/hook-unresolvable-key-fstring",
             'def a(root, reg, n):\n    return reg.get(f"ledger_{n}")\n', 2),
            ("refuse/hook-unresolvable-key-parameter",
             'def a(root, reg, key):\n    return reg.get(key)\n', 2),
            ("refuse/hook-unresolvable-key-named-collection-loop",
             'KEYS = ("ledger_path",)\ndef a(root, reg):\n    for key in KEYS:\n        reg.get(key)\n', 4),
            ("refuse/hook-unresolvable-key-tuple-unpacking-loop",
             'def a(root, reg):\n    for key, _tag in (("ledger_path", 1),):\n        reg.get(key)\n', 3),
            ("refuse/hook-unresolvable-key-mixed-literal-loop",
             'def a(root, reg, extra):\n    for key in ("ledger_path", extra):\n        reg.get(key)\n', 3),
            ("refuse/hook-unresolvable-key-other-scope-loop",
             'def a(root, rec):\n    return rec.get(key)\ndef b():\n    for key in ("ledger_far",):\n'
             '        pass\n', 2),
            ("refuse/hook-unresolvable-key-alias-call",
             'def a(root, reg, k):\n    g = reg.get\n    return g(k)\n', 2),
            ("refuse/hook-assigned-unresolvable-key",
             'def a(root, reg, key):\n    box = reg.get(key)\n    return box\n', 2),
            ("refuse/rebound-loop-variable-key",
             'def a(root, reg, other):\n    for key in ("ledger_path",):\n        key = other\n'
             '        reg.get(key)\n', 4),
            ("refuse/lambda-default-registry",
             'def a(root, reg):\n    f = lambda k, r=reg: r.get(k)\n    return f("ledger_hid")\n', 2),
            ("refuse/lambda-returns-registry",
             'def a(root, reg, k):\n    f = lambda: reg\n    return f().get(k)\n', 2),
            ("refuse/getattr-non-get-attribute",
             'def a(root, reg):\n    return getattr(reg, "values")\n', 2),
            # A comprehension is its own scope: its target pins keys only for reads inside it, so a
            # read beside it that at run time sees an outer binding of the same name is refused.
            ("refuse/comprehension-shadowed-outer-key",
             'qk = "x"\ndef f(root, reg):\n    _ = [0 for qk in ("ledger_sh",)]\n    return reg.get(qk)\n', 4),
            # A loop key a nested scope rebinds (nonlocal, or global for a module-level loop) is not
            # pinned: the read can see the rebound value.
            ("refuse/nonlocal-rebound-loop-key",
             'def a(root, reg):\n    for qk in ("ledger_nl",):\n        def g():\n            nonlocal qk\n'
             '            qk = "x"\n        g()\n        reg.get(qk)\n', 7),
            ("refuse/global-rebound-module-loop-key",
             'def g():\n    global qk\n    qk = "x"\nfor qk in ("ledger_gl",):\n    g()\n    reg.get(qk)\n', 6),
            # A registry name handed to a module-level def is accepted only when the matching
            # parameter is itself a registry name; anything else is refused.
            ("refuse/helper-non-registry-parameter",
             'def _take(r, root):\n    return r\ndef a(root, reg):\n    return _take(reg, root)\n'
             'def b(root, reg):\n    return reg.get("ledger_path")\n', 4),
            ("refuse/helper-starargs-parameter",
             'def _take(*args):\n    return args\ndef a(root, reg):\n    return _take(reg)\n'
             'def b(root, reg):\n    return reg.get("ledger_path")\n', 4),
            ("refuse/helper-rebound-callee-name",
             '_take = None\ndef _take(reg, root):\n    return reg.get("ledger_path")\n'
             'def a(root, reg):\n    return _take(reg, root)\n', 5),
            # Each binding construct that must void a literal-loop pin in its own scope: a global or
            # nonlocal declaration, a match capture and an except capture each rebind the name.
            ("refuse/global-declared-loop-key",
             'def a(root, reg):\n    global key\n    for key in ("ledger_g",):\n        reg.get(key)\n', 4),
            ("refuse/match-capture-loop-key",
             'def a(root, reg, v):\n    for key in ("ledger_m",):\n        match v:\n'
             '            case str() as key:\n                pass\n        reg.get(key)\n', 6),
            ("refuse/except-capture-loop-key",
             'def a(root, reg):\n    for key in ("ledger_e",):\n        try:\n            pass\n'
             '        except ValueError as key:\n            pass\n        reg.get(key)\n', 7),
            # A comprehension's first iterable is evaluated in the enclosing scope: a read there is
            # never pinned by the comprehension's own targets, for each comprehension kind and each
            # access form (get, subscript, (R or {}).get(K, default)).
            ("refuse/listcomp-first-iter-enclosing-binding",
             'def a(root, reg):\n    k = "ledger_other"\n'
             '    return [x for x in reg.get(k) for k in ("ledger_a",)]\n', 3),
            ("refuse/setcomp-first-iter-enclosing-binding",
             'def a(root, reg):\n    k = "ledger_other"\n'
             '    return {x for x in reg[k] for k in ("ledger_a",)}\n', 3),
            ("refuse/dictcomp-first-iter-enclosing-binding",
             'def a(root, reg):\n    k = "ledger_other"\n'
             '    return {x: 1 for x in (reg or {}).get(k, ()) for k in ("ledger_a",)}\n', 3),
            ("refuse/genexp-first-iter-enclosing-binding",
             'def a(root, reg):\n    k = "ledger_other"\n'
             '    return list(x for x in reg[k] for k in ("ledger_a",))\n', 3),
            # A hand-off is accepted only on a callee and call shape whose run-time binding is
            # certain: a decorated def binds its name to the decorator's result, and a starred or
            # double-starred argument makes the counted parameter position or keyword uncertain.
            ("refuse/helper-decorated-callee",
             'def _deco(f):\n    return f\n@_deco\ndef _take(reg, root):\n'
             '    return reg.get("ledger_path")\ndef a(root, reg):\n    return _take(reg, root)\n', 7),
            ("refuse/helper-starred-argument-call",
             'def _take(a, reg={}):\n    return reg.get("ledger_path")\ndef a(root, reg, pre):\n'
             '    return _take(*pre, reg)\n', 4),
            ("refuse/helper-doublestar-argument-call",
             'def _take(reg, root=None):\n    return reg.get("ledger_path")\ndef a(root, reg, kw):\n'
             '    return _take(reg, **kw)\n', 4),
            # A def's decorators, parameter defaults and annotations and a lambda's defaults are
            # evaluated in the enclosing scope: a walrus there rebinds the enclosing loop key, and a
            # read there is never pinned by a loop inside the def.
            ("refuse/def-default-walrus-rebound-loop-key",
             'def a(root, reg):\n    for k in ("ledger_a",):\n'
             '        def g(x=(k := "ledger_other")):\n            return x\n        reg.get(k)\n', 5),
            ("refuse/lambda-default-walrus-rebound-loop-key",
             'def a(root, reg):\n    for k in ("ledger_a",):\n'
             '        f = lambda x=(k := "ledger_other"): x\n        reg.get(k)\n', 4),
            ("refuse/lambda-default-comprehension-walrus-loop-key",
             'def a(root, reg):\n    for k in ("ledger_a",):\n'
             '        f = lambda x=[(k := "ledger_other") for _ in (1,)]: x\n        reg.get(k)\n', 4),
            ("refuse/decorator-walrus-rebound-loop-key",
             'def d(x):\n    return lambda f: f\nfor k in ("ledger_a",):\n'
             '    @d((k := "ledger_other"))\n    def h():\n        return None\n    reg.get(k)\n', 7),
            ("refuse/module-def-default-walrus-loop-key",
             'for k in ("ledger_a",):\n    def h(x=(k := "ledger_other")):\n        return x\n'
             '    reg.get(k)\n', 4),
            ("refuse/def-default-read-pinned-by-inner-loop",
             'def a(root, reg, k):\n    def g(x=reg.get(k)):\n        for k in ("ledger_a",):\n'
             '            pass\n        return x\n    return g()\n', 2),
            ("refuse/decorator-read-pinned-by-inner-loop",
             'def d(x):\n    return lambda f: f\nk = "ledger_other"\n@d(reg.get(k))\ndef h():\n'
             '    for k in ("ledger_a",):\n        pass\n', 4),
            ("refuse/annotation-read-pinned-by-inner-loop",
             'def a(root, reg, k):\n    def g(x: reg.get(k) = None):\n        for k in ("ledger_a",):\n'
             '            pass\n        return x\n    return g()\n', 2),
            # A class body is its own scope: its loop targets never pin a read in the enclosing
            # scope (which at run time resolves the enclosing or module binding), and a read inside
            # it is never pinned by the enclosing function's loop (fail closed on the cross-scope
            # pin, even where the closure read would see it).
            ("refuse/class-body-loop-pins-enclosing-read",
             'k = "ledger_other"\ndef a(root, reg):\n    class C:\n        for k in ("ledger_a",):\n'
             '            pass\n    return reg.get(k)\n', 6),
            ("refuse/class-body-read-enclosing-loop-pin",
             'def a(root, reg):\n    for k in ("ledger_a",):\n        class C:\n'
             '            V = reg.get(k)\n', 4),
            # A module that writes globals(), locals() or vars() by subscript or a method call can
            # rebind any name at run time, so every pin and hand-off in it is refused.
            ("refuse/globals-subscript-write-voids-pin",
             'def a(root, reg):\n    for k in ("ledger_a",):\n        reg.get(k)\n'
             'globals()["x"] = 1\n', 3),
            ("refuse/globals-update-voids-handoff",
             'def _take(reg, root):\n    return reg.get("ledger_path")\ndef a(root, reg):\n'
             '    return _take(reg, root)\nglobals().update({})\n', 4),
            ("refuse/locals-subscript-write-voids-pin",
             'def a(root, reg):\n    for k in ("ledger_a",):\n        reg.get(k)\n'
             '    locals()["k"] = "ledger_other"\n', 3),
            ("refuse/vars-method-call-voids-pin",
             'def a(root, reg):\n    for k in ("ledger_a",):\n        reg.get(k)\n'
             '    vars().update(k="ledger_other")\n', 3),
            # A type-parameter list and a type-alias statement evaluate lazily, in their own
            # annotation scope, so no registry use inside one is accepted, constant key included.
            ("refuse/type-param-registry-read",
             'def a[T: reg.get("ledger_tp")](root):\n    return None\n', 1),
            ("refuse/type-alias-registry-read",
             'type Alias = reg.get("ledger_ta")\n', 1)):
        rc, out = _gate(dict(_FIXTURE_FILES, **{HOOK_LIBRARY: hook}))
        check(case_id, rc == 2 and "outside the accepted access forms" in out
              and "at line {} column".format(line) in out, (rc, out))

    # A dynamic-namespace write voids pins and hand-offs only: a constant-key read stays accepted.
    hook_dyn = 'def a(root, reg):\n    return reg.get("ledger_path")\nglobals()["flag"] = 1\n'
    rc, out = _gate(dict(_FIXTURE_FILES, **{HOOK_LIBRARY: hook_dyn}))
    check("pass/dynamic-write-constant-key-kept", rc == 0, (rc, out))

    # Voiding on the scanned shapes: a module where the scan reads any listed voiding shape keeps
    # no literal-loop pin and no hand-off. Each shape
    # is refused with the rule present (void/), its quiet twin without the construct passes (quiet/),
    # and the flip that removes the rule accepts the same module (flip/), so each row depends on it.
    for void_id, quiet_id, flip_id, hook, twin, line in (
            ("void/setattr-modules-attr-rebinding", "quiet/setattr-modules-attr-rebinding",
             "flip/setattr-modules-attr-rebinding",
             'import sys\nfor k in ("ledger_path",):\n    reg.get(k)\n'
             'setattr(sys.modules[__name__], "k", "x")\n',
             'import sys\nfor k in ("ledger_path",):\n    reg.get(k)\nset_attr = sys.intern("x")\n', 3),
            ("void/exec-statement-rebinding", "quiet/exec-statement-rebinding",
             "flip/exec-statement-rebinding",
             'for k in ("ledger_path",):\n    reg.get(k)\nexec("k = 0")\n',
             'for k in ("ledger_path",):\n    reg.get(k)\ndef executor():\n    return None\n', 2),
            ("void/dict-update-globals-rebinding", "quiet/dict-update-globals-rebinding",
             "flip/dict-update-globals-rebinding",
             'for k in ("ledger_path",):\n    reg.get(k)\ndict.update(globals(), k="x")\n',
             'D = {}\nfor k in ("ledger_path",):\n    reg.get(k)\ndict.update(D, other="x")\n', 2),
            ("void/builtins-globals-subscript-rebinding", "quiet/builtins-globals-subscript-rebinding",
             "flip/builtins-globals-subscript-rebinding",
             'import builtins\nfor k in ("ledger_path",):\n    reg.get(k)\n'
             'builtins.globals()["k"] = "x"\n',
             'import builtins\nfor k in ("ledger_path",):\n    reg.get(k)\nX = builtins.id\n', 3),
            ("void/getframe-f-locals-write", "quiet/getframe-f-locals-write",
             "flip/getframe-f-locals-write",
             'import sys\ndef a(root, reg):\n    for k in ("ledger_path",):\n        reg.get(k)\n'
             '        sys._getframe().f_locals["k"] = "x"\n',
             'import sys\ndef a(root, reg):\n    for k in ("ledger_path",):\n        reg.get(k)\n'
             '        x = sys.maxsize\n', 4),
            ("void/star-import-inside-loop", "quiet/star-import-inside-loop",
             "flip/star-import-inside-loop",
             'for k in ("ledger_path",):\n    from os.path import *\n    reg.get(k)\n',
             'from os.path import join\nfor k in ("ledger_path",):\n    reg.get(k)\n', 3),
            ("void/setattr-redirected-handoff-callee", "quiet/setattr-redirected-handoff-callee",
             "flip/setattr-redirected-handoff-callee",
             'import sys\ndef _take(reg, root):\n    return reg.get("ledger_path")\n'
             'def _other(x, root):\n    return None\nsetattr(sys.modules[__name__], "_take", _other)\n'
             'def a(root, reg):\n    return _take(reg, root)\n',
             'def _take(reg, root):\n    return reg.get("ledger_path")\n'
             'def _other(x, root):\n    return None\ndef a(root, reg):\n    return _take(reg, root)\n', 8),
            ("void/imported-module-attribute-store", "quiet/imported-module-attribute-store",
             "flip/imported-module-attribute-store",
             'import os\nfor k in ("ledger_path",):\n    reg.get(k)\nos.lab_marker = "x"\n',
             'import os\ndef f(box):\n    box.lab_marker = "x"\nfor k in ("ledger_path",):\n'
             '    reg.get(k)\n', 3),
            ("void/sys-modules-attribute-store", "quiet/sys-modules-attribute-store",
             "flip/sys-modules-attribute-store",
             'import sys\nfor k in ("ledger_path",):\n    reg.get(k)\nsys.modules[__name__].k = "x"\n',
             'import sys\nSM = sys.argv\nfor k in ("ledger_path",):\n    reg.get(k)\n', 3),
            ("void/dunder-dict-subscript-write", "quiet/dunder-dict-subscript-write",
             "flip/dunder-dict-subscript-write",
             'def f(o):\n    o.__dict__["k"] = "x"\nfor k in ("ledger_path",):\n    reg.get(k)\n',
             'def f(o):\n    o.store["k"] = "x"\nfor k in ("ledger_path",):\n    reg.get(k)\n', 4),
            ("void/f-globals-reference", "quiet/f-globals-reference", "flip/f-globals-reference",
             'def f(frame):\n    return frame.f_globals\nfor k in ("ledger_path",):\n    reg.get(k)\n',
             'def f(frame):\n    return frame.f_lineno\nfor k in ("ledger_path",):\n    reg.get(k)\n', 4),
            ("void/star-import-before-handoff", "quiet/star-import-before-handoff",
             "flip/star-import-before-handoff",
             'from os.path import *\ndef _take(reg, root):\n    return reg.get("ledger_path")\n'
             'def a(root, reg):\n    return _take(reg, root)\n',
             'from os.path import basename\ndef _take(reg, root):\n    return reg.get("ledger_path")\n'
             'def a(root, reg):\n    return _take(reg, root)\n', 5),
            ("void/eval-name-binding", "quiet/eval-name-binding", "flip/eval-name-binding",
             'E = eval\nfor k in ("ledger_path",):\n    reg.get(k)\n',
             'evaluate = None\nfor k in ("ledger_path",):\n    reg.get(k)\n', 3),
            ("void/compile-attribute-call", "quiet/compile-attribute-call",
             "flip/compile-attribute-call",
             'def f(box):\n    return box.compile("x")\nfor k in ("ledger_path",):\n    reg.get(k)\n',
             'import re\nR = re.escape("x")\nfor k in ("ledger_path",):\n    reg.get(k)\n', 4),
            ("void/delattr-call", "quiet/delattr-call", "flip/delattr-call",
             'def f(o):\n    delattr(o, "x")\nfor k in ("ledger_path",):\n    reg.get(k)\n',
             'def f(o):\n    return hasattr(o, "x")\nfor k in ("ledger_path",):\n    reg.get(k)\n', 4),
            ("void/dunder-import-call", "quiet/dunder-import-call", "flip/dunder-import-call",
             'M = __import__("os", None)\nfor k in ("ledger_path",):\n    reg.get(k)\n',
             'NOTE = "__import__ named only inside a string constant"\n'
             'for k in ("ledger_path",):\n    reg.get(k)\n', 3),
            ("void/import-module-attribute-call", "quiet/import-module-attribute-call",
             "flip/import-module-attribute-call",
             'import importlib\nM = importlib.import_module("ledger_dynamic_plugin")\n'
             'for k in ("ledger_path",):\n    reg.get(k)\n',
             'import importlib\nX = importlib.machinery\nfor k in ("ledger_path",):\n    reg.get(k)\n',
             4),
            ("void/globals-bare-reference", "quiet/globals-bare-reference",
             "flip/globals-bare-reference",
             'def f():\n    return globals\nfor k in ("ledger_path",):\n    reg.get(k)\n',
             'globs = None\nfor k in ("ledger_path",):\n    reg.get(k)\n', 4),
            ("void/locals-name-binding", "quiet/locals-name-binding", "flip/locals-name-binding",
             'L = locals\nfor k in ("ledger_path",):\n    reg.get(k)\n',
             'locale = None\nfor k in ("ledger_path",):\n    reg.get(k)\n', 3),
            ("void/vars-name-binding", "quiet/vars-name-binding", "flip/vars-name-binding",
             'V = vars\nfor k in ("ledger_path",):\n    reg.get(k)\n',
             'variants = None\nfor k in ("ledger_path",):\n    reg.get(k)\n', 3),
            ("void/f-locals-read-reference", "quiet/f-locals-read-reference",
             "flip/f-locals-read-reference",
             'def f(frame):\n    return frame.f_locals\nfor k in ("ledger_path",):\n    reg.get(k)\n',
             'def f(frame):\n    return frame.f_back\nfor k in ("ledger_path",):\n    reg.get(k)\n', 4),
            ("void/sys-modules-read-reference", "quiet/sys-modules-read-reference",
             "flip/sys-modules-read-reference",
             'import sys\nM = sys.modules\nfor k in ("ledger_path",):\n    reg.get(k)\n',
             'import sys\nM = sys.platform\nfor k in ("ledger_path",):\n    reg.get(k)\n', 4)):
        files = dict(_FIXTURE_FILES, **{HOOK_LIBRARY: hook})
        rc, out = _gate(files)
        check(void_id, rc == 2 and "outside the accepted access forms" in out
              and "at line {} column".format(line) in out, (rc, out))
        rc, out = _gate(dict(_FIXTURE_FILES, **{HOOK_LIBRARY: twin}))
        check(quiet_id, rc == 0, (rc, out))
        rc, out = _gate_without_voiding(files)
        check(flip_id, rc == 0, (rc, out))

    # The two narrow voiding exceptions, the only forms that stop voiding because neither can rebind
    # a name or run code in the module: (a) an attribute named compile whose object is a name bound,
    # at module level and only there, by `import re` (or `import re as <alias>`) and never rebound
    # anywhere in the module; (b) a call __import__(<one string literal>) or
    # importlib.import_module(<one string literal>) whose literal names a module in
    # sys.stdlib_module_names, with no other arguments, __import__ only when neither __import__
    # nor __builtins__ has a binding occurrence anywhere in the module. Each excepted shape passes
    # quietly, and the
    # flip that removes the exceptions refuses the same module, so each row depends on its exception.
    for quiet_id, flip_id, hook, line in (
            ("exception/re-compile-import-re", "flip/re-compile-import-re",
             'import re\nR = re.compile("x")\nfor k in ("ledger_path",):\n    reg.get(k)\n', 4),
            ("exception/re-alias-compile", "flip/re-alias-compile",
             'import re as r\nR = r.compile("x")\nfor k in ("ledger_path",):\n    reg.get(k)\n', 4),
            ("exception/dunder-import-stdlib-literal", "flip/dunder-import-stdlib-literal",
             'H = __import__("hashlib")\nfor k in ("ledger_path",):\n    reg.get(k)\n', 3),
            ("exception/import-module-stdlib-literal", "flip/import-module-stdlib-literal",
             'import importlib\nJ = importlib.import_module("json")\n'
             'for k in ("ledger_path",):\n    reg.get(k)\n', 4)):
        files = dict(_FIXTURE_FILES, **{HOOK_LIBRARY: hook})
        rc, out = _gate(files)
        check(quiet_id, rc == 0, (rc, out))
        rc, out = _gate_without_void_exceptions(files)
        check(flip_id, rc == 2 and "outside the accepted access forms" in out
              and "at line {} column".format(line) in out, (rc, out))

    # The near neighbours of the two exceptions stay voided: compile through an alias of re that the
    # module later rebinds, a bare compile brought in by `from re import compile`, __import__ of a
    # literal that names no sys.stdlib_module_names module, __import__ of a variable, __import__
    # with a keyword argument, compile through a function-level import of re, __import__ bound by a
    # def, a class, an import alias, a from-import or a parameter, a module that assigns or reads
    # __builtins__, a write through an attribute __globals__, and a call of getattr or
    # __getattribute__ whose attribute name is a voiding literal or no string literal. The flip
    # that removes voiding entirely accepts each, so each row depends on the voiding rule.
    for void_id, flip_id, hook, line in (
            ("void/re-compile-rebound-alias", "flip/re-compile-rebound-alias",
             'import re\nR = re.compile("x")\nre = None\nfor k in ("ledger_path",):\n'
             '    reg.get(k)\n', 5),
            ("void/from-re-import-compile-call", "flip/from-re-import-compile-call",
             'from re import compile\nC = compile("x")\nfor k in ("ledger_path",):\n'
             '    reg.get(k)\n', 4),
            ("void/dunder-import-non-stdlib-literal", "flip/dunder-import-non-stdlib-literal",
             'M = __import__("not_a_stdlib_module_xyz")\nfor k in ("ledger_path",):\n'
             '    reg.get(k)\n', 3),
            ("void/dunder-import-variable-argument", "flip/dunder-import-variable-argument",
             'def f(name):\n    return __import__(name)\nfor k in ("ledger_path",):\n'
             '    reg.get(k)\n', 4),
            ("void/dunder-import-keyword-argument", "flip/dunder-import-keyword-argument",
             'M = __import__("hashlib", level=0)\nfor k in ("ledger_path",):\n    reg.get(k)\n', 3),
            ("void/function-level-import-re-compile", "flip/function-level-import-re-compile",
             'def f():\n    import re\n    return re.compile("x")\nfor k in ("ledger_path",):\n'
             '    reg.get(k)\n', 5),
            ("void/dunder-import-def-bound", "flip/dunder-import-def-bound",
             'def __import__(name):\n    return None\nM = __import__("hashlib")\n'
             'for k in ("ledger_path",):\n    reg.get(k)\n', 5),
            ("void/dunder-import-class-bound", "flip/dunder-import-class-bound",
             'class __import__:\n    pass\nM = __import__("hashlib")\n'
             'for k in ("ledger_path",):\n    reg.get(k)\n', 5),
            ("void/dunder-import-import-alias-bound", "flip/dunder-import-import-alias-bound",
             'import os as __import__\nM = __import__("hashlib")\nfor k in ("ledger_path",):\n'
             '    reg.get(k)\n', 4),
            ("void/dunder-import-from-import-bound", "flip/dunder-import-from-import-bound",
             'from importlib import __import__\nM = __import__("hashlib")\n'
             'for k in ("ledger_path",):\n    reg.get(k)\n', 4),
            ("void/dunder-import-parameter-bound", "flip/dunder-import-parameter-bound",
             'def f(__import__):\n    return __import__("hashlib")\nfor k in ("ledger_path",):\n'
             '    reg.get(k)\n', 4),
            ("void/dunder-import-builtins-assignment", "flip/dunder-import-builtins-assignment",
             '__builtins__ = {}\nM = __import__("hashlib")\nfor k in ("ledger_path",):\n'
             '    reg.get(k)\n', 4),
            ("void/builtins-name-reference", "flip/builtins-name-reference",
             'B = __builtins__\nfor k in ("ledger_path",):\n    reg.get(k)\n', 3),
            ("void/dunder-globals-attribute-write", "flip/dunder-globals-attribute-write",
             'def f(g):\n    g.__globals__["k"] = "x"\nfor k in ("ledger_path",):\n'
             '    reg.get(k)\n', 4),
            ("void/getattr-voiding-literal", "flip/getattr-voiding-literal",
             'import builtins\nE = getattr(builtins, "exec")\nfor k in ("ledger_path",):\n'
             '    reg.get(k)\n', 4),
            ("void/getattr-non-literal-name", "flip/getattr-non-literal-name",
             'def f(o, n):\n    return getattr(o, n)\nfor k in ("ledger_path",):\n'
             '    reg.get(k)\n', 4),
            ("void/dunder-getattribute-call", "flip/dunder-getattribute-call",
             'def f(o):\n    return o.__getattribute__("exec")\nfor k in ("ledger_path",):\n'
             '    reg.get(k)\n', 4)):
        files = dict(_FIXTURE_FILES, **{HOOK_LIBRARY: hook})
        rc, out = _gate(files)
        check(void_id, rc == 2 and "outside the accepted access forms" in out
              and "at line {} column".format(line) in out, (rc, out))
        rc, out = _gate_without_voiding(files)
        check(flip_id, rc == 0, (rc, out))

    # A from-import that imports or binds a voiding name pulls the callable or namespace out of its
    # module under an arbitrary local name (from builtins import exec as e; a later e(...) spells no
    # scanned name), so the binding itself voids, as import builtins followed by builtins.exec
    # already does; and a getattr call whose literal names getattr or __getattribute__ itself
    # returns the accessor bound to no scanned name, so that literal voids too. Each row is refused
    # with its check present and accepted by the flip that removes only that check.
    for void_id, flip_id, flip, hook, line in (
            ("void/from-import-exec-alias", "flip/from-import-exec-alias",
             _gate_without_from_import_voiding,
             'from builtins import exec as e\nfor k in ("ledger_path",):\n    reg.get(k)\n', 3),
            ("void/from-import-globals-alias", "flip/from-import-globals-alias",
             _gate_without_from_import_voiding,
             'from builtins import globals as g\nfor k in ("ledger_path",):\n    reg.get(k)\n', 3),
            ("void/from-import-sys-modules-alias", "flip/from-import-sys-modules-alias",
             _gate_without_from_import_voiding,
             'from sys import modules as mm\nfor k in ("ledger_path",):\n    reg.get(k)\n', 3),
            ("void/from-import-import-module-alias", "flip/from-import-import-module-alias",
             _gate_without_from_import_voiding,
             'from importlib import import_module as im\nfor k in ("ledger_path",):\n'
             '    reg.get(k)\n', 3),
            ("void/getattr-accessor-literal", "flip/getattr-accessor-literal",
             _gate_without_accessor_literal_voiding,
             'def f(o, p):\n    return getattr(o, "getattr") or getattr(p, "__getattribute__")\n'
             'for k in ("ledger_path",):\n    reg.get(k)\n', 4)):
        files = dict(_FIXTURE_FILES, **{HOOK_LIBRARY: hook})
        rc, out = _gate(files)
        check(void_id, rc == 2 and "outside the accepted access forms" in out
              and "at line {} column".format(line) in out, (rc, out))
        rc, out = flip(files)
        check(flip_id, rc == 0, (rc, out))

    # Each binding branch of _bound_anywhere that stops the __import__ exception on its own: the
    # module binds the name __import__ by exactly one construct (an except capture, a bare global
    # declaration, a match capture, a type-parameter name), so the call is not excepted and voids;
    # the flip that removes only that branch accepts the same module. The parameter, def-or-class
    # and import-alias branches are guarded by their rows above: each of those rows binds
    # __import__ by exactly one construct beside no other voiding shape, so it fails with its
    # branch removed too. No row can guard the store-or-delete branch: a stored or deleted
    # bare-name __import__ or __builtins__ is itself a voiding occurrence, so removing that branch
    # never changes the exit; the branch is kept for completeness of _bound_anywhere and is
    # covered by the occurrence check. The branch flips the self-test runs are exactly the four
    # removals below, the two match-capture narrowings after them, and the __builtins__
    # exception-condition drop after those.
    for void_id, flip_id, branch, hook, line in (
            ("void/dunder-import-except-bound", "flip/dunder-import-except-bound", "except",
             'try:\n    pass\nexcept ValueError as __import__:\n    pass\n'
             'M = __import__("hashlib")\nfor k in ("ledger_path",):\n    reg.get(k)\n', 7),
            ("void/dunder-import-global-declared", "flip/dunder-import-global-declared",
             "global-nonlocal",
             'def f():\n    global __import__\nM = __import__("hashlib")\n'
             'for k in ("ledger_path",):\n    reg.get(k)\n', 5),
            ("void/dunder-import-match-capture-bound", "flip/dunder-import-match-capture-bound",
             "match-capture",
             'def f(v):\n    match v:\n        case str() as __import__:\n            pass\n'
             'M = __import__("hashlib")\nfor k in ("ledger_path",):\n    reg.get(k)\n', 7),
            ("void/dunder-import-type-param-bound", "flip/dunder-import-type-param-bound",
             "type-parameter",
             'def f[__import__]():\n    return None\nM = __import__("hashlib")\n'
             'for k in ("ledger_path",):\n    reg.get(k)\n', 5)):
        files = dict(_FIXTURE_FILES, **{HOOK_LIBRARY: hook})
        rc, out = _gate(files)
        check(void_id, rc == 2 and "outside the accepted access forms" in out
              and "at line {} column".format(line) in out, (rc, out))
        rc, out = _gate_without_binding_branch(files, branch)
        check(flip_id, rc == 0, (rc, out))

    # The match-capture branch counts three forms: a MatchAs name, a MatchStar name and a
    # MatchMapping rest. The whole-branch removal above cannot tell a branch that keeps one form
    # and drops another from the full branch, so the star and the mapping-rest form each have
    # their own row, refused with the branch whole and accepted by the flip that narrows the
    # branch to the other two forms.
    def _match_without_star(node):
        if isinstance(node, ast.MatchAs) and node.name:
            return (node.name,)
        if isinstance(node, ast.MatchMapping) and node.rest:
            return (node.rest,)
        return ()

    def _match_without_rest(node):
        return (node.name,) if isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name else ()

    for void_id, flip_id, binds, hook, line in (
            ("void/dunder-import-match-star-bound", "flip/dunder-import-match-star-bound",
             _match_without_star,
             'def f(v):\n    match v:\n        case [*__import__]:\n            pass\n'
             'M = __import__("hashlib")\nfor k in ("ledger_path",):\n    reg.get(k)\n', 7),
            ("void/dunder-import-match-mapping-rest-bound",
             "flip/dunder-import-match-mapping-rest-bound", _match_without_rest,
             'def f(v):\n    match v:\n        case {**__import__}:\n            pass\n'
             'M = __import__("hashlib")\nfor k in ("ledger_path",):\n    reg.get(k)\n', 7)):
        files = dict(_FIXTURE_FILES, **{HOOK_LIBRARY: hook})
        rc, out = _gate(files)
        check(void_id, rc == 2 and "outside the accepted access forms" in out
              and "at line {} column".format(line) in out, (rc, out))
        rc, out = _gate_with_binding_branch(files, "match-capture", binds)
        check(flip_id, rc == 0, (rc, out))

    # A binding of __builtins__ with no bare-name or attribute occurrence the scan reads: the
    # bound object is the mapping a bare-name __import__ resolves through, so the __import__
    # exception applies only when neither __import__ nor __builtins__ has a binding occurrence
    # anywhere in the module. One row per binding construct, each beside an otherwise excepted
    # __import__("hashlib") call and a pin, refused with the condition whole and accepted by the
    # flip that drops __builtins__ from it. A stored or deleted bare-name __builtins__ has no row
    # here: the occurrence itself voids (void/dunder-import-builtins-assignment above), so that
    # module can never be accepted by this flip; a from-import binding of __builtins__ likewise
    # voids on the from-import itself (the void/from-import-* rows guard that check), so the
    # import-alias construct is exercised by its plain-import spelling.
    for void_id, flip_id, hook, line in (
            ("void/builtins-import-alias-blocks-exception",
             "flip/builtins-import-alias-blocks-exception",
             'import shim as __builtins__\nM = __import__("hashlib")\n'
             'for k in ("ledger_path",):\n    reg.get(k)\n', 4),
            ("void/builtins-def-bound-blocks-exception",
             "flip/builtins-def-bound-blocks-exception",
             'def __builtins__():\n    return None\nM = __import__("hashlib")\n'
             'for k in ("ledger_path",):\n    reg.get(k)\n', 5),
            ("void/builtins-class-bound-blocks-exception",
             "flip/builtins-class-bound-blocks-exception",
             'class __builtins__:\n    pass\nM = __import__("hashlib")\n'
             'for k in ("ledger_path",):\n    reg.get(k)\n', 5),
            ("void/builtins-parameter-bound-blocks-exception",
             "flip/builtins-parameter-bound-blocks-exception",
             'def f(__builtins__):\n    return None\nM = __import__("hashlib")\n'
             'for k in ("ledger_path",):\n    reg.get(k)\n', 5),
            ("void/builtins-except-bound-blocks-exception",
             "flip/builtins-except-bound-blocks-exception",
             'try:\n    pass\nexcept ValueError as __builtins__:\n    pass\n'
             'M = __import__("hashlib")\nfor k in ("ledger_path",):\n    reg.get(k)\n', 7),
            ("void/builtins-global-declared-blocks-exception",
             "flip/builtins-global-declared-blocks-exception",
             'def f():\n    global __builtins__\nM = __import__("hashlib")\n'
             'for k in ("ledger_path",):\n    reg.get(k)\n', 5),
            ("void/builtins-match-capture-blocks-exception",
             "flip/builtins-match-capture-blocks-exception",
             'def f(v):\n    match v:\n        case str() as __builtins__:\n            pass\n'
             'M = __import__("hashlib")\nfor k in ("ledger_path",):\n    reg.get(k)\n', 7),
            ("void/builtins-type-param-blocks-exception",
             "flip/builtins-type-param-blocks-exception",
             'def f[__builtins__]():\n    return None\nM = __import__("hashlib")\n'
             'for k in ("ledger_path",):\n    reg.get(k)\n', 5)):
        files = dict(_FIXTURE_FILES, **{HOOK_LIBRARY: hook})
        rc, out = _gate(files)
        check(void_id, rc == 2 and "outside the accepted access forms" in out
              and "at line {} column".format(line) in out, (rc, out))
        rc, out = _gate_without_builtins_blocker(files)
        check(flip_id, rc == 0, (rc, out))

    # A getattr call whose attribute name is a string literal outside the voiding and accessor
    # names cannot reach a voiding callable or namespace, so it does not void (the real hook
    # library holds many such calls).
    rc, out = _gate(dict(_FIXTURE_FILES, **{
        HOOK_LIBRARY: 'import os\nF = getattr(os, "O_PATH", 0)\nfor k in ("ledger_path",):\n'
                      '    reg.get(k)\n'}))
    check("quiet/getattr-plain-literal", rc == 0, (rc, out))

    # A class with any base, keyword or decorator: its metaclass's __prepare__ mapping can resolve
    # any name in the body, so a pin or a hand-off there is refused while a constant-key read in the
    # same body stays accepted; the flip that vets every class accepts each refused shape.
    for void_id, quiet_id, flip_id, hook, twin, line in (
            ("void/class-metaclass-prepare-pin", "quiet/class-metaclass-prepare-pin",
             "flip/class-metaclass-prepare-pin",
             'class M(type):\n    pass\nclass C(metaclass=M):\n    for k in ("ledger_path",):\n'
             '        V = reg.get(k)\n',
             'class C:\n    for k in ("ledger_path",):\n        V = reg.get(k)\n', 5),
            ("void/class-base-pin", "quiet/class-base-pin", "flip/class-base-pin",
             'class C(dict):\n    for k in ("ledger_path",):\n        V = reg.get(k)\n',
             'class C(dict):\n    V = reg.get("ledger_path")\n', 3),
            ("void/class-decorator-pin", "quiet/class-decorator-pin", "flip/class-decorator-pin",
             'def d(c):\n    return c\n@d\nclass C:\n    for k in ("ledger_path",):\n'
             '        V = reg.get(k)\n',
             'def d(c):\n    return c\n@d\nclass C:\n    V = reg.get("ledger_path")\n', 6),
            ("void/class-keyword-handoff", "quiet/class-keyword-handoff",
             "flip/class-keyword-handoff",
             'def _take(reg, root):\n    return reg.get("ledger_path")\nclass M(type):\n    pass\n'
             'class C(metaclass=M):\n    V = _take(reg, None)\n',
             'def _take(reg, root):\n    return reg.get("ledger_path")\nclass C:\n'
             '    V = _take(reg, None)\n', 6)):
        files = dict(_FIXTURE_FILES, **{HOOK_LIBRARY: hook})
        rc, out = _gate(files)
        check(void_id, rc == 2 and "outside the accepted access forms" in out
              and "at line {} column".format(line) in out, (rc, out))
        rc, out = _gate(dict(_FIXTURE_FILES, **{HOOK_LIBRARY: twin}))
        check(quiet_id, rc == 0, (rc, out))
        rc, out = _gate_every_class_vetted(files)
        check(flip_id, rc == 0, (rc, out))

    # A def's type parameter shadows a module-level name inside the def it parameterizes, so
    # def a[_take](...) never resolves its _take call to the module-level hand-off callee; an
    # unrelated type parameter leaves the hand-off accepted, and the flip that ignores type-parameter
    # bindings accepts the shadowed shape.
    hook_tp = ('def _take(reg, root):\n    return reg.get("ledger_path")\n'
               'def a[_take](root, reg):\n    return _take(reg, root)\n')
    files_tp = dict(_FIXTURE_FILES, **{HOOK_LIBRARY: hook_tp})
    rc, out = _gate(files_tp)
    check("void/type-param-shadowed-handoff-callee", rc == 2
          and "outside the accepted access forms" in out and "at line 4 column" in out, (rc, out))
    rc, out = _gate(dict(_FIXTURE_FILES, **{HOOK_LIBRARY: hook_tp.replace("def a[_take]", "def a[T]")}))
    check("quiet/type-param-shadowed-handoff-callee", rc == 0, (rc, out))
    rc, out = _gate_without_type_param_binds(files_tp)
    check("flip/type-param-shadowed-handoff-callee", rc == 0, (rc, out))

    # Each routing a scope-opening node owes the ENCLOSING scope, one row per routed part: a walrus in
    # a keyword-only default, a class base or a class keyword rebinds the enclosing loop key, and a
    # return-annotation read resolves against the enclosing scope, never an inner loop's pin, so
    # rerouting any of these parts to the inner scope makes its row fail.
    for case_id, hook, line in (
            ("route/kwonly-default-walrus-rebinds-loop-key",
             'def a(root, reg):\n    for k in ("ledger_path",):\n'
             '        def g(*, x=(k := "ledger_other")):\n            return x\n        reg.get(k)\n', 5),
            ("route/return-annotation-read-enclosing-scope",
             'def a(root, reg, k):\n    def g() -> reg.get(k):\n        for k in ("ledger_path",):\n'
             '            pass\n    return g()\n', 2),
            ("route/class-base-walrus-rebinds-loop-key",
             'def a(root, reg):\n    for k in ("ledger_path",):\n'
             '        class C((k := "ledger_other") and object):\n            pass\n        reg.get(k)\n',
             5),
            ("route/class-keyword-walrus-rebinds-loop-key",
             'def a(root, reg):\n    for k in ("ledger_path",):\n'
             '        class C(metaclass=(k := type)):\n            pass\n        reg.get(k)\n', 5)):
        rc, out = _gate(dict(_FIXTURE_FILES, **{HOOK_LIBRARY: hook}))
        check(case_id, rc == 2 and "outside the accepted access forms" in out
              and "at line {} column".format(line) in out, (rc, out))

    # The hand-off rule's accepted side: a registry name passed, positionally or by keyword, to a
    # module-level def whose matching parameter is a registry name needs no allowlist entry.
    hook_hand = ('def _take(reg, root):\n    return reg.get("ledger_path")\n'
                 'def a(root, reg):\n    return _take(reg, root)\n')
    rc, out = _gate(dict(_FIXTURE_FILES, **{HOOK_LIBRARY: hook_hand}))
    check("pass/helper-registry-parameter", rc == 0, (rc, out))
    hook_hand_kw = ('def _take(root, reg=None):\n    return reg.get("ledger_path")\n'
                    'def a(root, reg):\n    return _take(root, reg=reg)\n')
    rc, out = _gate(dict(_FIXTURE_FILES, **{HOOK_LIBRARY: hook_hand_kw}))
    check("pass/helper-keyword-registry-parameter", rc == 0, (rc, out))

    # The [[detect.registry-use]] allowlist: an entry whose anchor matches exactly one place excuses
    # the non-conforming use(s) on its lines and contributes its keys-read to the derived set.
    hook_call = ('def a(root, reg, helper):\n    return helper(reg)\n'
                 'def b(root, reg):\n    return reg.get("ledger_path")\n')
    use_entry = ('[[detect.registry-use]]\nline-anchor = "    return helper(reg)"\n'
                 'reason = "a fixture helper call"\nkeys-read = ["ledger_aux"]\nuses = ["helper(reg)"]\n')
    detect_aux = _FIXTURE_DETECT.replace('keys = ["ledger_path"]', 'keys = ["ledger_path", "ledger_aux"]')
    rc, out = _gate(dict(_FIXTURE_FILES, **{HOOK_LIBRARY: hook_call}),
                    _switch_text(detect=detect_aux + use_entry))
    check("pass/allowlist-excused-use", rc == 0, (rc, out))
    rc, out = _gate(dict(_FIXTURE_FILES, **{HOOK_LIBRARY: hook_call}),
                    _switch_text(detect=_FIXTURE_DETECT + use_entry))
    check("refuse/allowlist-key-undeclared", rc == 2 and "lacks 'ledger_aux'" in out, (rc, out))
    rc, out = _gate(dict(_FIXTURE_FILES, **{HOOK_LIBRARY: hook_call}), _switch_text(
        detect=detect_aux + use_entry.replace("    return helper(reg)", "    return nothing(reg)")))
    check("refuse/allowlist-anchor-unmatched", rc == 2 and "matches no line" in out, (rc, out))
    hook_twice = ('def a(root, reg, helper):\n    return helper(reg)\ndef b(root, reg, helper):\n'
                  '    return helper(reg)\ndef c(root, reg):\n    return reg.get("ledger_path")\n')
    rc, out = _gate(dict(_FIXTURE_FILES, **{HOOK_LIBRARY: hook_twice}),
                    _switch_text(detect=detect_aux + use_entry))
    check("refuse/allowlist-anchor-ambiguous", rc == 2 and "matches 2 places" in out, (rc, out))
    # A multi-line anchor pins one of several identical lines, so an entry survives line-number drift.
    two_entries = ('[[detect.registry-use]]\n'
                   'line-anchor = "def a(root, reg, helper):\\n    return helper(reg)"\n'
                   'reason = "a fixture helper call"\nkeys-read = []\nuses = ["helper(reg)"]\n'
                   '[[detect.registry-use]]\n'
                   'line-anchor = "def b(root, reg, helper):\\n    return helper(reg)"\n'
                   'reason = "a fixture helper call"\nkeys-read = []\nuses = ["helper(reg)"]\n')
    rc, out = _gate(dict(_FIXTURE_FILES, **{HOOK_LIBRARY: hook_twice}),
                    _switch_text(detect=_FIXTURE_DETECT + two_entries))
    check("pass/allowlist-multiline-anchor", rc == 0, (rc, out))
    rc, out = _gate(dict(_FIXTURE_FILES, **{HOOK_LIBRARY: hook_call}), _switch_text(
        detect=detect_aux + use_entry.replace('reason = "a fixture helper call"', 'reason = "  "')))
    check("refuse/allowlist-empty-reason", rc == 2 and "reason must be a non-empty string" in out, (rc, out))
    stale_entry = ('[[detect.registry-use]]\nline-anchor = "def b(root, reg):"\n'
                   'reason = "anchors a line with no non-conforming use"\nkeys-read = []\n'
                   'uses = ["helper(reg)"]\n')
    rc, out = _gate(dict(_FIXTURE_FILES, **{HOOK_LIBRARY: hook_call}), _switch_text(
        detect=detect_aux + use_entry + stale_entry))
    check("refuse/allowlist-stale-entry", rc == 2 and "excuses no registry use" in out, (rc, out))
    rc, out = _gate(dict(_FIXTURE_FILES, **{HOOK_LIBRARY: hook_call}), _switch_text(
        detect=detect_aux + use_entry.replace('keys-read = ["ledger_aux"]',
                                              'keys-read = ["ledger_aux"]\nnote = "x"')))
    check("refuse/malformed-registry-use-entry", rc == 2 and "must hold exactly" in out, (rc, out))
    rc, out = _gate(dict(_FIXTURE_FILES, **{HOOK_LIBRARY: hook_call}), _switch_text(
        detect=detect_aux + use_entry.replace('uses = ["helper(reg)"]', 'uses = []')))
    check("refuse/allowlist-empty-uses", rc == 2 and "uses must be a non-empty list" in out, (rc, out))

    # Refused uses are keyed by occurrence (line and column): an entry excuses only the uses on its
    # anchored lines whose exact source text it declares, so a second, different use on the same line
    # is not excused by an entry that does not declare it, and a declared text no use matches is stale.
    hook_two_uses = ('def a(root, reg, helper):\n    return helper(reg) or len(reg)\n'
                     'def b(root, reg):\n    return reg.get("ledger_path")\n')
    entry_two = ('[[detect.registry-use]]\nline-anchor = "    return helper(reg) or len(reg)"\n'
                 'reason = "a fixture helper call beside a length test"\nkeys-read = []\n'
                 'uses = ["helper(reg)", "len(reg)"]\n')
    rc, out = _gate(dict(_FIXTURE_FILES, **{HOOK_LIBRARY: hook_two_uses}),
                    _switch_text(detect=_FIXTURE_DETECT + entry_two))
    check("pass/allowlist-occurrence-uses", rc == 0, (rc, out))
    rc, out = _gate(dict(_FIXTURE_FILES, **{HOOK_LIBRARY: hook_two_uses}), _switch_text(
        detect=_FIXTURE_DETECT + entry_two.replace('uses = ["helper(reg)", "len(reg)"]',
                                                   'uses = ["helper(reg)"]')))
    check("refuse/allowlist-undeclared-second-use",
          rc == 2 and "does not declare the refused use 'len(reg)' at line 2 column" in out, (rc, out))
    rc, out = _gate(dict(_FIXTURE_FILES, **{HOOK_LIBRARY: hook_call}), _switch_text(
        detect=detect_aux + use_entry.replace('uses = ["helper(reg)"]',
                                              'uses = ["helper(reg)", "str(reg)"]')))
    check("refuse/allowlist-stale-use-declaration",
          rc == 2 and "declares the use 'str(reg)' in uses but no refused use" in out, (rc, out))

    # [detect.non-record-keys]: a derived key that names no record path passes when excluded with a
    # reason; a key in both lists, a stale exclusion and a malformed table refuse.
    hook_aux = 'def a(root, reg):\n    return reg.get("ledger_path") or reg.get("ledger_aux")\n'
    excl = '[detect.non-record-keys]\nledger_aux = "a fixture key that names no record path"\n'
    rc, out = _gate(dict(_FIXTURE_FILES, **{HOOK_LIBRARY: hook_aux}),
                    _switch_text(detect=_FIXTURE_DETECT + excl))
    check("pass/non-record-key-excluded", rc == 0, (rc, out))
    both = '[detect.non-record-keys]\nledger_path = "also declared as a record key"\n'
    rc, out = _gate(switch=_switch_text(detect=_FIXTURE_DETECT + both))
    check("refuse/key-in-both-lists", rc == 2 and "repeats 'ledger_path' from [detect] keys" in out, (rc, out))
    ghost = '[detect.non-record-keys]\nledger_ghost = "never read by the fixture hook library"\n'
    rc, out = _gate(switch=_switch_text(detect=_FIXTURE_DETECT + ghost))
    check("refuse/stale-non-record-key",
          rc == 2 and "lists 'ledger_ghost' but no registry read" in out, (rc, out))
    badexcl = 'non-record-keys = ["ledger_aux"]\n'
    rc, out = _gate(switch=_switch_text(detect=_FIXTURE_DETECT + badexcl))
    check("refuse/malformed-non-record-keys",
          rc == 2 and "[detect.non-record-keys] must map" in out, (rc, out))

    # A module-level constant that reaches a resolver is no registry use at all.
    hook = ('TAIL = "ledger-side.txt"\ndef a(root, reg):\n'
            '    return _orch_path(root, TAIL) or _orch_path(root, reg.get("ledger_path"))\n')
    rc, out = _gate(dict(_FIXTURE_FILES, **{HOOK_LIBRARY: hook}))
    check("ignore/enclosing-plain-constant", rc == 0, (rc, out))

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
          "nested, duplicated, unknown-disposition or indirect-via-cycle list exit 2; the hook library is "
          "scanned whole (module, class bodies, decorators, lambdas, comprehensions and nested defs): every "
          "accepted access form (R.get(K), R.get(K, default), R[K], (R or {{}}).get(K), (R or {{}})[K], a "
          "literal-loop key bound in the read's own scope, and a hand-off, with no starred or double-starred "
          "argument, to an undecorated module-level def whose matching parameter is a registry name) derives "
          "its key, and [detect] keys is checked as a superset of the derived set; scopes follow run-time "
          "evaluation (a comprehension target pins keys only inside the comprehension while its first "
          "iterable belongs to the enclosing scope, a def's decorators, defaults and annotations and a "
          "lambda's defaults belong to the enclosing scope, and a class body is its own scope), a loop key "
          "a nested scope rebinds by nonlocal or global, or that a global, match or except capture also "
          "binds, never pins, a type-parameter list and a type-alias statement accept no registry use, a "
          "def's type parameter shadows a module-level hand-off callee, every pin and hand-off is voided "
          "in a module where the scan reads any of a fixed list of shapes: a bare-name or attribute "
          "occurrence (read-only included) of exec, eval, compile, "
          "setattr, delattr, __import__ or import_module, such an occurrence of "
          "globals, locals, vars, modules, _getframe, f_locals, f_globals, __dict__, __globals__ or "
          "__builtins__, a from-import that imports or binds any of those names under its own name or "
          "an alias, a call of getattr or __getattribute__ whose attribute name is a string literal "
          "naming a voiding callable or namespace or getattr or __getattribute__ itself, or is not a "
          "string literal at all, a star import, or "
          "a store or delete to an attribute rooted in an import-bound name (each voided shape has a "
          "quiet twin that passes and a flip that accepts it with the voiding removed, and the "
          "from-import and accessor-literal shapes each have a flip that removes only that check), "
          "excepting "
          "exactly two forms that can neither rebind a name nor run code in the module: an attribute "
          "named compile whose object is a name bound, at module level and only there, by import re "
          "(or import re as <alias>) and never rebound anywhere in the module, and a call "
          "__import__(<one string literal>) or importlib.import_module(<one string literal>) whose "
          "literal names a module in sys.stdlib_module_names, with no other arguments, __import__ only "
          "when neither __import__ nor __builtins__ has a binding occurrence anywhere in the module, "
          "every binding branch counted there except store-or-delete (a parameter, a def or class "
          "name, an import alias, an except capture, a global or nonlocal declaration, a match "
          "capture in its as, star and mapping-rest forms, a type-parameter name) guarded by a row "
          "that fails with that branch removed, while no row can guard the store-or-delete branch "
          "(a stored or deleted bare-name __import__ or __builtins__ is itself a voiding occurrence, "
          "so that branch never changes the exit; it is kept for completeness of _bound_anywhere and "
          "is covered by the occurrence check), the flips the self-test runs removing the except, "
          "global-nonlocal, match-capture and type-parameter branches, narrowing the match-capture "
          "branch to drop its star and its mapping-rest form, and dropping __builtins__ from the "
          "exception condition (each excepted "
          "shape passes and is refused by the flip that removes the exceptions, and its near "
          "neighbours, a rebound alias of re, a bare compile from re, a non-stdlib literal, a "
          "variable argument, a keyword argument, a function-level import of re, each binding of "
          "the name __import__ or __builtins__ and a __builtins__ reference or assignment, stay "
          "voided), and a pin or "
          "hand-off in the body of a class with any base, keyword or decorator is refused while "
          "constant-key reads stay accepted; every registry use outside those forms (a non-constant key, "
          "another method or attribute, a call argument through any other callee or parameter, a match, "
          "except or loop capture, an alias, a splat, a conditional or or-fallback receiver, and the rest) "
          "exits 2 naming its line and column unless a [[detect.registry-use]] entry anchors it exactly "
          "once and declares its exact source text in uses (an anchor matching zero or several places, a "
          "blank reason, a stale entry, an undeclared second use on an anchored line and a stale use "
          "declaration each exit 2; asserted keys-read join the derived set); [detect.non-record-keys] "
          "excludes derived keys that name no record path (a key in both lists or an exclusion nothing "
          "derives exits 2); the residual: a registry object reached under a name not in "
          "HOOK_REGISTRY_NAMES is not seen, a key used outside the hook library is not seen, an "
          "allowlisted use is covered only by its asserted keys-read, and any other run-time route "
          "that reaches the module namespace or a voiding callable without a bare-name, attribute, "
          "from-import or literal-accessor occurrence the scan reads is not seen (a getattr bound to "
          "another name, an unnamed functools.partial of getattr, operator.attrgetter, "
          "gc.get_referents, a deserializer such as pickle that runs code, or a rebinding by code "
          "outside the hook library; the live hook library is clean today by this gate's own run); the "
          "parse site handles "
          "RecursionError, MemoryError and ValueError; the live tree passes)".format(len(EXECUTED)))
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
