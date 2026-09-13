#!/usr/bin/env python3
"""Advisory scan for unsafe classification of control and state paths (a deterministic code-shape gate).

Code that decides what to do about a control or state path (a lock, marker, journal, or state directory)
by a FOLLOWING existence or type check on the string name, then proceeds as if a negative or wrong-type
result simply meant nothing to do, both follows a symbolic link an attacker can swap in and collapses a
wrong-type or unreadable entry into benign absence. The corpus rule secspr (security-seci-symlink-resolution,
its control-path-classification clause) requires the classification to be a no-follow inspection bound to a
trusted directory descriptor, with a three-way result: genuine absence, presence of exactly the expected
type confirmed on the opened object, and everything else refused as an error rather than collapsed into
absence; and its sibling fail-closed and no-silent-empty rules (secfcl, chkfcl) require a wrong-type or
unreadable entry to surface, never read as clean.

This gate is an ADVISORY (WARN-only) v1 best-effort heuristic, not a decision procedure and not a
specification of its own behaviour. It scans a recognizable subset of the repo's own Python (AST plus a
local, per-scope dataflow pass; a regex never convicts) and EMITS WARN advisories, at a certain-shape or a
resist-static tier, for the following-classify-then-mutate and conflated-except scopes it recognizes,
flagging them for human review against secspr and chkfcl/secfcl. It NEVER blocks CI. Recognition is
syntactic and per-scope, and it applies internal suppression and downgrade heuristics: for example it can
recognize an explicit raise on a negative branch, and no-follow evidence, which can suppress or
downgrade a finding. It guarantees neither a WARN for every risky case nor silence for any particular
shape: a shape it does not model may emit or may go unflagged, so it can both miss a genuine collapse and,
on dataflow it does not follow (for example an `import ... as <name>` rebind, an unproven receiver
provenance, a classify that is only part of a larger BoolOp, a cross-function or cross-module flow, dynamic
dispatch, or getattr indirection), emit a false-positive advisory; both are harmless under WARN-only. The
DETECTOR source below and its --self-test are the authoritative account of exactly what it flags; this
docstring does not restate that account, and sharper precision that could later support a non-advisory
classification is a disclosed follow-on, deferred because a sound static-dataflow conviction over arbitrary
Python binding and control-flow is not yet achieved.

CANNOT-EVALUATE (exit 2, fail-closed per the check-fails-closed-on-unreadable rule): any declared scanned
input missing, unreadable, non-regular, non-UTF-8, or unparseable (a SyntaxError on a declared file is a
named refusal, never a skip); a declared input whose AST exceeds the analyzer's recursion or memory
capacity; or a non-isolated run (the bootstrap self-guard).

BOOTSTRAP SELF-GUARD. The gate's first executable statements import only sys and refuse to run (exit 2)
unless the interpreter is isolated, so a sibling planted beside this gate cannot shadow a stdlib import and
neuter the gate before it can check anything.

SCANNED SURFACES (the declared set, resolved from the repo root): every regular *.py directly under tools/
(the vendored tools/_vendor/ subtree is excluded), and every regular *.py directly under
.aiqt/core/hooks/scripts/. Both directories are REQUIRED: an unreadable or absent one is a cannot-evaluate.

The classifier table is Python-first; further languages are additive rows plus a parser, a named follow-on,
not covered here. The secspr and chkfcl/secfcl rules carry the full obligation.

  check_path_classification.py             scan the declared surfaces
  check_path_classification.py --self-test build synthetic trees and assert the gate's invariants

Run this gate isolated: python3 -I -B tools/check_path_classification.py
"""
import sys


def _interpreter_isolated(flags):
    """True iff a sys.flags-like object reports isolated mode: -I, or the full -P -E -s equivalent."""
    return bool(flags.isolated) or bool(
        getattr(flags, "safe_path", 0) and flags.ignore_environment and flags.no_user_site)


if not _interpreter_isolated(sys.flags):
    sys.stderr.write("check_path_classification: refusing to run non-isolated; launch it as "
                     "`python3 -I -B tools/check_path_classification.py` (a sibling file could otherwise "
                     "shadow a stdlib import and neuter this gate)\n")
    raise SystemExit(2)

import ast  # noqa: E402  imported only after the isolation guard above
import os  # noqa: E402
import stat  # noqa: E402
from pathlib import Path  # noqa: E402

TOOLS_REL = "tools"
HOOKS_SCRIPTS_REL = ".aiqt/core/hooks/scripts"

# --- classifier / suppressor / state-change DATA TABLES (Python-first; other languages are additive) ----
OSPATH_FOLLOW = frozenset({"exists", "isdir", "isfile"})       # os.path.<name>(P), following
OS_STAT_FOLLOW = frozenset({"stat"})                           # os.stat(P) without follow_symlinks=False
METHOD_FOLLOW = frozenset({"exists", "is_dir", "is_file", "stat"})  # p.<name>() following
NOFOLLOW_METHODS = frozenset({"lstat", "is_symlink"})          # never a following-classify
OSPATH_NOFOLLOW = frozenset({"lexists"})                       # os.path.lexists: no-follow
OS_NOFOLLOW = frozenset({"lstat"})                             # os.lstat: no-follow
NAME_HINTS = ("lock", "state", "journal", "marker", "pid")
CONFLATED_NONABSENCE = frozenset({"PermissionError", "NotADirectoryError", "IsADirectoryError", "OSError"})
OS_STATE_CHANGE = frozenset({"mkdir", "makedirs", "remove", "unlink", "rmdir", "rename", "replace",
                             "symlink", "link"})
SHUTIL_STATE_CHANGE = frozenset({"move", "copy", "copy2", "copyfile", "copytree", "rmtree"})
METHOD_STATE_CHANGE = frozenset({"mkdir", "unlink", "rename", "replace", "touch", "write_text",
                                 "write_bytes", "rmdir"})
WRITE_MODES = ("w", "a", "x", "+")
# pathlib provenance: a receiver is treated as a filesystem path when it traces to a pathlib constructor,
# a path-returning method/attribute on one, a `/` join involving one, or a Path-typed annotation. This is
# one best-effort heuristic input the detector weighs; the tier a scope is reported at (certain-shape vs
# resist-static), receiver recognition, and control-flow handling are all best-effort and defined by the
# code below and its --self-test, not promised here. An unmodelled or ambiguous case may still be flagged,
# at either tier; the detector over-fires rather than under-fires, harmless under WARN-only.
PATH_CTORS = frozenset({"Path", "PurePath", "PosixPath", "WindowsPath", "PurePosixPath", "PureWindowsPath"})
PATH_RETURNING_METHODS = frozenset({"joinpath", "resolve", "absolute", "expanduser", "with_name",
                                    "with_suffix", "with_stem", "relative_to", "readlink"})
PATH_RETURNING_ATTRS = frozenset({"parent"})


class _Resolver:
    """Resolve, within one module, the os / os.path / shutil module aliases and the pathlib names honoured
    by the tables above. Unresolved aliases are simply not recognized (a disclosed residual)."""

    def __init__(self, tree):
        self.os_aliases = {"os"}
        self.ospath_aliases = {"os.path"}   # tracked as a marker; real detection is Attribute(Attribute)
        self.shutil_aliases = {"shutil"}
        self.bare_ospath = {}   # name -> attr for `from os.path import exists`
        self.bare_os = {}       # name -> attr for `from os import stat, mkdir`
        self.pathlib_aliases = set()   # `import pathlib [as pl]`
        self.path_ctor_names = {}      # name -> ctor for `from pathlib import Path [as P]`
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    if a.name == "os":
                        self.os_aliases.add(a.asname or "os")
                    elif a.name == "shutil":
                        self.shutil_aliases.add(a.asname or "shutil")
                    elif a.name == "pathlib":
                        self.pathlib_aliases.add(a.asname or "pathlib")
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                if node.module == "os.path":
                    for a in node.names:
                        self.bare_ospath[a.asname or a.name] = a.name
                elif node.module == "os":
                    for a in node.names:
                        self.bare_os[a.asname or a.name] = a.name
                elif node.module == "pathlib":
                    for a in node.names:
                        if a.name in PATH_CTORS:
                            self.path_ctor_names[a.asname or a.name] = a.name

    def scoped(self, shadowed):
        """A per-scope view of this resolver with the os / os.path / shutil / pathlib module and
        constructor names that a local binding SHADOWS removed, so `def ensure(os): os.makedirs(...)`
        (a parameter shadowing the module) is not read as the real os module, and `def refresh(Path):
        Path("x")` (a parameter shadowing the constructor) is not read as the pathlib constructor. This
        shadow-stripping is a best-effort heuristic over the module/constructor aliases this resolver
        tracks; it does not model every name a scope could rebind (a shadowing builtin such as `open`, for
        instance, is not tracked here), so an unmodelled or ambiguous case may still be flagged, at either
        tier. Receiver recognition, the emitted tier, and control-flow handling are best-effort and defined
        by the code below and its --self-test, not promised here; the detector over-fires rather than
        under-fires, harmless under WARN-only. The `os.path` marker is left intact: os.path.<name>
        resolution keys off the (now shadow-stripped) os_aliases plus the literal `path` attribute, so
        dropping `os` already disables it."""
        if not shadowed:
            return self
        r = _Resolver.__new__(_Resolver)
        r.os_aliases = self.os_aliases - shadowed
        r.ospath_aliases = self.ospath_aliases
        r.shutil_aliases = self.shutil_aliases - shadowed
        r.bare_ospath = {k: v for k, v in self.bare_ospath.items() if k not in shadowed}
        r.bare_os = {k: v for k, v in self.bare_os.items() if k not in shadowed}
        r.pathlib_aliases = self.pathlib_aliases - shadowed
        r.path_ctor_names = {k: v for k, v in self.path_ctor_names.items() if k not in shadowed}
        return r


