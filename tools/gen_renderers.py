#!/usr/bin/env python3
"""Render .aiqt/core/renderers.toml: the manifest-covered RENDERER/GENERATOR DECLARATION (VER-CORE 6.5,
R9-5). Offline, stdlib only, fail-closed.

For each declared adapter renderer (gen_agents, gen_adapters, gen_claude, gen_cursor, gen_rules, gen_skill,
gen_worker_pack) this tool STATICALLY PARSES (ast, never import) the generator's module-level RENDERER_DECL
literal ({"renderer-id": <str>, "semantics-revision": <int>}) and its GENSRC_OUTPUTS targets (recovered
through gen_gensrc's own validated loader, the house import-reuse pattern), computes the ORDERED PACK-LOCAL
IMPORT CLOSURE (the entrypoint plus every tools/<module>.py it transitively imports, entrypoint first then
the rest bytewise), and computes a FRAMED generator-code digest over per-file records "<path>\\t<bytes>\\t
<sha256>\\n" in closure order, NEVER a raw concatenation (so a boundary-shifting edit stays detectable).
Any edit anywhere in a closure changes the framed digest, forcing a declaration diff at the Step-4 delta
gate; check_manifest.py recomputes the ARTIFACTS roster from these targets.

Fail-closed (GateError -> exit 2): a tools/*.py that declares RENDERER_DECL but is absent from RENDERERS
(a --check against a fresh run proves the file current, never the roster complete, since both read the
same RENDERERS); a tools/*.py that binds, reads or spells RENDERER_DECL anywhere the source names it other
than in ONE single-target top-level assignment or annotated assignment to the bare name, a comparison
against the string or a third or later call argument aside, exemptions that never cover a folded dotted or
colon path string whose components include the name (_declaration, the single predicate discovery
and the reader share), a tools/*.py that cannot be read, declares a source encoding other than UTF-8 or
cannot be parsed (any parse error class, including a parser stack overflow on deep nesting), or a *.py
entry that is not a regular file (_declared_renderer_stems, which also discloses the reflective-write
residual); a missing/malformed RENDERER_DECL or GENSRC_OUTPUTS; a pack-local
import that cannot be statically resolved (a relative import, or a wildcard `from <pack-local> import *`);
an unreadable closure member; or any other cannot-evaluate. DISCLOSED RESIDUAL (disclose-guard-residuals):
the closure is computed from statically-parsed Import/ImportFrom nodes only. A truly-dynamic pack-local
import (importlib.import_module, __import__, or globals() manipulation) is beyond static analysis; it is
not used by any declared generator today, and the delta gate's fail-closed-on-incomplete-closure leg is
the backstop, but this generator does not detect that class and discloses it here rather than implying it.

  gen_renderers.py             regenerate .aiqt/core/renderers.toml
  gen_renderers.py --check     exit 1 if the committed file differs from a fresh regeneration; write nothing
  gen_renderers.py --self-test build synthetic generators and assert the fail-closed invariants
  gen_renderers.py --root DIR  operate on DIR instead of the repo root (fixtures)

Exit convention (matches the repo's gates): 0 clean; 1 drift; 2 malformed/unreadable input or any
cannot-evaluate.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: gen_renderers.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import ast
import codecs
import hashlib
import io
import os
import stat
import tokenize
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _gen_common import repo_root, reconcile  # noqa: E402
from gen_rules import SLUG_RE  # noqa: E402  the authoritative renderer-id slug syntax
import gen_gensrc  # noqa: E402  reuse its validated GENSRC_OUTPUTS loader, never a second parser

GENSRC_OUTPUTS = (
    {"target": ".aiqt/core/renderers.toml", "kind": "file",
     "sources": ("tools/",), "regenerate": "python3 tools/gen_renderers.py"},
)
RENDERERS_REL = ".aiqt/core/renderers.toml"
# The declared adapter renderers, in a fixed hand-kept order (not sorted by renderer-id or by file name) for a
# deterministic render; reordering changes the bytes of renderers.toml. build_rows fails closed when a
# tools/*.py file declares RENDERER_DECL but is absent here (_declared_renderer_stems).
RENDERERS = ("gen_agents", "gen_adapters", "gen_claude", "gen_cursor", "gen_rules", "gen_skill",
             "gen_worker_pack")


class GateError(Exception):
    """A fail-closed condition (malformed declaration, unresolvable import, unreadable input): exit 2."""


def _sha256(data):
    return hashlib.sha256(data).hexdigest()


def _toml_str(value):
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


# Every exception class reading a hostile *.py can raise: OSError (unreadable: a permission, a directory, a
# vanished file) and MemoryError (too large to hold). Each is a cannot-evaluate reported as a READ failure.
_READ_ERRORS = (OSError, MemoryError)
# Every exception class parsing it can raise: SyntaxError (incl. a bad coding cookie or invalid UTF-8),
# ValueError (incl. UnicodeDecodeError), and MemoryError or RecursionError (a parser or compiler stack overflow
# on very deep nesting). Each is a cannot-evaluate (GateError, exit 2), never a traceback.
_PARSE_ERRORS = (SyntaxError, ValueError, MemoryError, RecursionError)


def _parse_module(path, where, purpose):
    """Parse a tools/*.py to an AST from its raw BYTES, as Python's own import does (a BOM and a PEP 263
    coding cookie are honoured, never bypassed by a UTF-8 text read), after refusing a declared source
    encoding other than UTF-8: a UTF-7 cookie, say, can turn a line that reads as a comment in UTF-8 into a
    statement. Fail-closed (GateError) on that cookie, on every exception class in _READ_ERRORS (reported as
    a read failure) and on every exception class in _PARSE_ERRORS (reported as a parse failure)."""
    try:
        data = path.read_bytes()
    except _READ_ERRORS as exc:
        raise GateError("{}: cannot read it to scan for {} ({}: {})".format(
            where, purpose, type(exc).__name__, exc))
    try:
        encoding = tokenize.detect_encoding(io.BytesIO(data).readline)[0]
        if codecs.lookup(encoding).name not in ("utf-8", "utf-8-sig"):
            raise GateError("{}: declares the source encoding {!r}; only UTF-8 is supported, so it cannot be "
                            "scanned for {} and is refused".format(where, encoding, purpose))
        return ast.parse(data, filename=where)
    except _PARSE_ERRORS as exc:
        raise GateError("{}: cannot parse it to scan for {} ({}: {})".format(
            where, purpose, type(exc).__name__, exc))


def _read_renderer_decl(path, where):
    """Recover a generator's single module-level RENDERER_DECL literal by parsing to an AST and evaluating
    ONLY that literal (ast.literal_eval); the module is never imported. The binding is found by
    _declaration, the same predicate discovery uses, so the two accept exactly the same binding set.
    Fail-closed (GateError) when there is not exactly one top-level assignment or annotated assignment to
    RENDERER_DECL (or any other use of it), when that binding has no value or a non-literal value, or when
    the shape is wrong ({'renderer-id': <slug str>, 'semantics-revision': <non-negative int>})."""
    bindings = _declaration(_parse_module(path, where, "RENDERER_DECL"), where)
    if not bindings:
        raise GateError("{}: expected exactly one top-level RENDERER_DECL assignment or annotated "
                        "assignment, found none".format(where))
    value = bindings[0][1]
    if value is None:
        raise GateError("{}: RENDERER_DECL is annotated but never assigned a value".format(where))
    try:
        decl = ast.literal_eval(value)
    except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError) as exc:
        raise GateError("{}: RENDERER_DECL must be a literal dict ({})".format(where, exc))
    if not isinstance(decl, dict) or set(decl) != {"renderer-id", "semantics-revision"}:
        raise GateError("{}: RENDERER_DECL keys must be exactly renderer-id/semantics-revision".format(where))
    rid = decl["renderer-id"]
    rev = decl["semantics-revision"]
    # renderer-id is the authoritative slug (this round's #3): reject a non-slug (e.g. "Bad ID") at the
    # source RENDERER_DECL, so a malformed id can never be rendered into a "fresh" renderers.toml.
    if not isinstance(rid, str) or not SLUG_RE.fullmatch(rid):
        raise GateError("{}: renderer-id {!r} is not a valid slug".format(where, rid))
    if not isinstance(rev, int) or isinstance(rev, bool) or rev < 0:
        raise GateError("{}: semantics-revision must be a non-negative integer".format(where))
    return rid, rev


# A readable name for the construct that binds RENDERER_DECL, for the fail-closed message.
_BINDER_FORMS = {"Assign": "an assignment", "AnnAssign": "an annotated assignment",
                 "AugAssign": "an augmented assignment", "NamedExpr": "an assignment expression (walrus)",
                 "Delete": "a del statement", "For": "a for-loop target", "AsyncFor": "a for-loop target",
                 "comprehension": "a comprehension target", "withitem": "a with-as target",
                 "TypeAlias": "a type alias statement"}
_BLOCK_FORMS = {"If": "an if/else block", "Try": "a try block", "TryStar": "a try block",
                "With": "a with block", "AsyncWith": "a with block", "For": "a for block",
                "AsyncFor": "a for block", "While": "a while block", "Match": "a match block",
                "FunctionDef": "a def block", "AsyncFunctionDef": "a def block", "ClassDef": "a class block",
                "Lambda": "a lambda body"}
# The scopes among _BLOCK_FORMS: only their BODY runs inside them. A decorator, a default, an annotation or
# a base is evaluated in the enclosing scope, so a binder there is not "inside" the def, class or lambda.
_SCOPE_FORMS = ("FunctionDef", "AsyncFunctionDef", "ClassDef", "Lambda")


def _top_level_bindings(tree):
    """Every (target, value) pair binding the bare name RENDERER_DECL as a target of a top-level Assign
    (each such target of a chained assignment counts) or AnnAssign (value None for a bare annotation)."""
    found = []
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        else:
            continue
        found.extend((t, node.value) for t in targets if isinstance(t, ast.Name) and t.id == "RENDERER_DECL")
    return found


def _declaration(tree, where):
    """THE supported RENDERER_DECL declaration, the single predicate shared by discovery
    (_declared_renderer_stems) and the reader (_read_renderer_decl) so both accept exactly the same binding
    set: at most ONE single-target top-level Assign or AnnAssign to the bare name, and no other use of it
    that _unsupported_binding sees. Returns a list of zero or one (target, value) pair. Fail-closed
    (GateError) on an unsupported use (_unsupported_binding) and on more than one top-level binding, since a
    later rebinding (an annotated one, say) or an in-place change to the declared dict (a subscript store or
    an update call, which needs a read or an alias _unsupported_binding refuses) would make the value Python
    runs differ from the value read. The one exception is the reflective residual _declared_renderer_stems
    discloses."""
    form = _unsupported_binding(tree)
    if form is not None:
        raise GateError("{}: unsupported use of RENDERER_DECL: {}; the only supported use is one "
                        "single-target top-level assignment or annotated assignment to the bare name, never "
                        "read, rebound or otherwise named".format(where, form))
    bindings = _top_level_bindings(tree)
    if len(bindings) > 1:
        raise GateError("{}: RENDERER_DECL is bound {} times at top level; exactly one top-level assignment "
                        "or annotated assignment is a supported declaration".format(where, len(bindings)))
    return bindings


# A fold longer than this is dropped. For the exact-name rule any cap at least as long as the reserved name
# (13 characters) is exact, since concatenation only lengthens a string; the cap bounds the memory a long +
# chain can take. For the dotted-component rule the cap is a bound, not exact: a spelled path longer than
# _FOLD_CAP is not folded and stays in the residual _declared_renderer_stems discloses.
_FOLD_CAP = 64


def _folded_strings(tree):
    """{id(node): str} for every expression node whose string value a BOUNDED constant fold computes: a str
    constant (an implicit concatenation is already one), a bytes constant decoded as latin-1 (every byte
    decodes, so a spelled bytes name or path is judged by the same rules as a str one; a consumer such as
    pickle reads spelled bytes directly, with no decode in the source), an f-string whose every part is
    such a value (a replacement field with no conversion other than !s and no format spec), and a + of two
    such operands. Folded bottom-up over the reversed breadth-first walk, where every child precedes its
    parent, so a long chain needs no recursion; a value longer than _FOLD_CAP is dropped."""
    folded = {}
    for node in reversed(list(ast.walk(tree))):
        if isinstance(node, ast.Constant):
            if isinstance(node.value, str):
                value = node.value
            elif isinstance(node.value, bytes):
                value = node.value.decode("latin-1")
            else:
                value = None
        elif isinstance(node, ast.FormattedValue):
            value = (folded.get(id(node.value)) if node.conversion in (-1, ord("s")) and node.format_spec is None
                     else None)
        elif isinstance(node, ast.JoinedStr):
            parts = [folded.get(id(part)) for part in node.values]
            value = None if any(part is None for part in parts) else "".join(parts)
        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            left, right = folded.get(id(node.left)), folded.get(id(node.right))
            value = None if left is None or right is None else left + right
        else:
            continue
        if value is not None and len(value) <= _FOLD_CAP:
            folded[id(node)] = value
    return folded


def _unsupported_binding(tree):
    """The description of the first use of RENDERER_DECL in `tree` other than ONE single-target top-level
    Assign or AnnAssign to the bare name, or None when there is none. The identifier scan is bounded by
    construction, never node shape by node shape: for EVERY walked node, EVERY string-valued or
    list-of-string field it carries (ast.iter_fields) is refused when the reserved name is one of its
    dot-separated components. One rule therefore covers a def/class name, a parameter, a type parameter, an
    except-as, a match capture, MatchMapping.rest, MatchClass.kwd_attrs, a global/nonlocal name, an
    attribute, a keyword-argument name, an import alias or module in any dotted position (import-as,
    import RENDERER_DECL.sub, from RENDERER_DECL import x), and any string field a later Python adds. A
    Name and a Constant are the two nodes the rule routes around. A Name, because the declaration itself
    is one: the allowed top-level targets pass, any other Store/Del is refused naming its binder form, and
    a Load of the bare name is refused because RENDERER_DECL[...] = ... or RENDERER_DECL.update(...)
    changes in place the value the reader recovered; a chained assignment is refused because its other
    target is an alias. A Constant, because the bounded fold below judges its value.

    A string or bytes CONSTANT is governed by the bounded fold, never the identifier rule (a bytes value
    folds decoded as latin-1, so a spelled bytes name or path is judged like a str one): any expression
    _folded_strings computes to exactly 'RENDERER_DECL' is refused unless _inert allows it as one of
    the TWO EXEMPT USES, a comparison operand or a third or later positional argument of a call with no
    starred argument before it, and any folded value whose dot- or colon-separated components include the
    name is refused with NO exemption, because a resolver splits such a path at run time and reaches the
    declared value through it (pkgutil.resolve_name('pkg.RENDERER_DECL'), pkgutil.resolve_name
    ('pkg:RENDERER_DECL'), operator.attrgetter('m.RENDERER_DECL')); this file's own scan never
    spells such a path in a foldable string. Dot and colon are the ONLY separators this rule knows; a
    spelled path in any other resolver syntax is NOT detected and is part of the residual
    _declared_renderer_stems discloses. Both exemptions are required by this file's own scan
    (self-test case (q)): the walk compares folded values and str fields against the literal name, and
    _parse_module takes it as the third positional argument (purpose). The exemptions are allowed uses, NOT
    proof of inertness: an overloaded comparison method (ANY comparison method receives the spelled name as
    its operand; __eq__, __ne__, __lt__ and __contains__ are examples, never a closed list) and a
    helper taking it as a third or later argument (functools.partial(setattr, module, 'RENDERER_DECL'))
    each consume the literal name at run time and can store or read through it; both routes are part of the
    residual _declared_renderer_stems discloses."""
    allowed = {id(t) for t, _ in _top_level_bindings(tree)}
    parents = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[id(child)] = node
    folded = _folded_strings(tree)

    def _where(node):
        # The innermost enclosing compound statement whose body holds the binder, if any.
        child, anc = node, parents.get(id(node))
        while anc is not None and not isinstance(anc, ast.Module):
            name = type(anc).__name__
            if name in _BLOCK_FORMS:
                if name not in _SCOPE_FORMS or child is anc.body or (
                        isinstance(anc.body, list) and any(child is stmt for stmt in anc.body)):
                    return " inside " + _BLOCK_FORMS[name]
            child, anc = anc, parents.get(id(anc))
        return ""

    def _stores(node):
        return isinstance(node.ctx, (ast.Store, ast.Del))

    def _names_decl(node):
        return folded.get(id(node)) == "RENDERER_DECL"

    def _inert(node):
        # A comparison operand, or a third or later positional argument with no starred argument before it:
        # every direct namespace write (setattr, operator.setitem, dict.__setitem__, a bound __setitem__ or
        # setdefault) takes its key first or second, so a later argument (the purpose this file passes to
        # _parse_module, say) is not that key; a helper that takes it later is in the disclosed residual.
        parent = parents.get(id(node))
        if isinstance(parent, ast.Compare):
            return True
        if not isinstance(parent, ast.Call):
            return False
        for index, arg in enumerate(parent.args):
            if isinstance(arg, ast.Starred):
                return False
            if arg is node:
                return index >= 2
        return False

    for node in tree.body:
        if (isinstance(node, ast.Assign) and len(node.targets) > 1
                and any(id(t) in allowed for t in node.targets)):
            return "a chained assignment (each other target is an alias of the declared value)"
    for node in ast.walk(tree):
        if _names_decl(node) and not _inert(node):
            return ("the string 'RENDERER_DECL' outside a comparison or a later call argument (a first or "
                    "second call argument such as globals().setdefault('RENDERER_DECL', ...), a dict key, or "
                    "a value a store can use)" + _where(node))
        value = folded.get(id(node))
        if (value is not None and value != "RENDERER_DECL"
                and "RENDERER_DECL" in value.replace(":", ".").split(".")):
            return ("a string whose dot- or colon-separated components include RENDERER_DECL (a resolver "
                    "such as pkgutil.resolve_name or operator.attrgetter splits such a path at run time "
                    "and can reach the declared value through it)" + _where(node))
        if isinstance(node, ast.Name):
            if node.id != "RENDERER_DECL" or id(node) in allowed:
                continue
            if not _stores(node):
                return ("a read of the bare name (through which RENDERER_DECL[...] = ... or "
                        "RENDERER_DECL.update(...) changes the value in place)" + _where(node))
            binder, unpack = parents.get(id(node)), False
            while isinstance(binder, (ast.Tuple, ast.List, ast.Starred)):
                unpack = True
                binder = parents.get(id(binder))
            kind = type(binder).__name__
            form = _BINDER_FORMS.get(kind, "a {} statement".format(kind))
            if unpack:
                form = "a tuple, list or starred unpacking in " + form
            if kind in ("comprehension", "withitem"):
                binder = parents.get(id(binder))
            return form + _where(binder)
        if isinstance(node, ast.Constant):
            continue  # a string constant is judged by the fold rule above, never by the identifier rule
        if isinstance(node, ast.alias) and node.asname is None and node.name == "*":
            return "a wildcard import" + _where(node)
        if isinstance(node, ast.Subscript):
            if _stores(node) and _names_decl(node.slice):
                return ("a subscript store or delete keyed 'RENDERER_DECL' (such as vars() or globals())"
                        + _where(node))
        elif isinstance(node, ast.Call):
            if (isinstance(node.func, ast.Name) and node.func.id in ("setattr", "delattr")
                    and len(node.args) >= 2 and _names_decl(node.args[1])):
                return "a {} call naming RENDERER_DECL".format(node.func.id) + _where(node)
        for field, value in ast.iter_fields(node):
            for item in (value if isinstance(value, list) else (value,)):
                if isinstance(item, str) and "RENDERER_DECL" in item.split("."):
                    return ("the identifier RENDERER_DECL in the {} field of an ast.{} node (every "
                            "string-valued or list-of-string AST field is refused, whatever the node "
                            "shape)".format(field, type(node).__name__) + _where(node))
    return None


def _declared_renderer_stems(tools_dir):
    """Every tools/<stem>.py carrying a top-level RENDERER_DECL declaration as _declaration (shared with the
    reader) defines it, listed by an explicit os.scandir (a glob returns nothing on an unreadable directory
    and would pass clean) and parsed with ast, never imported. Discovery is flat: only the entries directly
    in tools/ whose names end in '.py'; a subdirectory's contents are out of scope.

    Fail-closed (GateError) on an unlistable directory; on a *.py entry that is not a regular file once
    symlinks are followed (a dangling or looping symlink, a symlink to a directory or other non-file, or a
    directory, FIFO, socket or device named *.py), since a candidate is enumerated by NAME before its type
    is checked and is never skipped; on an unreadable or unparsable *.py file or one declaring a source
    encoding other than UTF-8 (_parse_module); and on any binding, read or spelling of RENDERER_DECL the
    walk sees other than ONE single-target top-level Assign or AnnAssign to the bare name (_declaration),
    each of which could hide a declaration or change it in place. A symlink to a regular file is read
    through. RENDERER_DECL is a RESERVED name: the walk is conservative and also refuses an unrelated
    function-local reuse of it, or the string anywhere but a comparison or a third or later call argument,
    which fails in the safe direction.

    DISCLOSED RESIDUAL (disclose-guard-residuals): the walk sees the name only where the source spells it, in
    a string-valued or list-of-string AST field (an identifier, an attribute, an import name, a
    keyword-argument name, a pattern or parameter name, or any such field a later Python adds), or as a
    string the bounded fold of _folded_strings computes (a str or bytes constant, bytes decoded as
    latin-1, an f-string, a + chain), where both a fold equal to the name and a fold
    whose dot- or colon-separated components include it (a pkgutil.resolve_name or operator.attrgetter
    path) are refused. What remains is beyond this static scan, which does not detect it and does not claim
    to: a SPELLED name or path framed by any resolver syntax other than dot- or colon-separated components,
    such as a str.format or string.Formatter replacement-field path ('0[RENDERER_DECL]', whose bracket
    field string.Formatter().get_field resolves to the declared value) or pickle opcode text (a GLOBAL
    frame is newline-separated, so a spelled pickle reaches a module attribute with no dot or colon in
    sight); a name or resolver path computed any other way (a join, %-formatting, str.format, a case
    change, a decode of computed bytes, chr, an f-string format spec) or folded to more than _FOLD_CAP
    characters, then stored
    through setattr(module, name, ...), globals()[name] = ..., globals().update({name: ...}), handed to a
    resolver (pkgutil.resolve_name, operator.attrgetter, importlib) or consumed by any other namespace
    write or read; the literal name consumed through one of the two EXEMPT USES, by a comparison operand
    (ANY overloaded comparison method receives the spelled name and can store or read through it; __eq__,
    __ne__, __lt__ and __contains__ are examples, never a closed list) or
    by a helper taking it as a third or later positional argument that then stores or reads, such as
    functools.partial(setattr, module, 'RENDERER_DECL'); a bulk copy that never
    names it, such as globals().update(vars(other)); exec, eval or compile of source text; an import-time
    side effect of another module; a module-level __getattr__ that answers the name without binding it; and
    an in-place change to the declared dict reached without spelling the name (by iterating
    globals().values(), say), which would make the value Python runs differ from the value read."""
    try:
        with os.scandir(tools_dir) as it:
            names = sorted(e.name for e in it if e.name.endswith(".py"))
    except OSError as exc:
        raise GateError("tools/: cannot list for RENDERER_DECL ({})".format(exc))
    stems = []
    for name in names:
        where = "tools/" + name
        path = tools_dir / name
        try:
            mode = os.stat(path).st_mode  # follows a symlink: a dangling or looping link raises here
        except OSError as exc:
            raise GateError("{}: cannot resolve this *.py entry to scan it for RENDERER_DECL (a dangling "
                            "or looping symlink?) ({})".format(where, exc))
        if not stat.S_ISREG(mode):
            raise GateError("{}: this *.py entry is not a regular file (or a symlink to one), so it cannot "
                            "be scanned for RENDERER_DECL and is refused rather than skipped".format(where))
        if _declaration(_parse_module(path, where, "RENDERER_DECL"), where):
            stems.append(name[:-3])
    return stems


def _local_imports(path, tools_dir, where):
    """The set of pack-local module stems (a tools/<stem>.py exists) directly imported by `path`. Parses
    Import/ImportFrom nodes only. Fail-closed (GateError) on a relative import (level > 0) or a wildcard
    `from <pack-local> import *`, both of which a static closure cannot resolve honestly."""
    tree = _parse_module(path, where, "its imports")
    stems = set()

    def _consider(root):
        if (tools_dir / (root + ".py")).is_file():
            stems.add(root)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                _consider(alias.name.partition(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                raise GateError("{}: a relative import cannot be resolved into the pack-local closure "
                                "(fail-closed)".format(where))
            root = (node.module or "").partition(".")[0]
            is_wildcard = any(alias.name == "*" for alias in node.names)
            if is_wildcard and (tools_dir / (root + ".py")).is_file():
                raise GateError("{}: a wildcard import of the pack-local module {!r} cannot be resolved "
                                "into the closure (fail-closed)".format(where, root))
            if root:
                _consider(root)
    return stems


def compute_closure(entry_stem, tools_dir):
    """The ordered pack-local import closure of tools/<entry_stem>.py: the entrypoint first, then every
    transitively-imported tools/<module>.py bytewise by repo-relative path. Returns a list of repo-relative
    paths ('tools/<stem>.py'). Fail-closed on an unresolvable import or unreadable member."""
    entry_rel = "tools/{}.py".format(entry_stem)
    seen = {entry_stem}
    frontier = [entry_stem]
    while frontier:
        stem = frontier.pop()
        deps = _local_imports(tools_dir / (stem + ".py"), tools_dir, "tools/{}.py".format(stem))
        for dep in sorted(deps):
            if dep not in seen:
                seen.add(dep)
                frontier.append(dep)
    rest = sorted("tools/{}.py".format(s) for s in seen if s != entry_stem)
    return [entry_rel] + rest


def framed_code_digest(closure, root):
    """SHA-256 over the concatenated framed records '<path>\\t<bytes>\\t<sha256>\\n' per closure file in
    order (never a raw byte concatenation), so a per-file change or a cross-boundary byte move both change
    the digest. Fail-closed on an unreadable member."""
    records = []
    for rel in closure:
        try:
            data = (root / rel).read_bytes()
        except OSError as exc:
            raise GateError("cannot read closure member {} ({})".format(rel, exc))
        records.append("{}\t{}\t{}\n".format(rel, len(data), _sha256(data)))
    return _sha256("".join(records).encode("utf-8"))


def build_rows(root):
    """One row per declared renderer: renderer-id, entrypoint, semantics-revision, targets (from the
    generator's GENSRC_OUTPUTS, verbatim including a tree's trailing '/'), the ordered import closure, and
    the framed code-digest. Fail-closed on an incomplete roster (a tools/*.py declares RENDERER_DECL but is
    absent from RENDERERS), any malformed declaration, or an unresolvable import."""
    tools_dir = root / "tools"
    unlisted = [stem for stem in _declared_renderer_stems(tools_dir) if stem not in RENDERERS]
    if unlisted:
        raise GateError("renderer roster incomplete: {} declare(s) RENDERER_DECL but are absent from "
                        "RENDERERS; add each to RENDERERS".format(
                            ", ".join("tools/{}.py".format(s) for s in unlisted)))
    rows = []
    seen_ids = set()
    for stem in RENDERERS:
        entry = tools_dir / (stem + ".py")
        where = "tools/{}.py".format(stem)
        if not entry.is_file():
            raise GateError("{}: declared renderer generator is absent".format(where))
        rid, rev = _read_renderer_decl(entry, where)
        if rid in seen_ids:
            raise GateError("duplicate renderer-id {!r}".format(rid))
        seen_ids.add(rid)
        try:
            decl = gen_gensrc._read_declaration(entry, where)
        except (ValueError, OSError) as exc:
            raise GateError("{}: cannot read GENSRC_OUTPUTS ({})".format(where, exc))
        targets = [e["target"] for e in decl]
        closure = compute_closure(stem, tools_dir)
        rows.append({"renderer-id": rid, "entrypoint": "tools/{}.py".format(stem),
                     "semantics-revision": rev, "targets": targets, "closure": closure,
                     "code-digest": framed_code_digest(closure, root)})
    return rows


def render(rows):
    lines = ["# .aiqt/core/renderers.toml: the RENDERER/GENERATOR DECLARATION (VER-CORE 6.5, R9-5).",
             "# GENERATED by tools/gen_renderers.py from each adapter generator's RENDERER_DECL, its",
             "# GENSRC_OUTPUTS targets, and its ordered pack-local import closure; do not hand-edit.",
             "# code-digest is SHA-256 over framed '<path>\\t<bytes>\\t<sha256>' records in closure order,",
             "# so any edit anywhere in a generator's closure forces a declaration diff (6.5).",
             "",
             "format-version = 1",
             ""]
    for row in rows:
        lines.append("[[renderer]]")
        lines.append("renderer-id = {}".format(_toml_str(row["renderer-id"])))
        lines.append("entrypoint = {}".format(_toml_str(row["entrypoint"])))
        lines.append("semantics-revision = {}".format(row["semantics-revision"]))
        lines.append("targets = [{}]".format(", ".join(_toml_str(t) for t in row["targets"])))
        lines.append("closure = [{}]".format(", ".join(_toml_str(c) for c in row["closure"])))
        lines.append("code-digest = {}".format(_toml_str(row["code-digest"])))
        lines.append("")
    return "\n".join(lines)


def run(root, check):
    try:
        text = render(build_rows(root))
    except GateError as exc:
        print("error: {}; fail-closed".format(exc), file=sys.stderr)
        return 2
    if reconcile(root / RENDERERS_REL, text, check):
        print("drift: {} is out of date; run tools/gen_renderers.py".format(RENDERERS_REL),
              file=sys.stderr)
        return 1
    if not check:
        print("wrote {} ({} renderers)".format(RENDERERS_REL, text.count("[[renderer]]")))
    return 0


def main():
    args = sys.argv[1:]
    if "--self-test" in args:
        return self_test_main()
    root = repo_root()
    if "--root" in args:
        i = args.index("--root")
        if i + 1 >= len(args):
            print("usage: gen_renderers.py [--check] [--root DIR] | --self-test", file=sys.stderr)
            return 2
        root = Path(args[i + 1]).resolve()
    return run(root, "--check" in args)


# --- self-test ----------------------------------------------------------------------------------------
# Synthetic generators in a tempdir prove this generator's own fail-closed invariants:
#   (a) a conformant set generates then re-checks drift-clean, and two runs are byte-identical (determinism);
#   (b) a helper edit inside a closure changes the framed code-digest (closure completeness);
#   (c) a mutated renderers.toml is caught by --check (exit 1);
#   (d) a wildcard import of a pack-local module fails closed (exit 2), an unresolvable-closure case;
#   (e) a missing/malformed RENDERER_DECL fails closed (exit 2).
#   (g) a tools/*.py that declares RENDERER_DECL but is absent from RENDERERS fails --check (exit 2), even
#       when renderers.toml is otherwise current (roster completeness, not only freshness).
#   (h) each of the _UNSUPPORTED_BINDINGS forms of RENDERER_DECL in an unlisted tools/*.py (unpacking,
#       starred, conditional, try, with, loop, walrus, augmented, del, import-as, wildcard import, global,
#       nonlocal, except-as, match capture, def, class, parameter, type parameter, comprehension target,
#       type alias, a store through the module object by attribute, vars()/globals() subscript or
#       setattr, a literal-name store through any other call (qualified or aliased setattr, __setattr__,
#       setdefault, __setitem__, dict.__setitem__, operator.setitem), a dict-display or keyword update, a
#       variable holding the name, an f-string, split f-string, concatenated or spelled-bytes key (folded
#       decoded as latin-1), an import naming it, an
#       import from it (the module position), an import of a package rooted at it (the dotted module
#       position), a match-mapping rest, a match-star name, and a later argument that a starred argument
#       before it can shift to the key position)
#       fails --check (exit 2) instead of reading as "no declaration".
#   (i) a *.py entry that is a dangling symlink, a looping symlink, a symlink to a directory, a directory,
#       or a FIFO fails --check (exit 2) instead of being skipped.
#   (j) discovery and the reader share one binding predicate: a listed generator declaring RENDERER_DECL by
#       an annotated assignment alone renders that declaration, while an ordinary declaration followed by an
#       annotated (or a second plain) rebinding, or a bare annotation, fails closed (exit 2), and the reader
#       alone also refuses two bindings; each _MUTATED in-place change to the declared dict (a subscript
#       store, an update call, a chained-assignment alias, a module attribute read, a class-pattern keyword
#       read, a pkgutil.resolve_name path string, an operator.attrgetter path string spelled as str or as
#       bytes decoded at run time) also fails closed, so
#       none of these routes can make the value Python runs differ from the value read.
#   (k) a PEP 263 cookie cannot hide a declaration: a UTF-7 cookie that makes a UTF-8 comment a declaration,
#       and any other non-UTF-8 cookie, fail --check (exit 2); a UTF-8 BOM is parsed as Python parses it.
#   (l) a parser or compiler stack overflow (RecursionError or MemoryError) while parsing a tools/*.py, or
#       while evaluating a RENDERER_DECL literal, fails with exit 2 (GateError), never a traceback. The
#       overflow is INJECTED at the gate's ast.parse and ast.literal_eval calls rather than provoked by a
#       deeply nested body: the depth at which CPython overflows is an interpreter implementation limit
#       (parser stack, C stack, recursion checks) that changes between patch releases, so a nesting body
#       that overflows on one interpreter parses cleanly on another and the case would flip.
#   (m) the refusal names where a binder runs: a walrus in a decorator, a default or a class base runs in
#       the enclosing scope, so it is not reported as inside the def or class; one in a body is.
#   (n) an unreadable *.py is reported as "cannot read it to scan for", an unparsable one as "cannot parse
#       it to scan for"; the FULL prefix is asserted, so an older or shorter wording fails the case.
#   (o) a comparison against the string RENDERER_DECL, a third positional argument (where no direct namespace
#       write takes its key), a longer string mentioning it, or an f-string whose !r or !a conversion or
#       format spec renders a different string at run time (the fold must not over-match any), stays allowed.
#   (p) the bounded string fold keeps no value longer than _FOLD_CAP.
#   (q) this file passes its own scan (a renderer-free tools/*.py that discovery reads like any other).

_HELPER = "VALUE = 1\n"

_ENTRY_GOOD = ('import sys\n'
               'from pathlib import Path\n'
               'from selfhelper import VALUE\n'
               'RENDERER_DECL = {"renderer-id": "alpha", "semantics-revision": 1}\n'
               'GENSRC_OUTPUTS = (\n'
               '    {"target": "OUT.md", "kind": "file",\n'
               '     "sources": ("src.txt",), "regenerate": "python3 tools/gen_alpha.py"},\n'
               ')\n')

_ENTRY_NODECL = ('from selfhelper import VALUE\n'
                 'GENSRC_OUTPUTS = (\n'
                 '    {"target": "OUT.md", "kind": "file",\n'
                 '     "sources": ("src.txt",), "regenerate": "python3 tools/gen_alpha.py"},\n'
                 ')\n')

_ENTRY_WILDCARD = ('from selfhelper import *\n'
                   'RENDERER_DECL = {"renderer-id": "alpha", "semantics-revision": 1}\n'
                   'GENSRC_OUTPUTS = (\n'
                   '    {"target": "OUT.md", "kind": "file",\n'
                   '     "sources": ("src.txt",), "regenerate": "python3 tools/gen_alpha.py"},\n'
                   ')\n')

_ENTRY_BADID = ('from selfhelper import VALUE\n'
                'RENDERER_DECL = {"renderer-id": "Bad ID", "semantics-revision": 1}\n'
                'GENSRC_OUTPUTS = (\n'
                '    {"target": "OUT.md", "kind": "file",\n'
                '     "sources": ("src.txt",), "regenerate": "python3 tools/gen_alpha.py"},\n'
                ')\n')


_ENTRY_UNLISTED = ('RENDERER_DECL = {"renderer-id": "beta", "semantics-revision": 1}\n'
                   'GENSRC_OUTPUTS = (\n'
                   '    {"target": "BETA.md", "kind": "file",\n'
                   '     "sources": ("src.txt",), "regenerate": "python3 tools/gen_beta.py"},\n'
                   ')\n')


_DECL_LITERAL = '{"renderer-id": "beta", "semantics-revision": 1}'
# (h) one unlisted tools/gen_beta.py body per unsupported static binding form; each must fail closed.
_UNSUPPORTED_BINDINGS = (
    ("tuple unpacking", "RENDERER_DECL, = ({},)\n".format(_DECL_LITERAL)),
    ("list unpacking", "[RENDERER_DECL] = [{}]\n".format(_DECL_LITERAL)),
    ("starred unpacking", "*RENDERER_DECL, = ({},)\n".format(_DECL_LITERAL)),
    ("conditional", "if True:\n    RENDERER_DECL = {}\n".format(_DECL_LITERAL)),
    ("try block", "try:\n    RENDERER_DECL = {}\nexcept Exception:\n    pass\n".format(_DECL_LITERAL)),
    ("with block", "import contextlib\nwith contextlib.nullcontext({}) as RENDERER_DECL:\n    pass\n"
                   .format(_DECL_LITERAL)),
    ("for loop", "for RENDERER_DECL in ({},):\n    pass\n".format(_DECL_LITERAL)),
    ("assignment expression", "(RENDERER_DECL := {})\n".format(_DECL_LITERAL)),
    ("augmented assignment", "RENDERER_DECL |= {}\n".format(_DECL_LITERAL)),
    ("del", "del RENDERER_DECL\n"),
    ("import-as", "from selfhelper import VALUE as RENDERER_DECL\n"),
    ("wildcard import", "from selfhelper import *\n"),
    ("global statement", "def f():\n    global RENDERER_DECL\n    RENDERER_DECL = {}\n"
                         .format(_DECL_LITERAL)),
    ("nonlocal statement", "def f():\n    RENDERER_DECL = 0\n    def g():\n        nonlocal RENDERER_DECL\n"
                           "    return g\n"),
    ("except-as", "try:\n    pass\nexcept Exception as RENDERER_DECL:\n    pass\n"),
    ("match capture", "match {}:\n    case RENDERER_DECL:\n        pass\n".format(_DECL_LITERAL)),
    ("def", "def RENDERER_DECL():\n    pass\n"),
    ("class", "class RENDERER_DECL:\n    pass\n"),
    ("parameter", "def f(RENDERER_DECL):\n    pass\n"),
    ("type parameter", "def f[RENDERER_DECL]():\n    pass\n"),
    ("comprehension target", "[0 for RENDERER_DECL in ({},)]\n".format(_DECL_LITERAL)),
    ("type alias", "type RENDERER_DECL = dict\n"),
    ("sys.modules attribute", "import sys\nsys.modules[__name__].RENDERER_DECL = {}\n".format(_DECL_LITERAL)),
    ("vars() subscript", "vars()['RENDERER_DECL'] = {}\n".format(_DECL_LITERAL)),
    ("globals() subscript", "globals()['RENDERER_DECL'] = {}\n".format(_DECL_LITERAL)),
    ("setattr call", "import sys\nsetattr(sys.modules[__name__], 'RENDERER_DECL', {})\n"
                     .format(_DECL_LITERAL)),
    ("qualified setattr call", "import builtins, sys\n"
                               "builtins.setattr(sys.modules[__name__], 'RENDERER_DECL', {})\n"
                               .format(_DECL_LITERAL)),
    ("aliased setattr call", "import sys\nfrom builtins import setattr as put\n"
                             "put(sys.modules[__name__], 'RENDERER_DECL', {})\n".format(_DECL_LITERAL)),
    ("__setattr__ call", "import sys\nsys.modules[__name__].__setattr__('RENDERER_DECL', {})\n"
                         .format(_DECL_LITERAL)),
    ("setdefault call", "globals().setdefault('RENDERER_DECL', {})\n".format(_DECL_LITERAL)),
    ("__setitem__ call", "globals().__setitem__('RENDERER_DECL', {})\n".format(_DECL_LITERAL)),
    ("dict.__setitem__ call", "dict.__setitem__(globals(), 'RENDERER_DECL', {})\n".format(_DECL_LITERAL)),
    ("operator.setitem call", "import operator\noperator.setitem(globals(), 'RENDERER_DECL', {})\n"
                              .format(_DECL_LITERAL)),
    ("keyword update", "globals().update(RENDERER_DECL={})\n".format(_DECL_LITERAL)),
    ("name held in a variable", "key = 'RENDERER_DECL'\nglobals()[key] = {}\n".format(_DECL_LITERAL)),
    ("dict-display update", "globals().update({{('RENDERER_DECL'): {}}})\n".format(_DECL_LITERAL)),
    ("f-string subscript key", "vars()[f'RENDERER_DECL'] = {}\n".format(_DECL_LITERAL)),
    ("split f-string subscript key", "vars()[f'RENDERER_{{({!r})}}'] = {}\n".format("DECL", _DECL_LITERAL)),
    ("concatenated subscript key", "globals()['RENDERER_' + 'DECL'] = {}\n".format(_DECL_LITERAL)),
    ("bytes subscript key", "vars()[b'RENDERER_DECL'.decode()] = {}\n".format(_DECL_LITERAL)),
    ("import naming it", "from gen_alpha import RENDERER_DECL as other\nother['semantics-revision'] = 99\n"),
    ("import from it", "from RENDERER_DECL import x\n"),
    ("dotted import root", "import RENDERER_DECL.sub\n"),
    ("match mapping rest", "match {}:\n    case {{**RENDERER_DECL}}:\n        pass\n".format(_DECL_LITERAL)),
    ("match star", "match [{}]:\n    case [*RENDERER_DECL]:\n        pass\n".format(_DECL_LITERAL)),
    ("later argument after a starred one", "import sys\n"
                                           "setattr(*[], *[sys.modules[__name__]], 'RENDERER_DECL', {})\n"
                                           .format(_DECL_LITERAL)),
)

# (j) listed tools/gen_alpha.py bodies: the shared declaration predicate (one Assign or AnnAssign, no more).
_ALPHA_DECL = 'RENDERER_DECL = {"renderer-id": "alpha", "semantics-revision": 1}\n'
_ENTRY_ANNOTATED = _ENTRY_GOOD.replace(_ALPHA_DECL, _ALPHA_DECL.replace(" = ", ": dict = ", 1))
_REBOUND = (
    ("annotated rebinding", _ENTRY_GOOD + 'RENDERER_DECL: dict = {"renderer-id": "rebound", '
                                          '"semantics-revision": 99}\n'),
    ("plain rebinding", _ENTRY_GOOD + 'RENDERER_DECL = {"renderer-id": "rebound", '
                                      '"semantics-revision": 99}\n'),
    ("bare annotation", _ENTRY_GOOD.replace(
        _ALPHA_DECL,
        "# a bare annotation binds no value, so the reader must refuse it\nRENDERER_DECL: dict\n")),
)
# (j) listed tools/gen_alpha.py bodies that change the declared dict in place after it is defined, so the value
# Python runs would differ from the value read; each must fail closed.
_MUTATED = (
    ("a subscript store", _ENTRY_GOOD + 'RENDERER_DECL["semantics-revision"] = 99\n'),
    ("an update call", _ENTRY_GOOD
     + 'RENDERER_DECL.update([("semantics-revision", 99)])  # an in-place update\n'),
    ("a chained-assignment alias", _ENTRY_GOOD.replace(_ALPHA_DECL, "ALIAS = " + _ALPHA_DECL)
     + 'ALIAS["semantics-revision"] = 99\n'),
    ("a module attribute read", _ENTRY_GOOD
     + 'sys.modules[__name__].RENDERER_DECL["semantics-revision"] = 99\n'),
    ("a class-pattern keyword read", _ENTRY_GOOD
     + 'match sys.modules[__name__]:\n'
       '    case object(RENDERER_DECL=alias):\n'
       '        alias["semantics-revision"] = 99\n'),
    ("a pkgutil.resolve_name path string", _ENTRY_GOOD
     + "import pkgutil\n"
       "pkgutil.resolve_name(__name__ + ':RENDERER_DECL')['semantics-revision'] = 99\n"),
    ("an operator.attrgetter path string", _ENTRY_GOOD
     + "import operator, types\n"
       "operator.attrgetter('m.RENDERER_DECL')(types.SimpleNamespace(m=sys.modules[__name__]))"
       "['semantics-revision'] = 99\n"),
    ("a bytes operator.attrgetter path", _ENTRY_GOOD
     + "import operator, types\n"
       "operator.attrgetter(b'm.RENDERER_DECL'.decode())"
       "(types.SimpleNamespace(m=sys.modules[__name__]))['semantics-revision'] = 99\n"),
)

# (k) unlisted tools/gen_beta.py bodies (bytes) a coding cookie must not hide; '+AAo-' is UTF-7 for a newline.
_COOKIES = (
    ("UTF-7 cookie hiding a declaration",
     "# -*- coding: utf-7 -*-\n# +AAo-RENDERER_DECL = {}\n".format(_DECL_LITERAL).encode("ascii")),
    ("latin-1 cookie", b"# -*- coding: latin-1 -*-\nVALUE = 1\n"),
)
_BOM_DECL = b"\xef\xbb\xbf" + "RENDERER_DECL = {}\n".format(_DECL_LITERAL).encode("ascii")

# (l) (label, the ast attribute the overflow is injected at, the exception class). The parse injection fires
# only for tools/zz_deep.py, an unlisted file; the literal_eval injection fires on the first literal the gate
# evaluates, the listed tools/gen_alpha.py RENDERER_DECL.
_DEEP = (("parser RecursionError", "parse", RecursionError),
         ("parser MemoryError", "parse", MemoryError),
         ("literal RecursionError", "literal_eval", RecursionError),
         ("literal MemoryError", "literal_eval", MemoryError))
_DEEP_FILE = "tools/zz_deep.py"

# (m) (source, the expected _unsupported_binding description).
_WALRUS = "an assignment expression (walrus)"
_WHERE_MESSAGES = (
    ("@(RENDERER_DECL := (lambda f: f))\ndef f():\n    pass\n", _WALRUS),
    ("def f(x=(RENDERER_DECL := 1)):\n    pass\n", _WALRUS),
    ("class C((RENDERER_DECL := object)):\n    pass\n", _WALRUS),
    ("def f():\n    (RENDERER_DECL := 1)\n", _WALRUS + " inside a def block"),
    ("def g():\n    def f(x=(RENDERER_DECL := 1)):\n        pass\n", _WALRUS + " inside a def block"),
)

# (o) unlisted tools/gen_beta.py bodies whose only use of the name is one of the two exempt uses (allowed
# uses, NOT proof of inertness) or a string the fold must not over-match: a comparison, a longer-string
# mention, a third or later positional argument, and three f-strings (a !r conversion, a !a conversion and
# a format spec each render a DIFFERENT string at run time, so folding any of them as if it were the plain
# value would refuse a harmless mention); each must stay allowed.
_ALLOWED_USES = (
    ("comparison", "def is_decl(name):\n    return name == 'RENDERER_DECL' or 'RENDERER_DECL' != name\n"),
    ("mention in a longer string", "NOTE = 'this module carries no RENDERER_DECL'\n"),
    ("third positional argument", "def scan(path, where, purpose):\n    return purpose\n"
                                  "scan(None, None, 'RENDERER_DECL')\n"),
    ("f-string !r mention", "NOTE = f\"RENDERER_{'DECL'!r}\"\n"),
    ("f-string !a mention", "NOTE = f\"RENDERER_{'DECL'!a}\"\n"),
    ("f-string format-spec mention", "NOTE = f\"RENDERER_{'DECL':.2}\"\n"),
)


def _fixture(base, entry_body, helper_body=_HELPER):
    tools = base / "tools"
    tools.mkdir(parents=True)
    (base / ".aiqt" / "core").mkdir(parents=True)
    (base / "src.txt").write_text("source\n", encoding="utf-8")
    (tools / "gen_alpha.py").write_text(entry_body, encoding="utf-8")
    (tools / "selfhelper.py").write_text(helper_body, encoding="utf-8")


def self_test_main():
    import io
    import shutil
    import tempfile
    from contextlib import redirect_stdout, redirect_stderr

    global RENDERERS
    saved = RENDERERS
    RENDERERS = ("gen_alpha",)

    def run_quiet(root, check):
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            try:
                return run(root, check)
            except SystemExit as exc:
                return "raised SystemExit({!r})".format(exc.code)

    def run_refused(root, check):
        # The exit code AND the stderr text, for a vector that must be refused FOR ITS OWN REASON: asserting
        # only exit 2 would keep a vector green when it is refused for an unrelated reason (an unreadable or
        # unparsable fixture, say) while the guard under test no longer sees it.
        err = io.StringIO()
        with redirect_stdout(io.StringIO()), redirect_stderr(err):
            try:
                rc = run(root, check)
            except SystemExit as exc:
                rc = "raised SystemExit({!r})".format(exc.code)
        return rc, err.getvalue()

    try:
        tmp = Path(tempfile.mkdtemp(prefix="aiqt-gen-renderers-selftest-"))
    except OSError as exc:
        print("SELF-TEST ERROR: no writable temporary directory: {}".format(exc), file=sys.stderr)
        return 2
    failures = []
    try:
        # (a) conformant + determinism. The second generation must SUCCEED before its output is compared:
        # were its return ignored, a failed second run that writes nothing would compare the first output
        # with itself, so a nondeterminism defect could escape whenever regeneration fails. The injection
        # control proves a reported failure is recorded, never a silent pass.
        good = tmp / "good"
        _fixture(good, _ENTRY_GOOD)
        if run_quiet(good, check=False) != 0:
            failures.append("conformant: generation expected exit 0")
        if run_quiet(good, check=True) != 0:
            failures.append("conformant: regeneration expected drift-clean exit 0")
        first = (good / RENDERERS_REL).read_text(encoding="utf-8")

        def _determinism_failure(generate):
            rc = generate(good, False)
            if rc != 0:
                return "determinism: second generation expected exit 0, got {}".format(rc)
            if (good / RENDERERS_REL).read_text(encoding="utf-8") != first:
                return "determinism: two runs are not byte-identical"
            return None

        problem = _determinism_failure(run_quiet)
        if problem is not None:
            failures.append(problem)
        if _determinism_failure(lambda root, check: 2) != \
                "determinism: second generation expected exit 0, got 2":
            failures.append("determinism control: an injected failing second generation (no write) expected "
                            "a recorded failure, not a comparison of the first output with itself")

        # (b) a helper edit inside the closure changes the framed code-digest. The generation must SUCCEED
        # before its output is compared (the same shape as (a) and (c)); the injection control proves that
        # output written by a run that then reports failure is never accepted.
        edited = tmp / "edited"
        _fixture(edited, _ENTRY_GOOD, helper_body="VALUE = 2\n")

        def _closure_failure(generate):
            rc = generate(edited, False)
            if rc != 0:
                return "closure completeness: generation expected exit 0, got {}".format(rc)
            if (edited / RENDERERS_REL).read_text(encoding="utf-8") == first:
                return "closure completeness: a helper edit did not change the code-digest"
            return None

        problem = _closure_failure(run_quiet)
        if problem is not None:
            failures.append(problem)
        if _closure_failure(lambda root, check: (run_quiet(root, check), 2)[1]) != \
                "closure completeness: generation expected exit 0, got 2":
            failures.append("closure control: an injected failure after a real generation expected a "
                            "recorded failure, not acceptance of the written output")

        # (c) a mutated renderers.toml is caught by --check. The regeneration prerequisite records its own
        # failure: were it skipped silently, a drift-detection defect could escape whenever regeneration
        # fails, so the drift assertion runs only after a successful regeneration.
        if run_quiet(good, check=False) != 0:
            failures.append("mutated renderers.toml: prerequisite regeneration expected exit 0")
        else:
            target = good / RENDERERS_REL
            target.write_text(target.read_text(encoding="utf-8") + "\n# tamper\n", encoding="utf-8")
            if run_quiet(good, check=True) != 1:
                failures.append("mutated renderers.toml expected exit 1 (drift)")

        # (d) a wildcard import of a pack-local module fails closed (exit 2).
        wild = tmp / "wild"
        _fixture(wild, _ENTRY_WILDCARD)
        if run_quiet(wild, check=False) != 2:
            failures.append("wildcard pack-local import expected exit 2 (fail-closed)")

        # (e) a missing RENDERER_DECL fails closed (exit 2).
        nodecl = tmp / "nodecl"
        _fixture(nodecl, _ENTRY_NODECL)
        if run_quiet(nodecl, check=False) != 2:
            failures.append("missing RENDERER_DECL expected exit 2 (fail-closed)")

        # (f) a non-slug renderer-id in RENDERER_DECL ("Bad ID") fails closed (exit 2), so a malformed id
        # can never be rendered into a fresh renderers.toml (this round's #3).
        badid = tmp / "badid"
        _fixture(badid, _ENTRY_BADID)
        if run_quiet(badid, check=False) != 2:
            failures.append("non-slug renderer-id 'Bad ID' expected exit 2 (fail-closed, this round's #3)")

        # (g) roster completeness: renderers.toml is generated current for the listed gen_alpha, then
        # tools/gen_beta.py appears declaring RENDERER_DECL without being listed in RENDERERS. A freshness
        # comparison alone would pass, since both sides read the same RENDERERS; --check must fail closed.
        unlisted = tmp / "unlisted"
        _fixture(unlisted, _ENTRY_GOOD)
        if run_quiet(unlisted, check=False) != 0:
            failures.append("roster completeness: fixture generation expected exit 0")
        (unlisted / "tools" / "gen_beta.py").write_text(_ENTRY_UNLISTED, encoding="utf-8")
        if run_quiet(unlisted, check=True) != 2:
            failures.append("an unlisted tools/gen_beta.py declaring RENDERER_DECL expected --check exit 2 "
                            "(roster incomplete, fail-closed)")

        # (h) an unsupported static binding of RENDERER_DECL is refused, never read as absence.
        for label, body in _UNSUPPORTED_BINDINGS:
            case = tmp / ("binding-" + label.replace(" ", "-"))
            _fixture(case, _ENTRY_GOOD)
            if run_quiet(case, check=False) != 0:
                failures.append("binding {}: fixture generation expected exit 0".format(label))
            (case / "tools" / "gen_beta.py").write_text(body, encoding="utf-8")
            if label == "dotted import root":
                # the imported fixture package really exists, so under a weakened scan the import would
                # genuinely rebind the name at run time; discovery itself is flat and never enters it
                pkg = Path(case, "tools", "RENDERER_DECL")
                pkg.mkdir()
                (pkg / "__init__.py").write_text("", encoding="utf-8")
                (pkg / "sub.py").write_text("VALUE = 1\n", encoding="utf-8")
            rc, err = run_refused(case, check=True)
            if rc != 2 or "unsupported use of RENDERER_DECL" not in err:
                failures.append("an unlisted tools/gen_beta.py binding RENDERER_DECL by {} expected --check "
                                "exit 2 naming an unsupported use (fail-closed), got exit {}"
                                .format(label, rc))

        # (i) a *.py entry that is not a regular file is refused, never skipped.
        def _dangling(p):
            p.symlink_to("missing.py")

        def _looping(p):
            p.symlink_to(p.name)

        def _to_dir(p):
            (p.parent / "adir").mkdir()
            p.symlink_to("adir")

        def _fifo(p):
            os.mkfifo(str(p))

        for label, make in (("dangling symlink", _dangling), ("looping symlink", _looping),
                            ("symlink to a directory", _to_dir), ("directory", Path.mkdir),
                            ("FIFO", _fifo)):
            case = tmp / ("entry-" + label.replace(" ", "-"))
            _fixture(case, _ENTRY_GOOD)
            if run_quiet(case, check=False) != 0:
                failures.append("entry {}: fixture generation expected exit 0".format(label))
            try:
                make(case / "tools" / "gen_beta.py")
            except (OSError, AttributeError, NotImplementedError) as exc:
                failures.append("entry {}: cannot build the fixture ({})".format(label, exc))
                continue
            if run_quiet(case, check=True) != 2:
                failures.append("a tools/gen_beta.py that is a {} expected --check exit 2 (non-regular "
                                "*.py entry, fail-closed)".format(label))

        # (j) one declaration predicate for discovery and the reader.
        annotated = tmp / "decl-annotated"
        _fixture(annotated, _ENTRY_ANNOTATED)
        if run_quiet(annotated, check=False) != 0 or 'renderer-id = "alpha"' not in \
                (annotated / RENDERERS_REL).read_text(encoding="utf-8"):
            failures.append("a listed generator declaring RENDERER_DECL by an annotated assignment alone "
                            "expected generation exit 0 rendering renderer-id alpha (shared predicate)")
        for label, body in _REBOUND:
            case = tmp / ("decl-" + label.replace(" ", "-"))
            _fixture(case, body)
            if run_quiet(case, check=False) != 2:
                failures.append("a listed generator with {} of RENDERER_DECL expected generation exit 2 "
                                "(fail-closed)".format(label))
        rebound = tmp / "decl-annotated-rebinding" / "tools" / "gen_alpha.py"
        if not rebound.is_file():
            failures.append("the reader-alone fixture decl-annotated-rebinding/tools/gen_alpha.py is "
                            "missing, so the two-binding refusal was never exercised")
        else:
            try:
                _read_renderer_decl(rebound, "gen_alpha.py")
                failures.append("the reader alone expected GateError on an annotated rebinding of "
                                "RENDERER_DECL")
            except GateError as exc:
                if "bound 2 times at top level" not in str(exc):
                    failures.append("the reader alone expected the GateError to name the rebinding ('bound "
                                    "2 times at top level'), got {!r}".format(str(exc)))
        for label, body in _MUTATED:
            case = tmp / ("mutated-" + label.replace(" ", "-"))
            _fixture(case, body)
            rc, err = run_refused(case, check=False)
            if rc != 2 or "unsupported use of RENDERER_DECL" not in err:
                failures.append("a listed generator changing RENDERER_DECL in place through {} expected "
                                "generation exit 2 naming an unsupported use (the value run would differ "
                                "from the value read), got exit {}".format(label, rc))

        # (k) a coding cookie cannot hide a declaration; a UTF-8 BOM parses as Python parses it.
        for label, data in _COOKIES:
            case = tmp / ("cookie-" + label.split()[0])
            _fixture(case, _ENTRY_GOOD)
            if run_quiet(case, check=False) != 0:
                failures.append("cookie {}: fixture generation expected exit 0".format(label))
            (case / "tools" / "gen_beta.py").write_bytes(data)
            if run_quiet(case, check=True) != 2:
                failures.append("an unlisted tools/gen_beta.py with a {} expected --check exit 2 "
                                "(fail-closed)".format(label))
        bom = tmp / "cookie-bom"
        _fixture(bom, _ENTRY_GOOD)
        (bom / "tools" / "gen_beta.py").write_bytes(_BOM_DECL)
        try:
            if _declared_renderer_stems(bom / "tools") != ["gen_alpha", "gen_beta"]:
                failures.append("a UTF-8 BOM before a declaration expected discovery of gen_beta")
        except GateError as exc:
            failures.append("a UTF-8 BOM before a declaration expected discovery, got GateError ({})"
                            .format(exc))

        # (l) a parser or compiler stack overflow is a GateError (exit 2), never a traceback. The overflow is
        # injected (see the case note above), so the case is identical on every interpreter and turns red if
        # MemoryError or RecursionError is dropped from either mapping.
        for label, attr, exc_class in _DEEP:
            case = tmp / ("deep-" + label.replace(" ", "-"))
            _fixture(case, _ENTRY_GOOD)
            (case / "tools" / "zz_deep.py").write_text("VALUE = 1\n", encoding="utf-8")
            real = getattr(ast, attr)

            def overflow(*args, _real=real, _attr=attr, _exc=exc_class, **kwargs):
                if _attr == "literal_eval" or kwargs.get("filename") == _DEEP_FILE:
                    raise _exc("injected {} overflow".format(_attr))
                return _real(*args, **kwargs)

            setattr(ast, attr, overflow)
            try:
                rc = run_quiet(case, check=False)
            except (MemoryError, RecursionError) as exc:
                rc = "raised {}".format(type(exc).__name__)
            finally:
                setattr(ast, attr, real)
            if rc != 2:
                failures.append("an injected {} ({}) expected exit 2 (GateError), got {}"
                                .format(label, "tools/gen_alpha.py RENDERER_DECL" if attr == "literal_eval"
                                        else _DEEP_FILE, rc))

        # (m) an accurate refusal message.
        for source, expected in _WHERE_MESSAGES:
            got = _unsupported_binding(ast.parse(source))
            if got != expected:
                failures.append("binding message for {!r}: expected {!r}, got {!r}"
                                .format(source, expected, got))

        # (n) a file that cannot be read is reported as a read failure, an unparsable one as a parse failure. A
        # directory is a read failure on every platform and uid (a mode-0 file is readable by root); the
        # MemoryError read is injected through an object whose read_bytes raises it. The FULL diagnostic
        # prefix ("cannot read it to scan for", "cannot parse it to scan for") is asserted, so an older or
        # shorter wording fails the case.
        class _Unreadable:
            def read_bytes(self):
                raise MemoryError("injected read overflow")

        messages = tmp / "messages"
        (messages / "unreadable.py").mkdir(parents=True)
        (messages / "unparsable.py").write_bytes(b"(\n")
        for name, expected in (("unreadable.py", "cannot read it to scan for"),
                               ("oversized.py", "cannot read it to scan for"),
                               ("unparsable.py", "cannot parse it to scan for")):
            path = _Unreadable() if name == "oversized.py" else messages / name
            try:
                _parse_module(path, "tools/" + name, "RENDERER_DECL")
                failures.append("tools/{} expected GateError ({})".format(name, expected))
            except GateError as exc:
                if not str(exc).startswith("tools/{}: {} ".format(name, expected)):
                    failures.append("tools/{} expected a message starting {!r}, got {!r}"
                                    .format(name, expected, str(exc)))

        # (o) every control stays allowed: a comparison against the string, a third positional argument, a
        # longer string mentioning it, and the f-string !r, !a and format-spec mentions; each is an exempt
        # use or a non-matching string, never proof of inertness.
        for label, body in _ALLOWED_USES:
            case = tmp / ("allowed-" + label.replace(" ", "-"))
            _fixture(case, _ENTRY_GOOD)
            if run_quiet(case, check=False) != 0:
                failures.append("allowed {}: fixture generation expected exit 0".format(label))
            (case / "tools" / "gen_beta.py").write_text(body, encoding="utf-8")
            if run_quiet(case, check=True) != 0:
                failures.append("an unlisted tools/gen_beta.py with a {} of RENDERER_DECL expected --check "
                                "exit 0 (an exempt use or a non-matching string, never proof of "
                                "inertness)".format(label))

        # (p) the fold keeps no value longer than _FOLD_CAP, so a long + chain of long strings stays bounded.
        chain = " + ".join([repr("x" * _FOLD_CAP)] * 4)
        longest = max(len(value) for value in _folded_strings(ast.parse(chain)).values())
        if longest > _FOLD_CAP:
            failures.append("the string fold kept a {}-character value, over _FOLD_CAP {}"
                            .format(longest, _FOLD_CAP))

        # (q) this file passes its own scan: it spells the reserved name only in comparisons and as a third
        # positional argument.
        try:
            own = _unsupported_binding(_parse_module(Path(__file__), "tools/gen_renderers.py", "its own scan"))
        except GateError as exc:
            own = str(exc)
        if own is not None:
            failures.append("tools/gen_renderers.py expected to pass its own RENDERER_DECL scan, got {!r}"
                            .format(own))
    finally:
        RENDERERS = saved
        shutil.rmtree(tmp, ignore_errors=True)

    if failures:
        print("SELF-TEST FAIL:")
        for failure in failures:
            print("  - " + failure)
        return 1
    print("SELF-TEST PASS: a conformant renderer set generates and regenerates drift-clean and is "
          "deterministic; a helper edit inside a closure changes the framed code-digest; a mutated "
          "renderers.toml fails --check (exit 1); and a wildcard pack-local import, a missing "
          "RENDERER_DECL, a non-slug renderer-id, an unlisted generator declaring RENDERER_DECL, {} "
          "unsupported RENDERER_DECL binding or literal-name forms, 5 non-regular *.py entries, {} rebindings "
          "or bare annotations, {} in-place changes to a listed declaration, {} non-UTF-8 coding cookies and "
          "{} injected parser or literal stack overflows each fail closed (exit 2), while an annotated "
          "declaration alone renders, a UTF-8 BOM parses, {} refusal messages name where the binder runs, an "
          "unreadable file is reported as a read failure, {} exempt or non-matching uses of the name stay "
          "allowed (allowed uses, never proof of inertness), the string fold stays bounded, and this file "
          "passes its own scan".format(
              len(_UNSUPPORTED_BINDINGS), len(_REBOUND), len(_MUTATED), len(_COOKIES), len(_DEEP),
              len(_WHERE_MESSAGES), len(_ALLOWED_USES)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
