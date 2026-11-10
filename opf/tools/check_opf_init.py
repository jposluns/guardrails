#!/usr/bin/env python3
"""OPF source-only init gate, exercised exclusively through isolated temporary fixtures.

Both the default entry and --self-test run the fixture suite. There is no live-adopter mutation
leg and no root option. Unlike doctor fixtures, init fixtures need no committed HEAD: initialization
creates sources, leaving staging, committing, and rendering to the adopter.

The dedicated gate launches opf.py isolated (-I -B). The dispatcher self-test reuses _suite with
its own main callable. Fixtures are created beneath a fresh temporary directory and removed afterward.
Missing git, unusable temporary storage, or a harness failure returns 2, never a clean skip.

Exit convention: 0 observed assertions pass; 1 an assertion fails; 2 cannot evaluate the harness.
Coverage is representative: it does not exhaust concurrent namespace changes or filesystem failures.

Construction-side residual (QA round 13, requirement E): the route gate reads spelled
references and constructions in opf.py alone. A rendered-type construction it cannot read,
one in a sibling module, one reached through introspection or minted at runtime, or any
other spelling of a construction the reference scan does not parse, is NOT chased with
further static rules: it is covered at the OUTPUT by requirement A's rendering (opf.py's
constructors format only registered _INIT_TEMPLATES members and render any other template
as a value, and the refusal handler accepts a message or restore line only with its
constructor's rendering proof, re-rendering everything else at the point of output), and
this suite drives that closure directly with inheriting-subclass, overriding-subclass and
swapped-args vectors.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: check_opf_init.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import ast
import contextlib
import errno
import io
import json
import os
import shlex
import shutil
import stat
import string
import subprocess
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

EXIT_OK = 0
EXIT_FINDING = 1
EXIT_ERROR = 2

# The first-parent scan limit, worded identically in the init refusal, the successful init output
# and OPF-QUICKSTART.md (QA round 2 MAJOR 1). Asserted literally against whitespace-normalized
# text, so the earlier "unmerged side branch" wording, which missed a store created and deleted
# on a side branch that WAS merged, fails every site check.
_SIDELINE_LIMIT = ("a store that never reached a tree on HEAD's first-parent line (for example "
                   "one created and deleted on a side branch, merged or not) is not detected")


# The current-path limit (QA round 3 MINOR 4), worded identically at the same three sites and in
# the docstring: the pathspecs are the root's PRESENT prefix and a pathspec walk follows no rename.
_PATH_LIMIT = ("the scan checks the root's current path only (a store committed under another "
               "directory, for example before a rename, is not detected)")


# Homes-rule scan exemptions (checked by this gate over its own source too): the fixture suite's
# driver chain down to the homes-rule analysis helpers, which legitimately carry the rule-name
# strings they scan for. Their bodies are not scanned; registering them asserts they are
# test-only, which this gate trusts and does not check.
_HOMES_SELFTEST_EXEMPT = (
    "_gen2_cli_vectors",
    "_homes_module_binding_findings",
    "_homes_module_summary",
    "_homes_rule_call",
    "_homes_rule_unit_findings",
    "_homes_rule_violations",
    "_init_binding_allowed",
    "_init_callee_spellings",
    "_is_run_init_call",
    "_self_test",
    "_self_test_lifecycle",
    "_store_write_findings",
    "_suite",
    "_suite_isolated",
)


def _states_sideline_limit(text):
    flat = " ".join(text.split())
    return _SIDELINE_LIMIT in flat and "unmerged" not in flat


def _states_path_limit(text):
    return _PATH_LIMIT in " ".join(text.split())


def _limit_address_space():
    """preexec_fn for a child that probes a FIFO: a 4 GiB address-space cap (RLIMIT_AS), so a
    regression that reads the path cannot exhaust host memory; the caller's timeout bounds time."""
    import resource
    resource.setrlimit(resource.RLIMIT_AS, (4 << 30, 4 << 30))


def _run_init(argv):
    """Run the real dispatcher isolated; preserve its output for discriminating assertions."""
    script = Path(__file__).resolve().parent / "opf.py"
    proc = subprocess.run(
        [sys.executable, "-I", "-B", str(script)] + list(argv),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=120)
    if proc.returncode not in (EXIT_OK, EXIT_FINDING, EXIT_ERROR):
        raise OSError("opf child returned unexpected status {}".format(proc.returncode))
    sys.stdout.write(proc.stdout)
    sys.stderr.write(proc.stderr)
    return proc.returncode


def _snapshot(root):
    """Read the complete small fixture tree, including hidden files, links, and empty directories.

    A .git directory (top-level or nested) is excluded and never descended into: ambient git
    bookkeeping (for example .git/index, which init's read-only git status / ls-files refreshes)
    is not part of the working-tree state the preservation checks assert.
    """
    result = {}

    def walk(directory):
        for path in sorted(directory.iterdir()):
            if path.name == ".git":
                continue
            st = path.lstat()
            relpath = path.relative_to(root).as_posix()
            mode = stat.S_IMODE(st.st_mode)
            if stat.S_ISDIR(st.st_mode):
                result[relpath] = ("directory", mode)
                walk(path)
            elif stat.S_ISREG(st.st_mode):
                result[relpath] = ("file", mode, path.read_bytes())
            elif stat.S_ISLNK(st.st_mode):
                result[relpath] = ("symlink", os.readlink(path))
            else:
                result[relpath] = ("special", st.st_mode)

    walk(root)
    return result


_ROUTE_EMITTER = "_init_echo"
_ROUTE_RENDERED = ("_InitRestoreLine", "_InitPriorStoreRefusal")
_ROUTE_PINNED = ("_init_echo", "_init_inert", "_init_inert_json",
                 "_init_exact_str", "_InitRestoreLine", "_InitPriorStoreRefusal")
# The template-bearing parameters of each rendered type, by POSITIONAL index and
# (same name) keyword, derived from the real constructor signatures in opf.py:
# _InitRestoreLine.__new__(cls, template, *values) carries one template, at
# positional index 0 / keyword "template" (values is a *vararg, each rendered at
# construction); _InitPriorStoreRefusal.__init__(self, template, values,
# restore_lines, remedy_template="", remedy_values=(), partial=False) carries the
# template at index 0 / keyword "template" and the remedy template at index 3 /
# keyword "remedy_template". The gate reads every one of these and refuses a splat
# that could reach any of them (QA round 12 B: a positional remedy template and a
# ** splat each skipped the round-11 rule, which read only args[0] and the
# remedy_template keyword).
_ROUTE_RENDERED_TEMPLATES = dict(
    _InitRestoreLine={0: "template"},
    _InitPriorStoreRefusal={0: "template", 3: "remedy_template"},
)
_ROUTE_BANNED = frozenset(["print", "builtins", "stdout", "stderr",
                           "write", "writelines", "warnings", "logging"])

# The PERMITTED-REFERENCE allowlist (QA round 9, requirement A: the round-8
# permitted-CALL gate checked only call targets, so a spelled output function
# passed whole as an ARGUMENT -- sorted(rest, key=os.system), key=input,
# key=sys.displayhook, key=sys.exit, json.dumps(default=os.system),
# rows.sort(key=os.system) -- and an ordinary import alias of a permitted name
# -- from os import writev as ascii -- rebound an allowed spelling to a writer,
# each reaching a descriptor with zero violations). Every Name and every
# attribute-chain reference in every function reachable from _cmd_init must now
# resolve (_route_call_spelling) to a name the function itself binds, to a
# module-level reachable definition, or to an entry in this reviewed roster,
# SCOPED to the one function the real init source uses it in: an allowed
# spelling in one function is a violation in another, str.__new__ is permitted
# in _InitRestoreLine alone, and a binding (an import alias included) of a
# rostered external base anywhere on the graph or at module level, other than
# the seven pinned module imports, is a violation. The spellings are
# deliberately variable-name-specific (journal._open_parent, touched.out.split):
# a rename in opf.py fails the gate until this roster is re-reviewed.
_ROUTE_ALLOWED_REFS = dict(
    _InitPriorStoreRefusal=frozenset([
        "RuntimeError", "_INIT_TEMPLATES", "remedy_template.format",
        "self._opf_rendered_message", "self.restore_lines", "str",
        "str-literal.join", "super", "super().__init__", "template.format",
        "type",
    ]),
    _InitRestoreLine=frozenset([
        "_INIT_TEMPLATES", "line._opf_line_rendered", "str", "str-literal.join",
        "str.__new__", "template.format", "type",
    ]),
    _cmd_init=frozenset([
        "BaseException", "EXIT_MALFORMED", "str", "str-literal.format", "type",
        "type().__name__",
    ]),
    _init_main=frozenset([
        "BaseException", "EXIT_MALFORMED", "EXIT_OK", "Exception", "Path",
        "RuntimeError",
        "_opf_emit.emit_checked", "_opf_emit.emit_checked().encode",
        "_opf_init._manifest_model", "_opf_init.bootstrap_documents",
        "_opf_init.bootstrap_documents().items",
        "_opf_oplock.OpLockError", "_opf_oplock.acquire_init_operation",
        "_opf_oplock.attach_init_lease", "_opf_oplock.detach_init_lease",
        "_opf_oplock.release_init_holder", "_opf_oplock.release_operation",
        "_opf_store.DEFAULT_MACHINE_SUBDIR", "_opf_store.LOCAL_POINTER_REL",
        "_opf_store.NOT_ADOPTED", "_opf_store.POINTER_REL",
        "_opf_store.RESOLVED", "_opf_store.init_writes_homes2",
        "_opf_store.WORKING_DIRNAME", "_opf_store._close_fd_exc_safe",
        "_opf_store._journal", "_opf_store._journal._close_fd_quietly",
        "_opf_store._open_dir_nofollow", "_opf_store.homes_generation",
        "_opf_store.render_homes_gitignore",
        "_opf_store.render_homes_gitignore().encode",
        "_opf_store.resolve_store", "all",
        "any", "ascii", "cap._released", "dict",
        "exc.restore_lines", "held._released", "held._spent",
        "journal._check_rel", "journal._lstat_at", "journal._lstat_contained",
        "journal._open_parent", "journal.require_containment", "json.dumps",
        "len", "list", "os.fsync", "os.mkdir", "os.path.abspath",
        "path.startswith", "payloads.items", "res.detail", "res.status", "set",
        "str", "str-literal.format", "str-literal.join", "text.encode", "type",
        "type().__name__", "unwind.append", "uuid.uuid4", "val.startswith",
    ]),
    _init_bare=frozenset([
        "_INIT_BARE_SAFE", "all",
    ]),
    _init_create=frozenset([
        "Exception", "RuntimeError", "_opf_store._close_fd_exc_safe",
        "_opf_store._journal", "journal._open_parent",
        "journal._recreate_file", "os.fsync", "str-literal.format",
    ]),
    _init_exact_str=frozenset([
        "issubclass", "str", "str.__str__", "type",
    ]),
    _init_git=frozenset([
        "RuntimeError", "_opf_observe._run_git", "result.completed",
        "result.err", "result.out", "result.rc", "str", "str-literal.format",
    ]),
    _init_glob_escape=frozenset([
        "str-literal.join",
    ]),
    _init_homes_gitignore_inspect=frozenset([
        "RuntimeError", "_opf_write_guard.WriteGuardError",
        "_opf_write_guard.inspect_homes_gitignore", "str-literal.format",
        "str-literal.join",
    ]),
    _init_homes_gitignore_verify=frozenset([
        "RuntimeError", "_opf_write_guard.WriteGuardError",
        "_opf_write_guard.verify_homes_gitignore_effective", "str-literal.format",
        "str-literal.join",
    ]),
    _init_history_rels=frozenset([
        "_opf_store.POINTER_REL", "_opf_store.WORKING_DIRNAME",
        "pointer_path.as_posix", "working_path.as_posix",
    ]),
    _init_inert=frozenset([
        "_INIT_INERT_SAFE", "ord", "str-literal.format", "str-literal.join",
    ]),
    _init_inert_json=frozenset([
        "bool", "dict", "float", "int", "isinstance", "list", "tuple", "type",
        "value.items",
    ]),
    _init_inventory=frozenset([
        "Exception", "RuntimeError", "_INIT_MAX_DEPTH", "_INIT_MAX_ENTRIES",
        "_INIT_MAX_NAME_BYTES", "_opf_store.WORKING_DIRNAME",
        "_opf_store._close_fd_exc_safe", "_opf_store._journal", "ascii",
        "entry.name", "journal._lstat_at", "len", "os.O_DIRECTORY",
        "os.O_NOFOLLOW", "os.O_RDONLY", "os.fsencode", "os.open", "os.scandir",
        "os.stat", "rows.append", "rows.sort", "st.st_mode", "stat.S_ISDIR",
    ]),
    _init_kind=frozenset([
        "st.st_mode", "stat.S_ISDIR", "stat.S_ISLNK", "stat.S_ISREG",
    ]),
    _init_line_rendered=frozenset([
        "AttributeError", "line._opf_line_rendered", "type",
    ]),
    _init_no_prior_store=frozenset([
        "FileNotFoundError", "OSError", "RuntimeError", "UnicodeError",
        "_opf_check.COUNTERS_NAME", "_opf_observe._run_git",
        "_opf_store.MANIFEST_NAME", "all", "any", "ascii", "commits.append",
        "decided.add", "entry.partition", "entry.startswith", "enumerate",
        "extra_paths.append", "grafts_raw.decode", "grafts_st.st_size",
        "grafts_text.endswith", "head.rc", "held.append", "len",
        "listing.out.split", "meta.split", "name.split", "os.fsdecode",
        "os.fsencode", "os.lstat", "os.path.isabs", "os.path.join",
        "os.readlink", "parent.out.strip", "parent.rc", "paths.add",
        "probe.out.split", "probe2.out.split", "raw.decode", "read().out",
        "read().out.strip", "records.append", "rel.split",
        "restore_lines.append", "result.completed", "result.err", "result.rc",
        "root.relative_to", "set", "sorted", "stores.add", "str",
        "str-literal.format", "str-literal.join", "tokens.pop", "touched.out",
        "touched.out.endswith", "touched.out.split",
    ]),
    _init_observed=frozenset([
        "Exception", "_opf_store._close_fd_exc_safe", "_opf_store._journal",
        "ascii", "journal._lstat_contained", "journal._open_parent",
        "journal._read_at", "len", "list", "opened.st_nlink", "rows.append",
        "st.st_mode", "st.st_size", "stat.S_ISREG",
    ]),
    _init_refusal_message=frozenset([
        "AttributeError", "exc._opf_rendered_message", "str", "type",
    ]),
    _init_repo=frozenset([
        "Path", "RuntimeError", "_opf_observe._git_path",
        "_opf_store._journal._close_fd_propagating",
        "_opf_store._open_dir_nofollow", "len", "os.fsdecode",
        "os.path.abspath", "os.path.isabs", "raw.split", "root.parents",
    ]),
    _init_same_root=frozenset([
        "RuntimeError", "_opf_store._close_fd_exc_safe",
        "_opf_store._open_dir_nofollow", "current.st_dev", "current.st_ino",
        "opened.st_dev", "opened.st_ino", "os.fstat",
    ]),
    _init_unignored=frozenset([
        "RuntimeError", "_opf_write_guard.init_ignore_refusal", "root.relative_to",
    ]),
    _init_untracked=frozenset([
        "RuntimeError", "os.fsdecode", "root.relative_to", "sorted", "str",
        "str-literal.format",
    ]),
)

# The bases that resolve OUTSIDE a reachable function: builtins and modules.
# Any binding of one of these names, at module level or inside a reachable
# function, is a violation unless it is that name's single pinned import form
# below (QA round 9, codex MAJOR 1: from os import writev as ascii bound a
# writer to a permitted spelling and passed the call gate).
_ROUTE_EXTERNAL_BASES = frozenset([
    "AttributeError", "BaseException", "Exception", "FileNotFoundError", "OSError", "Path",
    "RuntimeError", "UnicodeError", "all", "any", "ascii", "bool", "dict", "enumerate",
    "float", "int", "isinstance", "issubclass", "json", "len", "list", "ord", "os", "set",
    "sorted", "stat",
    "str", "super", "tuple", "type", "uuid",
    "_opf_check", "_opf_emit", "_opf_init", "_opf_observe", "_opf_oplock",
    "_opf_store", "_opf_write_guard",
])

_ROUTE_EXTERNAL_IMPORTS = dict(
    os=("import", "os"),
    json=("import", "json"),
    stat=("import", "stat"),
    Path=("from", "pathlib", "Path"),
    _opf_init=("import", "_opf_init"),
    _opf_oplock=("import", "_opf_oplock"),
    uuid=("import", "uuid"),
)

# The fresh-init entries outside opf.py that choose a store's homes generation. opf.py's _init_main
# is held to the one rule by its roster above (it admits _opf_store.init_writes_homes2, never
# SUPPORTED_HOMES). _homes_rule_violations never reads a closed call-site list for coverage: it
# DISCOVERS every REFERENCE to run_init_operation in the sources it is given (every module-level
# statement and every module-level function, nested functions and lambdas included; the only
# unscanned bodies are the functions a module's own _HOMES_SELFTEST_EXEMPT registry declares,
# trusted to be test-only and never checked for reachability) and is fail-closed over FORM: a
# reference that is not the callee of a direct call of the imported name (an alias, a getattr
# string, a functools.partial, a __call__ spelling, a rebinding, a passed value) is a finding, and
# each direct call must pass homes2 as exactly <alias>.init_writes_homes2() with no arguments and
# no * or ** splat, where <alias> is a name the module binds with a module-level `import _opf_store
# [as alias]` (in the module body or a module-level try). The module-level import of
# run_init_operation must be unaliased, so no string other than "run_init_operation" reaches the
# callee. A module-level binding of the store alias or of the callee's name other than its one
# import (a match capture, a with target, a type alias, a later def or import, any form the one
# binding-name function reads), or a global declaration of either, is a finding in any unit, and so
# is any binding of either name in the calling unit; a rebinding by a spelling this scan does not
# read (listed in _homes_rule_violations' docstring) is a disclosed residual. The production unit
# holding such a call must name SUPPORTED_HOMES nowhere -- not as a name, not as an attribute, and
# not as the string "SUPPORTED_HOMES" (which a getattr read would carry). No production code in any
# scanned module may bind init_writes_homes2 or SUPPORTED_HOMES (assignment to a name or an
# attribute, a del, an import alias, a def, a class, a parameter, a loop, with, except or match
# target, a type alias), write any attribute of a store alias, reach a store alias's namespace
# through setattr, delattr, vars(<alias>) or <alias>.__dict__, or carry the exact string
# "init_writes_homes2" (a dynamic rebinding would ride it); the one allowed binding is the store
# module's own module-level definition. The rows below are the minimum PRESENCE roster: each named
# function must still exist exactly once at module level and call run_init_operation exactly once,
# so a required entry renamed or dropped stays a finding even though discovery holds whatever
# replaced it. Rows are (file in this directory, function).
_HOMES_RULE_SITES = (
    ("_opf_adopt_apply.py", "_init_store_run"),
    ("_opf_init_operation.py", "init_operation"),
    ("_opf_init_operation.py", "_child_main"),
)


def _homes_rule_call(node, aliases):
    """True only for the exact spelling <alias>.init_writes_homes2() with no arguments, where
    <alias> is a name bound by a module-level `import _opf_store [as alias]`."""
    return (isinstance(node, ast.Call) and not node.args and not node.keywords
            and isinstance(node.func, ast.Attribute) and node.func.attr == "init_writes_homes2"
            and isinstance(node.func.value, ast.Name) and node.func.value.id in aliases)


def _is_run_init_call(node):
    """True for any call whose callee is named run_init_operation, plain or as an attribute."""
    return (isinstance(node, ast.Call)
            and ((isinstance(node.func, ast.Name) and node.func.id == "run_init_operation")
                 or (isinstance(node.func, ast.Attribute)
                     and node.func.attr == "run_init_operation")))


def _definition_time_exprs(node):
    """The child expressions of a def that the interpreter evaluates AT DEFINITION TIME, in
    the scope that holds the def statement: the decorator expressions and the argument
    defaults (positional and keyword-only)."""
    spec = node.args
    return (list(node.decorator_list) + list(spec.defaults)
            + [default for default in spec.kw_defaults if default is not None])


_HOMES_EXEMPT_NAME = "_HOMES_SELFTEST_EXEMPT"


def _homes_exempt_registry(tree):
    """The module's declared homes-rule exemption registry, as (names, malformed): the single
    module-level statement `_HOMES_SELFTEST_EXEMPT = ("name", ...)` -- a plain assignment to that
    one plain name, its value a tuple of unique nonempty string constants, placed before the
    first module-level def and bound nowhere else in the file in any form (assignment, del,
    aug-assign, import alias, def, class, parameter, loop, with or except target, global or
    nonlocal declaration). A module with no registry returns ((), None); a module whose
    declaration breaks any of these rules returns (None, reason): the declaration must be
    textually unambiguous or the module cannot be judged at all."""
    statements = [node for node in tree.body
                  if isinstance(node, ast.Assign)
                  and any(isinstance(target, ast.Name) and target.id == _HOMES_EXEMPT_NAME
                          for target in node.targets)]
    other_binds = [node for node in ast.walk(tree)
                   if _binds_name(node, _HOMES_EXEMPT_NAME)
                   and not any(node is stmt for stmt in statements)]
    lines = _line_map([tree]) if other_binds else {}
    if not statements:
        if other_binds:
            return None, "bound outside a single module-level registry assignment (line {})".format(
                lines[id(other_binds[0])])
        return (), None
    if other_binds:
        return None, "bound again outside the registry statement (line {})".format(
            lines[id(other_binds[0])])
    if len(statements) > 1:
        return None, "declared more than once (lines {})".format(
            ", ".join(str(stmt.lineno) for stmt in statements))
    stmt = statements[0]
    if len(stmt.targets) != 1 or not isinstance(stmt.targets[0], ast.Name):
        return None, "the registry statement must assign the one plain name (line {})".format(
            stmt.lineno)
    first_def = next((node for node in tree.body
                      if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))), None)
    if first_def is not None and stmt.lineno > first_def.lineno:
        return None, "the registry must precede the first module-level def (line {})".format(
            stmt.lineno)
    if not isinstance(stmt.value, ast.Tuple):
        return None, "the registry value must be a tuple of string constants (line {})".format(
            stmt.lineno)
    names = []
    for elt in stmt.value.elts:
        if not isinstance(elt, ast.Constant) or not isinstance(elt.value, str) or not elt.value:
            return None, "a registry element is not a nonempty string constant (line {})".format(
                stmt.lineno)
        if elt.value in names:
            return None, "duplicate registry element {!r} (line {})".format(elt.value, stmt.lineno)
        names.append(elt.value)
    return tuple(names), None


def _store_import_nodes(tree):
    """A module's module-level `import _opf_store [as name]` statements: those in the module
    body and those nested in a module-level try statement (its body, handlers, else and finally
    blocks), which run at import exactly like the body's own."""
    found = []
    stack = list(reversed(tree.body))
    while stack:
        node = stack.pop()
        if isinstance(node, ast.Import):
            if any(name.name == "_opf_store" for name in node.names):
                found.append(node)
        elif isinstance(node, (ast.Try, ast.TryStar)):
            blocks = [node.body] + [handler.body for handler in node.handlers]
            blocks += [node.orelse, node.finalbody]
            for block in reversed(blocks):
                stack.extend(reversed(block))
    return found


def _module_scope_nodes(nodes):
    """The nodes under `nodes` whose bindings land in the module namespace: everything except
    the insides of a def, lambda or class body (a def or class node itself is kept, since its
    name binds there, and so are its decorators, defaults and bases, which run in the module
    scope). Comprehensions are descended into, so a comprehension target is kept too, which
    over-reports and never under-reports."""
    found = []
    stack = list(nodes)
    while stack:
        node = stack.pop()
        found.append(node)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            stack.extend(list(node.args.defaults)
                         + [default for default in node.args.kw_defaults if default is not None]
                         + list(getattr(node, "decorator_list", ())))
            continue
        if isinstance(node, ast.ClassDef):
            stack.extend(list(node.decorator_list) + list(node.bases)
                         + [keyword.value for keyword in node.keywords])
            continue
        stack.extend(ast.iter_child_nodes(node))
    return found


def _store_import_aliases(tree):
    """The names a module's module-level `import _opf_store [as name]` statements bind."""
    aliases = []
    for node in _store_import_nodes(tree):
        for name in node.names:
            if name.name == "_opf_store":
                aliases.append(name.asname or "_opf_store")
    return aliases


def _init_callee_spellings(tree):
    """The spellings a module may use for a DIRECT run_init_operation call: the plain names (its
    own module-level `def run_init_operation`, or `from _opf_init_operation import
    run_init_operation [as name]`) and the module aliases (`import _opf_init_operation [as
    name]`) whose .run_init_operation attribute is the callee."""
    direct = set()
    modules = set()
    for node in tree.body:
        if (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == "run_init_operation"):
            direct.add(node.name)
        elif isinstance(node, ast.Import):
            for name in node.names:
                if name.name == "_opf_init_operation":
                    modules.add(name.asname or "_opf_init_operation")
        elif isinstance(node, ast.ImportFrom) and node.module == "_opf_init_operation":
            for name in node.names:
                if name.name == "run_init_operation":
                    direct.add(name.asname or "run_init_operation")
    return direct, modules


def _bind_targets(node):
    """The target expressions `node` writes, for every binding statement form."""
    if isinstance(node, ast.Assign):
        return list(node.targets)
    if isinstance(node, (ast.AugAssign, ast.AnnAssign, ast.NamedExpr, ast.For, ast.AsyncFor)):
        return [node.target]
    if isinstance(node, ast.Delete):
        return list(node.targets)
    if isinstance(node, ast.withitem) and node.optional_vars is not None:
        return [node.optional_vars]
    return []


def _write_positions(target):
    """The nodes a target expression actually WRITES: names and attributes in binding position,
    tuple, list and starred elements, and a subscript's container chain. A subscript's slice is a
    READ and is never a write position (`d[store.KEY] = v` writes d, not the store)."""
    out = []
    stack = [target]
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.Tuple, ast.List)):
            stack.extend(node.elts)
        elif isinstance(node, ast.Starred):
            stack.append(node.value)
        elif isinstance(node, ast.Subscript):
            stack.append(node.value)
        else:
            out.append(node)
    return out


def _binds_name(node, name):
    """True when `node` BINDS `name`. The names a statement or clause binds come from the one
    complete binding-name function, _route_binding_names (every assignment form, loop, with,
    except and comprehension targets, import aliases, def and class names, type aliases and type
    parameters, match captures, a match star and a mapping rest, global and nonlocal), so this
    rule never keeps a second, shorter list. Two writes that bind no NAME are added here because
    the rule's names must not be written through them either: a parameter of that name (a def's
    or a lambda's), and an assignment or del whose write position is that Attribute of anything
    or whose subscript container is that Name. Reading or calling it is not a binding."""
    if name in _route_binding_names(node):
        return True
    for target in _bind_targets(node):
        for sub in _write_positions(target):
            if ((isinstance(sub, ast.Name) and sub.id == name)
                    or (isinstance(sub, ast.Attribute) and sub.attr == name)):
                return True
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
        args = node.args
        for arg in (args.posonlyargs + args.args + args.kwonlyargs
                    + ([args.vararg] if args.vararg else [])
                    + ([args.kwarg] if args.kwarg else [])):
            if arg.arg == name:
                return True
    return False


def _line_map(roots):
    """Line numbers for every node under `roots`, keyed by id: a node without its own lineno (a
    with item, a comprehension, a match case, an argument list) takes the line of the nearest
    enclosing node that has one, so no finding can fail on a node the parser left unnumbered."""
    lines = {}
    stack = [(root, getattr(root, "lineno", 0)) for root in roots]
    while stack:
        node, inherited = stack.pop()
        line = getattr(node, "lineno", None) or inherited
        lines[id(node)] = line
        stack.extend((child, line) for child in ast.iter_child_nodes(node))
    return lines


def _protected_bind_allowed(fname, unit_name, node, name):
    """The one allowed binding per protected name: the store module's own module-level
    `SUPPORTED_HOMES = ...` assignment and its own module-level `def init_writes_homes2`."""
    if fname != "_opf_store.py":
        return False
    if name == "SUPPORTED_HOMES":
        return unit_name is None and isinstance(node, (ast.Assign, ast.AnnAssign))
    return (unit_name == name and node.name == name
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) else False)


def _is_store_namespace_handle(node, store_aliases):
    """True for an expression that IS the live namespace of a store alias: vars(<alias>) or
    <alias>.__dict__."""
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == "vars"
            and any(isinstance(arg, ast.Name) and arg.id in store_aliases
                    for arg in node.args)):
        return True
    return (isinstance(node, ast.Attribute) and node.attr == "__dict__"
            and isinstance(node.value, ast.Name) and node.value.id in store_aliases)


def _store_write_findings(fname, label, node, store_aliases, line):
    """Findings for one node, at `line`, that writes THROUGH the store module object: an
    assignment or del whose write position is an attribute of a store alias or an item of
    vars(<alias>) or <alias>.__dict__, a setattr or delattr on a store alias or with a rule-name
    string, or a raw namespace handle bound to a name by a plain, annotated or walrus assignment
    (which a later write would ride). A read-only use such as dict(vars(<alias>)) or a handle
    passed into a callee is not a finding here; the callee's own writes are scanned where its
    source is handed in."""
    found = []
    for target in _bind_targets(node):
        for sub in _write_positions(target):
            if (isinstance(sub, ast.Attribute) and sub.attr != "__dict__"
                    and isinstance(sub.value, ast.Name) and sub.value.id in store_aliases):
                found.append("{}: {} writes an attribute of the store module (line {})".format(
                    fname, label, line))
            elif _is_store_namespace_handle(sub, store_aliases):
                via = "vars()" if isinstance(sub, ast.Call) else "__dict__"
                found.append("{}: {} writes into the store module's namespace through {} "
                             "(line {})".format(fname, label, via, line))
    if (isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr)) and node.value is not None
            and _is_store_namespace_handle(node.value, store_aliases)):
        found.append("{}: {} binds a raw handle on the store module's namespace (line {})"
                     .format(fname, label, line))
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        if node.func.id in ("setattr", "delattr"):
            hit = (node.args and isinstance(node.args[0], ast.Name)
                   and node.args[0].id in store_aliases)
            hit = hit or (len(node.args) > 1 and isinstance(node.args[1], ast.Constant)
                          and node.args[1].value in ("init_writes_homes2", "SUPPORTED_HOMES"))
            if hit:
                found.append("{}: {} rebinds a store module attribute through {} (line {})"
                             .format(fname, label, node.func.id, line))
    return found


_HOMES_ACCEPTED_FORM = ("; if the module is compliant, write the binding in a form the homes rule "
                        "accepts (listed in _homes_rule_violations' docstring)")
_HOMES_NAMESPACE_CALLS = frozenset(["globals", "locals", "vars"])
_HOMES_NAMESPACE_ATTRS = frozenset(["__dict__", "modules"])
_HOMES_WRITER_NAMES = frozenset(["setattr", "delattr", "globals", "locals", "vars", "__import__",
                                 "__setattr__", "__delattr__"])
_HOMES_READ_CONSUMERS = frozenset(["dict", "list", "tuple", "sorted", "len", "set", "frozenset",
                                   "iter", "repr"])
_HOMES_READ_METHODS = frozenset(["get", "keys", "values", "items", "copy"])
# The namespace-reach arm (_homes_namespace_reach_findings): in a guarded module every spelling
# below is a finding wherever it appears, whatever object it reads from, unless the exact form
# matches a row of _HOMES_REACH_ALLOWLIST. Nothing is resolved or classified.
_HOMES_REACH_NAMES = frozenset(["builtins", "__builtins__"])
_HOMES_REACH_ATTRS = frozenset(["__globals__", "f_globals", "f_locals", "f_builtins", "__dict__",
                                "__builtins__", "__getattribute__", "__code__", "__defaults__",
                                "__kwdefaults__"])
_HOMES_REACH_ATTR_CALLS = frozenset(["globals", "locals", "vars"])
_HOMES_REACH_STRINGS = frozenset(["setattr", "delattr", "getattr", "globals", "locals", "vars",
                                  "__dict__", "__globals__", "__builtins__", "builtins",
                                  "f_globals", "f_locals", "f_builtins", "modules",
                                  "__getattribute__", "__setattr__", "__delattr__", "__import__",
                                  "__code__", "__defaults__", "__kwdefaults__"])
_HOMES_REACH_MODULES = frozenset(["builtins", "ctypes", "inspect", "operator"])
# The gc names that hand out live objects (and so any namespace dict or function among them):
# get_objects, get_referrers and get_referents return lists of live objects, garbage is a list of
# live objects the collector kept, and callbacks is a list of live callables. Every other gc name
# (enable, disable, isenabled, collect, freeze, unfreeze, the count, threshold, debug, stats and
# tracking queries, and the DEBUG_ constants) returns None, a bool, an int or plain statistics,
# so this arm does not list it.
_HOMES_REACH_GC_NAMES = frozenset(["get_objects", "get_referrers", "get_referents", "garbage",
                                   "callbacks"])
# The lookup calls whose second argument names the attribute read or written on the first: a
# name argument other than one plain string constant is a finding.
_HOMES_LOOKUP_CALLS = frozenset(["getattr", "setattr", "delattr"])
# The tail of every namespace-reach finding.
_HOMES_REACH_SUFFIX = ("; in a module that calls the seam every dynamic namespace reach is a "
                       "finding unless its exact form is a row of _HOMES_REACH_ALLOWLIST")
# The reviewed allowlist: the ONLY forms of a listed spelling the namespace-reach arm does not
# report. Each row is (key, the exact form, why it is safe). A new row is a reviewed change to
# this table and to the rows of _homes_reach_rows; nothing else exempts a spelling.
_HOMES_REACH_ALLOWLIST = (
    ("local-int-builtins",
     "a read of the name builtins in a file where every binding of builtins, anywhere in the "
     "file, is a plain assignment of an integer constant to that one name inside a function body",
     "the name then holds an integer wherever it is read, never the builtins module (an "
     "unimported builtins is not an interpreter builtin), and an integer has no namespace"),
    ("ordinary-dict-copy",
     "dict(<name>.__dict__) with one argument and no keywords, where dict is bound nowhere in the "
     "file and every binding of <name>, anywhere in the file, is a plain assignment of "
     "types.SimpleNamespace(...) to that one name, with types bound only by `import types`",
     "the object is a fresh ordinary namespace object, never a module, function, frame or "
     "class, and dict() returns a new mapping that no module namespace reads"),
    ("string-bound-to-name",
     "a string constant from the reach-string list that is the whole value of a plain "
     "assignment to plain names (message = \"callbacks\")",
     "a string held by a name reaches nothing by itself: every consumer that could turn it into "
     "a reach (a non-literal getattr, setattr or delattr name, a subscript or method call on a "
     "namespace mapping or a module object, getattr referenced other than as a callee, a call of "
     "__import__) is itself a finding"),
    ("literal-name-loop",
     "a getattr, setattr or delattr whose name argument is a name every binding of which, "
     "anywhere in the file, is a for-loop or comprehension target over a literal tuple or list "
     "of string constants, or over <Class>.__slots__ of a class statement bound once whose "
     "__slots__ is such a literal, where no constant is a reach string or a protected name",
     "the name argument then takes only those listed constants, so the call is as static as "
     "the same calls with each literal written out, which the literal-name checks read"),
    ("foreign-module-lookup",
     "a getattr, setattr or delattr whose first argument is a name every binding of which, "
     "anywhere in the file, is an import of a module other than this module, the store and "
     "init modules, sys, builtins and __main__ (_homes_foreign_module_names)",
     "the lookup reads or writes that other module's namespace, never this module's or the "
     "store's, and any further reach from the value it returns needs a listed spelling"),
    ("safe-sys-modules-item",
     "an item assignment sys.modules[<key>] = <value> whose key resolves to string constants "
     "naming neither this module, the store or init module, __main__ nor a submodule of one "
     "(_homes_sys_modules_key_ok, the form the binding arm already accepts)",
     "the write replaces only an unrelated module's entry, never this module, the store or "
     "the init module"),
)


def _write_chain_ids(target):
    """The ids of every node on the write chains of one target expression: each write position
    and, below it, the objects it is reached through (an attribute's or subscript's value, a
    call's callee), so a write INTO sys.modules[...] or globals()[...] is seen as a write through
    them."""
    out = set()
    for sub in _write_positions(target):
        cur = sub
        while cur is not None:
            out.add(id(cur))
            if isinstance(cur, (ast.Attribute, ast.Subscript, ast.Starred)):
                cur = cur.value
            elif isinstance(cur, ast.Call):
                cur = cur.func
            else:
                cur = None
    return out


def _is_namespace_root(node):
    """True for an expression that IS a live namespace mapping: a call of globals(), locals() or
    vars(...), any .__dict__ attribute, and any .modules attribute (sys.modules under any
    spelling of its owner)."""
    if isinstance(node, ast.Call):
        return isinstance(node.func, ast.Name) and node.func.id in _HOMES_NAMESPACE_CALLS
    return isinstance(node, ast.Attribute) and node.attr in _HOMES_NAMESPACE_ATTRS


def _namespace_read_only(node, parent, grand, chains):
    """True when the namespace root `node` sits in one of the read-only positions the rule
    accepts: an item read (root[key]), an argument of a copying or measuring builtin
    (_HOMES_READ_CONSUMERS), the receiver of a read method called at once
    (_HOMES_READ_METHODS), an operand of a comparison (name in sys.modules), or the iterable of
    a loop or comprehension, in each case not on a write chain."""
    if parent is None or id(node) in chains:
        return False
    if isinstance(parent, ast.Subscript):
        return (parent.value is node and isinstance(parent.ctx, ast.Load)
                and id(parent) not in chains)
    if isinstance(parent, ast.Call):
        return (isinstance(parent.func, ast.Name) and parent.func.id in _HOMES_READ_CONSUMERS
                and any(arg is node for arg in parent.args) and not parent.keywords)
    if isinstance(parent, ast.Attribute):
        return (parent.value is node and parent.attr in _HOMES_READ_METHODS
                and isinstance(grand, ast.Call) and grand.func is parent
                and id(grand) not in chains)
    if isinstance(parent, ast.Compare):
        return True
    if isinstance(parent, (ast.For, ast.AsyncFor, ast.comprehension)):
        return parent.iter is node
    return False


_HOMES_UNFOREIGN_MODULES = frozenset(["_opf_store", "_opf_init_operation", "sys", "builtins",
                                      "__main__"])


def _homes_foreign_module_names(fname, tree):
    """The names that are certainly ANOTHER module's object everywhere in this module: every
    binding of the name anywhere in the file (any form _route_binding_names reads, or a
    parameter) is an `import X [as name]` of a module X other than this module itself, the
    store and init modules, sys, builtins and __main__. A setattr or delattr on such a name
    writes that module's namespace, never this module's."""
    stem = fname[:-3] if fname.endswith(".py") else fname
    unforeign = _HOMES_UNFOREIGN_MODULES | {stem}
    good = {}
    bad = set()
    for node in ast.walk(tree):
        names = list(_route_binding_names(node))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            args = node.args
            names += [arg.arg for arg in (args.posonlyargs + args.args + args.kwonlyargs
                                          + ([args.vararg] if args.vararg else [])
                                          + ([args.kwarg] if args.kwarg else []))]
        if isinstance(node, ast.Import):
            for alias in node.names:
                bound = alias.asname or alias.name.split(".")[0]
                if alias.asname is not None and alias.name.split(".")[0] not in unforeign:
                    good[bound] = True
                elif alias.asname is None and "." not in alias.name and alias.name not in unforeign:
                    good[bound] = True
                else:
                    bad.add(bound)
            continue
        bad.update(names)
    return frozenset(name for name in good if name not in bad)


def _homes_sys_modules_key_ok(sub, walked, forbidden):
    """True when the write target `sub`, an item of sys.modules, has a key the rule resolves to
    string constants none of which is in `forbidden` (this module, the store and init modules,
    __main__, or a submodule of one): a string constant, or a name whose every binding in the
    unit is a for-loop target over a literal tuple or list of string constants."""
    key = sub.slice
    if isinstance(key, ast.Constant) and isinstance(key.value, str):
        values = [key.value]
    elif isinstance(key, ast.Name):
        values = []
        for node in walked:
            if key.id not in _route_binding_names(node):
                continue
            if not (isinstance(node, (ast.For, ast.AsyncFor)) and isinstance(node.target, ast.Name)
                    and isinstance(node.iter, (ast.Tuple, ast.List)) and node.iter.elts
                    and all(isinstance(elt, ast.Constant) and isinstance(elt.value, str)
                            for elt in node.iter.elts)):
                return False
            values += [elt.value for elt in node.iter.elts]
        if not values:
            return False
    else:
        return False
    return not any(value.split(".")[0] in forbidden for value in values)


def _homes_dynamic_binding_findings(fname, label, walked, lines, protected, store_imports,
                                    foreign, guarded, facts):
    """Findings for the binding forms the homes rule cannot resolve, over one scanned unit's
    nodes `walked`: the fail-closed arm of the rule. `protected` holds the module's store
    aliases, its run_init_operation import names and its _opf_init_operation aliases. Each of
    these is a finding, whatever object or string it names: (1) an assignment, del, loop, with
    or comprehension target whose write position is an attribute named like a protected name,
    on any object; (2) a setattr or delattr call, with any first argument, whose name argument
    is a protected name or anything but one plain string constant; (3) any star import; (4) a
    write through a live namespace mapping (globals(), locals(), vars(...), any .__dict__, any
    .modules such as sys.modules), its item or attribute assigned or deleted, a method other
    than a read method called on it, or the mapping itself bound, passed or returned (only the
    read-only positions _namespace_read_only lists are accepted); (5) a reference to setattr,
    delattr, globals, locals, vars or __import__ other than as the direct callee of a call, and
    any reference to __setattr__ or __delattr__; (6) an import form the rule does not resolve:
    `import sys as <other name>`, `from sys import modules`, an import of a submodule of
    _opf_store or _opf_init_operation, and an `import _opf_store` anywhere but a module-level
    statement (a function-local import binds a store handle the rule does not track); (7) every
    spelling the namespace-reach arm reports (_homes_namespace_reach_findings, which reads the
    whole file's bindings and store aliases from `facts`)."""
    found = []
    for node in walked:
        if isinstance(node, ast.ImportFrom) and any(alias.name == "*" for alias in node.names):
            found.append("{}: {} has a star import from {} (line {}); a star import can bind "
                         "any name, the protected ones included{}".format(
                             fname, label, node.module or ".", lines[id(node)],
                             _HOMES_ACCEPTED_FORM))
    if not guarded:
        return found
    stem = fname[:-3] if fname.endswith(".py") else fname
    forbidden = frozenset(["_opf_store", "_opf_init_operation", "__main__", stem])
    parents = {}
    for node in walked:
        for child in ast.iter_child_nodes(node):
            parents[id(child)] = node
    chains = set()
    for node in walked:
        targets = _bind_targets(node)
        if isinstance(node, ast.comprehension):
            targets = [node.target]
        for target in targets:
            chains |= _write_chain_ids(target)
            for sub in _write_positions(target):
                if isinstance(sub, ast.Attribute) and sub.attr in protected:
                    found.append(
                        "{}: {} writes or deletes the attribute {} on an object (line {}); the "
                        "store module alias, the callee's name and the init module alias are "
                        "bound only by their module-level imports{}".format(
                            fname, label, sub.attr, lines[id(node)], _HOMES_ACCEPTED_FORM))
    callees = set()
    for node in walked:
        if not isinstance(node, ast.Call):
            continue
        callees.add(id(node.func))
        func = node.func
        name = (func.id if isinstance(func, ast.Name)
                else func.attr if isinstance(func, ast.Attribute) else None)
        if name not in ("setattr", "delattr"):
            continue
        key = node.args[1] if len(node.args) > 1 else None
        resolved = (isinstance(key, ast.Constant) and isinstance(key.value, str)
                    and not any(isinstance(arg, ast.Starred) for arg in node.args[:2]))
        foreign_target = (bool(node.args) and isinstance(node.args[0], ast.Name)
                          and node.args[0].id in foreign)
        if (not resolved and not foreign_target) or (resolved and key.value in protected):
            found.append(
                "{}: {} calls {} with {} (line {}); a setattr or delattr may name only a "
                "plain string constant that is not the store module alias, the callee's name "
                "or the init module alias{}".format(
                    fname, label, name,
                    "a protected name {!r}".format(key.value) if resolved
                    else "a name argument the rule cannot resolve", lines[id(node)],
                    _HOMES_ACCEPTED_FORM))
    for node in walked:
        line = lines[id(node)]
        if isinstance(node, ast.ImportFrom):
            if node.module == "sys" and any(alias.name == "modules" for alias in node.names):
                found.append("{}: {} imports sys.modules by name (line {}); the rule reads "
                             "sys.modules only under that spelling{}".format(
                                 fname, label, line, _HOMES_ACCEPTED_FORM))
            if (node.module or "").startswith(("_opf_store.", "_opf_init_operation.")):
                found.append("{}: {} imports from a submodule of {} (line {}), a form the rule "
                             "does not resolve{}".format(fname, label, node.module.split(".")[0],
                                                         line, _HOMES_ACCEPTED_FORM))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "sys" and alias.asname not in (None, "sys"):
                    found.append("{}: {} imports sys under the name {} (line {}); the rule "
                                 "reads sys.modules only under that spelling{}".format(
                                     fname, label, alias.asname, line, _HOMES_ACCEPTED_FORM))
                elif alias.name.startswith(("_opf_store.", "_opf_init_operation.")):
                    found.append("{}: {} imports the submodule {} (line {}), a form the rule "
                                 "does not resolve{}".format(fname, label, alias.name, line,
                                                             _HOMES_ACCEPTED_FORM))
                elif alias.name == "_opf_store" and id(node) not in store_imports:
                    found.append("{}: {} imports _opf_store other than by a module-level "
                                 "statement (line {}); the rule tracks the store module only "
                                 "through its module-level import{}".format(
                                     fname, label, line, _HOMES_ACCEPTED_FORM))
        if isinstance(node, (ast.Name, ast.Attribute)):
            spelled = node.id if isinstance(node, ast.Name) else node.attr
            if spelled in _HOMES_WRITER_NAMES and (
                    spelled in ("__setattr__", "__delattr__") or id(node) not in callees):
                found.append("{}: {} references {} other than as the direct callee of a call "
                             "(line {}); the rule reads a namespace writer only where it is "
                             "called{}".format(fname, label, spelled, line,
                                                _HOMES_ACCEPTED_FORM))
        if _is_namespace_root(node):
            parent = parents.get(id(node))
            grand = parents.get(id(parent)) if parent is not None else None
            if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                    and node.value.id == "sys" and isinstance(parent, ast.Subscript)
                    and parent.value is node and isinstance(parent.ctx, ast.Store)
                    and _homes_sys_modules_key_ok(parent, walked, forbidden)):
                continue
            if not _namespace_read_only(node, parent, grand, chains):
                found.append("{}: {} writes through, binds or passes a live namespace mapping "
                             "(globals(), locals(), vars(), __dict__ or sys.modules) (line {}); "
                             "only an item read, a copy, a read method, a membership test or "
                             "an iteration is accepted{}".format(
                                 fname, label, line, _HOMES_ACCEPTED_FORM))
    found.extend(_homes_namespace_reach_findings(fname, label, walked, lines,
                                                 dict(facts, protected=protected)))
    return found


def _homes_binding_index(nodes):
    """Name to the nodes under `nodes` that bind it: every binding _route_binding_names reads,
    plus each parameter of a def or lambda (as its ast.arg node)."""
    index = {}
    for node in nodes:
        for name in _route_binding_names(node):
            index.setdefault(name, []).append(node)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            args = node.args
            for arg in (args.posonlyargs + args.args + args.kwonlyargs
                        + ([args.vararg] if args.vararg else [])
                        + ([args.kwarg] if args.kwarg else [])):
                index.setdefault(arg.arg, []).append(arg)
    return index


def _homes_local_ids(tree):
    """The ids of every node inside the body of a def or lambda anywhere in `tree` (the nodes
    whose plain bindings are function-local)."""
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            bodies = node.body
        elif isinstance(node, ast.Lambda):
            bodies = [node.body]
        else:
            continue
        for stmt in bodies:
            found.update(id(sub) for sub in ast.walk(stmt))
    return found


def _homes_reach_allowed(key, node, parent, facts):
    """True when `node`, a listed spelling, matches the _HOMES_REACH_ALLOWLIST row `key` exactly
    (the row's form, read over the whole file's bindings in facts["file_binds"])."""
    binds = facts["file_binds"]
    if key == "local-int-builtins":
        found = binds.get("builtins", ())
        return bool(found) and all(
            isinstance(bind, ast.Assign) and id(bind) in facts["local_ids"]
            and len(bind.targets) == 1 and isinstance(bind.targets[0], ast.Name)
            and isinstance(bind.value, ast.Constant) and type(bind.value.value) is int
            for bind in found)
    if key == "ordinary-dict-copy":
        if not (isinstance(parent, ast.Call) and isinstance(parent.func, ast.Name)
                and parent.func.id == "dict" and len(parent.args) == 1
                and parent.args[0] is node and not parent.keywords
                and "dict" not in binds and isinstance(node.value, ast.Name)):
            return False
        types_binds = binds.get("types", ())
        if not types_binds or not all(
                isinstance(bind, ast.Import) and any(
                    alias.name == "types" and alias.asname is None for alias in bind.names)
                and not any(alias.asname == "types" for alias in bind.names)
                for bind in types_binds):
            return False
        found = binds.get(node.value.id, ())
        return bool(found) and all(
            isinstance(bind, ast.Assign) and len(bind.targets) == 1
            and isinstance(bind.targets[0], ast.Name)
            and isinstance(bind.value, ast.Call) and isinstance(bind.value.func, ast.Attribute)
            and bind.value.func.attr == "SimpleNamespace"
            and isinstance(bind.value.func.value, ast.Name)
            and bind.value.func.value.id == "types"
            for bind in found)
    if key == "string-bound-to-name":
        return (isinstance(parent, ast.Assign) and parent.value is node
                and all(isinstance(target, ast.Name) for target in parent.targets))
    if key == "literal-name-loop":
        name = node.args[1] if len(node.args) > 1 else None
        if not isinstance(name, ast.Name):
            return False
        values = _homes_loop_literals(name.id, binds)
        banned = (_HOMES_REACH_STRINGS | _HOMES_REACH_GC_NAMES | set(facts["store_aliases"])
                  | set(facts["protected"]))
        return bool(values) and not (values & banned)
    if key == "foreign-module-lookup":
        return (bool(node.args) and isinstance(node.args[0], ast.Name)
                and node.args[0].id in facts["foreign"])
    if key == "safe-sys-modules-item":
        return (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                and node.value.id == "sys" and isinstance(parent, ast.Subscript)
                and parent.value is node and isinstance(parent.ctx, ast.Store)
                and _homes_sys_modules_key_ok(parent, facts["walked"], facts["forbidden"]))
    return False


def _homes_loop_literals(name, binds):
    """The string constants `name` can hold when every binding of it in the file is a for-loop
    or comprehension target over a literal tuple or list of string constants, or over
    <Class>.__slots__ of a class statement bound once whose __slots__ is one such literal
    assignment; otherwise the empty set."""
    values = set()
    found = binds.get(name, ())
    for bind in found:
        if not (isinstance(bind, (ast.For, ast.AsyncFor, ast.comprehension))
                and isinstance(bind.target, ast.Name) and bind.target.id == name):
            return set()
        source = bind.iter
        if (isinstance(source, ast.Attribute) and source.attr == "__slots__"
                and isinstance(source.value, ast.Name)):
            owners = binds.get(source.value.id, ())
            if len(owners) != 1 or not isinstance(owners[0], ast.ClassDef):
                return set()
            slots = [stmt for stmt in owners[0].body
                     if isinstance(stmt, ast.Assign) and any(
                         isinstance(target, ast.Name) and target.id == "__slots__"
                         for target in stmt.targets)]
            if len(slots) != 1 or len(slots[0].targets) != 1:
                return set()
            source = slots[0].value
        if not (isinstance(source, (ast.Tuple, ast.List)) and source.elts and all(
                isinstance(elt, ast.Constant) and isinstance(elt.value, str)
                for elt in source.elts)):
            return set()
        values.update(elt.value for elt in source.elts)
    return values


def _homes_namespace_reach_findings(fname, label, walked, lines, facts):
    """Findings for the namespace-reach arm, over one guarded unit's nodes `walked`. The arm is
    bounded and fail-closed: it resolves no target and classifies no object; every spelling
    below is a finding wherever it appears, unless its exact form is a row of
    _HOMES_REACH_ALLOWLIST (_homes_reach_allowed). The spellings: (1) a getattr, setattr or
    delattr call (by a name or an attribute) whose name argument is not one plain string
    constant, and getattr referenced other than as the direct callee of a call; (2) a reference
    to the name builtins or __builtins__; (3) an attribute __globals__, f_globals, f_locals,
    f_builtins, __dict__, __builtins__, __getattribute__, __code__, __defaults__ or
    __kwdefaults__, an attribute named globals, locals or vars, and a gc name that hands out
    live objects (get_objects, get_referrers, get_referents, garbage, callbacks) as an
    attribute, each on any object; (4) a subscript (read, item assignment or del) or a method
    call or any other attribute on a call of globals, locals or vars or on a .modules attribute
    (sys.modules), and a subscript of a name bound anywhere in the file by an import (a module
    object); (5) a call of __import__; (6) a string constant equal to one of
    _HOMES_REACH_STRINGS or a gc name in (3), in any position; (7) any import, plain or from,
    of builtins, ctypes, inspect or operator (or a submodule of one), a from-import of a gc name
    in (3), a from-import of the store module or of a name from it, and an import of the store
    module binding a name other than the module's first store alias. An import of gc itself and
    every other gc name are not reported by this arm."""
    found = []
    stem = fname[:-3] if fname.endswith(".py") else fname
    facts = dict(facts, walked=walked, forbidden=frozenset(
        ["_opf_store", "_opf_init_operation", "__main__", stem]))
    parents = {}
    for node in walked:
        for child in ast.iter_child_nodes(node):
            parents[id(child)] = node
    callees = set(id(node.func) for node in walked if isinstance(node, ast.Call))
    binds = facts["file_binds"]
    module_names = frozenset(
        name for name, nodes in binds.items()
        if any(isinstance(bind, ast.Import) for bind in nodes))
    reach_strings = _HOMES_REACH_STRINGS | _HOMES_REACH_GC_NAMES
    first_store = facts["store_aliases"][0] if facts["store_aliases"] else None
    for node in walked:
        line = lines[id(node)]
        parent = parents.get(id(node))
        what = []
        allow = []
        if isinstance(node, ast.Call):
            func = node.func
            name = (func.id if isinstance(func, ast.Name)
                    else func.attr if isinstance(func, ast.Attribute) else None)
            if name in _HOMES_LOOKUP_CALLS:
                key = node.args[1] if len(node.args) > 1 else None
                if not (isinstance(key, ast.Constant) and isinstance(key.value, str)
                        and not any(isinstance(arg, ast.Starred) for arg in node.args[:2])):
                    what.append("calls {} with a name argument that is not a string "
                                "literal".format(name))
                    allow = ["literal-name-loop", "foreign-module-lookup"]
            if name == "__import__":
                what.append("calls __import__")
        if (isinstance(node, (ast.Name, ast.Attribute))
                and (node.id if isinstance(node, ast.Name) else node.attr) == "getattr"
                and id(node) not in callees):
            what.append("references getattr other than as the direct callee of a call")
        if isinstance(node, ast.Name) and node.id in _HOMES_REACH_NAMES:
            what.append("references {}".format(node.id))
            if node.id == "builtins":
                allow = ["local-int-builtins"]
        if isinstance(node, ast.Attribute):
            if node.attr in _HOMES_REACH_ATTRS:
                what.append("reads the attribute {}".format(node.attr))
                if node.attr == "__dict__":
                    allow = ["ordinary-dict-copy"]
            elif node.attr in _HOMES_REACH_ATTR_CALLS:
                what.append("reaches {} through an attribute".format(node.attr))
            elif node.attr in _HOMES_REACH_GC_NAMES:
                what.append("reads the gc live-object accessor {}".format(node.attr))
        mapping = ((isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id in _HOMES_NAMESPACE_CALLS)
                   or (isinstance(node, ast.Attribute) and node.attr == "modules"))
        if (mapping and isinstance(parent, (ast.Subscript, ast.Attribute))
                and parent.value is node):
            what.append("subscripts or calls a method of a namespace mapping ({})".format(
                node.func.id + "()" if isinstance(node, ast.Call) else "." + node.attr))
            allow = ["safe-sys-modules-item"]
        if (isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name)
                and node.value.id in module_names):
            what.append("subscripts {}, a name an import binds (a module object)".format(
                node.value.id))
        if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                and node.value in reach_strings):
            what.append("carries the string {!r}{}".format(
                node.value, ", a gc live-object accessor"
                if node.value in _HOMES_REACH_GC_NAMES else ""))
            allow = ["string-bound-to-name"]
        if isinstance(node, ast.Import):
            hits = sorted(alias.name for alias in node.names
                          if alias.name.split(".")[0] in _HOMES_REACH_MODULES)
            if hits:
                what.append("imports {}".format(", ".join(hits)))
            second = sorted(alias.asname for alias in node.names
                            if alias.name == "_opf_store" and alias.asname is not None
                            and alias.asname != first_store)
            second += sorted("_opf_store" for alias in node.names
                             if alias.name == "_opf_store" and alias.asname is None
                             and first_store != "_opf_store")
            if second:
                what.append("imports the store module under another name {}".format(second[0]))
        if isinstance(node, ast.ImportFrom) and node.level == 0:
            module = node.module or ""
            if module.split(".")[0] in _HOMES_REACH_MODULES:
                what.append("imports from {}".format(module))
            if module == "_opf_store":
                what.append("imports names from the store module")
            if module == "gc":
                hits = sorted(alias.name for alias in node.names
                              if alias.name in _HOMES_REACH_GC_NAMES)
                if hits:
                    what.append("imports the gc live-object accessor {}".format(
                        ", ".join(hits)))
        if len(what) == 1 and any(_homes_reach_allowed(key, node, parent, facts)
                                  for key in allow):
            continue
        for text in what:
            found.append("{}: {} {} (line {}){}".format(
                fname, label, text, line, _HOMES_REACH_SUFFIX))
    return found


def _homes_reach_rows(alias_store, init_import, alias_call):
    """Self-test rows for the namespace-reach arm and for the binding-arm findings no other row
    names: (label, module source, expected finding text, or None for a form that must scan
    clean). Each form sits in a guarded module beside a compliant call. A reach row's expected
    text includes _HOMES_REACH_SUFFIX, so only the namespace-reach arm can satisfy it and the row
    turns red when that arm is removed; an allowlisted form's row turns red when its
    _HOMES_REACH_ALLOWLIST row is removed."""
    fake = "types.SimpleNamespace(init_writes_homes2=lambda: True)"
    head = "import sys\nimport types\n\n" + init_import + alias_store

    def module(before, after=""):
        return head + before.replace("FAKE", fake) + "\n\n\n" + alias_call + after.replace(
            "FAKE", fake)

    def reach(text):
        return (text, _HOMES_REACH_SUFFIX)

    rows = [
        # Escapes reported by earlier reviews: each must stay a finding.
        ("builtins.globals() item write",
         module("import builtins\nbuiltins.globals()['_opf_store'] = FAKE\n"),
         reach("module-level code references builtins")),
        ("from builtins import globals under an alias",
         module("from builtins import globals as _g\n_g()['_opf_store'] = FAKE\n"),
         reach("module-level code imports from builtins")),
        ("from builtins import setattr under an alias",
         module("from builtins import setattr as _s\n"
                "_s(sys.modules[__name__], '_opf_store', FAKE)\n"),
         reach("module-level code imports from builtins")),
        ("a getattr of setattr whose result is called",
         module("import builtins\n"
                "getattr(builtins, 'setattr')(sys.modules[__name__], '_opf_store', FAKE)\n"),
         reach("module-level code carries the string 'setattr'")),
        ("a module-level item write through a function's __globals__",
         module("", "\n_init_ok.__globals__['_opf_store'] = FAKE\n"),
         reach("module-level code reads the attribute __globals__")),
        ("an item write through __globals__ in a unit that never calls the seam",
         module("", "\n\ndef _swap(fake):\n    _init_ok.__globals__['_opf_store'] = fake\n"),
         reach("_swap reads the attribute __globals__")),
        ("a getattr string naming __dict__",
         module("getattr(sys.modules[__name__], '__dict__')['_opf_store'] = FAKE\n"),
         reach("module-level code carries the string '__dict__'")),
        ("operator.setitem into a getattr __dict__",
         module("import operator\noperator.setitem(getattr(sys.modules[__name__], '__dict__'), "
                "'_opf_store', FAKE)\n"),
         reach("module-level code imports operator")),
        ("an item write through a frame's f_globals",
         module("sys._getframe().f_globals['_opf_store'] = FAKE\n"),
         reach("module-level code reads the attribute f_globals")),
        ("__builtins__ item lookup of globals",
         module("__builtins__['globals']()['_opf_store'] = FAKE\n"),
         reach("module-level code references __builtins__")),
        ("builtins.vars() of the module object",
         module("import builtins\nbuiltins.vars(sys.modules[__name__])['_opf_store'] = FAKE\n"),
         reach("module-level code reaches vars through an attribute")),
        ("an update through a function's __globals__",
         module("", "\n_init_ok.__globals__.update(_opf_store=FAKE)\n"),
         reach("module-level code reads the attribute __globals__")),
        ("inspect.currentframe().f_globals item write",
         module("import inspect\ninspect.currentframe().f_globals['_opf_store'] = FAKE\n"),
         reach("module-level code imports inspect")),
        ("gc.get_referrers of the store module",
         module("import gc\nfor _ref in gc.get_referrers(_opf_store):\n"
                "    if isinstance(_ref, dict) and '_opf_store' in _ref:\n"
                "        _ref['_opf_store'] = FAKE\n"),
         reach("module-level code reads the gc live-object accessor get_referrers")),
        ("from gc import get_objects under an alias",
         module("from gc import get_objects as _objs\nfor _ref in _objs():\n"
                "    if isinstance(_ref, dict) and '_opf_store' in _ref:\n"
                "        _ref['_opf_store'] = FAKE\n"),
         reach("module-level code imports the gc live-object accessor get_objects")),
        ("a getattr string naming get_referents",
         module("import gc\n_walk = getattr(gc, 'get_referents')\n"),
         reach("module-level code carries the string 'get_referents', a gc live-object "
               "accessor")),
        ("builtins.globals().update",
         module("import builtins\nbuiltins.globals().update(_opf_store=FAKE)\n"),
         reach("module-level code reaches globals through an attribute")),
        ("globals().update by keyword",
         module("globals().update(_opf_store=FAKE)\n"),
         reach("module-level code subscripts or calls a method of a namespace mapping "
               "(globals())")),
        ("a globals() item write",
         module("globals()['_opf_store'] = FAKE\n"),
         reach("module-level code subscripts or calls a method of a namespace mapping "
               "(globals())")),
        ("a vars() item write of the module object",
         module("vars(sys.modules[__name__])['_opf_store'] = FAKE\n"),
         reach("module-level code subscripts or calls a method of a namespace mapping "
               "(vars())")),
        ("a sys.modules item read of this module",
         module("_me = sys.modules[__name__]\n"),
         reach("module-level code subscripts or calls a method of a namespace mapping "
               "(.modules)")),
        ("a sys.modules method call",
         module("_me = sys.modules.get(__name__)\n"),
         reach("module-level code subscripts or calls a method of a namespace mapping "
               "(.modules)")),
        ("a module __dict__ item write",
         module("sys.modules[__name__].__dict__['_opf_store'] = FAKE\n"),
         reach("module-level code reads the attribute __dict__")),
        ("a setattr whose name argument is a parameter",
         module("", "\n\ndef _swap(fake, name):\n    setattr(sys.modules[__name__], name, fake)\n"),
         reach("_swap calls setattr with a name argument that is not a string literal")),
        ("a delattr whose name argument is a parameter",
         module("", "\n\ndef _drop(name):\n    delattr(sys.modules[__name__], name)\n"),
         reach("_drop calls delattr with a name argument that is not a string literal")),
        ("getattr passed as a value",
         module("import functools\n_g = functools.reduce(getattr, ['x'], types)\n"),
         reach("module-level code references getattr other than as the direct callee")),
        ("a call of __import__",
         module("_m = __import__(sys.argv[0])\n"),
         reach("module-level code calls __import__")),
        ("a subscript of an imported module object",
         module("import os\n_v = os['environ']\n"),
         reach("module-level code subscripts os, a name an import binds")),
        ("a from-import of a store name",
         module("from _opf_store import SOMETHING as _thing\n"),
         reach("module-level code imports names from the store module")),
        ("the store module imported under a second name",
         module("import _opf_store as _second\n"),
         reach("module-level code imports the store module under another name _second")),
        ("a function's __code__ replaced",
         module("", "\n_init_ok.__code__ = (lambda root: root).__code__\n"),
         reach("module-level code reads the attribute __code__")),
        ("object.__getattribute__ of a function",
         module("", "\n_g = object.__getattribute__(_init_ok, 'x')\n"),
         reach("module-level code reads the attribute __getattribute__")),
        # Escapes reported against the previous resolver: a module-level name rebound through a
        # global declaration in a sibling function, a decorated class, a rebound builtin, a
        # string carried by a special name, and a frame's f_locals by a getattr string.
        ("a global rebinding to a function, then __globals__",
         module("box = None\n", "\n\ndef _setup():\n    global box\n    box = _init_ok\n\n\n"
                "def _swap(fake):\n    box.__globals__['_opf_store'] = fake\n"),
         reach("_swap reads the attribute __globals__")),
        ("a global rebinding to a frame, then f_globals",
         module("box = None\n", "\n\ndef _setup():\n    global box\n    box = sys._getframe()\n"
                "\n\ndef _swap(fake):\n    box.f_globals['_opf_store'] = fake\n"),
         reach("_swap reads the attribute f_globals")),
        ("a class decorator returning the seam caller, then __globals__",
         module("", "\n\ndef _deco(cls):\n    return _init_ok\n\n\n@_deco\nclass _Box:\n"
                "    pass\n\n\n_Box.__globals__['_opf_store'] = FAKE\n"),
         reach("module-level code reads the attribute __globals__")),
        ("a lambda class decorator, then __globals__ of an instance",
         module("", "\n\n@(lambda cls: lambda: _init_ok)\nclass _Box:\n    pass\n\n\n"
                "_Box().__globals__['_opf_store'] = FAKE\n"),
         reach("module-level code reads the attribute __globals__")),
        ("a reach string set through a global declaration, then getattr",
         module("_name = 'x'\n", "\n\ndef _setup():\n    global _name\n    _name = '__globals__'"
                "\n\n\ngetattr(_init_ok, _name)['_opf_store'] = FAKE\n"),
         reach("module-level code calls getattr with a name argument that is not a string "
               "literal")),
        ("dict rebound through a global declaration, then __globals__",
         module("", "\n\ndef _s():\n    global dict\n    dict = lambda: _init_ok\n\n\n_s()\n"
                "dict().__globals__['_opf_store'] = FAKE\n"),
         reach("module-level code reads the attribute __globals__")),
        ("a string named builtins, then a getattr of it",
         module("builtins = '__dict__'\n"
                "getattr(sys.modules[__name__], builtins)['_opf_store'] = FAKE\n"),
         reach("module-level code references builtins")),
        ("the same through a name __builtins__",
         module("__builtins__ = '__dict__'\n"
                "getattr(sys.modules[__name__], __builtins__)['_opf_store'] = FAKE\n"),
         reach("module-level code references __builtins__")),
        ("a frame's f_locals by a getattr string",
         module("getattr(sys._getframe(), 'f_locals')['_opf_store'] = FAKE\n"),
         reach("module-level code carries the string 'f_locals'")),
        # Binding-arm findings no other row names.
        ("setattr bound to a name",
         module("_w = setattr\n"),
         "module-level code references setattr other than as the direct callee"),
        ("sys imported under another name",
         module("import sys as _system\n"),
         "module-level code imports sys under the name _system"),
        ("sys.modules imported by name",
         module("from sys import modules\n"),
         "module-level code imports sys.modules by name"),
        ("a from-import of a store submodule",
         module("from _opf_store.sub import x\n"),
         "module-level code imports from a submodule of _opf_store"),
        ("an import of a store submodule",
         module("import _opf_store.sub\n"),
         "module-level code imports the submodule _opf_store.sub"),
        ("a function-local store import",
         module("", "\n\ndef _local():\n    import _opf_store\n    return _opf_store\n"),
         "_local imports _opf_store other than by a module-level statement"),
        # Forms outside every listed spelling: no reach, so they scan clean.
        ("a sorted copy of globals() (no reach)", module("_names = sorted(globals())\n"), None),
        ("an aliased from-import of another module (no reach)",
         module("from os.path import join as _g\n_p = _g('a', 'b')\n"), None),
        ("a literal setattr of an unprotected name (no reach)",
         module("setattr(types.SimpleNamespace(), 'x', 1)\n"), None),
        ("a literal getattr whose result is not called (no reach)",
         module("_v = getattr(sys, 'version')\n"), None),
        ("a literal getattr result called on an ordinary object (no reach)",
         module("", "\n\ndef _run():\n    box = types.SimpleNamespace(go=dict)\n"
                "    return getattr(box, 'go')()\n"), None),
        ("a module-level read of a function's __name__ (no reach)",
         module("", "\n_name = _init_ok.__name__\n"), None),
        ("a frame's f_lineno (no reach)", module("_line = sys._getframe().f_lineno\n"), None),
        ("gc.disable and gc.collect under an alias (no reach)",
         module("import gc as _gc\n_gc.disable()\n_freed = _gc.collect()\n"), None),
        ("from gc import collect under an alias (no reach)",
         module("from gc import collect as _collect\n_freed = _collect()\n"), None),
        ("a membership test on sys.modules (no reach)",
         module("_loaded = 'json' in sys.modules\n"), None),
        # Every allowlisted form scans clean; each turns red when its allowlist row is removed.
        ("allowlist local-int-builtins: a local integer named builtins",
         module("", "\n\ndef _unrelated():\n    builtins = 1\n    return builtins\n"), None),
        ("allowlist ordinary-dict-copy: a copy of an ordinary object's __dict__",
         module("box = types.SimpleNamespace(a=1)\n_copy = dict(box.__dict__)\n"), None),
        ("allowlist string-bound-to-name: a string 'callbacks' bound to a name",
         module("message = \"callbacks\"\n"), None),
        ("allowlist string-bound-to-name: a string 'modules' bound to a name",
         module("x = 'modules'\n"), None),
        ("allowlist literal-name-loop: getattr over a literal tuple of names",
         module("for _attr in ('version', 'platform'):\n    _v = getattr(sys, _attr)\n"), None),
        ("allowlist literal-name-loop: getattr over a class's literal __slots__",
         module("class _Rec:\n    __slots__ = ('a', 'b')\n\n\n"
                "_row = {_slot: getattr(_Rec, _slot, None) for _slot in _Rec.__slots__}\n"),
         None),
        ("allowlist foreign-module-lookup: setattr on another module by a computed name",
         module("import json as _json\n", "\n\ndef _patch(name, value):\n"
                "    setattr(_json, name, value)\n"), None),
        ("allowlist safe-sys-modules-item: blocking an unrelated module",
         module("for _blocked in ('_testcapi', '_interpreters'):\n"
                "    sys.modules[_blocked] = None\n"), None),
        # The allowlist's exact forms: a near neighbour of each is a finding.
        ("a module-level integer named builtins (not local)",
         module("builtins = 1\n_v = builtins\n"),
         reach("module-level code references builtins")),
        ("a copy of a parameter's __dict__",
         module("", "\n\ndef _peek(obj):\n    return dict(obj.__dict__)\n"),
         reach("_peek reads the attribute __dict__")),
        ("a copy of a module-level class instance's __dict__",
         module("class _Box:\n    pass\n\n\n_items = sorted(_Box().__dict__)\n"),
         reach("module-level code reads the attribute __dict__")),
        ("a reach string passed to a call",
         module("_label = str('callbacks')\n"),
         reach("module-level code carries the string 'callbacks', a gc live-object accessor")),
        ("a loop over literal names that include a reach string",
         module("for _attr in ('version', '__dict__'):\n    _v = getattr(sys, _attr)\n"),
         reach("module-level code calls getattr with a name argument that is not a string "
               "literal")),
        ("a loop name bound a second time",
         module("for _attr in ('version',):\n    _v = getattr(sys, _attr)\n_attr = 'x'\n"),
         reach("module-level code calls getattr with a name argument that is not a string "
               "literal")),
        ("a sys.modules item assignment of this module",
         module("sys.modules[__name__] = FAKE\n"),
         reach("module-level code subscripts or calls a method of a namespace mapping "
               "(.modules)")),
    ]
    # One row per listed spelling, written out here rather than read from the arm's own sets,
    # so dropping any one spelling from the arm turns its row red.
    for name in ("__builtins__", "builtins"):
        rows.append(("the name " + name,
                     module(("import builtins\n" if name == "builtins" else "")
                            + "_v = repr(" + name + ")\n"),
                     reach("module-level code references " + name)))
    for attr in ("__builtins__", "__code__", "__defaults__", "__dict__", "__getattribute__",
                 "__globals__", "__kwdefaults__", "f_builtins", "f_globals", "f_locals"):
        rows.append(("the attribute " + attr,
                     module("", "\n_v = repr(_init_ok." + attr + ")\n"),
                     reach("module-level code reads the attribute " + attr)))
    for attr in ("globals", "locals", "vars"):
        rows.append((attr + " through an attribute",
                     module("_v = repr(sys.modules['builtins']." + attr + ")\n"),
                     reach("module-level code reaches " + attr + " through an attribute")))
        rows.append((attr + "() subscripted",
                     module("_v = " + attr + "()['x']\n"),
                     reach("module-level code subscripts or calls a method of a namespace "
                           "mapping (" + attr + "())")))
    for text in ("__builtins__", "__code__", "__defaults__", "__delattr__", "__dict__",
                 "__getattribute__", "__globals__", "__import__", "__kwdefaults__",
                 "__setattr__", "builtins", "delattr", "f_builtins", "f_globals", "f_locals",
                 "getattr", "globals", "locals", "modules", "setattr", "vars"):
        rows.append(("the string " + text,
                     module("_v = repr('" + text + "')\n"),
                     reach("module-level code carries the string '" + text + "'")))
    for mod in ("builtins", "ctypes", "inspect", "operator"):
        rows.append(("an import of " + mod, module("import " + mod + " as _m\n"),
                     reach("module-level code imports " + mod)))
    for name in ("callbacks", "garbage", "get_objects", "get_referents", "get_referrers"):
        rows.append(("the gc attribute " + name,
                     module("import gc\n_v = repr(gc." + name + ")\n"),
                     reach("module-level code reads the gc live-object accessor " + name)))
        rows.append(("a from-import of the gc name " + name,
                     module("from gc import " + name + " as _accessor\n"),
                     reach("module-level code imports the gc live-object accessor " + name)))
        rows.append(("the gc string " + name,
                     module("import gc\n_v = getattr(gc, '" + name + "', None)\n"),
                     reach("module-level code carries the string '" + name
                           + "', a gc live-object accessor")))
    return rows


def _rebind_finding(fname, label, name, line, store_aliases):
    """The finding for a rebinding of a store alias or of a name the module's run_init_operation
    import binds, at module level or through a global declaration."""
    if name in store_aliases:
        return ("{}: {} rebinds the store module alias {} at module level or through a global "
                "declaration (line {}); the rule must be read through the module-level "
                "import".format(fname, label, name, line))
    return ("{}: {} rebinds the run_init_operation import {} at module level or through a "
            "global declaration (line {}); a direct call is recognized only through the one "
            "module-level import".format(fname, label, name, line))


def _init_binding_allowed(node, name, direct_names, init_aliases):
    """True when the module-top-level `node` binds `name` only by the one form the rule reads
    as the callee's spelling: an unaliased or aliased `from _opf_init_operation import
    run_init_operation` (the alias itself is reported separately), a `def run_init_operation`
    (the defining module), or an `import _opf_init_operation [as name]`."""
    if isinstance(node, ast.ImportFrom):
        bound = [alias for alias in node.names if (alias.asname or alias.name) == name]
        return (name in direct_names and node.module == "_opf_init_operation"
                and node.level == 0 and bool(bound)
                and all(alias.name == "run_init_operation" for alias in bound))
    if isinstance(node, ast.Import):
        bound = [alias for alias in node.names
                 if (alias.asname or alias.name.split(".")[0]) == name]
        return (name in init_aliases and bool(bound)
                and all(alias.name == "_opf_init_operation" for alias in bound))
    return (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and name == "run_init_operation" == node.name and name in direct_names)


def _homes_module_binding_findings(fname, tree, store_aliases, direct_names, init_aliases):
    """Findings for the module namespace as a whole: every node whose bindings land there (the
    module body, module-level compound statements, module-top-level def and class statements and
    their definition-time expressions; _module_scope_nodes) is read with the one complete
    binding-name function, so a match capture, a with target, a type alias, an annotated
    assignment, a def, an import or any other binding of a store alias other than its import
    statement is a finding, and so is any binding of a name the run_init_operation import binds
    other than the first module-top-level statement that binds it in the form the rule reads (a
    later def or import of that name rebinds the callee every call site then reaches). A
    module-level `from _opf_init_operation import run_init_operation as <alias>` is itself a
    finding: with the import unaliased, a lookup by string (getattr on the module, globals())
    can only spell "run_init_operation", which the reference check already reports."""
    found = []
    label = "module-level code"
    lines = _line_map(tree.body)
    for node in tree.body:
        if (isinstance(node, ast.ImportFrom) and node.module == "_opf_init_operation"
                and node.level == 0):
            for alias in node.names:
                if alias.name == "run_init_operation" and alias.asname not in (
                        None, "run_init_operation"):
                    found.append("{}: {} imports run_init_operation under the alias {} (line {}); "
                                 "the module-level import must be unaliased".format(
                                     fname, label, alias.asname, lines[id(node)]))
    store_imports = set(map(id, _store_import_nodes(tree)))
    top = set(map(id, tree.body))
    init_names = sorted(set(direct_names) | set(init_aliases))
    claimed = set()
    scope = sorted(_module_scope_nodes(tree.body), key=lambda node: lines[id(node)])
    for node in scope:
        bound = set(_route_binding_names(node))
        if id(node) in store_imports:
            rebound = sorted(set(
                alias.asname or alias.name.split(".")[0] for alias in node.names
                if alias.name != "_opf_store") & set(store_aliases))
        else:
            rebound = sorted(bound & set(store_aliases))
        for name in init_names:
            if name not in bound:
                continue
            if (id(node) in top and name not in claimed
                    and _init_binding_allowed(node, name, direct_names, init_aliases)):
                claimed.add(name)
            else:
                rebound.append(name)
        for name in rebound:
            found.append(_rebind_finding(fname, label, name, lines[id(node)], store_aliases))
    return found


def _homes_rule_unit_findings(fname, unit_name, nodes, store_aliases, direct_names, init_aliases,
                              facts):
    """Findings for one production unit (a module-level function's body, or the module-level
    code), fail-closed over FORM: (a) every reference to run_init_operation or to a name the
    module's module-level `from _opf_init_operation import run_init_operation [as name]` binds
    (a Name, an attribute or the exact string) that is not the callee of a direct call is a
    finding, so is an import of run_init_operation anywhere but a module-level statement, and
    every direct call must pass homes2=<alias>.init_writes_homes2() with no splat and exactly
    one homes2; (b) every write to the rule's names, or through a store alias by the spellings
    _store_write_findings reads, is a finding wherever it appears, and so is the exact string
    "init_writes_homes2"; (c) a global declaration of a store alias or of the callee's spelling
    is a finding whether or not the unit calls the seam (module-level bindings are read by
    _homes_module_binding_findings); (d) a unit holding such a call may not bind the store alias
    or the callee's spelling by any form and names SUPPORTED_HOMES nowhere. Every line reported
    comes from _line_map, so a node without its own line number reports its enclosing one."""
    found = []
    label = unit_name or "module-level code"
    lines = _line_map(nodes)
    walked = [sub for node in nodes for sub in ast.walk(node)]
    calls = []
    callee_nodes = set()
    for node in walked:
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in direct_names:
                calls.append(node)
                callee_nodes.add(id(func))
            elif (isinstance(func, ast.Attribute) and func.attr == "run_init_operation"
                  and isinstance(func.value, ast.Name) and func.value.id in init_aliases):
                calls.append(node)
                callee_nodes.add(id(func))
    for node in walked:
        if id(node) in callee_nodes:
            continue
        if ((isinstance(node, ast.Name)
             and (node.id == "run_init_operation" or node.id in direct_names))
                or (isinstance(node, ast.Attribute) and node.attr == "run_init_operation")
                or (isinstance(node, ast.Constant) and node.value == "run_init_operation")):
            found.append(
                "{}: {} references run_init_operation other than as a direct call of the "
                "imported name (line {}); an alias, a getattr string, a partial, a __call__, a "
                "rebinding or a passed value cannot carry the homes rule".format(
                    fname, label, lines[id(node)]))
        if (isinstance(node, ast.ImportFrom) and node.module == "_opf_init_operation"
                and any(alias.name in ("run_init_operation", "*") for alias in node.names)
                and not (unit_name is None and any(node is stmt for stmt in nodes))):
            found.append("{}: {} imports run_init_operation other than by a module-level "
                         "statement (line {}); a direct call is recognized only through the "
                         "module-level import".format(fname, label, lines[id(node)]))
    rebind_names = set(store_aliases) | set(direct_names) | set(init_aliases)
    for node in walked:
        if isinstance(node, ast.Global):
            for name in sorted(set(node.names) & rebind_names):
                found.append(_rebind_finding(fname, label, name, lines[id(node)], store_aliases))
    for node in walked:
        for name in ("init_writes_homes2", "SUPPORTED_HOMES"):
            if _binds_name(node, name) and not _protected_bind_allowed(
                    fname, unit_name, node, name):
                found.append("{}: {} binds {} (line {}); only the store module's own "
                             "module-level definition binds the rule's names".format(
                                 fname, label, name, lines[id(node)]))
        found.extend(_store_write_findings(fname, label, node, store_aliases, lines[id(node)]))
        if isinstance(node, ast.Constant) and node.value == "init_writes_homes2":
            found.append("{}: {} carries the string \"init_writes_homes2\" (line {}); a "
                         "dynamic rebinding could ride it".format(fname, label, lines[id(node)]))
    rule_calls = {id(node): node for node in walked if _is_run_init_call(node)}
    rule_calls.update({id(node): node for node in calls})
    if rule_calls:
        shadow = rebind_names
        for node in walked:
            if any(_binds_name(node, name) for name in shadow) and not (
                    unit_name in direct_names and node in nodes
                    and isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))):
                found.append("{}: {} binds the store module alias or the callee's name "
                             "(line {}); the rule must be read through the module-level "
                             "imports".format(fname, label, lines[id(node)]))
            if ((isinstance(node, ast.Name) and node.id == "SUPPORTED_HOMES")
                    or (isinstance(node, ast.Attribute) and node.attr == "SUPPORTED_HOMES")
                    or (isinstance(node, ast.Constant) and node.value == "SUPPORTED_HOMES")):
                found.append("{}: {} names SUPPORTED_HOMES (line {})".format(
                    fname, label, lines[id(node)]))
    found.extend(_homes_dynamic_binding_findings(
        fname, label, walked, lines, rebind_names, facts["store_imports"], facts["foreign"],
        facts["guarded"], facts))
    for call in rule_calls.values():
        rule = [kw for kw in call.keywords if kw.arg == "homes2"]
        if (any(isinstance(arg, ast.Starred) for arg in call.args)
                or any(kw.arg is None for kw in call.keywords)):
            found.append("{}: {} splats its run_init_operation arguments (line {})".format(
                fname, label, lines[id(call)]))
        elif len(rule) != 1 or not _homes_rule_call(rule[0].value, store_aliases):
            found.append("{}: {} does not pass homes2=<_opf_store alias>.init_writes_homes2() "
                         "to run_init_operation (line {})".format(fname, label, lines[id(call)]))
    return found


_HOMES_SCAN_CACHE = {}


def _homes_internal_cause(exc):
    """The cannot-evaluate cause for an internal error in the homes rule itself: the rule fails
    closed with a finding that names the error, never a traceback. Formatting the error is
    itself guarded: an exception whose __str__ (or whose type's name) raises gives the named
    cause "an exception whose message cannot be formatted" instead of a second traceback."""
    try:
        return "internal error in the homes rule: {}: {}".format(type(exc).__name__, exc)
    except Exception:  # noqa: BLE001 -- the message itself failed; name that instead
        pass
    try:
        name = str(type(exc).__name__)
    except Exception:  # noqa: BLE001 -- even the type name failed
        name = "an unnamed exception type"
    return ("internal error in the homes rule: {} (an exception whose message cannot be "
            "formatted)".format(name))


def _homes_internal_error(fname, exc):
    """The cannot-evaluate finding for one source whose scan raised inside the rule."""
    return "{}: cannot evaluate ({})".format(fname, _homes_internal_cause(exc))


def _homes_parse(text):
    """The one parse of a handed source, in both the summary and the findings phase (a separate
    function so a self-test row can seed a fault at the parse boundary)."""
    return ast.parse(text)


def _homes_module_summary(text):
    """The per-source data _homes_rule_violations needs, memoized on the source TEXT, so a
    mutated directory scan (one changed file among many) never re-walks the unchanged trees: the
    parse outcome, the declared exemption registry (or its malformedness), the roster def and
    call counts, the names defined anywhere in the file, and a per-context findings memo. No AST
    is retained; a findings miss re-parses its one source."""
    key = (len(text), hash(text))
    cached = _HOMES_SCAN_CACHE.get(key)
    if cached is not None and cached["text"] == text:
        return cached
    summary = {"text": text, "error": None, "findings": {}}
    try:
        tree = _homes_parse(text)
    except (SyntaxError, ValueError, RecursionError, MemoryError) as exc:
        summary["error"] = type(exc).__name__
        _HOMES_SCAN_CACHE[key] = summary
        return summary
    except Exception as exc:  # noqa: BLE001 -- fail closed with a named cause
        summary["error"] = _homes_internal_cause(exc)
        _HOMES_SCAN_CACHE[key] = summary
        return summary
    try:
        registry, malformed = _homes_exempt_registry(tree)
        defcounts = {}
        callcounts = {}
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                defcounts[node.name] = defcounts.get(node.name, 0) + 1
                callcounts[node.name] = sum(
                    1 for sub in ast.walk(node) if _is_run_init_call(sub))
        all_def_names = {node.name for node in ast.walk(tree)
                         if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    except Exception as exc:  # noqa: BLE001 -- fail closed with a named cause
        summary["error"] = _homes_internal_cause(exc)
        _HOMES_SCAN_CACHE[key] = summary
        return summary
    summary.update(registry=registry, malformed=malformed, defcounts=defcounts,
                   callcounts=callcounts, all_def_names=all_def_names)
    _HOMES_SCAN_CACHE[key] = summary
    return summary


def _homes_rule_violations(sources):
    """Findings over `sources`, a dict of file name to source text covering the opf/tools
    modules. The rule: every run_init_operation call, and every write of the rule's names by a
    spelling listed below, in the handed sources is homes-compliant or lies inside a function
    named in its module's well-formed _HOMES_SELFTEST_EXEMPT registry, which is kept apart from
    the presence roster. A missing or unparsable source, a malformed registry (each a
    cannot-evaluate finding), a registered name without exactly one module-top-level def or
    naming a roster member, or a roster function absent or defined more than once at module
    level or not calling run_init_operation exactly once, is itself a finding.

    What this check CAN see: every unit of every handed source except the bodies of registered
    functions. The scanned units are the module-level code (class bodies, comprehensions, the
    __main__ block, defs nested inside module-level compound statements, and the decorator
    expressions and argument defaults of every module-top-level def, registered or not, which
    run at import) and each unregistered module-top-level function with its entire lexical
    contents. Exemption exists only by the per-module declared registry, checked for shape (one
    plain assignment of a tuple of unique nonempty string constants, placed before the first
    def and bound nowhere else), for exactly one module-top-level def per name, and for roster
    disjointness. A store alias is a name bound by `import _opf_store [as name]` in the module
    body or in a module-level try statement. Over every scanned unit it sees these spellings in
    the handed sources' ASTs: indirect references to run_init_operation or to its module-level
    imported alias (an alias, a getattr string, a functools.partial, a __call__ spelling, a
    rebinding, a passed value), an import of run_init_operation anywhere but a module-level
    statement, a non-rule homes2, a splat, bindings of init_writes_homes2 or SUPPORTED_HOMES
    anywhere, writes to an attribute of a store alias (assignment, del, setattr, delattr, an item
    of vars(<alias>) or <alias>.__dict__, or a raw vars(<alias>) or <alias>.__dict__ handle bound
    to a name by a plain, annotated or walrus assignment), a module-level binding of a store alias
    other than its import or a global declaration of one (whether or not the unit calls the
    seam), a module-level binding of the run_init_operation import's name other than that one
    import (a later def or import of it) or a global declaration of it, an aliased module-level
    import of run_init_operation (so a getattr or globals() string can only spell
    "run_init_operation", which is a finding), any binding of the store alias or the callee's
    name in a calling unit, and the exact rule-name strings. Every binding check reads names
    through the one binding-name function (_route_binding_names), so a match capture, a with
    target, a type alias or an annotated assignment counts like a plain assignment. An internal
    error raised while one source is scanned (its parse, its registry and roster summary, its
    alias and callee spellings, and every unit's findings) is a cannot-evaluate finding that
    names it, never a traceback.

    Disclosed residual, the registry's trust: a registered function is trusted to be test-only
    code. This check does NOT verify that registered code is unreachable from production: a
    production path that calls a registered function, by any spelling and from any module, is
    not a finding here, so a registration is a reviewed claim, not a checked one. Reachability
    of registered code from production is left to a separate rule.

    What it also CANNOT see: a call or reference assembled at runtime -- exec or eval on built
    text (refused by the maintenance-pin scan), a name spelled in pieces such as "run_init_" +
    "operation", a sys.modules or importlib or globals() lookup with a computed string; code
    never handed to it (modules outside opf/tools/*.py, extension modules, or a subprocess
    running python built from strings). opf.py's own init path is held by the route roster
    above, so the uncovered forms sit behind adjacent gates where they are covered at all.

    Fail-closed binding arm (_homes_dynamic_binding_findings). In a guarded module (one that
    holds a run_init_operation call or a callee spelling) the protected names are its store
    aliases, its run_init_operation import names and its _opf_init_operation aliases, and the
    rule ACCEPTS only these binding forms of them: the module-level imports named above (a store
    import may sit in a module-level try), the defining module's own def run_init_operation, a
    function-local binding in a unit that does not call the seam and declares no global, a class
    body binding, and a parameter of a unit that does not call the seam. These forms are
    findings whose text says to rewrite the binding in an accepted form: an attribute write or
    delete of a protected name on any object; a setattr or delattr naming a protected name, or
    with a name argument other than one string constant unless its first argument is a name
    bound only by imports of another module; a write through globals(), locals(), vars(),
    __dict__ or sys.modules (an item or attribute assigned or deleted, a mutating method, the
    mapping bound, passed or returned; only an item read, a copy, a read method, a membership
    test or an iteration of globals(), locals(), vars() or sys.modules is accepted, and a
    sys.modules item assignment whose key resolves to string constants naming no protected
    module and not this module); a reference to setattr, delattr, globals, locals, vars or
    __import__ other than as a callee, or to __setattr__ or __delattr__; `import sys as
    <other>`, `from sys import modules`, an import of a submodule of the store or init module,
    and a function-local `import _opf_store`. A star import is a finding in every module.

    Namespace-reach arm (_homes_namespace_reach_findings). In a guarded module the arm resolves
    no target and classifies no object: every dynamic namespace reach it lists is a finding
    wherever it appears. The list: a getattr, setattr or delattr whose name argument is not one
    plain string constant, and getattr referenced other than as a callee; a reference to
    builtins or __builtins__; an attribute __globals__, f_globals, f_locals, f_builtins,
    __dict__, __builtins__, __getattribute__, __code__, __defaults__ or __kwdefaults__, an
    attribute named globals, locals or vars, and a gc live-object accessor (get_objects,
    get_referrers, get_referents, garbage, callbacks) as an attribute, on any object; any
    subscript, item assignment or method call on globals(), locals(), vars(...) or a .modules
    attribute (sys.modules), and a subscript of a name an import binds; a call of __import__; a
    string constant naming any of these (or setattr, delattr, getattr, modules, builtins,
    __setattr__, __delattr__ or __import__) in any position; an import of builtins, ctypes,
    inspect or operator, a from-import of a gc live-object accessor, a from-import of the store
    module, and an import of the store module under a name other than the module's first store
    alias. The literal-name checks are kept: a literal getattr, setattr or delattr name is
    reported when it is a reach string, and the binding arm's protected-name checks still
    apply. An import of gc and its other names are not reported, since none returns a live
    object.

    Exceptions exist only through _HOMES_REACH_ALLOWLIST, a reviewed table of exact forms, each
    with the reason it is safe: a read of builtins where every binding of it in the file is a
    function-local integer; dict(<name>.__dict__) of a types.SimpleNamespace bound only so; a
    reach string that is the whole value of a plain assignment to names; a getattr, setattr or
    delattr whose name only ever takes literal non-reach, non-protected strings from a literal
    loop or a class's literal __slots__; one whose first argument is only another module's
    import; and a sys.modules item assignment naming an unrelated module.

    Residual. In a guarded module a protected name can be rebound with no finding only through
    (1) a form the allowlist accepts, exactly as its row states, and (2) anything not
    statically visible in the scanned module's own source: code another module runs (a write
    another module makes, a module that hands out a namespace or writer from inside a call,
    such as importlib, types, copyreg or a deserializer acting on data), a replacement of the
    store module in sys.modules by code that is not scanned before a module imports it, code
    never handed to the scan, and the bodies and annotations of registered defs. Each can change
    init_writes_homes2() for every fresh init, so this check guards against an accidental
    non-compliant call, not a deliberate rewrite.
    """
    found = []
    summaries = {}
    for fname in sorted(sources):
        text = sources[fname]
        if type(text) is not str:
            found.append("{}: source not supplied".format(fname))
            continue
        summary = _homes_module_summary(text)
        if summary["error"] is not None:
            found.append("{}: cannot evaluate ({})".format(fname, summary["error"]))
            continue
        if summary["malformed"] is not None:
            found.append("{}: cannot evaluate (malformed {} registry: {})".format(
                fname, _HOMES_EXEMPT_NAME, summary["malformed"]))
            continue
        summaries[fname] = summary
    for fname, func in _HOMES_RULE_SITES:
        if fname not in sources:
            found.append("{}: source not supplied".format(fname))
            continue
        summary = summaries.get(fname)
        if summary is None:
            continue
        count = summary["defcounts"].get(func, 0)
        if count != 1:
            found.append("{}: {} is defined {} times at module level".format(fname, func, count))
            continue
        calls = summary["callcounts"].get(func, 0)
        if calls != 1:
            found.append("{}: {} calls run_init_operation {} times, not once".format(
                fname, func, calls))
    roster_by_file = {}
    for site_fname, func in _HOMES_RULE_SITES:
        roster_by_file.setdefault(site_fname, set()).add(func)
    effective = {}
    for fname in sorted(summaries):
        summary = summaries[fname]
        names = []
        for name in summary["registry"]:
            # A registration grants nothing unless it is valid: an invalid name is reported
            # and the function (if any) stays scanned, so both arms fail closed.
            if name in roster_by_file.get(fname, ()):
                found.append("{}: the roster function {} may not be exempt; roster entries "
                             "are production by contract".format(fname, name))
                continue
            count = summary["defcounts"].get(name, 0)
            if count == 0 and name in summary["all_def_names"]:
                found.append("{}: the exempt function {} is not a module-top-level def".format(
                    fname, name))
                continue
            if count != 1:
                found.append("{}: the exempt function {} is defined {} times at module "
                             "level".format(fname, name, count))
                continue
            names.append(name)
        effective[fname] = frozenset(names)
    for fname in sorted(summaries):
        summary = summaries[fname]
        registered = effective[fname]
        # The findings memo keys on the file name and its effective registry, so a mutated
        # directory scan re-walks only the changed source.
        cache_key = (fname, registered)
        cached = summary["findings"].get(cache_key)
        if cached is None:
            # The findings-phase parse catches every Exception subclass: a parse error by its
            # type, anything else as a named internal error, each a cannot-evaluate finding.
            # (KeyboardInterrupt, SystemExit and GeneratorExit are not Exception subclasses
            # and are left to propagate.)
            try:
                tree = _homes_parse(summary["text"])
            except (SyntaxError, ValueError, RecursionError, MemoryError) as exc:
                found.append("{}: cannot evaluate ({})".format(fname, type(exc).__name__))
                continue
            except Exception as exc:  # noqa: BLE001 -- fail closed with a named cause
                found.append(_homes_internal_error(fname, exc))
                continue
            # Everything from the alias and callee spellings to the last unit's findings runs
            # inside one catch, so an internal error anywhere in it is a named finding.
            try:
                store_aliases = set(_store_import_aliases(tree))
                direct_names, init_aliases = _init_callee_spellings(tree)
                # Decorator expressions and argument defaults run at import, so EVERY
                # module-top-level def's definition-time expressions scan with the module-level
                # unit, an exempt function's included; an exempt function's body (and only its
                # body) is skipped.
                module_nodes = [node for node in tree.body
                                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]
                module_nodes += [expr for node in tree.body
                                 if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                                 for expr in _definition_time_exprs(node)]
                units = [(None, module_nodes)]
                units += [(node.name, [node]) for node in tree.body
                          if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                          and node.name not in registered]
                # Every binding that lands in the module namespace (a module-top-level def,
                # registered or not, included) is read once, over the whole module scope.
                cached = _homes_module_binding_findings(
                    fname, tree, store_aliases, direct_names, init_aliases)
                # The fail-closed binding arm runs over every unit of a module that holds a
                # seam call or a callee spelling (a guarded module); a star import is a finding
                # in every module.
                facts = dict(
                    store_imports=frozenset(map(id, _store_import_nodes(tree))),
                    foreign=_homes_foreign_module_names(fname, tree),
                    file_binds=_homes_binding_index(list(ast.walk(tree))),
                    local_ids=_homes_local_ids(tree),
                    store_aliases=tuple(_store_import_aliases(tree)),
                    guarded=bool(direct_names or init_aliases)
                    or any(_is_run_init_call(sub) for sub in ast.walk(tree)))
                for unit_name, nodes in units:
                    cached.extend(_homes_rule_unit_findings(
                        fname, unit_name, nodes, store_aliases, direct_names, init_aliases,
                        facts))
            except Exception as exc:  # noqa: BLE001 -- fail closed with a named cause
                cached = [_homes_internal_error(fname, exc)]
            summary["findings"][cache_key] = cached
        found.extend(cached)
    return found


def _route_external_binding_ok(node, name):
    """True only when node is exactly the pinned import form for the external
    base name: a plain `import name` (no alias, the module under its own name)
    or the pinned `from module import name` (no alias, absolute). Every other
    binding of an external base, assignments and import aliases included, is a
    violation wherever it appears on the scanned surface."""
    pinned = _ROUTE_EXTERNAL_IMPORTS.get(name)
    if pinned is None:
        return False
    if pinned[0] == "import":
        if not isinstance(node, ast.Import):
            return False
        for alias in node.names:
            if (alias.asname or alias.name.split(".")[0]) == name:
                if alias.asname is not None or alias.name != name:
                    return False
        return True
    if not isinstance(node, ast.ImportFrom) or node.level != 0:
        return False
    if node.module != pinned[1]:
        return False
    for alias in node.names:
        if (alias.asname or alias.name.split(".")[0]) == name:
            if alias.asname is not None or alias.name != pinned[2]:
                return False
    return True


def _route_unit_params(sub):
    """Parameter names a def or lambda binds (positional of both kinds,
    keyword-only, *args and **kwargs)."""
    spec = sub.args
    names = [arg.arg for arg in
             list(spec.posonlyargs) + list(spec.args) + list(spec.kwonlyargs)]
    if spec.vararg is not None:
        names.append(spec.vararg.arg)
    if spec.kwarg is not None:
        names.append(spec.kwarg.arg)
    return names


def _route_literal_bound(unit, name_id):
    """True when unit's OWN scope binds name_id at least once, ONLY by plain
    assignments of a string literal to plain names, and no nested def, lambda or
    class binds or receives it at all: only then does every read of the name in
    unit resolve to one of those literal bindings, so it may serve as a rendering
    template (QA round 9, requirement B: the remedy templates are branch-selected
    literals bound to one name). The round-9 rule walked nested defs as if they
    shared the outer scope and never saw match capture patterns, so a match
    capture rebinding the name to the subject value, and a literal binding hidden
    inside a nested def while the outer read resolved to a module global, both
    passed (QA round 10 MAJOR 2); _route_binding_names now yields match captures
    (not plain Assigns, so they refuse here), and a binding or parameter inside
    any nested unit refuses outright. A global or nonlocal declaration of the
    name is a non-Assign binding and refuses the same way."""
    if name_id in _route_unit_params(unit):
        return False
    if any(param.name == name_id
           for param in getattr(unit, "type_params", [])):
        return False
    bindings = 0
    stack = [(unit, False)]
    while stack:
        node, nested = stack.pop()
        for child in ast.iter_child_nodes(node):
            stack.append((child, nested or isinstance(
                child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda,
                        ast.ClassDef))))
        if node is unit:
            continue
        bound_here = list(_route_binding_names(node))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            bound_here += _route_unit_params(node)
        for bound in bound_here:
            if bound != name_id:
                continue
            if nested:
                return False
            bindings += 1
            if not (isinstance(node, ast.Assign)
                    and all(isinstance(target, ast.Name)
                            for target in node.targets)
                    and isinstance(node.value, ast.Constant)
                    and isinstance(node.value.value, str)):
                return False
    return bindings > 0


def _route_call_spelling(func):
    """Spell a call's target for the permitted-call allowlist: a Name is its id, an
    attribute chain is spelled dotted down to a Name base, a string-literal base is
    spelled str-literal (so " ".join and "{}".format resolve without admitting
    arbitrary objects), and a call base is spelled with a () suffix (read().out.strip),
    so a chain is allowed only as the exact spelled shape. Anything else (a subscript,
    a conditional expression, an operator result, a lambda) returns None: a COMPUTED
    call target, which the gate refuses outright."""
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        base = _route_call_spelling(func.value)
        return None if base is None else base + "." + func.attr
    if isinstance(func, ast.Constant) and isinstance(func.value, str):
        return "str-literal"
    if isinstance(func, ast.Call):
        inner = _route_call_spelling(func.func)
        return None if inner is None else inner + "()"
    return None


def _route_binding_names(node):
    """Names a statement or clause BINDS (not reads): assignment targets of every kind
    (plain, annotated, augmented, starred, tuple/list, walrus), loop / with / except /
    comprehension targets, import aliases, nested def / class statements, and match
    capture patterns (MatchAs, MatchStar, and a MatchMapping rest; QA round 10 MAJOR
    2: a case clause rebinds its capture name to the match subject, and the round-9
    scan never saw it), PEP 695 type alias statements (the alias name a `type X = ...`
    binds) and type parameters (the names a def's, class's or type alias's
    [T]/[*Ts]/[**P] list binds; QA round 12 D: the round-11 scan saw neither, so a
    name a `type` statement or a type parameter rebound passed the literal-only
    template rule and the rebinding rules), and an async-for target. Used for
    two refusals: a rebinding of a pinned route name anywhere in opf.py, and a
    module-level rebinding of any function on the init call graph."""
    names = []
    targets = []
    if isinstance(node, ast.Assign):
        targets = list(node.targets)
    elif isinstance(node, (ast.AnnAssign, ast.AugAssign, ast.NamedExpr)):
        targets = [node.target]
    elif isinstance(node, (ast.For, ast.AsyncFor)):
        targets = [node.target]
    elif isinstance(node, ast.withitem) and node.optional_vars is not None:
        targets = [node.optional_vars]
    elif isinstance(node, ast.comprehension):
        targets = [node.target]
    elif isinstance(node, ast.ExceptHandler) and node.name:
        names.append(node.name)
    elif isinstance(node, (ast.Import, ast.ImportFrom)):
        for alias in node.names:
            names.append(alias.asname or alias.name.split(".")[0])
    elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        names.append(node.name)
        names.extend(param.name for param in node.type_params)
    elif isinstance(node, ast.TypeAlias):
        targets = [node.name]
        names.extend(param.name for param in node.type_params)
    elif isinstance(node, (ast.TypeVar, ast.ParamSpec, ast.TypeVarTuple)):
        names.append(node.name)
    elif isinstance(node, (ast.Global, ast.Nonlocal)):
        names.extend(node.names)
    elif isinstance(node, ast.MatchAs) and node.name is not None:
        names.append(node.name)
    elif isinstance(node, ast.MatchStar) and node.name is not None:
        names.append(node.name)
    elif isinstance(node, ast.MatchMapping) and node.rest is not None:
        names.append(node.rest)
    elif isinstance(node, ast.Delete):
        targets = list(node.targets)
    stack = list(targets)
    while stack:
        sub = stack.pop()
        if isinstance(sub, ast.Name):
            names.append(sub.id)
        elif isinstance(sub, (ast.Tuple, ast.List)):
            stack.extend(sub.elts)
        elif isinstance(sub, ast.Starred):
            stack.append(sub.value)
        # a Subscript or Attribute target binds no NAME: its base and index are
        # reads, which the reference scan checks in their own right
    return names


def _init_route_violations(source):
    """PERMITTED-REFERENCE output-route gate over opf.py (QA round 7, requirement B;
    QA round 8: the denylist became a permitted-CALL allowlist; QA round 9,
    requirement A: the call allowlist became this REFERENCE allowlist, because a
    spelled output function passed whole as an argument -- sorted(rest,
    key=os.system) -- and an import alias of a permitted name -- from os import
    writev as ascii -- both reached a descriptor with zero violations). It computes
    the set of module-level opf.py functions and classes REACHABLE from _cmd_init
    (every Name that resolves to a module-level def, transitively; a duplicate
    module-level definition or a module-level rebinding of a reachable def refuses
    as unresolvable) and, in every reachable function except the one emitter
    _init_echo, requires EVERY reference -- a call target, a bare Name read
    anywhere (an argument, a callback keyword, a default, a subscript base), and
    every maximal attribute chain in any context, store context included -- to
    resolve through _route_call_spelling to a name the function itself binds, to a
    reachable module-level definition, or to an entry in _ROUTE_ALLOWED_REFS,
    SCOPED to this one function; an unresolvable (computed) reference and a
    reference outside the function's allowed set are both violations. It
    additionally refuses BY NAME any reference to print, builtins, stdout, stderr,
    write, writelines, warnings or logging; any binding, at module level or inside
    a reachable function, of a rostered EXTERNAL base (builtins and modules) other
    than its single pinned import form (_ROUTE_EXTERNAL_IMPORTS), so an import
    alias or assignment cannot shadow an allowed spelling; any binding of a pinned
    route name (the emitter, the renderers and the two rendering types) anywhere in
    the file; any binding of a reachable definition's name inside a reachable
    function; any assert statement in any reachable function (QA round 10 MAJOR 1:
    an assert raises AssertionError carrying its message expression, and the
    round-9 raise rule never saw ast.Assert); any departure of _cmd_init from the
    pinned last-resort boundary shape, and any raise or assert inside it (QA
    round 10 MAJOR 1: no static rule can refuse every implicit exception, a
    subscript's KeyError or an IndexError included, so _cmd_init must BE the one
    boundary that catches BaseException and prints only rendered text through the
    emitter, its own report guarded so a failing print cannot re-raise into a
    chained traceback; the suite additionally pins the wrapper's exact bytes,
    which covers the rendering calls this shape rule does not read); any raise in
    _init_main lexically outside the body of the one try whose handlers catch
    _InitPriorStoreRefusal, unless it constructs _InitPriorStoreRefusal itself
    with no raise ... from cause (the constructor renders its message at
    construction; QA round 9, claude MAJOR 1: a raise before that try printed a
    raw argv value in the traceback; QA round 10 MAJOR 1: a cause rides the
    chained traceback raw; either way the last-resort boundary now renders
    whatever escapes at runtime, and this rule stays as the narrower static pin);
    and any
    _InitRestoreLine or _InitPriorStoreRefusal construction in which a
    template-bearing argument (by position OR keyword, per the real constructor
    signatures in _ROUTE_RENDERED_TEMPLATES) is neither a string literal nor a name
    the constructing function's OWN scope binds only by plain assignments of string
    literals, with no nested def, lambda, match capture, type alias or type
    parameter binding (_route_literal_bound; QA round 10 MAJOR 2; QA round 12 B, D),
    or which passes a template through a * or ** splat the static scan cannot resolve. Any ClassDef whose
    bases or keywords reference a rendered type, anywhere in the file, and any
    ClassDef inside a reachable function, are refused too (QA round 12 MAJOR,
    both reviewers: a declared local subclass of _InitPriorStoreRefusal
    inherited the rendering constructor and carried a hostile value in the
    template position, which the construction rule, keyed on the constructor
    NAME, never read; the runtime closure for construction routes this scan
    cannot see at all is the output-side rendering, QA round 13 A).
    str.__new__ is rostered for
    _InitRestoreLine alone, so minting an instance of that type anywhere else on
    the graph is a violation. Returns a list of violation descriptions; the suite
    requires it empty on the real source and NON-empty on every seeded mutant, so
    each recognizer is discriminated. RESIDUAL (what the static pin cannot see, stated
    exactly): a static reference check sees spelled references, not values,
    dataflow or runtime bindings. It cannot see what an ALLOWED spelling does at
    runtime on a hostile or rebound OBJECT, code in ANOTHER MODULE (the sibling
    _opf_* modules the init path calls into), or introspection REACHED at runtime
    through allowed spellings; no introspection spelling (getattr, globals, vars,
    __import__) is itself in any roster. The suite's RUNTIME paste test, which
    drives hostile values through every init exit path and feeds every printed
    line alone to real shells, is the primary guard for those; this gate keeps the
    reachable reference surface enumerable by a reviewer."""
    tree = ast.parse(source)
    violations = []
    top = dict()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name in top:
                violations.append(
                    "call graph unresolved: {} is defined more than once at module "
                    "level (lines {} and {})".format(
                        node.name, top[node.name].lineno, node.lineno))
            top[node.name] = node
    if violations:
        return violations
    if "_cmd_init" not in top:
        return ["call graph unresolved: _cmd_init is not a module-level definition"]
    if (_ROUTE_EMITTER not in top
            or not isinstance(top[_ROUTE_EMITTER], ast.FunctionDef)):
        return ["output route gate: the emitter {} is not a module-level "
                "function".format(_ROUTE_EMITTER)]

    reachable = set(["_cmd_init"])
    queue = ["_cmd_init"]
    while queue:
        for sub in ast.walk(top[queue.pop()]):
            if (isinstance(sub, ast.Name) and sub.id in top
                    and sub.id not in reachable):
                reachable.add(sub.id)
                queue.append(sub.id)

    # A module-level statement that rebinds a reachable definition makes the name's
    # runtime binding ambiguous: refuse as unresolvable rather than guessing. A
    # module-level binding of a rostered external base other than its pinned import
    # form shadows an allowed spelling: refused (QA round 9, codex MAJOR 1).
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        for name in _route_binding_names(node):
            if name in reachable:
                violations.append(
                    "call graph unresolved: module-level statement at line {} rebinds "
                    "the reachable definition {}".format(node.lineno, name))
            if (name in _ROUTE_EXTERNAL_BASES
                    and not _route_external_binding_ok(node, name)):
                violations.append(
                    "module-level statement at line {} binds the external base {} "
                    "outside its pinned import form".format(node.lineno, name))

    # A binding of a pinned route name ANYWHERE in the file (outside the pinned
    # definition itself) can redirect or disarm the route: refused by name.
    for node in ast.walk(tree):
        if node in tree.body and isinstance(
                node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        for name in _route_binding_names(node):
            if name in _ROUTE_PINNED:
                violations.append(
                    "line {}: binding of the pinned route name {}".format(
                        getattr(node, "lineno", 0), name))

    # A subclass of a rendered type, ANYWHERE in the file (QA round 12 MAJOR,
    # both reviewers): it inherits a rendering constructor, and a call to it is
    # not a construction the template rule reads, so a hostile value in the
    # template position passed the round-12 gate with zero violations. The
    # bases and keywords (a metaclass spelling included) are walked whole, so a
    # rendered name anywhere in a base expression refuses.
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for expr in list(node.bases) + [kw.value for kw in node.keywords]:
                for sub in ast.walk(expr):
                    if isinstance(sub, ast.Name) and sub.id in _ROUTE_RENDERED:
                        violations.append(
                            "line {}: class {} derives from the rendered type "
                            "{}".format(node.lineno, node.name, sub.id))

    # The boundary rule (QA round 10 MAJOR 1): an assert, a raise ... from, and
    # an implicit exception (a subscript's KeyError, an IndexError) raised in the
    # round-9 argument loop sat outside both rendering handlers and printed a raw
    # argv value in an escaping traceback, and no static rule can refuse every
    # implicit exception. _cmd_init is therefore required to BE the one
    # last-resort rendering boundary: its whole body is one try whose body only
    # returns _init_main(rest) and whose ONE handler catches BaseException,
    # prints through the emitter inside its own BaseException-guarded try (a
    # failing report must not re-raise: a chained traceback prints the original
    # message raw) and then returns; no raise and no assert may appear anywhere
    # in _cmd_init. The suite additionally pins the wrapper's exact bytes, which
    # covers what this shape rule does not read (the rendering calls inside the
    # guarded report).
    cmd_unit = top["_cmd_init"]
    cmd_body = [stmt for stmt in cmd_unit.body
                if not (isinstance(stmt, ast.Expr)
                        and isinstance(stmt.value, ast.Constant)
                        and isinstance(stmt.value.value, str))]

    def _boundary_guard(handler):
        # ONE guarded emitter report, then a plain return: the guard's single
        # handler catches BaseException with a bare pass.
        if len(handler.body) != 2:
            return False
        guard, final = handler.body
        return (isinstance(guard, ast.Try)
                and not guard.orelse and not guard.finalbody
                and len(guard.handlers) == 1
                and isinstance(guard.handlers[0].type, ast.Name)
                and guard.handlers[0].type.id == "BaseException"
                and all(isinstance(sub, ast.Pass)
                        for sub in guard.handlers[0].body)
                and all(isinstance(sub, ast.Expr)
                        and isinstance(sub.value, ast.Call)
                        and isinstance(sub.value.func, ast.Name)
                        and sub.value.func.id == _ROUTE_EMITTER
                        for sub in guard.body)
                and isinstance(final, ast.Return))

    boundary_ok = (
        len(cmd_body) == 1 and isinstance(cmd_body[0], ast.Try)
        and not cmd_body[0].orelse and not cmd_body[0].finalbody
        and len(cmd_body[0].body) == 1
        and isinstance(cmd_body[0].body[0], ast.Return)
        and isinstance(cmd_body[0].body[0].value, ast.Call)
        and isinstance(cmd_body[0].body[0].value.func, ast.Name)
        and cmd_body[0].body[0].value.func.id == "_init_main"
        and len(cmd_body[0].handlers) == 1
        and isinstance(cmd_body[0].handlers[0].type, ast.Name)
        and cmd_body[0].handlers[0].type.id == "BaseException"
        and _boundary_guard(cmd_body[0].handlers[0]))
    if not boundary_ok:
        violations.append(
            "_cmd_init: not the pinned last-resort rendering boundary (one try "
            "returning _init_main(rest), one BaseException handler whose guarded "
            "emitter report cannot re-raise, then a plain return)")
    for sub in ast.walk(cmd_unit):
        if isinstance(sub, (ast.Raise, ast.Assert)):
            violations.append(
                "_cmd_init line {}: raise or assert inside the last-resort "
                "boundary".format(sub.lineno))

    # An assert ANYWHERE on the reachable surface is refused (QA round 10 MAJOR
    # 1: assert raises AssertionError carrying its message expression, and the
    # round-9 raise rule only saw ast.Raise).
    for name in sorted(reachable):
        for sub in ast.walk(top[name]):
            if isinstance(sub, ast.Assert):
                violations.append(
                    "{} line {}: assert statement on the reachable init "
                    "surface".format(name, sub.lineno))

    # The raise rule (QA round 9, claude MAJOR 1; QA round 10 MAJOR 1): inside
    # _init_main, a raise that is not lexically inside the BODY of the one try
    # whose handlers catch _InitPriorStoreRefusal escapes both of _init_main's
    # rendering handlers (the last-resort boundary still renders it at runtime;
    # this rule stays as the narrower static pin), so every such raise must
    # construct _InitPriorStoreRefusal itself (its constructor renders at
    # construction) and carry NO raise ... from cause (QA round 10 MAJOR 1: a
    # cause rides the chained traceback raw).
    if "_init_main" not in reachable or not isinstance(
            top.get("_init_main"), ast.FunctionDef):
        violations.append(
            "call graph unresolved: _init_main is not a reachable module-level "
            "function")
    else:
        main_unit = top["_init_main"]
        handled_tries = [sub for sub in ast.walk(main_unit)
                         if isinstance(sub, ast.Try)
                         and any(isinstance(handler.type, ast.Name)
                                 and handler.type.id == "_InitPriorStoreRefusal"
                                 for handler in sub.handlers)]
        if len(handled_tries) != 1:
            violations.append(
                "_init_main: expected exactly one try handling "
                "_InitPriorStoreRefusal, found {}".format(len(handled_tries)))
        else:
            inside_try = set()
            for stmt in handled_tries[0].body:
                for sub in ast.walk(stmt):
                    inside_try.add(id(sub))
            for sub in ast.walk(main_unit):
                if isinstance(sub, ast.Raise) and id(sub) not in inside_try:
                    permitted_raise = (
                        isinstance(sub.exc, ast.Call)
                        and isinstance(sub.exc.func, ast.Name)
                        and sub.exc.func.id == "_InitPriorStoreRefusal"
                        and sub.cause is None)
                    if not permitted_raise:
                        violations.append(
                            "_init_main line {}: raise outside the handled try "
                            "does not construct a cause-free "
                            "_InitPriorStoreRefusal".format(sub.lineno))

    for name in sorted(reachable):
        if name == _ROUTE_EMITTER:
            continue
        unit = top[name]
        allowed = _ROUTE_ALLOWED_REFS.get(name, frozenset())
        local_defs = set(sub.name for sub in ast.walk(unit)
                         if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef,
                                             ast.ClassDef)))
        local_bound = set(local_defs)
        for sub in ast.walk(unit):
            if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                local_bound.update(_route_unit_params(sub))
            if sub is unit:
                continue
            if isinstance(sub, ast.ClassDef):
                # No class is defined inside any reachable init function: a
                # local class can inherit a rendered constructor through ANY
                # base expression, computed ones included, so the whole form
                # is refused rather than enumerating base spellings (QA
                # round 12 MAJOR; QA round 13 A).
                violations.append(
                    "{} line {}: class definition inside a reachable init "
                    "function".format(name, sub.lineno))
            for bound in _route_binding_names(sub):
                local_bound.add(bound)
                if (bound in _ROUTE_EXTERNAL_BASES
                        and not _route_external_binding_ok(sub, bound)):
                    violations.append(
                        "{} line {}: binding of the external base {} outside its "
                        "pinned import form".format(
                            name, getattr(sub, "lineno", 0), bound))
                if bound in reachable:
                    violations.append(
                        "{} line {}: binding of the reachable definition name "
                        "{}".format(name, getattr(sub, "lineno", 0), bound))
        # A node on the SPINE of a longer chain (an attribute base, a call target)
        # is checked as part of that chain, never alone: only MAXIMAL references
        # are resolved against the roster.
        interior = set()
        for sub in ast.walk(unit):
            if isinstance(sub, ast.Attribute):
                interior.add(id(sub.value))
            elif isinstance(sub, ast.Call):
                interior.add(id(sub.func))
        for sub in ast.walk(unit):
            if isinstance(sub, ast.Call):
                spelled = _route_call_spelling(sub.func)
                if spelled is None:
                    violations.append(
                        "{} line {}: computed call target (not a spellable name, "
                        "attribute chain or call chain)".format(name, sub.lineno))
                elif not (spelled in top or spelled in local_defs
                          or spelled in allowed):
                    violations.append(
                        "{} line {}: call target {} is not in the "
                        "permitted-reference roster for this function".format(
                            name, sub.lineno, spelled))
            if isinstance(sub, ast.Name) and id(sub) not in interior:
                if (isinstance(sub.ctx, ast.Load)
                        and not (sub.id in local_bound or sub.id in top
                                 or sub.id in allowed)):
                    violations.append(
                        "{} line {}: reference to the name {} is not permitted "
                        "in this function".format(name, sub.lineno, sub.id))
            elif isinstance(sub, ast.Attribute) and id(sub) not in interior:
                spelled = _route_call_spelling(sub)
                if spelled is None:
                    violations.append(
                        "{} line {}: computed attribute reference (not a "
                        "spellable chain)".format(name, sub.lineno))
                elif not (spelled in allowed):
                    violations.append(
                        "{} line {}: reference to the attribute chain {} is not "
                        "in the permitted-reference roster for this "
                        "function".format(name, sub.lineno, spelled))
            if isinstance(sub, ast.Name) and sub.id in _ROUTE_BANNED:
                violations.append("{} line {}: reference to the banned name "
                                  "{}".format(name, sub.lineno, sub.id))
            elif isinstance(sub, ast.Attribute) and sub.attr in _ROUTE_BANNED:
                violations.append("{} line {}: reference to the banned attribute "
                                  "{}".format(name, sub.lineno, sub.attr))
            elif isinstance(sub, (ast.Import, ast.ImportFrom)):
                module = getattr(sub, "module", None) or ""
                pieces = [alias.name.split(".")[0] for alias in sub.names]
                pieces += [alias.asname for alias in sub.names if alias.asname]
                pieces.append(module.split(".")[0])
                for piece in pieces:
                    if piece in _ROUTE_BANNED:
                        violations.append(
                            "{} line {}: import of the banned name {}".format(
                                name, sub.lineno, piece))
            elif isinstance(sub, ast.keyword) and sub.arg in _ROUTE_BANNED:
                violations.append("{} line {}: keyword named {}".format(
                    name, sub.lineno, sub.arg))
            if (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name)
                    and sub.func.id in _ROUTE_RENDERED):
                positions = _ROUTE_RENDERED_TEMPLATES[sub.func.id]
                keyword_params = frozenset(positions.values())
                templates = []
                # A * splat in the arguments or a ** splat in the keywords can land
                # a non-literal on any template parameter and the static scan cannot
                # see which, so either refuses outright (QA round 12 B).
                if any(isinstance(arg, ast.Starred) for arg in sub.args):
                    violations.append(
                        "{} line {}: {} construction with a * splat in its "
                        "positional arguments".format(
                            name, sub.lineno, sub.func.id))
                if any(kw.arg is None for kw in sub.keywords):
                    violations.append(
                        "{} line {}: {} construction with a ** splat in its "
                        "keywords".format(name, sub.lineno, sub.func.id))
                # Every template-bearing parameter, by position AND by keyword.
                for position in positions:
                    if position < len(sub.args):
                        templates.append(sub.args[position])
                supplied = set()
                for kw in sub.keywords:
                    if kw.arg in keyword_params:
                        templates.append(kw.value)
                        supplied.add(kw.arg)
                # The required template (position 0 / its keyword) must be present
                # unless a ** splat could carry it; a call supplying it no way is a
                # construction without a template.
                if (len(sub.args) == 0 and positions[0] not in supplied
                        and not any(kw.arg is None for kw in sub.keywords)):
                    violations.append(
                        "{} line {}: {} construction without a template".format(
                            name, sub.lineno, sub.func.id))
                for expr in templates:
                    literal = (isinstance(expr, ast.Constant)
                               and isinstance(expr.value, str))
                    pinned_name = (isinstance(expr, ast.Name)
                                   and _route_literal_bound(unit, expr.id))
                    if not (literal or pinned_name):
                        violations.append(
                            "{} line {}: {} template is not a string literal or a "
                            "literal-only-bound name".format(
                                name, sub.lineno, sub.func.id))
    return violations


def _gen2_cli_vectors(check, make_git, git_call, fd_capture, inert):
    """Plain `opf init` on homes generation 2 (spec 4.2), driven in process with both tooling gates
    raised inside each vector (SUPPORTED_HOMES 2, SUPPORTED_SPEC_VERSION at IMPORTED_SPEC_VERSION) and
    restored in a finally. While they are raised, the spec 16.1 runtime check of a verb run through
    opf.main reads the declaration the raised build ships (opf._running_build_declaration), not the
    installed one, which declares the shipped constants. The homes .gitignore inspection refuses the
    fixture's GIT_CONFIG_GLOBAL and GIT_CONFIG_SYSTEM overrides, so those two are unset around each
    raised vector. The last vector pins the shipped constants: the payload names, their order and
    their bytes are the ones the per-document builders (build_manifest, build_counters,
    build_version, build_worklog and build_index) produced before the shared bootstrap builder."""
    import tomllib
    from unittest import mock
    import _opf_check
    import _opf_init
    import _opf_init_operation
    import _opf_observe
    import _opf_store
    import _opf_write_guard
    import opf

    working = _opf_store.WORKING_DIRNAME
    machine = working + "/" + _opf_store.DEFAULT_MACHINE_SUBDIR
    gitignore = working + "/.gitignore"

    @contextlib.contextmanager
    def gen2():
        saved = (_opf_store.SUPPORTED_HOMES, _opf_store.SUPPORTED_SPEC_VERSION)
        env = {k: os.environ.pop(k, None) for k in ("GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM")}
        _opf_store.SUPPORTED_HOMES = 2
        _opf_store.SUPPORTED_SPEC_VERSION = _opf_store.IMPORTED_SPEC_VERSION
        try:
            with opf._running_build_declaration():
                yield
        finally:
            _opf_store.SUPPORTED_HOMES, _opf_store.SUPPORTED_SPEC_VERSION = saved
            for key, value in env.items():
                if value is not None:
                    os.environ[key] = value

    def created_paths(output):
        rows = [json.loads(line) for line in output.splitlines() if line.startswith("{")]
        return next((row.get("paths") for row in rows if row.get("event") == "created"), None)

    def drive(root, patches=()):
        def driven():
            with contextlib.ExitStack() as stack:
                for target, attr, value in patches:
                    stack.enter_context(mock.patch.object(target, attr, value))
                return opf._cmd_init(["--root", str(root)])
        return fd_capture(driven)

    # One generation-2 run, its creates traced. The created set is the init operation's bootstrap
    # roster for the same model plus CHANGELOG.md (red when the shared builder drops the imported
    # leaves: the roster still names them), and .working/.gitignore is the FIRST create, so its
    # ignore rules exist before any store file (red when it is moved to the end of the payloads).
    real_create = opf._init_create
    creates = []

    def tracing_create(root_fd, relpath, data):
        creates.append(relpath)
        return real_create(root_fd, relpath, data)

    root = make_git("gen2-cli")
    with gen2():
        model = _opf_init._manifest_model(homes2=True)
        roster = set(_opf_init_operation.bootstrap_source_roster(model))
        rc, output = drive(root, ((opf, "_init_create", tracing_create),))
    paths = created_paths(output)
    leaves = {machine + "/" + name
              for name in _opf_store.imported_leaf_names(_opf_store.BASELINE_TYPES)}
    check("init-cli-gen2-roster",
          rc == EXIT_OK and paths is not None and len(paths) == len(set(paths))
          and leaves | {gitignore} <= roster
          and set(paths) == roster | {"CHANGELOG.md"})
    check("init-cli-gen2-gitignore-first",
          rc == EXIT_OK and creates[:1] == [gitignore]
          and machine + "/" + _opf_store.MANIFEST_NAME in creates[1:])

    # The same store, committed, then rendered and committed again (the order init's own hint
    # gives), grades VALID under the raised gates with the homes-2 roster plus the four imported
    # checks (red when the counters lose the model's imported rows: C-COUNTERS then finds each
    # missing imported counter row).
    verdict = None
    if rc == EXIT_OK:
        with gen2():
            git_call(root, ["add", "-A"])
            git_call(root, ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "init"])
            render_rc, _render_output = fd_capture(
                lambda: opf.main(["render", "--write", "--root", str(root)]))
            if render_rc == EXIT_OK:
                # Render refuses an unsound store and writes nothing; only views it wrote commit.
                git_call(root, ["add", "-A"])
                git_call(root, ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "render"])
            resolution = _opf_store.resolve_store(root)
            observations, _notes = _opf_observe.gather(resolution)
            verdict = _opf_check.validate_store(resolution, observations=observations)
            roster_checks = list(_opf_check.required_checks(2, True))
            counters = tomllib.loads(
                (root / machine / _opf_check.COUNTERS_NAME).read_text(encoding="utf-8"))
    check("init-cli-gen2-doctor-valid",
          verdict is not None and render_rc == EXIT_OK and verdict.status == _opf_store.VALID
          and not verdict.findings and not verdict.cannot_evaluate
          and list(verdict.checks) == roster_checks
          and all(v == "PASS" for v in verdict.checks.values())
          and counters["counters"].get("imported:BI") == 0)

    # A hold the inspection names refuses in the preflight: nothing created and the init mutex
    # never taken (red when the preflight inspection is removed: the run then succeeds, or, with
    # only the re-inspection under the mutex left, refuses after taking the mutex).
    def held(_root, _verb):
        return (_opf_store.render_homes_gitignore().encode("utf-8"), ["synthetic-hold"], None)

    root = make_git("gen2-cli-held")
    before = _snapshot(root)
    with gen2():
        rc, output = drive(root, ((_opf_write_guard, "inspect_homes_gitignore", held),))
    check("init-cli-gen2-hold-refused",
          rc == EXIT_ERROR and _snapshot(root) == before
          and not (root / ".git" / "opf-oplock").exists()
          and inert("the .working/.gitignore installation is held: synthetic-hold") in output
          and "preflight refused; no publication attempted." in output)

    # Plain init is create-only: an inspection with no hold that found the file present, or that
    # planned bytes other than init's own payload, refuses in the preflight the same way (red when
    # the create-only condition, or either arm of it, is removed: the run then succeeds). The
    # earlier destination refusal stops a real present file first, so each arm is driven through
    # the inspection's answer.
    for tag, prestate, extra in (("present", b"# adopter\n", b""), ("unplanned", None, b"# drift\n")):
        def answer(_root, _verb, prestate=prestate, extra=extra):
            return (_opf_store.render_homes_gitignore().encode("utf-8") + extra, [], prestate)

        root = make_git("gen2-cli-create-only-" + tag)
        before = _snapshot(root)
        with gen2():
            rc, output = drive(root, ((_opf_write_guard, "inspect_homes_gitignore", answer),))
        check("init-cli-gen2-create-only-" + tag + "-refused",
              rc == EXIT_ERROR and _snapshot(root) == before
              and not (root / ".git" / "opf-oplock").exists()
              and inert("the .working/.gitignore inspection did not plan the bootstrap block for an "
                        "absent file; init is create-only") in output
              and "preflight refused; no publication attempted." in output)

    # The re-inspection under the held mutex: a hold that appears only there refuses with
    # nothing published (red when the re-inspection is removed: the run then succeeds).
    real_inspect = _opf_write_guard.inspect_homes_gitignore
    calls = []

    def held_second(store_root, verb):
        calls.append(verb)
        if len(calls) == 1:
            return real_inspect(store_root, verb)
        return held(store_root, verb)

    root = make_git("gen2-cli-held-under-mutex")
    before = _snapshot(root)
    with gen2():
        rc, output = drive(root, ((_opf_write_guard, "inspect_homes_gitignore", held_second),))
    check("init-cli-gen2-hold-refused-under-mutex",
          rc == EXIT_ERROR and len(calls) == 2 and _snapshot(root) == before
          and (root / ".git" / "opf-oplock").exists()
          and inert("the .working/.gitignore installation is held: synthetic-hold") in output
          and "preflight refused; no publication attempted." in output)

    # The post-write effectiveness re-check runs while the mutex is held: a finding refuses with
    # the partial-publication disclosure (red when the verification is removed: exit 0).
    def ineffective(_root, _verb):
        return ["synthetic-finding"]

    root = make_git("gen2-cli-ineffective")
    with gen2():
        rc, output = drive(root, ((_opf_write_guard, "verify_homes_gitignore_effective",
                                   ineffective),))
    check("init-cli-gen2-verify-refused",
          rc == EXIT_ERROR
          and inert("the installed .working/.gitignore is not effective: synthetic-finding") in output
          and "publication may be partial" in output
          and (root / gitignore).is_file())

    # The shipped constants: the created paths, in order, and their bytes are exactly the ones the
    # per-document builders produce one by one, with no .gitignore and no imported leaf (red on any
    # default change: an added, dropped, reordered or re-encoded payload).
    expected = [(_opf_store.MANIFEST_NAME, _opf_init.build_manifest()),
                (_opf_check.COUNTERS_NAME, _opf_init.build_counters()),
                (_opf_check.VERSION_NAME, _opf_init.build_version()),
                (_opf_check.WORKLOG_NAME, _opf_init.build_worklog())]
    expected += [(name + _opf_check.INDEX_SUFFIX, _opf_init.build_index(name))
                 for name in _opf_init.INDEX_TYPES]
    expected = [(machine + "/" + name, text.encode("utf-8")) for name, text in expected]
    expected += [(_opf_store.POINTER_REL, b'[store]\ntarget = "dir:."\n'),
                 ("CHANGELOG.md", b"# Changelog\n")]
    root = make_git("gen2-cli-default")
    rc, output = drive(root)
    paths = created_paths(output)
    check("init-cli-default-unchanged",
          rc == EXIT_OK and paths == [path for path, _data in expected]
          and all((root / path).read_bytes() == data for path, data in expected)
          and not (root / gitignore).exists())


def _suite(invoke):
    """Isolate fixture configuration and restore the caller even on failure. The fixture
    environment keeps the caller's PATH unchanged; a caller that drives init must already sit
    inside the OPF git lifecycle (see _self_test), because the init operation mutex's rev-parse
    strips every GIT_* variable and only that lifecycle's PATH git wrapper puts the system-config
    pins back."""
    import tempfile
    from unittest.mock import patch
    with tempfile.TemporaryDirectory(prefix="opf-selftest-home-") as home:
        with patch.dict(os.environ, HOME=home, XDG_CONFIG_HOME=home,
                        GIT_CONFIG_NOSYSTEM="1"):
            return _suite_isolated(invoke)


def _suite_isolated(invoke):
    """Run parser, publication, refusal, and preservation vectors against fresh fixtures."""
    try:
        import tomllib
        from pathlib import PureWindowsPath
        import _opf_check
        import _opf_init_observe
        import _opf_observe
        import _opf_oplock
        import _opf_release
        import _opf_schema
        import _opf_store
        import _opf_views

        failures = []
        checked = []

        def check(label, condition):
            checked.append(label)
            if not condition:
                failures.append(label)

        def fd_capture(runner):
            # QA round 8 (codex MAJOR): capture at the DESCRIPTOR level.
            # contextlib.redirect_stdout rebinds only the sys module attribute, so
            # output written through the raw descriptors (os.write or os.writev on 1
            # or 2, a subprocess inheriting them, an open of /dev/stdout) escaped the
            # earlier in-process capture entirely and the captured buffer looked
            # clean. Descriptors 1 and 2 are dup2-ed onto one unlinked temporary
            # file around the call and restored on every path; the file's bytes are
            # the captured output, whatever route wrote them.
            sys.stdout.flush()
            sys.stderr.flush()
            with tempfile.TemporaryFile(prefix="opf-init-fd-capture-") as capture:
                saved_out = os.dup(1)
                try:
                    saved_err = os.dup(2)
                    try:
                        os.dup2(capture.fileno(), 1)
                        os.dup2(capture.fileno(), 2)
                        try:
                            rc = runner()
                        finally:
                            sys.stdout.flush()
                            sys.stderr.flush()
                            os.dup2(saved_out, 1)
                            os.dup2(saved_err, 2)
                    finally:
                        os.close(saved_err)
                finally:
                    os.close(saved_out)
                capture.seek(0)
                return rc, capture.read().decode("utf-8", errors="replace")

        def call(argv):
            return fd_capture(lambda: invoke(list(argv)))

        def run(root):
            return call(["init", "--root", str(root)])

        def inert(text):
            # An independent spelling of init's ONE renderer (QA rounds 6, 7 and 8):
            # the safe alphabet is FIXED and narrow, ASCII letters, digits and
            # . / - _ only, and every other character, the SPACE included, becomes a
            # Python \\x / \\u / \\U escape of its code point (written doubled so a
            # raw_unicode_escape re-decode of this file stays well formed). QA
            # round 8 BLOCKER 1: the widened round-8 set let "!" through raw, where
            # interactive bash history expansion replays the user's history, and let
            # zsh glob qualifiers and fish command substitution reach output raw;
            # readability is not a goal that may widen this set.
            def escape(ch):
                code = ord(ch)
                if code < 0x100:
                    return "\\x%02x" % code
                if code < 0x10000:
                    return "\\u%04x" % code
                return "\\U%08x" % code
            safe = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
                       "0123456789./-_")
            return "".join(ch if ch in safe else escape(ch) for ch in text)


        def good(result):
            return result.status == _opf_store.VALID and not result.findings

        working = _opf_store.WORKING_DIRNAME
        machine_name = _opf_store.DEFAULT_MACHINE_SUBDIR
        # Independent roster authority: the baseline type registry, excluding the worklog ledger.
        index_types = sorted(set(_opf_store.BASELINE_TYPES) - {"worklog"})
        source_names = {
            _opf_store.MANIFEST_NAME, _opf_check.COUNTERS_NAME,
            _opf_check.VERSION_NAME, _opf_check.WORKLOG_NAME,
        } | {name + _opf_check.INDEX_SUFFIX for name in index_types}

        def valid_sources(root):
            machine = root / working / machine_name
            try:
                if set(path.name for path in (root / working).iterdir()) != {machine_name}:
                    return False
                if set(path.name for path in machine.iterdir()) != source_names:
                    return False
                if any(not stat.S_ISREG((machine / name).lstat().st_mode) for name in source_names):
                    return False
                docs = {
                    name: tomllib.loads((machine / name).read_text(encoding="utf-8"))
                    for name in source_names
                }
                if not good(_opf_store.validate_manifest(docs[_opf_store.MANIFEST_NAME])):
                    return False
                # Spec 8.1: a fresh store carries no legacy_fragment (LF) counter, so the
                # baseline roster is exact and any importer namespace is a finding.
                namespaces = frozenset(_opf_store.BASELINE_TYPES.values())
                high, findings = _opf_schema.validate_counters(
                    docs[_opf_check.COUNTERS_NAME], known_namespaces=namespaces)
                if findings or high != {namespace: 0 for namespace in namespaces}:
                    return False
                if not good(_opf_release.validate_version(docs[_opf_check.VERSION_NAME])):
                    return False
                if not good(_opf_release.validate_worklog(docs[_opf_check.WORKLOG_NAME])):
                    return False
                root_fd = _opf_store._open_dir_nofollow(root)
                try:
                    for name in index_types:
                        filename = name + _opf_check.INDEX_SUFFIX
                        if docs[filename] != {"schema": _opf_schema.SUPPORTED_SCHEMA, "record": []}:
                            return False
                        _raw, records = _opf_views._load_records(
                            root_fd, working + "/" + machine_name + "/" + filename,
                            name, set(), set())
                        if records:
                            return False
                finally:
                    os.close(root_fd)
                pointer = tomllib.loads(
                    (root / _opf_store.POINTER_REL).read_text(encoding="utf-8"))
                return pointer == {"store": {"target": "dir:."}}
            except (FileNotFoundError, ValueError, UnicodeError, _opf_views.ViewsError):
                return False

        git = _opf_observe._git_path()
        if git is None:
            raise OSError("git not found on PATH")

        def git_call(root, args):
            # Fixture SETUP reproduces the adopter's REAL repository, so it permits lazy fetch
            # (allow_lazy_fetch=True): a partial-clone checkout, or a real `git add` that fetches an absent
            # blob through the promisor, is legitimate fixture behaviour. Production OBSERVATIONS keep
            # _run_git's default (lazy fetch suppressed), which the object-reading probes under test exercise
            # directly (e.g. _opf_observe._show_toml / _run_git below, without this flag).
            # F-367: config_overrides (git's environment-config mechanism, command-scope
            # precedence) pins automatic maintenance OFF, so no DETACHED auto-gc can outlive a
            # fixture commit and churn .git while a ground-truth copytree or a later read
            # traverses it.
            result = _opf_observe._run_git(
                git, root, args, allow_lazy_fetch=True,
                config_overrides=[("gc.auto", "0"), ("gc.autoDetach", "false"),
                                  ("maintenance.auto", "false")])
            if not result.completed or result.rc != 0:
                raise OSError("fixture git failed at {!r}: {}".format(str(root), result.err))
            return result.out

        def git_input(root, args, data):
            # A stdin-fed fixture git call (git update-index --index-info reads the index entry from
            # stdin). Runs under _opf_observe._scrubbed_env (PATH and the
            # isolated HOME carried over, global/system config neutralized, and EVERY ambient GIT_* variable
            # dropped), so an inherited GIT_INDEX_FILE / GIT_DIR / GIT_WORK_TREE / GIT_OBJECT_DIRECTORY /
            # GIT_COMMON_DIR cannot redirect this write to a caller's external index or repository; it stays
            # hermetic like the other fixture calls, which all route through the same scrub (test-hermeticity).
            proc = subprocess.run([git, "-C", str(root)] + list(args), input=data,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  env=_opf_observe._scrubbed_env())
            if proc.returncode != 0:
                raise OSError("fixture git (stdin) failed at {!r}: {}".format(
                    str(root), proc.stderr.decode("utf-8", "replace")))
            return proc.stdout

        with tempfile.TemporaryDirectory(prefix="opf-init-gate-") as temporary:
            base = Path(temporary).resolve()
            outside = _opf_observe._run_git(git, base, ["rev-parse", "--show-toplevel"])
            if not outside.completed or not _opf_observe._is_no_repo(outside):
                raise OSError("temporary fixture parent is not a confirmed non-git location")

            def make_git(name, bare=False):
                root = base / name
                root.mkdir()
                args = ["-c", "init.templateDir=", "-c", "init.defaultBranch=main", "init"]
                if bare:
                    args.append("--bare")
                git_call(root, args)
                return root

            # Isolate git's default ignore discovery ($XDG_CONFIG_HOME/git/ignore, or ~/.config/git/ignore
            # when XDG is unset) from the caller's real HOME: a host personal git-ignore matching .working/,
            # .opf.toml, or CHANGELOG.md would otherwise spuriously fail the positive vectors. HOME reaches
            # every fixture git call and the in-process init via _opf_observe._scrubbed_env, and the
            # subprocess init via inherited os.environ; the saved values are restored in the finally below.
            home = base / "home"
            home.mkdir()
            saved_env = dict(os.environ)
            # Even a broken parser that ignores --root defaults into this isolated, non-git directory.
            # Restore the caller's cwd before TemporaryDirectory removes the fixture.
            saved_cwd = os.open(".", os.O_RDONLY | os.O_DIRECTORY)
            try:
                # Use the OPF-local allowlist before mutating os.environ, retaining PATH.
                # The subprocess init must not inherit an unenumerated GIT_* selector.
                fixture_env = dict(_opf_observe._scrubbed_env(), HOME=str(home))
                os.environ.clear()
                os.environ.update(fixture_env)
                os.chdir(base)
                parser_root = base / "parser"
                parser_root.mkdir()
                parser_before = _snapshot(parser_root)
                parser_vectors = (
                    (["--bogus"], "unrecognized argument"),
                    (["--root"], "--root requires a directory argument"),
                    (["--root", ""], "non-empty directory argument"),
                    (["--root", "--bogus"], "non-empty directory argument"),
                    (["--root", str(parser_root), "--root", str(parser_root)],
                     "--root given more than once"),
                    (["--root", str(parser_root), "--recover", "--recover"],
                     "--recover given more than once"),
                )
                for rest, reason in parser_vectors:
                    rc, output = call(["init"] + rest)
                    check("parser " + repr(rest), rc == EXIT_ERROR and reason in output)
                check("parser fixture preserved", _snapshot(parser_root) == parser_before)

                clean = make_git("clean")
                git_before = _snapshot(clean / ".git")
                rc, output = run(clean)
                check("empty git root succeeds", rc == EXIT_OK)
                check("created sources parse and validate", valid_sources(clean))
                check("completion is source-only",
                      "SOURCES created" in output and "NOT yet git-tracked" in output
                      and "git " in output and "Commit" in output
                      and "opf render --write" in output)
                # The one git write is the operation-lock home: the shared init mutex's anchor, which
                # is never unlinked, with no active record left behind. Literal names, independent of
                # _opf_oplock's constants. DISCRIMINATOR: the pre-lease init writes nothing under
                # .git, so the anchor is absent.
                git_after = _snapshot(clean / ".git")
                git_changed = {key for key in set(git_before) | set(git_after)
                               if git_before.get(key) != git_after.get(key)}
                check("git writes are only the operation-lock home, anchor kept, no active record",
                      all(key.split("/")[0] == "opf-oplock" for key in git_changed)
                      and git_after.get("opf-oplock/mutex.lock", ("absent",))[0] == "file"
                      and "opf-oplock/active.toml" not in git_after)
                check("no root VERSION", not (clean / "VERSION").exists())
                check("heading-only changelog",
                      (clean / "CHANGELOG.md").is_file()
                      and (clean / "CHANGELOG.md").read_bytes() == b"# Changelog\n")
                created = [
                    json.loads(line) for line in output.splitlines() if line.startswith("{")
                ]
                expected_paths = {
                    working + "/" + machine_name + "/" + name for name in source_names
                } | {_opf_store.POINTER_REL, "CHANGELOG.md"}
                check("completion enumerates created paths", any(
                    row.get("event") == "created"
                    and row.get("root") == str(clean)
                    and set(row.get("paths", [])) == expected_paths
                    for row in created))

                before = _snapshot(clean)
                rc, output = run(clean)
                check("second init names existing state",
                      rc == EXIT_ERROR and inert("existing pointer") in output
                      and _opf_store.POINTER_REL in output)
                check("second init preserves source", _snapshot(clean) == before)

                foreign = make_git("foreign")
                (foreign / working / ".empty" / "nested").mkdir(parents=True)
                (foreign / working / ".hidden").write_bytes(b"foreign\n")
                victim = base / "victim"
                victim.write_bytes(b"untouched\n")
                (foreign / working / "link").symlink_to(victim)
                before = _snapshot(foreign)
                rc, output = run(foreign)
                reports = [
                    json.loads(line) for line in output.splitlines() if line.startswith("{")
                ]
                expected_inventory = {
                    (working + "/.empty", "directory"),
                    (working + "/.empty/nested", "directory"),
                    (working + "/.hidden", "file"),
                    (working + "/link", "symlink"),
                }
                check("foreign content is refused with reason",
                      rc == EXIT_ERROR and inert("foreign .working content") in output)
                check("foreign inventory is surfaced", any(
                    row.get("event") == "foreign-content"
                    and row.get("root") == str(foreign)
                    and row.get("complete") is True
                    and {(entry["path"], entry["kind"]) for entry in row.get("entries", [])}
                    == expected_inventory for row in reports))
                check("foreign tree preserved", _snapshot(foreign) == before)
                check("foreign link target preserved", victim.read_bytes() == b"untouched\n")

                nongit = base / "non-git"
                nongit.mkdir()
                before = _snapshot(nongit)
                rc, output = run(nongit)
                check("non-git refused with reason",
                      rc == EXIT_ERROR and inert("git preflight") in output)
                check("non-git untouched", _snapshot(nongit) == before)

                bare = make_git("bare", bare=True)
                before = _snapshot(bare)
                rc, output = run(bare)
                check("bare git refused with reason",
                      rc == EXIT_ERROR and inert("git preflight") in output)
                check("bare git untouched", _snapshot(bare) == before)

                for number, pointer in enumerate(
                        (_opf_store.POINTER_REL, _opf_store.LOCAL_POINTER_REL)):
                    target = make_git("pointer-" + str(number))
                    (target / pointer).write_bytes(b"malformed {{{\n")
                    before = _snapshot(target)
                    rc, output = run(target)
                    check(pointer + " refused",
                          rc == EXIT_ERROR and inert("existing pointer") in output
                          and pointer in output)
                    check(pointer + " preserved", _snapshot(target) == before)

                empty_working = make_git("empty-working")
                (empty_working / working).mkdir()
                before = _snapshot(empty_working)
                rc, output = run(empty_working)
                check("empty partial store refused",
                      rc == EXIT_ERROR and inert("store resolution refused") in output)
                check("empty partial store preserved", _snapshot(empty_working) == before)

                # Spec 14.2: plain init never keeps an undispositioned root deliverable, so a
                # pre-existing root CHANGELOG.md or VERSION is refused at preflight, nothing is
                # written, and the refusal names opf adopt. The plain changelog has no level-2
                # heading, so doctor would accept it beside the fresh ledger; it is refused all the
                # same. The entry-heading changelog is the case doctor reports INVALID (its ##
                # heading matches no release in the fresh, empty version ledger). Each name is read
                # through lstat, so a symlink there (to an outside file, or dangling) is refused,
                # never followed. DISCRIMINATOR: rc 0 against the pre-fix init, which kept the
                # changelog and never read VERSION; a followed VERSION link passes the dangling
                # vector (rc 0), and a followed CHANGELOG.md link leaves only the generic
                # destination refusal, which lacks this text.
                outside = base / "deliverable-outside"
                outside.write_bytes(b"untouched\n")
                dangling = base / "deliverable-absent"
                for number, (label, name, content) in enumerate((
                        ("existing changelog", "CHANGELOG.md", b"Existing changelog.\n"),
                        ("existing changelog with an entry heading", "CHANGELOG.md",
                         b"# History\n\n## 2.4.0\n"),
                        ("existing changelog symlink", "CHANGELOG.md", outside),
                        ("dangling changelog symlink", "CHANGELOG.md", dangling),
                        ("existing VERSION", "VERSION", b"2.4.0\n"),
                        ("dangling VERSION symlink", "VERSION", dangling))):
                    target = make_git("deliverable-" + str(number))
                    if isinstance(content, bytes):
                        (target / name).write_bytes(content)
                    else:
                        (target / name).symlink_to(content)
                    before = _snapshot(target)
                    rc, output = run(target)
                    check(label + " refused",
                          rc == EXIT_ERROR and "during preflight: RuntimeError(" in output
                          and inert("an existing " + name + " sits at") in output
                          and inert("opf adopt") in output
                          and "preflight refused; no publication attempted." in output)
                    check(label + " preserved", _snapshot(target) == before)
                check("deliverable symlink targets untouched (none followed or created)",
                      outside.read_bytes() == b"untouched\n" and not dangling.exists()
                      and not dangling.is_symlink())

                # Spec 5.7 and 9: plain init publishes under the shared init operation mutex, the
                # anchor the coupled init and adoption take for the same root. Held here by the
                # harness, it refuses init at the acquisition, after the read-only preflight, and
                # nothing is written. DISCRIMINATOR: the pre-lease init takes no mutex and
                # publishes (rc 0).
                def anchor_free(root):
                    try:
                        probe = _opf_oplock.acquire_init_operation(str(root), "fixture-probe")
                    except _opf_oplock.OpLockError:
                        return False
                    _opf_oplock.release_init_holder(probe)
                    return True

                # _snapshot never descends into .git, so the control area the lock writes
                # (.git/opf-oplock: the anchor and any records beside it) is compared
                # separately, entry names and bytes both; without this the refused
                # acquisition could write under .git unnoticed.
                def control_area(root):
                    area = root / ".git" / "opf-oplock"
                    if not area.is_dir():
                        return None
                    entries = {}
                    for path in sorted(area.rglob("*")):
                        rel = path.relative_to(area).as_posix()
                        entries[rel] = path.read_bytes() if path.is_file() else "non-file"
                    return entries

                held_root = make_git("lease-held")
                held = _opf_oplock.acquire_init_operation(str(held_root), "fixture-holder")
                try:
                    before = _snapshot(held_root)
                    held_area = control_area(held_root)
                    rc, output = run(held_root)
                finally:
                    _opf_oplock.release_init_holder(held)
                check("lease: a held init mutex refuses plain init",
                      rc == EXIT_ERROR
                      and "during taking-the-init-operation-mutex: OpLockError(" in output
                      and inert("held by another process") in output
                      and "preflight refused; no publication attempted." in output)
                check("lease: a held init mutex refusal wrote nothing",
                      _snapshot(held_root) == before
                      and held_area is not None
                      and control_area(held_root) == held_area)

                # Stale-record recovery: a dead run's leftover active record under the free
                # anchor refuses a later init until --recover, which clears it only once its
                # holder is confirmed dead; a live holder's record is never seized. The planted
                # record names this root's ABSENT machine store. Plain init writes the record
                # only after the store directories exist, so a run that stopped before its
                # directories existed leaves NO record; this state is what remains once a
                # crashed run's partly published .working/ was later moved aside, the recovery
                # case the refusal exists for. DISCRIMINATOR: the pre-lease init never reads the
                # record (rc 0 on the first vector), and an init that ignores --recover keeps
                # refusing.
                def plant_active(root, pid):
                    # The fixture acquisition creates the control directory and its anchor with
                    # the modes the lock requires; the record is then written beside them.
                    _opf_oplock.release_init_holder(
                        _opf_oplock.acquire_init_operation(str(root), "fixture-holder"))
                    record = root / ".git" / "opf-oplock" / "active.toml"
                    op_id = "00000000-0000-4000-8000-000000000000"
                    node = os.uname().nodename
                    record.write_text("\n".join((
                        "schema = 2",
                        "op_id = " + json.dumps(op_id),
                        "holder = " + json.dumps("opf:{}:{}".format(node, pid)),
                        'operation = "opf-init-plain"',
                        'acquired_at = "2026-01-01T00:00:00Z"',
                        "",
                        "[owner]",
                        "pid = {}".format(pid),
                        "uid = {}".format(os.getuid()),
                        "nodename = " + json.dumps(node),
                        "session = " + json.dumps(op_id),
                        'utc = "2026-01-01T00:00:00Z"',
                        'pid-start = ""',
                        "",
                        "[machine_store]",
                        "path = " + json.dumps(str(root / working / machine_name)),
                        'dev = "1"',
                        'ino = "1"',
                        "")), encoding="utf-8")
                    return record

                stale_root = make_git("lease-stale-record")
                dead = subprocess.Popen([sys.executable, "-I", "-c", ""])
                dead.wait()
                stale_record = plant_active(stale_root, dead.pid)
                stale_bytes = stale_record.read_bytes()
                before = _snapshot(stale_root)
                rc, output = run(stale_root)
                # The rendered refusal is pinned WHOLE: it names the CLI flag --recover before
                # the API keyword, so an operator at the command line is never pointed at
                # recover=True alone. The record is compared by its exact bytes, not merely
                # its presence.
                check("lease: a dead run's leftover record refuses plain init without --recover",
                      rc == EXIT_ERROR
                      and inert("stale operation record(s) under a free anchor (active.toml); "
                                "refusing without EXPLICIT recovery (--recover on the opf "
                                "command line; recover=True from the API), which itself "
                                "proceeds only when the recorded holder is confirmed dead")
                      in output
                      and "preflight refused; no publication attempted." in output
                      and _snapshot(stale_root) == before and stale_record.is_file()
                      and stale_record.read_bytes() == stale_bytes)
                rc, output = call(["init", "--root", str(stale_root), "--recover"])
                check("lease: --recover clears a dead holder's record, then init publishes",
                      rc == EXIT_OK and valid_sources(stale_root) and not stale_record.exists())
                live_root = make_git("lease-live-record")
                live_record = plant_active(live_root, os.getpid())
                live_bytes = live_record.read_bytes()
                before = _snapshot(live_root)
                rc, output = call(["init", "--root", str(live_root), "--recover"])
                check("lease: --recover never seizes a live holder's record",
                      rc == EXIT_ERROR and inert("not confirmed dead") in output
                      and _snapshot(live_root) == before
                      and live_record.read_bytes() == live_bytes)

                # Symlinked root, containment-isolated: the symlink target is a REAL directory INSIDE
                # the same git repo, and --root names the sibling symlink. _init_repo's repo-containment
                # check therefore passes (git reports the enclosing repo, which lexically contains the
                # symlink path), leaving _open_dir_nofollow's O_NOFOLLOW on the final component as the
                # ONLY guard that can refuse. A fresh separate repo as target would instead be caught by
                # the containment check, so the no-follow protection would stay unexercised.
                symlink_repo = make_git("symlink-repo")
                symlink_target = symlink_repo / "realdir"
                symlink_target.mkdir()
                (symlink_target / "keep").write_bytes(b"target\n")
                linked = symlink_repo / "linkdir"
                linked.symlink_to(symlink_target, target_is_directory=True)
                before = _snapshot(symlink_target)
                rc, output = run(linked)
                check("symlink root refused",
                      rc == EXIT_ERROR and "preflight refused" in output)
                check("symlink root target preserved",
                      _snapshot(symlink_target) == before)

                # A destination tracked in the index but absent from the working tree (the deleted-copy
                # case _init_untracked exists for): commit it, then remove the working copy. With the
                # file left present the earlier existence/resolution checks would fire first, so removing
                # the working copy routes the refusal through _init_untracked's index read; init must
                # refuse rather than publish over a tracked-but-deleted path.
                tracked = make_git("tracked-dest")
                tracked_machine = tracked / working / machine_name
                tracked_machine.mkdir(parents=True)
                (tracked_machine / _opf_store.MANIFEST_NAME).write_bytes(b"x = 1\n")
                git_call(tracked, ["--literal-pathspecs", "add", "--",
                                   working + "/" + machine_name + "/" + _opf_store.MANIFEST_NAME])
                git_call(tracked, ["-c", "user.email=t@t", "-c", "user.name=t",
                                   "commit", "-m", "seed"])
                shutil.rmtree(tracked / working)
                before = _snapshot(tracked)
                rc, output = run(tracked)
                check("tracked destination refused",
                      rc == EXIT_ERROR
                      and inert("planned destination already git-tracked") in output)
                check("tracked destination preserved", _snapshot(tracked) == before)

                # A planned destination under a .gitignore rule: init must refuse, because an ignored
                # store cannot be staged (git add silently no-ops on an ignored path) or discovered.
                ignored = make_git("ignored-target")
                (ignored / ".gitignore").write_bytes(working.encode("ascii") + b"/\n")
                git_call(ignored, ["--literal-pathspecs", "add", "--", ".gitignore"])
                git_call(ignored, ["-c", "user.email=t@t", "-c", "user.name=t",
                                   "commit", "-m", "seed"])
                before = _snapshot(ignored)
                rc, output = run(ignored)
                check("ignored destination refused",
                      rc == EXIT_ERROR and "git-ignored" in output)
                check("ignored destination preserved", _snapshot(ignored) == before)

                # Local pointer gitignored -> SUCCESS: the machine-local pointer is a path init NEVER
                # creates and adopters legitimately gitignore, so an ignore rule naming only it must not
                # block init. This proves the ignore check is scoped to the CREATED destinations; it
                # FAILS if the check is passed index_paths (which carry the local pointer).
                local_ignored = make_git("local-pointer-ignored")
                (local_ignored / ".gitignore").write_bytes(
                    _opf_store.LOCAL_POINTER_REL.encode("ascii") + b"\n")
                git_call(local_ignored, ["--literal-pathspecs", "add", "--", ".gitignore"])
                git_call(local_ignored, ["-c", "user.email=t@t", "-c", "user.name=t",
                                         "commit", "-m", "seed"])
                rc, output = run(local_ignored)
                check("local-pointer-ignored init succeeds",
                      rc == EXIT_OK and valid_sources(local_ignored))

                # Magic-named ignored root -> REFUSE: a --root whose repo-relative prefix begins with a
                # pathspec-magic sigil (a leading colon). A repo-local rule ignores that subdir's store
                # tree, so init must refuse. Without the literal "./" prefix in _init_unignored the colon
                # is consumed as an empty magic signature, the check bypasses (rc 1), and init would
                # wrongly succeed; this vector discriminates the ./ neutralization.
                magic = make_git("magic-root")
                magic_sub = magic / ":magic"
                magic_sub.mkdir()
                (magic_sub / "keep").write_bytes(b"target\n")
                (magic / ".gitignore").write_bytes(b"/:magic/" + working.encode("ascii") + b"\n")
                git_call(magic, ["--literal-pathspecs", "add", "--", ".gitignore"])
                git_call(magic, ["-c", "user.email=t@t", "-c", "user.name=t",
                                 "commit", "-m", "seed"])
                before = _snapshot(magic_sub)
                rc, output = run(magic_sub)
                check("magic-named ignored root refused",
                      rc == EXIT_ERROR and "git-ignored" in output)
                check("magic-named ignored root preserved", _snapshot(magic_sub) == before)

                # OPF-D2B: a destination ignored ONLY by the adopter's GLOBAL core.excludesFile (no repo-local
                # rule) must be refused. The pre-D2B check neutralized the global config, so init wrongly
                # succeeded; the config-discovery probe now reads the adopter's ~/.gitconfig through HOME.
                # DISCRIMINATOR: this fails (init succeeds, no "git-ignored") against the pre-D2B check. The
                # global config is written into the isolated HOME only for THIS vector and removed after, so
                # it cannot ignore .working/ for the positive vectors that follow.
                global_ignored = make_git("global-excludes-target")
                gexcludes = base / "d2b-global-excludes"
                gexcludes.write_bytes(working.encode("ascii") + b"/\n")
                gitconfig = home / ".gitconfig"
                gitconfig.write_bytes(
                    b"[core]\n\texcludesFile = " + str(gexcludes).encode("ascii") + b"\n")
                gi_before = _snapshot(global_ignored)
                try:
                    rc, output = run(global_ignored)
                finally:
                    gitconfig.unlink()
                check("global-excludesFile ignored destination refused",
                      rc == EXIT_ERROR and "git-ignored" in output)
                check("global-excludesFile ignored destination preserved",
                      _snapshot(global_ignored) == gi_before)

                # OPF-D2B: a config that would make the read-only probe WRITE a trace file (trace2.eventTarget
                # in the adopter's ~/.gitconfig) must produce NO write: the probe forces GIT_TRACE2* off in
                # its environment (a command-line -c cannot, since trace2 reads its config before -c). Init
                # proceeds normally and the trace target is never created. DISCRIMINATOR for the trace-off fix.
                trace_target = make_git("trace2-target")
                tracefile = base / "d2b-trace2-out.json"
                tconfig = home / ".gitconfig"
                tconfig.write_bytes(
                    b"[trace2]\n\teventTarget = " + str(tracefile).encode("ascii") + b"\n")
                try:
                    rc, output = run(trace_target)
                finally:
                    tconfig.unlink()
                check("trace2 config does not make the probe write a file", not tracefile.exists())
                check("trace2 config does not break a clean init",
                      rc == EXIT_OK and valid_sources(trace_target))

                # Defence in depth: after a successful init, the staged-and-committed store resolves
                # end to end, at the standard machine subdir. resolve_store reads the working tree, so
                # the commit is not required for resolution; it is included to exercise the realistic
                # adopter flow (init -> stage -> commit -> resolvable).
                resolvable = make_git("resolve-store")
                rc, output = run(resolvable)
                check("resolve-store init succeeds", rc == EXIT_OK)
                git_call(resolvable, ["--literal-pathspecs", "add", "-A"])
                git_call(resolvable, ["-c", "user.email=t@t", "-c", "user.name=t",
                                      "commit", "-m", "init"])
                resolution = _opf_store.resolve_store(resolvable)
                check("resolve-store resolves committed store",
                      resolution.status == _opf_store.RESOLVED
                      and resolution.machine_rel == working + "/" + machine_name)

                # QA round 3 (MEDIUM, MINOR 2): every ancestry refusal vector compares the
                # COMPLETE output with text built here, from the commit ids and paths this test
                # computes itself, never by substring. The builders spell out each diagnostic
                # in full, so any change to the refusal wording, its escaping (the one inert
                # renderer, never ascii or repr), its remedy command (the render-unchanged
                # gate, --literal-pathspecs, the restored paths) or its stated limits fails
                # every vector that reaches it.
                history_stage = "checking-git-history-for-a-prior-store"

                def restore_command(repo, commit, paths):
                    # Printed by init only when every interpolated value renders UNCHANGED
                    # through the inert escape, so the command carries the raw words bare,
                    # with no quoting at all (shlex.quote is retired: it kept a
                    # newline-holding value syntactically quoted while its middle physical
                    # lines ran standalone when pasted line by line, QA round 6 MAJOR).
                    return ("  git -C " + str(repo)
                            + " --no-replace-objects --literal-pathspecs checkout "
                            + commit + " -- " + " ".join(paths))

                def prior_store_output(root, repo, commit, restored, shell_ready,
                                       extra=None, gaps=None):
                    rel = os.path.relpath(str(root), str(repo)).replace(os.sep, "/")
                    rel = "" if rel == "." else rel + "/"
                    paths = [rel + name for name in restored]
                    extra_commit = extra[0] if extra else None
                    extra_paths = [rel + name for name in extra[1]] if extra else []
                    gap_paths = [rel + name for name in gaps] if gaps else []
                    if shell_ready:
                        if extra:
                            remedy = ("restore the store paths the restore commands below "
                                      "name, from the commits they name (this history "
                                      "deleted the store across more than one commit, so "
                                      "one command cannot restore every store path)")
                            label = ("shell-ready; it restores only the store paths its "
                                     "commit holds, and the additional command below "
                                     "restores the other named store path as git checks "
                                     "it out from its own commit")
                        else:
                            remedy = ("restore the store paths the restore command below "
                                      "names, from that commit")
                            label = ("shell-ready; it restores the named store paths as "
                                     "git checks them out from that commit")
                        restore = ["opf init: restore command (" + label + "):",
                                   restore_command(repo, commit, paths)]
                        if extra:
                            restore += [
                                "opf init: additional restore command (shell-ready; it "
                                "restores the remaining store path from the newest "
                                "first-parent tree that holds it):",
                                restore_command(repo, extra_commit, extra_paths)]
                    else:
                        remedy = ("restore the store paths the lines below name, from the "
                                  + ("commits" if extra else "commit")
                                  + " named below (no command is printed: a value in it "
                                  "would hold a character outside the bare-command "
                                  "alphabet)")
                        restore = (
                            ["opf init: restore command withheld: a value in it holds a "
                             "character outside the bare-command alphabet "
                             "A-Za-z0-9 . / - _ and an escaped "
                             "command could still carry a live shell "
                             "substitution or split across "
                             "physical lines, so no command is printed. Restore each "
                             "path named "
                             "below from the commit named above it, in the repository "
                             "named below, "
                             "by your own means, reading each rendered value literally: "
                             "the safe "
                             "alphabet is ASCII letters, digits and . / - _ only, and "
                             "every other "
                             "character, the space included, is written as a backslash "
                             "escape of "
                             "its code point:",
                             "opf init:   repository: " + inert(str(repo)),
                             "opf init:   commit: " + commit]
                            + ["opf init:   restore path: " + inert(path)
                               for path in paths])
                        if extra:
                            restore += (
                                ["opf init:   additional commit: " + extra_commit]
                                + ["opf init:   additional restore path: " + inert(path)
                                   for path in extra_paths])
                    if gaps:
                        remedy += (", and the restore is PARTIAL (the missing-store-path "
                                   "lines below name structural store files the restored "
                                   ".working tree lacks)")
                        restore += (
                            ["opf init: the restore is PARTIAL: the .working tree the "
                             "command or "
                             "lines above restore lacks each store path below, so no "
                             "command or "
                             "path printed above provides it (this scan treats a "
                             "first-level "
                             ".working subdirectory as a machine store only when it "
                             "directly holds "
                             "manifest.toml or counters.toml, in that tree or in this "
                             "line's "
                             "first-parent history at or before the named commit; a "
                             "store found "
                             "either way is expected to hold both files, and a "
                             "subdirectory "
                             "holding only other files or only deeper directories is "
                             "neither "
                             "expected nor named; a structural file counts at its "
                             "rename destination instead of its old path only when "
                             "the recorded destination blob id equals the source "
                             "blob id (an exact byte-identical move; git's "
                             "similarity score of 100 alone does not prove that, "
                             "since reordered lines also score 100), no older "
                             "first-parent record touched the destination, and the "
                             "renaming commit's tree holds no structural file under "
                             "the source subdirectory, so a machine subdirectory "
                             "renamed whole with git mv is complete at its new path, "
                             "while any other pairing git's rename detection "
                             "reports, for example a store deleted beside a similar "
                             "store created in the same commit, or a pairing whose "
                             "bytes were rearranged, reads as a deletion of its "
                             "source, which stays expected, fail-safe); "
                             "supply each missing path by your own means, or re-adopt "
                             "with opf "
                             "adopt:"]
                            + ["opf init:   missing store path: " + inert(path)
                               for path in gap_paths])
                    return (
                        "opf init: REFUSED at " + inert(str(root)) + " during "
                        + history_stage
                        + ": a prior store exists in this repository's git history: commit "
                        + commit + " on HEAD's first-parent line holds "
                        + inert(rel + ".opf.toml") + " or a store manifest "
                        + inert(rel + ".working/<subdir>/manifest.toml")
                        + " (spec 4.3, 4.5), and spec 8.2 forbids restarting its counters at "
                        "zero (record ids would be reissued). Remedy: " + remedy
                        + ", or re-adopt the ancestry "
                        "with opf adopt. Plain opf init refuses whenever this scan of "
                        "HEAD's first-parent line finds a prior store, and " + _SIDELINE_LIMIT
                        + " (adoption across such side lines is opf adopt's authority); "
                        + _PATH_LIMIT + "; exit 2\n"
                        + "\n".join(restore) + "\n"
                        "opf init: preflight refused; no publication attempted.\n")

                def cannot_evaluate_output(root, message, kind="RuntimeError"):
                    # The exception text is rendered by the one inert escaper at the print
                    # (QA rounds 5 and 6: the space-admitting prose renderer is retired),
                    # and the exception TYPE is printed by name around the escaped text.
                    return ("opf init: cannot evaluate at " + inert(str(root))
                            + " during " + history_stage + ": " + kind + "("
                            + inert(message) + "); exit 2\n"
                            "opf init: preflight refused; no publication attempted.\n")

                def grafts_message(found, grafts_path):
                    return ("cannot evaluate prior-store ancestry: found " + found
                            + " at the legacy grafts path " + ascii(grafts_path)
                            + "; git consults that path in every history walk, where grafts it "
                            "can read may rewrite parent links (--no-replace-objects does not "
                            "neutralize them), and this scan never interprets grafts, so a prior "
                            "store, whose counters spec 8.2 forbids restarting, cannot be ruled "
                            "out; convert or remove it (git replace --convert-graft-file turns a "
                            "grafts file into replace refs, which this scan already ignores) and "
                            "retry")

                def head_of(repo):
                    return git_call(repo, ["rev-parse", "HEAD"]).decode("ascii").strip()

                def store_contents(root):
                    # Bytes and link targets only: git checkout writes modes from the umask,
                    # which init's own create modes need not match.
                    return dict((path, entry[-1]) for path, entry in _snapshot(root).items()
                                if entry[0] != "directory")

                if shutil.which("sh") is None:
                    raise OSError("no POSIX sh on PATH to run the printed restore command")
                if shutil.which("bash") is None:
                    raise OSError(
                        "no bash on PATH: the interactive history-expansion paste leg "
                        "(QA round 8 BLOCKER 1) cannot run, and a required leg never "
                        "skips silently")
                # zsh and fish legs run only where installed (their hazards, glob
                # qualifiers that execute code and parenthesis command substitution,
                # are shell-specific and both shells are optional on CI hosts); the
                # sh and bash legs are REQUIRED and their absence refuses above.
                probe_has_zsh = shutil.which("zsh") is not None
                probe_has_fish = shutil.which("fish") is not None
                probe_seed_names = ("probe-seed-aa.txt", "probe-seed-zz.txt")
                probe_hist_prime = "true || touch HIST-REPLAYED"

                def seed_cell(cell):
                    # Probe directories are NOT empty (QA round 8 BLOCKER 1): an
                    # empty directory gives a glob nothing to match, so a glob or
                    # glob-qualifier payload would pass unexercised; violation
                    # detection subtracts exactly these names.
                    for seed_name in probe_seed_names:
                        (cell / seed_name).write_bytes(b"seed\n")

                def probe_line_legs(line, cell):
                    # The per-line legs, each launched with its LITERAL argv head
                    # (the maintenance-pin scan resolves launch heads to literals):
                    # plain sh -c; bash --norc --noprofile -i fed a PRIMED history
                    # line then the probed line (history expansion is live in
                    # interactive bash, and a replayed prior line is the round-8
                    # blocker; the primed line is inert unless replayed INTO a
                    # failing line, where the || arm fires); zsh -f -i (banghist
                    # and glob qualifiers) and fish --no-config -i (parenthesis
                    # command substitution) where installed. Returns the completed
                    # processes so the caller can scan their output for the marker.
                    primed = (probe_hist_prime + "\n" + line + "\n").encode("utf-8")
                    probe_env = dict(os.environ, HISTFILE=os.devnull, TERM="dumb")
                    procs = [subprocess.run(
                        ["sh", "-c", line], input=b"", cwd=str(cell),
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                        timeout=60, env=probe_env)]
                    procs.append(subprocess.run(
                        ["bash", "--norc", "--noprofile", "-i"], input=primed,
                        cwd=str(cell), stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE, timeout=60, env=probe_env))
                    if probe_has_zsh:
                        procs.append(subprocess.run(
                            ["zsh", "-f", "-i"], input=primed, cwd=str(cell),
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            timeout=60, env=probe_env))
                    if probe_has_fish:
                        procs.append(subprocess.run(
                            ["fish", "--no-config", "-i"],
                            input=(line + "\n").encode("utf-8"), cwd=str(cell),
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            timeout=60, env=probe_env))
                    return procs

                def run_restore(output):
                    # Runs every printed restore line through a real POSIX shell, in the
                    # printed order, from a directory outside the repository (each command
                    # carries its own -C); None when no command line was printed at all, 0
                    # only when every printed command succeeded.
                    lines = [line for line in output.splitlines() if line.startswith("  git -C ")]
                    if not lines:
                        return None
                    for line in lines:
                        proc = subprocess.run(["sh", "-c", line.strip()], cwd=str(base),
                                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                              timeout=120)
                        if proc.returncode != 0:
                            return proc.returncode
                    return 0

                def sh_inert_probe(output, tag):
                    # QA round 5 (requirement A): EVERY line init prints must be
                    # shell-inert. Five legs, each from the same fresh SEEDED directory,
                    # returning the sorted union of non-seed entries created there
                    # (expected: none); legs 4 and 5 are the interactive-bash
                    # primed-history legs added below (QA round 8 BLOCKER 1). Leg 1
                    # runs the WHOLE output through the real POSIX sh, which
                    # EXITS at its first syntax error, so that leg alone proves nothing
                    # past the first unparsable line (QA round 6 MINOR: the earlier
                    # docstring claimed the whole output ran). Leg 2 runs each line alone
                    # through sh -c, a fresh parse per line. Leg 3 feeds the whole output
                    # to sh -i, whose interactive error recovery keeps reading after a
                    # syntax error and carries an unbalanced quote across physical lines,
                    # the closest non-tty stand-in for a paste into a terminal. The fixed
                    # prose may make sh print not-found or syntax errors; those run
                    # nothing. What must never happen is a name-derived substitution
                    # running (the PWNED sentinel) or a redirect creating an entry. Every
                    # output probed here was produced for a hostile-named fixture, whose
                    # command hints init withholds, so no leg reaches a runnable git line.
                    # QA round 8 BLOCKER 1 additions: the directory is SEEDED (so a
                    # glob has something to match), and two interactive-bash legs run
                    # with a primed history line, whole-output and line by line,
                    # because bash history expansion acts on a raw "!" even inside
                    # double quotes and dash (the usual sh) never would.
                    probe = base / ("inert-probe-" + tag)
                    probe.mkdir()
                    seed_cell(probe)
                    created = set()

                    def swept():
                        return {entry.name for entry in probe.iterdir()} - set(
                            probe_seed_names)

                    subprocess.run(["sh"], input=output.encode("utf-8"), cwd=str(probe),
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   timeout=120)
                    created |= swept()
                    for line in output.splitlines():
                        subprocess.run(["sh", "-c", line], cwd=str(probe),
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       timeout=120)
                        subprocess.run(["bash", "--norc", "--noprofile", "-i"],
                                       input=(probe_hist_prime + "\n" + line
                                              + "\n").encode("utf-8"),
                                       cwd=str(probe), stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE, timeout=120,
                                       env=dict(os.environ, HISTFILE=os.devnull,
                                                TERM="dumb"))
                    created |= swept()
                    subprocess.run(["sh", "-i"], input=output.encode("utf-8"),
                                   cwd=str(probe), stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, timeout=120)
                    subprocess.run(["bash", "--norc", "--noprofile", "-i"],
                                   input=(probe_hist_prime + "\n"
                                          + output).encode("utf-8"),
                                   cwd=str(probe), stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, timeout=120,
                                   env=dict(os.environ, HISTFILE=os.devnull,
                                            TERM="dumb"))
                    created |= swept()
                    return sorted(created)

                def init_store(root):
                    # A store fixture's FIRST init. Plain init refuses a pre-existing root
                    # CHANGELOG.md ahead of its history scan (spec 14.2), so a fixture that commits
                    # this store and later deletes only the store paths would stop there, short of
                    # the scan it exercises. The starter changelog is renamed to an inert file
                    # before the fixture commits: the root still holds one non-store file once the
                    # store is deleted (so git rm keeps the directory), and none at a deliverable
                    # destination.
                    result = run(root)
                    if (root / "CHANGELOG.md").is_file():
                        (root / "CHANGELOG.md").rename(root / "notes.txt")
                    return result

                def coupled_holder(repo, prefix=""):
                    # The coupled init's detector (_opf_init_observe.prior_store_holder) over
                    # the same fixture: the commit it names, None, or the code it refuses with.
                    # Each "ancestry parity" check pins it to the verdict plain init gives here,
                    # so a drift in either scan fails the pair.
                    try:
                        return _opf_init_observe.prior_store_holder(
                            git, str(repo), pinned_head=head_of(repo), prefix=prefix,
                            object_format="sha1")
                    except _opf_init_observe.ObservationError as exc:
                        return exc.code

                # SPEC 8.2 ancestry (fix-init-ancestry): plain init must never restart counters
                # over a store git HISTORY shows existed. Reproduction: init, commit the store,
                # raise a counter high-water and commit, `git rm -r .working .opf.toml` and
                # commit, then init again. The unfixed init passed (rc 0) and re-zeroed every
                # namespace, so record ids could be reissued; it must refuse (exit 2), NAME the
                # newest first-parent commit whose tree held the store (the counter-bump commit,
                # not the deletion commit), and give the remedy (restore, or `opf adopt`).
                # DISCRIMINATOR: rc 0 against the pre-fix init.
                ancestry = make_git("ancestry-prior-store")
                rc, output = init_store(ancestry)
                check("ancestry fixture first init succeeds", rc == EXIT_OK)
                git_call(ancestry, ["--literal-pathspecs", "add", "-A"])
                git_call(ancestry, ["-c", "user.email=t@t", "-c", "user.name=t",
                                    "commit", "-m", "store"])
                counters_path = ancestry / working / machine_name / _opf_check.COUNTERS_NAME
                seeded = counters_path.read_text(encoding="utf-8").replace("BI = 0", "BI = 7")
                if "BI = 7" not in seeded:
                    raise OSError("ancestry fixture: counters.toml carried no BI = 0 to raise")
                counters_path.write_text(seeded, encoding="utf-8")
                git_call(ancestry, ["--literal-pathspecs", "add", "-A"])
                git_call(ancestry, ["-c", "user.email=t@t", "-c", "user.name=t",
                                    "commit", "-m", "BI high-water 7"])
                held_commit = git_call(ancestry, ["rev-parse", "HEAD"]).decode("ascii").strip()
                git_call(ancestry, ["rm", "-r", "-q", "--",
                                    working, _opf_store.POINTER_REL])
                git_call(ancestry, ["-c", "user.email=t@t", "-c", "user.name=t",
                                    "commit", "-m", "drop store"])
                before = _snapshot(ancestry)
                rc, output = run(ancestry)
                check("ancestry: re-init over a deleted committed store refused",
                      rc == EXIT_ERROR and "prior store" in output)
                check("ancestry: refusal names the newest holding commit", held_commit in output)
                check("ancestry: refusal gives the adopt remedy", "opf adopt" in output)
                # QA round 1 MINOR: a DEFINITE prior-store finding is reported as REFUSED in
                # its own words, never under the generic cannot-evaluate prefix (exit stays 2).
                check("ancestry: definite finding reported as REFUSED, not cannot-evaluate",
                      "opf init: REFUSED" in output and "cannot evaluate" not in output)
                # QA round 2 MAJOR 1: the refusal states the first-parent limit exactly
                # (merged or not). DISCRIMINATOR: mutant M-refusal-unmerged (the round-1
                # "only ever lived on an unmerged side branch" refusal wording) fails this.
                check("ancestry: refusal states the exact first-parent limit",
                      _states_sideline_limit(output))
                check("ancestry: refusal states the current-path limit", _states_path_limit(output))
                # QA round 3 MEDIUM: the COMPLETE refusal, built independently. DISCRIMINATORS:
                # M-remedy-allows ("forbids restarting its counters at zero" reworded),
                # M-remedy-drop-path (a restored path dropped from the command),
                # M-no-literal-pathspecs and an appended raw U+202E all fail (M-repr-for-ascii
                # cannot fail here, where every path is ASCII: the cafe vector below kills it).
                check("ancestry: the complete refusal text, built independently",
                      output == prior_store_output(ancestry, ancestry, held_commit,
                                                   [".opf.toml", ".working"], True))
                check("ancestry: refusing init wrote nothing", _snapshot(ancestry) == before)
                check("ancestry parity: the coupled init's detector names the same commit",
                      coupled_holder(ancestry) == held_commit)
                # The same reproduction with the starter CHANGELOG.md left in place (the file the
                # fixture above renames away): plain init refuses that changelog at preflight,
                # ahead of the history scan (spec 14.2), so the counters stay protected either way
                # and nothing is written. DISCRIMINATOR: against the pre-fix init the history scan
                # refuses instead, without the changelog text.
                kept = make_git("ancestry-kept-changelog")
                rc, output = run(kept)
                check("ancestry kept-changelog fixture first init succeeds", rc == EXIT_OK)
                git_call(kept, ["--literal-pathspecs", "add", "-A"])
                git_call(kept, ["-c", "user.email=t@t", "-c", "user.name=t",
                                "commit", "-m", "store"])
                git_call(kept, ["rm", "-r", "-q", "--", working, _opf_store.POINTER_REL])
                git_call(kept, ["-c", "user.email=t@t", "-c", "user.name=t",
                                "commit", "-m", "drop store"])
                before = _snapshot(kept)
                rc, output = run(kept)
                check("ancestry: a deleted store whose CHANGELOG.md stayed is refused at the "
                      "changelog check, ahead of the history scan",
                      rc == EXIT_ERROR and "during preflight: RuntimeError(" in output
                      and inert("an existing CHANGELOG.md sits at") in output
                      and "prior store" not in output)
                check("ancestry: that changelog refusal wrote nothing", _snapshot(kept) == before)

                # SPEC 8.2 ancestry, fail closed: a SHALLOW clone truncates the first-parent
                # history, so it cannot prove no prior store existed -- the clipped commits of
                # THIS fixture do hold one, and the pathspec walk stops silently at the shallow
                # boundary -- so init is a cannot-evaluate refusal, never a silent pass.
                # DISCRIMINATOR: rc 0 (the truncation hides the store from an ungated scan)
                # against a scan without the shallow gate.
                shallow_prior = base / "ancestry-shallow"
                git_call(base, ["-c", "protocol.file.allow=always", "clone",
                                "--depth", "1",
                                "file://" + str(ancestry), str(shallow_prior)])
                rc, output = run(shallow_prior)
                check("ancestry: shallow history refused as cannot-evaluate",
                      rc == EXIT_ERROR and "SHALLOW" in output and "unshallow" in output)
                check("ancestry: the complete shallow refusal text",
                      output == cannot_evaluate_output(shallow_prior, (
                          "cannot evaluate prior-store ancestry: this repository is SHALLOW, "
                          "so the first-parent history of HEAD is truncated and a prior store, "
                          "whose counters spec 8.2 forbids restarting, cannot be ruled out; "
                          "unshallow the clone (git fetch --unshallow) and retry")))
                check("ancestry parity: the coupled init's detector refuses the shallow clone",
                      coupled_holder(shallow_prior) == _opf_init_observe.CANNOT_EVALUATE)

                # SPEC 8.2 ancestry scope (first-parent line): a store that existed ONLY on a
                # side branch and was deleted there before the merge never reached a
                # first-parent tree, so the mainline being initialized never held its counters;
                # the scan follows the first-parent line (the same line the adoption seed is
                # proven on, _opf_init_operation decision 6) and init PROCEEDS -- side-line
                # ancestry is the section 14 adoption investigation concern. (A store that WAS
                # merged to the mainline is caught: the merge commit tree holds it on the
                # first-parent line.)
                sideline = make_git("ancestry-sideline")
                (sideline / "seed.txt").write_bytes(b"base\n")
                git_call(sideline, ["--literal-pathspecs", "add", "-A"])
                git_call(sideline, ["-c", "user.email=t@t", "-c", "user.name=t",
                                    "commit", "-m", "base"])
                git_call(sideline, ["checkout", "-q", "-b", "feature"])
                side_machine = sideline / working / machine_name
                side_machine.mkdir(parents=True)
                (side_machine / _opf_store.MANIFEST_NAME).write_bytes(b"x = 1\n")
                (sideline / _opf_store.POINTER_REL).write_bytes(
                    b'[store]\ntarget = "dir:."\n')
                git_call(sideline, ["--literal-pathspecs", "add", "-A"])
                git_call(sideline, ["-c", "user.email=t@t", "-c", "user.name=t",
                                    "commit", "-m", "store on side"])
                git_call(sideline, ["rm", "-r", "-q", "--",
                                    working, _opf_store.POINTER_REL])
                git_call(sideline, ["-c", "user.email=t@t", "-c", "user.name=t",
                                    "commit", "-m", "drop store on side"])
                git_call(sideline, ["checkout", "-q", "main"])
                git_call(sideline, ["-c", "user.email=t@t", "-c", "user.name=t",
                                    "merge", "-q", "--no-ff", "-m",
                                    "merge feature", "feature"])
                # The side branch IS merged (the case the round-1 "unmerged" wording missed).
                check("ancestry-sideline fixture: the store's side branch is merged into HEAD",
                      _opf_observe._run_git(git, sideline, ["merge-base", "--is-ancestor",
                                                            "feature", "HEAD"]).rc == 0)
                rc, output = run(sideline)
                check("ancestry: side-branch-only store does not block init (first-parent scope)",
                      rc == EXIT_OK and valid_sources(sideline))
                check("ancestry parity: the coupled init's detector finds no side-branch store",
                      coupled_holder(sideline) is None)
                # QA round 1: the first-parent limit is disclosed WHERE USERS SEE IT -- the
                # successful init output -- not only in the docstring. QA round 2 MAJOR 1: in
                # exact words, since this very fixture's store sat on a MERGED side branch.
                # DISCRIMINATOR: mutant M-output-unmerged (the round-1 output lines) fails.
                check("ancestry: success output states the exact first-parent limit",
                      "first-parent line only" in output and _states_sideline_limit(output))
                check("ancestry: success output states the current-path limit",
                      _states_path_limit(output))
                # The quickstart's statement of the same limit (QA round 2 MAJOR 1), read the
                # way check_opf_homes reads it. DISCRIMINATOR: the round-1 quickstart wording.
                quickstart = Path(__file__).resolve().parents[1] / "spec" / "OPF-QUICKSTART.md"
                check("ancestry: OPF-QUICKSTART.md states the exact first-parent limit",
                      _states_sideline_limit(quickstart.read_text(encoding="utf-8")))
                check("ancestry: OPF-QUICKSTART.md states the current-path limit",
                      _states_path_limit(quickstart.read_text(encoding="utf-8")))

                # SPEC 8.2 ancestry, POINTER-ONLY marker (spec 4.3 relocated store): history
                # holds ONLY the committed pointer .opf.toml (no .working tree at all), then
                # deletes it. Only the :(literal) pointer pathspec and tree_holds_store's
                # pointer arm can find it, so this fixture is the independent discriminator
                # for both: mutant M-pointer-spec (the :(literal) pathspec dropped) passes
                # the scan (rc 0), and mutant M-pointer-arm (the pointer arm disabled)
                # degrades the verdict to refusing-rather-than-guessing without the
                # prior-store finding; either way the assertions below fail.
                ptr_only = make_git("ancestry-pointer-only")
                (ptr_only / "seed.txt").write_bytes(b"base\n")
                git_call(ptr_only, ["--literal-pathspecs", "add", "-A"])
                git_call(ptr_only, ["-c", "user.email=t@t", "-c", "user.name=t",
                                    "commit", "-m", "base"])
                (ptr_only / _opf_store.POINTER_REL).write_bytes(
                    b'[store]\ntarget = "dir:../elsewhere"\n')
                git_call(ptr_only, ["--literal-pathspecs", "add", "-A"])
                git_call(ptr_only, ["-c", "user.email=t@t", "-c", "user.name=t",
                                    "commit", "-m", "pointer only"])
                ptr_commit = git_call(ptr_only, ["rev-parse", "HEAD"]).decode("ascii").strip()
                git_call(ptr_only, ["rm", "-q", "--", _opf_store.POINTER_REL])
                git_call(ptr_only, ["-c", "user.email=t@t", "-c", "user.name=t",
                                    "commit", "-m", "drop pointer"])
                rc, output = run(ptr_only)
                check("ancestry: pointer-only prior store refused",
                      rc == EXIT_ERROR and "prior store exists" in output)
                check("ancestry: pointer-only refusal names the holding commit",
                      ptr_commit in output)
                check("ancestry parity: the coupled init's detector names the pointer-only commit",
                      coupled_holder(ptr_only) == ptr_commit)
                # The restore command names only what that commit holds: git checkout fails
                # outright on a pathspec the commit lacks, so a command naming .working too
                # would restore nothing. DISCRIMINATOR: M-restore-both (every store path named
                # whatever the commit holds) fails the text and the restore run.
                check("ancestry: the complete pointer-only refusal text (pointer restored alone)",
                      output == prior_store_output(ptr_only, ptr_only, ptr_commit,
                                                   [".opf.toml"], True))
                check("ancestry: the printed pointer-only restore command restores the pointer",
                      run_restore(output) == 0
                      and (ptr_only / ".opf.toml").read_bytes()
                      == b'[store]\ntarget = "dir:../elsewhere"\n')

                # SPEC 8.2 ancestry, MANIFEST-ONLY marker (spec 4.3 default location, pointer
                # never committed), at the DEFAULT machine subdir: only the :(glob) manifest
                # pathspec and tree_holds_store's manifest listing can find it. Independent
                # discriminator for both: mutant M-manifest-spec (the :(glob) pathspec
                # dropped) passes the scan (rc 0), and mutant M-manifest-arm (the listing
                # arm disabled) loses the prior-store finding.
                mf_only = make_git("ancestry-manifest-only")
                (mf_only / "seed.txt").write_bytes(b"base\n")
                git_call(mf_only, ["--literal-pathspecs", "add", "-A"])
                git_call(mf_only, ["-c", "user.email=t@t", "-c", "user.name=t",
                                   "commit", "-m", "base"])
                mf_machine = mf_only / working / machine_name
                mf_machine.mkdir(parents=True)
                (mf_machine / _opf_store.MANIFEST_NAME).write_bytes(b"x = 1\n")
                git_call(mf_only, ["--literal-pathspecs", "add", "-A"])
                git_call(mf_only, ["-c", "user.email=t@t", "-c", "user.name=t",
                                   "commit", "-m", "manifest only"])
                mf_commit = git_call(mf_only, ["rev-parse", "HEAD"]).decode("ascii").strip()
                git_call(mf_only, ["rm", "-r", "-q", "--", working])
                git_call(mf_only, ["-c", "user.email=t@t", "-c", "user.name=t",
                                   "commit", "-m", "drop manifest"])
                rc, output = run(mf_only)
                check("ancestry: manifest-only prior store refused",
                      rc == EXIT_ERROR and "prior store exists" in output)
                check("ancestry: manifest-only refusal names the holding commit",
                      mf_commit in output)
                check("ancestry parity: the coupled init's detector names the manifest-only "
                      "commit", coupled_holder(mf_only) == mf_commit)
                check("ancestry: the complete manifest-only refusal text (.working alone, "
                      "its missing counters.toml named as a PARTIAL restore)",
                      output == prior_store_output(
                          mf_only, mf_only, mf_commit, [".working"], True,
                          gaps=[working + "/" + machine_name + "/"
                                + _opf_check.COUNTERS_NAME]))

                # The same manifest-only shape under a RENAMED machine subdir: spec 4.4/4.5
                # admit any single-level subdir name, so the glob (and the listing arm) must
                # match .working/<any>/manifest.toml, not just the default name.
                mf_renamed = make_git("ancestry-manifest-renamed")
                (mf_renamed / "seed.txt").write_bytes(b"base\n")
                git_call(mf_renamed, ["--literal-pathspecs", "add", "-A"])
                git_call(mf_renamed, ["-c", "user.email=t@t", "-c", "user.name=t",
                                      "commit", "-m", "base"])
                ren_machine = mf_renamed / working / "custom-machine"
                ren_machine.mkdir(parents=True)
                (ren_machine / _opf_store.MANIFEST_NAME).write_bytes(b"x = 1\n")
                git_call(mf_renamed, ["--literal-pathspecs", "add", "-A"])
                git_call(mf_renamed, ["-c", "user.email=t@t", "-c", "user.name=t",
                                      "commit", "-m", "renamed-machine manifest"])
                ren_commit = git_call(mf_renamed, ["rev-parse", "HEAD"]).decode("ascii").strip()
                git_call(mf_renamed, ["rm", "-r", "-q", "--", working])
                git_call(mf_renamed, ["-c", "user.email=t@t", "-c", "user.name=t",
                                      "commit", "-m", "drop renamed store"])
                rc, output = run(mf_renamed)
                check("ancestry: renamed-machine-subdir manifest refused",
                      rc == EXIT_ERROR and "prior store exists" in output)
                check("ancestry: renamed-machine refusal names the holding commit",
                      ren_commit in output)
                check("ancestry parity: the coupled init's detector names the renamed-machine "
                      "commit", coupled_holder(mf_renamed) == ren_commit)
                check("ancestry: the complete renamed-machine refusal text (its missing "
                      "counters.toml named as a PARTIAL restore)",
                      output == prior_store_output(
                          mf_renamed, mf_renamed, ren_commit, [".working"], True,
                          gaps=[working + "/custom-machine/" + _opf_check.COUNTERS_NAME]))

                # SPEC 8.2 ancestry, GRAFT fail-closed (QA round 1 MAJOR): a legacy
                # .git/info/grafts line naming the deletion commit WITH NO PARENTS cuts the
                # parent link inside every rev-list walk (--no-replace-objects neutralizes
                # replace refs, NOT grafts), so the pre-fix scan saw no store-path commit,
                # re-init passed (rc 0), and counters restarted at zero over the BI = 7
                # high-water this history holds. A grafts file that exists is a
                # cannot-evaluate REFUSAL, never interpreted (the adoption reader's stance);
                # removing it restores the ordinary prior-store refusal, proving the graft
                # was the only thing hiding the store. DISCRIMINATOR: rc 0 and a re-zeroed
                # counters.toml against a scan that lets git resolve grafts.
                grafted = make_git("ancestry-graft")
                rc, output = init_store(grafted)
                check("ancestry graft fixture first init succeeds", rc == EXIT_OK)
                git_call(grafted, ["--literal-pathspecs", "add", "-A"])
                git_call(grafted, ["-c", "user.email=t@t", "-c", "user.name=t",
                                   "commit", "-m", "store"])
                g_counters = grafted / working / machine_name / _opf_check.COUNTERS_NAME
                g_seeded = g_counters.read_text(encoding="utf-8").replace("BI = 0", "BI = 7")
                if "BI = 7" not in g_seeded:
                    raise OSError("graft fixture: counters.toml carried no BI = 0 to raise")
                g_counters.write_text(g_seeded, encoding="utf-8")
                git_call(grafted, ["--literal-pathspecs", "add", "-A"])
                git_call(grafted, ["-c", "user.email=t@t", "-c", "user.name=t",
                                   "commit", "-m", "BI high-water 7"])
                g_held = head_of(grafted)
                git_call(grafted, ["rm", "-r", "-q", "--",
                                   working, _opf_store.POINTER_REL])
                git_call(grafted, ["-c", "user.email=t@t", "-c", "user.name=t",
                                   "commit", "-m", "drop store"])
                g_drop = git_call(grafted, ["rev-parse", "HEAD"]).decode("ascii").strip()
                g_file = grafted / ".git" / "info" / "grafts"
                # init.templateDir= leaves fixtures without .git/info; a real adopter repo has it.
                g_file.parent.mkdir(exist_ok=True)
                g_file.write_bytes((g_drop + "\n").encode("ascii"))
                before = _snapshot(grafted)
                rc, output = run(grafted)
                check("ancestry: grafted-away history refused as cannot-evaluate",
                      rc == EXIT_ERROR and "grafts" in output and "cannot evaluate" in output)
                check("ancestry: grafted re-init wrote nothing (counters never re-zeroed)",
                      _snapshot(grafted) == before)
                check("ancestry parity: the coupled init's detector refuses the grafts entry",
                      coupled_holder(grafted) == _opf_init_observe.CANNOT_EVALUATE)
                # QA round 2 MINOR 3: the grafts refusal states exactly what it found, for
                # every kind of entry, never that the entry "rewrites parent links" (an empty
                # file or a dangling link rewrites nothing). DISCRIMINATOR: mutant
                # M-grafts-found (one fixed description for every kind) fails the kind checks.
                check("ancestry: grafts refusal names a regular file and its size",
                      inert("found a regular file of {} byte(s)".format(len(g_drop) + 1))
                      in output
                      and inert("may rewrite parent links") in output)
                check("ancestry: the complete grafts refusal text (regular file)",
                      output == cannot_evaluate_output(grafted, grafts_message(
                          "a regular file of " + str(len(g_drop) + 1) + " byte(s)",
                          str(g_file))))
                g_file.unlink()
                g_file.write_bytes(b"")
                rc, output = run(grafted)
                check("ancestry: an EMPTY grafts file refuses, named as 0 bytes",
                      rc == EXIT_ERROR and output == cannot_evaluate_output(
                          grafted, grafts_message("a regular file of 0 byte(s)", str(g_file))))
                g_file.unlink()
                g_file.symlink_to("absent-grafts-target")
                rc, output = run(grafted)
                check("ancestry: a DANGLING grafts link refuses, named with its target",
                      rc == EXIT_ERROR and output == cannot_evaluate_output(
                          grafted, grafts_message("a symbolic link to 'absent-grafts-target'",
                                                  str(g_file))))
                g_file.unlink()
                g_file.mkdir()
                rc, output = run(grafted)
                check("ancestry: a grafts DIRECTORY refuses, named by kind",
                      rc == EXIT_ERROR and output == cannot_evaluate_output(
                          grafted, grafts_message("an entry of kind directory", str(g_file))))
                g_file.rmdir()
                # QA round 3 MINOR 3: the "special" kind arm, through a FIFO at the grafts
                # path. lstat never opens it, but a regression that read the path would block
                # on the FIFO, so this vector always runs init in a child with a timeout and an
                # address-space limit, never in this process. DISCRIMINATOR: M-special-kind
                # (the else arm's description changed) fails the complete text.
                os.mkfifo(g_file)
                fifo_proc = subprocess.run(
                    [sys.executable, "-I", "-B", str(Path(__file__).resolve().parent / "opf.py"),
                     "init", "--root", str(grafted)],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=120,
                    preexec_fn=_limit_address_space)
                check("ancestry: a grafts FIFO refuses, named as an entry of kind special",
                      fifo_proc.returncode == EXIT_ERROR
                      and fifo_proc.stdout + fifo_proc.stderr == cannot_evaluate_output(
                          grafted, grafts_message("an entry of kind special", str(g_file))))
                g_file.unlink()
                rc, output = run(grafted)
                check("ancestry: grafts file removed, the prior store is found again",
                      rc == EXIT_ERROR and output == prior_store_output(
                          grafted, grafted, g_held, [".opf.toml", ".working"], True))
                check("ancestry parity: grafts file removed, the coupled init's detector names "
                      "the same commit", coupled_holder(grafted) == g_held)

                # QA round 2 MINOR 4: the info/grafts PATH arms, pinned. git prints the
                # grafts path of a separate git directory as an absolute path, so a git
                # directory name carrying a non-UTF-8 byte, or a newline, reaches the shape
                # checks. DISCRIMINATORS: mutant M-undecodable-arm (lenient decode, no
                # refusal) lstat()s a mangled path, reads it as absent and passes (rc 0);
                # mutant M-malformed-arm (shape check removed) passes the same way (rc 0).
                def make_separate(name, gitdir_name):
                    sep_root = base / name
                    sep_root.mkdir()
                    git_call(sep_root, ["-c", "init.templateDir=", "-c", "init.defaultBranch=main",
                                        "init", "--separate-git-dir",
                                        str(base / os.fsdecode(gitdir_name))])
                    (sep_root / "seed.txt").write_bytes(b"base\n")
                    git_call(sep_root, ["--literal-pathspecs", "add", "-A"])
                    git_call(sep_root, ["-c", "user.email=t@t", "-c", "user.name=t",
                                        "commit", "-m", "base"])
                    return sep_root

                undecodable = make_separate("ancestry-undecodable-gitdir", b"gitdir-\xff")
                rc, output = run(undecodable)
                check("ancestry: undecodable info/grafts path refused, never read as absent",
                      rc == EXIT_ERROR and "cannot evaluate" in output
                      and inert("undecodable info/grafts path") in output)
                check("ancestry: the complete undecodable-grafts-path refusal text",
                      output == cannot_evaluate_output(undecodable, (
                          "git history preflight: undecodable info/grafts path "
                          + ascii(os.fsencode(str(base)) + b"/gitdir-\xff/info/grafts\n")
                          + "; refusing")))
                newline_dir = make_separate("ancestry-newline-gitdir", b"gitdir-\nx")
                rc, output = run(newline_dir)
                check("ancestry: malformed (multi-line) info/grafts path refused",
                      rc == EXIT_ERROR and "cannot evaluate" in output
                      and inert("malformed info/grafts path") in output)
                check("ancestry: the complete multi-line-grafts-path refusal text",
                      output == cannot_evaluate_output(newline_dir, (
                          "git history preflight: malformed info/grafts path "
                          + ascii(os.fsencode(str(base)) + b"/gitdir-\nx/info/grafts\n")
                          + "; refusing")))

                # The lstat-failure arm: .git/info is a regular FILE, so lstat of info/grafts
                # fails with NotADirectoryError; the refusal states that exact failure.
                # DISCRIMINATOR: mutant M-lstat-arm (any OSError read as absent) passes (rc 0).
                info_file = make_git("ancestry-info-not-a-directory")
                (info_file / "seed.txt").write_bytes(b"base\n")
                git_call(info_file, ["--literal-pathspecs", "add", "-A"])
                git_call(info_file, ["-c", "user.email=t@t", "-c", "user.name=t",
                                     "commit", "-m", "base"])
                (info_file / ".git" / "info").write_bytes(b"not a directory\n")
                rc, output = run(info_file)
                check("ancestry: an uninspectable grafts path refuses, naming the lstat failure",
                      rc == EXIT_ERROR and inert("lstat of the legacy grafts path") in output
                      and "NotADirectoryError" in output)
                info_grafts = str(info_file / ".git" / "info" / "grafts")
                try:
                    os.lstat(info_grafts)
                    info_failure = None
                except OSError as exc:
                    info_failure = exc
                check("ancestry: the complete lstat-failure refusal text",
                      isinstance(info_failure, NotADirectoryError)
                      and output == cannot_evaluate_output(info_file, (
                          "cannot evaluate prior-store ancestry: lstat of the legacy grafts "
                          "path " + ascii(info_grafts) + " failed (" + ascii(info_failure)
                          + "), which this scan does not take as proof that no grafts file is "
                          "present; grafts that git can read at that path may rewrite parent "
                          "links in every history walk and hide a prior store whose counters "
                          "spec 8.2 forbids restarting; refusing")))

                # The POSIX-separator helper, pinned on POSIX hosts: a Windows-flavoured prefix
                # must still yield "/" pathspecs. DISCRIMINATOR: mutant M-as-posix-to-str
                # (str(Path) instead of as_posix) yields backslashes and fails.
                import opf
                if opf._bootstrap() != EXIT_OK:
                    raise OSError("opf helper modules could not be imported")
                check("ancestry: history pathspecs use POSIX separators for a Windows prefix",
                      opf._init_history_rels(PureWindowsPath("sub", "dir"))
                      == ("sub/dir/" + _opf_store.POINTER_REL, "sub/dir/" + working))

                # QA round 4 MINOR 4: the docstring's statement of the current-path limit,
                # pinned like the three user-facing sites (the refusal, the success output and
                # OPF-QUICKSTART.md), so the fourth copy cannot drift from them silently.
                check("ancestry: the _init_no_prior_store docstring states the current-path "
                      "limit in the shared words",
                      _states_path_limit(opf._init_no_prior_store.__doc__))

                # #516: a NESTED product root beneath an enclosing repository with search but no read
                # permission (0111). _init_repo opens the repository root only as a no-follow probe it
                # closes unread, and _init_same_root only fstat-s its descriptor, so both open
                # traverse_only: where os.O_PATH exists the preflight passes and a search-only root
                # binds. The init operation mutex then refuses that nested root, with nothing written:
                # the shared lock binds a nested store's path by listing the git toplevel, which needs
                # read permission there (the residual _opf_oplock._bind_repository_view discloses).
                # DISCRIMINATORS: the readable open restored at either site refuses EACCES (the nested
                # init exits 2 in preflight, never at the mutex; the identity check raises
                # PermissionError). Mode bits do not
                # bind uid 0, so that run discloses a skip. Without os.O_PATH the walk keeps its
                # documented O_RDONLY fallback, so the EACCES refusal is asserted there instead
                # (fail-closed); the root refusal is compared by its errno (errno.EACCES, never the
                # "[Errno N]" text, which ascii() of an OSError omits) and by the refused component, the
                # fixture root itself, and an injected EIO refusal of the same root is a control that
                # must not pass for it. The fallback runs natively where os.O_PATH is absent and is
                # SIMULATED where it exists, as simulated_fallback in check_opf_init_observe.py does:
                # os.O_PATH is hidden and _opf_store._WALK_O_TRAVERSE set to os.O_RDONLY, with the nested
                # init driven in process. The simulation opens each strict ancestor of the fixture
                # parent in the native traverse mode, so a search-only ancestor of the temporary
                # directory (a root-owned 0711 scratch root) cannot answer EACCES for the fixture: only
                # the mode bits of the fixture decide. A native run without O_PATH is not claimed by it.
                from unittest import mock

                def search_only_vectors(prefix, label, init_run):
                    so_repo = make_git(prefix + "search-only-enclosing-repo")
                    so_product = so_repo / "product"
                    so_product.mkdir()
                    so_root = base / (prefix + "search-only-root")
                    so_root.mkdir()
                    so_other = base / (prefix + "search-only-root-other")
                    so_other.mkdir()
                    os.chmod(so_repo, 0o111)
                    try:
                        rc, output = init_run(so_product)
                    finally:
                        os.chmod(so_repo, 0o755)

                    def same_root(candidate, fd):
                        # A refusal keeps its errno and the component it names.
                        try:
                            opf._init_same_root(candidate, fd)
                            return ("bound",)
                        except RuntimeError as exc:
                            return ("moved", str(exc))
                        except OSError as exc:
                            return ("refused", exc.errno, exc.filename)

                    def injected_eio(path, **kwargs):
                        raise OSError(errno.EIO, os.strerror(errno.EIO), Path(path).name)

                    so_fd = _opf_store._open_dir_nofollow(str(so_root), traverse_only=True)
                    try:
                        os.chmod(so_root, 0o111)
                        try:
                            so_bound = same_root(so_root, so_fd)
                            so_moved = same_root(so_other, so_fd)
                            with mock.patch.object(_opf_store, "_open_dir_nofollow", injected_eio):
                                so_eio = same_root(so_root, so_fd)
                        finally:
                            os.chmod(so_root, 0o755)
                    finally:
                        os.close(so_fd)
                    check(label + "the root identity check still refuses a different directory",
                          so_moved == ("moved", "root changed since its contained directory was opened"))
                    if hasattr(os, "O_PATH"):
                        check(label + "a nested product root beneath a search-only (0111) enclosing "
                              "repository passes the preflight and is refused at the init mutex, "
                              "nothing written",
                              rc == EXIT_ERROR
                              and "during taking-the-init-operation-mutex: OpLockError(" in output
                              and inert(os.strerror(errno.EACCES)) in output
                              and sorted(entry.name for entry in so_product.iterdir()) == [])
                        check(label + "the root identity check binds a search-only (0111) root",
                              so_bound == ("bound",))
                        return

                    def documented_refusal(outcome):
                        return outcome == ("refused", errno.EACCES, so_root.name)

                    # The preflight refusal names EACCES at the enclosing repository component itself.
                    repo_refusal = PermissionError(errno.EACCES, os.strerror(errno.EACCES), so_repo.name)
                    check(label + "without os.O_PATH a search-only (0111) enclosing repository keeps the "
                          "documented EACCES refusal",
                          rc == EXIT_ERROR and "PermissionError(" + inert(str(repo_refusal)) + ")" in output
                          and not (so_product / working).exists())
                    check(label + "without os.O_PATH a search-only (0111) root keeps the documented EACCES "
                          "refusal", documented_refusal(so_bound))
                    check(label + "control: an injected EIO refusal of the root is not the documented "
                          "EACCES refusal",
                          so_eio == ("refused", errno.EIO, so_root.name) and not documented_refusal(so_eio))

                if hasattr(os, "geteuid") and os.geteuid() == 0:
                    print("check_opf_init: DISCLOSED SKIP: search-only (0111) enclosing repository and root "
                          "vectors not run (uid 0 is not bound by mode bits)", file=sys.stderr)
                else:
                    search_only_vectors("", "", run)
                if hasattr(os, "O_PATH") and not (hasattr(os, "geteuid") and os.geteuid() == 0):
                    real_open = os.open
                    saved_path, saved_walk = os.O_PATH, _opf_store._WALK_O_TRAVERSE
                    above = set()
                    for ancestor in base.parents:
                        ancestor_stat = os.stat(ancestor)
                        above.add((ancestor_stat.st_dev, ancestor_stat.st_ino))

                    def native_above(path, flags, *args, dir_fd=None, **kwargs):
                        # Only a read-only directory open of a strict ancestor of the fixture parent is
                        # given the native traverse mode; a symlink is never an ancestor (lstat identity).
                        if flags & os.O_DIRECTORY and flags & os.O_ACCMODE == os.O_RDONLY:
                            try:
                                target = os.stat(path, dir_fd=dir_fd, follow_symlinks=False)
                            except OSError:
                                target = None
                            if target is not None and (target.st_dev, target.st_ino) in above:
                                flags |= saved_path
                        return real_open(path, flags, *args, dir_fd=dir_fd, **kwargs)

                    # native_above forwards dir_fd, so it joins os.supports_dir_fd: the containment
                    # probe (_journal.require_containment) must not refuse the in-process init instead.
                    simulated_dir_fd = os.supports_dir_fd | {native_above}
                    del os.O_PATH
                    _opf_store._WALK_O_TRAVERSE = os.O_RDONLY
                    try:
                        with mock.patch.object(os, "open", native_above):
                            with mock.patch.object(os, "supports_dir_fd", simulated_dir_fd):
                                search_only_vectors(
                                    "simulated-", "simulated no-O_PATH fallback: ",
                                    lambda root: fd_capture(lambda: opf._cmd_init(["--root", str(root)])))
                    finally:
                        os.O_PATH = saved_path
                        _opf_store._WALK_O_TRAVERSE = saved_walk

                # QA round 3 MINOR 3: the missing-newline and NUL arms of the info/grafts shape
                # check. Real git always ends the line with a newline and cannot print a NUL in
                # a path, so these answers come from a stubbed _run_git (a commit-holding,
                # unshallow history whose rev-list finds nothing), driving the scan directly.
                # DISCRIMINATORS: M-endswith-arm (the endswith test removed) strips the last
                # path character, lstats an absent path and PASSES (no exception);
                # M-nul-arm (the NUL test removed) reaches lstat, which raises ValueError
                # (embedded null character) instead of the refusal; both fail the exact text.
                from unittest import mock

                def stubbed_scan(grafts_answer):
                    calls = []

                    def fake_run_git(git_arg, repo_arg, args, **kwargs):
                        calls.append(list(args))
                        answers = [
                            (["rev-parse", "--verify", "--quiet", "HEAD"], b"0" * 40 + b"\n"),
                            (["rev-parse", "--is-shallow-repository"], b"false\n"),
                            (["rev-parse", "--git-path", "info/grafts"], grafts_answer),
                        ]
                        for expected_args, out in answers:
                            if list(args) == expected_args:
                                return _opf_observe._GitOutcome(True, 0, out, "")
                        if list(args[:1]) == ["rev-list"]:
                            return _opf_observe._GitOutcome(True, 0, b"", "")
                        return _opf_observe._GitOutcome(False, None, b"", "unexpected git call")

                    stub_repo = base / "stub-repo"
                    with mock.patch.object(opf._opf_observe, "_run_git", fake_run_git):
                        try:
                            opf._init_no_prior_store(git, stub_repo, stub_repo)
                            return None, calls
                        except Exception as exc:  # noqa: BLE001  the type is part of the verdict
                            return (type(exc), str(exc)), calls

                absent_grafts = os.fsencode(str(base / "stub-absent" / "info" / "grafts"))
                verdict, calls = stubbed_scan(absent_grafts)
                check("ancestry: a grafts path answer missing its newline refuses, before any walk",
                      verdict == (RuntimeError, "git history preflight: malformed info/grafts "
                                  "path " + ascii(absent_grafts) + "; refusing")
                      and calls[-1] == ["rev-parse", "--git-path", "info/grafts"])
                nul_grafts = os.fsencode(str(base / "stub-absent")) + b"/in\0fo/grafts\n"
                verdict, calls = stubbed_scan(nul_grafts)
                check("ancestry: a grafts path answer holding a NUL refuses, before any walk",
                      verdict == (RuntimeError, "git history preflight: malformed info/grafts "
                                  "path " + ascii(nul_grafts) + "; refusing")
                      and calls[-1] == ["rev-parse", "--git-path", "info/grafts"])
                verdict, calls = stubbed_scan(absent_grafts + b"\n")
                check("ancestry: the stubbed scan passes a well-formed absent grafts path",
                      verdict is None and calls[-1][:1] == ["rev-list"])

                # QA round 2 MINOR 2, QA round 3 MEDIUM 1 and MINOR 2, QA round 4 MINOR 2:
                # path-derived text in the refusal. Each fixture commits a store at a sub-root
                # with a hostile or non-ASCII name, deletes the store (CHANGELOG.md stays), and
                # re-inits there; the complete output is compared with text built here.
                # QA round 6: a command is printed ONLY when every value in it renders
                # UNCHANGED through the one inert renderer; any name outside the inert
                # alphabet (a control or format character, a space, a quote, "$", an
                # accent) withholds the command, and the refusal carries only inert
                # withheld-command detail lines, because an escaped or quoted command can
                # stay syntactically whole while a pasted physical line of it runs (the
                # round-6 newline MAJOR). DISCRIMINATORS: M-refusal-raw (paths formatted
                # raw) fails every hostile vector; the round-6 shlex-quoting code prints a
                # shell-ready command for cafe, it's$HOME and the both-quotes name and
                # fails their complete withheld texts.
                def sub_root_refusal(fixture, name):
                    repo = make_git(fixture)
                    sub = repo / name
                    sub.mkdir()
                    first_rc, first_output = init_store(sub)
                    git_call(repo, ["--literal-pathspecs", "add", "-A"])
                    git_call(repo, ["-c", "user.email=t@t", "-c", "user.name=t",
                                    "commit", "-m", "store"])
                    commit = head_of(repo)
                    held = store_contents(sub)
                    git_call(repo, ["--literal-pathspecs", "rm", "-r", "-q", "--",
                                    name + "/.working", name + "/.opf.toml"])
                    git_call(repo, ["-c", "user.email=t@t", "-c", "user.name=t",
                                    "commit", "-m", "drop store"])
                    sub_rc, sub_output = run(sub)
                    return (repo, sub, commit, held, first_rc, first_output,
                            sub_rc, sub_output)

                def raw_free(text):
                    return all(ch.isprintable() or ch == "\n" for ch in text)

                format_name = "f" + chr(0x202E) + "evil" + chr(0x200B) + "zw" + chr(0x9B) + "c1"
                for label, fixture, name in (
                        ("C0 controls ESC and BEL", "ancestry-control-chars", "a\x1b[31mred\x07b"),
                        ("format characters U+202E and U+200B and the C1 control U+009B",
                         "ancestry-format-chars", format_name)):
                    (repo, sub, commit, held, first_rc, _first,
                     rc, output) = sub_root_refusal(fixture, name)
                    check("ancestry " + label + ": fixture first init succeeds",
                          first_rc == EXIT_OK)
                    check("ancestry " + label + ": refused with the complete text, the "
                          "command withheld and the details inert-escaped",
                          rc == EXIT_ERROR and output == prior_store_output(
                              sub, repo, commit, [".opf.toml", ".working"], False))
                    check("ancestry " + label + ": no raw non-printable character in the output",
                          raw_free(output))
                    check("ancestry " + label + ": no runnable restore line is printed",
                          not any(line.startswith("  git")
                                  for line in output.splitlines()))

                for label, fixture, name in (
                        ("printable non-ASCII cafe", "ancestry-cafe", "caf" + chr(0xE9)),
                        ("shell-active it's$HOME", "ancestry-shell-chars", "it's$HOME")):
                    (repo, sub, commit, held, first_rc, first_out,
                     rc, output) = sub_root_refusal(fixture, name)
                    check("ancestry " + label + ": fixture first init succeeds",
                          first_rc == EXIT_OK)
                    check("ancestry " + label + ": refused with the complete text, the "
                          "command withheld under the render-unchanged gate",
                          rc == EXIT_ERROR and output == prior_store_output(
                              sub, repo, commit, [".opf.toml", ".working"], False))
                    check("ancestry " + label + ": no runnable restore line is printed",
                          not any(line.startswith("  git")
                                  for line in output.splitlines()))
                    check("ancestry " + label + ": the success output withholds the "
                          "staging and render hints",
                          "command hints withheld" in first_out
                          and not any(line.startswith("  git")
                                      for line in first_out.splitlines())
                          and "opf render --write --root" not in first_out)

                # The shell-ready arm at a SUB-ROOT (relative restored paths), under a
                # name inside the inert alphabet: the command is printed bare (no quoting
                # exists to drop) and a POSIX shell restores the committed store from it.
                (repo, sub, commit, held, first_rc, _first,
                 rc, output) = sub_root_refusal("ancestry-plain-sub", "plain-sub.dir")
                check("ancestry plain sub-root: fixture first init succeeds",
                      first_rc == EXIT_OK)
                check("ancestry plain sub-root: refused with the complete text and a "
                      "shell-ready restore command",
                      rc == EXIT_ERROR and output == prior_store_output(
                          sub, repo, commit, [".opf.toml", ".working"], True))
                check("ancestry plain sub-root: the printed restore command, run by a "
                      "POSIX shell, restores the committed store",
                      run_restore(output) == 0 and store_contents(sub) == held)

                # QA round 4 MINOR 2 and QA round 5 MAJOR (treated as security fixes): the
                # refusal must print NOTHING a shell would execute, on ANY line. The round-3
                # fallback printed an ascii()-escaped command, and for a name holding a
                # single quote and no double quote, ascii() yields a DOUBLE-quoted Python
                # literal in which $(touch PWNED) stays live under POSIX double-quote
                # semantics: pasting that line created PWNED. The round-4 vector then probed
                # only the remedy block, while the REFUSED line itself still interpolated the
                # root with ascii() (QA round 5 MAJOR: for a name holding both quote kinds
                # ascii() emits a single-quoted literal whose embedded backslash-quote ends
                # the shell's quote early and re-arms the substitution). Now every
                # interpolation is inert-escaped, no command line is printed for a
                # non-printable path, the WHOLE output carries no "$" or "`" at all, and
                # sh_inert_probe runs the complete output, and separately each of its lines,
                # through a real POSIX shell from an EMPTY directory, asserting nothing was
                # created and no payload ran. DISCRIMINATORS: M-ascii-fallback (the round-3
                # escaped command printed) creates PWNED in the empty directory; a safe set
                # widened to admit "$" fails the no-dollar check; an ascii()-formatted
                # REFUSED line fails the complete text and leaves "$" in the output.
                subst_name = "x'$(touch PWNED)\x07"
                (repo, sub, commit, held, first_rc, first_out,
                 rc, output) = sub_root_refusal("ancestry-subst", subst_name)
                check("ancestry substitution name: fixture first init succeeds",
                      first_rc == EXIT_OK)
                check("ancestry substitution name: refused with the complete withheld text",
                      rc == EXIT_ERROR and output == prior_store_output(
                          sub, repo, commit, [".opf.toml", ".working"], False))
                check("ancestry substitution name: no raw non-printable character in the output",
                      raw_free(output))
                check("ancestry substitution name: the whole output is the withheld form, "
                      "with no command line and no substitution character anywhere",
                      "opf init: restore command withheld" in output
                      and not any(line.startswith("  git")
                                  for line in output.splitlines())
                      and "$" not in output and "`" not in output)
                check("ancestry substitution name: the complete refusal output run through "
                      "sh in an empty directory, whole and line by line, created nothing "
                      "and ran no payload",
                      sh_inert_probe(output, "subst-refusal") == []
                      and not (repo / "PWNED").exists()
                      and not (sub / "PWNED").exists())
                check("ancestry substitution name: the successful first-init output is "
                      "shell-inert the same way",
                      sh_inert_probe(first_out, "subst-success") == []
                      and not (repo / "PWNED").exists()
                      and not (sub / "PWNED").exists())

                # QA round 4 MINOR 3: the REPOSITORY-path half of the printable gate, with the
                # root at the repository top and every restored path printable, so only
                # str(repo) can trip the gate. A gate reading the restored paths alone would
                # label this remedy shell-ready and print the raw ESC control character.
                # DISCRIMINATOR: M-gate-ignores-repo ([str(repo)] dropped from the gate)
                # prints a shell-ready command with a raw ESC and fails both checks.
                ctl_repo = make_git("ancestry-repo\x1b[31mred")
                rc, output = init_store(ctl_repo)
                check("ancestry control-character repository: fixture first init succeeds",
                      rc == EXIT_OK)
                git_call(ctl_repo, ["--literal-pathspecs", "add", "-A"])
                git_call(ctl_repo, ["-c", "user.email=t@t", "-c", "user.name=t",
                                    "commit", "-m", "store"])
                ctl_commit = head_of(ctl_repo)
                git_call(ctl_repo, ["rm", "-r", "-q", "--",
                                    working, _opf_store.POINTER_REL])
                git_call(ctl_repo, ["-c", "user.email=t@t", "-c", "user.name=t",
                                    "commit", "-m", "drop store"])
                rc, output = run(ctl_repo)
                check("ancestry control-character repository: refused with the complete "
                      "withheld text, the repository path alone tripping the gate",
                      rc == EXIT_ERROR and output == prior_store_output(
                          ctl_repo, ctl_repo, ctl_commit,
                          [".opf.toml", ".working"], False))
                check("ancestry control-character repository: no raw non-printable character "
                      "and no runnable restore line in the output",
                      raw_free(output)
                      and not any(line.startswith("  git")
                                  for line in output.splitlines()))

                # QA round 4 MEDIUM (replacement refs): the printed command carries
                # --no-replace-objects, the neutralization every scan read already runs
                # under, because git checkout honours replacement refs: without the flag a
                # replacement ref from the named commit to one with different store bytes
                # makes a shell-ready command exit 0 while writing the SUBSTITUTED bytes.
                # Fixture: commit the store, commit a substituted-counters variant on a side
                # branch, delete the store on main, install git replace from the store commit
                # to the variant, re-init. The scan ignores the replacement and names the real
                # store commit; the printed command must restore that commit's ORIGINAL
                # bytes. The potency probe shows a flagless checkout of the same commit really
                # follows the replacement in this fixture, so the byte comparison
                # DISCRIMINATES M-no-replace-flag (the flag dropped from the printed command).
                repl = make_git("ancestry-replace")
                rc, output = init_store(repl)
                check("ancestry replacement fixture first init succeeds", rc == EXIT_OK)
                git_call(repl, ["--literal-pathspecs", "add", "-A"])
                git_call(repl, ["-c", "user.email=t@t", "-c", "user.name=t",
                                "commit", "-m", "store"])
                repl_commit = head_of(repl)
                repl_original = store_contents(repl)
                git_call(repl, ["checkout", "-q", "-b", "substituted"])
                repl_counters = repl / working / machine_name / _opf_check.COUNTERS_NAME
                repl_sub = repl_counters.read_text(encoding="utf-8").replace("BI = 0", "BI = 9")
                if "BI = 9" not in repl_sub:
                    raise OSError("replacement fixture: counters.toml carried no BI = 0 "
                                  "to substitute")
                repl_counters.write_text(repl_sub, encoding="utf-8")
                git_call(repl, ["--literal-pathspecs", "add", "-A"])
                git_call(repl, ["-c", "user.email=t@t", "-c", "user.name=t",
                                "commit", "-m", "substituted counters"])
                repl_bad = head_of(repl)
                git_call(repl, ["checkout", "-q", "main"])
                git_call(repl, ["rm", "-r", "-q", "--",
                                working, _opf_store.POINTER_REL])
                git_call(repl, ["-c", "user.email=t@t", "-c", "user.name=t",
                                "commit", "-m", "drop store"])
                git_call(repl, ["replace", repl_commit, repl_bad])
                rc, output = run(repl)
                check("ancestry: a replacement ref changes neither the scan nor the complete "
                      "refusal text",
                      rc == EXIT_ERROR and output == prior_store_output(
                          repl, repl, repl_commit, [".opf.toml", ".working"], True))
                # Potency: git_input runs raw git (no --no-replace-objects), so this checkout
                # resolves the store commit THROUGH the replacement and writes the variant.
                git_input(repl, ["--literal-pathspecs", "checkout", repl_commit, "--",
                                 _opf_store.POINTER_REL, working], b"")
                check("ancestry replacement fixture potency: a flagless checkout of the same "
                      "commit restores the substituted bytes",
                      repl_counters.read_text(encoding="utf-8") == repl_sub
                      and store_contents(repl) != repl_original)
                check("ancestry: the printed restore command restores the ORIGINAL store "
                      "bytes with the replacement ref still installed",
                      run_restore(output) == 0 and store_contents(repl) == repl_original)

                # QA round 4 MEDIUM 1 (split deletion): .working deleted in one commit and
                # the pointer in a later one left the round-3 remedy restoring the pointer
                # ALONE while presenting that one command as the store restore: the named
                # commit (the final deletion's first parent) holds only .opf.toml, whose
                # dir:. target, counters included, stayed deleted after an exit-0 command.
                # Now the refusal labels the first command incomplete by itself and prints an
                # ADDITIONAL command restoring .working from the newest first-parent tree
                # that holds it (here the counter-bump commit); running both restores the
                # whole store, high-water included. DISCRIMINATOR: M-no-split-probe (the
                # probe removed) prints the round-3 single pointer-only command labelled as
                # the store restore and fails the text and the byte comparison.
                split = make_git("ancestry-split-working-first")
                rc, output = init_store(split)
                check("ancestry split-deletion fixture first init succeeds", rc == EXIT_OK)
                git_call(split, ["--literal-pathspecs", "add", "-A"])
                git_call(split, ["-c", "user.email=t@t", "-c", "user.name=t",
                                 "commit", "-m", "store"])
                split_counters = split / working / machine_name / _opf_check.COUNTERS_NAME
                split_seeded = split_counters.read_text(encoding="utf-8").replace(
                    "BI = 0", "BI = 7")
                if "BI = 7" not in split_seeded:
                    raise OSError("split fixture: counters.toml carried no BI = 0 to raise")
                split_counters.write_text(split_seeded, encoding="utf-8")
                git_call(split, ["--literal-pathspecs", "add", "-A"])
                git_call(split, ["-c", "user.email=t@t", "-c", "user.name=t",
                                 "commit", "-m", "BI high-water 7"])
                split_full = head_of(split)
                split_original = store_contents(split)
                git_call(split, ["rm", "-r", "-q", "--", working])
                git_call(split, ["-c", "user.email=t@t", "-c", "user.name=t",
                                 "commit", "-m", "drop working first"])
                split_ptr = head_of(split)
                git_call(split, ["rm", "-q", "--", _opf_store.POINTER_REL])
                git_call(split, ["-c", "user.email=t@t", "-c", "user.name=t",
                                 "commit", "-m", "drop pointer second"])
                rc, output = run(split)
                check("ancestry split deletion: the complete refusal names both commits and "
                      "labels the pointer-only command incomplete by itself",
                      rc == EXIT_ERROR and output == prior_store_output(
                          split, split, split_ptr, [".opf.toml"], True,
                          extra=(split_full, [".working"])))
                check("ancestry split deletion: running both printed commands restores the "
                      "whole store, high-water counters included",
                      run_restore(output) == 0
                      and store_contents(split) == split_original)

                # The same split in the OTHER order (pointer deleted first), exercising the
                # pointer arm of the probe: the named commit holds .working alone and the
                # additional command restores .opf.toml.
                split2 = make_git("ancestry-split-pointer-first")
                rc, output = init_store(split2)
                check("ancestry pointer-first split fixture first init succeeds",
                      rc == EXIT_OK)
                git_call(split2, ["--literal-pathspecs", "add", "-A"])
                git_call(split2, ["-c", "user.email=t@t", "-c", "user.name=t",
                                  "commit", "-m", "store"])
                split2_full = head_of(split2)
                split2_original = store_contents(split2)
                git_call(split2, ["rm", "-q", "--", _opf_store.POINTER_REL])
                git_call(split2, ["-c", "user.email=t@t", "-c", "user.name=t",
                                  "commit", "-m", "drop pointer first"])
                split2_wrk = head_of(split2)
                git_call(split2, ["rm", "-r", "-q", "--", working])
                git_call(split2, ["-c", "user.email=t@t", "-c", "user.name=t",
                                  "commit", "-m", "drop working second"])
                rc, output = run(split2)
                check("ancestry pointer-first split: the complete refusal restores .working "
                      "first and the pointer through the additional command",
                      rc == EXIT_ERROR and output == prior_store_output(
                          split2, split2, split2_wrk, [".working"], True,
                          extra=(split2_full, [".opf.toml"])))
                check("ancestry pointer-first split: running both printed commands restores "
                      "the whole store",
                      run_restore(output) == 0
                      and store_contents(split2) == split2_original)

                # QA round 5 MAJOR (requirement B): a split deletion INSIDE .working.
                # counters.toml (BI = 7) is deleted one commit before the rest of the store,
                # so the newest tree holding both markers has a .working WITHOUT counters:
                # the remedy must never be labelled as restoring the store, must say the
                # restore is PARTIAL, and must name the missing counters path.
                # DISCRIMINATOR: the round-4 code printed one command under the remedy
                # "restore the store from that commit", exited 0 and left the spec 8.2
                # high-water deleted with no disclosure; it fails the complete text and the
                # phrase bans below.
                inside = make_git("ancestry-split-inside-counters")
                rc, output = init_store(inside)
                check("ancestry counters-first inside-split fixture first init succeeds",
                      rc == EXIT_OK)
                git_call(inside, ["--literal-pathspecs", "add", "-A"])
                git_call(inside, ["-c", "user.email=t@t", "-c", "user.name=t",
                                  "commit", "-m", "store"])
                in_counters = inside / working / machine_name / _opf_check.COUNTERS_NAME
                in_seeded = in_counters.read_text(encoding="utf-8").replace("BI = 0", "BI = 7")
                if "BI = 7" not in in_seeded:
                    raise OSError("inside-split fixture: counters.toml carried no BI = 0")
                in_counters.write_text(in_seeded, encoding="utf-8")
                git_call(inside, ["--literal-pathspecs", "add", "-A"])
                git_call(inside, ["-c", "user.email=t@t", "-c", "user.name=t",
                                  "commit", "-m", "BI high-water 7"])
                in_missing = working + "/" + machine_name + "/" + _opf_check.COUNTERS_NAME
                git_call(inside, ["rm", "-q", "--", in_missing])
                git_call(inside, ["-c", "user.email=t@t", "-c", "user.name=t",
                                  "commit", "-m", "drop counters inside working"])
                in_partial = head_of(inside)
                git_call(inside, ["rm", "-r", "-q", "--", working, _opf_store.POINTER_REL])
                git_call(inside, ["-c", "user.email=t@t", "-c", "user.name=t",
                                  "commit", "-m", "drop the rest"])
                rc, output = run(inside)
                check("ancestry counters-first inside-split: the complete refusal labels the "
                      "restore PARTIAL and names the missing counters path",
                      rc == EXIT_ERROR and output == prior_store_output(
                          inside, inside, in_partial, [".opf.toml", ".working"], True,
                          gaps=[in_missing]))
                check("ancestry counters-first inside-split: no remedy is labelled as "
                      "restoring the store",
                      "restore the store from" not in output
                      and "restores the rest" not in output
                      and "opf init: the restore is PARTIAL" in output
                      and "missing store path: " + in_missing in output)
                check("ancestry counters-first inside-split: the printed command runs clean "
                      "yet provably does not bring the counters back, as disclosed",
                      run_restore(output) == 0 and not in_counters.exists()
                      and (inside / _opf_store.POINTER_REL).is_file())

                # The same split INSIDE .working at the MANIFEST, under a three-commit outer
                # split, pinning the holder-is-the-hit arm (QA round 5 MEDIUM 3, mutant
                # always-parent): manifest.toml deleted first, .working second, .opf.toml
                # third. The probe for .working must name the manifest-less tree itself (the
                # newest first-parent tree that holds it), never that commit's parent, and
                # the remedy must say the restore is PARTIAL naming the manifest.
                # DISCRIMINATORS: mutant always-parent (holder taken from the hit's parent
                # even when the hit's own tree holds the marker) names the full-store commit
                # and fails the complete text; a scan without working_gaps presents the
                # manifest-less .working with no PARTIAL disclosure and fails it too.
                minside = make_git("ancestry-split-inside-manifest")
                rc, output = init_store(minside)
                check("ancestry manifest-first inside-split fixture first init succeeds",
                      rc == EXIT_OK)
                git_call(minside, ["--literal-pathspecs", "add", "-A"])
                git_call(minside, ["-c", "user.email=t@t", "-c", "user.name=t",
                                   "commit", "-m", "store"])
                m_missing = working + "/" + machine_name + "/" + _opf_store.MANIFEST_NAME
                git_call(minside, ["rm", "-q", "--", m_missing])
                git_call(minside, ["-c", "user.email=t@t", "-c", "user.name=t",
                                   "commit", "-m", "drop manifest inside working"])
                m_partial = head_of(minside)
                git_call(minside, ["rm", "-r", "-q", "--", working])
                git_call(minside, ["-c", "user.email=t@t", "-c", "user.name=t",
                                   "commit", "-m", "drop working"])
                m_ptr = head_of(minside)
                git_call(minside, ["rm", "-q", "--", _opf_store.POINTER_REL])
                git_call(minside, ["-c", "user.email=t@t", "-c", "user.name=t",
                                   "commit", "-m", "drop pointer"])
                rc, output = run(minside)
                check("ancestry manifest-first inside-split: the additional command names "
                      "the manifest-less tree itself and the refusal says PARTIAL naming "
                      "the manifest",
                      rc == EXIT_ERROR and output == prior_store_output(
                          minside, minside, m_ptr, [".opf.toml"], True,
                          extra=(m_partial, [".working"]), gaps=[m_missing]))
                check("ancestry manifest-first inside-split: both printed commands run "
                      "clean, restore the counters, and provably not the manifest, as "
                      "disclosed",
                      run_restore(output) == 0
                      and (minside / working / machine_name
                           / _opf_check.COUNTERS_NAME).is_file()
                      and not (minside / m_missing).exists())

                # QA round 5 MEDIUM 3 (mutants no-withheld-extra and withheld-commit-
                # singular): a NON-printable hostile name combined with an outer split
                # deletion, so the withheld form must carry the ADDITIONAL commit and path
                # detail lines and the plural "commits" remedy. The name also holds both
                # quote kinds, $(...), a backquote, ";" and "#" (requirement A), and the
                # complete refusal output and the successful first-init output both go
                # through sh_inert_probe. DISCRIMINATORS: dropping the withheld extra block
                # loses the two additional detail lines; hardcoding the singular "commit"
                # fails the plural remedy; both fail the complete text.
                wname = "w'$(touch PWNED)" + '"' + "`;#" + "\x07"
                wrepo = make_git("ancestry-withheld-split")
                wsub = wrepo / wname
                wsub.mkdir()
                rc, wfirst = init_store(wsub)
                check("ancestry withheld-split fixture first init succeeds", rc == EXIT_OK)
                git_call(wrepo, ["--literal-pathspecs", "add", "-A"])
                git_call(wrepo, ["-c", "user.email=t@t", "-c", "user.name=t",
                                 "commit", "-m", "store"])
                wfull = head_of(wrepo)
                git_call(wrepo, ["--literal-pathspecs", "rm", "-q", "--",
                                 wname + "/" + _opf_store.POINTER_REL])
                git_call(wrepo, ["-c", "user.email=t@t", "-c", "user.name=t",
                                 "commit", "-m", "drop pointer first"])
                wmid = head_of(wrepo)
                git_call(wrepo, ["--literal-pathspecs", "rm", "-r", "-q", "--",
                                 wname + "/" + working])
                git_call(wrepo, ["-c", "user.email=t@t", "-c", "user.name=t",
                                 "commit", "-m", "drop working second"])
                rc, output = run(wsub)
                check("ancestry withheld-split: the complete withheld refusal carries the "
                      "additional commit and path lines and the plural commits remedy",
                      rc == EXIT_ERROR and output == prior_store_output(
                          wsub, wrepo, wmid, [".working"], False,
                          extra=(wfull, [".opf.toml"])))
                check("ancestry withheld-split: the additional lines name the holder and "
                      "the plural remedy names the commits",
                      "opf init:   additional commit: " + wfull in output
                      and "opf init:   additional restore path: " in output
                      and "from the commits named below" in output)
                check("ancestry withheld-split: no raw non-printable character, no command "
                      "line, no substitution character anywhere in the output",
                      raw_free(output)
                      and not any(line.startswith("  git")
                                  for line in output.splitlines())
                      and "$" not in output and "`" not in output)
                check("ancestry withheld-split: refusal and first-init outputs are "
                      "shell-inert through sh, whole and line by line, and no payload ran",
                      sh_inert_probe(output, "withheld-split-refusal") == []
                      and sh_inert_probe(wfirst, "withheld-split-success") == []
                      and not (wrepo / "PWNED").exists()
                      and not (wsub / "PWNED").exists())

                # QA round 5 MAJOR (requirement A, the orchestrator reproduction): a
                # PRINTABLE name holding BOTH quote kinds plus $(...), a backquote payload,
                # ";" and "#". ascii() rendered such a name as a single-quoted Python literal
                # whose embedded backslash-quote ended the shell's quote early, so pasting
                # the REFUSED line itself executed the embedded payload. QA round 6: the
                # name renders CHANGED through the inert escaper, so the restore command
                # and the success staging and render hints are all withheld; every line of
                # the refusal and of the successful first init goes through the three-leg
                # sh probe. DISCRIMINATOR: an ascii()-interpolated REFUSED line or a raw
                # JSON root creates PWNED; the round-6 shlex-quoting code prints commands
                # here and fails both complete texts.
                bq_name = "b'$(touch PWNED)" + '"' + "`touch PWNED`;#"
                (bq_repo, bq_sub, bq_commit, bq_held, first_rc, bq_first,
                 rc, output) = sub_root_refusal("ancestry-both-quotes", bq_name)
                check("ancestry both-quotes name: fixture first init succeeds",
                      first_rc == EXIT_OK)
                check("ancestry both-quotes name: refused with the complete text, the "
                      "command withheld under the render-unchanged gate",
                      rc == EXIT_ERROR and output == prior_store_output(
                          bq_sub, bq_repo, bq_commit, [".opf.toml", ".working"], False))
                check("ancestry both-quotes name: refusal and first-init outputs are "
                      "shell-inert through sh, whole and line by line, and no payload ran",
                      sh_inert_probe(output, "both-quotes-refusal") == []
                      and sh_inert_probe(bq_first, "both-quotes-success") == []
                      and not (bq_repo / "PWNED").exists()
                      and not (bq_sub / "PWNED").exists())
                check("ancestry both-quotes name: no command line is printed anywhere "
                      "and the success output withholds the staging and render hints",
                      run_restore(output) is None
                      and not any(line.startswith("  git")
                                  for line in output.splitlines())
                      and "command hints withheld" in bq_first
                      and "opf render --write --root" not in bq_first)
                bq_rows = [json.loads(line) for line in bq_first.splitlines()
                           if line.startswith("{")]
                check("ancestry both-quotes name: the created-event JSON carries the root "
                      "inert-escaped, never raw",
                      any(row.get("event") == "created"
                          and row.get("root") == inert(str(bq_sub))
                          for row in bq_rows)
                      and all("$(" not in json.dumps(row) and "`" not in json.dumps(row)
                              for row in bq_rows))

                # The cannot-evaluate arm under the same hostile-name class (requirement A):
                # a grafts entry in a repository whose own name holds both quote kinds and
                # the payload, so the printed exception text interpolates the hostile grafts
                # path. The exception text is prose-inert-escaped at the print; the complete
                # output must carry no raw "$" or "`" and be shell-inert.
                ce_repo = make_git("ancestry-ce'$(touch PWNED)" + '"' + "`;#")
                (ce_repo / "seed.txt").write_bytes(b"base\n")
                git_call(ce_repo, ["--literal-pathspecs", "add", "-A"])
                git_call(ce_repo, ["-c", "user.email=t@t", "-c", "user.name=t",
                                   "commit", "-m", "base"])
                ce_grafts = ce_repo / ".git" / "info" / "grafts"
                ce_grafts.parent.mkdir(exist_ok=True)
                ce_grafts.write_bytes(b"x\n")
                rc, output = run(ce_repo)
                check("ancestry both-quotes cannot-evaluate: the complete grafts refusal "
                      "text with the hostile path inert-escaped",
                      rc == EXIT_ERROR and output == cannot_evaluate_output(
                          ce_repo, grafts_message("a regular file of 2 byte(s)",
                                                  str(ce_grafts))))
                check("ancestry both-quotes cannot-evaluate: no raw substitution character "
                      "and shell-inert through sh, whole and line by line",
                      "$" not in output and "`" not in output
                      and sh_inert_probe(output, "both-quotes-cannot-evaluate") == []
                      and not (ce_repo / "PWNED").exists())

                # QA round 5 MEDIUM 3 (mutant no-parentless-arm): the split probe's hit is a
                # PARENTLESS commit whose tree lacks a real pointer (it committed a DIRECTORY
                # at the pointer path), so the arm must conclude no pointer ever existed and
                # print the single .working command with no additional disclosure.
                # DISCRIMINATOR: without the parentless continue, the arm asks for the root
                # commit's first parent, gets an empty object id, and degrades the refusal
                # to a cannot-evaluate; the complete-text check fails.
                pl = make_git("ancestry-parentless-pointer-dir")
                pl_sub = pl / "sub"
                pl_fake = pl_sub / _opf_store.POINTER_REL
                pl_fake.mkdir(parents=True)
                (pl_fake / "x").write_bytes(b"not a pointer\n")
                (pl_sub / "seed.txt").write_bytes(b"keeps the sub-root non-empty\n")
                git_call(pl, ["--literal-pathspecs", "add", "-A"])
                git_call(pl, ["-c", "user.email=t@t", "-c", "user.name=t",
                              "commit", "-m", "parentless pointer-path directory"])
                pl_machine = pl_sub / working / machine_name
                pl_machine.mkdir(parents=True)
                (pl_machine / _opf_store.MANIFEST_NAME).write_bytes(b"x = 1\n")
                git_call(pl, ["--literal-pathspecs", "add", "-A"])
                git_call(pl, ["-c", "user.email=t@t", "-c", "user.name=t",
                              "commit", "-m", "manifest beside the directory"])
                pl_holder = head_of(pl)
                git_call(pl, ["rm", "-r", "-q", "--", "sub/" + working,
                              "sub/" + _opf_store.POINTER_REL])
                git_call(pl, ["-c", "user.email=t@t", "-c", "user.name=t",
                              "commit", "-m", "drop both"])
                rc, output = run(pl_sub)
                check("ancestry parentless pointer-path directory: refused with the single "
                      ".working command, no additional disclosure, PARTIAL for counters",
                      rc == EXIT_ERROR and output == prior_store_output(
                          pl_sub, pl, pl_holder, [".working"], True,
                          gaps=[working + "/" + machine_name + "/"
                                + _opf_check.COUNTERS_NAME]))
                check("ancestry parentless pointer-path directory: the refusal stays the "
                      "definite prior-store finding",
                      "prior store exists" in output and "cannot evaluate" not in output
                      and "additional" not in output)

                # QA round 5 MEDIUM 3 (mutant no-nonstore-holder-arm): the split probe's hit
                # is a DELETION whose first parent's tree holds only a committed DIRECTORY at
                # the pointer path (a non-store change), so the arm must conclude there is no
                # restorable pointer and print the single .working command.
                # DISCRIMINATOR: without the non-store-holder continue, the probe prints a
                # bogus additional command restoring the directory as if it were the pointer;
                # the complete-text check fails.
                ns = make_git("ancestry-nonstore-holder")
                ns_sub = ns / "sub"
                ns_fake = ns_sub / _opf_store.POINTER_REL
                ns_fake.mkdir(parents=True)
                (ns_fake / "x").write_bytes(b"not a pointer\n")
                (ns_sub / "seed.txt").write_bytes(b"keeps the sub-root non-empty\n")
                git_call(ns, ["--literal-pathspecs", "add", "-A"])
                git_call(ns, ["-c", "user.email=t@t", "-c", "user.name=t",
                              "commit", "-m", "pointer-path directory"])
                git_call(ns, ["rm", "-r", "-q", "--", "sub/" + _opf_store.POINTER_REL])
                git_call(ns, ["-c", "user.email=t@t", "-c", "user.name=t",
                              "commit", "-m", "drop the directory"])
                ns_machine = ns_sub / working / machine_name
                ns_machine.mkdir(parents=True)
                (ns_machine / _opf_store.MANIFEST_NAME).write_bytes(b"x = 1\n")
                git_call(ns, ["--literal-pathspecs", "add", "-A"])
                git_call(ns, ["-c", "user.email=t@t", "-c", "user.name=t",
                              "commit", "-m", "manifest after the directory era"])
                ns_holder = head_of(ns)
                git_call(ns, ["rm", "-r", "-q", "--", "sub/" + working])
                git_call(ns, ["-c", "user.email=t@t", "-c", "user.name=t",
                              "commit", "-m", "drop working"])
                rc, output = run(ns_sub)
                check("ancestry non-store holder: refused with the single .working command "
                      "and no bogus additional pointer command",
                      rc == EXIT_ERROR and output == prior_store_output(
                          ns_sub, ns, ns_holder, [".working"], True,
                          gaps=[working + "/" + machine_name + "/"
                                + _opf_check.COUNTERS_NAME]))
                check("ancestry non-store holder: no additional command or path is disclosed",
                      "additional" not in output)

                # QA round 6 MAJOR (argument diagnostics): the {!r} rendering of a
                # rejected argument is a quoted Python literal in which $(...) stays live
                # when the printed line reaches a POSIX shell; both early-return
                # diagnostics now render the value through the inert escaper, and both
                # outputs go through the three-leg sh probe. DISCRIMINATOR: the round-6
                # {!r} code prints the raw payload and the probe creates PWNED.
                arg_payload = "x'$(touch PWNED)" + '"' + "`touch PWNED`;#"
                rc, output = call(["init", arg_payload])
                check("argument error: unrecognized argument renders the value inert, "
                      "as the complete line",
                      rc == EXIT_ERROR and output ==
                      "opf init: unrecognized argument, inert-escaped: "
                      + inert(arg_payload) + "\n")
                check("argument error: unrecognized-argument output is shell-inert and "
                      "ran no payload",
                      sh_inert_probe(output, "arg-unrecognized") == []
                      and "$" not in output and "`" not in output)
                root_payload = "-'$(touch PWNED)" + '"' + "`touch PWNED`;#"
                rc, output = call(["init", "--root", root_payload])
                check("argument error: rejected --root value renders inert, as the "
                      "complete line",
                      rc == EXIT_ERROR and output ==
                      "opf init: --root requires a non-empty directory argument, not "
                      "the inert-escaped value " + inert(root_payload)
                      + " (an empty value shows as nothing here)\n")
                check("argument error: rejected --root output is shell-inert and ran "
                      "no payload",
                      sh_inert_probe(output, "arg-root") == []
                      and "$" not in output and "`" not in output)
                check("argument error: no payload file appeared beside the fixtures",
                      not (base / "PWNED").exists())

                # QA round 6 MAJOR (newline-holding root): a root whose name holds
                # newlines, both quote kinds and $(...) payloads. The repository preflight
                # cannot confirm such a worktree, so init refuses fail-closed BEFORE any
                # command hint could be built, and the refusal renders the name inert:
                # no physical output line carries a payload fragment, and the three-leg
                # probe (including sh -i, where quote state crosses lines) creates
                # nothing. DISCRIMINATOR: any raw interpolation of the root emits the
                # standalone "touch PWNED-NL" physical line, which leg 2 executes.
                nl_name = "nl'$(touch PWNED)\ntouch PWNED-NL\n" + '"' + "x`y"
                nl_repo = make_git(nl_name)
                before = _snapshot(nl_repo)
                rc, output = run(nl_repo)
                check("newline root: refused fail-closed as cannot-evaluate",
                      rc == EXIT_ERROR and "cannot evaluate" in output)
                # QA round 8 (requirement A): the alphabet is fixed and narrow
                # again, so quotes and spaces render as escapes too; what must never
                # happen is a payload fragment LANDING AT A LINE START (the newline
                # is escaped) or a raw substitution or separator character surviving
                # anywhere.
                check("newline root: no output line carries a payload fragment raw",
                      "$" not in output and "`" not in output
                      and not any(line.startswith("touch")
                                  for line in output.splitlines())
                      and all(ch.isprintable() for line in output.splitlines()
                              for ch in line))
                check("newline root: output is shell-inert through all three sh legs",
                      sh_inert_probe(output, "newline-root") == []
                      and not (base / "PWNED").exists()
                      and not (base / "PWNED-NL").exists())
                check("newline root: refusing init wrote nothing",
                      _snapshot(nl_repo) == before)

                # QA round 6 MAJOR (split deletion of BOTH structural files): one commit
                # deletes manifest.toml AND counters.toml inside .working, a later commit
                # deletes the rest of the store. The named tree still holds .working (the
                # other sources survive into it), so the round-6 working_gaps, which
                # examined only subdirectories still holding one structural file, saw no
                # gap: the printed command exited 0 with the spec 8.2 high-water counters
                # and the manifest still deleted and NO disclosure. Now every first-level
                # subdirectory of the restored tree requires both files, so the refusal
                # says PARTIAL and names both. DISCRIMINATOR: the round-6 code prints this
                # refusal with no PARTIAL block and fails the complete text.
                sb = make_git("ancestry-split-both-structural")
                rc, output = init_store(sb)
                check("ancestry split-both fixture first init succeeds", rc == EXIT_OK)
                git_call(sb, ["--literal-pathspecs", "add", "-A"])
                git_call(sb, ["-c", "user.email=t@t", "-c", "user.name=t",
                              "commit", "-m", "store"])
                sb_counters = sb / working / machine_name / _opf_check.COUNTERS_NAME
                sb_seeded = sb_counters.read_text(encoding="utf-8").replace(
                    "BI = 0", "BI = 7")
                if "BI = 7" not in sb_seeded:
                    raise OSError("split-both fixture: counters.toml carried no BI = 0")
                sb_counters.write_text(sb_seeded, encoding="utf-8")
                git_call(sb, ["--literal-pathspecs", "add", "-A"])
                git_call(sb, ["-c", "user.email=t@t", "-c", "user.name=t",
                              "commit", "-m", "BI high-water 7"])
                sb_manifest = working + "/" + machine_name + "/" + _opf_store.MANIFEST_NAME
                sb_missing = working + "/" + machine_name + "/" + _opf_check.COUNTERS_NAME
                git_call(sb, ["rm", "-q", "--", sb_manifest, sb_missing])
                git_call(sb, ["-c", "user.email=t@t", "-c", "user.name=t",
                              "commit", "-m", "drop both structural files"])
                sb_named = head_of(sb)
                git_call(sb, ["rm", "-r", "-q", "--", working, _opf_store.POINTER_REL])
                git_call(sb, ["-c", "user.email=t@t", "-c", "user.name=t",
                              "commit", "-m", "drop the rest"])
                rc, output = run(sb)
                check("ancestry split-both: the complete refusal says PARTIAL and names "
                      "both structural paths",
                      rc == EXIT_ERROR and output == prior_store_output(
                          sb, sb, sb_named, [".opf.toml", ".working"], True,
                          gaps=sorted([sb_missing, sb_manifest])))
                check("ancestry split-both: the printed command runs clean yet provably "
                      "brings back neither structural file, as disclosed",
                      run_restore(output) == 0
                      and (sb / _opf_store.POINTER_REL).is_file()
                      and not (sb / sb_missing).exists()
                      and not (sb / sb_manifest).exists())

                # QA round 6 MAJOR, second shape: a SECOND machine subdirectory deleted
                # WHOLE in an earlier commit. The restored tree holds no trace of it, so
                # only the first-parent history walk can expect its structural files; the
                # refusal must say PARTIAL and name both of them. DISCRIMINATOR: a
                # working_gaps without the history union sees a complete surviving
                # subdirectory and prints no PARTIAL block, failing the complete text.
                ws = make_git("ancestry-whole-subdir")
                rc, output = init_store(ws)
                check("ancestry whole-subdir fixture first init succeeds", rc == EXIT_OK)
                ws_zeta = ws / working / "zeta-machine"
                ws_zeta.mkdir()
                (ws_zeta / _opf_store.MANIFEST_NAME).write_bytes(b"x = 1\n")
                (ws_zeta / _opf_check.COUNTERS_NAME).write_bytes(b"y = 1\n")
                git_call(ws, ["--literal-pathspecs", "add", "-A"])
                git_call(ws, ["-c", "user.email=t@t", "-c", "user.name=t",
                              "commit", "-m", "store with second machine subdir"])
                git_call(ws, ["rm", "-r", "-q", "--", working + "/zeta-machine"])
                git_call(ws, ["-c", "user.email=t@t", "-c", "user.name=t",
                              "commit", "-m", "drop the second subdir whole"])
                ws_named = head_of(ws)
                git_call(ws, ["rm", "-r", "-q", "--", working, _opf_store.POINTER_REL])
                git_call(ws, ["-c", "user.email=t@t", "-c", "user.name=t",
                              "commit", "-m", "drop the rest"])
                rc, output = run(ws)
                ws_gaps = sorted([working + "/zeta-machine/" + _opf_check.COUNTERS_NAME,
                                  working + "/zeta-machine/" + _opf_store.MANIFEST_NAME])
                check("ancestry whole-subdir: the complete refusal says PARTIAL and "
                      "names the deleted subdirectory's structural paths",
                      rc == EXIT_ERROR and output == prior_store_output(
                          ws, ws, ws_named, [".opf.toml", ".working"], True,
                          gaps=ws_gaps))
                check("ancestry whole-subdir: the printed command runs clean yet the "
                      "second subdirectory stays absent, as disclosed",
                      run_restore(output) == 0 and not ws_zeta.exists()
                      and (ws / working / machine_name
                           / _opf_check.COUNTERS_NAME).is_file())

                # QA round 6 (the three inert-JSON report arms, each driven with hostile
                # values): removing the _init_inert_json wrapper from any of them left the
                # suite green, because no vector put a substitution payload into the
                # reported values. Each arm now gets one. DISCRIMINATOR: with the wrapper
                # removed the raw $(touch PWNED) sits inside the printed JSON double
                # quotes, where a POSIX shell keeps it live, and the probe creates PWNED.
                fc_repo = make_git("foreign'$(touch PWNED)" + '"' + "`;#x")
                (fc_repo / working).mkdir()
                (fc_repo / working / "junk").write_bytes(b"j\n")
                fc_before = _snapshot(fc_repo)
                rc, output = run(fc_repo)
                fc_rows = [json.loads(line) for line in output.splitlines()
                           if line.startswith("{")]
                check("foreign-content report at a hostile root: refused with the root "
                      "inert in the JSON event",
                      rc == EXIT_ERROR and inert("foreign .working content") in output
                      and any(row.get("event") == "foreign-content"
                              and row.get("root") == inert(str(fc_repo))
                              and {(entry["path"], entry["kind"])
                                   for entry in row.get("entries", [])}
                              == {(working + "/junk", "file")}
                              for row in fc_rows))
                check("foreign-content report at a hostile root: output is shell-inert "
                      "and ran no payload",
                      sh_inert_probe(output, "foreign-hostile") == []
                      and "$" not in output and "`" not in output
                      and not (fc_repo / "PWNED").exists()
                      and not (base / "PWNED").exists())
                check("foreign-content report at a hostile root: fixture preserved",
                      _snapshot(fc_repo) == fc_before)

                # The partial-publication and post-publish-inventory arms need a fault
                # DURING publication, which no filesystem precondition can arrange (the
                # destination checks run before publishing starts), so each is driven
                # in-process with one injected fault, against a hostile-named repository,
                # and the complete captured output goes through the same probes.
                def drive_cmd_init(root, patches):
                    # Captured at the descriptor level through fd_capture (QA round 8,
                    # codex MAJOR): a redirect_stdout capture missed descriptor-level
                    # output from a driven fault or mutant.
                    def driven():
                        with contextlib.ExitStack() as stack:
                            for attr, value in patches.items():
                                stack.enter_context(
                                    mock.patch.object(opf, attr, value))
                            return opf._cmd_init(["--root", str(root)])
                    return fd_capture(driven)

                # QA round 9, requirement C (codex MEDIUM 3): the
                # descriptor-level capture is PINNED, not merely present. A runner
                # that writes to descriptor 1 and descriptor 2 with os.write and
                # spawns a child process that inherits them must have ALL THREE
                # outputs in the returned capture; a reversion to the sys-level
                # redirect_stdout / redirect_stderr capture misses every one of
                # them and fails here. Each in-process driver (call,
                # drive_cmd_init, drive_module_init, and the drive of
                # _gen2_cli_vectors, which is handed this fd_capture) returns
                # fd_capture directly, pinned by the source count below, so this
                # potency holds for every driver's capture path; drive_cmd_init and
                # drive_module_init are additionally driven with raw-descriptor
                # probes of their own below.
                def fd_probe_runner():
                    os.write(1, b"QA-FD1-MARKER\n")
                    os.write(2, b"QA-FD2-MARKER\n")
                    subprocess.run(["/bin/echo", "QA-FDCHILD-MARKER"])
                    return 0

                rc, output = fd_capture(fd_probe_runner)
                check("capture potency: descriptor 1, descriptor 2 and an "
                      "inherited child process all land in the capture",
                      rc == 0 and "QA-FD1-MARKER" in output
                      and "QA-FD2-MARKER" in output
                      and "QA-FDCHILD-MARKER" in output)
                this_source = (Path(__file__).resolve()
                               .read_text(encoding="utf-8"))
                capture_needle = "return fd_" + "capture("
                check("capture: each of the four in-process drivers returns "
                      "fd_capture directly",
                      this_source.count(capture_needle) == 4)

                def fd_probe_scan(git_arg, repo_arg, root_arg):
                    os.write(1, b"QA-DRIVE-FD1\n")
                    os.write(2, b"QA-DRIVE-FD2\n")
                    subprocess.run(["/bin/echo", "QA-DRIVE-CHILD"])
                    raise RuntimeError("qa capture probe")

                cap_repo = make_git("capture-potency")
                rc, output = drive_cmd_init(
                    cap_repo, dict(_init_no_prior_store=fd_probe_scan))
                check("capture potency through drive_cmd_init: raw descriptor "
                      "and inherited child output are captured",
                      rc == EXIT_ERROR and "QA-DRIVE-FD1" in output
                      and "QA-DRIVE-FD2" in output
                      and "QA-DRIVE-CHILD" in output
                      and inert("qa capture probe") in output)

                # The fresh changelog payload is UNCONDITIONAL: a CHANGELOG.md that appears after
                # the deliverable refusal (planted here while the payloads are built, a stand-in
                # for a concurrent writer) is refused by the destination check, never silently
                # left out of the publication. DISCRIMINATOR: a payload built only when the file
                # is absent skips it and publishes the store with no fresh changelog (rc 0).
                late_repo = make_git("deliverable-late-changelog")
                real_emit = opf._opf_emit

                class _LateChangelogEmit:
                    @staticmethod
                    def emit_checked(document):
                        (late_repo / "CHANGELOG.md").write_bytes(b"late\n")
                        return real_emit.emit_checked(document)

                rc, output = drive_cmd_init(late_repo, dict(_opf_emit=_LateChangelogEmit))
                check("a CHANGELOG.md appearing after the refusal check is refused, never "
                      "skipped",
                      rc == EXIT_ERROR and inert("destination already exists") in output
                      and "CHANGELOG.md" in output
                      and "preflight refused; no publication attempted." in output)
                check("that late CHANGELOG.md is the only new entry; nothing was published",
                      sorted(entry.name for entry in late_repo.iterdir())
                      == [".git", "CHANGELOG.md"]
                      and (late_repo / "CHANGELOG.md").read_bytes() == b"late\n")

                # Spec 5.7: every source create runs with .working/toml/lease.toml and its
                # control-side active record present, both naming the plain init operation; after
                # success neither remains and the anchor is free again, which this in-process
                # driver can see (a child process frees it on exit). DISCRIMINATOR: the pre-lease
                # init creates every source with no lease.
                lease_repo = make_git("lease-during-publication")
                lease_real_create = opf._init_create
                lease_seen = []

                def lease_recording_create(root_fd, relpath, data):
                    # Both records are canonical emitter output, so the top-level operation key is
                    # matched as its exact line (no TOML parse in the harness).
                    lease_path = lease_repo / working / machine_name / "lease.toml"
                    active_path = lease_repo / ".git" / "opf-oplock" / "active.toml"
                    operation_line = 'operation = "opf-init-plain"'
                    lease_seen.append(
                        lease_path.is_file() and active_path.is_file()
                        and operation_line in lease_path.read_text(encoding="utf-8").splitlines()
                        and operation_line in active_path.read_text(encoding="utf-8").splitlines())
                    return lease_real_create(root_fd, relpath, data)

                rc, output = drive_cmd_init(lease_repo, dict(_init_create=lease_recording_create))
                check("lease: lease.toml and its active record are held at every source create",
                      rc == EXIT_OK and len(lease_seen) == len(expected_paths) and all(lease_seen))
                check("lease: nothing is held after success (no lease, no active record, a free "
                      "anchor)",
                      valid_sources(lease_repo)
                      and not (lease_repo / ".git" / "opf-oplock" / "active.toml").exists()
                      and anchor_free(lease_repo))

                # Under the mutex the absence checks run again in the preflight's words. A file a
                # cooperating writer lands between the preflight and the acquisition (planted here
                # from the preflight's history scan, its last read-only step) is refused with the
                # deliverable wording; VERSION is no payload, so only the explicit re-read catches
                # it. The refusal releases the mutex. DISCRIMINATOR: the pre-lease init publishes
                # beside a planted VERSION (rc 0) and reports a planted CHANGELOG.md only as a
                # partial publication; without the deliverable re-read under the mutex, VERSION
                # publishes and CHANGELOG.md is refused only in the generic destination wording.
                real_scan = opf._init_no_prior_store
                for plant_name, plant_bytes in (("VERSION", b"2.4.0\n"),
                                                ("CHANGELOG.md", b"late\n")):
                    plant_repo = make_git("lease-recheck-" + plant_name.lower().replace(".", "-"))

                    def planting_scan(git_arg, repo_arg, root_arg, plant_repo=plant_repo,
                                      plant_name=plant_name, plant_bytes=plant_bytes):
                        result = real_scan(git_arg, repo_arg, root_arg)
                        if not (plant_repo / plant_name).exists():
                            (plant_repo / plant_name).write_bytes(plant_bytes)
                        return result

                    rc, output = drive_cmd_init(plant_repo,
                                                dict(_init_no_prior_store=planting_scan))
                    check("lease: a " + plant_name + " landing before the mutex is refused under it",
                          rc == EXIT_ERROR
                          and "during re-checking-the-destinations-under-the-mutex: RuntimeError("
                          in output
                          and inert("an existing " + plant_name + " sits at") in output
                          and "preflight refused; no publication attempted." in output)
                    check("lease: that " + plant_name + " refusal published nothing and left "
                          "nothing held",
                          sorted(entry.name for entry in plant_repo.iterdir())
                          == [".git", plant_name]
                          and not (plant_repo / ".git" / "opf-oplock" / "active.toml").exists()
                          and anchor_free(plant_repo))

                # The OTHER re-checks under the mutex, each landed in the same window (planted
                # from the preflight's history scan, its last read-only step) and each refused
                # under the mutex with nothing published and nothing held. DISCRIMINATOR for
                # each: without its own re-check the failure is no clean refusal, so the pinned
                # "preflight refused" sentence and the untouched-root assertion both break: a
                # landed payload destination fails only mid-publication in the
                # partial-publication wording, a landed local pointer publishes and fails the
                # post-publish observation, a destination turned tracked fails only at the
                # post-publish index read, and an ignored destination publishes and exits 0.
                dest_repo = make_git("lease-recheck-destination")

                def dest_planting_scan(git_arg, repo_arg, root_arg):
                    result = real_scan(git_arg, repo_arg, root_arg)
                    if not (dest_repo / _opf_store.POINTER_REL).exists():
                        (dest_repo / _opf_store.POINTER_REL).write_bytes(b"junk\n")
                    return result

                rc, output = drive_cmd_init(dest_repo,
                                            dict(_init_no_prior_store=dest_planting_scan))
                check("lease: a planned destination landing before the mutex is refused by the "
                      "destination re-check under it",
                      rc == EXIT_ERROR
                      and "during re-checking-the-destinations-under-the-mutex: RuntimeError("
                      in output
                      and inert("destination already exists") in output
                      and _opf_store.POINTER_REL in output
                      and "preflight refused; no publication attempted." in output)
                check("lease: that destination refusal published nothing and left nothing held",
                      sorted(entry.name for entry in dest_repo.iterdir())
                      == sorted([".git", _opf_store.POINTER_REL])
                      and (dest_repo / _opf_store.POINTER_REL).read_bytes() == b"junk\n"
                      and not (dest_repo / ".git" / "opf-oplock" / "active.toml").exists()
                      and anchor_free(dest_repo))

                lp_repo = make_git("lease-recheck-local-pointer")

                def lp_planting_scan(git_arg, repo_arg, root_arg):
                    result = real_scan(git_arg, repo_arg, root_arg)
                    if not (lp_repo / _opf_store.LOCAL_POINTER_REL).exists():
                        (lp_repo / _opf_store.LOCAL_POINTER_REL).write_bytes(b"junk\n")
                    return result

                rc, output = drive_cmd_init(lp_repo,
                                            dict(_init_no_prior_store=lp_planting_scan))
                check("lease: a local pointer landing before the mutex is refused by the "
                      "re-check under it",
                      rc == EXIT_ERROR
                      and "during re-checking-the-destinations-under-the-mutex: RuntimeError("
                      in output
                      and inert("existing pointer: "
                                + _opf_store.LOCAL_POINTER_REL) in output
                      and "preflight refused; no publication attempted." in output)
                check("lease: that local-pointer refusal published nothing and left nothing "
                      "held",
                      sorted(entry.name for entry in lp_repo.iterdir())
                      == sorted([".git", _opf_store.LOCAL_POINTER_REL])
                      and (lp_repo / _opf_store.LOCAL_POINTER_REL).read_bytes() == b"junk\n"
                      and not (lp_repo / ".git" / "opf-oplock" / "active.toml").exists()
                      and anchor_free(lp_repo))

                tr_repo = make_git("lease-recheck-tracked")
                tr_calls = []

                def tracking_scan(git_arg, repo_arg, root_arg):
                    tr_calls.append(root_arg)
                    result = real_scan(git_arg, repo_arg, root_arg)
                    if len(tr_calls) == 1:
                        # Tracked but absent from the working tree, so only the index re-read
                        # under the mutex can see it (the lstat re-checks find nothing).
                        (tr_repo / "CHANGELOG.md").write_bytes(b"# tracked\n")
                        git_call(tr_repo, ["add", "--", "CHANGELOG.md"])
                        (tr_repo / "CHANGELOG.md").unlink()
                    return result

                rc, output = drive_cmd_init(tr_repo, dict(_init_no_prior_store=tracking_scan))
                check("lease: a destination turning git-tracked before the mutex is refused by "
                      "the index re-check under it",
                      rc == EXIT_ERROR and len(tr_calls) == 1
                      and "during re-checking-the-destinations-under-the-mutex: RuntimeError("
                      in output
                      and inert("planned destination already git-tracked") in output
                      and "preflight refused; no publication attempted." in output)
                check("lease: that index refusal published nothing and left nothing held",
                      sorted(entry.name for entry in tr_repo.iterdir()) == [".git"]
                      and not (tr_repo / ".git" / "opf-oplock" / "active.toml").exists()
                      and anchor_free(tr_repo))

                ig_repo = make_git("lease-recheck-ignored")

                def ignoring_scan(git_arg, repo_arg, root_arg):
                    result = real_scan(git_arg, repo_arg, root_arg)
                    if not (ig_repo / ".gitignore").exists():
                        (ig_repo / ".gitignore").write_bytes(b".working\n")
                    return result

                rc, output = drive_cmd_init(ig_repo, dict(_init_no_prior_store=ignoring_scan))
                check("lease: a destination turning git-ignored before the mutex is refused by "
                      "the ignore re-check under it",
                      rc == EXIT_ERROR
                      and "during re-checking-the-destinations-under-the-mutex: RuntimeError("
                      in output
                      and inert("planned destination is git-ignored") in output
                      and "preflight refused; no publication attempted." in output)
                check("lease: that ignore refusal published nothing and left nothing held",
                      sorted(entry.name for entry in ig_repo.iterdir())
                      == [".git", ".gitignore"]
                      and (ig_repo / ".gitignore").read_bytes() == b".working\n"
                      and not (ig_repo / ".git" / "opf-oplock" / "active.toml").exists()
                      and anchor_free(ig_repo))

                # The history scan runs again under the mutex: a store committed and then deleted
                # between the preflight scan and the acquisition (committed here from the first
                # scan, a stand-in for a concurrent writer) is refused as a prior store, nothing is
                # published, and the mutex is released. DISCRIMINATOR: without the re-scan, init
                # zero-seeds a fresh store over that history (rc 0), which spec 8.2 forbids.
                race_repo = make_git("lease-recheck-history")
                race_calls = []

                def committing_scan(git_arg, repo_arg, root_arg):
                    race_calls.append(root_arg)
                    result = real_scan(git_arg, repo_arg, root_arg)
                    if len(race_calls) == 1:
                        (race_repo / working / machine_name).mkdir(parents=True)
                        (race_repo / working / machine_name
                         / _opf_store.MANIFEST_NAME).write_bytes(b"[opf]\n")
                        (race_repo / _opf_store.POINTER_REL).write_bytes(
                            b'[store]\ntarget = "dir:."\n')
                        git_call(race_repo, ["add", "-A"])
                        git_call(race_repo, ["-c", "user.email=t@t", "-c", "user.name=t",
                                             "commit", "-q", "-m", "a store"])
                        git_call(race_repo, ["rm", "-r", "-q", "--", working,
                                             _opf_store.POINTER_REL])
                        git_call(race_repo, ["-c", "user.email=t@t", "-c", "user.name=t",
                                             "commit", "-q", "-m", "drop the store"])
                    return result

                rc, output = drive_cmd_init(race_repo, dict(_init_no_prior_store=committing_scan))
                check("lease: a prior store committed before the mutex is refused by the re-scan "
                      "under it",
                      rc == EXIT_ERROR and len(race_calls) == 2
                      and "during checking-git-history-for-a-prior-store" in output
                      and "prior store" in output
                      and "preflight refused; no publication attempted." in output)
                check("lease: the re-scan refusal published nothing and left nothing held",
                      sorted(entry.name for entry in race_repo.iterdir()) == [".git"]
                      and not (race_repo / ".git" / "opf-oplock" / "active.toml").exists()
                      and anchor_free(race_repo))

                # A release failure is reported beside the failure being unwound, on its own
                # rendered line, never instead of it; after a complete publication a failed final
                # release is a failure, never exit 0. The injected release closes the holder for
                # real first, so nothing stays held. DISCRIMINATOR: the pre-lease init releases
                # nothing (no unwind line, and the clean root exits 0); an unwind that swallowed
                # the error drops the line, and one that raised it would replace the refusal.
                real_release = _opf_oplock.release_init_holder

                def failing_release(holder):
                    real_release(holder)
                    raise _opf_oplock.OpLockError("injected release failure")

                unwind_repo = make_git("lease-unwind-release-failure")
                with mock.patch.object(_opf_oplock, "release_init_holder", failing_release):
                    rc, output = drive_cmd_init(unwind_repo, dict(
                        _init_no_prior_store=lambda git_arg, repo_arg, root_arg: (
                            (unwind_repo / "VERSION").write_bytes(b"2.4.0\n")
                            if not (unwind_repo / "VERSION").exists() else None)))
                check("lease: a release failure while a refusal unwinds is reported beside it",
                      rc == EXIT_ERROR
                      and "opf init: releasing the operation lock failed while this failure "
                      "unwound: " + inert("injected release failure") in output
                      and inert("an existing VERSION sits at") in output
                      and "preflight refused; no publication attempted." in output
                      and anchor_free(unwind_repo))
                final_repo = make_git("lease-final-release-failure")
                with mock.patch.object(_opf_oplock, "release_init_holder", failing_release):
                    rc, output = drive_cmd_init(final_repo, {})
                check("lease: a failed final release after publication exits 2, never 0",
                      rc == EXIT_ERROR
                      and "during releasing-the-init-operation-mutex: OpLockError(" in output
                      and inert("injected release failure") in output
                      and "publication may be partial" in output
                      and anchor_free(final_repo))

                pp_repo = make_git("partial'$(touch PWNED)" + '"' + "`;#")

                def fail_create(root_fd, relpath, data):
                    raise RuntimeError("driven create failure '$(touch PWNED)`;#")

                rc, output = drive_cmd_init(pp_repo, {"_init_create": fail_create})
                pp_rows = [json.loads(line) for line in output.splitlines()
                           if line.startswith("{")]
                check("partial-publication report at a hostile root: the event carries "
                      "the root and the planned paths inert",
                      rc == EXIT_ERROR
                      and inert("driven create failure") in output
                      and "publication may be partial" in output
                      and any(row.get("event") == "partial-publication"
                              and row.get("root") == inert(str(pp_repo))
                              and row.get("root_binding") == "same-root"
                              and any(entry.get("path") == _opf_store.POINTER_REL
                                      for entry in row.get("paths", []))
                              for row in pp_rows))
                check("partial-publication report at a hostile root: output is "
                      "shell-inert and ran no payload",
                      sh_inert_probe(output, "partial-publication") == []
                      and "$" not in output and "`" not in output
                      and not (pp_repo / "PWNED").exists()
                      and not (base / "PWNED").exists())

                # Spec 5.7 unwind: a create failing mid-publication, under the attached lease,
                # must leave no capability state behind: the unwind releases the lease
                # capability, so lease.toml and the active record are gone and the anchor is
                # free for the next writer. The failure is injected on the THIRD create, so the
                # lease is demonstrably attached and in use when it strikes. DISCRIMINATOR:
                # without the capability unwind the spent holder releases nothing, both records
                # survive the failure and the anchor stays held, so every lock assertion below
                # breaks.
                uw_repo = make_git("lease-unwind-after-create-failure")
                uw_real_create = opf._init_create
                uw_calls = []

                def uw_fail_third_create(root_fd, relpath, data):
                    uw_calls.append(relpath)
                    if len(uw_calls) >= 3:
                        raise RuntimeError("driven create failure under the lease")
                    return uw_real_create(root_fd, relpath, data)

                rc, output = drive_cmd_init(uw_repo, {"_init_create": uw_fail_third_create})
                check("lease: a failed mid-publication create unwinds the lease and its active "
                      "record and frees the anchor",
                      rc == EXIT_ERROR
                      and inert("driven create failure under the lease") in output
                      and "publication may be partial" in output
                      and len(uw_calls) >= 3
                      and not (uw_repo / working / machine_name / "lease.toml").exists()
                      and not (uw_repo / ".git" / "opf-oplock" / "active.toml").exists()
                      and anchor_free(uw_repo))

                # Spec 5.7 unwind after the detach: once the lease is detached, the capability is
                # spent and the mutex is held through the holder the detach returned, so a
                # failure in the final observation must release THAT holder. The failure is
                # injected on the first published-path observation (the disclosure's own
                # observation afterwards runs for real), and the stand-in records that the lease
                # and its active record were already gone when it struck, so the failure is
                # demonstrably after the detach. DISCRIMINATOR: an unwind that releases only the
                # original (spent) holder leaves the detached holder's flock held in this process,
                # so the later acquisition below is refused.
                dt_repo = make_git("lease-unwind-after-detach")
                dt_real_observed = opf._init_observed
                dt_seen = []

                def dt_fail_first_observed(root_fd, directories, payloads):
                    if not dt_seen:
                        dt_seen.append((
                            (dt_repo / working / machine_name / "lease.toml").exists(),
                            (dt_repo / ".git" / "opf-oplock" / "active.toml").exists()))
                        raise RuntimeError("driven observation failure after the detach")
                    return dt_real_observed(root_fd, directories, payloads)

                rc, output = drive_cmd_init(dt_repo, {"_init_observed": dt_fail_first_observed})
                check("lease: a failure after the lease detach releases the detached holder, "
                      "leaving no lease or active record and the anchor free",
                      rc == EXIT_ERROR
                      and inert("driven observation failure after the detach") in output
                      and "during observing-published-source-paths" in output
                      and "publication may be partial" in output
                      and dt_seen == [(False, False)]
                      and not (dt_repo / working / machine_name / "lease.toml").exists()
                      and not (dt_repo / ".git" / "opf-oplock" / "active.toml").exists()
                      and anchor_free(dt_repo))

                # QA round 12 C (claude round-11 MEDIUM 2): a non-Exception raised
                # after publication started (an interrupt or SystemExit) must still
                # produce the partial-publication disclosure, then exit 2. On the
                # round-11 handler (except Exception) the interrupt escaped _init_main
                # to _cmd_init's boundary, which printed only a "cannot evaluate" line
                # and dropped the disclosure; the round-12 handler (except
                # BaseException) reports it here before returning rendered. Each test
                # uses its own fresh repository so publication actually starts.
                real_init_create = opf._init_create

                def interrupt_on_third_create(root_fd, relpath, data):
                    interrupt_on_third_create.calls += 1
                    if interrupt_on_third_create.calls >= 3:
                        raise KeyboardInterrupt()
                    return real_init_create(root_fd, relpath, data)
                interrupt_on_third_create.calls = 0
                ipp_repo = make_git("interrupt-during-publication")
                rc, output = drive_cmd_init(
                    ipp_repo, {"_init_create": interrupt_on_third_create})
                ipp_rows = [json.loads(line) for line in output.splitlines()
                            if line.startswith("{")]
                check("interrupt during publication still reports the "
                      "partial-publication disclosure, then exits 2 (QA round 12 C)",
                      rc == EXIT_ERROR
                      and "publication may be partial" in output
                      and any(row.get("event") == "partial-publication"
                              for row in ipp_rows))

                def exit_on_first_create(root_fd, relpath, data):
                    raise SystemExit(0)
                spp_repo = make_git("systemexit-during-publication")
                rc, output = drive_cmd_init(
                    spp_repo, {"_init_create": exit_on_first_create})
                spp_rows = [json.loads(line) for line in output.splitlines()
                            if line.startswith("{")]
                check("SystemExit during publication also reports the "
                      "partial-publication disclosure, then exits 2 (QA round 12 C)",
                      rc == EXIT_ERROR
                      and "publication may be partial" in output
                      and any(row.get("event") == "partial-publication"
                              for row in spp_rows))

                # QA round 13 D (claude round-12 MINOR 4): a SECOND interrupt,
                # raised while the disclosure's detail report itself runs (here
                # from the observation pass it calls), must not drop the fixed
                # partial-publication sentence: the detail JSON is dropped and
                # a fixed fallback line says so. On the round-12 handler the
                # second interrupt escaped to _cmd_init's boundary and no
                # partial-publication line printed at all, so this check fails
                # with the disclosure guard reverted.
                def interrupt2_create(root_fd, relpath, data):
                    interrupt2_create.calls += 1
                    if interrupt2_create.calls >= 3:
                        raise KeyboardInterrupt()
                    return real_init_create(root_fd, relpath, data)
                interrupt2_create.calls = 0

                def interrupt2_observed(root_fd, directories, payloads):
                    raise KeyboardInterrupt()
                dpp_repo = make_git("double-interrupt-publication")
                rc, output = drive_cmd_init(
                    dpp_repo, {"_init_create": interrupt2_create,
                               "_init_observed": interrupt2_observed})
                check("a second interrupt during the disclosure's detail report "
                      "still prints the fixed partial-publication sentence and "
                      "the fallback line, then exits 2 (QA round 13 D)",
                      rc == EXIT_ERROR
                      and "publication may be partial" in output
                      and "partial-publication detail could not be "
                      "reported" in output
                      and not any(line.startswith("{")
                                  and "partial-publication" in line
                                  for line in output.splitlines()))

                # QA round 14 D (claude round-13 MINOR 2): a second interrupt
                # landing in the handler's FIRST print, the cannot-evaluate
                # line, escaped past the round-13 guard to the last-resort
                # boundary and dropped the partial-publication sentence. That
                # print now runs inside the guard, so the fallback line and the
                # fixed sentence still print; on the round-13 handler only the
                # last-resort line printed, so this check fails with the move
                # reverted.
                real_init_echo = opf._init_echo

                def interrupt3_create(root_fd, relpath, data):
                    interrupt3_create.calls += 1
                    if interrupt3_create.calls >= 3:
                        raise KeyboardInterrupt()
                    return real_init_create(root_fd, relpath, data)
                interrupt3_create.calls = 0

                def interrupt3_echo(line, err=False):
                    if (not interrupt3_echo.fired
                            and str(line).startswith("opf init: cannot evaluate at ")):
                        interrupt3_echo.fired = True
                        raise KeyboardInterrupt()
                    return real_init_echo(line, err=err)
                interrupt3_echo.fired = False
                fpp_repo = make_git("first-print-interrupt-publication")
                rc, output = drive_cmd_init(
                    fpp_repo, dict(_init_create=interrupt3_create,
                                   _init_echo=interrupt3_echo))
                check("a second interrupt in the handler's first print (the "
                      "cannot-evaluate line) still prints the fallback line and "
                      "the fixed partial-publication sentence, then exits 2 (QA "
                      "round 14 D)",
                      rc == EXIT_ERROR and interrupt3_echo.fired
                      and "publication may be partial" in output
                      and "cannot-evaluate line or the partial-publication "
                      "detail could not be reported" in output
                      and "last-resort boundary" not in output)

                pi_repo = make_git("postpub'$(touch PWNED)" + '"' + "`;#")
                pi_hostile = working + "/x'$(touch PWNED)`"
                pi_state = {"calls": 0}
                pi_real = opf._init_inventory

                def fake_inventory(root_fd):
                    pi_state["calls"] += 1
                    if pi_state["calls"] == 1:
                        return pi_real(root_fd)
                    return {"complete": True,
                            "entries": [{"path": pi_hostile, "kind": "file"}]}

                rc, output = drive_cmd_init(pi_repo, {"_init_inventory": fake_inventory})
                pi_rows = [json.loads(line) for line in output.splitlines()
                           if line.startswith("{")]
                check("post-publish-inventory report at a hostile root: the event "
                      "carries the root and the hostile entry inert",
                      rc == EXIT_ERROR
                      and inert("working inventory changed during publication") in output
                      and any(row.get("event") == "post-publish-inventory"
                              and row.get("root") == inert(str(pi_repo))
                              and [entry.get("path")
                                   for entry in row.get("entries", [])]
                              == [inert(pi_hostile)]
                              for row in pi_rows))
                check("post-publish-inventory report at a hostile root: output is "
                      "shell-inert and ran no payload",
                      sh_inert_probe(output, "post-publish-inventory") == []
                      and "$" not in output and "`" not in output
                      and not (pi_repo / "PWNED").exists()
                      and not (base / "PWNED").exists())

                # QA round 7 (requirement B) and QA round 8 (requirement B: the
                # gate is now a PERMITTED-CALL allowlist): the route gate over the
                # real source, plus one seeded mutant per recognizer, so the gate
                # itself is discriminated. The roster includes every round-7 bypass of
                # the retired dataflow gate that a NAME can catch (the
                # annotated-assignment print, the print alias and the external helper,
                # plus builtins.print, sys.stderr.writelines, warnings.warn and the
                # print inside _init_same_root) and, below it, every round-8 SPELLED
                # route that passed the round-7 denylist. The binding mutants that
                # carry no banned name and spell only real-source call targets are
                # neutralized at RUNTIME by the rendering constructors and are
                # covered in the runtime mutant block below.
                gate_source = (Path(__file__).resolve().parent / "opf.py").read_text(
                    encoding="utf-8")
                check("route gate: the real source is clean",
                      _init_route_violations(gate_source) == [])
                # The homes rule outside opf.py, held by DISCOVERY over EVERY unit of every
                # module in this directory (never a closed site list) and fail-closed over FORM:
                # any reference to run_init_operation that is not a direct call of the imported
                # name passing homes2 from the one rule is a finding, a write through a store
                # alias by the spellings the scan reads is a finding wherever it sits, and the
                # rule is read through the module-level _opf_store import, whose module-level or
                # global rebinding is a finding (the docstring's residuals list the writes and
                # rebindings the scan does not read). The only exemption is each module's
                # declared _HOMES_SELFTEST_EXEMPT registry, whose functions' bodies are trusted
                # to be test-only and are not scanned (their reachability from production is not
                # checked here). One seeded mutant per escape form below; each row asserts the
                # SPECIFIC finding its form must raise, never only a non-empty list.
                rule_dir = Path(__file__).resolve().parent
                rule_sources = dict(
                    (path.name, path.read_text(encoding="utf-8"))
                    for path in sorted(rule_dir.glob("*.py")))
                check("homes rule sites: every roster file is in the directory scan",
                      set(site[0] for site in _HOMES_RULE_SITES) <= set(rule_sources))
                check("homes rule sites: the real sources are clean",
                      _homes_rule_violations(rule_sources) == [])
                rule_adopt = "homes2=store.init_writes_homes2())"
                rule_seam = ("                              admitted_dirs=admitted_dirs,\n"
                             "                              homes2=_opf_store.init_writes_homes2())")
                rule_child = "operation_id=opid, homes2=_opf_store.init_writes_homes2())"
                rule_coupled = "            result = init_op.run_init_operation("
                rule_seam_def = ("def init_operation(product_root, *, ancestral=None, "
                                 "recover=False, admitted=None,")
                check("homes rule sites: the mutation anchors are present once",
                      rule_sources["_opf_adopt_apply.py"].count(rule_adopt) == 1
                      and rule_sources["_opf_adopt_apply.py"].count(rule_coupled) == 1
                      and rule_sources["_opf_init_operation.py"].count(rule_seam) == 1
                      and rule_sources["_opf_init_operation.py"].count(rule_child) == 1
                      and rule_sources["_opf_init_operation.py"].count(rule_seam_def) == 1
                      and rule_sources["_opf_init_operation.py"].count(
                          "\ndef init_operation(") == 1)
                rule_mutants = [
                    ("the adoption-coupled init re-deriving SUPPORTED_HOMES >= 2",
                     "_opf_adopt_apply.py", rule_adopt, "homes2=store.SUPPORTED_HOMES >= 2)",
                     "names SUPPORTED_HOMES"),
                    ("the adoption-coupled init splatting its arguments",
                     "_opf_adopt_apply.py", rule_adopt,
                     "homes2=store.init_writes_homes2(), **{})",
                     "splats its run_init_operation arguments"),
                    ("the public seam omitting homes2",
                     "_opf_init_operation.py", rule_seam,
                     "                              admitted_dirs=admitted_dirs)",
                     "does not pass homes2"),
                    ("the public seam re-deriving SUPPORTED_HOMES >= 2",
                     "_opf_init_operation.py", rule_seam,
                     rule_seam.replace("_opf_store.init_writes_homes2()",
                                       "_opf_store.SUPPORTED_HOMES >= 2"),
                     "does not pass homes2"),
                    ("the kill-point child passing a constant",
                     "_opf_init_operation.py", rule_child, "operation_id=opid, homes2=False)",
                     "does not pass homes2"),
                    ("the public seam renamed away",
                     "_opf_init_operation.py", "\ndef init_operation(",
                     "\ndef init_operation_v1(", "is defined 0 times"),
                    ("a new production function calling run_init_operation without the rule",
                     "_opf_init_operation.py", "\ndef init_operation(",
                     "\ndef _init_rehearse(root):\n    return run_init_operation(root)\n\n\n"
                     "def init_operation(", "does not pass homes2"),
                    ("a new seam passing homes2=False",
                     "_opf_init_operation.py", "\ndef init_operation(",
                     "\ndef _init_quick(root):\n    return run_init_operation(root, homes2=False)"
                     "\n\n\ndef init_operation(", "does not pass homes2"),
                    ("a rebinding of init_writes_homes2 before the call",
                     "_opf_adopt_apply.py", rule_coupled,
                     "            store.init_writes_homes2 = lambda: False\n"
                     "            result = init_op.run_init_operation(",
                     "binds init_writes_homes2"),
                    ("a getattr read of SUPPORTED_HOMES at the call site",
                     "_opf_adopt_apply.py", rule_coupled,
                     "            homes_now = getattr(store, \"SUPPORTED_HOMES\")\n"
                     "            result = init_op.run_init_operation(",
                     "names SUPPORTED_HOMES"),
                    ("a local alias of run_init_operation",
                     "_opf_init_operation.py", "\ndef init_operation(",
                     "\ndef _init_alias(root):\n    op = run_init_operation\n    return op(root)"
                     "\n\n\ndef init_operation(",
                     "references run_init_operation"),
                    ("a getattr string naming the callee",
                     "_opf_init_operation.py", "\ndef init_operation(",
                     "\ndef _init_dyn(mod, root):\n"
                     "    return getattr(mod, \"run_init_operation\")(root)\n\n\n"
                     "def init_operation(",
                     "references run_init_operation"),
                    ("a functools.partial of the callee",
                     "_opf_init_operation.py", "\ndef init_operation(",
                     "\ndef _init_partial(root):\n    import functools\n"
                     "    return functools.partial(run_init_operation, root)\n\n\n"
                     "def init_operation(",
                     "references run_init_operation"),
                    ("a __call__ spelling of the callee",
                     "_opf_init_operation.py", "\ndef init_operation(",
                     "\ndef _init_dunder(root):\n    return run_init_operation.__call__(root)"
                     "\n\n\ndef init_operation(",
                     "references run_init_operation"),
                    ("a self_test-named helper that production calls",
                     "_opf_init_operation.py", "\ndef init_operation(",
                     "\ndef _homes_probe_self_test(root):\n    return run_init_operation(root)"
                     "\n\n\ndef _init_probe(root):\n    return _homes_probe_self_test(root)"
                     "\n\n\ndef init_operation(",
                     "does not pass homes2"),
                    ("a new seam referenced only by a minted self-test function",
                     "_opf_init_operation.py", "\ndef init_operation(",
                     "\ndef _init_seam(root):\n    return run_init_operation(root, homes2=False)"
                     "\n\n\ndef _mint_self_test():\n    return _init_seam\n\n\n"
                     "def init_operation(",
                     "does not pass homes2"),
                    ("a mutually referencing production pair, one calling the seam "
                     "without the rule",
                     "_zz_homes_mutant.py", None,
                     "from _opf_init_operation import run_init_operation\n\n\n"
                     "def _init_pair_a(root):\n    return _init_pair_b(root)\n\n\n"
                     "def _init_pair_b(root):\n    if root is None:\n"
                     "        return _init_pair_a(root)\n"
                     "    return run_init_operation(root)\n",
                     "_init_pair_b does not pass homes2"),
                    ("a three-function production cycle, one calling the seam with "
                     "homes2=False",
                     "_zz_homes_mutant.py", None,
                     "import _opf_init_operation\n\n\n"
                     "def _init_tri_a(root):\n    return _init_tri_b(root)\n\n\n"
                     "def _init_tri_b(root):\n    return _init_tri_c(root)\n\n\n"
                     "def _init_tri_c(root):\n    if root is None:\n"
                     "        return _init_tri_a(root)\n"
                     "    return _opf_init_operation.run_init_operation("
                     "root, homes2=False)\n",
                     "_init_tri_c does not pass homes2"),
                    ("a self-recursive production function calling the seam without "
                     "the rule",
                     "_zz_homes_mutant.py", None,
                     "from _opf_init_operation import run_init_operation\n\n\n"
                     "def _init_solo(root):\n    if root is None:\n"
                     "        return _init_solo(root)\n"
                     "    return run_init_operation(root)\n",
                     "_init_solo does not pass homes2"),
                    ("a local rebinding of the store alias before the call",
                     "_opf_adopt_apply.py", rule_coupled,
                     "            store = None\n"
                     "            result = init_op.run_init_operation(",
                     "binds the store module alias"),
                    ("a parameter shadowing the store alias at the seam",
                     "_opf_init_operation.py", rule_seam_def,
                     "def init_operation(product_root, _opf_store=None, *, ancestral=None, "
                     "recover=False, admitted=None,",
                     "binds the store module alias"),
                    ("a setattr rebinding of the rule in a module that never calls the seam",
                     "_zz_homes_mutant.py", None,
                     "import _opf_store\n\n\ndef _tune():\n"
                     "    setattr(_opf_store, \"init_writes_homes2\", lambda: False)\n",
                     "through setattr"),
                    ("a vars() rebinding of the rule in a module that never calls the seam",
                     "_zz_homes_mutant.py", None,
                     "import _opf_store\n\n\ndef _tune():\n"
                     "    vars(_opf_store)[\"init_writes_homes2\"] = lambda: False\n",
                     "through vars()"),
                    ("a SUPPORTED_HOMES write in a helper away from the call",
                     "_opf_init_operation.py", "\ndef init_operation(",
                     "\ndef _init_tune():\n    _opf_store.SUPPORTED_HOMES = 2\n\n\n"
                     "def init_operation(",
                     "binds SUPPORTED_HOMES"),
                    ("a production rebinding of run_init_operation itself",
                     "_opf_adopt_apply.py", rule_coupled,
                     "            init_op.run_init_operation = None\n"
                     "            result = init_op.run_init_operation(",
                     "references run_init_operation"),
                    ("a decorator calling the seam, applied to a self_test-named function",
                     "_zz_homes_mutant.py", None,
                     "import sys\n\nfrom _opf_init_operation import run_init_operation\n\n\n"
                     "def _arm(fn):\n    return run_init_operation(fn)\n\n\n"
                     "@_arm\ndef _homes_wrap_self_test(root):\n    return None\n\n\n"
                     "if __name__ == \"__main__\":\n"
                     "    if \"--self-test\" in sys.argv:\n"
                     "        _homes_wrap_self_test(None)\n",
                     "_arm does not pass homes2"),
                    ("an argument default on a self_test-named function calling the seam",
                     "_zz_homes_mutant.py", None,
                     "import sys\n\nfrom _opf_init_operation import run_init_operation\n\n\n"
                     "def _homes_seed_self_test(root, seed=run_init_operation(None)):\n"
                     "    return seed\n\n\n"
                     "if __name__ == \"__main__\":\n"
                     "    if \"--self-test\" in sys.argv:\n"
                     "        _homes_seed_self_test(None)\n",
                     "module-level code does not pass homes2"),
                    ("a method dispatching to a self_test-named helper that calls the seam",
                     "_zz_homes_mutant.py", None,
                     "from _opf_init_operation import run_init_operation\n\n\n"
                     "def _homes_probe_self_test(root):\n    return run_init_operation(root)"
                     "\n\n\nclass _InitDriver:\n"
                     "    def prepare(self, root):\n"
                     "        return _homes_probe_self_test(root)\n",
                     "_homes_probe_self_test does not pass homes2"),
                    ("a module-level alias of a self_test-named helper that production calls",
                     "_zz_homes_mutant.py", None,
                     "from _opf_init_operation import run_init_operation\n\n\n"
                     "def _homes_probe_self_test(root):\n    return run_init_operation(root)"
                     "\n\n\nprobe = _homes_probe_self_test\n\n\n"
                     "def _init_probe(root):\n    return probe(root)\n",
                     "_homes_probe_self_test does not pass homes2"),
                    ("a self_test-named decorator, calling the seam, on a production def",
                     "_zz_homes_mutant.py", None,
                     "from _opf_init_operation import run_init_operation\n\n\n"
                     "def _homes_deco_self_test(fn):\n    run_init_operation(None)\n"
                     "    return fn\n\n\n"
                     "@_homes_deco_self_test\ndef _init_prod(root):\n    return root\n",
                     "_homes_deco_self_test does not pass homes2"),
                    ("a production def whose default calls a self_test-named helper",
                     "_zz_homes_mutant.py", None,
                     "from _opf_init_operation import run_init_operation\n\n\n"
                     "def _homes_probe_self_test(root):\n    return run_init_operation(root)"
                     "\n\n\ndef _init_prod(root, seed=_homes_probe_self_test(None)):\n"
                     "    return seed\n",
                     "_homes_probe_self_test does not pass homes2"),
                    ("a self_test-named decorator, calling the seam, on a method",
                     "_zz_homes_mutant.py", None,
                     "from _opf_init_operation import run_init_operation\n\n\n"
                     "def _homes_deco_self_test(fn):\n    run_init_operation(None)\n"
                     "    return fn\n\n\nclass _InitDriver:\n"
                     "    @_homes_deco_self_test\n    def prepare(self, root):\n"
                     "        return root\n",
                     "_homes_deco_self_test does not pass homes2"),
                    ("a tuple alias of a self_test-named helper that production calls",
                     "_zz_homes_mutant.py", None,
                     "from _opf_init_operation import run_init_operation\n\n\n"
                     "def _homes_probe_self_test(root):\n    return run_init_operation(root)"
                     "\n\n\nprobe, _unused = _homes_probe_self_test, None\n\n\n"
                     "def _init_probe(root):\n    return probe(root)\n",
                     "_homes_probe_self_test does not pass homes2"),
                    ("a dict entry aliasing a self_test-named helper that production calls",
                     "_zz_homes_mutant.py", None,
                     "from _opf_init_operation import run_init_operation\n\n\n"
                     "def _homes_probe_self_test(root):\n    return run_init_operation(root)"
                     "\n\n\n_TABLE = {\"probe\": _homes_probe_self_test}\n\n\n"
                     "def _init_probe(root):\n    return _TABLE[\"probe\"](root)\n",
                     "_homes_probe_self_test does not pass homes2"),
                    ("a class attribute aliasing a self_test-named helper",
                     "_zz_homes_mutant.py", None,
                     "from _opf_init_operation import run_init_operation\n\n\n"
                     "def _homes_probe_self_test(root):\n    return run_init_operation(root)"
                     "\n\n\nclass _InitDriver:\n"
                     "    probe = staticmethod(_homes_probe_self_test)\n\n\n"
                     "def _init_probe(root):\n    return _InitDriver.probe(root)\n",
                     "_homes_probe_self_test does not pass homes2"),
                ]
                for mutant_label, fname, old, new, expect in rule_mutants:
                    mutated_sources = dict(rule_sources)
                    if old is None:
                        mutated = fname not in rule_sources
                        mutated_sources[fname] = new
                    else:
                        mutated_sources[fname] = rule_sources[fname].replace(old, new)
                        mutated = mutated_sources[fname] != rule_sources[fname]
                    mutant_rows = _homes_rule_violations(mutated_sources)
                    check("homes rule sites flag " + mutant_label,
                          mutated and any(expect in row for row in mutant_rows))
                # The exemption registry, mutant and flip side by side: every mutant row names
                # the SPECIFIC finding it must raise, and every flip row (`expect` None) is the
                # edit that must turn its mutant's result around to ZERO findings, so each guard
                # (the registered-body exemption, its per-name and per-module scope, the
                # definition-time scan of registered defs, the registry's shape checks and its
                # roster disjointness) is discriminated in both directions.
                exempt_import = "from _opf_init_operation import run_init_operation\n\n"
                exempt_defs = ("def _homes_probe_self_test(root):\n"
                               "    return run_init_operation(root)\n\n\n"
                               "def _homes_suite_self_test():\n"
                               "    return _homes_probe_self_test(None)\n")
                exempt_reg = (exempt_import
                              + "_HOMES_SELFTEST_EXEMPT = (\"_homes_probe_self_test\",)\n\n\n"
                              "def _homes_probe_self_test(root):\n"
                              "    return run_init_operation(root)\n")
                exempt_clean = (exempt_import
                                + "_HOMES_SELFTEST_EXEMPT = (\"_homes_probe_self_test\", "
                                "\"_homes_suite_self_test\")\n\n\n" + exempt_defs)
                plain_def = "def _probe_x():\n    return None\n"
                foreign_def = ("def _seam_tests(root):\n"
                               "    return run_init_operation(root)\n")
                zz = "_zz_homes_mutant.py"
                registry_rows = [
                    ("a registered helper calling the seam without the rule, driven by a "
                     "registered driver, scans clean (the exemption's positive arm)",
                     zz, None, exempt_clean, None),
                    ("the same helper and driver with the registry removed (mutant of the "
                     "positive arm)",
                     zz, None, exempt_import + "\n" + exempt_defs,
                     "_homes_probe_self_test does not pass homes2"),
                    ("the same helper left out of a registry that names only its driver (a "
                     "registration exempts the named function alone)",
                     zz, None, exempt_import
                     + "_HOMES_SELFTEST_EXEMPT = (\"_homes_suite_self_test\",)\n\n\n"
                     + exempt_defs, "_homes_probe_self_test does not pass homes2"),
                    ("a function named in another module's registry, unregistered here (a "
                     "registry exempts its own module alone)",
                     zz, None, exempt_import + "\n" + foreign_def,
                     "_seam_tests does not pass homes2"),
                    ("the same function registered in its own module (flip)",
                     zz, None, exempt_import
                     + "_HOMES_SELFTEST_EXEMPT = (\"_seam_tests\",)\n\n\n" + foreign_def, None),
                    ("a registered name whose def is nested in `if True:`",
                     zz, None, "_HOMES_SELFTEST_EXEMPT = (\"_probe_x\",)\n\n"
                     "if True:\n    def _probe_x():\n        return None\n",
                     "is not a module-top-level def"),
                    ("a registered name with zero defs",
                     zz, None, "_HOMES_SELFTEST_EXEMPT = (\"_probe_gone\",)\n",
                     "defined 0 times"),
                    ("a registered name with two defs",
                     zz, None, "_HOMES_SELFTEST_EXEMPT = (\"_probe_x\",)\n\n\n"
                     + plain_def + "\n\n" + plain_def, "defined 2 times"),
                    ("the same name with exactly one top-level def in a well-formed registry "
                     "(flip of the nested, 0/2-def and malformed-registry rows)",
                     zz, None, "_HOMES_SELFTEST_EXEMPT = (\"_probe_x\",)\n\n\n" + plain_def,
                     None),
                    ("a roster member added to a registry (its flip is the unmutated file, "
                     "held clean by the real-sources row)",
                     "_opf_adopt_apply.py", "_HOMES_SELFTEST_EXEMPT = (\n",
                     "_HOMES_SELFTEST_EXEMPT = (\n    \"_init_store_run\",\n",
                     "may not be exempt"),
                    ("a registry whose value is a list",
                     zz, None, "_HOMES_SELFTEST_EXEMPT = [\"_probe_x\"]\n\n\n" + plain_def,
                     "the registry value must be a tuple of string constants"),
                    ("a registry with a duplicate element",
                     zz, None, "_HOMES_SELFTEST_EXEMPT = (\"_probe_x\", \"_probe_x\")\n\n\n"
                     + plain_def, "duplicate registry element '_probe_x'"),
                    ("a registry with an empty string element",
                     zz, None, "_HOMES_SELFTEST_EXEMPT = (\"\",)\n\n\n" + plain_def,
                     "a registry element is not a nonempty string constant"),
                    ("a registry with a non-string element",
                     zz, None, "_HOMES_SELFTEST_EXEMPT = (1,)\n\n\n" + plain_def,
                     "a registry element is not a nonempty string constant"),
                    ("a registry bound by a chained assignment",
                     zz, None, "_X = _HOMES_SELFTEST_EXEMPT = (\"_probe_x\",)\n\n\n" + plain_def,
                     "the registry statement must assign the one plain name"),
                    ("a registry bound by an annotated assignment",
                     zz, None, "_HOMES_SELFTEST_EXEMPT: tuple = (\"_probe_x\",)\n\n\n"
                     + plain_def,
                     "bound outside a single module-level registry assignment"),
                    ("a second registry statement",
                     zz, None, "_HOMES_SELFTEST_EXEMPT = (\"_probe_x\",)\n"
                     "_HOMES_SELFTEST_EXEMPT = (\"_probe_x\",)\n\n\n" + plain_def,
                     "declared more than once"),
                    ("an aug-assign of the registry name inside a function",
                     zz, None, "_HOMES_SELFTEST_EXEMPT = (\"_probe_x\",)\n\n\n" + plain_def
                     + "\n\ndef _grow():\n    global _HOMES_SELFTEST_EXEMPT\n"
                     "    _HOMES_SELFTEST_EXEMPT += (\"_probe_x\",)\n",
                     "bound again outside the registry statement"),
                    ("a registry placed after the first top-level def",
                     zz, None, plain_def + "\n\n_HOMES_SELFTEST_EXEMPT = (\"_probe_x\",)\n",
                     "the registry must precede the first module-level def"),
                    ("a registered function whose default calls the seam (defaults are "
                     "import-time module code, never exempt)",
                     zz, None, exempt_import
                     + "_HOMES_SELFTEST_EXEMPT = (\"_homes_probe_self_test\",)\n\n\n"
                     "def _homes_probe_self_test(root, seed=run_init_operation(None)):\n"
                     "    return seed\n", "module-level code does not pass homes2"),
                    ("the same default moved into the exempt body (flip)",
                     zz, None, exempt_reg, None),
                    ("a negated self-test guard over a module-level seam call (guards grant "
                     "nothing)",
                     zz, None, "import sys\n\nfrom _opf_init_operation import "
                     "run_init_operation\n\nif \"--self-test\" not in sys.argv:\n"
                     "    run_init_operation(None)\n", "does not pass homes2"),
                    ("the negated guard kept, the call deleted (flip: the guard was never "
                     "what mattered)",
                     zz, None, "import sys\n\nfrom _opf_init_operation import "
                     "run_init_operation\n\nif \"--self-test\" not in sys.argv:\n"
                     "    pass\n", None),
                ]
                for mutant_label, fname, old, new, expect in registry_rows:
                    mutated_sources = dict(rule_sources)
                    if old is None:
                        mutated = fname not in rule_sources
                        mutated_sources[fname] = new
                    else:
                        mutated_sources[fname] = rule_sources[fname].replace(old, new)
                        mutated = mutated_sources[fname] != rule_sources[fname]
                    mutant_rows = _homes_rule_violations(mutated_sources)
                    if expect is None:
                        check("homes exemption registry: " + mutant_label,
                              mutated and mutant_rows == [])
                    else:
                        check("homes exemption registry: " + mutant_label,
                              mutated and any(expect in row for row in mutant_rows))
                # Spellings of the callee and of the store alias, mutant and flip side by side:
                # each mutant names its SPECIFIC finding, and each flip (`expect` None) is the
                # compliant neighbour that must scan clean.
                alias_store = "import _opf_store\n"
                alias_call = ("def _init_ok(root):\n    return run_init_operation("
                              "root, homes2=_opf_store.init_writes_homes2())\n")
                alias_rows = [
                    ("a variable bound to an imported alias of run_init_operation",
                     alias_store + "from _opf_init_operation import run_init_operation as init"
                     "\n\n\ndef _init_indirect(root):\n    invoke = init\n"
                     "    return invoke(root, homes2=_opf_store.init_writes_homes2())\n",
                     "_init_indirect references run_init_operation"),
                    ("the imported alias called directly with the rule (an aliased import "
                     "is itself a finding)",
                     alias_store + "from _opf_init_operation import run_init_operation as init"
                     "\n\n\ndef _init_direct(root):\n"
                     "    return init(root, homes2=_opf_store.init_writes_homes2())\n",
                     "imports run_init_operation under the alias init"),
                    ("a getattr string on the module object naming the imported alias",
                     "import sys\n\n" + alias_store
                     + "from _opf_init_operation import run_init_operation as init\n\n\n"
                     "def _init_dyn(root):\n"
                     "    invoke = getattr(sys.modules[__name__], \"init\")\n"
                     "    return invoke(root)\n",
                     "imports run_init_operation under the alias init"),
                    ("a globals() string naming the imported alias",
                     alias_store + "from _opf_init_operation import run_init_operation as init"
                     "\n\n\ndef _init_dyn(root):\n    return globals()[\"init\"](root)\n",
                     "imports run_init_operation under the alias init"),
                    ("the same call through the unaliased import (flip of the aliased-import "
                     "rows)",
                     alias_store + exempt_import.replace("\n\n", "\n") + "\n\n" + alias_call,
                     None),
                    ("a module-level def rebinding the imported callee name",
                     alias_store + exempt_import.replace("\n\n", "\n")
                     + "\n\ndef run_init_operation(root, **kwargs):\n    return None\n\n\n"
                     + alias_call,
                     "module-level code rebinds the run_init_operation import "
                     "run_init_operation"),
                    ("a later module-level import rebinding the imported callee name",
                     alias_store + exempt_import.replace("\n\n", "\n")
                     + "from os import getcwd as run_init_operation\n\n\n" + alias_call,
                     "module-level code rebinds the run_init_operation import "
                     "run_init_operation"),
                    ("a global rebinding of the callee name in a unit that never calls it",
                     alias_store + exempt_import.replace("\n\n", "\n")
                     + "\n\ndef _swap(fake):\n    global run_init_operation\n"
                     "    run_init_operation = fake\n\n\n" + alias_call,
                     "_swap rebinds the run_init_operation import run_init_operation"),
                    ("a module-level match capture rebinding the store alias",
                     "import types\n\n" + exempt_import.replace("\n\n", "\n") + alias_store
                     + "\nmatch types.SimpleNamespace(init_writes_homes2=lambda: True):\n"
                     "    case _opf_store:\n        pass\n\n\n" + alias_call,
                     "module-level code rebinds the store module alias _opf_store"),
                    ("a module-level match `as` capture rebinding the store alias",
                     "import types\n\n" + exempt_import.replace("\n\n", "\n") + alias_store
                     + "\nmatch 1:\n    case types.SimpleNamespace() as _opf_store:\n"
                     "        pass\n\n\n" + alias_call,
                     "module-level code rebinds the store module alias _opf_store"),
                    ("a match capture of the store alias in the calling unit",
                     "import types\n\n" + exempt_import.replace("\n\n", "\n") + alias_store
                     + "\n\ndef _init_m(root):\n    match types.SimpleNamespace():\n"
                     "        case _opf_store:\n            pass\n"
                     "    return run_init_operation(root, "
                     "homes2=_opf_store.init_writes_homes2())\n",
                     "_init_m binds the store module alias or the callee's name"),
                    ("a module-level type alias rebinding the store alias",
                     exempt_import.replace("\n\n", "\n") + alias_store
                     + "\ntype _opf_store = int\n\n\n" + alias_call,
                     "module-level code rebinds the store module alias _opf_store"),
                    ("a module-level with target rebinding the store alias",
                     "import contextlib\n\n" + exempt_import.replace("\n\n", "\n") + alias_store
                     + "\nwith contextlib.nullcontext(None) as _opf_store:\n    pass\n\n\n"
                     + alias_call,
                     "module-level code rebinds the store module alias _opf_store at module "
                     "level or through a global declaration (line 6)"),
                    ("a with target rebinding the store alias in the calling unit",
                     "import contextlib\n\n" + exempt_import.replace("\n\n", "\n") + alias_store
                     + "\n\ndef _init_w(root):\n"
                     "    with contextlib.nullcontext(None) as _opf_store:\n        pass\n"
                     "    return run_init_operation(root, "
                     "homes2=_opf_store.init_writes_homes2())\n",
                     "_init_w binds the store module alias or the callee's name (line 8)"),
                    ("a with target named SUPPORTED_HOMES in a unit that never calls the seam",
                     "import contextlib\n\n" + alias_store
                     + "\n\ndef _tune():\n    with contextlib.nullcontext(2) as SUPPORTED_HOMES:"
                     "\n        return SUPPORTED_HOMES\n",
                     "_tune binds SUPPORTED_HOMES (line 7)"),
                    ("an annotated raw namespace handle on the store",
                     alias_store + "\n\ndef _tune():\n    ns: dict = vars(_opf_store)\n"
                     "    ns[\"SUPPORTED_HOMES\"] = 2\n",
                     "_tune binds a raw handle on the store module's namespace"),
                    ("an annotated read-only copy of the store namespace (flip)",
                     alias_store + "\n\ndef _tune():\n    ns: dict = dict(vars(_opf_store))\n"
                     "    return ns\n", None),
                    ("a function-local import of run_init_operation under an alias",
                     alias_store + "\n\ndef _init_local(root):\n"
                     "    from _opf_init_operation import run_init_operation as init\n"
                     "    return init(root, homes2=_opf_store.init_writes_homes2())\n",
                     "_init_local imports run_init_operation other than by a module-level"),
                    ("a module-level rebinding of the store alias beside a compliant call",
                     "import types\n\n" + exempt_import.replace("\n\n", "\n") + alias_store
                     + "\n_opf_store = types.SimpleNamespace(init_writes_homes2=lambda: True)"
                     "\n\n\n" + alias_call,
                     "module-level code rebinds the store module alias _opf_store"),
                    ("a global rebinding of the store alias in a unit that never calls the seam",
                     exempt_import.replace("\n\n", "\n") + alias_store
                     + "\n\ndef _swap(fake):\n    global _opf_store\n    _opf_store = fake\n\n\n"
                     + alias_call, "_swap rebinds the store module alias _opf_store"),
                    ("a module-top-level def named like the store alias",
                     exempt_import.replace("\n\n", "\n") + alias_store
                     + "\n\ndef _opf_store():\n    return None\n\n\n" + alias_call,
                     "module-level code rebinds the store module alias _opf_store"),
                    ("the same module with no rebinding of the store alias (flip of the "
                     "module-level, global and def rows)",
                     exempt_import.replace("\n\n", "\n") + alias_store + "\n\n" + alias_call,
                     None),
                    ("a class attribute and a method parameter named like the store alias "
                     "(flip: neither binds the module namespace)",
                     alias_store + "\n\nclass _Holder:\n    _opf_store = None\n\n"
                     "    def read(self, _opf_store):\n        return _opf_store\n", None),
                    ("a store import inside a module-level try read as the alias (flip)",
                     "try:\n    import _opf_store\nexcept ImportError:\n    raise\n"
                     + exempt_import.replace("\n\n", "\n") + "\n\n" + alias_call, None),
                    ("a try-wrapped store import whose handler rebinds the alias",
                     "try:\n    import _opf_store\nexcept ImportError:\n    _opf_store = None\n"
                     + exempt_import.replace("\n\n", "\n") + "\n\n" + alias_call,
                     "module-level code rebinds the store module alias _opf_store"),
                    ("a module-level attribute write of the store alias through sys.modules",
                     "import sys\nimport types\n\n" + exempt_import.replace("\n\n", "\n")
                     + alias_store + "\nsys.modules[__name__]._opf_store = "
                     "types.SimpleNamespace(init_writes_homes2=lambda: True)\n\n\n" + alias_call,
                     "module-level code writes or deletes the attribute _opf_store on an object"),
                    ("the same attribute write through a module-level handle on sys.modules",
                     "import sys\nimport types\n\n" + exempt_import.replace("\n\n", "\n")
                     + alias_store + "\n_this = sys.modules[__name__]\n_this._opf_store = "
                     "types.SimpleNamespace(init_writes_homes2=lambda: True)\n\n\n" + alias_call,
                     "module-level code writes or deletes the attribute _opf_store on an object"),
                    ("a literal setattr of the store alias on the module object",
                     "import sys\n\n" + exempt_import.replace("\n\n", "\n") + alias_store
                     + "\n\ndef _swap(fake):\n"
                     "    setattr(sys.modules[__name__], \"_opf_store\", fake)\n\n\n" + alias_call,
                     "_swap calls setattr with a protected name '_opf_store'"),
                    ("a star import beside the store import",
                     alias_store + exempt_import.replace("\n\n", "\n")
                     + "from _zz_replacement import *\n\n\n" + alias_call,
                     "module-level code has a star import from _zz_replacement"),
                ]
                for mutant_label, new, expect in alias_rows:
                    mutated_sources = dict(rule_sources)
                    mutated = zz not in rule_sources
                    mutated_sources[zz] = new
                    mutant_rows = _homes_rule_violations(mutated_sources)
                    if expect is None:
                        check("homes rule aliases: " + mutant_label,
                              mutated and mutant_rows == [])
                    else:
                        check("homes rule aliases: " + mutant_label,
                              mutated and any(expect in row for row in mutant_rows))
                # The namespace-reach arm: a row for each escape it closes, each with a
                # compliant flip; one row per listed spelling (so dropping any one spelling
                # turns a row red); the safe code it must not report; the cannot-classify rows;
                # and the binding-arm findings no row above names. A tuple `expect` needs every
                # one of its parts in one finding.
                for mutant_label, new, expect in _homes_reach_rows(
                        alias_store, exempt_import.replace("\n\n", "\n"), alias_call):
                    mutated_sources = dict(rule_sources)
                    mutated = zz not in rule_sources
                    mutated_sources[zz] = new
                    mutant_rows = _homes_rule_violations(mutated_sources)
                    parts = (expect,) if isinstance(expect, str) else expect
                    if expect is None:
                        check("homes rule namespace reach: " + mutant_label,
                              mutated and mutant_rows == [])
                    else:
                        check("homes rule namespace reach: " + mutant_label,
                              mutated and any(all(part in row for part in parts)
                                              for row in mutant_rows))
                # An internal error inside the rule fails closed as a named cannot-evaluate
                # finding, never a traceback: one helper at a time is replaced for one scan of
                # a source no earlier row used (so no memoized result answers it). The binding-
                # name function faults in the per-source summary; the alias and callee
                # spellings and the foreign-name helper fault in the findings phase, so each
                # catch has a row.
                this_module = sys.modules[__name__]
                for probe, helper in enumerate(("_route_binding_names", "_store_import_aliases",
                                                "_init_callee_spellings",
                                                "_homes_foreign_module_names")):
                    real_helper = getattr(this_module, helper)

                    def _raising_helper(*args):
                        raise RuntimeError("seeded fault")

                    setattr(this_module, helper, _raising_helper)
                    try:
                        fault_sources = dict(rule_sources)
                        fault_sources[zz] = alias_store + "\n_FAULT_PROBE = {}\n".format(probe)
                        fault_rows = _homes_rule_violations(fault_sources)
                    except Exception:  # noqa: BLE001 -- a traceback is this row's failure
                        fault_rows = None
                    finally:
                        setattr(this_module, helper, real_helper)
                    check("homes rule: an internal error in " + helper + " is a named "
                          "cannot-evaluate finding",
                          fault_rows is not None and any(
                              row.startswith(zz + ": cannot evaluate (internal error in the "
                                             "homes rule: RuntimeError: seeded fault)")
                              for row in fault_rows))
                # The findings-phase parse boundary: the source's summary is built first (so
                # its own parse succeeds), then the parse raises RuntimeError, which must be a
                # named cannot-evaluate finding, never a traceback.
                real_parse = _homes_parse
                fault_sources = dict(rule_sources)
                fault_sources[zz] = alias_store + "\n_FAULT_PROBE = 'parse'\n"
                _homes_module_summary(fault_sources[zz])

                def _raising_parse(*args):
                    raise RuntimeError("seeded fault")

                setattr(this_module, "_homes_parse", _raising_parse)
                try:
                    fault_rows = _homes_rule_violations(fault_sources)
                except Exception:  # noqa: BLE001 -- a traceback is this row's failure
                    fault_rows = None
                finally:
                    setattr(this_module, "_homes_parse", real_parse)
                check("homes rule: a RuntimeError at the findings-phase parse is a named "
                      "cannot-evaluate finding",
                      fault_rows is not None and zz + ": cannot evaluate (internal error in the "
                      "homes rule: RuntimeError: seeded fault)" in fault_rows)

                # An internal error whose own message cannot be formatted (its __str__ raises)
                # is still a named cannot-evaluate finding, in the summary phase (a source no
                # earlier scan cached) and in the findings phase (its summary built first).
                class _UnprintableFault(Exception):
                    def __str__(self):
                        raise RuntimeError("the message itself raises")

                def _unprintable_parse(*args):
                    raise _UnprintableFault()

                unprintable = ("internal error in the homes rule: _UnprintableFault (an "
                               "exception whose message cannot be formatted)")
                for phase in ("summary", "findings"):
                    fault_sources = dict(rule_sources)
                    fault_sources[zz] = alias_store + "\n_FAULT_PROBE = 'unprintable {}'\n".format(
                        phase)
                    if phase == "findings":
                        _homes_module_summary(fault_sources[zz])
                    setattr(this_module, "_homes_parse", _unprintable_parse)
                    try:
                        fault_rows = _homes_rule_violations(fault_sources)
                    except Exception:  # noqa: BLE001 -- a traceback is this row's failure
                        fault_rows = None
                    finally:
                        setattr(this_module, "_homes_parse", real_parse)
                    check("homes rule: an exception whose message cannot be formatted, raised "
                          "in the " + phase + " phase, is a named cannot-evaluate finding",
                          fault_rows is not None
                          and zz + ": cannot evaluate (" + unprintable + ")" in fault_rows)
                gate_anchor = '    stage = "preflight"'
                check("route gate: the mutation anchor is present once",
                      gate_source.count(gate_anchor) == 1)
                deep_anchor = "    check_fd = _opf_store._open_dir_nofollow(root, traverse_only=True)"
                check("route gate: the deep-helper anchor is present once",
                      gate_source.count(deep_anchor) == 1)
                gate_mutants = [
                    ("a print in _cmd_init", "    print(str(root))"),
                    ("an annotated assignment feeding print",
                     "    diagnostic: str = str(root)@N@    print(diagnostic)"),
                    ("a print alias", "    _p = print@N@    _p(str(root))"),
                    ("builtins.print",
                     "    import builtins@N@    builtins.print(str(root))"),
                    ("sys.stderr.writelines",
                     "    sys.stderr.writelines([str(root)])"),
                    ("sys.stdout.write", "    sys.stdout.write(str(root))"),
                    ("warnings.warn", "    warnings.warn(str(root))"),
                    ("logging.error", "    logging.error(str(root))"),
                    ("the emitter rebound by assignment", "    _init_echo = None"),
                    ("the renderer rebound by an annotated assignment",
                     "    _init_inert: object = str"),
                    ("the emitter rebound as a loop target",
                     "    for _init_echo in []:@N@        pass"),
                    ("the emitter shadowed by a nested def",
                     "@N@    def _init_echo(line, err=False):@N@        pass"),
                    ("the emitter rebound by an import alias",
                     "    import json as _init_echo"),
                    ("a restore-line template that is not a literal",
                     "    _InitRestoreLine(str(root))"),
                    ("a refusal template that is not a literal",
                     "    _InitPriorStoreRefusal(str(root), (), [])"),
                ]
                newline = chr(10)
                for mutant_label, insertion in gate_mutants:
                    mutated = gate_source.replace(
                        gate_anchor,
                        gate_anchor + newline + insertion.replace("@N@", newline))
                    check("route gate flags " + mutant_label,
                          _init_route_violations(mutated) != [])
                check("route gate flags a print inside the reached helper "
                      "_init_same_root",
                      _init_route_violations(gate_source.replace(
                          deep_anchor,
                          "    print(str(root))" + newline + deep_anchor)) != [])
                check("route gate flags an external helper that prints",
                      _init_route_violations(
                          gate_source.replace(
                              gate_anchor,
                              gate_anchor + newline
                              + "    _qa_route_helper(str(root))")
                          + newline + newline + "def _qa_route_helper(value):"
                          + newline + "    print(value)" + newline) != [])
                # QA round 8 (requirement B, both reviewers): the SPELLED output
                # routes that passed the round-7 denylist, one vector each (the 11
                # claude routes and the 5 codex routes), plus a computed target, a
                # spelled getattr and a module-level alias. Each must FAIL the
                # permitted-call gate; on the round-7 denylist every one of them
                # returned zero violations.
                spelled_routes = [
                    ("os.writev on stderr", "    os.writev(2, [str(rest).encode()])"),
                    ("traceback.print_exc",
                     "    import traceback@N@    traceback.print_exc()"),
                    ("pprint.pprint", "    import pprint@N@    pprint.pprint(rest)"),
                    ("input with a prompt", "    input(str(rest))"),
                    ("sys.exit with a message", "    sys.exit(str(rest))"),
                    ("raise SystemExit with a message",
                     "    raise SystemExit(str(rest))"),
                    ("subprocess.run of echo",
                     "    import subprocess@N@"
                     "    subprocess.run(['/bin/echo', str(rest)])"),
                    ("os.system of echo", "    os.system('echo ' + str(rest))"),
                    ("json.dump to an opened /dev/stdout",
                     "    json.dump(rest, open('/dev/stdout', 'w'))"),
                    ("shutil.copyfileobj to sys.__stdout__",
                     "    import shutil, io@N@    shutil.copyfileobj("
                     "io.StringIO(str(rest)), sys.__stdout__)"),
                    ("sys.displayhook", "    sys.displayhook(rest)"),
                    ("codex subprocess echo",
                     "    import subprocess@N@"
                     "    subprocess.run(['/bin/echo', rest[0]])"),
                    ("codex os.system", "    os.system('echo ' + rest[0])"),
                    ("codex os.writev", "    os.writev(2, [rest[0].encode()])"),
                    ("codex Path write_text",
                     "    Path('/dev/stdout').write_text(rest[0])"),
                    ("a computed call target",
                     "    (str if rest else repr)(rest)"),
                    ("a spelled getattr route",
                     "    getattr(os, 'writev')(2, [b'x'])"),
                    ("codex round-9: an import alias of a permitted name",
                     "    from os import writev as ascii@N@"
                     "    ascii(1, [b'QA-SPELLED-ROUTE'])"),
                    ("claude round-9: sorted with an os.system callback",
                     "    sorted(rest, key=os.system)"),
                    ("claude round-9: sorted with an input callback",
                     "    sorted(rest, key=input)"),
                    ("claude round-9: sorted with a sys.displayhook callback",
                     "    sorted(rest, key=sys.displayhook)"),
                    ("claude round-9: sorted with a sys.exit callback",
                     "    sorted(rest, key=sys.exit)"),
                    ("claude round-9: json.dumps with an os.system default",
                     "    json.dumps(list(rest), default=os.system)"),
                    ("claude round-9: rows.sort with an os.system key",
                     "    rows = list(rest)@N@    rows.sort(key=os.system)"),
                ]
                for mutant_label, insertion in spelled_routes:
                    mutated = gate_source.replace(
                        gate_anchor,
                        gate_anchor + newline + insertion.replace("@N@", newline))
                    check("route gate flags the spelled route: " + mutant_label,
                          _init_route_violations(mutated) != [])
                check("route gate flags the codex module-level alias to an "
                      "out-of-graph helper",
                      _init_route_violations(
                          gate_source.replace(
                              gate_anchor,
                              gate_anchor + newline
                              + "    _qa_alias_route(str(rest))")
                          + newline + newline + "def _qa_spelled_helper(value):"
                          + newline + "    print(value)" + newline + newline
                          + newline + "_qa_alias_route = _qa_spelled_helper"
                          + newline) != [])
                check("route gate refuses a duplicate module-level definition",
                      _init_route_violations(
                          gate_source + newline + newline
                          + "def _init_same_root(root, root_fd):" + newline
                          + "    return None" + newline) != [])
                check("route gate refuses a module-level rebinding of a reachable "
                      "definition",
                      _init_route_violations(
                          gate_source + newline + newline
                          + "_init_same_root = None" + newline) != [])
                check("route gate refuses a source without _cmd_init",
                      _init_route_violations(gate_source.replace(
                          "def _cmd_init(rest):",
                          "def _cmd_init_renamed(rest):")) != [])
                check("route gate refuses a source without the emitter",
                      _init_route_violations(gate_source.replace(
                          "def _init_echo(line, err=False):",
                          "def _init_echo_gone(line, err=False):")) != [])
                # QA round 9, requirement A: the remaining reproductions, each a
                # zero-violation pass on the round-8 call gate. The module-level
                # import alias rebinds a permitted spelling outside any function;
                # the pre-try raise escapes both rendering handlers into a raw
                # traceback; str.__new__ outside _InitRestoreLine mints an
                # instance of the rendering type around an unrendered string; and
                # a permitted spelling used in a function whose roster does not
                # carry it fails the per-function scoping.
                check("route gate flags the codex round-9 module-level import "
                      "alias of a permitted name",
                      _init_route_violations(
                          gate_source + newline
                          + "from os import writev as ascii" + newline) != [])
                # The gate anchor sits after the parser loop and BEFORE the
                # handled try, so an insertion there escapes both of _init_main's
                # rendering handlers (the last-resort boundary in _cmd_init still
                # renders whatever escapes at runtime; the rule is the narrower
                # static pin).
                check("route gate flags a raise before the handled try (claude "
                      "round-9: the traceback printed a raw argv value)",
                      _init_route_violations(gate_source.replace(
                          gate_anchor,
                          gate_anchor + newline
                          + "    raise RuntimeError(rest[0])")) != [])
                check("route gate passes a pre-try raise of the rendering refusal "
                      "type (its constructor renders, and the last-resort "
                      "boundary reports whatever escapes, rendered)",
                      _init_route_violations(gate_source.replace(
                          gate_anchor,
                          gate_anchor + newline
                          + "    raise _InitPriorStoreRefusal(" + chr(34) + "qa"
                          + chr(34) + ", (), [])")) == [])
                mint_anchor = "    raise _InitPriorStoreRefusal("
                check("route gate: the mint anchor is present once",
                      gate_source.count(mint_anchor) == 1)
                check("route gate flags str.__new__ minting a restore line "
                      "outside _InitRestoreLine (codex round-9 MAJOR 2)",
                      _init_route_violations(gate_source.replace(
                          mint_anchor,
                          "    restore_lines.append(str.__new__("
                          "_InitRestoreLine, str(repo)))" + newline
                          + mint_anchor)) != [])
                check("route gate flags str.__new__ bound to the remedy template "
                      "(claude round-9 MAJOR 1)",
                      _init_route_violations(gate_source.replace(
                          mint_anchor,
                          "    remedy_template = str.__new__("
                          "_InitRestoreLine, str(repo))" + newline
                          + mint_anchor)) != [])
                check("route gate scopes a permitted spelling to its one "
                      "function (rows.sort is rostered for _init_inventory, not "
                      "_cmd_init)",
                      _init_route_violations(gate_source.replace(
                          gate_anchor,
                          gate_anchor + newline + "    rows = []" + newline
                          + "    rows.sort()")) != [])

                # QA round 10 MAJOR 1 (claude): the round-9 raise rule never saw
                # ast.Assert, a raise ... from cause, or an implicit exception,
                # and the argument loop sat outside both rendering handlers, so
                # each printed a raw argv value in an escaping traceback. The
                # gate now refuses assert on the whole reachable surface and a
                # cause on the one permitted raise shape; the implicit shapes (a
                # subscript's KeyError, an IndexError) are statically invisible,
                # a stated residual, and are closed at RUNTIME by the
                # last-resort boundary, driven in the runtime block below. The
                # boundary itself is pinned twice: by the gate's shape rule
                # (held by the narrowed-handler mutant here) and by its exact
                # bytes.
                wrapper_pin = (
                    "    try:" + newline
                    + "        return _init_main(rest)" + newline
                    + "    except BaseException as exc:  # noqa: BLE001  the "
                    "last-resort rendering boundary" + newline
                    + "        try:" + newline
                    + "            _init_echo(" + newline
                    + '                "opf init: cannot evaluate: {}({}); exit '
                    + "2 (reported by the " + chr(34) + newline
                    + '                "last-resort boundary, which prints '
                    + "nothing else)" + chr(34) + ".format(" + newline
                    + "                    _init_inert(type(exc).__name__), "
                    "_init_inert(str(exc)))," + newline
                    + "                err=True)" + newline
                    + "        except BaseException:  # noqa: BLE001  a failing "
                    "report must not re-raise" + newline
                    + "            pass" + newline
                    + "        return EXIT_MALFORMED" + newline)
                check("route runtime boundary: the last-resort wrapper's exact "
                      "bytes are pinned once in the source",
                      gate_source.count(wrapper_pin) == 1)
                check("route gate flags a boundary whose handler narrows to "
                      "Exception (the boundary must catch BaseException)",
                      _init_route_violations(gate_source.replace(
                          "    except BaseException as exc:",
                          "    except Exception as exc:")) != [])
                parser_anchor = ('            _init_echo("opf init: unrecognized '
                                 'argument, inert-escaped: {}".format(')
                check("route gate: the parser anchor is present once",
                      gate_source.count(parser_anchor) == 1)
                check("route gate flags an assert in the argument loop (QA "
                      "round 10 MAJOR 1: AssertionError carries its message "
                      "expression into the traceback raw)",
                      _init_route_violations(gate_source.replace(
                          parser_anchor,
                          '            assert tok == "--root", tok' + newline
                          + parser_anchor)) != [])
                check("route gate flags a pre-try raise ... from (QA round 10 "
                      "MAJOR 1: the cause rides the chained traceback raw)",
                      _init_route_violations(gate_source.replace(
                          parser_anchor,
                          '            raise _InitPriorStoreRefusal("qa {}", '
                          "(tok,), []) from RuntimeError(tok)" + newline
                          + parser_anchor)) != [])
                journal_anchor = "        journal = _opf_store._journal"
                check("route gate: the journal anchor is present once",
                      gate_source.count(journal_anchor) == 1)
                # QA round 10 MAJOR 2 (claude, a round-9 regression): the
                # literal-only-bound template rule counted a binding a match
                # capture hid from it, and a literal binding inside a nested def
                # that did not bind the outer read, so a raw template passed the
                # gate and printed raw through the refusal handler. Each mutant
                # here must be refused; the literal-only control, the real
                # source's remedy-template shape, must pass.
                m2_match = ('        qa_t = "{}"' + newline
                            + "        match root:" + newline
                            + "            case qa_t:" + newline
                            + "                pass" + newline)
                check("route gate flags a match-captured name used as the "
                      "refusal template (a case clause rebinds the name to the "
                      "match subject)",
                      _init_route_violations(gate_source.replace(
                          journal_anchor,
                          journal_anchor + newline + m2_match
                          + "        raise _InitPriorStoreRefusal(qa_t, (), [])"
                          )) != [])
                check("route gate flags a match-captured name used as a restore "
                      "line template",
                      _init_route_violations(gate_source.replace(
                          journal_anchor,
                          journal_anchor + newline + m2_match
                          + '        raise _InitPriorStoreRefusal("qa {}", '
                          '("x",), [_InitRestoreLine(qa_t)])')) != [])
                check("route gate flags a template name bound only inside a "
                      "nested def (the binding does not bind the outer read, "
                      "which can resolve to a raw module global at runtime)",
                      _init_route_violations(gate_source.replace(
                          journal_anchor,
                          journal_anchor + newline
                          + "        def _qa_unused():" + newline
                          + '            qa_g = "{}"' + newline
                          + "        raise _InitPriorStoreRefusal(qa_g, (), [])"
                          )) != [])
                check("route gate flags a match capture that rebinds the "
                      "emitter",
                      _init_route_violations(gate_source.replace(
                          journal_anchor,
                          journal_anchor + newline
                          + "        match root:" + newline
                          + "            case _init_echo:" + newline
                          + "                pass")) != [])
                check("route gate passes a literal-only-bound template name in "
                      "the constructing function's own scope (the real source's "
                      "remedy-template shape)",
                      _init_route_violations(gate_source.replace(
                          journal_anchor,
                          journal_anchor + newline
                          + '        qa_t = "qa"' + newline
                          + "        raise _InitPriorStoreRefusal(qa_t, (), [])"
                          )) == [])

                # QA round 12 B (claude round-11 MAJOR 1): the round-11 template rule
                # read only args[0] and the remedy_template keyword, so a remedy
                # template supplied positionally (index 3) or through a ** splat
                # skipped the gate, and a * splat could land one on any position.
                # Each mutant here must be refused; the controls above (the real
                # source's keyword remedy-template shape) still pass.
                check("route gate flags a positional remedy template (4th argument, "
                      "QA round 12 B)",
                      _init_route_violations(gate_source.replace(
                          journal_anchor,
                          journal_anchor + newline
                          + '        qa_t = "qa {remedy} ."' + newline
                          + "        raise _InitPriorStoreRefusal("
                          "qa_t, (), [], str(root))")) != [])
                check("route gate flags a ** splat remedy template",
                      _init_route_violations(gate_source.replace(
                          journal_anchor,
                          journal_anchor + newline
                          + '        qa_t = "qa {remedy} ."' + newline
                          + "        raise _InitPriorStoreRefusal("
                          "qa_t, (), [], **dict(remedy_template=str(root)))")) != [])
                check("route gate flags a * splat in a rendered-type construction",
                      _init_route_violations(gate_source.replace(
                          journal_anchor,
                          journal_anchor + newline
                          + "        qa_args = [str(root)]" + newline
                          + "        raise _InitPriorStoreRefusal(*qa_args)")) != [])
                # QA round 13 C (codex round-12 MINOR 3): the leading-splat vector
                # above is also refused by the template rule (args[0] IS the splat),
                # so it could not discriminate the splat rule itself. A splat AFTER
                # a literal positional template is refused ONLY by the splat rule:
                # with that rule reverted this construction reports zero violations
                # and this check fails, pinning the rule.
                check("route gate flags a * splat following a literal positional "
                      "template (only the splat rule reads it, QA round 13 C)",
                      _init_route_violations(gate_source.replace(
                          journal_anchor,
                          journal_anchor + newline
                          + "        qa_args = [()]" + newline
                          + '        raise _InitPriorStoreRefusal("qa {}", '
                          "*qa_args, [])")) != [])
                check("route gate flags a non-literal template passed only as the "
                      "template keyword",
                      _init_route_violations(gate_source.replace(
                          journal_anchor,
                          journal_anchor + newline
                          + "        raise _InitPriorStoreRefusal("
                          "template=str(root), values=(), restore_lines=[])")) != [])
                # QA round 13 C (codex round-12 MINOR 3): the round-11 gate refused
                # a keyword-only template as a construction WITHOUT a template even
                # when it was a literal, so the non-literal keyword vector above
                # could not discriminate the round-12 keyword reading. This literal
                # control passes only on a gate that reads the template keyword:
                # with that reading reverted this acceptance fails, pinning it.
                check("route gate passes a literal template supplied only as the "
                      "template keyword (QA round 13 C)",
                      _init_route_violations(gate_source.replace(
                          journal_anchor,
                          journal_anchor + newline
                          + '        raise _InitPriorStoreRefusal(template="qa", '
                          "values=(), restore_lines=[])")) == [])

                # QA round 12 D (codex round-11 MEDIUM 2, claude MINOR 4): the binding
                # scan did not see a PEP 695 type alias statement, type parameters, or
                # an async-for target, so a name one rebound passed the literal-only
                # template rule and the pinned-name / reachable-definition rebinding
                # rules. Each form here must be refused; the literal control still
                # passes.
                check("route gate flags a type-alias rebinding of a template name "
                      "(QA round 12 D)",
                      _init_route_violations(gate_source.replace(
                          journal_anchor,
                          journal_anchor + newline
                          + '        qa_t = "qa {} ."' + newline
                          + "        type qa_t = str" + newline
                          + "        raise _InitPriorStoreRefusal(qa_t, (), [])")) != [])
                check("route gate flags a type parameter rebinding a template name",
                      _init_route_violations(gate_source.replace(
                          journal_anchor,
                          journal_anchor + newline
                          + '        qa_t = "qa ."' + newline
                          + "        def _qa_nested[qa_t]():" + newline
                          + "            pass" + newline
                          + "        raise _InitPriorStoreRefusal(qa_t, (), [])")) != [])
                check("route gate flags a type-alias rebinding of the pinned emitter",
                      _init_route_violations(gate_source.replace(
                          journal_anchor,
                          journal_anchor + newline
                          + "        type _init_echo = str")) != [])
                check("route gate flags a type-alias rebinding of a reachable "
                      "definition",
                      _init_route_violations(gate_source.replace(
                          journal_anchor,
                          journal_anchor + newline
                          + "        type _init_same_root = str")) != [])
                check("route gate flags an async-for target rebinding the pinned "
                      "emitter",
                      _init_route_violations(gate_source.replace(
                          journal_anchor,
                          journal_anchor + newline
                          + "        async def _qa_async():" + newline
                          + "            async for _init_echo in []:" + newline
                          + "                pass")) != [])
                check("route gate passes a plain literal-assignment control for the "
                      "QA round 12 D forms",
                      _init_route_violations(gate_source.replace(
                          journal_anchor,
                          journal_anchor + newline
                          + '        qa_lit = "qa"' + newline
                          + "        raise _InitPriorStoreRefusal(qa_lit, (), [])")) == [])

                # QA round 13 A (QA round 12 MAJOR, both reviewers): a declared
                # subclass of a rendered type inherits the rendering constructor,
                # and a call to it is not a construction the template rule reads,
                # so a hostile value in the template position passed the round-12
                # gate with zero violations. Each form here must be refused; with
                # the subclass rules reverted every one reports zero violations,
                # so these checks pin them.
                check("route gate flags a local subclass of the refusal type "
                      "(QA round 12 MAJOR)",
                      _init_route_violations(gate_source.replace(
                          journal_anchor,
                          journal_anchor + newline
                          + "        class _qa_sub(_InitPriorStoreRefusal):" + newline
                          + "            pass" + newline
                          + "        raise _qa_sub(str(root), (), [])")) != [])
                check("route gate flags a module-level subclass of the refusal "
                      "type",
                      _init_route_violations(
                          gate_source + newline + newline
                          + "class _QaModRefusal(_InitPriorStoreRefusal):" + newline
                          + "    pass" + newline) != [])
                check("route gate flags a local subclass of the restore-line type",
                      _init_route_violations(gate_source.replace(
                          journal_anchor,
                          journal_anchor + newline
                          + "        class _qa_line(_InitRestoreLine):" + newline
                          + "            pass")) != [])
                check("route gate flags a local class with a computed base inside "
                      "a reachable function",
                      _init_route_violations(gate_source.replace(
                          journal_anchor,
                          journal_anchor + newline
                          + "        class _qa_dyn(type(root)):" + newline
                          + "            pass")) != [])

                # QA rounds 7 and 8 (requirements A and C): the RUNTIME paste
                # test, the primary guard. THE EXIT-PATH BY VALUE-CLASS MATRIX:
                #
                #   exit path                        | driven with
                #   ---------------------------------+------------------------------
                #   P1 missing --root argument       | every non-empty value (as the
                #      (parser; fixed text)          | consumed prior value) + once
                #                                    | with no value at all
                #   P2 repeated --root               | every non-empty value (as
                #      (parser; fixed text)          | both values) + a bare value
                #   P3 empty / dash-led --root value | the empty value and a
                #      (parser; value interpolated)  | dash-led substitution payload
                #   P4 unrecognized argument         | every value
                #      (parser; value interpolated)  |
                #   P5 prior-store refusal, withheld | every non-empty value as the
                #      restore + PARTIAL block       | repository name (line-broken
                #                                    | values refuse earlier, in the
                #                                    | P6 shape, asserted as such)
                #   P6 cannot-evaluate report        | every non-empty value as a
                #      (non-git root)                | non-git directory name
                #   P7 success, hints printed        | the bare-named root (the only
                #                                    | shape that prints hints)
                #   P8 success, hints withheld       | every non-empty value as the
                #                                    | repository name
                #   P9 foreign-content report        | every non-empty value as a
                #                                    | foreign .working entry name
                #   P10 partial-publication report   | every value, in the injected
                #       (fault during publication)   | create-failure text
                #   P11 post-publish-inventory       | every non-empty value, in the
                #       report (fault after publish) | injected inventory entry path
                #   P12 prior-store refusal, PRINTED | every non-empty value as a
                #       restore hints + hostile      | historical machine-subdir
                #       PARTIAL gap paths (the       | name, deleted whole before
                #       repository name is bare)     | the store was dropped
                #
                # Value classes: both quote kinds, command substitution, backquote,
                # ";", "#", LF, CRLF, U+202E, U+2028, U+0085, the empty value,
                # printable non-ASCII, and (QA round 8 BLOCKER 1) history expansion
                # ("!", "!!", "!-1"), glob characters, a zsh glob QUALIFIER that
                # executes code, parentheses (fish command substitution), braces
                # and the tilde. Every physical stdout and stderr line is passed
                # ALONE through sh -c AND through bash --norc --noprofile -i with a
                # primed history line (plus zsh -f -i and fish --no-config -i where
                # installed), each in its own SEEDED, non-empty directory; the
                # probe fails on ANY non-seed entry created there or on the payload
                # marker appearing alone on an output line. The two intended,
                # deliberately runnable hint lines are excluded by an EXACT prefix
                # list, and every hostile-value run additionally asserts that NO
                # printed line matches those prefixes (so the exclusion cannot mask
                # a leak).
                paste_hint_prefixes = ("  git -C ", "  opf render --write --root ")
                paste_marker = "QA-PASTE-MARKER-LINE"

                def paste_probe(output, tag):
                    probe_root = base / ("paste-probe-" + tag)
                    probe_root.mkdir()
                    violations = []
                    for number, line in enumerate(output.splitlines()):
                        if line.startswith(paste_hint_prefixes):
                            continue
                        cell = probe_root / str(number)
                        cell.mkdir()
                        seed_cell(cell)
                        echoed = []
                        for proc in probe_line_legs(line, cell):
                            echoed += [
                                answer for answer in
                                (proc.stdout.decode("utf-8", "replace").splitlines()
                                 + proc.stderr.decode("utf-8", "replace").splitlines())
                                if answer.strip() == paste_marker]
                        created = sorted(entry.name for entry in cell.iterdir()
                                         if entry.name not in probe_seed_names)
                        if created or echoed:
                            violations.append((number, line, created, echoed))
                    return violations

                paste_payload = "$(touch PASTE-PWNED)"
                paste_echo = "$(echo " + paste_marker + ")"
                paste_values = [
                    ("double-quote", 'd"q" ' + paste_payload + ' "' + paste_echo + '"'),
                    ("single-quote", "s'q' " + paste_payload + " '" + paste_echo + "'"),
                    ("backtick",
                     "b`touch PASTE-PWNED``echo " + paste_marker + "`x"),
                    ("semicolon",
                     "v; touch PASTE-PWNED; echo " + paste_marker + " ;x"),
                    ("hash", "v; touch PASTE-PWNED #" + paste_payload),
                    ("linefeed", "v\ntouch PASTE-PWNED\necho " + paste_marker),
                    ("crlf", "v\r\ntouch PASTE-PWNED\r\necho " + paste_marker),
                    ("rtl-override", "v\u202e; touch PASTE-PWNED"),
                    ("line-separator", "v\u2028touch PASTE-PWNED"),
                    ("next-line", "v\u0085touch PASTE-PWNED"),
                    ("empty", ""),
                    ("non-ascii", "caf\u00e9 " + paste_payload),
                    ("bang", "h! ! " + paste_payload),
                    ("double-bang", "v !! x"),
                    ("bang-minus-one", "v !-1"),
                    ("glob-chars", "g* ?[a-z] " + paste_payload),
                    ("glob-qualifier", "*(e:'touch PASTE-PWNED':)"),
                    ("parens", "(touch PASTE-PWNED)"),
                    ("braces", "{touch,PASTE-PWNED} {a,b}"),
                    ("tilde", "~ x~root"),
                ]

                def paste_clean(output, tag):
                    return (paste_probe(output, tag) == []
                            and not any(line.startswith(paste_hint_prefixes)
                                        for line in output.splitlines()))

                # Exit path 1: argument errors (the only path an EMPTY value reaches;
                # a directory name cannot be empty).
                for tag, value in paste_values:
                    rc, output = call(["init", value])
                    check("paste " + tag + ": the unrecognized-argument error is "
                          "paste-inert",
                          rc == EXIT_ERROR and paste_clean(output, "arg-" + tag))
                rc, output = call(["init", "--root", ""])
                check("paste empty: the empty --root error is paste-inert",
                      rc == EXIT_ERROR and paste_clean(output, "arg-empty-root"))
                rc, output = call(["init", "--root", "-" + paste_payload])
                check("paste dash-led --root value: the rejected-value error is "
                      "paste-inert",
                      rc == EXIT_ERROR and paste_clean(output, "arg-dash-root"))

                # Exit paths P1 and P2 (QA round 8, codex MAJOR: the missing and
                # repeated --root parser exits were asserted on rc and a reason
                # substring only, never paste-tested, so a diagnostic edited to
                # carry a live payload would have passed). Their diagnostics are
                # fixed literals that interpolate nothing, and the probe holds
                # exactly that; the hostile value still rides in argv (as the
                # consumed first --root value), so a regression that echoes argv
                # into either diagnostic is caught per value class.
                rc, output = call(["init", "--root"])
                check("paste P1: the missing --root error is paste-inert",
                      rc == EXIT_ERROR
                      and "--root requires a directory argument" in output
                      and paste_clean(output, "arg-missing-root"))
                for tag, value in paste_values:
                    if not value:
                        continue
                    rc, output = call(["init", "--root", value, "--root"])
                    check("paste " + tag + ": the missing --root error after a "
                          "consumed hostile value is paste-inert",
                          rc == EXIT_ERROR
                          and "--root requires a directory argument" in output
                          and paste_clean(output, "arg-missing-" + tag))
                    rc, output = call(["init", "--root", value, "--root", value])
                    check("paste " + tag + ": the repeated --root error is "
                          "paste-inert",
                          rc == EXIT_ERROR
                          and "--root given more than once" in output
                          and paste_clean(output, "arg-repeat-" + tag))
                rc, output = call(["init", "--root", ".", "--root", "."])
                check("paste P2: the repeated --root error on a bare value is "
                      "paste-inert",
                      rc == EXIT_ERROR and "--root given more than once" in output
                      and paste_clean(output, "arg-repeat-bare"))

                # Exit paths 2 to 5 need the value as a DIRECTORY name; the empty
                # value cannot form one and is covered by path 1 above.
                paste_dir_values = [(tag, value) for tag, value in paste_values
                                    if value]
                for tag, value in paste_dir_values:
                    # Path 2: the prior-store refusal, with a PARTIAL block (the
                    # history held old-machine/manifest.toml only, so BOTH of that
                    # store's structural files are expected and missing) and the
                    # restore command withheld (the repository name is hostile).
                    hist = make_git("paste-hist-" + tag + "-" + value)
                    hist_machine = hist / working / machine_name
                    hist_machine.mkdir(parents=True)
                    (hist_machine / _opf_store.MANIFEST_NAME).write_bytes(b"x = 1\n")
                    (hist_machine / _opf_check.COUNTERS_NAME).write_bytes(b"y = 1\n")
                    hist_old = hist / working / "old-machine"
                    hist_old.mkdir()
                    (hist_old / _opf_store.MANIFEST_NAME).write_bytes(b"z = 1\n")
                    (hist / _opf_store.POINTER_REL).write_bytes(
                        b'[store]\ntarget = "dir:."\n')
                    git_call(hist, ["--literal-pathspecs", "add", "-A"])
                    git_call(hist, ["-c", "user.email=t@t", "-c", "user.name=t",
                                    "commit", "-m", "store"])
                    git_call(hist, ["rm", "-q", "--",
                                    working + "/old-machine/"
                                    + _opf_store.MANIFEST_NAME])
                    git_call(hist, ["-c", "user.email=t@t", "-c", "user.name=t",
                                    "commit", "-m", "drop the old manifest"])
                    git_call(hist, ["rm", "-r", "-q", "--",
                                    working, _opf_store.POINTER_REL])
                    git_call(hist, ["-c", "user.email=t@t", "-c", "user.name=t",
                                    "commit", "-m", "drop the store"])
                    rc, output = run(hist)
                    line_broken = chr(10) in value or chr(13) in value
                    if line_broken:
                        # A root whose name holds a line break cannot be confirmed
                        # as a worktree (the rev-parse answer is line-oriented), so
                        # the repository preflight refuses fail-closed BEFORE the
                        # ancestry scan: these values exercise the cannot-evaluate
                        # exit here, and their refusal/success legs below assert the
                        # same fail-closed shape. Both stay paste-inert.
                        check("paste " + tag + ": the line-broken root refuses "
                              "fail-closed and is paste-inert",
                              rc == EXIT_ERROR and "cannot evaluate" in output
                              and paste_clean(output, "refusal-" + tag))
                    else:
                        check("paste " + tag + ": the withheld refusal with its "
                              "PARTIAL block is paste-inert",
                              rc == EXIT_ERROR
                              and "opf init: the restore is PARTIAL" in output
                              and "opf init:   missing store path: "
                              + inert(working + "/old-machine/"
                                      + _opf_check.COUNTERS_NAME) in output
                              and "opf init:   missing store path: "
                              + inert(working + "/old-machine/"
                                      + _opf_store.MANIFEST_NAME) in output
                              and paste_clean(output, "refusal-" + tag))

                    # Path 3: the cannot-evaluate report (a non-git root).
                    nongit_dir = base / ("paste-nongit-" + tag + "-" + value)
                    nongit_dir.mkdir()
                    rc, output = call(["init", "--root", str(nongit_dir)])
                    check("paste " + tag + ": the cannot-evaluate report is "
                          "paste-inert",
                          rc == EXIT_ERROR and "cannot evaluate" in output
                          and paste_clean(output, "cannot-evaluate-" + tag))

                    # Path 4: the success report (hints withheld: the name is not
                    # bare).
                    ok_repo = make_git("paste-ok-" + tag + "-" + value)
                    rc, output = run(ok_repo)
                    if line_broken:
                        check("paste " + tag + ": the line-broken root refuses "
                              "fail-closed before publication and is paste-inert",
                              rc == EXIT_ERROR and "cannot evaluate" in output
                              and paste_clean(output, "success-" + tag))
                    else:
                        check("paste " + tag + ": the success report withholds the "
                              "hints and is paste-inert",
                              rc == EXIT_OK and "command hints withheld" in output
                              and paste_clean(output, "success-" + tag))

                    # Path 5: the foreign-content report (the value as an entry name).
                    foreign_repo = make_git("paste-foreign-" + tag)
                    (foreign_repo / working).mkdir()
                    (foreign_repo / working / value).write_bytes(b"f\n")
                    rc, output = run(foreign_repo)
                    check("paste " + tag + ": the foreign-content report is "
                          "paste-inert",
                          rc == EXIT_ERROR
                          and inert("foreign .working content") in output
                          and paste_clean(output, "foreign-" + tag))

                check("paste: no payload file appeared beside the fixtures",
                      not (base / "PASTE-PWNED").exists())

                # A BARE-named success prints the two intended hint lines; they are
                # excluded by the exact prefixes, every other line is paste-inert,
                # and both hints are really present (the exclusion list names real
                # lines, not dead ones).
                bare_ok = make_git("paste-ok-bare")
                rc, output = run(bare_ok)
                check("paste bare success: hints print and the rest is paste-inert",
                      rc == EXIT_OK
                      and any(line.startswith("  git -C ")
                              for line in output.splitlines())
                      and any(line.startswith("  opf render --write --root ")
                              for line in output.splitlines())
                      and paste_probe(output, "success-bare") == [])

                # Exit paths P10 and P11 per value class (QA round 8, codex MAJOR:
                # the fault-driven partial-publication and post-publish-inventory
                # legs kept single vectors). The hostile value rides in the injected
                # fault, not the repository name, so EVERY value class reaches these
                # two exits, the line-broken ones included (a line-broken repository
                # name would refuse earlier, before publication). The driver
                # captures at the descriptor level (fd_capture).
                for tag, value in paste_values:
                    p10_repo = make_git("paste-p10-" + tag)

                    def p10_fail(root_fd, relpath, data, _value=value):
                        raise RuntimeError("driven create failure " + _value)

                    rc, output = drive_cmd_init(p10_repo, {"_init_create": p10_fail})
                    check("paste " + tag + ": the partial-publication report is "
                          "paste-inert",
                          rc == EXIT_ERROR
                          and "publication may be partial" in output
                          and inert("driven create failure " + value) in output
                          and paste_clean(output, "p10-" + tag))
                    if not value:
                        continue
                    p11_repo = make_git("paste-p11-" + tag)
                    p11_state = {"calls": 0}

                    def p11_inventory(root_fd, _value=value, _state=p11_state):
                        _state["calls"] += 1
                        if _state["calls"] == 1:
                            return pi_real(root_fd)
                        return {"complete": True,
                                "entries": [{"path": working + "/x" + _value,
                                             "kind": "file"}]}

                    rc, output = drive_cmd_init(
                        p11_repo, {"_init_inventory": p11_inventory})
                    p11_rows = [json.loads(line) for line in output.splitlines()
                                if line.startswith("{")]
                    check("paste " + tag + ": the post-publish-inventory report is "
                          "paste-inert",
                          rc == EXIT_ERROR
                          and inert("working inventory changed during publication")
                          in output
                          and any(row.get("event") == "post-publish-inventory"
                                  and [entry.get("path")
                                       for entry in row.get("entries", [])]
                                  == [inert(working + "/x" + value)]
                                  for row in p11_rows)
                          and paste_clean(output, "p11-" + tag))

                # Exit path P12 (QA round 9 MINOR, both reviewers): the PRINTED
                # restore hints beside hostile PARTIAL gap paths. The repository
                # name is bare, so the shell-ready restore command prints; the
                # hostile value is a HISTORICAL machine-subdirectory name whose
                # store was deleted whole in an earlier commit, so each gap line
                # carries the rendered hostile value. The full output is compared
                # byte-for-byte (so the excluded hint lines are exactly the
                # intended commands and nothing else matches their prefixes), and
                # every other line, the hostile gap lines included, runs alone
                # through the paste probe's shells.
                for tag, value in paste_dir_values:
                    p12 = make_git("paste-p12-" + tag)
                    p12_machine = p12 / working / machine_name
                    p12_machine.mkdir(parents=True)
                    (p12_machine / _opf_store.MANIFEST_NAME).write_bytes(
                        b"x = 1\n")
                    (p12_machine / _opf_check.COUNTERS_NAME).write_bytes(
                        b"y = 1\n")
                    p12_dir = p12 / working / value
                    p12_dir.mkdir()
                    (p12_dir / _opf_store.MANIFEST_NAME).write_bytes(b"z = 1\n")
                    (p12 / _opf_store.POINTER_REL).write_bytes(
                        b'[store]\ntarget = "dir:."\n')
                    git_call(p12, ["--literal-pathspecs", "add", "-A"])
                    git_call(p12, ["-c", "user.email=t@t", "-c", "user.name=t",
                                   "commit", "-m", "store with a hostile subdir"])
                    git_call(p12, ["--literal-pathspecs", "rm", "-r", "-q", "--",
                                   working + "/" + value])
                    git_call(p12, ["-c", "user.email=t@t", "-c", "user.name=t",
                                   "commit", "-m", "drop the hostile subdir"])
                    p12_commit = head_of(p12)
                    git_call(p12, ["rm", "-r", "-q", "--",
                                   working, _opf_store.POINTER_REL])
                    git_call(p12, ["-c", "user.email=t@t", "-c", "user.name=t",
                                   "commit", "-m", "drop the store"])
                    rc, output = run(p12)
                    p12_gaps = sorted([
                        working + "/" + value + "/" + _opf_check.COUNTERS_NAME,
                        working + "/" + value + "/" + _opf_store.MANIFEST_NAME])
                    check("paste " + tag + ": the printed restore hints beside "
                          "hostile PARTIAL gap paths are byte-exact and "
                          "paste-inert",
                          rc == EXIT_ERROR and output == prior_store_output(
                              p12, p12, p12_commit, [".opf.toml", ".working"],
                              True, gaps=p12_gaps)
                          and any(line.startswith("  git -C ")
                                  for line in output.splitlines())
                          and paste_probe(output, "p12-" + tag) == [])
                check("paste P12: no payload file appeared beside the fixtures",
                      not (base / "PASTE-PWNED").exists())

                # PROBE POTENCY (QA round 8 BLOCKER 1): the interactive-bash leg,
                # with its primed history line, catches a RAW history-expansion
                # payload that the sh leg alone cannot (dash has no history
                # expansion), so the round-8 widened renderer, which printed "!"
                # raw, cannot pass this probe again. The zsh and fish legs are
                # exercised the same way where those shells are installed.
                check("paste probe potency: a raw history-expansion line fails the "
                      "primed interactive-bash leg",
                      paste_probe("opf init: unrecognized argument, inert-escaped: "
                                  "v !-1", "potency-bang") != [])
                if probe_has_zsh:
                    check("paste probe potency: a raw zsh glob qualifier fails "
                          "the zsh leg",
                          paste_probe("opf init:   root: *(e:'touch "
                                      "PASTE-PWNED':)", "potency-zsh") != [])
                if probe_has_fish:
                    check("paste probe potency: a raw fish command substitution "
                          "fails the fish leg",
                          paste_probe("opf init:   root: (touch PASTE-PWNED)",
                                      "potency-fish") != [])

                # QA round 7: the round-7 BINDING mutants carry no banned name, so
                # the route gate deliberately passes them (its docstring states that
                # residual); the rendering CONSTRUCTORS neutralize them at runtime.
                # Each mutant is loaded as its own module from a mutated copy of the
                # real source (importlib, never exec), driven in process against a
                # hostile-named fixture, and must answer rc 2 with every hostile
                # character rendered: no raw substitution opener, no raw backquote,
                # and a paste probe that creates nothing.
                import importlib.util

                def load_mutant(tag, mutated_source):
                    mutant_path = base / ("route-mutant-" + tag + ".py")
                    mutant_path.write_text(mutated_source, encoding="utf-8")
                    spec = importlib.util.spec_from_file_location(
                        "opf_route_mutant_" + tag.replace("-", "_"),
                        str(mutant_path))
                    module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(module)
                    if module._bootstrap() != EXIT_OK:
                        raise OSError("route mutant could not bootstrap: " + tag)
                    return module

                def drive_module_init(module, argv):
                    # Descriptor-level capture, like every other in-process leg (QA
                    # round 8, codex MAJOR).
                    return fd_capture(lambda: module._cmd_init(list(argv)))

                rt_repo = make_git("route-mutant'$(touch PWNED-RT)`; #a")
                rt_machine = rt_repo / working / machine_name
                rt_machine.mkdir(parents=True)
                (rt_machine / _opf_store.MANIFEST_NAME).write_bytes(b"x = 1\n")
                (rt_machine / _opf_check.COUNTERS_NAME).write_bytes(b"y = 1\n")
                (rt_repo / _opf_store.POINTER_REL).write_bytes(
                    b'[store]\ntarget = "dir:."\n')
                git_call(rt_repo, ["--literal-pathspecs", "add", "-A"])
                git_call(rt_repo, ["-c", "user.email=t@t", "-c", "user.name=t",
                                   "commit", "-m", "store"])
                git_call(rt_repo, ["rm", "-r", "-q", "--",
                                   working, _opf_store.POINTER_REL])
                git_call(rt_repo, ["-c", "user.email=t@t", "-c", "user.name=t",
                                   "commit", "-m", "drop the store"])
                raise_anchor = "    raise _InitPriorStoreRefusal("
                check("route runtime: the raise anchor is present once",
                      gate_source.count(raise_anchor) == 1)
                # The permitted-call gate (QA round 8) now FLAGS the extend mutant
                # (restore_lines.extend is not a real-source call target), so only
                # the mutants whose spelled calls the real source also makes remain
                # stated residuals; every one of them is still neutralized at
                # runtime by the rendering constructors below.
                runtime_mutants = [
                    ("append", "    restore_lines.append(str(repo))", True),
                    ("setitem", "    restore_lines[0] = str(repo)", True),
                    ("extend", "    restore_lines.extend([str(repo)])", False),
                    ("annassign-remedy", "    remedy: str = str(repo)", True),
                    ("chained-remedy", "    remedy = _qa_x = str(repo)", True),
                    ("tuple-remedy", "    remedy, _qa_x = str(repo), 0", True),
                ]
                for tag, insertion, gate_passes in runtime_mutants:
                    mutated = gate_source.replace(
                        raise_anchor, insertion + newline + raise_anchor)
                    if gate_passes:
                        check("route gate passes the no-banned-name mutant " + tag
                              + " (its stated residual)",
                              _init_route_violations(mutated) == [])
                    else:
                        check("route gate flags the mutant " + tag
                              + " (its spelled call target is outside the "
                              "allowlist)",
                              _init_route_violations(mutated) != [])
                    module = load_mutant(tag, mutated)
                    rc, output = drive_module_init(
                        module, ["--root", str(rt_repo)])
                    check("route runtime: mutant " + tag
                          + " is rendered by the constructors, never raw",
                          rc == EXIT_ERROR and "$(" not in output
                          and "`" not in output
                          and inert(str(rt_repo)) in output
                          and paste_probe(output, "route-" + tag) == [])

                # Claude round-7 mutant 12, adapted to the constructor signature: a
                # refusal raised from _init_same_root with the raw root as message
                # value and restore line. The round-9 reference gate FLAGS it (str
                # is not rostered for _init_same_root, so the per-function scoping
                # catches even this harmless spelling); the constructor still
                # renders both channels at runtime, held below.
                sr_repo = make_git("route-same'$(touch PWNED-SR)`x")
                sr_mutated = gate_source.replace(
                    deep_anchor,
                    "    raise _InitPriorStoreRefusal(" + chr(34) + chr(123)
                    + chr(125) + chr(34) + ", (str(root),), [str(root)])"
                    + newline + deep_anchor)
                check("route gate flags the raw-refusal-from-_init_same_root mutant "
                      "(per-function scoping: str is not rostered there)",
                      _init_route_violations(sr_mutated) != [])
                sr_module = load_mutant("same-root-refusal", sr_mutated)
                rc, output = drive_module_init(sr_module, ["--root", str(sr_repo)])
                check("route runtime: a refusal raised from _init_same_root is "
                      "rendered by the constructors, never raw",
                      rc == EXIT_ERROR and "REFUSED" in output
                      and "$(" not in output and "`" not in output
                      and inert(str(sr_repo)) in output
                      and paste_probe(output, "route-same-root") == [])

                # POTENCY: a mutant that interpolates the rejected argument RAW. The
                # gate passes it (no banned name: that is the stated residual), and
                # the paste probe DISCRIMINATES it: the payload line executes in the
                # probe cell, so the probe reports violations. This pins the paste
                # test as the primary guard.
                check("route runtime: the raw-interpolation anchor is present once",
                      gate_source.count("_init_inert(tok)") == 1)
                pot_mutated = gate_source.replace("_init_inert(tok)", "tok")
                check("route gate passes the raw-interpolation mutant (its stated "
                      "residual)",
                      _init_route_violations(pot_mutated) == [])
                pot_module = load_mutant("raw-arg", pot_mutated)
                rc, output = drive_module_init(
                    pot_module, ["pot; touch PASTE-PWNED #"])
                check("route runtime: the paste probe discriminates the raw "
                      "interpolation (the primary guard is potent)",
                      rc == EXIT_ERROR
                      and paste_probe(output, "route-raw-arg") != [])

                # QA round 10 MAJOR 1: the four traceback routes, driven at
                # RUNTIME through the loaded module's _cmd_init with a hostile
                # argument. The assert and the raise ... from are ALSO flagged
                # statically above; the two implicit exceptions (a subscript's
                # KeyError, an IndexError) are statically invisible, the gate's
                # stated residual, so the last-resort boundary is their closure.
                # Each run must exit 2, name the exception type rendered, print
                # no raw substitution opener, no raw backquote and no Python
                # traceback, and pass the paste probe; on the round-9 parent
                # every one of these printed the raw value in a traceback on
                # descriptor 2.
                boundary_value = ("v; echo " + paste_marker
                                  + " #$(touch PASTE-PWNED)`touch PASTE-PWNED`")
                boundary_mutants = [
                    ("assert-tok", '            assert tok == "--root", tok',
                     "AssertionError", True),
                    ("raise-from",
                     '            raise _InitPriorStoreRefusal("qa {}", (tok,), '
                     "[]) from RuntimeError(tok)",
                     "_InitPriorStoreRefusal", False),
                    ("implicit-keyerror", "            dict()[tok]",
                     "KeyError", False),
                    ("implicit-indexerror", "            rest[9000]",
                     "IndexError", False),
                ]
                for tag, insertion, type_name, carries_value in boundary_mutants:
                    mutated = gate_source.replace(
                        parser_anchor, insertion + newline + parser_anchor)
                    if tag.startswith("implicit-"):
                        check("route gate passes the " + tag + " boundary "
                              "mutant (an implicit exception is its stated "
                              "residual; the boundary is the runtime closure)",
                              _init_route_violations(mutated) == [])
                    module = load_mutant("boundary-" + tag, mutated)
                    rc, output = drive_module_init(module, [boundary_value])
                    check("route runtime boundary: " + tag + " is caught and "
                          "rendered by the last-resort boundary, never printed "
                          "as a traceback",
                          rc == EXIT_ERROR
                          and "cannot evaluate: " + type_name in output
                          and "Traceback" not in output
                          and "$(" not in output and "`" not in output
                          and (not carries_value
                               or inert(boundary_value) in output)
                          and paste_probe(output, "boundary-" + tag) == [])

                # QA round 13 A, the RUNTIME closure for QA round 12 MAJOR,
                # driven on the real rendering code through loaded mutant
                # modules: a subclass that INHERITS the refusal constructor
                # carries its hostile template into the registered-template
                # fail-safe (the unregistered template renders as a value), and
                # a subclass that OVERRIDES the constructor carries no rendering
                # proof, so the refusal handler re-renders str(exc) at the point
                # of output and coerces its raw restore line. On the round-12
                # code both printed the raw value, so each fails with its
                # closure reverted.
                scl_repo = make_git("subclass'$(touch PWNED-SCL)`;#")
                scl_inherit = gate_source.replace(
                    journal_anchor,
                    journal_anchor + newline
                    + "        class _qa_sub(_InitPriorStoreRefusal):" + newline
                    + "            pass" + newline
                    + "        raise _qa_sub(str(root), (), [])")
                check("route gate flags the inheriting-subclass runtime mutant",
                      _init_route_violations(scl_inherit) != [])
                scl_module = load_mutant("subclass-inherit", scl_inherit)
                rc, output = drive_module_init(
                    scl_module, ["--root", str(scl_repo)])
                check("route runtime: a subclass inheriting the refusal "
                      "constructor prints its hostile template RENDERED, never "
                      "raw (QA round 12 MAJOR; QA round 13 A)",
                      rc == EXIT_ERROR and "REFUSED" in output
                      and "$(" not in output and "`" not in output
                      and inert(str(scl_repo)) in output
                      and not (base / "PWNED-SCL").exists()
                      and paste_probe(output, "subclass-inherit") == [])
                ovr_repo = make_git("override'$(touch PWNED-OVR)`;#")
                scl_override = gate_source.replace(
                    journal_anchor,
                    journal_anchor + newline
                    + "        class _qa_raw(_InitPriorStoreRefusal):" + newline
                    + "            def __init__(self, value):" + newline
                    + "                RuntimeError.__init__(self, value)" + newline
                    + "                self.restore_lines = [value]" + newline
                    + "        raise _qa_raw(str(root))")
                check("route gate flags the overriding-subclass runtime mutant",
                      _init_route_violations(scl_override) != [])
                ovr_module = load_mutant("subclass-override", scl_override)
                rc, output = drive_module_init(
                    ovr_module, ["--root", str(ovr_repo)])
                check("route runtime: a subclass overriding the refusal "
                      "constructor (no rendering proof) is re-rendered at the "
                      "point of output, its raw restore line included",
                      rc == EXIT_ERROR and "REFUSED" in output
                      and "$(" not in output and "`" not in output
                      and inert(str(ovr_repo)) in output
                      and not (base / "PWNED-OVR").exists()
                      and paste_probe(output, "subclass-override") == [])

                # QA round 9, requirement C: the third driver's capture path,
                # driven with raw-descriptor and child-process writes from a
                # loaded mutant (the gate flags its spelled routes, and the
                # capture still observes what they wrote at runtime).
                fdm_source = gate_source.replace(
                    gate_anchor,
                    gate_anchor + newline
                    + "    os.write(1, b'QA-MODULE-FD1 ')" + newline
                    + "    os.write(2, b'QA-MODULE-FD2 ')" + newline
                    + "    import subprocess as _qa_sub" + newline
                    + "    _qa_sub.run(['/bin/echo', 'QA-MODULE-CHILD'])")
                check("route gate flags the capture-probe mutant's spelled "
                      "routes",
                      _init_route_violations(fdm_source) != [])
                fdm_module = load_mutant("fd-capture-probe", fdm_source)
                fdm_repo = make_git("capture-module")
                rc, output = drive_module_init(
                    fdm_module, ["--root", str(fdm_repo)])
                check("capture potency through drive_module_init: raw descriptor "
                      "and inherited child output are captured",
                      "QA-MODULE-FD1" in output and "QA-MODULE-FD2" in output
                      and "QA-MODULE-CHILD" in output)

                # QA round 7 MINOR 4: the key-rendering arm of _init_inert_json,
                # driven with a hostile KEY (no CLI path binds one today, so this
                # in-process vector is the arm's discriminator: dropping it fails
                # here).
                hostile_key = "k$(x)`"
                hostile_item = "v$(y)"
                hostile_report = dict()
                hostile_report[hostile_key] = [hostile_item]
                rendered_report = dict()
                rendered_report[inert(hostile_key)] = [inert(hostile_item)]
                check("inert JSON renders keys through the one renderer",
                      opf._init_inert_json(hostile_report) == rendered_report)

                # QA round 7 (requirement B): the rendering constructors, pinned in
                # process. The refusal message values and the restore lines are
                # rendered at construction, and a non-line entry is coerced to the
                # line type.
                probe_exc = opf._InitPriorStoreRefusal(
                    chr(123) + chr(125),
                    ("$(touch QA-X)`;",), ["raw $(touch QA-X)"])
                check("refusal constructor renders message values and coerces "
                      "restore lines",
                      str(probe_exc) == inert("$(touch QA-X)`;")
                      and list(probe_exc.restore_lines)
                      == [inert("raw $(touch QA-X)")]
                      and type(probe_exc.restore_lines[0]).__name__
                      == "_InitRestoreLine")

                # QA round 13 A: the registered-template set, held in process.
                # An UNREGISTERED template, whatever supplied it, is itself
                # rendered as a value and never formatted; on the round-12
                # constructors each template below passed through raw and its
                # braces formatted, so each check fails with the registry
                # reverted.
                qa_tpl = "qa $(touch QA-REG) " + chr(123) + chr(125) + " `x"
                qa_unreg = opf._InitPriorStoreRefusal(qa_tpl, ("v$(y)",), [])
                check("an unregistered refusal template is rendered as a value, "
                      "never formatted (QA round 13 A)",
                      str(qa_unreg) == inert(qa_tpl) + " " + inert("v$(y)")
                      and "$(" not in str(qa_unreg))
                qa_line_tpl = "qa $(x) " + chr(123) + chr(125)
                qa_line = opf._InitRestoreLine(qa_line_tpl, "v")
                check("an unregistered restore-line template is rendered as a "
                      "value, never formatted",
                      str(qa_line) == inert(qa_line_tpl) + " v")
                qa_remedy = opf._InitPriorStoreRefusal(
                    "qa-t $(a)", (), [],
                    remedy_template="qa $(z) " + chr(123) + chr(125),
                    remedy_values=("w$(q)",))
                check("an unregistered remedy template is rendered as a value, "
                      "never formatted",
                      str(qa_remedy) == inert("qa-t $(a)") + " "
                      + inert("qa $(z) " + chr(123) + chr(125))
                      + " " + inert("w$(q)"))

                # QA round 13 A: the refusal handler's point-of-output proof.
                # An untouched constructed refusal passes unchanged (idempotent
                # acceptance); a message swapped in after construction and an
                # exception that never ran the real constructor are re-rendered
                # at the point of output. With the proof check reverted to the
                # round-12 handler's trust in str(exc), the swapped-args vector
                # fails.
                qa_ok = opf._InitPriorStoreRefusal(
                    chr(123) + chr(125), ("ok",), [])
                check("the output-point proof accepts an untouched constructed "
                      "refusal unchanged (idempotent acceptance)",
                      opf._init_refusal_message(qa_ok) == "ok"
                      and opf._init_refusal_message(qa_ok) == str(qa_ok))
                qa_swap = opf._InitPriorStoreRefusal(
                    chr(123) + chr(125), ("ok",), [])
                qa_swap.args = ("$(touch QA-SWAP)`",)
                check("a message swapped in after construction (exc.args) fails "
                      "the proof and is rendered at the point of output",
                      opf._init_refusal_message(qa_swap)
                      == inert("$(touch QA-SWAP)`"))
                check("an exception that never ran the real constructor is "
                      "rendered at the point of output",
                      opf._init_refusal_message(RuntimeError("$(touch QA-RT)`"))
                      == inert("$(touch QA-RT)`"))

                # QA round 14 A (codex round-13 MAJOR; claude round-13 MEDIUM
                # 1): str() may return a str SUBCLASS, and the round-13 code
                # compared, iterated and formatted that object as it came, so
                # a subclass's __eq__, __iter__ (yielding characters that hash
                # and compare like a safe one) or __format__ decided the output
                # while its own characters printed raw, past an untouched
                # constructor proof. Every value is now converted to an exact
                # built-in str first (_init_exact_str). The classes are defined
                # HERE, outside the scanned module, so the route gate never
                # sees them; each vector is driven through the REAL refusal
                # handler by drive_cmd_init. On the round-13 code each printed
                # its payload raw, so each check fails with the conversion
                # reverted.
                qa14_open = chr(36) + chr(40)
                qa14_tick = chr(96)
                qa14_payload = ("qa14" + qa14_open + "touch QA14-PWNED)" + qa14_tick
                                + ";#" + chr(27))

                class Qa14Format(str):
                    def __format__(self, spec):
                        return ("qa" + newline + "RAW-FORMAT" + chr(27) + qa14_open
                                + "touch QA14-FMT)" + qa14_tick)

                class Qa14Eq(str):
                    def __eq__(self, other):
                        return True

                    __hash__ = str.__hash__

                class Qa14Char(str):
                    # Hashes and compares like the safe character "a", so a
                    # set-membership test admits it, while a join emits its
                    # own character.
                    def __eq__(self, other):
                        return True

                    def __hash__(self):
                        return hash("a")

                class Qa14Iter(str):
                    def __iter__(self):
                        return iter([Qa14Char(ch) for ch in str.__str__(self)])

                class Qa14Value:
                    def __str__(self):
                        return Qa14Iter(qa14_payload)

                class Qa14StrOverride(str):
                    def __str__(self):
                        return "override"

                class Qa14FormatRefusal(opf._InitPriorStoreRefusal):
                    def __str__(self):
                        return Qa14Format("safe")

                class Qa14EqRefusal(opf._InitPriorStoreRefusal):
                    def __str__(self):
                        return Qa14Eq(qa14_payload)

                class Qa14IterRefusal(opf._InitPriorStoreRefusal):
                    def __init__(self):
                        RuntimeError.__init__(self, "qa14")
                        self.restore_lines = []

                    def __str__(self):
                        return Qa14Iter(qa14_payload)

                class Qa14IterError(RuntimeError):
                    def __str__(self):
                        return Qa14Iter(qa14_payload)

                qa14_braces = chr(123) + chr(125)
                qa14_cases = [
                    ("codex Formatted, __format__ on a proven message",
                     lambda: Qa14FormatRefusal(qa14_braces, ("safe",), []),
                     "opf init: REFUSED at ", ": safe; exit 2"),
                    ("E2, __eq__ fakes the proof comparison",
                     lambda: Qa14EqRefusal(qa14_braces, ("ok",), []),
                     "opf init: REFUSED at ", ": " + inert(qa14_payload) + "; exit 2"),
                    ("E3, no proof and __iter__ spoofs the renderer",
                     Qa14IterRefusal,
                     "opf init: REFUSED at ", ": " + inert(qa14_payload) + "; exit 2"),
                    ("E4, the real constructor over a value whose str() "
                     "iterates spoofed characters",
                     lambda: opf._InitPriorStoreRefusal(
                         qa14_braces, (Qa14Value(),), []),
                     "opf init: REFUSED at ", ": " + inert(qa14_payload) + "; exit 2"),
                    ("E5, a plain RuntimeError subclass on the cannot-evaluate "
                     "path", Qa14IterError, "opf init: cannot evaluate at ",
                     "Qa14IterError(" + inert(qa14_payload) + "); exit 2"),
                ]
                for qa14_number, qa14_case in enumerate(qa14_cases):
                    qa14_tag, qa14_make, qa14_prefix, qa14_expected = qa14_case

                    def qa14_scan(git_arg, repo_arg, root_arg, make=qa14_make):
                        raise make()
                    qa14_repo = make_git("str-subclass-" + str(qa14_number))
                    rc, output = drive_cmd_init(
                        qa14_repo, dict(_init_no_prior_store=qa14_scan))
                    check("str-subclass vector through the real handler, "
                          + qa14_tag + ": only exact, rendered characters print "
                          "(QA round 14 A)",
                          rc == EXIT_ERROR and qa14_prefix in output
                          and qa14_expected in output
                          and qa14_open not in output and qa14_tick not in output
                          and chr(27) not in output and "RAW-FORMAT" not in output
                          and not (base / "QA14-PWNED").exists()
                          and paste_probe(output, "str-subclass-"
                                          + str(qa14_number)) == [])
                check("the renderer and the bare gate read a str subclass by its "
                      "own characters, never by its spoofed iteration (QA round "
                      "14 A)",
                      opf._init_inert(Qa14Iter(qa14_payload)) == inert(qa14_payload)
                      and type(opf._init_inert(Qa14Iter(qa14_payload))) is str
                      and opf._init_bare(Qa14Iter(qa14_payload)) is False)
                check("a str-subclass value is rendered from its own characters, "
                      "never from its overriding __str__ (QA round 14 A)",
                      str(opf._InitRestoreLine(qa14_braces, Qa14StrOverride("data")))
                      == "data"
                      and str(opf._InitPriorStoreRefusal(
                          qa14_braces, (Qa14StrOverride("data"),), []))
                      == "data")

                # QA round 15 A (claude round-14 MEDIUM 1): the JSON walk FAILS
                # CLOSED. The round-14 walk returned unchanged any value that
                # was not a str, list or dict, and json.dumps serialised a tuple
                # as an array of its raw strings; two routes reached it through
                # SIBLING-module behaviour only (no init function is patched
                # beyond the injected publication fault): a stat result whose
                # st_size is a tuple, and a root binding a str subclass's
                # reflected __radd__ turned into a tuple. On the round-14 code
                # both printed the payload raw, so both route checks and the
                # in-process table fail with the change reverted; the binding
                # check also fails with only the binding line reverted (its
                # value is then a walked list, not the one rendered string).
                # The int-size run is a CONTROL: an exact int passes unchanged
                # on both revisions, so it guards against over-rendering and is
                # not a discriminating vector.
                qa15_payload = ("qa15" + chr(36) + chr(40) + "touch QA15-PWNED)"
                                + chr(96) + ";#")
                qa15_store = opf._opf_store
                qa15_journal = qa15_store._journal
                qa15_real_create = opf._init_create
                qa15_real_lstat = qa15_journal._lstat_contained
                qa15_real_open_dir = qa15_store._open_dir_nofollow

                def qa15_fault_create(state):
                    def create(root_fd, relpath, data):
                        state["calls"] += 1
                        if state["calls"] >= 3:
                            state["published"] = True
                            raise RuntimeError("qa15 create fault")
                        return qa15_real_create(root_fd, relpath, data)
                    return create

                class Qa15Stat:
                    def __init__(self, size):
                        self.st_mode = stat.S_IFREG | 0o644
                        self.st_size = size

                def qa15_sized_lstat(state, size):
                    def lstat_contained(root_fd, relpath):
                        if not state["published"]:
                            return qa15_real_lstat(root_fd, relpath)
                        return Qa15Stat(size)
                    return lstat_contained

                class Qa15Radd(str):
                    def __radd__(self, other):
                        return (other, qa15_payload)

                class Qa15BindingError(RuntimeError):
                    def __repr__(self):
                        return Qa15Radd("Qa15BindingError(" + qa15_payload + ")")

                def qa15_binding_open(state):
                    def open_dir_nofollow(*args, **kwargs):
                        if state["published"]:
                            raise Qa15BindingError()
                        return qa15_real_open_dir(*args, **kwargs)
                    return open_dir_nofollow

                def qa15_drive(tag, sibling):
                    state = dict(calls=0, published=False)
                    repo = make_git(tag)
                    with contextlib.ExitStack() as stack:
                        for owner, attr, make in sibling:
                            stack.enter_context(
                                mock.patch.object(owner, attr, make(state)))
                        rc, output = drive_cmd_init(
                            repo, dict(_init_create=qa15_fault_create(state)))
                    rows = [json.loads(line) for line in output.splitlines()
                            if line.startswith("{")]
                    events = [row for row in rows
                              if row.get("event") == "partial-publication"]
                    return state, rc, output, events

                def qa15_sized(events):
                    return [entry for row in events for entry in row.get("paths", [])
                            if entry.get("state") == "different-size"]

                state, rc, output, events = qa15_drive(
                    "qa15-st-size-tuple",
                    [(qa15_journal, "_lstat_contained",
                      lambda st: qa15_sized_lstat(st, (qa15_payload,)))])
                check("a tuple a sibling module's stat returns as st_size is walked "
                      "and its strings rendered in the partial-publication JSON, "
                      "never serialised raw (QA round 15 A)",
                      state["published"] and rc == EXIT_ERROR
                      and qa15_sized(events) != []
                      and all(entry.get("bytes") == [inert(qa15_payload)]
                              for entry in qa15_sized(events))
                      and chr(36) + chr(40) not in output and chr(96) not in output
                      and not (base / "QA15-PWNED").exists()
                      and paste_probe(output, "qa15-st-size-tuple") == [])
                state, rc, output, events = qa15_drive(
                    "qa15-st-size-int",
                    [(qa15_journal, "_lstat_contained",
                      lambda st: qa15_sized_lstat(st, 999999))])
                check("CONTROL: an exact int st_size passes the JSON walk unchanged, "
                      "as a JSON number (QA round 15 A; passes on both revisions)",
                      state["published"] and rc == EXIT_ERROR
                      and qa15_sized(events) != []
                      and all(type(entry.get("bytes")) is int
                              and entry.get("bytes") == 999999
                              for entry in qa15_sized(events)))
                state, rc, output, events = qa15_drive(
                    "qa15-radd-binding",
                    [(qa15_store, "_open_dir_nofollow", qa15_binding_open)])
                check("the root-binding exception text is an exact str before the "
                      "concatenation, so a str subclass's reflected __radd__ cannot "
                      "decide the binding, and the binding renders as one string "
                      "(QA round 15 A)",
                      state["published"] and rc == EXIT_ERROR and events != []
                      and all(row.get("root_binding")
                              == inert("cannot-confirm-root: Qa15BindingError("
                                       + qa15_payload + ")")
                              for row in events)
                      and chr(36) + chr(40) not in output and chr(96) not in output
                      and not (base / "QA15-PWNED").exists()
                      and paste_probe(output, "qa15-radd-binding") == [])

                class Qa15Int(int):
                    def __str__(self):
                        return qa15_payload

                qa15_walked = opf._init_inert_json(dict(
                    n=None, b=True, i=7, f=1.5, t=(qa15_payload, 3),
                    s=Qa15Int(5), o=qa15_payload.encode("ascii")))
                qa15_expected = dict(
                    n=None, b=True, i=7, f=1.5, t=[inert(qa15_payload), 3],
                    s=inert(qa15_payload),
                    o=inert(repr(qa15_payload.encode("ascii"))))
                check("the JSON walk passes only exact int, float, bool and None, "
                      "walks a tuple, and renders any other value as text (QA "
                      "round 15 A)",
                      qa15_walked == qa15_expected
                      and all(type(qa15_walked[key]) is type(qa15_expected[key])
                              for key in qa15_expected)
                      and opf._init_inert_json(dict([(1, "a")]))
                      == dict([("1", "a")]))

                # QA round 15 B (codex round-14 MINOR): a second interrupt
                # landing on the handler's PUBLICATION TEST, which the round-14
                # handler ran before entering the disclosure guard, escaped to
                # the last-resort boundary and dropped the partial-publication
                # sentence. The guard is now the handler's first statement. The
                # interrupt is injected by a trace function on the first line of
                # _init_main that spells the publication test after its
                # BaseException handler opens, found in the live source so the
                # vector follows the line wherever it sits; on the round-14
                # handler only the last-resort line printed, so this check fails
                # with the guard move reverted.
                qa15_lines = (Path(opf.__file__).resolve()
                              .read_text(encoding="utf-8").splitlines())
                qa15_code = opf._init_main.__code__
                qa15_target = None
                qa15_in_handler = False
                for qa15_number in range(qa15_code.co_firstlineno, len(qa15_lines) + 1):
                    qa15_text = qa15_lines[qa15_number - 1]
                    if qa15_text.startswith("    except BaseException as exc:"):
                        qa15_in_handler = True
                    elif (qa15_in_handler and qa15_text.strip()
                          == "if publishing and root_fd is not None:"):
                        qa15_target = qa15_number
                        break

                def qa15_trace(frame, event, arg):
                    if frame.f_code is not qa15_code:
                        return None
                    if (event == "line" and frame.f_lineno == qa15_target
                            and not qa15_trace.fired):
                        qa15_trace.fired = True
                        raise KeyboardInterrupt()
                    return qa15_trace
                qa15_trace.fired = False

                qa15_kb_state = dict(calls=0)

                def qa15_kb_create(root_fd, relpath, data):
                    qa15_kb_state["calls"] += 1
                    if qa15_kb_state["calls"] >= 3:
                        raise KeyboardInterrupt()
                    return qa15_real_create(root_fd, relpath, data)

                qa15_kb_repo = make_git("qa15-publication-test-interrupt")
                qa15_prior_trace = sys.gettrace()
                sys.settrace(qa15_trace)
                try:
                    rc, output = drive_cmd_init(
                        qa15_kb_repo, dict(_init_create=qa15_kb_create))
                finally:
                    sys.settrace(qa15_prior_trace)
                check("a second interrupt on the handler's publication test still "
                      "prints the fallback line and the fixed partial-publication "
                      "sentence, then exits 2 (QA round 15 B)",
                      qa15_target is not None and qa15_trace.fired
                      and rc == EXIT_ERROR
                      and "publication may be partial" in output
                      and "detail could not be reported" in output
                      and "last-resort boundary" not in output)

                # QA round 15 B, the pre-publication arm of the same guard: a
                # second interrupt in the cannot-evaluate line before publication
                # escaped to the last-resort boundary on the round-14 handler;
                # the guard now catches it and a fixed line says no publication
                # was attempted, so this check fails with the move reverted.
                qa15_real_echo = opf._init_echo

                def qa15_pre_scan(git_arg, repo_arg, root_arg):
                    raise RuntimeError("qa15 preflight fault")

                def qa15_pre_echo(line, err=False):
                    if (not qa15_pre_echo.fired
                            and str(line).startswith("opf init: cannot evaluate at ")):
                        qa15_pre_echo.fired = True
                        raise KeyboardInterrupt()
                    return qa15_real_echo(line, err=err)
                qa15_pre_echo.fired = False
                rc, output = drive_cmd_init(
                    make_git("qa15-preflight-second-interrupt"),
                    dict(_init_no_prior_store=qa15_pre_scan,
                         _init_echo=qa15_pre_echo))
                check("before publication, a second interrupt in the cannot-evaluate "
                      "line is caught by the same guard, which says no publication "
                      "was attempted, then exits 2 (QA round 15 B)",
                      rc == EXIT_ERROR and qa15_pre_echo.fired
                      and "opf init: a second failure interrupted this report; "
                      "preflight refused; no publication attempted." in output
                      and "last-resort boundary" not in output
                      and "publication may be partial" not in output)

                # QA round 15 C (claude round-14 MINOR 2): the argument loop reads
                # each token as its exact str before comparing or gating it. On
                # the round-14 loop a token whose own __eq__ answered "--root"
                # acted as --root, and a value whose startswith hid its leading
                # dash passed the gate; both checks fail with the conversion
                # reverted.
                class Qa15EqToken(str):
                    def __eq__(self, other):
                        return str.__eq__("--root", other)

                    __hash__ = str.__hash__

                class Qa15DashValue(str):
                    def startswith(self, *args):
                        return False

                qa15_missing = str(base / "qa15-argv-missing")
                rc, output = fd_capture(lambda: opf._cmd_init(
                    [Qa15EqToken("--qa15-not-root"), qa15_missing]))
                check("an argument token is compared as its exact str: a str subclass "
                      "whose __eq__ answers --root is an unrecognized argument (QA "
                      "round 15 C)",
                      rc == EXIT_ERROR
                      and "opf init: unrecognized argument, inert-escaped: "
                      + inert("--qa15-not-root") in output)
                rc, output = fd_capture(lambda: opf._cmd_init(
                    ["--root", Qa15DashValue("-qa15")]))
                check("a --root value is gated as its exact str: a str subclass "
                      "whose startswith hides its leading dash is refused (QA round "
                      "15 C)",
                      rc == EXIT_ERROR
                      and "requires a non-empty directory argument" in output
                      and inert("-qa15") in output)

                # QA round 15 C (claude round-14 MINOR 3): the emitter writes a
                # line by its exact characters. No real init line reaches it as
                # a str subclass with its own __str__ today, so this in-process
                # call pins the emitter itself: with its conversion reverted to
                # str(line), the overriding __str__ is written instead.
                class Qa15Line(str):
                    def __str__(self):
                        return "qa15-override"

                rc, output = fd_capture(
                    lambda: opf._init_echo(Qa15Line("qa15-own-line")))
                check("the emitter writes a line by its own exact characters, never "
                      "by its overriding __str__ (QA round 15 C)",
                      output == "qa15-own-line" + chr(10))

                # QA round 13 A, the SYNC check: every template a rendered-type
                # construction site in the real source spells, as a string
                # literal or through a name bound by literal assignments, is a
                # member of the registry (the two coercion templates "" and {}
                # included), so a site added or edited without registering it
                # fails HERE, before the constructor fail-safe changes the
                # byte-compared outputs.
                sync_tree = ast.parse(gate_source)
                sync_templates = set()
                sync_names = set()
                for sync_node in ast.walk(sync_tree):
                    if (isinstance(sync_node, ast.Call)
                            and isinstance(sync_node.func, ast.Name)
                            and sync_node.func.id in _ROUTE_RENDERED):
                        sync_positions = _ROUTE_RENDERED_TEMPLATES[
                            sync_node.func.id]
                        sync_exprs = [sync_node.args[position]
                                      for position in sync_positions
                                      if position < len(sync_node.args)]
                        sync_exprs += [
                            kw.value for kw in sync_node.keywords
                            if kw.arg in frozenset(sync_positions.values())]
                        for sync_expr in sync_exprs:
                            if (isinstance(sync_expr, ast.Constant)
                                    and isinstance(sync_expr.value, str)):
                                sync_templates.add(sync_expr.value)
                            elif isinstance(sync_expr, ast.Name):
                                sync_names.add(sync_expr.id)
                for sync_node in ast.walk(sync_tree):
                    if (isinstance(sync_node, ast.Assign)
                            and len(sync_node.targets) == 1
                            and isinstance(sync_node.targets[0], ast.Name)
                            and sync_node.targets[0].id in sync_names
                            and isinstance(sync_node.value, ast.Constant)
                            and isinstance(sync_node.value.value, str)):
                        sync_templates.add(sync_node.value.value)
                check("template sync: every construction-site template in the "
                      "real source is registered (QA round 13 A)",
                      len(sync_templates) > 0
                      and "" in opf._INIT_TEMPLATES
                      and chr(123) + chr(125) in opf._INIT_TEMPLATES
                      and sync_templates <= opf._INIT_TEMPLATES)

                # QA round 9, requirement B (codex MAJOR 2, claude MAJOR 1): a
                # FORGED instance of the exact _InitRestoreLine type, minted around
                # a raw string with str.__new__, must never ride a pass-through.
                # The constructor renders EVERY value (no type check), the refusal
                # constructor and the handler accept only the constructor's private
                # rendering proof, and a genuine constructed line passes those two
                # unchanged (the acceptance is idempotent for real lines), so
                # re-coercion never double-escapes real output.
                forged_raw = "v; echo " + paste_marker + " #`$(touch QA-FORGED)"
                forged = str.__new__(opf._InitRestoreLine, forged_raw)
                check("a forged exact-type restore line is rendered when it is a "
                      "constructor VALUE, never passed through",
                      str(opf._InitRestoreLine("{}", forged)) == inert(forged_raw))
                forged_exc = opf._InitPriorStoreRefusal(
                    "qa forged", (), [str.__new__(opf._InitRestoreLine,
                                                  forged_raw)])
                check("a forged exact-type restore line is re-rendered by the "
                      "refusal constructor",
                      list(forged_exc.restore_lines) == [inert(forged_raw)])
                genuine = opf._InitRestoreLine("{}", forged_raw)
                genuine_exc = opf._InitPriorStoreRefusal("qa", (), [genuine])
                check("a genuine constructed line passes the rendering-proof "
                      "check unchanged (idempotent acceptance)",
                      list(genuine_exc.restore_lines) == [genuine]
                      and str(genuine) == inert(forged_raw))

                # The handler accepts only the line type: a raw string appended to
                # the caught exception AFTER construction (past the constructor's
                # coercion) is re-rendered by the handler, never printed raw.
                # DISCRIMINATOR: a handler that prints restore lines unchecked fails
                # the no-raw assertions and the probe.
                def post_append_refusal(git_arg, repo_arg, root_arg):
                    smuggled = opf._InitPriorStoreRefusal(
                        "qa post-append refusal", (), [])
                    smuggled.restore_lines.append(
                        "$(touch PWNED-HANDLER); `touch PWNED-HANDLER`")
                    smuggled.restore_lines.append(str.__new__(
                        opf._InitRestoreLine,
                        "$(touch PWNED-FORGED); `touch PWNED-FORGED`"))
                    raise smuggled

                handler_repo = make_git("route-handler-coercion")
                rc, output = drive_cmd_init(
                    handler_repo, dict(_init_no_prior_store=post_append_refusal))
                check("the refusal handler re-renders a restore line smuggled in "
                      "after construction, a forged exact-type instance included "
                      "(QA round 9, requirement B)",
                      rc == EXIT_ERROR and "REFUSED" in output
                      and "$(" not in output and "`" not in output
                      and inert("$(touch PWNED-HANDLER); `touch PWNED-HANDLER`")
                      in output
                      and inert("$(touch PWNED-FORGED); `touch PWNED-FORGED`")
                      in output
                      and not (base / "PWNED-FORGED").exists()
                      and paste_probe(output, "handler-coercion") == [])

                # QA round 7 MAJOR 2 (claude): an adopter-kept NON-STORE file in a
                # first-level .working subdirectory (the adoption "keep" disposition
                # leaves such files in place). notes/ holds no structural file in the
                # tree or in history, so it is NOT a store: the refusal must carry NO
                # PARTIAL block and never name notes/. DISCRIMINATOR: the round-7
                # code expected both structural files in every first-level
                # subdirectory holding a direct file and printed a false PARTIAL
                # naming notes/, failing the complete text here.
                nk = make_git("ancestry-kept-file")
                nk_machine = nk / working / machine_name
                nk_machine.mkdir(parents=True)
                (nk_machine / _opf_store.MANIFEST_NAME).write_bytes(b"x = 1\n")
                (nk_machine / _opf_check.COUNTERS_NAME).write_bytes(b"y = 1\n")
                nk_notes = nk / working / "notes"
                nk_notes.mkdir()
                (nk_notes / "plan.md").write_bytes(b"adopter notes\n")
                (nk / _opf_store.POINTER_REL).write_bytes(
                    b'[store]\ntarget = "dir:."\n')
                git_call(nk, ["--literal-pathspecs", "add", "-A"])
                git_call(nk, ["-c", "user.email=t@t", "-c", "user.name=t",
                              "commit", "-m", "store with kept notes"])
                nk_commit = head_of(nk)
                git_call(nk, ["rm", "-r", "-q", "--",
                              working, _opf_store.POINTER_REL])
                git_call(nk, ["-c", "user.email=t@t", "-c", "user.name=t",
                              "commit", "-m", "drop the store"])
                rc, output = run(nk)
                check("ancestry notes-keep: a kept non-store file causes NO PARTIAL "
                      "block and is never named",
                      rc == EXIT_ERROR and output == prior_store_output(
                          nk, nk, nk_commit, [".opf.toml", ".working"], True)
                      and "PARTIAL" not in output
                      and inert(working + "/notes") not in output)
                check("ancestry notes-keep: the printed command restores the store "
                      "cleanly",
                      run_restore(output) == 0
                      and (nk_notes / "plan.md").is_file()
                      and (nk_machine / _opf_check.COUNTERS_NAME).is_file())

                # QA round 7 MINOR 3 (claude): a first-level subdirectory holding
                # ONLY deeper directories is not a store either; beside it, a real
                # store with a missing counters.toml still discloses its own gap, in
                # the NEW scope wording (a store is a subdirectory that directly
                # holds a structural file, in the tree or in history).
                # DISCRIMINATOR: the round-7 scope sentence differs, so the complete
                # text fails against the round-7 code.
                no2 = make_git("ancestry-nested-only")
                no2_machine = no2 / working / machine_name
                no2_machine.mkdir(parents=True)
                (no2_machine / _opf_store.MANIFEST_NAME).write_bytes(b"x = 1\n")
                no2_nested = no2 / working / "m2" / "archive" / "2026"
                no2_nested.mkdir(parents=True)
                (no2_nested / "archive.toml").write_bytes(b"a = 1\n")
                (no2 / _opf_store.POINTER_REL).write_bytes(
                    b'[store]\ntarget = "dir:."\n')
                git_call(no2, ["--literal-pathspecs", "add", "-A"])
                git_call(no2, ["-c", "user.email=t@t", "-c", "user.name=t",
                               "commit", "-m", "store with nested-only subdir"])
                no2_commit = head_of(no2)
                git_call(no2, ["rm", "-r", "-q", "--",
                               working, _opf_store.POINTER_REL])
                git_call(no2, ["-c", "user.email=t@t", "-c", "user.name=t",
                               "commit", "-m", "drop the store"])
                rc, output = run(no2)
                check("ancestry nested-only: the PARTIAL block names the real "
                      "store's gap in the new scope wording and never names the "
                      "nested-only subdirectory",
                      rc == EXIT_ERROR and output == prior_store_output(
                          no2, no2, no2_commit, [".opf.toml", ".working"], True,
                          gaps=[working + "/" + machine_name + "/"
                                + _opf_check.COUNTERS_NAME])
                      and inert(working + "/m2") not in output)

                # QA round 7 MEDIUM (codex): a store known ONLY from history
                # (old-machine/manifest.toml was committed and deleted before the
                # named commit) expects BOTH structural files, so the PARTIAL block
                # names its counters.toml too. DISCRIMINATOR: the round-7 code added
                # only the touched path itself and omitted the counters, failing the
                # complete text here.
                om = make_git("ancestry-old-manifest-history")
                om_machine = om / working / machine_name
                om_machine.mkdir(parents=True)
                (om_machine / _opf_store.MANIFEST_NAME).write_bytes(b"x = 1\n")
                (om_machine / _opf_check.COUNTERS_NAME).write_bytes(b"y = 1\n")
                om_old = om / working / "old-machine"
                om_old.mkdir()
                (om_old / _opf_store.MANIFEST_NAME).write_bytes(b"z = 1\n")
                (om / _opf_store.POINTER_REL).write_bytes(
                    b'[store]\ntarget = "dir:."\n')
                git_call(om, ["--literal-pathspecs", "add", "-A"])
                git_call(om, ["-c", "user.email=t@t", "-c", "user.name=t",
                              "commit", "-m", "store with an old machine subdir"])
                git_call(om, ["rm", "-q", "--",
                              working + "/old-machine/" + _opf_store.MANIFEST_NAME])
                git_call(om, ["-c", "user.email=t@t", "-c", "user.name=t",
                              "commit", "-m", "drop the old manifest"])
                om_commit = head_of(om)
                git_call(om, ["rm", "-r", "-q", "--",
                              working, _opf_store.POINTER_REL])
                git_call(om, ["-c", "user.email=t@t", "-c", "user.name=t",
                              "commit", "-m", "drop the store"])
                rc, output = run(om)
                om_gaps = sorted([
                    working + "/old-machine/" + _opf_check.COUNTERS_NAME,
                    working + "/old-machine/" + _opf_store.MANIFEST_NAME])
                check("ancestry old-manifest-history: a store found only in history "
                      "expects BOTH structural files",
                      rc == EXIT_ERROR and output == prior_store_output(
                          om, om, om_commit, [".opf.toml", ".working"], True,
                          gaps=om_gaps))

                # The store definition is stated in the SAME words at the two sites
                # users read it: the PARTIAL scope text (asserted by the complete
                # texts above) and OPF-QUICKSTART.md (asserted here with backticks
                # stripped and whitespace collapsed).
                store_rule_a = ("treats a first-level .working subdirectory as a "
                                "machine store only when it directly holds "
                                "manifest.toml or counters.toml")
                store_rule_b = ("a store found either way is expected to hold both "
                                "files, and a subdirectory holding only other files "
                                "or only deeper directories is neither expected nor "
                                "named")
                store_rule_c = ("a structural file counts at its rename destination "
                                "instead of its old path only when the recorded "
                                "destination blob id equals the source blob id (an "
                                "exact byte-identical move; git's similarity score "
                                "of 100 alone does not prove that, since reordered "
                                "lines also score 100), no older first-parent "
                                "record touched the destination, and the renaming "
                                "commit's tree holds no structural file under the "
                                "source subdirectory")
                store_rule_d = ("reads as a deletion of its source, which stays "
                                "expected, fail-safe")
                quickstart_flat = " ".join(
                    quickstart.read_text(encoding="utf-8").replace("`", "").split())
                check("ancestry: OPF-QUICKSTART.md states the store definition",
                      store_rule_a in quickstart_flat
                      and store_rule_b in quickstart_flat
                      and store_rule_c in quickstart_flat
                      and store_rule_d in quickstart_flat)
                check("ancestry: the PARTIAL scope text states the store definition",
                      store_rule_a in " ".join(output.split())
                      and store_rule_b in " ".join(output.split())
                      and store_rule_c in " ".join(output.split())
                      and store_rule_d in " ".join(output.split()))

                # QA round 8 MINOR 4 (claude; already in the round-7 parent): a
                # machine subdirectory renamed WHOLE with git mv, the store then
                # deleted. The named commit's tree holds the complete store at its
                # NEW name, so the restore is complete; the walk's rename detection
                # counts each structural file at its destination and the refusal
                # carries NO PARTIAL block and never names the old subdirectory.
                # DISCRIMINATOR: the name-only walk read the old paths as expected
                # and printed a false PARTIAL naming both of them, whose remedy
                # ("supply each missing path") would create a second store.
                rnm = make_git("ancestry-renamed-machine")
                rc, output = init_store(rnm)
                check("ancestry renamed-machine fixture first init succeeds",
                      rc == EXIT_OK)
                git_call(rnm, ["--literal-pathspecs", "add", "-A"])
                git_call(rnm, ["-c", "user.email=t@t", "-c", "user.name=t",
                               "commit", "-m", "store"])
                git_call(rnm, ["mv", working + "/" + machine_name,
                               working + "/renamed-machine"])
                git_call(rnm, ["-c", "user.email=t@t", "-c", "user.name=t",
                               "commit", "-m", "rename the machine subdirectory"])
                rnm_named = head_of(rnm)
                git_call(rnm, ["rm", "-r", "-q", "--",
                               working, _opf_store.POINTER_REL])
                git_call(rnm, ["-c", "user.email=t@t", "-c", "user.name=t",
                               "commit", "-m", "drop the store"])
                rc, output = run(rnm)
                check("ancestry renamed-machine: the complete refusal carries NO "
                      "PARTIAL block and never names the old subdirectory",
                      rc == EXIT_ERROR and output == prior_store_output(
                          rnm, rnm, rnm_named, [".opf.toml", ".working"], True)
                      and "PARTIAL" not in output
                      and working + "/" + machine_name + "/" not in output)
                check("ancestry renamed-machine: the printed command restores the "
                      "complete store at its new name, as the refusal says",
                      run_restore(output) == 0
                      and (rnm / working / "renamed-machine"
                           / _opf_check.COUNTERS_NAME).is_file()
                      and (rnm / working / "renamed-machine"
                           / _opf_store.MANIFEST_NAME).is_file()
                      and not (rnm / working / machine_name).exists())

                # The rename FAIL-SAFE arm: structural files renamed OUT of .working
                # (the destination matches no store pathspec) are reported by git as
                # plain deletions of their source, so that store still reads as
                # deleted and the refusal still says PARTIAL and names both paths.
                # DISCRIMINATOR: a walk that skipped every rename source, wherever
                # its destination landed, would print no PARTIAL block here.
                rno = make_git("ancestry-renamed-out")
                rc, output = init_store(rno)
                check("ancestry renamed-out fixture first init succeeds",
                      rc == EXIT_OK)
                rno_zeta = rno / working / "zeta-machine"
                rno_zeta.mkdir()
                (rno_zeta / _opf_store.MANIFEST_NAME).write_bytes(b"x = 1\n")
                (rno_zeta / _opf_check.COUNTERS_NAME).write_bytes(b"y = 1\n")
                git_call(rno, ["--literal-pathspecs", "add", "-A"])
                git_call(rno, ["-c", "user.email=t@t", "-c", "user.name=t",
                               "commit", "-m", "store with a second machine subdir"])
                (rno / "backup").mkdir()
                git_call(rno, ["mv", working + "/zeta-machine/"
                               + _opf_store.MANIFEST_NAME, "backup/zm.toml"])
                git_call(rno, ["mv", working + "/zeta-machine/"
                               + _opf_check.COUNTERS_NAME, "backup/zc.toml"])
                git_call(rno, ["-c", "user.email=t@t", "-c", "user.name=t",
                               "commit", "-m", "move the second subdir out"])
                rno_named = head_of(rno)
                git_call(rno, ["rm", "-r", "-q", "--",
                               working, _opf_store.POINTER_REL])
                git_call(rno, ["-c", "user.email=t@t", "-c", "user.name=t",
                               "commit", "-m", "drop the store"])
                # git mv left the emptied, untracked zeta-machine directory on
                # disk, which git rm does not prune; drop it so init reaches the
                # ancestry scan rather than the foreign-content refusal.
                if rno_zeta.exists():
                    rno_zeta.rmdir()
                if (rno / working).exists():
                    (rno / working).rmdir()
                rc, output = run(rno)
                rno_gaps = sorted([
                    working + "/zeta-machine/" + _opf_check.COUNTERS_NAME,
                    working + "/zeta-machine/" + _opf_store.MANIFEST_NAME])
                check("ancestry renamed-out: a store renamed out of .working still "
                      "reads as deleted and the refusal says PARTIAL, naming both "
                      "structural paths",
                      rc == EXIT_ERROR and output == prior_store_output(
                          rno, rno, rno_named, [".opf.toml", ".working"], True,
                          gaps=rno_gaps))

                # QA round 9 codex MEDIUM 4 (requirement D): a committed filename
                # may legally hold the 0x01 byte the history walk's header
                # sentinel uses. The walk parses git's NUL-delimited records
                # positionally, so this name never splits a record, and the store
                # it names (known from history: its directory was deleted whole in
                # an earlier commit) is still disclosed as a PARTIAL restore.
                # DISCRIMINATOR: the round-9 parent split the whole stream on
                # 0x01 and refused this previously supported disclosure as
                # cannot-evaluate.
                x01 = make_git("ancestry-x01-filename")
                x01_machine = x01 / working / machine_name
                x01_machine.mkdir(parents=True)
                (x01_machine / _opf_store.MANIFEST_NAME).write_bytes(b"x = 1\n")
                (x01_machine / _opf_check.COUNTERS_NAME).write_bytes(b"y = 1\n")
                x01_name = "v" + chr(1) + "x"
                x01_dir = x01 / working / x01_name
                x01_dir.mkdir()
                (x01_dir / _opf_store.MANIFEST_NAME).write_bytes(b"z = 1\n")
                (x01 / _opf_store.POINTER_REL).write_bytes(
                    b'[store]\ntarget = "dir:."\n')
                git_call(x01, ["--literal-pathspecs", "add", "-A"])
                git_call(x01, ["-c", "user.email=t@t", "-c", "user.name=t",
                               "commit", "-m", "store with an x01-named subdir"])
                git_call(x01, ["rm", "-r", "-q", "--", working + "/" + x01_name])
                git_call(x01, ["-c", "user.email=t@t", "-c", "user.name=t",
                               "commit", "-m", "drop the x01 subdir"])
                x01_commit = head_of(x01)
                git_call(x01, ["rm", "-r", "-q", "--",
                               working, _opf_store.POINTER_REL])
                git_call(x01, ["-c", "user.email=t@t", "-c", "user.name=t",
                               "commit", "-m", "drop the store"])
                rc, output = run(x01)
                x01_gaps = sorted([
                    working + "/" + x01_name + "/" + _opf_check.COUNTERS_NAME,
                    working + "/" + x01_name + "/" + _opf_store.MANIFEST_NAME])
                check("ancestry x01 filename: the 0x01-named store from history "
                      "is still disclosed as PARTIAL, with the byte rendered",
                      rc == EXIT_ERROR and output == prior_store_output(
                          x01, x01, x01_commit, [".opf.toml", ".working"], True,
                          gaps=x01_gaps)
                      and inert(working + "/" + x01_name) in output)

                # QA round 9 claude MEDIUM 2 (requirement E): one commit deletes a
                # store and creates a SIMILAR store beside it; git's rename
                # detection pairs the structural files by content similarity
                # (counters here differ in one line of twenty-one, manifests are
                # identical), yet the deleted store's counters are gone, exactly
                # the spec 8.2 counter-restart risk the refusal exists to surface.
                # The walk releases a rename source only on an exact (R100) pair
                # whose destination no older record touched and whose commit
                # leaves no structural file in the source subdirectory; the
                # counters pair is inexact, so aa-machine stays expected and the
                # refusal is PARTIAL, naming both of its structural files.
                # DISCRIMINATOR: the round-9 parent read the pairing as a rename
                # and reported the lost store nowhere (fails open); the genuine
                # whole-store git mv stays non-PARTIAL in the renamed-machine
                # fixture above.
                f2 = make_git("ancestry-rename-similar")
                f2_aa = f2 / working / "aa-machine"
                f2_aa.mkdir(parents=True)
                (f2_aa / _opf_store.MANIFEST_NAME).write_bytes(b"x = 1\n")
                (f2_aa / _opf_check.COUNTERS_NAME).write_bytes(
                    b"shared = 1\n" * 20 + b"bi = 7\n")
                (f2 / _opf_store.POINTER_REL).write_bytes(
                    b'[store]\ntarget = "dir:."\n')
                git_call(f2, ["--literal-pathspecs", "add", "-A"])
                git_call(f2, ["-c", "user.email=t@t", "-c", "user.name=t",
                              "commit", "-m", "store"])
                git_call(f2, ["rm", "-r", "-q", "--", working + "/aa-machine"])
                f2_bb = f2 / working / "bb-machine"
                f2_bb.mkdir(parents=True)
                (f2_bb / _opf_store.MANIFEST_NAME).write_bytes(b"x = 1\n")
                (f2_bb / _opf_check.COUNTERS_NAME).write_bytes(
                    b"shared = 1\n" * 20 + b"bi = 0\n")
                git_call(f2, ["--literal-pathspecs", "add", "-A"])
                git_call(f2, ["-c", "user.email=t@t", "-c", "user.name=t",
                              "commit", "-m", "swap in a similar store"])
                f2_named = head_of(f2)
                git_call(f2, ["rm", "-r", "-q", "--",
                              working, _opf_store.POINTER_REL])
                git_call(f2, ["-c", "user.email=t@t", "-c", "user.name=t",
                              "commit", "-m", "drop the store"])
                rc, output = run(f2)
                f2_gaps = sorted([
                    working + "/aa-machine/" + _opf_check.COUNTERS_NAME,
                    working + "/aa-machine/" + _opf_store.MANIFEST_NAME])
                check("ancestry rename-similar: a store deleted beside a similar "
                      "created store stays expected and the refusal is PARTIAL, "
                      "naming both of its structural files (fail-safe)",
                      rc == EXIT_ERROR and output == prior_store_output(
                          f2, f2, f2_named, [".opf.toml", ".working"], True,
                          gaps=f2_gaps))

                # QA round 10 MINOR 3 (claude F3): git's similarity score is a
                # multiset of line hashes, so a renamed counters.toml whose
                # lines were REORDERED still reports R100 while its bytes (and
                # its blob id) differ, and the round-9 rule, which trusted the
                # score, released the source store whose content had changed.
                # The walk now reads --raw and releases a rename source only
                # when the record's destination blob id equals the source blob
                # id, so aa-machine stays expected here and the refusal is
                # PARTIAL, naming both of its structural files (the manifest
                # pair IS byte-identical, but a store found either way is
                # expected to hold both). The R100 premise is asserted from
                # git's own --raw answer, so a git whose scoring changes fails
                # the premise rather than silently weakening the vector.
                # DISCRIMINATOR: the round-9 score-trusting rule released
                # aa-machine and printed no PARTIAL block; the byte-identical
                # whole-store git mv stays non-PARTIAL in the renamed-machine
                # fixture above.
                f3 = make_git("ancestry-rename-reordered")
                f3_aa = f3 / working / "aa-machine"
                f3_aa.mkdir(parents=True)
                (f3_aa / _opf_store.MANIFEST_NAME).write_bytes(b"x = 1\n")
                f3_lines = [b"shared%02d = 1\n" % n for n in range(20)]
                (f3_aa / _opf_check.COUNTERS_NAME).write_bytes(
                    b"".join(f3_lines) + b"bi = 7\n")
                (f3 / _opf_store.POINTER_REL).write_bytes(
                    b'[store]\ntarget = "dir:."\n')
                git_call(f3, ["--literal-pathspecs", "add", "-A"])
                git_call(f3, ["-c", "user.email=t@t", "-c", "user.name=t",
                              "commit", "-m", "store"])
                git_call(f3, ["rm", "-r", "-q", "--", working + "/aa-machine"])
                f3_bb = f3 / working / "bb-machine"
                f3_bb.mkdir(parents=True)
                (f3_bb / _opf_store.MANIFEST_NAME).write_bytes(b"x = 1\n")
                (f3_bb / _opf_check.COUNTERS_NAME).write_bytes(
                    b"bi = 7\n" + b"".join(reversed(f3_lines)))
                git_call(f3, ["--literal-pathspecs", "add", "-A"])
                git_call(f3, ["-c", "user.email=t@t", "-c", "user.name=t",
                              "commit", "-m", "swap in a reordered store"])
                f3_named = head_of(f3)
                f3_raw = git_call(f3, ["log", "--first-parent", "--raw",
                                       "--no-abbrev", "--find-renames", "-z",
                                       "--max-count=1", f3_named, "--",
                                       ":(glob)" + working + "/*/"
                                       + _opf_check.COUNTERS_NAME])
                check("ancestry rename-reordered: git itself reports the "
                      "reordered counters pair as R100 (the fixture premise)",
                      b" R100\x00" in f3_raw)
                git_call(f3, ["rm", "-r", "-q", "--",
                              working, _opf_store.POINTER_REL])
                git_call(f3, ["-c", "user.email=t@t", "-c", "user.name=t",
                              "commit", "-m", "drop the store"])
                rc, output = run(f3)
                f3_gaps = sorted([
                    working + "/aa-machine/" + _opf_check.COUNTERS_NAME,
                    working + "/aa-machine/" + _opf_store.MANIFEST_NAME])
                check("ancestry rename-reordered: an R100 rename whose blob ids "
                      "differ stays expected and the refusal is PARTIAL, naming "
                      "both structural files (fail-safe)",
                      rc == EXIT_ERROR and output == prior_store_output(
                          f3, f3, f3_named, [".opf.toml", ".working"], True,
                          gaps=f3_gaps))

                # QA round 13 B (codex round-12 MEDIUM; QA round 12 E): the --raw
                # walk refuses a stream without its terminating NUL and any record
                # whose modes, status and object ids are not mutually consistent
                # with the shapes git's documented --raw format prints (the
                # derivation is stated at the parse in opf.py). Real git cannot
                # emit these streams, so each is driven through a stubbed _run_git
                # whose other answers reach working_gaps; the two well-formed
                # controls prove the stub itself parses. On the round-12 parser
                # the no-NUL, zero-mode-rename and 777777 vectors released the
                # rename source or passed the frame, and the junk-mode and
                # malformed-score refusals had no committed vector at all, so
                # every refusal here fails with its check reverted.
                raw_c1 = "1" * 40
                raw_blob_a = "a" * 40
                raw_blob_b = "b" * 40
                raw_zero = "0" * 40
                raw_absent_grafts = os.fsencode(
                    str(base / "raw-stub-absent" / "info" / "grafts"))
                raw_pointer = _opf_store.POINTER_REL

                def raw_scan(stream):
                    def fake_raw_git(git_arg, repo_arg, args, **kwargs):
                        args = list(args)
                        if args == ["rev-parse", "--verify", "--quiet", "HEAD"]:
                            return _opf_observe._GitOutcome(
                                True, 0, raw_c1.encode("ascii") + b"\n", "")
                        if args == ["rev-parse", "--is-shallow-repository"]:
                            return _opf_observe._GitOutcome(True, 0, b"false\n", "")
                        if args == ["rev-parse", "--git-path", "info/grafts"]:
                            return _opf_observe._GitOutcome(
                                True, 0, raw_absent_grafts + b"\n", "")
                        if args[:1] == ["rev-list"]:
                            return _opf_observe._GitOutcome(
                                True, 0, raw_c1.encode("ascii") + b"\n", "")
                        if args[:2] == ["--literal-pathspecs", "ls-tree"]:
                            if args[-2:] == [raw_pointer, working]:
                                out = (b"100644 blob " + raw_blob_a.encode("ascii")
                                       + b"\t" + raw_pointer.encode("ascii") + b"\x00"
                                       + b"040000 tree " + raw_blob_b.encode("ascii")
                                       + b"\t" + working.encode("ascii") + b"\x00")
                                return _opf_observe._GitOutcome(True, 0, out, "")
                            return _opf_observe._GitOutcome(True, 0, b"", "")
                        if args[:4] == ["ls-tree", "-r", "-z", "--name-only"]:
                            out = (b"new-machine/manifest.toml\x00"
                                   b"new-machine/counters.toml\x00")
                            return _opf_observe._GitOutcome(True, 0, out, "")
                        if args[:1] == ["log"]:
                            return _opf_observe._GitOutcome(True, 0, stream, "")
                        return _opf_observe._GitOutcome(
                            False, None, b"", "unexpected raw-stub git call")
                    raw_stub = base / "raw-stub-repo"
                    with mock.patch.object(opf._opf_observe, "_run_git",
                                           fake_raw_git):
                        try:
                            opf._init_no_prior_store(git, raw_stub, raw_stub)
                            return ("passed", "")
                        except Exception as exc:  # noqa: BLE001  the type is part of the verdict
                            return (type(exc).__name__, str(exc))

                raw_w = working.encode("ascii")

                def raw_rename_rec(meta, old_name, new_name):
                    return (meta + b"\x00" + raw_w + b"/" + old_name + b"\x00"
                            + raw_w + b"/" + new_name + b"\x00")

                def raw_single_rec(meta, name):
                    return meta + b"\x00" + raw_w + b"/" + name + b"\x00"

                raw_header = b"\x01" + raw_c1.encode("ascii") + b"\x00\n"
                raw_r100_a = (":100644 100644 " + raw_blob_a + " " + raw_blob_a
                              + " R100").encode("ascii")
                raw_r100_b = (":100644 100644 " + raw_blob_b + " " + raw_blob_b
                              + " R100").encode("ascii")
                raw_valid = (raw_header
                             + raw_rename_rec(raw_r100_a,
                                              b"old-machine/counters.toml",
                                              b"new-machine/counters.toml")
                             + raw_rename_rec(raw_r100_b,
                                              b"old-machine/manifest.toml",
                                              b"new-machine/manifest.toml"))
                raw_verdict, raw_message = raw_scan(raw_valid)
                check("raw-walk control: a well-formed R100 stream parses, "
                      "releases its sources and refuses non-PARTIAL",
                      raw_verdict == "_InitPriorStoreRefusal"
                      and "PARTIAL" not in raw_message)
                raw_del = (":100644 000000 " + raw_blob_a + " " + raw_zero
                           + " D").encode("ascii")
                raw_verdict, raw_message = raw_scan(
                    raw_header
                    + raw_single_rec(raw_del, b"old-machine/counters.toml")
                    + raw_single_rec(raw_del, b"old-machine/manifest.toml"))
                check("raw-walk control: a well-formed deletion stream parses and "
                      "the deleted store reads as a PARTIAL restore",
                      raw_verdict == "_InitPriorStoreRefusal"
                      and "PARTIAL" in raw_message)
                raw_vectors = [
                    ("a stream without its terminating NUL", raw_valid[:-1],
                     "lacks its terminating NUL"),
                    ("an R100 record with 000000 modes and non-zero ids "
                     "(codex round-12 MEDIUM)",
                     raw_header + raw_rename_rec(
                         (":000000 000000 " + raw_blob_a + " " + raw_blob_a
                          + " R100").encode("ascii"),
                         b"old-machine/counters.toml",
                         b"new-machine/counters.toml"),
                     "not mutually consistent"),
                    ("a 777777 mode (codex round-12 MEDIUM)",
                     raw_header + raw_rename_rec(
                         (":777777 777777 " + raw_blob_a + " " + raw_blob_a
                          + " R100").encode("ascii"),
                         b"old-machine/counters.toml",
                         b"new-machine/counters.toml"),
                     "outside git's documented set"),
                    ("an undocumented octal mode 100664",
                     raw_header + raw_single_rec(
                         (":100664 100644 " + raw_blob_a + " " + raw_blob_b
                          + " M").encode("ascii"),
                         b"old-machine/counters.toml"),
                     "outside git's documented set"),
                    ("a non-octal mode (QA round 12 E)",
                     raw_header + raw_single_rec(
                         (":garbag 100644 " + raw_blob_a + " " + raw_blob_b
                          + " M").encode("ascii"),
                         b"old-machine/counters.toml"),
                     "outside git's documented set"),
                    ("an out-of-range similarity score R999 (QA round 12 E)",
                     raw_header + raw_rename_rec(
                         (":100644 100644 " + raw_blob_a + " " + raw_blob_a
                          + " R999").encode("ascii"),
                         b"old-machine/counters.toml",
                         b"new-machine/counters.toml"),
                     "malformed similarity score"),
                    ("a non-numeric similarity score Rxyz (QA round 12 E)",
                     raw_header + raw_rename_rec(
                         (":100644 100644 " + raw_blob_a + " " + raw_blob_a
                          + " Rxyz").encode("ascii"),
                         b"old-machine/counters.toml",
                         b"new-machine/counters.toml"),
                     "malformed similarity score"),
                    ("an A record whose created side carries the all-zero id",
                     raw_header + raw_single_rec(
                         (":000000 100644 " + raw_zero + " " + raw_zero
                          + " A").encode("ascii"),
                         b"old-machine/counters.toml"),
                     "not mutually consistent"),
                    ("an A record whose source side carries a file mode",
                     raw_header + raw_single_rec(
                         (":100644 100644 " + raw_blob_a + " " + raw_blob_b
                          + " A").encode("ascii"),
                         b"old-machine/counters.toml"),
                     "not mutually consistent"),
                    ("an M record carrying the all-zero id beside a file mode",
                     raw_header + raw_single_rec(
                         (":100644 100644 " + raw_zero + " " + raw_blob_b
                          + " M").encode("ascii"),
                         b"old-machine/counters.toml"),
                     "not mutually consistent"),
                ]
                for raw_label, raw_stream, raw_fragment in raw_vectors:
                    raw_verdict, raw_message = raw_scan(raw_stream)
                    check("raw-walk refusal: " + raw_label,
                          raw_verdict == "RuntimeError"
                          and raw_fragment in raw_message)

                # QA round 14 B (codex round-13 MEDIUM): the walk reconciles
                # the two sides' FILE TYPES with the status (the derivation is
                # stated at the parse in opf.py). Each refused relationship
                # below is one real git never emits; on the round-13 parser
                # each passed the parse, and the cross-type R100 records
                # released their sources, so each refusal fails with the type
                # check reverted. Each accepted relationship is one real git
                # emits (the real-git fixture below asserts them from git's
                # own --raw answer) and must still parse; the R100 controls
                # with both structural files renamed must still release their
                # sources (no PARTIAL), the others leave old-machine expected
                # (PARTIAL).
                nl_b = chr(10).encode("ascii")
                nul_b = chr(0).encode("ascii")
                raw_manifest_move = raw_rename_rec(
                    raw_r100_b, b"old-machine/manifest.toml",
                    b"new-machine/manifest.toml")

                def raw_typed(src_mode, dst_mode, src_id, dst_id, status):
                    meta = (":" + src_mode + " " + dst_mode + " " + src_id + " "
                            + dst_id + " " + status).encode("ascii")
                    if status[:1] in ("R", "C"):
                        return (raw_header + raw_rename_rec(
                            meta, b"old-machine/counters.toml",
                            b"new-machine/counters.toml") + raw_manifest_move)
                    return raw_header + raw_single_rec(
                        meta, b"old-machine/counters.toml")

                raw_type_refused = [
                    ("an R100 rename from a regular file to a symbolic link with "
                     "equal ids (codex round-13 MEDIUM)",
                     "100644", "120000", raw_blob_a, raw_blob_a, "R100"),
                    ("an R100 rename from a regular file to a gitlink with equal "
                     "ids (codex round-13 MEDIUM)",
                     "100644", "160000", raw_blob_a, raw_blob_a, "R100"),
                    ("an R100 rename from a symbolic link to a gitlink with equal "
                     "ids", "120000", "160000", raw_blob_a, raw_blob_a, "R100"),
                    ("a C100 copy from an executable file to a symbolic link",
                     "100755", "120000", raw_blob_a, raw_blob_a, "C100"),
                    ("an M from a regular file to a symbolic link (codex "
                     "round-13 MEDIUM)",
                     "100644", "120000", raw_blob_a, raw_blob_b, "M"),
                    ("an M from an executable file to a gitlink",
                     "100755", "160000", raw_blob_a, raw_blob_b, "M"),
                    ("an M from a symbolic link to a gitlink",
                     "120000", "160000", raw_blob_a, raw_blob_b, "M"),
                    ("a T for an executable-bit change alone (git prints M)",
                     "100644", "100755", raw_blob_a, raw_blob_a, "T"),
                    ("a T between two symbolic links",
                     "120000", "120000", raw_blob_a, raw_blob_b, "T"),
                    ("a T between two gitlinks",
                     "160000", "160000", raw_blob_a, raw_blob_b, "T"),
                    ("an inexact R090 rename between symbolic links",
                     "120000", "120000", raw_blob_a, raw_blob_b, "R090"),
                    ("an R100 rename between gitlinks whose ids differ",
                     "160000", "160000", raw_blob_a, raw_blob_b, "R100"),
                ]
                for raw_label, src_m, dst_m, src_i, dst_i, raw_st in raw_type_refused:
                    raw_verdict, raw_message = raw_scan(
                        raw_typed(src_m, dst_m, src_i, dst_i, raw_st))
                    check("raw-walk file-type refusal (QA round 14 B), before any "
                          "source is resolved or released: " + raw_label,
                          raw_verdict == "RuntimeError"
                          and "file types of its two sides" in raw_message)
                raw_type_accepted = [
                    ("an M executable-bit change 100644 to 100755",
                     "100644", "100755", raw_blob_a, raw_blob_a, "M", True),
                    ("an R100 rename with an executable-bit change (git mv plus "
                     "chmod)", "100644", "100755", raw_blob_a, raw_blob_a, "R100",
                     False),
                    ("an R100 rename between symbolic links with equal ids",
                     "120000", "120000", raw_blob_a, raw_blob_a, "R100", False),
                    ("an R100 rename between gitlinks with equal ids",
                     "160000", "160000", raw_blob_a, raw_blob_a, "R100", False),
                    ("an inexact R090 rename between regular files",
                     "100644", "100755", raw_blob_a, raw_blob_b, "R090", True),
                    ("a T from a regular file to a symbolic link",
                     "100644", "120000", raw_blob_a, raw_blob_b, "T", True),
                    ("a T from a symbolic link to a gitlink",
                     "120000", "160000", raw_blob_a, raw_blob_b, "T", True),
                    ("a T from a gitlink to an executable file",
                     "160000", "100755", raw_blob_a, raw_blob_b, "T", True),
                    ("an M between symbolic links",
                     "120000", "120000", raw_blob_a, raw_blob_b, "M", True),
                    ("an M between gitlinks",
                     "160000", "160000", raw_blob_a, raw_blob_b, "M", True),
                ]
                for (raw_label, src_m, dst_m, src_i, dst_i, raw_st,
                     raw_partial) in raw_type_accepted:
                    raw_verdict, raw_message = raw_scan(
                        raw_typed(src_m, dst_m, src_i, dst_i, raw_st))
                    check("raw-walk file-type control (QA round 14 B), parsed and "
                          + ("left expected (PARTIAL)" if raw_partial
                             else "its sources released (no PARTIAL)") + ": "
                          + raw_label,
                          raw_verdict == "_InitPriorStoreRefusal"
                          and ("PARTIAL" in raw_message) == raw_partial)

                # QA round 14 B, the REAL-GIT derivation: git's own --raw answer
                # for each relationship the parse accepts (an executable-bit M,
                # an R100 rename with an executable-bit change, a T from an
                # executable file to a symbolic link, an R100 between symbolic
                # links, a T from a symbolic link to a gitlink, an R100 between
                # gitlinks), read with the walk's own options, then the real
                # init over that history, which must refuse on the prior store
                # without PARTIAL (every rename released) and never as a
                # cannot-evaluate.
                ty_repo = make_git("raw-file-types")
                ty_w = ty_repo / working
                (ty_w / "a").mkdir(parents=True)
                (ty_w / "a" / _opf_store.MANIFEST_NAME).write_bytes(b"x = 1" + nl_b)
                (ty_w / "a" / _opf_check.COUNTERS_NAME).write_bytes(b"".join(
                    [b"c%02d = 1" % n + nl_b for n in range(12)]))
                (ty_repo / _opf_store.POINTER_REL).write_bytes(
                    b"[store]" + nl_b + b'target = "dir:."' + nl_b)
                ty_commit = ["-c", "user.email=t@t", "-c", "user.name=t",
                             "commit", "-q", "-m"]
                git_call(ty_repo, ["--literal-pathspecs", "add", "-A"])
                git_call(ty_repo, ty_commit + ["store"])
                ty_anchor = head_of(ty_repo)
                os.chmod(ty_w / "a" / _opf_check.COUNTERS_NAME, 0o755)
                git_call(ty_repo, ["--literal-pathspecs", "add", "-A"])
                git_call(ty_repo, ty_commit + ["executable bit"])
                git_call(ty_repo, ["mv", "--", working + "/a", working + "/b"])
                os.chmod(ty_w / "b" / _opf_store.MANIFEST_NAME, 0o755)
                git_call(ty_repo, ["--literal-pathspecs", "add", "-A"])
                git_call(ty_repo, ty_commit + ["move plus executable bit"])
                (ty_w / "b" / _opf_check.COUNTERS_NAME).unlink()
                os.symlink("qa-target", str(ty_w / "b" / _opf_check.COUNTERS_NAME))
                git_call(ty_repo, ["--literal-pathspecs", "add", "-A"])
                git_call(ty_repo, ty_commit + ["to a symbolic link"])
                git_call(ty_repo, ["mv", "--", working + "/b", working + "/c"])
                git_call(ty_repo, ty_commit + ["move the symbolic link"])
                ty_c_counters = working + "/c/" + _opf_check.COUNTERS_NAME
                ty_d_counters = working + "/d/" + _opf_check.COUNTERS_NAME
                git_call(ty_repo, ["rm", "-q", "--cached", "--", ty_c_counters])
                (ty_w / "c" / _opf_check.COUNTERS_NAME).unlink()
                git_call(ty_repo, ["update-index", "--add", "--cacheinfo",
                                   "160000," + ty_anchor + "," + ty_c_counters])
                git_call(ty_repo, ty_commit + ["to a gitlink"])
                git_call(ty_repo, ["update-index", "--force-remove", "--",
                                   ty_c_counters])
                git_call(ty_repo, ["update-index", "--add", "--cacheinfo",
                                   "160000," + ty_anchor + "," + ty_d_counters])
                (ty_w / "d").mkdir()
                git_call(ty_repo, ["mv", "--",
                                   working + "/c/" + _opf_store.MANIFEST_NAME,
                                   working + "/d/" + _opf_store.MANIFEST_NAME])
                git_call(ty_repo, ty_commit + ["move the gitlink"])
                ty_named = head_of(ty_repo)
                ty_raw = git_call(ty_repo, [
                    "log", "--first-parent", "--format=%x01%H", "--raw",
                    "--no-abbrev", "--find-renames", "-z", ty_named, "--",
                    ":(glob)" + working + "/*/" + _opf_check.COUNTERS_NAME,
                    ":(glob)" + working + "/*/" + _opf_store.MANIFEST_NAME])

                def raw_relationships(stream):
                    found = []
                    for token in stream.split(nul_b):
                        fields = token.lstrip(nl_b).split(b" ")
                        if fields[0][:1] == b":" and len(fields) == 5:
                            found.append((fields[0][1:].decode("ascii"),
                                          fields[1].decode("ascii"),
                                          fields[4].decode("ascii"),
                                          fields[2] == fields[3]))
                    return found

                ty_records = raw_relationships(ty_raw)
                ty_expected = [
                    ("100644", "100755", "M", True),
                    ("100644", "100755", "R100", True),
                    ("100755", "120000", "T", False),
                    ("120000", "120000", "R100", True),
                    ("120000", "160000", "T", False),
                    ("160000", "160000", "R100", True),
                ]
                check("raw file types: git's own --raw answer carries each "
                      "relationship the parse accepts (the fixture premise)",
                      all(record in ty_records for record in ty_expected))
                git_call(ty_repo, ["rm", "-r", "-q", "--",
                                   working, _opf_store.POINTER_REL])
                git_call(ty_repo, ty_commit + ["drop the store"])
                if ty_w.exists():
                    shutil.rmtree(ty_w)
                rc, output = run(ty_repo)
                check("raw file types: init over that real history refuses on "
                      "the prior store, never as a cannot-evaluate, and every "
                      "rename released its source (no PARTIAL)",
                      rc == EXIT_ERROR and "REFUSED" in output
                      and ty_named in output
                      and "cannot evaluate" not in output
                      and "PARTIAL" not in output)

                # The premise behind refusing a cross-type rename: git reports
                # a regular file moved into a symbolic link of identical bytes
                # as a D and an A, never as an R.
                tx_repo = make_git("raw-cross-type-move")
                (tx_repo / working / "p").mkdir(parents=True)
                (tx_repo / working / "p" / _opf_check.COUNTERS_NAME).write_bytes(
                    b"qa-target")
                git_call(tx_repo, ["--literal-pathspecs", "add", "-A"])
                git_call(tx_repo, ty_commit + ["regular"])
                (tx_repo / working / "p" / _opf_check.COUNTERS_NAME).unlink()
                (tx_repo / working / "q").mkdir()
                os.symlink("qa-target", str(
                    tx_repo / working / "q" / _opf_check.COUNTERS_NAME))
                git_call(tx_repo, ["--literal-pathspecs", "add", "-A"])
                git_call(tx_repo, ty_commit + ["moved into a symbolic link"])
                tx_records = raw_relationships(git_call(tx_repo, [
                    "log", "--first-parent", "--format=%x01%H", "--raw",
                    "--no-abbrev", "--find-renames", "-z", "--max-count=1",
                    "HEAD", "--",
                    ":(glob)" + working + "/*/" + _opf_check.COUNTERS_NAME]))
                check("raw file types: git reports a regular file moved into a "
                      "symbolic link of identical bytes as a D and an A, never "
                      "an R (the premise behind refusing a cross-type rename)",
                      sorted(record[:3] for record in tx_records)
                      == [("000000", "120000", "A"), ("100644", "000000", "D")])

                # QA round 13 C (QA round 11 codex MAJOR): the bootstrap arms
                # render at their print. Each fault is INJECTED by replacing the
                # first helper import in a loaded copy, so the except arms under
                # test are the real, unmodified code; with the rendering calls in
                # those arms reverted to the round-11 raw prints, each of these
                # fails on the raw payload.
                boot_anchor = "        import _opf_worklog"
                check("bootstrap: the import anchor is present once",
                      gate_source.count(boot_anchor) == 1)

                def load_plain(tag, mutated_source):
                    plain_path = base / ("boot-mutant-" + tag + ".py")
                    plain_path.write_text(mutated_source, encoding="utf-8")
                    plain_spec = importlib.util.spec_from_file_location(
                        "opf_boot_mutant_" + tag.replace("-", "_"),
                        str(plain_path))
                    plain_module = importlib.util.module_from_spec(plain_spec)
                    plain_spec.loader.exec_module(plain_module)
                    return plain_module

                boot_payload = "qa$(touch QA-BOOT)`;!x"
                boot_cases = [
                    ("import-named",
                     "        raise ImportError(" + repr("zz") + ", name="
                     + repr(boot_payload) + ")",
                     boot_payload),
                    ("import-nameless",
                     "        raise ImportError("
                     + repr("imp " + boot_payload) + ")",
                     "imp " + boot_payload),
                    ("oserror",
                     "        raise OSError(" + repr("os " + boot_payload) + ")",
                     "os " + boot_payload),
                ]
                for boot_tag, boot_insertion, boot_carried in boot_cases:
                    boot_module = load_plain(boot_tag, gate_source.replace(
                        boot_anchor, boot_insertion + newline + boot_anchor))
                    rc, output = fd_capture(boot_module._bootstrap)
                    check("bootstrap " + boot_tag + ": the cannot-bootstrap line "
                          "is rendered inert (QA round 13 C)",
                          rc == EXIT_ERROR and "cannot bootstrap" in output
                          and inert(boot_carried) in output
                          and "$(" not in output and "`" not in output
                          and not (base / "QA-BOOT").exists()
                          and paste_probe(output, "boot-" + boot_tag) == [])

                # QA round 8 (requirement A): the fixed narrow alphabet is pinned at
                # runtime, in the renderer docstring and in OPF-QUICKSTART.md (the
                # withheld-restore text is pinned by the complete-text comparisons
                # above). DISCRIMINATOR: the round-8 widened renderer fails all
                # three (its set admits "!", quotes and the space, and its texts
                # claim readability).
                check("renderer: the runtime safe alphabet is exactly ASCII "
                      "letters, digits and . / - _, the space escaped",
                      opf._INIT_INERT_SAFE == frozenset(
                          "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
                          "abcdefghijklmnopqrstuvwxyz0123456789./-_")
                      and opf._init_inert("a b!c") == "a\\x20b\\x21c")
                check("renderer: the _init_inert docstring states the fixed narrow "
                      "alphabet",
                      "ASCII letters, digits and . / - _ only" in
                      " ".join(opf._init_inert.__doc__.split()))
                check("renderer: OPF-QUICKSTART.md states the fixed narrow alphabet",
                      "ASCII letters, digits, and . / - _ render unchanged, and "
                      "every other character" in quickstart_flat
                      and "is written as a backslash escape of its code point" in
                      quickstart_flat)

                # SPEC 8.2 ancestry, --first-parent is LOAD-BEARING: a merge built with
                # commit-tree whose tree IS the storeless side tree (TREESAME to its side
                # parent) drops the mainline store. On HEAD's first-parent line that merge
                # is the newest store-path change and its first parent held the store, so
                # init REFUSES naming the mainline store commit; an UNSCOPED rev-list walk
                # instead simplifies the merge to its TREESAME side parent, never visits the
                # mainline store commits, sees no store-path commit at all, and would PASS.
                # DISCRIMINATOR: rc 0 against mutant M-first-parent (the flag dropped).
                fp = make_git("ancestry-first-parent")
                (fp / "seed.txt").write_bytes(b"base\n")
                git_call(fp, ["--literal-pathspecs", "add", "-A"])
                git_call(fp, ["-c", "user.email=t@t", "-c", "user.name=t",
                              "commit", "-m", "base"])
                fp_base = git_call(fp, ["rev-parse", "HEAD"]).decode("ascii").strip()
                fp_machine = fp / working / machine_name
                fp_machine.mkdir(parents=True)
                (fp_machine / _opf_store.MANIFEST_NAME).write_bytes(b"x = 1\n")
                (fp / _opf_store.POINTER_REL).write_bytes(b'[store]\ntarget = "dir:."\n')
                git_call(fp, ["--literal-pathspecs", "add", "-A"])
                git_call(fp, ["-c", "user.email=t@t", "-c", "user.name=t",
                              "commit", "-m", "store on main"])
                fp_store = git_call(fp, ["rev-parse", "HEAD"]).decode("ascii").strip()
                git_call(fp, ["checkout", "-q", "-b", "side", fp_base])
                (fp / "side.txt").write_bytes(b"side\n")
                git_call(fp, ["--literal-pathspecs", "add", "-A"])
                git_call(fp, ["-c", "user.email=t@t", "-c", "user.name=t",
                              "commit", "-m", "side work"])
                fp_side = git_call(fp, ["rev-parse", "HEAD"]).decode("ascii").strip()
                fp_tree = git_call(fp, ["rev-parse",
                                        fp_side + "^{tree}"]).decode("ascii").strip()
                fp_merge = git_call(fp, ["-c", "user.email=t@t", "-c", "user.name=t",
                                         "commit-tree", fp_tree, "-p", fp_store,
                                         "-p", fp_side, "-m",
                                         "merge drops store"]).decode("ascii").strip()
                git_call(fp, ["checkout", "-q", "main"])
                git_call(fp, ["reset", "--hard", "-q", fp_merge])
                rc, output = run(fp)
                check("ancestry: TREESAME-to-side merge dropping the mainline store refused",
                      rc == EXIT_ERROR and "prior store exists" in output)
                check("ancestry: first-parent refusal names the mainline store commit",
                      fp_store in output)
                check("ancestry parity: the coupled init's detector names the mainline store "
                      "commit", coupled_holder(fp) == fp_store)
                check("ancestry: the complete first-parent refusal text (its missing "
                      "counters.toml named as a PARTIAL restore)",
                      output == prior_store_output(
                          fp, fp, fp_store, [".opf.toml", ".working"], True,
                          gaps=[working + "/" + machine_name + "/"
                                + _opf_check.COUNTERS_NAME]))

                # _init_glob_escape is LOAD-BEARING (no false refusal): a store committed at
                # the sibling prefix weX/ must not block init at the metacharacter-named
                # root we[X]/ -- the :(glob) manifest pathspec escapes the bracket class, so
                # the name matches only the literal directory we[X]. DISCRIMINATOR: rc 2
                # (the unescaped glob we[X]/... matches the weX store history, a false
                # refusal) against mutant M-glob-escape (an identity _init_glob_escape).
                near = make_git("ancestry-glob-escape")
                (near / "seed.txt").write_bytes(b"base\n")
                git_call(near, ["--literal-pathspecs", "add", "-A"])
                git_call(near, ["-c", "user.email=t@t", "-c", "user.name=t",
                                "commit", "-m", "base"])
                sib_machine = near / "weX" / working / machine_name
                sib_machine.mkdir(parents=True)
                (sib_machine / _opf_store.MANIFEST_NAME).write_bytes(b"x = 1\n")
                git_call(near, ["--literal-pathspecs", "add", "-A"])
                git_call(near, ["-c", "user.email=t@t", "-c", "user.name=t",
                                "commit", "-m", "sibling store at weX"])
                bracket_init = near / "we[X]"
                bracket_init.mkdir()
                rc, output = run(bracket_init)
                check("ancestry: metacharacter-named root unaffected by sibling weX store",
                      rc == EXIT_OK and valid_sources(bracket_init))
                check("ancestry parity: the coupled init's detector escapes the we[X] prefix "
                      "and still finds the weX store",
                      coupled_holder(near, "we[X]") is None
                      and coupled_holder(near, "weX") == head_of(near))

                # The refuse-rather-than-guessing arm is LOAD-BEARING: a committed DIRECTORY
                # named .opf.toml is a store-path change whose tree holds no store
                # identifier (the pointer must be a BLOB), and after its deletion neither
                # the deletion commit's tree nor its first parent's holds one; init refuses
                # in those words rather than inventing a prior-store finding. DISCRIMINATOR:
                # mutant M-guess-arm (the arm removed) reports a definite "prior store
                # exists" instead.
                guess = make_git("ancestry-guess-arm")
                (guess / "seed.txt").write_bytes(b"base\n")
                git_call(guess, ["--literal-pathspecs", "add", "-A"])
                git_call(guess, ["-c", "user.email=t@t", "-c", "user.name=t",
                                 "commit", "-m", "base"])
                fake_dir = guess / _opf_store.POINTER_REL
                fake_dir.mkdir()
                (fake_dir / "x").write_bytes(b"not a pointer\n")
                git_call(guess, ["--literal-pathspecs", "add", "-A"])
                git_call(guess, ["-c", "user.email=t@t", "-c", "user.name=t",
                                 "commit", "-m", "directory named like the pointer"])
                git_call(guess, ["rm", "-r", "-q", "--", _opf_store.POINTER_REL])
                git_call(guess, ["-c", "user.email=t@t", "-c", "user.name=t",
                                 "commit", "-m", "drop the directory"])
                guess_drop = head_of(guess)
                rc, output = run(guess)
                check("ancestry: non-store change at a store path refuses rather than guessing",
                      rc == EXIT_ERROR and inert("refusing rather than guessing") in output
                      and "prior store exists" not in output)
                check("ancestry parity: the coupled init's detector refuses rather than guessing",
                      coupled_holder(guess) == _opf_init_observe.CANNOT_EVALUATE)
                check("ancestry: the complete refuse-rather-than-guessing text",
                      output == cannot_evaluate_output(guess, (
                          "git history preflight: commit " + guess_drop + " changed a store "
                          "path yet neither its tree nor its first parent's holds a store "
                          "identifier; refusing rather than guessing")))

                # SPEC 8.2 ancestry, no over-refusal: a history of ordinary commits that never
                # held a store identifier proceeds (the no-hit arm; the empty-git-root vector
                # above already covers the unborn-HEAD arm, which also proceeds).
                plain_history = make_git("ancestry-plain-history")
                (plain_history / "seed.txt").write_bytes(b"s\n")
                git_call(plain_history, ["--literal-pathspecs", "add", "-A"])
                git_call(plain_history, ["-c", "user.email=t@t", "-c", "user.name=t",
                                         "commit", "-m", "seed"])
                rc, output = run(plain_history)
                check("ancestry: committed history without a store proceeds",
                      rc == EXIT_OK and valid_sources(plain_history))
                check("ancestry parity: the coupled init's detector finds no store either",
                      coupled_holder(plain_history) is None)

                # QA round 3 MINOR 4: the CURRENT-PATH limit, pinned. A store committed at old/
                # on the first-parent line, deleted there, and the directory then renamed to
                # new/ (git mv): its counters did reach a first-parent tree, under another
                # directory, and the scan, whose pathspecs are new/'s present prefix and follow
                # no rename, does not detect it, so init PROCEEDS, and its output states that
                # limit. DISCRIMINATOR: M-any-prefix (the pointer pathspec widened to any
                # directory, a stand-in for a scan that looks beyond the current path) refuses
                # here; a scan extended that way must update every limit statement with it.
                moved = make_git("ancestry-renamed-root")
                old_root = moved / "old"
                old_root.mkdir()
                rc, output = init_store(old_root)
                check("ancestry renamed-root fixture first init succeeds", rc == EXIT_OK)
                git_call(moved, ["--literal-pathspecs", "add", "-A"])
                git_call(moved, ["-c", "user.email=t@t", "-c", "user.name=t",
                                 "commit", "-m", "store at old"])
                old_commit = head_of(moved)
                git_call(moved, ["rm", "-r", "-q", "--", "old/" + working,
                                 "old/" + _opf_store.POINTER_REL])
                git_call(moved, ["-c", "user.email=t@t", "-c", "user.name=t",
                                 "commit", "-m", "drop store at old"])
                git_call(moved, ["mv", "old", "new"])
                git_call(moved, ["-c", "user.email=t@t", "-c", "user.name=t",
                                 "commit", "-m", "rename old to new"])
                check("ancestry renamed-root fixture: a HEAD ancestor held the store at old/",
                      _opf_observe._run_git(git, moved, [
                          "cat-file", "-e", old_commit + ":old/" + _opf_store.POINTER_REL]).rc == 0
                      and _opf_observe._run_git(git, moved, [
                          "merge-base", "--is-ancestor", old_commit, "HEAD"]).rc == 0)
                rc, output = run(moved / "new")
                check("ancestry: a store committed under the root's former name is not detected "
                      "(current-path limit)",
                      rc == EXIT_OK and valid_sources(moved / "new"))
                check("ancestry: that success output states the current-path limit",
                      _states_path_limit(output) and _states_sideline_limit(output))

                # OPF-D2B round 6: in a PARTIAL clone a skip-worktree .gitignore whose blob is ABSENT locally
                # reads as no-rule under the no-lazy-fetch probe, but the adopter's own `git add` fetches that
                # blob and can then silently ignore the store. That divergence is a cannot-evaluate init must
                # REFUSE (guard-input-soundness), not pass. DISCRIMINATOR: without the availability check init
                # returns rc 0 and creates a store git add would skip.
                promisor_src = make_git("d2b-promisor-src")
                (promisor_src / ".gitignore").write_bytes(working.encode("ascii") + b"/\n")
                git_call(promisor_src, ["--literal-pathspecs", "add", ".gitignore"])
                git_call(promisor_src, ["-c", "user.email=t@t", "-c", "user.name=t",
                                        "commit", "-m", "seed"])
                git_call(promisor_src, ["config", "uploadpack.allowFilter", "true"])
                git_call(promisor_src, ["config", "uploadpack.allowAnySHA1InWant", "true"])
                src_url = "file://" + str(promisor_src)

                missing_blob = base / "d2b-missing-blob"
                git_call(base, ["-c", "protocol.file.allow=always", "clone", "--filter=blob:none",
                                "--no-checkout", src_url, str(missing_blob)])
                git_call(missing_blob, ["read-tree", "HEAD"])
                git_call(missing_blob, ["update-index", "--skip-worktree", ".gitignore"])
                mb_before = _snapshot(missing_blob)
                rc, output = run(missing_blob)
                check("partial-clone missing ignore-blob refused",
                      rc == EXIT_ERROR and inert("indexed .gitignore blob is unavailable") in output)
                check("partial-clone missing ignore-blob preserved",
                      _snapshot(missing_blob) == mb_before)

                # No over-refusal: a partial clone whose .gitignore is CHECKED OUT (blob materialized) and
                # does not match the store is read from disk like any full clone; init proceeds normally.
                unrelated_src = make_git("d2b-unrelated-src")
                (unrelated_src / ".gitignore").write_bytes(b"unrelated-only/\n")
                git_call(unrelated_src, ["--literal-pathspecs", "add", ".gitignore"])
                git_call(unrelated_src, ["-c", "user.email=t@t", "-c", "user.name=t",
                                         "commit", "-m", "seed"])
                git_call(unrelated_src, ["config", "uploadpack.allowFilter", "true"])
                materialized = base / "d2b-materialized"
                git_call(base, ["-c", "protocol.file.allow=always", "clone", "--filter=blob:none",
                                "file://" + str(unrelated_src), str(materialized)])
                rc, output = run(materialized)
                check("partial-clone materialized .gitignore init proceeds",
                      rc == EXIT_OK and valid_sources(materialized))

                # No regression: when the (skip-worktree) .gitignore blob IS available locally, the probe reads
                # it from the index exactly as git add, so a store it ignores is still refused as git-ignored --
                # the available-blob parity the round-6 check preserves.
                avail_blob = base / "d2b-available-blob"
                git_call(base, ["-c", "protocol.file.allow=always", "clone", src_url, str(avail_blob)])
                git_call(avail_blob, ["update-index", "--skip-worktree", ".gitignore"])
                (avail_blob / ".gitignore").unlink()
                ab_before = _snapshot(avail_blob)
                rc, output = run(avail_blob)
                check("available skip-worktree ignore-blob still refused as git-ignored",
                      rc == EXIT_ERROR and "git-ignored" in output)
                check("available skip-worktree ignore-blob preserved",
                      _snapshot(avail_blob) == ab_before)

                # OPF-D2B round 7 / FINDING 2: the materialized-file skip must NOT follow symlinks. A
                # skip-worktree .gitignore whose blob is absent and whose worktree path is a SYMLINK to a
                # readable regular file is opened by git with O_NOFOLLOW: the open fails and git falls back
                # to the index blob, which `git add` fetches and then ignores the store. is_file() followed
                # the link and wrongly skipped it as materialized (false PASS); the no-follow lstat classifies
                # the symlink as not-materialized and REFUSES. DISCRIMINATOR: init returns rc 0 (creates a
                # store git add would skip) against the is_file() version.
                symlink_blob = base / "d2b-symlink-gitignore"
                git_call(base, ["-c", "protocol.file.allow=always", "clone", "--filter=blob:none",
                                "--no-checkout", src_url, str(symlink_blob)])
                git_call(symlink_blob, ["read-tree", "HEAD"])
                git_call(symlink_blob, ["update-index", "--skip-worktree", ".gitignore"])
                (base / "d2b-symlink-target").write_bytes(b"readable regular file\n")
                (symlink_blob / ".gitignore").symlink_to(base / "d2b-symlink-target")
                sb_before = _snapshot(symlink_blob)
                rc, output = run(symlink_blob)
                check("symlink .gitignore over absent blob refused",
                      rc == EXIT_ERROR and inert("indexed .gitignore blob is unavailable") in output)
                check("symlink .gitignore fixture preserved", _snapshot(symlink_blob) == sb_before)

                # OPF-D2B round 7 / FINDING 3a: an ordinary stage-0 entry WITHOUT skip-worktree is one git
                # never index-reads (read_skip_worktree_file_from_index requires the flag), so an absent blob
                # for it cannot make `git add` ignore the store. The round-6 check refused ANY unmaterialized
                # entry with an absent blob (over-refusal); init must now PROCEED. DISCRIMINATOR: rc 2 against
                # the mode-only version.
                nonswt = base / "d2b-nonskipworktree"
                git_call(base, ["-c", "protocol.file.allow=always", "clone", "--filter=blob:none",
                                "--no-checkout", src_url, str(nonswt)])
                git_call(nonswt, ["read-tree", "HEAD"])
                rc, output = run(nonswt)
                check("non-skip-worktree absent ignore-blob proceeds",
                      rc == EXIT_OK and valid_sources(nonswt))

                # OPF-D2B round 7 / FINDING 3b: a DIRECTORY at the worktree .gitignore path is opened by git
                # successfully (its read then fails) and never index-read, so an absent blob cannot change what
                # `git add` ignores. is_file() was false and the round-6 check refused (over-refusal); the
                # no-follow classification treats a directory as not-a-fallback and PROCEEDS. DISCRIMINATOR:
                # rc 2 against the mode-only version.
                dir_at_path = base / "d2b-dir-at-gitignore"
                git_call(base, ["-c", "protocol.file.allow=always", "clone", "--filter=blob:none",
                                "--no-checkout", src_url, str(dir_at_path)])
                git_call(dir_at_path, ["read-tree", "HEAD"])
                git_call(dir_at_path, ["update-index", "--skip-worktree", ".gitignore"])
                (dir_at_path / ".gitignore").mkdir()
                rc, output = run(dir_at_path)
                check("directory at .gitignore path proceeds",
                      rc == EXIT_OK and valid_sources(dir_at_path))

                # OPF-D2B round 7 / FINDING 3c: an unmerged (stage-2-only) entry has no stage-0 entry, so
                # git's index-read lookup (index_name_pos, which searches stage 0) finds nothing and never
                # reads it; an absent stage-2 blob cannot make `git add` ignore the store. The round-6 check
                # keyed on mode alone and refused (over-refusal); init must now PROCEED. DISCRIMINATOR: rc 2
                # against the stage-blind version.
                unmerged = base / "d2b-unmerged-gitignore"
                git_call(base, ["-c", "protocol.file.allow=always", "clone", "--filter=blob:none",
                                "--no-checkout", src_url, str(unmerged)])
                git_call(unmerged, ["read-tree", "HEAD"])
                orig_blob = git_call(
                    promisor_src, ["rev-parse", "HEAD:.gitignore"]).decode("ascii").strip()
                git_call(unmerged, ["rm", "--cached", "-q", ".gitignore"])
                git_input(unmerged, ["update-index", "--index-info"],
                          ("100644 " + orig_blob + " 2\t.gitignore\n").encode("ascii"))
                rc, output = run(unmerged)
                check("stage-2-only unmerged .gitignore proceeds",
                      rc == EXIT_OK and valid_sources(unmerged))

                # OPF-D2B round 7 / FINDING 1b: candidate .gitignore paths must be matched as LITERAL
                # pathspecs. A store rooted at a directory literally named "[x]" yields the candidate
                # "[x]/.gitignore"; without --literal-pathspecs git expanded the bracket character-class and
                # matched an UNRELATED indexed "x/.gitignore" (a skip-worktree absent blob), a false refusal.
                # As a literal pathspec it matches nothing and init PROCEEDS. DISCRIMINATOR: rc 2 against the
                # non-literal version.
                bracket_src = make_git("d2b-bracket-src")
                (bracket_src / "x").mkdir()
                (bracket_src / "x" / ".gitignore").write_bytes(b"unrelated-only/\n")
                git_call(bracket_src, ["--literal-pathspecs", "add", "-A"])
                git_call(bracket_src, ["-c", "user.email=t@t", "-c", "user.name=t",
                                       "commit", "-m", "seed"])
                git_call(bracket_src, ["config", "uploadpack.allowFilter", "true"])
                git_call(bracket_src, ["config", "uploadpack.allowAnySHA1InWant", "true"])
                bracket = base / "d2b-bracket"
                git_call(base, ["-c", "protocol.file.allow=always", "clone", "--filter=blob:none",
                                "--no-checkout", "file://" + str(bracket_src), str(bracket)])
                git_call(bracket, ["read-tree", "HEAD"])
                git_call(bracket, ["--literal-pathspecs", "update-index", "--skip-worktree",
                                   "x/.gitignore"])
                bracket_root = bracket / "[x]"
                bracket_root.mkdir()
                rc, output = run(bracket_root)
                check("bracket-named store root proceeds (literal-pathspec candidate)",
                      rc == EXIT_OK and valid_sources(bracket_root))

                # OPF-D2B round 7 / FINDING 1a: a candidate whose repo-relative prefix begins with a pathspec
                # magic sigil (a leading colon) must be a LITERAL pathspec. A store rooted at ":magic" yields
                # the candidate ":magic/.gitignore"; without --literal-pathspecs the leading colon is read as
                # an empty magic signature, the listing is empty, and the ignoring skip-worktree absent blob
                # is MISSED (false PASS: a store `git add` would fetch-and-ignore is created). As a literal
                # pathspec the entry is found and init REFUSES. DISCRIMINATOR: rc 0 against the non-literal
                # version.
                colon_src = make_git("d2b-colon-src")
                (colon_src / ":magic").mkdir()
                (colon_src / ":magic" / ".gitignore").write_bytes(working.encode("ascii") + b"/\n")
                git_call(colon_src, ["--literal-pathspecs", "add", "-A"])
                git_call(colon_src, ["-c", "user.email=t@t", "-c", "user.name=t",
                                     "commit", "-m", "seed"])
                git_call(colon_src, ["config", "uploadpack.allowFilter", "true"])
                git_call(colon_src, ["config", "uploadpack.allowAnySHA1InWant", "true"])
                colon = base / "d2b-colon"
                git_call(base, ["-c", "protocol.file.allow=always", "clone", "--filter=blob:none",
                                "--no-checkout", "file://" + str(colon_src), str(colon)])
                git_call(colon, ["read-tree", "HEAD"])
                git_call(colon, ["--literal-pathspecs", "update-index", "--skip-worktree",
                                 ":magic/.gitignore"])
                colon_root = colon / ":magic"
                colon_root.mkdir()
                cr_before = _snapshot(colon_root)
                rc, output = run(colon_root)
                check("colon-magic candidate refused (literal-pathspec finds ignoring blob)",
                      rc == EXIT_ERROR and inert("indexed .gitignore blob is unavailable") in output)
                check("colon-magic store fixture preserved", _snapshot(colon_root) == cr_before)

                # OPF-D2B / D1 (BLOCKER): an UNREADABLE directory at the worktree .gitignore path. git's
                # own O_RDONLY|O_NOFOLLOW open of a chmod-000 directory FAILS (EACCES), so git falls back to
                # the index blob and the adopter's `git add` fetches it and then ignores the store. The
                # round-7 check exempted EVERY directory (false PASS); the git-faithful worktree open
                # classifies an unreadable directory as a fallback and REFUSES. DISCRIMINATOR: rc 0 (creates
                # a store git add would skip) against the exempt-all-directories version. (The readable
                # directory -> PROCEED direction is the "directory at .gitignore path" fixture above.)
                unread_dir = base / "d2b-unreadable-dir"
                git_call(base, ["-c", "protocol.file.allow=always", "clone", "--filter=blob:none",
                                "--no-checkout", src_url, str(unread_dir)])
                git_call(unread_dir, ["read-tree", "HEAD"])
                git_call(unread_dir, ["update-index", "--skip-worktree", ".gitignore"])
                ud_gi = unread_dir / ".gitignore"
                ud_gi.mkdir()
                os.chmod(str(ud_gi), 0o000)
                try:
                    rc, output = run(unread_dir)
                finally:
                    os.chmod(str(ud_gi), 0o755)   # restore so cleanup can traverse the fixture
                check("unreadable directory at .gitignore path refused",
                      rc == EXIT_ERROR and inert("indexed .gitignore blob is unavailable") in output)
                check("unreadable directory at .gitignore path preserved",
                      not (unread_dir / working).exists()
                      and not (unread_dir / _opf_store.POINTER_REL).exists()
                      and list(ud_gi.iterdir()) == [])

                # OPF-D2B / D2 (BLOCKER): a SYMLINK-mode (120000) index .gitignore. git reads the symlink
                # blob's target text as ignore patterns, so a committed symlink ".gitignore" -> ".working/"
                # in a partial clone (worktree path absent, skip-worktree) makes the adopter's `git add`
                # fetch the symlink blob and ignore the store. The round-7 check skipped mode 120000 (false
                # PASS); the fix reads a symlink-mode blob as an ignore source and REFUSES. DISCRIMINATOR:
                # rc 0 against the regular-file-mode-only version.
                symmode_src = make_git("d2b-symlink-mode-src")
                os.symlink(working + "/", str(symmode_src / ".gitignore"))   # committed as mode 120000
                git_call(symmode_src, ["--literal-pathspecs", "add", "--", ".gitignore"])
                git_call(symmode_src, ["-c", "user.email=t@t", "-c", "user.name=t",
                                       "commit", "-m", "seed"])
                git_call(symmode_src, ["config", "uploadpack.allowFilter", "true"])
                git_call(symmode_src, ["config", "uploadpack.allowAnySHA1InWant", "true"])
                symmode = base / "d2b-symlink-mode"
                git_call(base, ["-c", "protocol.file.allow=always", "clone", "--filter=blob:none",
                                "--no-checkout", "file://" + str(symmode_src), str(symmode)])
                git_call(symmode, ["read-tree", "HEAD"])
                git_call(symmode, ["update-index", "--skip-worktree", ".gitignore"])
                sm_before = _snapshot(symmode)
                rc, output = run(symmode)
                check("symlink-mode index .gitignore refused",
                      rc == EXIT_ERROR and inert("indexed .gitignore blob is unavailable") in output)
                check("symlink-mode index .gitignore preserved", _snapshot(symmode) == sm_before)

                # OPF-D2B / D4 (MAJOR): a --literal-pathspecs ls-files still matches DESCENDANTS of a
                # candidate. A store whose candidate is ".gitignore" matches an unrelated indexed
                # ".gitignore/data" (here ".gitignore" is a committed DIRECTORY holding "data"), which the
                # round-7 check treated as an ignore source and REFUSED. A descendant is not a file git ever
                # reads as ignore patterns; the exact-membership filter drops it and init PROCEEDS
                # (git add stages the store, the ".gitignore/data" blob being irrelevant to ignore
                # evaluation). DISCRIMINATOR: rc 2 (false refusal) against the descendant-matching version.
                descendant_src = make_git("d2b-descendant-src")
                (descendant_src / ".gitignore").mkdir()
                (descendant_src / ".gitignore" / "data").write_bytes(working.encode("ascii") + b"/\n")
                git_call(descendant_src, ["--literal-pathspecs", "add", "-A"])
                git_call(descendant_src, ["-c", "user.email=t@t", "-c", "user.name=t",
                                          "commit", "-m", "seed"])
                git_call(descendant_src, ["config", "uploadpack.allowFilter", "true"])
                git_call(descendant_src, ["config", "uploadpack.allowAnySHA1InWant", "true"])
                descendant = base / "d2b-descendant"
                git_call(base, ["-c", "protocol.file.allow=always", "clone", "--filter=blob:none",
                                "--no-checkout", "file://" + str(descendant_src), str(descendant)])
                git_call(descendant, ["read-tree", "HEAD"])
                git_call(descendant, ["--literal-pathspecs", "update-index", "--skip-worktree",
                                      ".gitignore/data"])
                rc, output = run(descendant)
                check("descendant .gitignore/data proceeds (exact-membership candidate)",
                      rc == EXIT_OK and valid_sources(descendant))

                # OPF-D2B round 8 / D5 (BLOCKER): a stage-0 skip-worktree GITLINK-mode (160000) index
                # .gitignore whose OID is a promisor-fetchable BLOB. git's fallback
                # (read_skip_worktree_file_from_index -> do_read_blob) is MODE-BLIND: for a skip-worktree
                # entry whose worktree open fails it reads the entry's OID and applies the object as ignore
                # patterns whenever it is a blob, never consulting the index mode. So this fabricated
                # gitlink-mode entry slips a mode allowlist, yet the adopter's own `git add` fetches the blob
                # and silently ignores the store (confirmed ground truth). The round-8 mode allowlist
                # (regular-file / symlink only) treated mode 160000 as not-an-ignore-source and PASSED (false
                # pass, rc 0, a store git add would skip); the mode-blind check refuses on the unavailable OID
                # regardless of mode. DISCRIMINATOR: rc 0 against the mode-allowlist version.
                gitlink = base / "d2b-gitlink-blob"
                git_call(base, ["-c", "protocol.file.allow=always", "clone", "--filter=blob:none",
                                "--no-checkout", src_url, str(gitlink)])
                git_call(gitlink, ["read-tree", "HEAD"])
                gitlink_blob = git_call(
                    promisor_src, ["rev-parse", "HEAD:.gitignore"]).decode("ascii").strip()
                git_call(gitlink, ["rm", "--cached", "-q", ".gitignore"])
                git_input(gitlink, ["update-index", "--index-info"],
                          ("160000 " + gitlink_blob + " 0\t.gitignore\n").encode("ascii"))
                git_call(gitlink, ["update-index", "--skip-worktree", ".gitignore"])
                gl_before = _snapshot(gitlink)
                rc, output = run(gitlink)
                check("gitlink-mode index .gitignore over fetchable blob refused",
                      rc == EXIT_ERROR and inert("indexed .gitignore blob is unavailable") in output)
                check("gitlink-mode index .gitignore preserved", _snapshot(gitlink) == gl_before)

                # OPF-D2B round 8 / D5 control (no over-refusal): the SAME gitlink-mode (160000) skip-worktree
                # entry, but with a real DIRECTORY at the worktree .gitignore path, as a valid initialized
                # submodule has. git's O_NOFOLLOW open of the directory SUCCEEDS, so git never index-reads the
                # OID and `git add` cannot ignore the store through it; init must PROCEED. This proves the
                # refusal is gated by the worktree open FAILING, not by the gitlink mode, so a valid submodule
                # is not blanket-refused.
                gitlink_dir = base / "d2b-gitlink-dir"
                git_call(base, ["-c", "protocol.file.allow=always", "clone", "--filter=blob:none",
                                "--no-checkout", src_url, str(gitlink_dir)])
                git_call(gitlink_dir, ["read-tree", "HEAD"])
                git_call(gitlink_dir, ["rm", "--cached", "-q", ".gitignore"])
                git_input(gitlink_dir, ["update-index", "--index-info"],
                          ("160000 " + gitlink_blob + " 0\t.gitignore\n").encode("ascii"))
                git_call(gitlink_dir, ["update-index", "--skip-worktree", ".gitignore"])
                (gitlink_dir / ".gitignore").mkdir()
                rc, output = run(gitlink_dir)
                check("gitlink-mode with directory at worktree path proceeds (no over-refusal)",
                      rc == EXIT_OK and valid_sources(gitlink_dir))

                # OPF-D2B round 9 / R9-1 (MAJOR over-refusal): the availability check must engage ONLY in a
                # PARTIAL clone. In a FULL clone git CANNOT lazy-fetch, so an absent indexed OID never causes a
                # silent fetch-and-ignore: `git add` stages the store (an absent submodule COMMIT is not a
                # blob, so do_read_blob stages nothing to ignore) rather than ignoring it. Here a REAL
                # submodule named ".gitignore" is sparse-omitted in a FULL clone (Codex's exact repro): its
                # gitlink entry is stage-0 skip-worktree ("S"), its worktree path is absent, and its commit OID
                # is absent from the superproject's object DB -- yet there is NO promisor remote. The round-9
                # HEAD reached cat-file on the absent commit OID and REFUSED (rc 2, a pure over-refusal); the
                # partial-clone gate returns [] for a full clone and init PROCEEDS, matching `git add`
                # (confirmed ground truth: `git add -- .working` stages, rc 0, check-ignore rc 1).
                # DISCRIMINATOR: rc 2 (false refusal) against the ungated round-9 HEAD.
                r9_submod_src = make_git("d2b-r9-submodule-src")
                (r9_submod_src / "keep").write_bytes(b"submodule content\n")
                git_call(r9_submod_src, ["--literal-pathspecs", "add", "-A"])
                git_call(r9_submod_src, ["-c", "user.email=t@t", "-c", "user.name=t",
                                         "commit", "-m", "subseed"])
                r9_super_src = make_git("d2b-r9-super-src")
                git_call(r9_super_src, ["-c", "protocol.file.allow=always", "submodule", "add",
                                        "file://" + str(r9_submod_src), ".gitignore"])
                git_call(r9_super_src, ["-c", "user.email=t@t", "-c", "user.name=t",
                                        "commit", "-m", "add sub as .gitignore"])
                r9_full = base / "d2b-r9-fullclone-omitted"
                git_call(base, ["-c", "protocol.file.allow=always", "clone",
                                "file://" + str(r9_super_src), str(r9_full)])
                # A FULL clone has no promisor remote; sparse-checkout omits the .gitignore submodule so its
                # worktree path is absent and skip-worktree is set, leaving the absent commit OID as what the
                # per-entry check would reach were it not gated on partial-clone-ness.
                git_input(r9_full, ["sparse-checkout", "set", "--no-cone", "--stdin"],
                          b"/*\n!/.gitignore\n")
                rc, output = run(r9_full)
                check("full-clone sparse-omitted submodule .gitignore proceeds (partial-clone gate)",
                      rc == EXIT_OK and valid_sources(r9_full))

                # OPF-D2B round 9 / R9-1 companion: an INITIALIZED submodule at ".gitignore" in a FULL
                # RECURSIVE clone, its directory chmod 000. git's O_RDONLY|O_NOFOLLOW open of the unreadable
                # directory FAILS, so the per-entry check would fall back to the gitlink commit OID -- which is
                # absent from the superproject's object DB (the submodule's objects live under .git/modules) --
                # and the round-9 HEAD REFUSED. But it is a FULL clone (no promisor), so `git add` cannot
                # lazy-fetch and stages the store (do_read_blob rejects the non-blob commit); the partial-clone
                # gate returns [] and init PROCEEDS. DISCRIMINATOR: rc 2 against the ungated round-9 HEAD.
                r9_rec = base / "d2b-r9-recursive-chmod000"
                git_call(base, ["-c", "protocol.file.allow=always", "clone", "--recurse-submodules",
                                "file://" + str(r9_super_src), str(r9_rec)])
                git_call(r9_rec, ["update-index", "--skip-worktree", ".gitignore"])
                r9_rec_gi = r9_rec / ".gitignore"
                os.chmod(str(r9_rec_gi), 0o000)
                try:
                    rc, output = run(r9_rec)
                finally:
                    os.chmod(str(r9_rec_gi), 0o755)   # restore so cleanup can traverse the fixture
                check("full-clone chmod-000 initialized submodule .gitignore proceeds (partial-clone gate)",
                      rc == EXIT_OK and valid_sources(r9_rec))

                # OPF-D2B round 11 / R10-1 + R10-2: _is_partial_clone must match git 2.53.0's OWN promisor
                # registration (repo_has_promisor_remote): git lazy-fetches a missing object iff ANY of
                # (a) a remote.*.partialclonefilter is PRESENT (value-blind, overrides promisor=false),
                # (b) a remote.*.promisor is boolean-TRUE, or (c) extensions.partialClone is present (no
                # format gate: git honours it at core.repositoryformatversion=0 too, confirmed empirically).
                # Each fixture below builds a blobless clone with an ABSENT skip-worktree .gitignore blob
                # (the store-ignoring src), strips the promisor config the clone wrote, then sets exactly the
                # keys under test; the availability check must REFUSE only when git add would fetch-and-ignore.
                def d2b_stripped_partial(name):
                    """A blobless partial clone (absent skip-worktree .gitignore blob) with ALL promisor
                    config stripped, so each scenario sets exactly the registration keys under test."""
                    dest = base / name
                    git_call(base, ["-c", "protocol.file.allow=always", "clone", "--filter=blob:none",
                                    "--no-checkout", src_url, str(dest)])
                    git_call(dest, ["read-tree", "HEAD"])
                    git_call(dest, ["update-index", "--skip-worktree", ".gitignore"])
                    for key in ("remote.origin.promisor", "remote.origin.partialclonefilter",
                                "extensions.partialClone"):
                        rr = _opf_observe._run_git(git, dest, ["config", "--unset-all", key])
                        if not rr.completed or rr.rc not in (0, 5):  # rc 5 = key already absent, tolerable
                            raise OSError("fixture --unset-all {} failed at {!r}: {}".format(
                                key, str(dest), rr.err))
                    return dest

                # R10-1 (BLOCKER): partialclonefilter PRESENT, promisor unset, no extension. The filter
                # registers the promisor by presence, so `git add` lazy-fetches the absent blob and ignores
                # the store (ground truth PARTIAL); init must REFUSE. The round-10 promisor-only probe read
                # this as a FULL clone (no promisor line) and PROCEEDED. DISCRIMINATOR: rc 0 (creates a store
                # git add would skip) against the round-10 HEAD.
                filt_only = d2b_stripped_partial("d2b-filter-only")
                git_call(filt_only, ["config", "remote.origin.partialclonefilter", "blob:none"])
                fo_before = _snapshot(filt_only)
                rc, output = run(filt_only)
                check("partialclonefilter-only partial clone refused (R10-1)",
                      rc == EXIT_ERROR and inert("indexed .gitignore blob is unavailable") in output)
                check("partialclonefilter-only fixture preserved", _snapshot(filt_only) == fo_before)

                # R10-2 (MAJOR over-refusal): promisor=false alone, no filter, no extension. A false promisor
                # does NOT register, so git CANNOT lazy-fetch: `git add` stages the store (ground truth FULL);
                # init must PROCEED. The round-10 probe matched the promisor=false LINE by presence and
                # REFUSED. DISCRIMINATOR: rc 2 (false refusal) against the round-10 HEAD.
                prom_false = d2b_stripped_partial("d2b-promisor-false")
                git_call(prom_false, ["config", "remote.origin.promisor", "false"])
                rc, output = run(prom_false)
                check("promisor=false-only clone proceeds (R10-2 no over-refusal)",
                      rc == EXIT_OK and valid_sources(prom_false))

                # filter OVERRIDES promisor=false: the filter's presence registers the promisor even with a
                # sibling promisor=false, so `git add` lazy-fetches (ground truth PARTIAL); init must REFUSE.
                # (Locks the value-blind filter semantics: it would break if the filter check became
                # value-sensitive or a promisor=false were read as de-registering.)
                filt_pf = d2b_stripped_partial("d2b-filter-promfalse")
                git_call(filt_pf, ["config", "remote.origin.partialclonefilter", "blob:none"])
                git_call(filt_pf, ["config", "remote.origin.promisor", "false"])
                fpf_before = _snapshot(filt_pf)
                rc, output = run(filt_pf)
                check("partialclonefilter overrides promisor=false: refused",
                      rc == EXIT_ERROR and inert("indexed .gitignore blob is unavailable") in output)
                check("filter-over-promfalse fixture preserved", _snapshot(filt_pf) == fpf_before)

                # extensions.partialClone OVERRIDES promisor=false (format 1): the extension registers the
                # named default promisor after the config parse, so `git add` lazy-fetches (ground truth
                # PARTIAL); init must REFUSE.
                ext_pf = d2b_stripped_partial("d2b-ext-promfalse")
                git_call(ext_pf, ["config", "core.repositoryformatversion", "1"])
                git_call(ext_pf, ["config", "extensions.partialClone", "origin"])
                git_call(ext_pf, ["config", "remote.origin.promisor", "false"])
                epf_before = _snapshot(ext_pf)
                rc, output = run(ext_pf)
                check("extensions.partialClone overrides promisor=false (format 1): refused",
                      rc == EXIT_ERROR and inert("indexed .gitignore blob is unavailable") in output)
                check("ext-over-promfalse fixture preserved", _snapshot(ext_pf) == epf_before)

                # extensions.partialClone at core.repositoryformatversion=0: the empirically-settled case.
                # git 2.53.0 HONOURS the extension at format 0 (handle_extension_v0 historical compat),
                # confirmed by isolated repro: `git add` lazy-fetches the absent blob and ignores the store
                # (ground truth PARTIAL), so init must REFUSE. Asserting REFUSE both matches git and is the
                # fail-closed direction; this fixture would fail against a (rejected) format>=1-gated
                # implementation, which would wrongly PROCEED.
                ext_v0 = d2b_stripped_partial("d2b-ext-format0")
                git_call(ext_v0, ["config", "extensions.partialClone", "origin"])
                git_call(ext_v0, ["config", "core.repositoryformatversion", "0"])
                ev0_before = _snapshot(ext_v0)
                rc, output = run(ext_v0)
                check("extensions.partialClone at format 0 refused (git honours the extension at v0)",
                      rc == EXIT_ERROR and inert("indexed .gitignore blob is unavailable") in output)
                check("ext-format0 fixture preserved", _snapshot(ext_v0) == ev0_before)

                # OPF-D2B round 12 / R11-1 (BLOCKER, fail-open false pass): a promisor remote whose NAME
                # carries non-UTF-8 bytes (promisor=true, no filter, no extension). The round-11 loop
                # enumerated the promisor key byte-preserving (b"remote.up\xffstream.promisor\x00") but then
                # re-queried it via a "replace"-decoded key ("remote.up�stream.promisor"), which asks git
                # for a DIFFERENT (nonexistent) key and returns rc 1 (not found); the round-11 code read that
                # rc 1 as "not a true promisor" and returned FULL, so the availability check was skipped and
                # init PROCEEDED (rc 0) even though `git add` lazy-fetches through the real up\xffstream
                # promisor and IGNORES the store. The fix fails closed: an enumerated promisor key that cannot
                # cleanly re-query (non-UTF-8 name, or any re-query rc != 0) is a cannot-determine -> partial.
                # DISCRIMINATOR: rc 0 (creates a store git add would skip) against the round-11 HEAD.
                nonutf8 = d2b_stripped_partial("d2b-nonutf8-promisor")
                git_call(nonutf8, ["config", "remote.origin.promisor", "true"])
                nu_cfg = nonutf8 / ".git" / "config"
                nu_raw = nu_cfg.read_bytes()
                if b'[remote "origin"]' not in nu_raw:
                    raise OSError("R11-1 fixture: expected a [remote \"origin\"] section to rename")
                nu_cfg.write_bytes(nu_raw.replace(b'[remote "origin"]', b'[remote "up\xffstream"]'))
                # Ground truth in a byte-exact COPY (git add mutates the index and can fetch): the real
                # `git add -A` fetches through the non-UTF-8 promisor and IGNORES the store, so .working is NOT
                # staged and check-ignore reports it ignored. This is the hazard the refusal below guards.
                nu_gt = base / "d2b-nonutf8-groundtruth"
                shutil.copytree(str(nonutf8), str(nu_gt))
                (nu_gt / working).mkdir()
                (nu_gt / working / "f").write_bytes(b"store\n")
                git_call(nu_gt, ["-c", "protocol.file.allow=always", "add", "-A"])
                nu_staged = git_call(nu_gt, ["--literal-pathspecs", "ls-files", "--", working])
                nu_ign = _opf_observe._run_git(git, nu_gt, ["check-ignore", working + "/f"])
                check("R11-1 ground truth: git add fetches via the non-UTF-8 promisor and ignores the store",
                      nu_staged.strip() == b"" and nu_ign.completed and nu_ign.rc == 0)
                nu_before = _snapshot(nonutf8)
                rc, output = run(nonutf8)
                check("non-UTF-8-named promisor remote refused (R11-1)",
                      rc == EXIT_ERROR and inert("indexed .gitignore blob is unavailable") in output)
                check("non-UTF-8-named promisor fixture preserved", _snapshot(nonutf8) == nu_before)

                # OPF-D2B / D3 (fixture hermeticity): the stdin-fed fixture git helper must build its OWN
                # scrubbed environment, so an inherited GIT_INDEX_FILE (or GIT_DIR / GIT_WORK_TREE /
                # GIT_OBJECT_DIRECTORY / GIT_COMMON_DIR) cannot redirect its write to a caller's external
                # index. We create a genuine external repo index, point GIT_INDEX_FILE at it, run an
                # index-info write through git_input, and assert the external index is BYTE-UNCHANGED.
                # DISCRIMINATOR: against the os.environ.copy() helper the update-index below lands in the
                # external index and its bytes change (the suite would silently mutate caller state, and a
                # stage-2 fixture could report PASS while writing an inherited external index).
                guard_repo = make_git("d3-index-guard")
                (guard_repo / "seed").write_bytes(b"seed\n")
                git_call(guard_repo, ["--literal-pathspecs", "add", "--", "seed"])
                guard_blob = git_call(guard_repo, ["rev-parse", ":seed"]).decode("ascii").strip()
                external_repo = make_git("d3-external-repo")
                (external_repo / "ext").write_bytes(b"external\n")
                git_call(external_repo, ["--literal-pathspecs", "add", "--", "ext"])
                external_index = external_repo / ".git" / "index"
                external_before = external_index.read_bytes()
                saved_index_file = os.environ.get("GIT_INDEX_FILE")
                os.environ["GIT_INDEX_FILE"] = str(external_index)
                try:
                    git_input(guard_repo, ["update-index", "--index-info"],
                              ("100644 " + guard_blob + " 0\tseed2\n").encode("ascii"))
                finally:
                    if saved_index_file is None:
                        os.environ.pop("GIT_INDEX_FILE", None)
                    else:
                        os.environ["GIT_INDEX_FILE"] = saved_index_file
                check("git_input ignores an inherited GIT_INDEX_FILE (external index byte-unchanged)",
                      external_index.read_bytes() == external_before)

                # OPF-FSMONITOR (F-OPF-INIT-FSMONITOR-EXEC): _run_git must suppress a repository-configured
                # core.fsmonitor, so an untrusted repo cannot obtain CODE EXECUTION when the adopter runs
                # `opf init`. init's non-probe git calls route through _init_git -> _opf_observe._run_git;
                # before the fix _run_git omitted `-c core.fsmonitor=false`, so git LAUNCHED the repo's
                # configured fsmonitor program during an index refresh in the init path. The D2B ignore probe
                # already suppressed fsmonitor via _run_git_config_discovery; this closes the parallel
                # _run_git path. We commit a seed, configure a marker-touching fsmonitor, run init, and assert
                # the marker was NOT created (the monitor process never ran). DISCRIMINATOR: against the
                # pre-fix _run_git the marker IS created (fsmonitor executed) even though init still exits 0.
                fsmon = make_git("fsmonitor-exec")
                (fsmon / "seed.txt").write_bytes(b"seed\n")
                git_call(fsmon, ["--literal-pathspecs", "add", "--", "seed.txt"])
                git_call(fsmon, ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "seed"])
                fsmon_marker = base / "fsmonitor-marker"
                fsmon_hook = base / "fsmonitor-evil.sh"
                fsmon_hook.write_text(
                    "#!/bin/sh\ntouch {}\nexit 0\n".format(shlex.quote(str(fsmon_marker))),
                    encoding="utf-8")
                os.chmod(str(fsmon_hook), 0o755)
                git_call(fsmon, ["config", "core.fsmonitor", str(fsmon_hook)])
                rc, output = run(fsmon)
                check("init suppresses a repo-configured core.fsmonitor (no process launched)",
                      not fsmon_marker.exists())
                check("init still succeeds with core.fsmonitor configured",
                      rc == EXIT_OK and valid_sources(fsmon))

                # OPF-OBSERVE-LAZYFETCH-EXEC (F-OPF-OBSERVE-LAZYFETCH-EXEC): an object-reading OPF
                # observation must never trigger a partial-clone LAZY FETCH, because the fetch EXECUTES the
                # repository-configured core.sshCommand -- the same code-execution class as the fsmonitor
                # vector above, reached instead through git's promisor machinery. _show_toml reads a committed
                # TOML via `git show HEAD:<path>` under _opf_observe._run_git; before the fix _scrubbed_env
                # neither set GIT_NO_LAZY_FETCH nor kept an ambient one (the allowlist scrub STRIPS it), so a
                # `show` of an ABSENT blob in a blobless clone lazy-fetched through origin and RAN
                # core.sshCommand. We commit seed.toml, make a blobless partial clone (blob absent, promisor
                # registered by the clone), repoint origin at ssh with a marker-touching core.sshCommand, then
                # call _show_toml and assert the marker was NOT created (no fetch/exec) and the read reports
                # the object unavailable via the existing omit-plus-note path (unchanged for a PRESENT
                # object). Hermetic and offline: ssh://example.invalid never connects -- git spawns
                # core.sshCommand before any network I/O, and the marker script exits without connecting.
                # DISCRIMINATOR: against the round-1 _run_git env the marker IS created (the ssh command ran).
                lazy_src = make_git("lazyfetch-src")
                (lazy_src / "seed.toml").write_bytes(b'name = "x"\n')
                git_call(lazy_src, ["--literal-pathspecs", "add", "--", "seed.toml"])
                git_call(lazy_src, ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "seed"])
                git_call(lazy_src, ["config", "uploadpack.allowFilter", "true"])
                git_call(lazy_src, ["config", "uploadpack.allowAnySHA1InWant", "true"])
                lazy_clone = base / "lazyfetch-clone"
                git_call(base, ["-c", "protocol.file.allow=always", "clone", "--filter=blob:none",
                                "--no-checkout", "file://" + str(lazy_src), str(lazy_clone)])
                lazy_marker = base / "lazyfetch-marker"
                lazy_hook = base / "lazyfetch-ssh.sh"
                lazy_hook.write_text(
                    "#!/bin/sh\ntouch {}\nexit 0\n".format(shlex.quote(str(lazy_marker))),
                    encoding="utf-8")
                os.chmod(str(lazy_hook), 0o755)
                git_call(lazy_clone, ["remote", "set-url", "origin", "ssh://example.invalid/repo"])
                git_call(lazy_clone, ["config", "ssh.variant", "ssh"])
                git_call(lazy_clone, ["config", "core.sshCommand", str(lazy_hook)])
                lazy_notes = []
                lazy_data = _opf_observe._show_toml(git, lazy_clone, "", "seed.toml", "seed", lazy_notes)
                check("observe _show_toml on an absent blob does not lazy-fetch/exec core.sshCommand",
                      not lazy_marker.exists())
                check("observe _show_toml on an absent blob omits the prior with a note",
                      lazy_data is None and any("seed" in n and ("absent" in n or "unreadable" in n)
                                                for n in lazy_notes))

                # Plain init on homes generation 2, under the raised tooling gates
                # (_gen2_cli_vectors), and its shipped-constant payloads pinned.
                _gen2_cli_vectors(check, make_git, git_call, fd_capture, inert)
            finally:
                os.environ.clear()
                os.environ.update(saved_env)
                try:
                    os.fchdir(saved_cwd)
                finally:
                    os.close(saved_cwd)

        if failures:
            for label in failures:
                print("check_opf_init: FAIL: " + label, file=sys.stderr)
            return EXIT_FINDING
        print("check_opf_init: PASS ({} executed fixture assertions)".format(len(checked)))
        return EXIT_OK
    except Exception as exc:  # noqa: BLE001  missing resources and harness errors cannot skip clean
        print("check_opf_init: cannot evaluate fixture suite: {}; exit 2".format(ascii(exc)),
              file=sys.stderr)
        return EXIT_ERROR


def _self_test():
    """The gate's fixture run inside the OPF git lifecycle (_opf_oplock._st_with_git_lifecycle).
    Each init child inherits the lifecycle's PATH git wrapper through _suite's fixture
    environment, and so does every in-process mutex acquisition in the lease fixtures, so the
    init operation mutex's rev-parse, which strips every GIT_* variable, still runs with the
    system-config pins and never reads the host's system configuration."""
    import _opf_oplock
    return _opf_oplock._st_with_git_lifecycle(_self_test_lifecycle)


def _self_test_lifecycle():
    """The lifecycle-wrapped leg of _self_test: the fixture suite over the isolated child."""
    return _suite(_run_init)


def main(argv=None):
    try:
        args = list(sys.argv[1:] if argv is None else argv)
        if args in ([], ["--self-test"]):
            return _self_test()
        print("usage: check_opf_init.py [--self-test]", file=sys.stderr)
        return EXIT_ERROR
    except Exception as exc:  # noqa: BLE001  final class-width fail-closed backstop
        print("check_opf_init: cannot evaluate: {}; exit 2".format(ascii(exc)), file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