def _has_kw(call, name):
    return any(kw.arg == name for kw in call.keywords)


def _kw_is_false(call, name):
    for kw in call.keywords:
        if kw.arg == name and isinstance(kw.value, ast.Constant) and kw.value.value is False:
            return True
    return False


def _is_path_ctor_call(call, resolver):
    """True iff `call` resolves, against the resolver it is given, to a pathlib constructor: a bare
    `Path(...)` bound from pathlib, or `pathlib.Path(...)` (any alias). Recognition is best-effort and only
    as sound as the resolver passed in (a shadowed name is stripped only where the caller scoped it); the
    detector over-fires rather than under-fires, harmless under WARN-only."""
    f = call.func
    if isinstance(f, ast.Name):
        return f.id in resolver.path_ctor_names
    if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name):
        return f.value.id in resolver.pathlib_aliases and f.attr in PATH_CTORS
    return False


def _is_proven_path(node, path_names, resolver):
    """True iff `node` is recognized as a filesystem path object: a pathlib constructor call, a
    path-returning method or attribute on a recognized path, a `/` join involving one, or a name/param
    Path-typed. This is a best-effort provenance heuristic, not a decision procedure: an unresolved or
    unmodelled receiver may still be flagged, at either tier. The emitted tier and receiver recognition
    are best-effort and defined by the code below and its --self-test, not promised here; the detector
    over-fires rather than under-fires, harmless under WARN-only."""
    if isinstance(node, ast.Name):
        return node.id in path_names
    if isinstance(node, ast.Call):
        if _is_path_ctor_call(node, resolver):
            return True
        f = node.func
        if isinstance(f, ast.Attribute) and f.attr in PATH_RETURNING_METHODS:
            return _is_proven_path(f.value, path_names, resolver)
        return False
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        return (_is_proven_path(node.left, path_names, resolver)
                or _is_proven_path(node.right, path_names, resolver))
    if isinstance(node, ast.Attribute):
        return node.attr in PATH_RETURNING_ATTRS and _is_proven_path(node.value, path_names, resolver)
    if isinstance(node, ast.Subscript):
        v = node.value   # p.parents[0]
        return (isinstance(v, ast.Attribute) and v.attr == "parents"
                and _is_proven_path(v.value, path_names, resolver))
    return False


def _is_path_annotation(ann, resolver):
    """Best-effort: MATCHES an annotation that spells a pathlib type: a resolver-tracked `Path`,
    `pathlib.Path` (any alias), or a forward-ref string whose tail token is a Path ctor name. The forward-ref
    string branch matches by token spelling and does not establish the name resolves to pathlib at runtime.
    Exact behavior is the code plus --self-test."""
    if isinstance(ann, ast.Name):
        return ann.id in resolver.path_ctor_names
    if isinstance(ann, ast.Attribute) and isinstance(ann.value, ast.Name):
        return ann.value.id in resolver.pathlib_aliases and ann.attr in PATH_CTORS
    if isinstance(ann, ast.Constant) and isinstance(ann.value, str):
        tail = ann.value.rsplit(".", 1)[-1]
        return tail in PATH_CTORS
    return False


def _assign_target_names(target):
    """The Name ids bound by an assignment target (a Name, or a Tuple/List of Names, possibly starred)."""
    out = []
    if isinstance(target, ast.Name):
        out.append(target.id)
    elif isinstance(target, (ast.Tuple, ast.List)):
        for elt in target.elts:
            if isinstance(elt, ast.Starred):
                elt = elt.value
            if isinstance(elt, ast.Name):
                out.append(elt.id)
    return out


def _iter_arg_annotations(func):
    """Yield (arg-name, annotation) for every annotated parameter of a function scope (none for a module)."""
    args = getattr(func, "args", None)
    if args is None:
        return
    groups = list(getattr(args, "posonlyargs", [])) + list(args.args) + list(args.kwonlyargs)
    for a in (args.vararg, args.kwarg):
        if a is not None:
            groups.append(a)
    for a in groups:
        if a.annotation is not None:
            yield a.arg, a.annotation


def _proven_path_names(func, resolver):
    """The set of local names in `func`'s scope proven to hold a pathlib path: seeded from Path-typed
    parameter and variable annotations, grown to a fixpoint over assignments whose value is a proven path,
    then SHRUNK to a fixpoint that DISQUALIFIES any name augmented-assigned or with any binding to a non-path
    value, PROPAGATING the disqualification through dependent aliases (a name proven only via `q = p` is
    dropped once `p` is dropped), so a name reused across a path and a non-path type, or an alias chain with
    any non-Path link the scan models, is dropped rather than treated as a certain filesystem receiver (an
    unmodelled `import ... as <name>` rebind the scan does not follow can still leave stale provenance)."""
    body = func.body if hasattr(func, "body") else []
    seeded = set()
    for arg_name, ann in _iter_arg_annotations(func):
        if _is_path_annotation(ann, resolver):
            seeded.add(arg_name)
    for n in _walk_no_nested(body):
        if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name) and n.annotation is not None:
            if _is_path_annotation(n.annotation, resolver):
                seeded.add(n.target.id)
    names = set(seeded)

    def _binding(n):
        """(target-names, value) for a value-producing assignment or walrus, else (None, None). A walrus
        (`(p := expr)`) is value-based exactly like an assignment: it KEEPS provenance when its value is a
        proven path and invalidates it otherwise (codex #4: `(p := cache)` drops p to unproven)."""
        if isinstance(n, ast.Assign):
            tn = []
            for t in n.targets:
                tn.extend(_assign_target_names(t))
            return tn, n.value
        if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name) and n.value is not None:
            return [n.target.id], n.value
        if isinstance(n, ast.NamedExpr):
            return _assign_target_names(n.target), n.value
        return None, None

    # Collect every value bound to each name across the scope, plus the names invalidated by a rebind whose
    # value the scan does not model as a path, so provenance can be recomputed to a fixpoint rather than
    # read once. CATCH-ALL 2 (all-binding invalidation): a name rebound by one of the binding constructs
    # this scan tracks that does not carry a proven-path value loses filesystem provenance and propagates
    # that loss through dependent aliases; a rebind this scan does NOT model, such as an `import ... as
    # <name>` alias binding a proven-path name, is not invalidated and may leave stale provenance (a
    # possible false-positive advisory, advisory precision a disclosed follow-on).
    # `augmented` (an aug-assign is a derivation) and `rebound` (a for/with/except/comprehension
    # target or a nested def/class, each binding a value this scan cannot prove is a path) both force
    # disqualification in the shrink below; a walrus is handled value-based through `bindings`, not here.
    bindings = {}
    augmented = set()
    rebound = set()
    for n in _walk_no_nested(body):
        if isinstance(n, ast.AugAssign):
            augmented.update(_assign_target_names(n.target))
            continue
        if isinstance(n, (ast.For, ast.AsyncFor)):
            rebound.update(_assign_target_names(n.target))     # a loop target: element type unproven
        elif isinstance(n, (ast.With, ast.AsyncWith)):
            for item in n.items:
                if item.optional_vars is not None:
                    rebound.update(_assign_target_names(item.optional_vars))  # the __enter__ result
        elif isinstance(n, ast.ExceptHandler):
            if n.name:
                rebound.add(n.name)                            # the caught exception object
        elif isinstance(n, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            for gen in n.generators:
                rebound.update(_assign_target_names(gen.target))
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            rebound.add(n.name)                                # a nested def/class rebinds the name
        tn, val = _binding(n)
        if val is not None:
            for nm in tn:
                bindings.setdefault(nm, []).append(val)

    # Grow a candidate proven set to a fixpoint: a name becomes a candidate when a value bound to it is a
    # proven path under the candidate set, so an alias chain (`q = p` after `p = Path(...)`) reaches proven.
    changed = True
    while changed:
        changed = False
        for nm, vals in bindings.items():
            if nm in names:
                continue
            if any(_is_proven_path(v, names, resolver) for v in vals):
                names.add(nm)
                changed = True

    # Shrink to a fixpoint so an INVALIDATED link propagates through every dependent alias. A name is
    # dropped when it is augmented, or ANY value bound to it is not a proven path under the current
    # (already shrinking) set, or it is not annotation-seeded and has no binding at all. Recomputing until
    # stable means `q = p; q = p` is dropped once `p` is dropped for a later non-path rebind (codex #3): an
    # alias chain with a non-Path link this scan models drops the dependent name from the recognized set.
    # This provenance shrink is best-effort, not a tier guarantee: a rebind this scan does not model (an
    # `import ... as <name>` rebind, or other unmodelled flow) can leave stale provenance, so an unmodelled
    # or ambiguous case may still be flagged, at either tier. The emitted tier is defined by the code and
    # its --self-test, not promised here; the detector over-fires rather than under-fires, harmless under
    # WARN-only.
    changed = True
    while changed:
        changed = False
        for nm in list(names):
            vals = bindings.get(nm, [])
            if nm in augmented or nm in rebound:
                names.discard(nm)
                changed = True
                continue
            if nm in seeded:
                if any(not _is_proven_path(v, names, resolver) for v in vals):
                    names.discard(nm)
                    changed = True
            elif not vals or any(not _is_proven_path(v, names, resolver) for v in vals):
                names.discard(nm)
                changed = True
    return names


def _open_write_mode(call, mode_index):
    """Best-effort: MATCHES a LITERAL write/append/create mode string on an open call: a `mode=` string
    keyword constant, or a positional mode string constant at `mode_index` (1 for the builtin
    open(path, mode), 0 for Path.open(mode)). A non-constant mode expression (a variable, a formatted
    string) is not matched, so this does not establish the runtime open mode. Exact behavior is the code
    plus --self-test."""
    for kw in call.keywords:
        if kw.arg == "mode" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
            return any(m in kw.value.value for m in WRITE_MODES)
    if len(call.args) > mode_index:
        a = call.args[mode_index]
        if isinstance(a, ast.Constant) and isinstance(a.value, str):
            return any(m in a.value for m in WRITE_MODES)
    return False


def _classify(call, resolver):
    """If `call` is a FOLLOWING-CLASSIFY, return its classified path expr; else None. No-follow forms
    (lexists, lstat, is_symlink, follow_symlinks=False, dir_fd=) are excluded."""
    if _kw_is_false(call, "follow_symlinks") or _has_kw(call, "dir_fd"):
        return None
    f = call.func
    # os.path.<name>(P)  -> Attribute(Attribute(Name os, 'path'), name)
    if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Attribute):
        inner = f.value
        if (isinstance(inner.value, ast.Name) and inner.value.id in resolver.os_aliases
                and inner.attr == "path"):
            if f.attr in OSPATH_FOLLOW:
                return call.args[0] if call.args else None
            if f.attr in OSPATH_NOFOLLOW:
                return None
    # os.<name>(P)  -> Attribute(Name os, name)
    if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) and f.value.id in resolver.os_aliases:
        if f.attr in OS_STAT_FOLLOW:
            return call.args[0] if call.args else None
        if f.attr in OS_NOFOLLOW:
            return None
    # from-import bare names
    if isinstance(f, ast.Name):
        if f.id in resolver.bare_ospath:
            attr = resolver.bare_ospath[f.id]
            if attr in OSPATH_FOLLOW:
                return call.args[0] if call.args else None
            return None
        if f.id in resolver.bare_os:
            attr = resolver.bare_os[f.id]
            if attr in OS_STAT_FOLLOW:
                return call.args[0] if call.args else None
            return None
    # method form: <recv>.<name>()  (heuristic Path receiver)
    if isinstance(f, ast.Attribute):
        if f.attr in NOFOLLOW_METHODS:
            return None
        if f.attr in METHOD_FOLLOW:
            return f.value      # the receiver is the classified path expression
    return None


