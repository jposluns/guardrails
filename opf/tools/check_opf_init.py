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
# the five pinned module imports, is a violation. The spellings are
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
        "_opf_check.COUNTERS_NAME", "_opf_check.INDEX_SUFFIX",
        "_opf_check.VERSION_NAME", "_opf_check.WORKLOG_NAME",
        "_opf_emit.emit_checked", "_opf_emit.emit_checked().encode",
        "_opf_init.INDEX_TYPES", "_opf_init.build_counters",
        "_opf_init.build_index", "_opf_init.build_manifest",
        "_opf_init.build_version", "_opf_init.build_worklog",
        "_opf_store.DEFAULT_MACHINE_SUBDIR", "_opf_store.LOCAL_POINTER_REL",
        "_opf_store.MANIFEST_NAME", "_opf_store.NOT_ADOPTED",
        "_opf_store.POINTER_REL", "_opf_store.RESOLVED",
        "_opf_store.WORKING_DIRNAME", "_opf_store._close_fd_exc_safe",
        "_opf_store._journal", "_opf_store._journal._close_fd_quietly",
        "_opf_store._open_dir_nofollow", "_opf_store.resolve_store", "all",
        "any", "ascii", "dict", "documents.extend", "exc.restore_lines",
        "journal._check_rel", "journal._lstat_at", "journal._lstat_contained",
        "journal._open_parent", "journal.require_containment", "json.dumps",
        "len", "list", "os.fsync", "os.mkdir", "os.path.abspath",
        "path.startswith", "payloads.items", "res.detail", "res.status", "set",
        "str", "str-literal.format", "str-literal.join", "text.encode", "type",
        "type().__name__", "val.startswith",
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
    "str", "super", "tuple", "type",
    "_opf_check", "_opf_emit", "_opf_init", "_opf_observe", "_opf_store",
    "_opf_write_guard",
])

_ROUTE_EXTERNAL_IMPORTS = dict(
    os=("import", "os"),
    json=("import", "json"),
    stat=("import", "stat"),
    Path=("from", "pathlib", "Path"),
    _opf_init=("import", "_opf_init"),
)


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


def _suite(invoke):
    """Isolate fixture configuration and restore the caller even on failure."""
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
        import _opf_observe
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
                check("no git mutation", _snapshot(clean / ".git") == git_before)
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
                # traverse_only: where os.O_PATH exists init proceeds and a search-only root binds.
                # DISCRIMINATORS: the readable open restored at either site refuses EACCES (the nested
                # init exits 2 in preflight; the identity check raises PermissionError). Mode bits do not
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
                              "repository initializes", rc == EXIT_OK and valid_sources(so_product))
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
                # drive_cmd_init, drive_module_init) returns fd_capture directly,
                # pinned by the source count below, so this potency holds for
                # every driver's capture path; drive_cmd_init and
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
                check("capture: each of the three in-process drivers returns "
                      "fd_capture directly",
                      this_source.count(capture_needle) == 3)

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


def main(argv=None):
    try:
        args = list(sys.argv[1:] if argv is None else argv)
        if args not in ([], ["--self-test"]):
            print("usage: check_opf_init.py [--self-test]", file=sys.stderr)
            return EXIT_ERROR
        return _suite(_run_init)
    except Exception as exc:  # noqa: BLE001  final class-width fail-closed backstop
        print("check_opf_init: cannot evaluate: {}; exit 2".format(ascii(exc)), file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
