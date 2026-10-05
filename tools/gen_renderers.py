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
same RENDERERS); a tools/*.py binding RENDERER_DECL by any static form other than ONE plain top-level
assignment or annotated assignment to the bare name (_declaration, the single predicate discovery and the
reader share), a tools/*.py that declares a source encoding other than UTF-8 or cannot be parsed (any
parse error class, including a parser stack overflow on deep nesting), or a *.py entry that is not a
regular file (_declared_renderer_stems, which also discloses the dynamic-binding residual); a
missing/malformed RENDERER_DECL or GENSRC_OUTPUTS; a pack-local
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
import ast
import codecs
import hashlib
import io
import os
import stat
import sys
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


# Every exception class reading and parsing a hostile *.py can raise: OSError (unreadable), SyntaxError (incl.
# a bad coding cookie or invalid UTF-8), ValueError (incl. UnicodeDecodeError), and MemoryError or
# RecursionError (a parser or compiler stack overflow on very deep nesting). Each is a cannot-evaluate
# (GateError, exit 2), never a traceback.
_PARSE_ERRORS = (OSError, SyntaxError, ValueError, MemoryError, RecursionError)


def _parse_module(path, where, purpose):
    """Parse a tools/*.py to an AST from its raw BYTES, as Python's own import does (a BOM and a PEP 263
    coding cookie are honoured, never bypassed by a UTF-8 text read), after refusing a declared source
    encoding other than UTF-8: a UTF-7 cookie, say, can turn a line that reads as a comment in UTF-8 into a
    statement. Fail-closed (GateError) on that cookie and on every exception class in _PARSE_ERRORS."""
    try:
        data = path.read_bytes()
        encoding = tokenize.detect_encoding(io.BytesIO(data).readline)[0]
        if codecs.lookup(encoding).name not in ("utf-8", "utf-8-sig"):
            raise GateError("{}: declares the source encoding {!r}; only UTF-8 is supported, so it cannot be "
                            "scanned for {} and is refused".format(where, encoding, purpose))
        return ast.parse(data, filename=where)
    except _PARSE_ERRORS as exc:
        raise GateError("{}: cannot parse for {} ({}: {})".format(where, purpose, type(exc).__name__, exc))


def _read_renderer_decl(path, where):
    """Recover a generator's single module-level RENDERER_DECL literal by parsing to an AST and evaluating
    ONLY that literal (ast.literal_eval); the module is never imported. The binding is found by
    _declaration, the same predicate discovery uses, so the two accept exactly the same binding set.
    Fail-closed (GateError) when there is not exactly one top-level assignment or annotated assignment to
    RENDERER_DECL (or any other binding of it), when that binding has no value or a non-literal value, or
    when the shape is wrong ({'renderer-id': <slug str>, 'semantics-revision': <non-negative int>})."""
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
    set: at most ONE top-level Assign or AnnAssign to the bare name, and no other static binding of it.
    Returns a list of zero or one (target, value) pair. Fail-closed (GateError) on an unsupported binding
    form (_unsupported_binding) and on more than one top-level binding, since a later rebinding (an
    annotated one, say) would make the value Python runs differ from the value read."""
    form = _unsupported_binding(tree)
    if form is not None:
        raise GateError("{}: RENDERER_DECL is bound by {}; only a single top-level assignment or annotated "
                        "assignment to the bare name is a supported declaration".format(where, form))
    bindings = _top_level_bindings(tree)
    if len(bindings) > 1:
        raise GateError("{}: RENDERER_DECL is bound {} times at top level; exactly one top-level assignment "
                        "or annotated assignment is a supported declaration".format(where, len(bindings)))
    return bindings


def _unsupported_binding(tree):
    """The description of the first binding of RENDERER_DECL in `tree` that is NOT a top-level Assign or
    AnnAssign to the bare name, or None when there is none. Modelled on gen_gensrc's _read_declaration
    walk: a Store/Del-context Name covers tuple/list/starred unpacking, a conditional or try/with/loop-nested
    assignment, an augmented assignment, del, a walrus, a type alias, and a for/with/comprehension target;
    the string-name binders (import alias incl. a dotted root, a wildcard import, def/class, except-as,
    match capture, a parameter, a type parameter, global/nonlocal) are matched by their str field; and a
    store or delete through the module object is matched by its literal name (an attribute such as
    sys.modules[__name__].RENDERER_DECL, a subscript such as vars()["RENDERER_DECL"] or
    globals()["RENDERER_DECL"], or a setattr/delattr call naming it)."""
    allowed = {id(t) for t, _ in _top_level_bindings(tree)}
    parents = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[id(child)] = node

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
        return isinstance(node, ast.Constant) and node.value == "RENDERER_DECL"

    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            if (node.id != "RENDERER_DECL" or not isinstance(node.ctx, (ast.Store, ast.Del))
                    or id(node) in allowed):
                continue
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
        if isinstance(node, ast.alias):
            bound = node.asname if node.asname is not None else node.name.partition(".")[0]
            if bound == "RENDERER_DECL" or (node.asname is None and node.name == "*"):
                return ("a wildcard import" if node.name == "*" else "an import") + _where(node)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name == "RENDERER_DECL":
                return "a def/class" + _where(node)
        elif isinstance(node, ast.ExceptHandler):
            if node.name == "RENDERER_DECL":
                return "an except-as" + _where(node)
        elif isinstance(node, (ast.MatchAs, ast.MatchStar)):
            if node.name == "RENDERER_DECL":
                return "a match capture" + _where(node)
        elif isinstance(node, ast.MatchMapping):
            if node.rest == "RENDERER_DECL":
                return "a match capture" + _where(node)
        elif isinstance(node, ast.arg):
            if node.arg == "RENDERER_DECL":
                return "a def or lambda parameter" + _where(node)
        elif isinstance(node, (ast.TypeVar, ast.ParamSpec, ast.TypeVarTuple)):
            if node.name == "RENDERER_DECL":
                return "a type parameter" + _where(node)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            if "RENDERER_DECL" in node.names:
                return "a global/nonlocal declaration" + _where(node)
        elif isinstance(node, ast.Attribute):
            if node.attr == "RENDERER_DECL" and _stores(node):
                return ("an attribute store or delete (such as sys.modules[__name__].RENDERER_DECL)"
                        + _where(node))
        elif isinstance(node, ast.Subscript):
            if _stores(node) and _names_decl(node.slice):
                return ("a subscript store or delete keyed 'RENDERER_DECL' (such as vars() or globals())"
                        + _where(node))
        elif isinstance(node, ast.Call):
            if (isinstance(node.func, ast.Name) and node.func.id in ("setattr", "delattr")
                    and len(node.args) >= 2 and _names_decl(node.args[1])):
                return "a {} call naming RENDERER_DECL".format(node.func.id) + _where(node)
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
    encoding other than UTF-8 (_parse_module); and on any statically visible binding of RENDERER_DECL other
    than ONE top-level Assign or AnnAssign to the bare name (_declaration), each of which could hide a
    declaration. A symlink to a regular file is read through. RENDERER_DECL is a RESERVED name: the walk is
    conservative and also refuses an unrelated function-local reuse of it, which fails in the safe direction.

    DISCLOSED RESIDUAL (disclose-guard-residuals): a truly dynamic binding (one whose name is computed rather
    than the literal RENDERER_DECL, such as setattr(module, name, ...) or globals()[key] = ..., a bulk update
    such as globals().update(...), exec, or an import-time side effect of another module) is beyond static
    analysis; this function does not detect it and does not claim to."""
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
#       type alias, and a store through the module object by attribute, vars()/globals() subscript or
#       setattr) fails --check (exit 2) instead of reading as "no declaration".
#   (i) a *.py entry that is a dangling symlink, a looping symlink, a symlink to a directory, a directory,
#       or a FIFO fails --check (exit 2) instead of being skipped.
#   (j) discovery and the reader share one binding predicate: a listed generator declaring RENDERER_DECL by
#       an annotated assignment alone renders that declaration, while an ordinary declaration followed by an
#       annotated (or a second plain) rebinding, or a bare annotation, fails closed (exit 2), and the reader
#       alone also refuses two bindings.
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
)

# (j) listed tools/gen_alpha.py bodies: the shared declaration predicate (one Assign or AnnAssign, no more).
_ALPHA_DECL = 'RENDERER_DECL = {"renderer-id": "alpha", "semantics-revision": 1}\n'
_ENTRY_ANNOTATED = _ENTRY_GOOD.replace(_ALPHA_DECL, _ALPHA_DECL.replace(" = ", ": dict = ", 1))
_REBOUND = (
    ("annotated rebinding", _ENTRY_GOOD + 'RENDERER_DECL: dict = {"renderer-id": "rebound", '
                                          '"semantics-revision": 99}\n'),
    ("plain rebinding", _ENTRY_GOOD + 'RENDERER_DECL = {"renderer-id": "rebound", '
                                      '"semantics-revision": 99}\n'),
    ("bare annotation", _ENTRY_GOOD.replace(_ALPHA_DECL, "RENDERER_DECL: dict\n")),
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

    try:
        tmp = Path(tempfile.mkdtemp(prefix="aiqt-gen-renderers-selftest-"))
    except OSError as exc:
        print("SELF-TEST ERROR: no writable temporary directory: {}".format(exc), file=sys.stderr)
        return 2
    failures = []
    try:
        # (a) conformant + determinism.
        good = tmp / "good"
        _fixture(good, _ENTRY_GOOD)
        if run_quiet(good, check=False) != 0:
            failures.append("conformant: generation expected exit 0")
        if run_quiet(good, check=True) != 0:
            failures.append("conformant: regeneration expected drift-clean exit 0")
        first = (good / RENDERERS_REL).read_text(encoding="utf-8")
        run_quiet(good, check=False)
        if (good / RENDERERS_REL).read_text(encoding="utf-8") != first:
            failures.append("determinism: two runs are not byte-identical")

        # (b) a helper edit inside the closure changes the framed code-digest.
        edited = tmp / "edited"
        _fixture(edited, _ENTRY_GOOD, helper_body="VALUE = 2\n")
        run_quiet(edited, check=False)
        if (edited / RENDERERS_REL).read_text(encoding="utf-8") == first:
            failures.append("closure completeness: a helper edit did not change the code-digest")

        # (c) a mutated renderers.toml is caught by --check.
        if run_quiet(good, check=False) == 0:
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
            if run_quiet(case, check=True) != 2:
                failures.append("an unlisted tools/gen_beta.py binding RENDERER_DECL by {} expected --check "
                                "exit 2 (unsupported binding, fail-closed)".format(label))

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
        try:
            _read_renderer_decl(tmp / "decl-annotated-rebinding" / "tools" / "gen_alpha.py", "gen_alpha.py")
            failures.append("the reader alone expected GateError on an annotated rebinding of RENDERER_DECL")
        except GateError:
            pass

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
          "unsupported RENDERER_DECL binding forms, 5 non-regular *.py entries, {} rebindings or bare "
          "annotations, {} non-UTF-8 coding cookies and {} injected parser or literal stack overflows each fail "
          "closed (exit 2), while an annotated declaration alone renders, a UTF-8 BOM parses, and {} "
          "refusal messages name where the binder runs".format(
              len(_UNSUPPORTED_BINDINGS), len(_REBOUND), len(_COOKIES), len(_DEEP), len(_WHERE_MESSAGES)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