def _classify_is_certain_fs(call, resolver, path_names):
    """True iff a following-classify `call` is a CERTAIN filesystem operation: an os.path.<exists/isdir/
    isfile>, os.stat, or from-os bare stat / from-os.path bare classify (module-resolved, inherently
    filesystem), or a method-form classify (.exists()/.is_dir()/.is_file()/.stat()) whose receiver is a
    PROVEN pathlib path. A method-form classify on an unproven receiver grades NOT certain here: the
    receiver may be a non-filesystem object with a same-named method (codex #4). This grade is consulted
    ONLY on the conflated-except certain-shape path in _analyze_try (its sole caller); it does NOT gate the
    if-branch same-path-mutation path in _analyze_function, which grades the mutation target rather than the
    classified receiver, so on that path an unproven method-form receiver CAN still anchor a certain-shape
    advisory (a disclosed tier-imprecision follow-on, harmless under WARN-only v1). Called only where
    _classify already matched, so it needs only to grade the receiver's provenance."""
    f = call.func
    if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Attribute):
        inner = f.value
        if (isinstance(inner.value, ast.Name) and inner.value.id in resolver.os_aliases
                and inner.attr == "path" and f.attr in OSPATH_FOLLOW):
            return True
    if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) and f.value.id in resolver.os_aliases:
        if f.attr in OS_STAT_FOLLOW:
            return True
    if isinstance(f, ast.Name):
        if f.id in resolver.bare_ospath and resolver.bare_ospath[f.id] in OSPATH_FOLLOW:
            return True
        if f.id in resolver.bare_os and resolver.bare_os[f.id] in OS_STAT_FOLLOW:
            return True
    if isinstance(f, ast.Attribute) and f.attr in METHOD_FOLLOW and f.attr not in NOFOLLOW_METHODS:
        return _is_proven_path(f.value, path_names, resolver)
    return False


def _unparse(node):
    try:
        return ast.unparse(node)
    except (RecursionError, MemoryError):
        # A capacity failure (a node too deep for ast.unparse) is a cannot-evaluate, not an unmatched
        # node: re-raise it BEFORE the broad except below so it propagates to scan()'s capacity handler
        # and the run fails closed (exit 2, a located capacity diagnostic) rather than silently
        # downgrading to a rc-0 advisory. Per the check-fails-closed-on-unreadable / capacity contract.
        raise
    except Exception:  # noqa: BLE001  an unparseable node is simply not matched (disclosed residual)
        return None


def _has_name_hint(node):
    for sub in ast.walk(node):
        if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
            low = sub.value.lower()
            if any(h in low for h in NAME_HINTS):
                return True
        if isinstance(sub, ast.Name) and any(h in sub.id.lower() for h in NAME_HINTS):
            return True
        if isinstance(sub, ast.Attribute) and any(h in sub.attr.lower() for h in NAME_HINTS):
            return True
    return False


def _walk_no_nested(nodes):
    """Walk statements/expressions without descending into nested function or lambda bodies. A nested
    def/lambda is yielded (visible as a statement) but never entered, so a statement inside a nested
    function is attributed to that function's own scope, not the enclosing one."""
    stack = list(nodes)
    while stack:
        n = stack.pop()
        yield n
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        for c in ast.iter_child_nodes(n):
            stack.append(c)


def _scope_bound_names(scope):
    """The set of names bound LOCALLY in one scope by a parameter or an in-scope binding (assignment,
    annotation, aug-assign, walrus, for/with/except/comprehension target, or a nested def/class), NOT
    descending into a nested def and NOT counting a plain `import` (import bindings are OMITTED by this
    heuristic; counting a module-level import here would wrongly disable module-scope detection). Because
    import bindings are omitted, an in-scope `import ... as <name>` rebind can leave stale API recognition
    while runtime calls the rebound name (consistent with the module-level disclosure), a best-effort limit
    of this scan. Used by CATCH-ALL 1 to match a local name that shadows an os/os.path/shutil/pathlib API
    name."""
    names = set()
    args = getattr(scope, "args", None)
    if args is not None:
        for a in list(getattr(args, "posonlyargs", [])) + list(args.args) + list(args.kwonlyargs):
            names.add(a.arg)
        for a in (args.vararg, args.kwarg):
            if a is not None:
                names.add(a.arg)
    body = scope.body if hasattr(scope, "body") else []
    for n in _walk_no_nested(body):
        if isinstance(n, ast.Assign):
            for t in n.targets:
                names.update(_assign_target_names(t))
        elif isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name):
            names.add(n.target.id)
        elif isinstance(n, ast.AugAssign):
            names.update(_assign_target_names(n.target))
        elif isinstance(n, ast.NamedExpr):
            names.update(_assign_target_names(n.target))
        elif isinstance(n, (ast.For, ast.AsyncFor)):
            names.update(_assign_target_names(n.target))
        elif isinstance(n, (ast.With, ast.AsyncWith)):
            for item in n.items:
                if item.optional_vars is not None:
                    names.update(_assign_target_names(item.optional_vars))
        elif isinstance(n, ast.ExceptHandler):
            if n.name:
                names.add(n.name)
        elif isinstance(n, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            for gen in n.generators:
                names.update(_assign_target_names(gen.target))
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(n.name)
    return names


def _state_change_targets(nodes, resolver, path_names):
    """The set of unparsed target-path expressions of PROVEN filesystem state-changing calls within `nodes`
    (no nested defs). A module call (os.<state>, shutil.<state>, from-os bare) is read as a filesystem
    mutation; a method-form mutation (p.mkdir(), p.open('w'), ...) is counted mainly where its receiver is a
    recognized pathlib path, so a str.replace() or a same-named non-path object is usually not read as a
    filesystem change. Recognition here is a best-effort heuristic, not a guarantee (the builtin `open`
    branch, for instance, is credited by spelling and does not model a shadowing local); an unmodelled or
    ambiguous case may still be flagged, at either tier, and the exact behavior is defined by the code and
    its --self-test, not promised here. The module-call branch is matched first and narrowly, so a
    Path-method call on a bare-Name receiver is no longer swallowed before it can reach the method
    classification (codex #4 / claude B-3)."""
    targets = set()
    for n in _walk_no_nested(nodes):
        if not isinstance(n, ast.Call):
            continue
        f = n.func
        tgt = None
        if (isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name)
                and f.value.id in resolver.os_aliases and f.attr in OS_STATE_CHANGE):
            tgt = n.args[0] if n.args else None
        elif (isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name)
                and f.value.id in resolver.shutil_aliases and f.attr in SHUTIL_STATE_CHANGE):
            tgt = n.args[-1] if n.args else None  # dst is the mutated target for move/copy
        elif isinstance(f, ast.Name) and f.id in resolver.bare_os and resolver.bare_os[f.id] in OS_STATE_CHANGE:
            tgt = n.args[0] if n.args else None
        elif (isinstance(f, ast.Attribute) and f.attr == "open"
                and _is_proven_path(f.value, path_names, resolver) and _open_write_mode(n, 0)):
            tgt = f.value  # Path(...).open('w') on a proven path
        elif (isinstance(f, ast.Attribute) and f.attr in METHOD_STATE_CHANGE
                and _is_proven_path(f.value, path_names, resolver)):
            tgt = f.value  # p.mkdir()/p.unlink()/p.replace()/... on a proven path
        elif isinstance(f, ast.Name) and f.id == "open" and _open_write_mode(n, 1):
            tgt = n.args[0] if n.args else None  # builtin open(path, 'w')
        if tgt is not None:
            u = _unparse(tgt)
            if u is not None:
                targets.add(u)
    return targets


def _walk_stop_at_try(nodes):
    """Walk `nodes` without descending into a nested function/lambda OR a nested `try`/`try*` statement. As
    a best-effort control-flow heuristic, an operation inside a nested try (plain or `except*`) may have its
    exceptions caught or transformed by that try's own handlers before they could reach an OUTER handler, so
    it is not credited to the outer try (codex #5: an inner `except PermissionError: raise RuntimeError` can
    keep the outer conflated handler from receiving that PermissionError; the same holds for an inner
    `except*`). ast.TryStar exists on Python 3.11+, so it is included via getattr for older interpreters.
    This does not model every exception-flow shape; an op directly in the try body, or nested only in an
    `if`/`with`/`for` that does not catch, still reaches this try's handlers and is walked."""
    stack = list(nodes)
    while stack:
        n = stack.pop()
        yield n
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.Try,
                          getattr(ast, "TryStar", ()))):
            continue
        for c in ast.iter_child_nodes(n):
            stack.append(c)


def _branch_refuses(nodes):
    """Best-effort: MATCHES a raise present at the branch's own statement level (not inside a nested def),
    a syntactic raise-PRESENCE heuristic. It does NOT establish that the branch refuses at runtime: a
    caught raise (`try: raise ...; except: pass`), a conditional raise, or an unreachable raise can match
    here without the branch actually refusing. Exact behavior is the code plus --self-test."""
    for n in nodes:
        for sub in _walk_no_nested([n]):
            if isinstance(sub, ast.Raise):
                return True
    return False


def _function_has_suppressor(func, resolver):
    """Best-effort scan for RECOGNIZED no-follow spellings in the enclosing function: an attribute-form
    lstat/lexists/is_symlink call, a bare os.lstat, a follow_symlinks=False or dir_fd= keyword, or an
    O_NOFOLLOW reference. It MATCHES these forms syntactically and can MISS a form it does not resolve, for
    example a directly imported `from os.path import lexists; lexists(p)` called as a bare Name. It does not
    establish that the function is symlink-safe. Exact behavior is the code plus --self-test."""
    for n in _walk_no_nested(func.body if hasattr(func, "body") else []):
        if isinstance(n, ast.Call):
            if _kw_is_false(n, "follow_symlinks") or _has_kw(n, "dir_fd"):
                return True
            f = n.func
            if isinstance(f, ast.Attribute) and f.attr in (NOFOLLOW_METHODS | OS_NOFOLLOW | OSPATH_NOFOLLOW):
                return True
            if isinstance(f, ast.Name) and (f.id in resolver.bare_os and resolver.bare_os[f.id] in OS_NOFOLLOW):
                return True
        if isinstance(n, ast.Name) and n.id == "O_NOFOLLOW":
            return True
        if isinstance(n, ast.Attribute) and n.attr == "O_NOFOLLOW":
            return True
    return False


def _iter_if_tests(nodes):
    """Yield (if_node) for every If in `nodes` at any depth, not descending nested defs."""
    for n in _walk_no_nested(nodes):
        if isinstance(n, ast.If):
            yield n


def _test_classify(test, resolver):
    """If an `if` test is a following-classify (optionally negated), return (path_expr, absent_is_body).
    absent_is_body is True when the BODY runs on absent/negative (i.e. `if not classify(P)`), False when the
    ELSE runs on absent (i.e. `if classify(P)`)."""
    if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not) and isinstance(test.operand, ast.Call):
        p = _classify(test.operand, resolver)
        if p is not None:
            return p, True
    if isinstance(test, ast.Call):
        p = _classify(test, resolver)
        if p is not None:
            return p, False
    return None


def _analyze_function(func, resolver, rel, deny, warn, path_names):
    """Analyze one function (or module pseudo-scope) for the if-based classify shapes."""
    suppressed = _function_has_suppressor(func, resolver)
    body = func.body if hasattr(func, "body") else []
    for node in _iter_if_tests(body):
        tc = _test_classify(node.test, resolver)
        if tc is None:
            continue
        path_expr, absent_is_body = tc
        absent_branch = node.body if absent_is_body else node.orelse
        path_u = _unparse(path_expr)
        state_targets = _state_change_targets(absent_branch, resolver, path_names)
        same_path_mutation = path_u is not None and path_u in state_targets
        relevant = _has_name_hint(path_expr) or bool(state_targets)
        if not relevant:
            continue
        if _branch_refuses(absent_branch):
            continue  # raise-PRESENCE suppression (see _branch_refuses); a caught or unreachable raise can suppress a branch that does not actually refuse
        where = (rel, node.lineno)
        if same_path_mutation and not suppressed:
            deny.append(where + ("a following classify of {!r} whose absent/wrong-type branch mutates the "
                                 "same path with no no-follow evidence; classify no-follow, three-way"
                                 .format(path_u),))
        else:
            warn.append(where + ("a following classify of a control-relevant path with a benign-negative "
                                 "branch (name-hint or state-change relevance); confirm no-follow, "
                                 "descriptor-bound, three-way classification (secspr)",))


def _analyze_try(func, resolver, rel, deny, warn, path_names):
    """Analyze try/except conflation within a function/module scope. Whether a conflated-except scope is
    reported, and at which tier, is decided by the code below and _classify_is_certain_fs, best-effort: it
    weighs whether the guarded operation looks like a filesystem classify/open (an os/os.path module
    classify, a builtin `open`, or a method-form classify on a recognized pathlib receiver), but the
    recognition is syntactic and can be fooled (a scope that shadows `open` with a local binding, for
    instance, is still credited by the spelling-only check), so an unmodelled or ambiguous case may still
    be flagged, at either tier. The exact behavior is defined by the code and the --self-test, not promised
    here; the detector over-fires rather than under-fires, harmless under WARN-only."""
    body = func.body if hasattr(func, "body") else []
    for node in _walk_no_nested(body):
        if not isinstance(node, ast.Try):
            continue
        # A control-relevant, CERTAIN-filesystem classify or open inside the try body whose exceptions may
        # reach THIS try's handlers (a best-effort heuristic: _walk_stop_at_try does not descend into a
        # nested try/try* whose own handlers could intercept first; catch-all 3)?
        guards_relevant = False
        for sub in _walk_stop_at_try(node.body):
            if isinstance(sub, ast.Call):
                p = _classify(sub, resolver)
                if (p is not None and _has_name_hint(p)
                        and _classify_is_certain_fs(sub, resolver, path_names)):
                    guards_relevant = True
                    break
                f = sub.func
                if isinstance(f, ast.Name) and f.id == "open" and sub.args and _has_name_hint(sub.args[0]):
                    guards_relevant = True
                    break
        if not guards_relevant:
            continue
        for handler in node.handlers:
            names = _handler_exc_names(handler.type)
            benign = _handler_is_benign(handler.body)
            if not benign:
                continue
            conflated_tuple = ("FileNotFoundError" in names) and bool(names & CONFLATED_NONABSENCE)
            bare_broad = names == {"OSError"}
            if conflated_tuple:
                deny.append((rel, handler.lineno, "a try guarding a control-relevant classify/open whose "
                             "except conflates FileNotFoundError with a non-absence error under a benign "
                             "default; refuse the non-absence error, do not collapse it into absence"))
            elif bare_broad:
                warn.append((rel, handler.lineno, "a bare `except OSError` with a benign default guards a "
                             "control-relevant classify/open; a permission or wrong-type error may be "
                             "collapsed into absence (confirm it fails closed, chkfcl/secfcl)"))


def _handler_exc_names(exc_type):
    """The set of exception class NAMES an except clause catches (a bare Name, or a Tuple of Names)."""
    out = set()
    if isinstance(exc_type, ast.Name):
        out.add(exc_type.id)
    elif isinstance(exc_type, ast.Tuple):
        for elt in exc_type.elts:
            if isinstance(elt, ast.Name):
                out.add(elt.id)
    return out


def _is_benign_absence_value(value):
    """Best-effort: MATCHES a returned value that spells a 'nothing here' sentinel: a bare return, None, an
    empty string, an empty list/dict/tuple/set literal, or an empty set/list/dict/tuple constructor call.
    A numeric or other constant (e.g. `return 2`) or a boolean is not matched, so it does not license a
    certain-shape advisory. Recognition is by SPELLING: an empty-constructor match credits the name
    (list/set/dict/tuple) syntactically, so a shadowed constructor (list/set/dict/tuple rebound as a local
    or parameter that returns a failure code) can defeat it. It does not establish genuine runtime absence.
    Exact behavior is the code plus --self-test."""
    if value is None:
        return True
    if isinstance(value, ast.Constant):
        return value.value is None or (isinstance(value.value, str) and value.value == "")
    if isinstance(value, (ast.List, ast.Tuple, ast.Set)):
        return len(value.elts) == 0
    if isinstance(value, ast.Dict):
        return len(value.keys) == 0
    if (isinstance(value, ast.Call) and isinstance(value.func, ast.Name)
            and value.func.id in ("set", "list", "dict", "tuple") and not value.args and not value.keywords):
        return True
    return False


def _handler_is_benign(handler_body):
    """Best-effort: MATCHES a handler body that reads syntactically as genuine absence: only pass, continue,
    or a return of a recognized absence sentinel (per _is_benign_absence_value), with no raise and no other
    statement. It matches known benign/return/refuse SPELLINGS syntactically; it does not establish that the
    handler truly collapses to absence at runtime. A shadowed constructor (e.g. `except (...): return list()`
    where `list` is a parameter returning a failure code) or a caller-defined return convention can defeat
    it, so a match here does not establish benign absence. Exact behavior is the code plus --self-test."""
    if not handler_body:
        return False
    for n in handler_body:
        if isinstance(n, ast.Pass):
            continue
        if isinstance(n, ast.Continue):
            continue
        if isinstance(n, ast.Return):
            if _is_benign_absence_value(n.value):
                continue
            return False
        return False
    return True


def _scopes(tree):
    yield tree
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield node


def _diagnose(rel, tree):
    resolver = _Resolver(tree)
    deny, warn = [], []
    for scope in _scopes(tree):
        scope_resolver = resolver.scoped(_scope_bound_names(scope))   # CATCH-ALL 1: per-scope shadowing
        path_names = _proven_path_names(scope, scope_resolver)
        _analyze_function(scope, scope_resolver, rel, deny, warn, path_names)
        _analyze_try(scope, scope_resolver, rel, deny, warn, path_names)
    return deny, warn


# --- scan-set enumeration (shared shape with check_timer_restore) -------------------------------------
def _iter_declared_dir(root, rel, errors):
    d = root / rel
    try:
        names = sorted(os.listdir(d))
    except OSError as exc:
        errors.append((rel, 0, "cannot list required directory: {}".format(exc)))
        return
    for name in names:
        if not name.endswith(".py"):
            continue
        r = rel + "/" + name
        p = d / name
        try:
            mode = p.lstat().st_mode
        except OSError as exc:
            errors.append((r, 0, "cannot stat declared input: {}".format(exc)))
            continue
        if not stat.S_ISREG(mode):
            errors.append((r, 0, "declared input is not a regular file"))
            continue
        yield r, p


def _scan_set(root, errors):
    out = []
    for r, p in _iter_declared_dir(root, TOOLS_REL, errors):
        out.append((r, p))
    for r, p in _iter_declared_dir(root, HOOKS_SCRIPTS_REL, errors):
        out.append((r, p))
    return sorted(out)


def scan(root, files):
    """Scan an explicit (rel, abspath) file list. Returns (errors, deny, warn) sorted. A read, decode, or
    parse failure on a declared input is a cannot-evaluate (fail-closed)."""
    errors, deny, warn = [], [], []
    for rel, abspath in files:
        phase = "read"
        try:
            try:
                raw = abspath.read_bytes()
            except OSError as exc:
                errors.append((rel, 0, "cannot read declared input: {}".format(exc)))
                continue
            except (RecursionError, MemoryError) as exc:
                # A huge declared input under memory pressure can raise MemoryError in the read/decode
                # phase, before the parse-stage capacity catch below is reached. Fail closed as a located
                # cannot-evaluate (exit 2), the same fail-closed path as an unreadable/unparseable input,
                # never an uncaught error escaping the run. Not portably self-testable (it needs an
                # address-space ulimit), so it is covered by this catch rather than a flaky self-test leg.
                errors.append((rel, 0, "declared input exceeds the analyzer's capacity (cannot evaluate): "
                               "{}".format(type(exc).__name__)))
                continue
            phase = "decode"
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError as exc:
                errors.append((rel, 0, "declared input is not valid UTF-8: {}".format(exc)))
                continue
            except (RecursionError, MemoryError) as exc:
                errors.append((rel, 0, "declared input exceeds the analyzer's capacity (cannot evaluate): "
                               "{}".format(type(exc).__name__)))
                continue
            phase = "parse"
            try:
                tree = ast.parse(text, filename=rel)
            except SyntaxError as exc:
                errors.append((rel, exc.lineno or 0, "declared input does not parse (SyntaxError): {}".format(exc.msg)))
                continue
            except ValueError as exc:
                # A null-byte or otherwise malformed source makes ast.parse raise ValueError (not
                # SyntaxError) on CPython < 3.12; 3.12+ raises SyntaxError, caught above. Close it
                # fail-closed either way as a located cannot-evaluate (exit 2), never an uncaught error
                # escaping the run (SC1 third state). RecursionError/MemoryError below are a disjoint
                # capacity case, unaffected.
                errors.append((rel, 0, "declared input does not parse (malformed source): {}".format(exc)))
                continue
            except (RecursionError, MemoryError) as exc:
                errors.append((rel, 0, "declared input exceeds the analyzer's capacity (cannot evaluate): "
                               "{}".format(type(exc).__name__)))
                continue
            phase = "diagnose"
            try:
                d, w = _diagnose(rel, tree)
            except (RecursionError, MemoryError) as exc:
                # A syntactically valid but analysis-hostile input (e.g. a very deep AST) can drive the
                # recursive dataflow/provenance pass past the interpreter's recursion or memory limit. Fail
                # closed as a located cannot-evaluate (exit 2), the same fail-closed path as an unreadable/
                # unparseable input. RecursionError and MemoryError get this specific capacity message; any
                # OTHER uncaught error is caught by the per-file broad backstop below as a located
                # cannot-evaluate (surfaced, never masked), never an uncaught error escaping the run.
                errors.append((rel, 0, "declared input exceeds the analyzer's capacity (cannot evaluate): "
                               "{}".format(type(exc).__name__)))
                continue
            deny.extend(d)
            warn.extend(w)
        except Exception as exc:  # noqa: BLE001  FINAL broad backstop; catch Exception, not BaseException
            # CLASS-WIDTH FAIL-CLOSED (SC1): any otherwise-uncaught error anywhere in this file's
            # read/decode/parse/diagnose pipeline is recorded as a LOCATED cannot-evaluate naming the file,
            # the phase, and the exception type+message, so a genuine bug is VISIBLE (never masked)
            # and drives exit 2, never an uncaught error escaping the run. This broad catch replaces the
            # piecemeal type-specific catches as the backstop; the narrower catches above stay for their
            # specific located messages. KeyboardInterrupt and SystemExit are BaseException, left uncaught.
            errors.append((rel, 0, "cannot evaluate declared input during {} ({}: {})".format(
                phase, type(exc).__name__, exc)))
            continue
    errors.sort()
    deny.sort()
    warn.sort()
    return errors, deny, warn


def _safe_write(stream, text):
    """Write text to a text stream ENCODING-SAFE. An advisory/diagnostic line carries file names and paths,
    so under a non-UTF-8 stdout locale a plain write could raise UnicodeEncodeError. On that error, re-encode
    through the stream's own encoding with unencodable characters backslash-escaped, so the line is written
    in the stream's encoding (a Latin-1 stream keeps its Latin-1 characters; it is not forced to ASCII) and
    the fallback does not itself raise UnicodeEncodeError. A write failure other than an encoding error (e.g.
    a closed or full stream) is an environment/tool failure, not a scanned-input verdict; it is left to
    propagate rather than being turned into an advisory or a scanned-input result."""
    try:
        stream.write(text)
        return
    except UnicodeEncodeError:
        enc = getattr(stream, "encoding", None) or "utf-8"
        data = text.encode(enc, "backslashreplace")
        buf = getattr(stream, "buffer", None)
        if buf is None:
            stream.write(data.decode(enc, "backslashreplace"))
        else:
            buf.write(data)
            buf.flush()


def _emit(line):
    """Print one advisory/diagnostic line to stdout, encoding-safe (see _safe_write)."""
    _safe_write(sys.stdout, line + "\n")


def run(root):
    """Scan the declared set and report. WARN-only v1: the certain collapse shapes and the resist-static
    shapes are both emitted as WARN advisories for human review, and the gate NEVER blocks. A scanned input
    yields exit 0 (ran, with or without advisories) or exit 2 (cannot-evaluate on an unreadable, unparseable,
    or over-capacity input); it never exits 1 on a scanned input. The per-file pipeline fails closed to a
    located cannot-evaluate (see scan()) and advisory rendering is encoding-safe (see _emit). An output-stream
    failure (e.g. a full or broken stdout) is an environment/tool failure that may surface as a nonzero tool
    exit; it is not a scanned-input verdict."""
    try:
        errors = []
        files = _scan_set(root, errors)
        scan_errors, deny, warn = scan(root, files)
        errors = sorted(errors + scan_errors)
        for rel, lineno, msg in errors:
            where = "{}:{}".format(rel, lineno) if lineno else rel
            _emit("cannot-evaluate: {}: {}".format(where, msg))
        # WARN-only v1: the certain-shape findings (deny) and the resist-static findings (warn) are both
        # advisories on the same stream; both tiers emit advisories at exit 0. Sharper precision is a
        # disclosed follow-on.
        for rel, lineno, msg in warn:
            _emit("WARN: {}:{}: {}".format(rel, lineno, msg))
        for rel, lineno, msg in deny:
            _emit("WARN: {}:{}: {}".format(rel, lineno, msg))
        if errors:
            _emit("RESULT: cannot-evaluate ({} issue(s)); fail-closed".format(len(errors)))
            return 2
        _emit("PASS (advisory): {} certain-shape and {} resist-static advisory WARN(s) in the scanned "
              "surfaces; WARN-only v1 never blocks".format(len(deny), len(warn)))
        return 0
    except Exception as exc:  # noqa: BLE001  final backstop: never let an uncaught error escape the run
        # An unrecoverable output or scan failure fails closed to a cannot-evaluate exit 2, and never
        # escapes uncaught. The stderr note is itself best-effort and encoding-safe.
        try:
            _safe_write(sys.stderr, "cannot-evaluate: advisory run failed ({}: {}); fail-closed\n".format(
                type(exc).__name__, exc))
        except Exception:  # noqa: BLE001  stderr itself unwritable; still fail closed, never an uncaught-error escape
            pass
        return 2


def _repo_root():
    p = Path(__file__).resolve()
    for anc in [p, *p.parents]:
        if (anc / ".git").exists():
            return anc
    return Path.cwd()


def main():
    if "--self-test" in sys.argv[1:]:
        return self_test_main()
    return run(_repo_root())


# --- self-test ----------------------------------------------------------------------------------------
# Synthetic trees under a private tempdir the test creates and removes (test-hermeticity); generic
# placeholders only (state.d, trusted_root). WARN-only v1: every scan that ran exits 0; a "certain" case
# emits the certain-shape advisory, a "warn" case emits the
# resist-static advisory, and a "clean" case emits no advisory:
#   each fixture in the `cases` table below is asserted against its own recorded kind, the cannot-evaluate
#   legs assert exit 2 with a located diagnostic naming the input, and the subprocess legs assert the child
#   never exits 1 and that run() emits a located advisory for both a resist-static and a certain-shape
#   fixture. These assertions bind to the exact fixture inputs defined below, not to any general per-shape
#   guarantee; the detector source is the authoritative account of what it flags.

_CERTAIN_STATE_SRC = '''\
import os
def ensure():
    if os.path.isdir("state.d"):
        use_it()
    else:
        os.makedirs("state.d")
        use_it()
'''

_PASS_DESC_SRC = '''\
import os
def classify(name):
    fd = os.open("trusted_root", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        st = os.lstat(name, dir_fd=fd)
        if os.path.stat.S_ISDIR(st.st_mode):
            return "dir"
        raise RuntimeError("wrong type")
    finally:
        os.close(fd)
'''

_WARN_HINT_SRC = '''\
import os
def check(root):
    if not os.path.exists(root + "/lockfile"):
        proceed()
'''

_CERTAIN_CONFLATED_SRC = '''\
def read_marker(path):
    try:
        with open(path + "/marker", "r") as fh:
            return fh.read()
    except (FileNotFoundError, PermissionError):
        return None
'''

_REFUSE_SRC = '''\
import os
def ensure(lock_path):
    if not os.path.exists(lock_path):
        raise RuntimeError("lock missing")
    proceed()
'''

# fixture nonfs-receiver (`def refresh(self): if not self.cache.exists(): self.cache.write_text("data")`):
# the receiver `self.cache` is not recognized as a filesystem path here; this fixture asserts kind clean.
_NONFS_RECEIVER_SRC = '''\
def refresh(self):
    if not self.cache.exists():
        self.cache.write_text("data")
'''

# A str.replace() in the negative branch of an os.path.exists check is a string format, not a filesystem
# mutation (codex #3): it must not emit a certain-shape advisory (exit 0; a name-hint WARN is acceptable).
_STR_REPLACE_SRC = '''\
import os
def label(self):
    if not os.path.exists(self.state_path):
        return self.state_path.replace("_", " ")
    return read(self.state_path)
'''

# A Path-method mutation on a bare-Name proven-Path receiver whose absent branch mutates the same path
# (codex #4 / claude B-3): the branch-ordering fix must let it reach method classification and be flagged
# (exit 0; a certain-shape advisory).
_PROVEN_PATH_CERTAIN_SRC = '''\
from pathlib import Path
def ensure(d):
    out = Path(d) / "cache"
    if not out.exists():
        out.mkdir()
'''

# A conflated except whose handler returns an explicit FAILURE status is a refusal, not a benign-absence
# collapse (codex #5): it must NOT emit a certain-shape advisory (exit 0, clean).
_CONFLATED_REFUSE_SRC = '''\
import os
def check_marker(marker):
    try:
        os.stat(marker)
    except (FileNotFoundError, PermissionError):
        return 2
    return 0
'''

# A builtin open in write mode given via a `mode=` keyword must be recognized as a state change (claude
# m-1), so this same-path check-then-create is a certain-shape advisory (exit 0).
_OPEN_KW_CERTAIN_SRC = '''\
import os
def make_lock():
    if not os.path.isfile("state.lock"):
        open("state.lock", mode="w")
'''

# An alias chain where a link becomes non-Path (codex #3): `p` is rebound to a caller-supplied `cache`, so
# the dependent alias `q = p` is no longer a proven filesystem receiver. The disqualification must propagate
# through the alias, so the classify+mutation on `q` is NOT convicted (exit 0).
_ALIAS_CHAIN_CLEAN_SRC = '''\
from pathlib import Path
def refresh(cache):
    p = Path("state")
    p = cache
    q = p
    if not q.exists():
        q.write_text("data")
'''

# A conflated except guarding a method-form classify on an UNPROVEN receiver (codex #4): `state_cache` may be
# a non-filesystem object with an `.exists()` method, so receiver provenance (applied uniformly on the
# conflated-except certain-shape path) withholds the certain-shape advisory (exit 0).
_CONFLATED_NONFS_SRC = '''\
def probe(state_cache):
    try:
        return state_cache.exists()
    except (FileNotFoundError, PermissionError):
        return None
'''

# CATCH-ALL 1 (codex #3): `os` is a PARAMETER shadowing the module, so os.path.isdir / os.makedirs are
# calls on a caller-supplied object, not the filesystem API; the scope bails out of the certain-shape advisory (exit 0).
_SHADOWED_OS_CLEAN_SRC = '''\
def ensure(os):
    if not os.path.isdir("state.d"):
        os.makedirs("state.d")
'''

# fixture shadowed-path-clean (`def refresh(Path): p = Path("state"); if not p.exists(): p.write_text(...)`):
# `Path` is a parameter shadowing the constructor here, so Path("state") is not recognized as a filesystem
# receiver; this fixture asserts kind clean (exit 0).
_SHADOWED_PATH_CLEAN_SRC = '''\
from pathlib import Path
def refresh(Path):
    p = Path("state")
    if not p.exists():
        p.write_text("data")
'''

# fixture loop-rebind-clean (`p = Path("state"); for p in [cache]: if not p.exists(): p.write_text(...)`):
# the for-loop target rebinds `p` from a recognized Path to an unproven loop element, so `p` is no longer
# recognized as a filesystem receiver here; this fixture asserts kind clean (exit 0).
_LOOP_REBIND_CLEAN_SRC = '''\
from pathlib import Path
def refresh(cache):
    p = Path("state")
    for p in [cache]:
        if not p.exists():
            p.write_text("data")
'''

# CATCH-ALL 2 (codex #4): a walrus REBINDS `p` from a proven Path to an unproven value before the
# conditional, so provenance is lost and no certain-shape advisory fires (exit 0).
_WALRUS_REBIND_CLEAN_SRC = '''\
from pathlib import Path
def refresh(cache):
    p = Path("state")
    if not (p := cache).exists():
        p.write_text("data")
'''

# CATCH-ALL 3 (codex #5): an INNER try transforms PermissionError into RuntimeError before it could reach
# the outer conflated handler, so the outer `except (FileNotFoundError, PermissionError)` does not actually
# conflate a non-absence error from os.stat; no certain-shape advisory fires (exit 0).
_NESTED_TRY_CLEAN_SRC = '''\
import os
def inspect(root):
    try:
        try:
            return os.stat(root + "/state")
        except PermissionError as exc:
            raise RuntimeError("refused") from exc
    except (FileNotFoundError, PermissionError):
        return None
'''

# FIX3 (TryStar boundary): an inner `except*` try transforms PermissionError before it could reach the
# outer conflated handler, so os.stat inside the inner try* is not credited to the outer
# `except (FileNotFoundError, PermissionError)`; _walk_stop_at_try stops at the inner ast.TryStar, so no
# certain-shape advisory fires (exit 0, clean). except* is Python 3.11+; this fixture is only added to the
# cases table when ast.TryStar exists (see self_test_main).
_NESTED_TRYSTAR_CLEAN_SRC = '''\
import os
def inspect(root):
    try:
        try:
            return os.stat(root + "/state")
        except* PermissionError as exc:
            raise RuntimeError("refused") from exc
    except (FileNotFoundError, PermissionError):
        return None
'''


# R9 (round-9 QA, early-return fall-through with a name-hint): an `if classify(p): return` guard with the
# mutation after it (outside the classify's own branch) on a name-hint path is not the modelled certain
# same-path shape, yet a following-classify with a benign-negative branch is visible; it must emit a
# resist-static WARN advisory (exit 0); the r9-early-return-namehint fixture asserts this for this input.
_EARLY_RETURN_WARN_SRC = '''\
import os
def ensure():
    if os.path.exists("state"):
        return
    os.mkdir("state")
'''


# A certain-shape (state-change collapse) whose classified PATH STRING carries a non-ASCII character, so its
# advisory message embeds a non-ASCII path and exercises the encoding-safe emitter under an ASCII stdout.
# Written with a \u escape so this test source stays ASCII-only; the fixture file on disk carries the real
# character (utf-8). "state" is the name-hint that makes it relevant.
_NONASCII_ADVISORY_SRC = '''\
import os
def ensure():
    if os.path.isdir("state_\u00e9.d"):
        use_it()
    else:
        os.makedirs("state_\u00e9.d")
'''


def self_test_main():
    import io
    import shutil
    import subprocess
    import tempfile
    from contextlib import redirect_stdout

    def run_quiet_files(root, files):
        # WARN-only v1: a scan that ran exits 0; only a cannot-evaluate exits 2. `deny` is the certain-shape
        # advisory list (still detected, now advisory), `warn` the resist-static advisory list.
        with redirect_stdout(io.StringIO()):
            scan_errors, deny, warn = scan(root, files)
            if scan_errors:
                return 2, deny, warn
            return 0, deny, warn

    def write(base, rel, text):
        p = base / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if text is not None:
            p.write_text(text, encoding="utf-8")
        return rel, p

    def _located_warn(out, expected_rel):
        # A WARN line renders as "WARN: <rel>:<lineno>: <diagnostic>" (see run()/_emit). Assert the
        # advisory is LOCATED: the expected fixture relative path, a positive line number, and nonempty
        # diagnostic text after the location, not merely a line that starts with "WARN:".
        for ln in out.splitlines():
            if not ln.startswith("WARN: "):
                continue
            head, sep, diag = ln[len("WARN: "):].partition(": ")
            if not sep or not diag.strip():
                continue
            rel_part, _, line_part = head.rpartition(":")
            if rel_part == expected_rel and line_part.isdigit() and int(line_part) > 0:
                return True
        return False


    try:
        tmp = Path(tempfile.mkdtemp(prefix="aiqt-path-classification-selftest-"))
    except OSError as exc:
        print("SELF-TEST ERROR: no writable temporary directory: {}".format(exc), file=sys.stderr)
        return 2
    failures = []
    skipped = []
    try:
        base = tmp / "tree"
        # kind: "certain" (a certain-shape advisory expected), "warn" (a
        # resist-static advisory expected), "clean" (no advisory at all), "clean-or-warn" (no certain-shape
        # advisory; a resist-static advisory is acceptable). Every scan that ran exits 0 under WARN-only v1.
        cases = [
            ("deny-state", _CERTAIN_STATE_SRC, "certain"),
            ("pass-desc", _PASS_DESC_SRC, "clean"),
            ("warn-hint", _WARN_HINT_SRC, "warn"),
            ("deny-conflated", _CERTAIN_CONFLATED_SRC, "certain"),
            ("refuse", _REFUSE_SRC, "clean"),
            ("nonfs-receiver", _NONFS_RECEIVER_SRC, "clean"),
            ("str-replace", _STR_REPLACE_SRC, "clean-or-warn"),
            ("proven-path-deny", _PROVEN_PATH_CERTAIN_SRC, "certain"),
            ("conflated-refuse", _CONFLATED_REFUSE_SRC, "clean"),
            ("open-kw-deny", _OPEN_KW_CERTAIN_SRC, "certain"),
            ("alias-chain-clean", _ALIAS_CHAIN_CLEAN_SRC, "clean"),
            ("conflated-nonfs-clean", _CONFLATED_NONFS_SRC, "clean"),
            ("shadowed-os-clean", _SHADOWED_OS_CLEAN_SRC, "clean"),
            ("shadowed-path-clean", _SHADOWED_PATH_CLEAN_SRC, "clean"),
            ("loop-rebind-clean", _LOOP_REBIND_CLEAN_SRC, "clean"),
            ("walrus-rebind-clean", _WALRUS_REBIND_CLEAN_SRC, "clean"),
            ("nested-try-clean", _NESTED_TRY_CLEAN_SRC, "clean"),
            ("r9-early-return-namehint", _EARLY_RETURN_WARN_SRC, "warn"),
        ]
        # FIX3: the nested-except*-try fixture uses `except*` syntax (Python 3.11+), so it is added only
        # where ast.TryStar exists; on an older interpreter it would not parse and is skipped by kind.
        if hasattr(ast, "TryStar"):
            cases.append(("nested-trystar-clean", _NESTED_TRYSTAR_CLEAN_SRC, "clean"))
        else:
            skipped.append("nested-trystar (no ast.TryStar before Python 3.11)")
        for name, src, kind in cases:
            rel, p = write(base, "tools/case_{}.py".format(name), src)
            code, deny, warns = run_quiet_files(base, [(rel, p)])
            if code != 0:
                failures.append("{}: expected exit 0 (WARN-only), got {}".format(name, code))
            if kind == "certain":
                if not deny:
                    failures.append("{}: expected a certain-shape advisory, got none"
                                    .format(name))
            elif kind == "warn":
                if deny:
                    failures.append("{}: expected no certain-shape advisory, got {}".format(name, len(deny)))
                if not warns:
                    failures.append("{}: expected a resist-static advisory WARN, got none".format(name))
            elif kind == "clean":
                if deny or warns:
                    failures.append("{}: expected a clean scan (no advisory), got deny {} warn {}"
                                    .format(name, len(deny), len(warns)))
            elif kind == "clean-or-warn":
                if deny:
                    failures.append("{}: expected no certain-shape advisory, got {}".format(name, len(deny)))

        # 5a. an unparseable declared input is a located cannot-evaluate (exit 2, fail-closed), never an
        # uncaught error escaping the run. This leg asserts exit 2 AND the located "does not parse
        # (SyntaxError)" diagnostic, not just the exit code. Call scan() directly to inspect the located
        # diagnostic; run_quiet_files discards the messages this leg asserts on.
        rel, p = write(base, "tools/case_bad.py", "def broken(:\n    pass\n")
        with redirect_stdout(io.StringIO()):
            bad_errors, _, _ = scan(base, [(rel, p)])
        bad_code = 2 if bad_errors else 0
        if bad_code != 2:
            failures.append("syntaxerror: expected exit 2, got {}".format(bad_code))
        if not any("does not parse (SyntaxError)" in msg for _, _, msg in bad_errors):
            failures.append("syntaxerror: expected the located parse cannot-evaluate diagnostic, got {}"
                            .format([msg for _, _, msg in bad_errors]))

        # 5b. an unreadable declared input is a located cannot-evaluate (exit 2, fail-closed), never an
        # uncaught error escaping the run. This leg asserts exit 2 AND the located "cannot read declared
        # input" diagnostic, not just the exit code. Skipped if chmod-0 stays readable (root).
        rel, p = write(base, "tools/case_unread.py", "import os\n")
        os.chmod(p, 0)
        if os.access(p, os.R_OK):
            skipped.append("unreadable (chmod-0 still readable)")
        else:
            with redirect_stdout(io.StringIO()):
                unread_errors, _, _ = scan(base, [(rel, p)])
            unread_code = 2 if unread_errors else 0
            if unread_code != 2:
                failures.append("unreadable: expected exit 2, got {}".format(unread_code))
            if not any("cannot read declared input" in msg for _, _, msg in unread_errors):
                failures.append("unreadable: expected the located read cannot-evaluate diagnostic, got {}"
                                .format([msg for _, _, msg in unread_errors]))
        os.chmod(p, 0o644)

        # 5c. a missing declared directory is a located cannot-evaluate via _scan_set. This leg asserts the
        # located "cannot list required directory" diagnostic, not merely a non-empty error list.
        empty = tmp / "empty"
        empty.mkdir()
        errs = []
        _scan_set(empty, errs)
        if not any("cannot list required directory" in msg for _, _, msg in errs):
            failures.append("missing-dirs: expected the located cannot-list cannot-evaluate diagnostic, got {}"
                            .format([msg for _, _, msg in errs]))

        # 5d. a declared input that is valid UTF-8 and PARSES but whose AST is so deep the recursive
        # provenance/dataflow pass exceeds the interpreter's recursion limit is a cannot-evaluate (exit 2,
        # fail-closed), never an uncaught RecursionError escaping the run. This leg asserts exit 2 AND the
        # located capacity ("exceeds the analyzer's capacity (cannot evaluate)") diagnostic, not just the
        # exit code.
        deep_src = "x = p" + ".parent" * 20000 + "\n"
        rel, p = write(base, "tools/case_deep.py", deep_src)
        # Call scan() directly to inspect the located diagnostic; the exit derived here (2 iff any
        # scan error) mirrors run_quiet_files, which discards the error messages this leg asserts on.
        with redirect_stdout(io.StringIO()):
            deep_errors, _, _ = scan(base, [(rel, p)])
        deep_code = 2 if deep_errors else 0
        if deep_code != 2:
            failures.append("deep-ast: expected exit 2 (cannot-evaluate), got {}".format(deep_code))
        if not any("exceeds the analyzer's capacity (cannot evaluate)" in msg
                   for _, _, msg in deep_errors):
            failures.append("deep-ast: expected the located capacity cannot-evaluate diagnostic, got {}"
                            .format([msg for _, _, msg in deep_errors]))

        # 5e. a NUL-byte source is a malformed declared input: ast.parse raises SyntaxError on CPython >=
        # 3.12 and ValueError on < 3.12, and either way it is a located cannot-evaluate (exit 2, fail-closed),
        # never an uncaught error escaping the run. Written as raw bytes so the NUL survives. This leg
        # asserts exit 2 AND the located "does not parse" diagnostic, not just the exit code. Call scan()
        # directly to inspect the located diagnostic; run_quiet_files discards the messages this leg asserts on.
        rel = "tools/case_nul.py"
        p = base / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"import os\nx = 0\x00\n")
        with redirect_stdout(io.StringIO()):
            nul_errors, _, _ = scan(base, [(rel, p)])
        nul_code = 2 if nul_errors else 0
        if nul_code != 2:
            failures.append("nul-byte: expected exit 2 (cannot-evaluate), got {}".format(nul_code))
        if not any("does not parse" in msg for _, _, msg in nul_errors):
            failures.append("nul-byte: expected the located parse cannot-evaluate diagnostic, got {}"
                            .format([msg for _, _, msg in nul_errors]))

        # 5f. SUBPROCESS regression legs: run the gate as a CHILD PROCESS over a fixture repo root,
        # exercising the FULL main/run path (read/decode/parse/diagnose AND advisory RENDERING/output), and
        # assert the child exit is 0 or 2, NEVER 1 (the class-width SC1 contract: no scanned input exits 1).
        # The child runs isolated (-I) as the gate is meant to, imports this module by path, and calls run()
        # on the fixture root, so _repo_root() is not consulted.
        this_dir = str(Path(__file__).resolve().parent)
        module_name = Path(__file__).stem

        def child_exit(fixture_root, extra_env=None, ascii_stdout=False):
            boot = "import sys\n"
            if ascii_stdout:
                # An isolated interpreter (-I implies -E) IGNORES PYTHONIOENCODING, and the C locale may be
                # coerced to UTF-8, so force an ASCII stdout/stderr directly to reproduce the non-UTF-8
                # rendering condition deterministically (the env is still set to record intent).
                boot += ("sys.stdout.reconfigure(encoding='ascii')\n"
                         "sys.stderr.reconfigure(encoding='ascii')\n")
            boot += ("sys.path.insert(0, {!r})\n".format(this_dir)
                     + "from pathlib import Path\n"
                     + "import {} as _m\n".format(module_name)
                     + "sys.exit(_m.run(Path({!r})))\n".format(str(fixture_root)))
            env = dict(os.environ)
            if extra_env:
                env.update(extra_env)
            proc = subprocess.run([sys.executable, "-I", "-B", "-c", boot],
                                  capture_output=True, env=env)
            return proc.returncode

        def make_child_root(sub, case_name, text=None, raw=None):
            froot = tmp / sub
            (froot / "tools").mkdir(parents=True, exist_ok=True)
            (froot / ".aiqt/core/hooks/scripts").mkdir(parents=True, exist_ok=True)
            fp = froot / "tools" / case_name
            if raw is not None:
                fp.write_bytes(raw)
            elif text is not None:
                fp.write_text(text, encoding="utf-8")
            return froot

        # deep-AST fixture -> cannot-evaluate (exit 2), never an uncaught-error escape.
        rc = child_exit(make_child_root("child_deep", "case_deep.py", text=deep_src))
        if rc == 1:
            failures.append("deep-ast (subprocess): child exited 1 (uncaught error escaped)")
        elif rc != 2:
            failures.append("deep-ast (subprocess): expected exit 2, got {}".format(rc))

        # null-byte fixture -> cannot-evaluate (exit 2), never an uncaught-error escape.
        rc = child_exit(make_child_root("child_nul", "case_nul.py", raw=b"import os\nx = 0\x00\n"))
        if rc == 1:
            failures.append("nul-byte (subprocess): child exited 1 (uncaught error escaped)")
        elif rc != 2:
            failures.append("nul-byte (subprocess): expected exit 2, got {}".format(rc))

        # ASCII-locale advisory rendering: a fixture whose advisory carries a NON-ASCII classified path,
        # rendered under an ASCII stdout. Encoding-safe rendering keeps the advisory emitted and the exit 0
        # (never 1); only an unrecoverable output failure routes to exit 2.
        rc = child_exit(make_child_root("child_ascii", "case_nonascii.py", text=_NONASCII_ADVISORY_SRC),
                        extra_env={"LC_ALL": "C", "PYTHONIOENCODING": "ascii"}, ascii_stdout=True)
        if rc == 1:
            failures.append("ascii-locale (subprocess): child exited 1 (advisory rendering not encoding-safe)")
        elif rc not in (0, 2):
            failures.append("ascii-locale (subprocess): expected exit 0 or 2, got {}".format(rc))

        # 5g. RUN-PATH ADVISORY-EMISSION leg: exercise the REAL run() entry point in an isolated child over a
        # /dev/shm fixture root (TMPDIR routes tmp there) and assert BOTH the child exits 0 AND a located
        # "WARN:" advisory is actually EMITTED on stdout, so a regression in the reporting/exit-code path
        # cannot pass unseen. The r9-early-return-namehint leg above exercises scan() alone (not run() or its
        # rendered output); this companion drives run() end to end over the same early-return fixture.
        def child_run_out(fixture_root):
            boot = ("import sys\n"
                    + "sys.path.insert(0, {!r})\n".format(this_dir)
                    + "from pathlib import Path\n"
                    + "import {} as _m\n".format(module_name)
                    + "sys.exit(_m.run(Path({!r})))\n".format(str(fixture_root)))
            proc = subprocess.run([sys.executable, "-I", "-B", "-c", boot],
                                  capture_output=True, env=dict(os.environ))
            return proc.returncode, proc.stdout.decode("utf-8", "replace")

        rc, out = child_run_out(make_child_root("child_warn_early_return", "case_early_return.py",
                                                text=_EARLY_RETURN_WARN_SRC))
        if rc != 0:
            failures.append("run-emit early-return (subprocess): expected exit 0, got {}".format(rc))
        expected_rel = "tools/case_early_return.py"
        if not _located_warn(out, expected_rel):
            failures.append("run-emit early-return (subprocess): expected a LOCATED WARN advisory "
                            "(WARN: {}:<line>: <diagnostic>) with a positive line number and nonempty "
                            "diagnostic text, got {!r}".format(expected_rel, out))

        # 5h. RUN-PATH ADVISORY-EMISSION leg for the CERTAIN-SHAPE tier: the run-emit leg above drives run()
        # over a resist-static fixture, so a break confined to the certain-shape emitter (the `deny` render
        # loop in run()) would pass it unseen. This companion drives run() end to end over a same-path
        # check-then-create (a certain-shape fixture) and asserts BOTH the child exits 0 AND a LOCATED
        # advisory is emitted for it, so a delocated or dropped certain-shape emission is caught.
        _CERTAIN_RUN_SRC = ("import os\n"
                            "def ensure():\n"
                            "    if not os.path.isdir(\"state.d\"):\n"
                            "        os.mkdir(\"state.d\")\n")
        rc, out = child_run_out(make_child_root("child_certain_run", "case_certain_run.py",
                                                text=_CERTAIN_RUN_SRC))
        if rc != 0:
            failures.append("run-emit certain-shape (subprocess): expected exit 0, got {}".format(rc))
        expected_rel = "tools/case_certain_run.py"
        if not _located_warn(out, expected_rel):
            failures.append("run-emit certain-shape (subprocess): expected a LOCATED advisory "
                            "(WARN: {}:<line>: <diagnostic>) with a positive line number and nonempty "
                            "diagnostic text for the certain-shape fixture, got {!r}".format(expected_rel, out))

        # 5i. FIX1 (unparse-capacity fail-closed): a declared input that PARSES but whose classified path
        # expression is too deeply nested for ast.unparse drives a RecursionError out of _unparse. That
        # capacity failure must propagate to scan()'s capacity handler and fail closed (run() exit 2 with a
        # located capacity diagnostic), never be swallowed into a rc-0 advisory. Depth 400 parses but
        # exceeds ast.unparse's recursion capacity (default recursion limit 1000). Driven end to end through
        # run() in an isolated child; asserts exit 2 AND the located capacity diagnostic naming the fixture.
        _UNPARSE_CAP_SRC = ("import os\n"
                            "def f(x):\n"
                            "    if os.path.exists(\"state\"" + "+x" * 400 + "):\n"
                            "        pass\n")
        rc, out = child_run_out(make_child_root("child_unparse_cap", "case_unparse_cap.py",
                                                text=_UNPARSE_CAP_SRC))
        if rc != 2:
            failures.append("unparse-capacity (subprocess): expected exit 2 (cannot-evaluate), got {}"
                            .format(rc))
        if ("exceeds the analyzer's capacity (cannot evaluate)" not in out
                or "tools/case_unparse_cap.py" not in out):
            failures.append("unparse-capacity (subprocess): expected the located capacity cannot-evaluate "
                            "diagnostic naming the fixture on stdout, got {!r}".format(out))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if failures:
        for f in failures:
            print("SELF-TEST FAIL: {}".format(f), file=sys.stderr)
        return 1
    tail = " ({} skipped: {})".format(len(skipped), "; ".join(skipped)) if skipped else ""
    print("SELF-TEST PASS (WARN-only v1): every scan that ran exited 0, every cases-table fixture matched "
          "its recorded kind, the malformed and unreadable declared inputs that ran each failed closed to a "
          "located cannot-evaluate (exit 2), and the subprocess legs confirmed the child never exits 1 and "
          "that run() emits a located advisory for both a resist-static and a certain-shape fixture; these "
          "are the fixture-bound invariants the suite exercises, not a general guarantee of the "
          "detector's behaviour{}"
          .format(tail))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
