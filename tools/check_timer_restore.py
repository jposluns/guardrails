#!/usr/bin/env python3
"""Advisory scan for verbatim restore of a borrowed process-global timer (a deterministic code-shape gate).

Code that borrows a caller's process-global deadline facility (a POSIX interval timer or single-shot
alarm) for a bounded window and then RESTORES the caller's saved value VERBATIM re-arms the caller's
deadline to its full original interval, silently moving or losing a deadline the caller had already set,
most dangerously a watchdog's. The corpus rule tmrrst (quali-elapsed-aware-timer-restore) requires the
restore to be elapsed-aware: the saved remaining interval reduced by the time the window consumed, a
deadline that would have expired during the window clamped to fire immediately rather than re-armed at
full value.

This gate is an ADVISORY (WARN-only) v1 best-effort heuristic, not a decision procedure and not a
specification of its own behaviour. It scans a recognizable subset of the repo's own Python (AST plus an
ordered, per-scope dataflow pass; a regex never convicts) and EMITS WARN advisories, at a certain-shape or
a resist-static tier, for the borrowed-timer save-plus-restore scopes it recognizes, flagging them for
human review against tmrrst. It NEVER blocks CI. Recognition is syntactic and per-scope, and it applies
internal suppression and downgrade heuristics: for example a derived restore accompanied by elapsed
evidence (a time.monotonic or perf_counter read in the same scope) can be suppressed as a plausibly
elapsed-aware restore. It guarantees neither a WARN for every risky restore nor silence for any particular
shape: a shape it does not model may emit or may go unflagged, so it can both miss a genuine verbatim
re-arm and, on dataflow it does not follow (for example a timer API reached through an unresolved alias,
getattr, or dynamic dispatch; a value that flows across a function or module boundary; a value stashed into
and read back from a container or helper; an `import ... as <name>` rebind; or FFI or non-Python
manipulation), emit a false-positive advisory; both are harmless under WARN-only. The DETECTOR source below
and its --self-test are the authoritative account of exactly what it flags; this docstring does not restate
that account, and sharper precision that could later support a non-advisory classification is a disclosed
follow-on, deferred because a sound static-dataflow conviction over arbitrary Python binding and
control-flow is not yet achieved.

CANNOT-EVALUATE (exit 2, fail-closed per the check-fails-closed-on-unreadable rule): any declared scanned
input missing, unreadable, non-regular, non-UTF-8, or unparseable (a SyntaxError on a declared file is a
named refusal, never a skip); a declared input whose AST exceeds the analyzer's recursion or memory
capacity; or a non-isolated run (the bootstrap self-guard).

BOOTSTRAP SELF-GUARD. The gate's first executable statements import only sys and refuse to run (exit 2)
unless the interpreter is isolated, so a sibling planted beside this gate cannot shadow a stdlib import
and neuter the gate before it can check anything.

SCANNED SURFACES (the declared set, resolved from the repo root): every regular *.py directly under
tools/ (the vendored tools/_vendor/ subtree is excluded), and every regular *.py directly under
.aiqt/core/hooks/scripts/. Both directories are REQUIRED: an unreadable or absent directory is a
cannot-evaluate, never an empty clean scan.

The tmrrst rule carries the full obligation, and the elapsed-aware save/restore living once in a shared
helper (which the rule requires) is the primary control this advisory backstops.

  check_timer_restore.py             scan the declared surfaces
  check_timer_restore.py --self-test build synthetic trees and assert the gate's invariants

Run this gate isolated: python3 -I -B tools/check_timer_restore.py
"""
import sys


def _interpreter_isolated(flags):
    """True iff a sys.flags-like object reports isolated mode: -I, or the full -P (safe_path) plus -E
    (ignore_environment) plus -s (no_user_site) equivalent."""
    return bool(flags.isolated) or bool(
        getattr(flags, "safe_path", 0) and flags.ignore_environment and flags.no_user_site)


if not _interpreter_isolated(sys.flags):
    sys.stderr.write("check_timer_restore: refusing to run non-isolated; launch it as "
                     "`python3 -I -B tools/check_timer_restore.py` (a sibling file could otherwise "
                     "shadow a stdlib import and neuter this gate)\n")
    raise SystemExit(2)

import ast  # noqa: E402  imported only after the isolation guard above
import os  # noqa: E402
import stat  # noqa: E402
from pathlib import Path  # noqa: E402

TOOLS_REL = "tools"
VENDOR_REL = "tools/_vendor"
HOOKS_SCRIPTS_REL = ".aiqt/core/hooks/scripts"

SAVE_ATTRS = frozenset({"getitimer", "alarm", "setitimer"})   # interval-bearing previous-state returns
ARM_ATTRS = frozenset({"alarm", "setitimer"})                 # the timer-arming calls
MONOTONIC_ATTRS = frozenset({"monotonic", "perf_counter", "monotonic_ns", "perf_counter_ns"})
COPY_CASTS = frozenset({"int", "float", "copy", "deepcopy"})  # an identity-preserving wrap of a saved name


class _ModuleResolver:
    """Resolve, within one module, which Name/Attribute call targets are the signal-timer and time-monotonic
    functions, honouring import aliases. `import signal [as x]` binds a module alias; `from signal import
    setitimer [as s]` binds a bare name. Unresolved aliases are simply not recognized (a disclosed residual),
    never guessed."""

    def __init__(self, tree):
        self.signal_module_aliases = set()
        self.time_module_aliases = set()
        self.bare_save = {}       # name -> attr for `from signal import getitimer as g`
        self.bare_arm = {}
        self.bare_monotonic = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "signal":
                        self.signal_module_aliases.add(alias.asname or "signal")
                    elif alias.name == "time":
                        self.time_module_aliases.add(alias.asname or "time")
            elif isinstance(node, ast.ImportFrom) and node.module == "signal" and node.level == 0:
                for alias in node.names:
                    bound = alias.asname or alias.name
                    if alias.name in SAVE_ATTRS:
                        self.bare_save[bound] = alias.name
                    if alias.name in ARM_ATTRS:
                        self.bare_arm[bound] = alias.name
            elif isinstance(node, ast.ImportFrom) and node.module == "time" and node.level == 0:
                for alias in node.names:
                    if alias.name in MONOTONIC_ATTRS:
                        self.bare_monotonic.add(alias.asname or alias.name)

    def _call_attr(self, func, module_aliases, attrs):
        """If `func` is `<module-alias>.<attr>` with attr in attrs, return attr; else None."""
        if isinstance(func, ast.Attribute) and func.attr in attrs and isinstance(func.value, ast.Name):
            if func.value.id in module_aliases:
                return func.attr
        return None

    def save_attr(self, call):
        a = self._call_attr(call.func, self.signal_module_aliases, SAVE_ATTRS)
        if a:
            return a
        if isinstance(call.func, ast.Name) and call.func.id in self.bare_save:
            return self.bare_save[call.func.id]
        return None

    def arm_attr(self, call):
        a = self._call_attr(call.func, self.signal_module_aliases, ARM_ATTRS)
        if a:
            return a
        if isinstance(call.func, ast.Name) and call.func.id in self.bare_arm:
            return self.bare_arm[call.func.id]
        return None

    def is_monotonic(self, call):
        if self._call_attr(call.func, self.time_module_aliases, MONOTONIC_ATTRS):
            return True
        if isinstance(call.func, ast.Name) and call.func.id in self.bare_monotonic:
            return True
        return False

    def scoped(self, shadowed):
        """A per-scope view of this resolver with any timer/time alias SHADOWED by a local binding removed,
        so `def f(signal): signal.alarm(...)` (a parameter shadowing the imported module) is not read as the
        real signal module. An imported alias is treated as the module only where no parameter or local
        binding in the scope reuses its name; where one does, the receiver is not provably the module and the
        scope is left unrecognized rather than convicted on an ambiguous receiver."""
        if not shadowed:
            return self
        r = _ModuleResolver.__new__(_ModuleResolver)
        r.signal_module_aliases = self.signal_module_aliases - shadowed
        r.time_module_aliases = self.time_module_aliases - shadowed
        r.bare_save = {k: v for k, v in self.bare_save.items() if k not in shadowed}
        r.bare_arm = {k: v for k, v in self.bare_arm.items() if k not in shadowed}
        r.bare_monotonic = self.bare_monotonic - shadowed
        return r


def _scope_bodies(tree):
    """Yield each analysis scope as a list of statements: the module top level (its statements minus nested
    function/class bodies are still walked, but a nested def is its own scope) and each function body. A
    scope is walked without descending into a nested FunctionDef, so `same scope` is faithful."""
    yield tree
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield node


def _names_bound(target):
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


def _walk_no_nested(nodes):
    """Walk statements/expressions without descending into a nested function or lambda body (each is its own
    scope). A nested def/lambda is yielded but never entered, so a binding inside it is attributed to that
    scope, not the enclosing one."""
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
    annotation, walrus, aug-assign, for/with/except/comprehension target), NOT descending into a nested def
    and NOT counting an `import` (an import IS the module, so it never shadows itself). Used to detect a local
    name that shadows an imported timer/time module alias."""
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
                names.update(_names_bound(t))
        elif isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name):
            names.add(n.target.id)
        elif isinstance(n, ast.AugAssign):
            names.update(_names_bound(n.target))
        elif isinstance(n, ast.NamedExpr):
            names.update(_names_bound(n.target))
        elif isinstance(n, (ast.For, ast.AsyncFor)):
            names.update(_names_bound(n.target))
        elif isinstance(n, (ast.With, ast.AsyncWith)):
            for item in n.items:
                if item.optional_vars is not None:
                    names.update(_names_bound(item.optional_vars))
        elif isinstance(n, ast.ExceptHandler):
            if n.name:
                names.add(n.name)
        elif isinstance(n, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            for gen in n.generators:
                names.update(_names_bound(gen.target))
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(n.name)                                   # a nested def/class rebinds the name
    return names


def _first_value_arg(call, attr):
    """The interval/value argument of an arm/restore call: alarm(SECONDS), setitimer(WHICH, SECONDS, ...).
    Returns the arg node or None."""
    if attr == "alarm":
        return call.args[0] if call.args else None
    if attr == "setitimer":
        return call.args[1] if len(call.args) >= 2 else None
    return None


def _is_zero_const(node):
    return isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and node.value == 0


def _is_nonzero_const(node):
    return isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and node.value != 0


def _refs_saved_identity(node, saved, bound_names):
    """True iff `node` is a saved name used identity-preserved: the bare name, a subscript/attribute of it,
    or a copy/numeric cast wrapping it (int(x), float(x), x.copy()). CATCH-ALL 1 (general shadowing): a
    bare-Name cast callable (int/float/copy/deepcopy from a builtin or import) is credited as identity-
    preserving ONLY when that name is NOT locally bound in the scope. A parameter or local that shadows
    `float` (or int/copy/deepcopy) is a caller-supplied callable that may adjust the value, not the
    identity cast, so it does not establish an identity flow and the restore is treated as derived (WARN),
    never a verbatim certain-shape advisory. The COPY_CASTS spelling alone never convicts; its binding must
    resolve."""
    if isinstance(node, ast.Name):
        return node.id in saved
    if isinstance(node, ast.Subscript):
        return _refs_saved_identity(node.value, saved, bound_names)
    if isinstance(node, ast.Attribute):
        return _refs_saved_identity(node.value, saved, bound_names)
    if isinstance(node, ast.Call):
        if (isinstance(node.func, ast.Name) and node.func.id in COPY_CASTS
                and node.func.id not in bound_names):
            return any(_refs_saved_identity(a, saved, bound_names) for a in node.args)
        if isinstance(node.func, ast.Attribute) and node.func.attr in COPY_CASTS:
            return _refs_saved_identity(node.func.value, saved, bound_names)
    return False


def _refs_saved_anywhere(node, saved):
    """True iff any saved name appears anywhere in the expression (an identity use or a derived one)."""
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name) and sub.id in saved:
            return True
    return False


def _analyze_scope(scope, resolver, bound_names):
    """Return a finding kind for one scope: 'deny', 'warn', or None. See the module docstring for the
    matrix.

    An ORDERED, per-flow pass over the scope's own statements (never descending into a nested function or
    lambda, which is its own scope) tracks which local names currently hold a VERBATIM saved timer value. A
    name ENTERS the saved set when it is bound to a timer save call (a plain assignment, an annotated
    assignment, a walrus, or a tuple unpack) or to an identity-preserving copy of a name already in the set
    (a bare name, a subscript, an attribute, or an int/float/copy wrap); it LEAVES the set when one of the
    rebinding constructs this scan models (an assignment, annotation, walrus, tuple/list unpack, aug-assign,
    for/loop target, with-as, except-as, or comprehension target) binds it to a non-save, non-identity value,
    so a reassignment to a constant, an arithmetic derivation, or a value routed through an elapsed-aware
    helper no longer carries the verbatim value. A rebind this scan does NOT model, such as an
    `import ... as <name>` alias binding the saved name, may leave the stale saved name in the set and yield a
    false-positive certain-shape advisory (harmless under WARN-only; precision a disclosed follow-on).

    Three catch-all invariants keep the certain-shape advisory confined to the robustly-simple certain shape:
      CATCH-ALL 1 (general shadowing): a cast callable spelled int/float/copy/deepcopy establishes an
        identity flow only where that name resolves to the real builtin/import, i.e. it is not locally
        bound (see _refs_saved_identity), so a shadowed `float` parameter never forges an identity restore.
      CATCH-ALL 2 (element-wise save collection): a tuple/list assignment matches its RHS elements to its
        targets element by element, so `saved, other = signal.alarm(0), 0` records the direct save on
        `saved` rather than clearing it as a whole-RHS non-save.
      CATCH-ALL 3 (straightline-only own-arm): the own nonzero arm credits the identity restore only when
        the arm certainly PRECEDES it on the SAME execution path. The `armed` state is threaded linearly
        and ISOLATED across mutually-exclusive branches (each `if`/`elif`/`else` arm, loop body, match
        case, and except handler is entered from the pre-branch armed state and its arm does not leak out),
        so an own-arm in one branch never credits an identity restore in a sibling branch. A `try` body,
        `else`, `finally`, and a `with` body are straightline continuations and carry the armed state
        through, so the ordinary save -> arm -> restore-in-finally shape still convicts."""
    saved = set()
    state = {"monotonic": False,
             "identity_restore": False, "identity_restore_armed": False, "derived_restore": False}

    def handle_call(call, armed):
        """Process one call against the current flow; returns the (possibly newly-armed) path state."""
        if resolver.is_monotonic(call):
            state["monotonic"] = True
        arm = resolver.arm_attr(call)
        if not arm:
            return armed
        val = _first_value_arg(call, arm)
        if val is None:
            return armed
        # An own nonzero arm marks THIS execution path armed (a live save must already exist), so
        # `alarm(5); saved = alarm(0); alarm(saved)` (arm before save) never forges a certain-shape advisory, and an arm in
        # a sibling branch never leaks to a restore in another (the caller isolates branch armed state).
        if _is_nonzero_const(val) and saved:
            return True
        if saved and _refs_saved_identity(val, saved, bound_names):
            state["identity_restore"] = True
            if armed:
                state["identity_restore_armed"] = True
        elif saved and _refs_saved_anywhere(val, saved):
            state["derived_restore"] = True
        return armed

    def bind(names, value):
        """Update the saved set for one target<-value binding; returns True iff the value is a combined
        save-and-own-arm (`saved = signal.alarm(5)`), whose arm coincides with the save and so arms the
        path here (the RHS calls are visited before this binding, so `saved` reflects the flow so far)."""
        if isinstance(value, ast.Call) and resolver.save_attr(value):
            combined = False
            arm = resolver.arm_attr(value)
            if arm is not None:
                v = _first_value_arg(value, arm)
                if v is not None and _is_nonzero_const(v):
                    combined = True
            saved.update(names)
            return combined
        if _refs_saved_identity(value, saved, bound_names):
            saved.update(names)                 # an identity-preserving copy propagates the saved value
            return False
        saved.difference_update(names)           # a non-save, non-identity rebind clears the name's saved status
        return False

    def bind_assign(targets, value, armed):
        """CATCH-ALL 2: bind an assignment element-wise when a tuple/list target pairs with a tuple/list
        value of equal, non-starred length, so a direct save inside a tuple assignment is collected on the
        right name; otherwise bind the whole value to each target."""
        for tgt in targets:
            if (isinstance(tgt, (ast.Tuple, ast.List)) and isinstance(value, (ast.Tuple, ast.List))
                    and len(tgt.elts) == len(value.elts)
                    and not any(isinstance(e, ast.Starred) for e in tgt.elts)):
                for t_elt, v_elt in zip(tgt.elts, value.elts):
                    if bind(_names_bound(t_elt), v_elt):
                        armed = True
            elif bind(_names_bound(tgt), value):
                armed = True
        return armed

    def visit_expr(node, armed):
        """Visit an expression subtree in source order for monotonic/arm/restore calls and walrus saves;
        returns the updated path armed state. Never descends into a nested function or lambda."""
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            return armed
        if isinstance(node, ast.NamedExpr):
            armed = visit_expr(node.value, armed)
            if bind(_names_bound(node.target), node.value):
                armed = True
            return armed
        if isinstance(node, ast.Call):
            for child in ast.iter_child_nodes(node):
                armed = visit_expr(child, armed)
            return handle_call(node, armed)
        for child in ast.iter_child_nodes(node):
            armed = visit_expr(child, armed)
        return armed

    def walk(stmts, armed):
        for node in stmts:
            armed = visit_stmt(node, armed)
        return armed

    def visit_stmt(node, armed):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            return armed                          # a nested scope; analysed on its own, never entered here
        if isinstance(node, ast.Assign):
            armed = visit_expr(node.value, armed)
            return bind_assign(node.targets, node.value, armed)
        if isinstance(node, ast.AnnAssign):
            if node.value is not None:
                armed = visit_expr(node.value, armed)
                if bind(_names_bound(node.target), node.value):
                    armed = True
            return armed
        if isinstance(node, ast.AugAssign):
            armed = visit_expr(node.value, armed)
            saved.difference_update(_names_bound(node.target))   # target OP value is a derivation
            return armed
        if isinstance(node, (ast.For, ast.AsyncFor)):
            armed = visit_expr(node.iter, armed)
            saved.difference_update(_names_bound(node.target))  # a loop target rebinds; verbatim status lost
            walk(node.body, armed)                # loop body isolated: an arm inside does not leak out
            walk(node.orelse, armed)
            return armed
        if isinstance(node, ast.While):
            armed = visit_expr(node.test, armed)
            walk(node.body, armed)                # loop body isolated
            walk(node.orelse, armed)
            return armed
        if isinstance(node, ast.If):
            armed = visit_expr(node.test, armed)  # a walrus save in the test runs before the branch
            walk(node.body, armed)                # branches isolated: an own-arm in one branch does not
            walk(node.orelse, armed)              # credit an identity restore in the sibling branch
            return armed                          # branch arms do not leak past the if (conservative)
        if isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                armed = visit_expr(item.context_expr, armed)
                if item.optional_vars is not None:
                    saved.difference_update(_names_bound(item.optional_vars))  # with-as rebinds the name
            return walk(node.body, armed)         # a with body always runs: a straightline continuation
        if isinstance(node, ast.Try):
            armed_before = armed
            armed = walk(node.body, armed)        # the try body runs straightline
            for handler in node.handlers:
                if handler.name:
                    saved.difference_update({handler.name})     # except-as rebinds the name
                walk(handler.body, armed_before)  # a handler runs on an exceptional path: pre-body armed
            armed = walk(node.orelse, armed)      # else runs after a clean body
            armed = walk(node.finalbody, armed)   # finally always runs: a straightline continuation
            return armed
        match_cls = getattr(ast, "Match", None)
        if match_cls is not None and isinstance(node, match_cls):
            armed = visit_expr(node.subject, armed)
            for case in node.cases:
                walk(case.body, armed)            # match cases are mutually exclusive: isolated
            return armed
        return visit_expr(node, armed)            # any other statement: visit its expressions in order

    walk(scope.body, False)

    if not (state["identity_restore"] or state["derived_restore"]):
        return None
    if state["identity_restore_armed"] and not state["monotonic"]:
        return "deny"
    if state["derived_restore"] and not state["identity_restore"] and state["monotonic"]:
        return None   # the correct elapsed-aware form: derived restore with elapsed evidence
    return "warn"


def _diagnose(rel, tree, resolver):
    """Return (deny, warn) diagnostic tuples for one parsed module."""
    deny, warn = [], []
    for scope in _scope_bodies(tree):
        bound = _scope_bound_names(scope)
        scope_resolver = resolver.scoped(bound)
        kind = _analyze_scope(scope, scope_resolver, bound)
        if kind is None:
            continue
        lineno = getattr(scope, "lineno", 1)
        name = getattr(scope, "name", "<module>")
        if kind == "deny":
            deny.append((rel, lineno, "a borrowed timer saved and re-armed verbatim in {!r} (own-arm plus "
                         "identity restore, no monotonic elapsed evidence); restore elapsed-aware".format(name)))
        else:
            warn.append((rel, lineno, "a timer save/restore in {!r} whose elapsed-aware handling this scan "
                         "cannot prove (own-arm, derivation, or pending/periodic preservation "
                         "unestablished); review against tmrrst".format(name)))
    return deny, warn


def _iter_declared_dir(root, rel, errors):
    """Yield (rel, abspath) for every regular *.py directly under root/rel (non-recursive). The directory
    is REQUIRED: an unreadable or absent one is a cannot-evaluate. A non-regular *.py entry (a symlink, a
    fifo) is a cannot-evaluate, never a silent skip."""
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
    """The declared scan set: tools/*.py (excluding tools/_vendor/), plus .aiqt/core/hooks/scripts/*.py."""
    out = []
    for r, p in _iter_declared_dir(root, TOOLS_REL, errors):
        out.append((r, p))
    # tools/_vendor is excluded by construction (the tools/ listing is non-recursive); nothing to do.
    for r, p in _iter_declared_dir(root, HOOKS_SCRIPTS_REL, errors):
        out.append((r, p))
    return sorted(out)


def scan(root, files):
    """Scan an explicit (rel, abspath) file list. Returns (errors, deny, warn) as sorted diagnostic tuples.
    A read, decode, or parse failure on a declared input is a cannot-evaluate (fail-closed)."""
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
                # never an uncaught error escaping as exit 1. Not portably self-testable (it needs an
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
                # escaping as exit 1 (SC1 third state). RecursionError/MemoryError below are a disjoint
                # capacity case, unaffected.
                errors.append((rel, 0, "declared input does not parse (malformed source): {}".format(exc)))
                continue
            except (RecursionError, MemoryError) as exc:
                errors.append((rel, 0, "declared input exceeds the analyzer's capacity (cannot evaluate): "
                               "{}".format(type(exc).__name__)))
                continue
            phase = "diagnose"
            try:
                resolver = _ModuleResolver(tree)
                d, w = _diagnose(rel, tree, resolver)
            except (RecursionError, MemoryError) as exc:
                # A syntactically valid but analysis-hostile input (e.g. a very deep AST) can drive the
                # recursive dataflow pass past the interpreter's recursion or memory limit. Fail closed as
                # a located cannot-evaluate (exit 2), the same fail-closed path as an unreadable/
                # unparseable input. RecursionError and MemoryError get this specific capacity message; any
                # OTHER uncaught error is caught by the per-file broad backstop below as a located
                # cannot-evaluate (surfaced, never masked), never an uncaught error escaping as exit 1.
                errors.append((rel, 0, "declared input exceeds the analyzer's capacity (cannot evaluate): "
                               "{}".format(type(exc).__name__)))
                continue
            deny.extend(d)
            warn.extend(w)
        except Exception as exc:  # noqa: BLE001  FINAL broad backstop; catch Exception, not BaseException
            # CLASS-WIDTH FAIL-CLOSED (SC1): any otherwise-uncaught error anywhere in this file's
            # read/decode/parse/diagnose pipeline is recorded as a LOCATED cannot-evaluate naming the file,
            # the phase, and the exception type+message, so a genuine bug is VISIBLE (never masked)
            # and drives exit 2, never an uncaught error escaping as exit 1. This broad catch replaces the
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
    """Scan the declared set and report. WARN-only v1: the certain verbatim-restore shape and the
    resist-static shapes are both emitted as WARN advisories for human review, and the gate NEVER blocks.
    A scanned input yields exit 0 (ran, with or without advisories) or exit 2 (cannot-evaluate on an
    unreadable, unparseable, or over-capacity input); it never exits 1 on a scanned input. The per-file
    pipeline fails closed to a located cannot-evaluate (see scan()) and advisory rendering is encoding-safe
    (see _emit). An output-stream failure (e.g. a full or broken stdout) is an environment/tool failure that
    may surface as a nonzero tool exit; it is not a scanned-input verdict."""
    try:
        errors = []
        files = _scan_set(root, errors)
        scan_errors, deny, warn = scan(root, files)
        errors = sorted(errors + scan_errors)
        for rel, lineno, msg in errors:
            where = "{}:{}".format(rel, lineno) if lineno else rel
            _emit("cannot-evaluate: {}: {}".format(where, msg))
        # WARN-only v1: the certain-shape findings (deny) and the resist-static findings (warn) are both
        # advisories on the same stream; neither sets a blocking exit. Sharper precision is a disclosed
        # follow-on.
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
    except Exception as exc:  # noqa: BLE001  final backstop: never let an uncaught error exit 1
        # An unrecoverable output or scan failure fails closed to a cannot-evaluate exit 2, never escapes as
        # exit 1. The stderr note is itself best-effort and encoding-safe.
        try:
            _safe_write(sys.stderr, "cannot-evaluate: advisory run failed ({}: {}); fail-closed\n".format(
                type(exc).__name__, exc))
        except Exception:  # noqa: BLE001  stderr itself unwritable; still fail closed, never exit 1
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
# Proves the gate against synthetic trees written under a private tempdir the test creates and removes
# (test-hermeticity), all fixtures using generic placeholders. WARN-only v1: every scan that ran exits 0;
# a "certain" case emits the certain-shape advisory, a
# "warn" case emits the resist-static advisory, a "clean" case emits no advisory, and a "clean-or-warn"
# case emits no certain-shape advisory (a resist-static advisory is acceptable):
#   each fixture in the `cases` table below is asserted against its own recorded kind, the cannot-evaluate
#   legs assert exit 2 with a located diagnostic naming the input, and the subprocess legs assert the child
#   never exits 1 and that run() emits a located WARN advisory. These assertions bind to the exact fixture
#   inputs defined below, not to any general per-shape guarantee; the detector source is the authoritative
#   account of what it flags.

_CERTAIN_SRC = '''\
import signal
def borrow():
    saved = signal.getitimer(signal.ITIMER_REAL)
    signal.setitimer(signal.ITIMER_REAL, 5.0)
    try:
        do_work()
    finally:
        signal.setitimer(signal.ITIMER_REAL, saved[0], saved[1])
'''

_PASS_SRC = '''\
import signal
import time
def borrow():
    saved_value, saved_interval = signal.getitimer(signal.ITIMER_REAL)
    t0 = time.monotonic()
    signal.setitimer(signal.ITIMER_REAL, 5.0)
    try:
        do_work()
    finally:
        rem = saved_value - (time.monotonic() - t0)
        signal.setitimer(signal.ITIMER_REAL, rem if rem > 0.0 else 1e-6, saved_interval)
'''

_WARN_SRC = '''\
import signal
def peek():
    saved = signal.getitimer(signal.ITIMER_REAL)
    read_only()
    signal.setitimer(signal.ITIMER_REAL, saved[0], saved[1])
'''

_ALIAS_CERTAIN_SRC = '''\
from signal import getitimer, setitimer, ITIMER_REAL
def borrow():
    saved = getitimer(ITIMER_REAL)
    setitimer(ITIMER_REAL, 3)
    try:
        do_work()
    finally:
        setitimer(ITIMER_REAL, saved[0], saved[1])
'''

_HANDLER_ONLY_SRC = '''\
import signal
def borrow():
    prev = signal.getsignal(signal.SIGALRM)
    signal.signal(signal.SIGALRM, _handler)
    try:
        do_work()
    finally:
        signal.signal(signal.SIGALRM, prev)
'''

# A verbatim restore reached through a one-hop copy of the saved name (M-1 / codex #1): the alias must
# propagate the saved value so the restore is still caught.
_COPY_CERTAIN_SRC = '''\
import signal
def borrow():
    saved = signal.getitimer(signal.ITIMER_REAL)
    signal.setitimer(signal.ITIMER_REAL, 5.0)
    alias = saved
    try:
        do_work()
    finally:
        signal.setitimer(signal.ITIMER_REAL, alias[0], alias[1])
'''

# An annotated-assignment save (codex #1): the save must be visible.
_ANN_CERTAIN_SRC = '''\
import signal
def borrow():
    saved: tuple = signal.getitimer(signal.ITIMER_REAL)
    signal.setitimer(signal.ITIMER_REAL, 5.0)
    try:
        do_work()
    finally:
        signal.setitimer(signal.ITIMER_REAL, saved[0], saved[1])
'''

# A walrus save (claude B-2): the save must be visible.
_WALRUS_CERTAIN_SRC = '''\
import signal
def borrow():
    if (saved := signal.getitimer(signal.ITIMER_REAL)):
        signal.setitimer(signal.ITIMER_REAL, 5.0)
        try:
            do_work()
        finally:
            signal.setitimer(signal.ITIMER_REAL, saved[0], saved[1])
'''

# A verbatim restore through a tuple-unpack alias of the saved value (M-1): must be caught.
_TUPLE_ALIAS_CERTAIN_SRC = '''\
import signal
def borrow():
    saved = signal.getitimer(signal.ITIMER_REAL)
    signal.setitimer(signal.ITIMER_REAL, 5.0)
    a, b = saved
    try:
        do_work()
    finally:
        signal.setitimer(signal.ITIMER_REAL, a, b)
'''

# The saved name is overwritten before the re-arm (codex #2): the argument is no longer the saved value, so
# this correct cancellation must NOT be a verbatim restore.
_OVERWRITE_CLEAN_SRC = '''\
import signal
def cancel_own_timer():
    saved = signal.alarm(5)
    saved = 0
    signal.alarm(saved)
'''

# The saved value is rebound through an elapsed-aware helper before the restore (claude B-1): elapsed-aware,
# not verbatim, so it must NOT emit a certain-shape advisory (exit 0, clean).
_HELPER_REBIND_CLEAN_SRC = '''\
import signal
def borrow():
    saved = signal.getitimer(signal.ITIMER_REAL)
    signal.setitimer(signal.ITIMER_REAL, 5.0)
    try:
        do_work()
    finally:
        saved = _adjust_elapsed(saved)
        signal.setitimer(signal.ITIMER_REAL, saved[0], saved[1])
'''

# A genuine verbatim restore where a single alarm(N) both arms and saves (codex #1 self-arm baseline): the
# combined save-and-own-arm is a certain-shape advisory (exit 0).
_ALARM_ARM_CERTAIN_SRC = '''\
import signal
def borrow():
    saved = signal.alarm(5)
    do_work()
    signal.alarm(saved)
'''

# The saved name is rebound by a FOR loop target before the re-arm (codex #1): the restore argument is the
# loop value, not the saved value, so this correct cancellation must NOT be a verbatim restore (exit 0).
_LOOP_REBIND_CLEAN_SRC = '''\
import signal
def cancel_own_timer():
    saved = signal.alarm(5)
    for saved in (0,):
        signal.alarm(saved)
'''

# The receiver `signal` is a PARAMETER shadowing the imported module (codex #2): the calls are methods on a
# caller-supplied object, not the timer API, so nothing is convicted (exit 0).
_SHADOWED_RECEIVER_CLEAN_SRC = '''\
import signal
def f(signal):
    saved = signal.alarm(0)
    signal.alarm(5)
    signal.alarm(saved)
'''

# The own nonzero arm PRECEDES the save (codex #1 ordering case): the arm does not sit in the ordered
# save -> arm -> restore window, so it is not a certain verbatim re-arm and does not emit a certain-shape advisory (exit 0; a resist-static WARN is
# acceptable).
_ARM_BEFORE_SAVE_SRC = '''\
import signal
def f():
    signal.alarm(5)
    saved = signal.alarm(0)
    signal.alarm(saved)
'''

# CATCH-ALL 1 (codex #1): the restore callable `float` is a PARAMETER shadowing the builtin cast, so it
# may adjust the elapsed value and does not establish a verbatim identity flow; the restore is derived,
# not identity, so no certain-shape advisory fires (exit 0; a resist-static WARN is acceptable).
_SHADOWED_CAST_CLEAN_SRC = '''\
import signal
def borrow(float):
    saved = signal.getitimer(signal.ITIMER_REAL)
    signal.setitimer(signal.ITIMER_REAL, 5)
    try:
        do_work()
    finally:
        signal.setitimer(signal.ITIMER_REAL, float(saved[0]), saved[1])
'''

# A genuine verbatim restore through the REAL builtin `int` cast (not shadowed): the identity flow must
# still be followed and flagged as a certain-shape advisory (exit 0), so the shadow guard does not
# over-narrow the real cast.
_REAL_CAST_CERTAIN_SRC = '''\
import signal
def borrow():
    saved = signal.alarm(0)
    signal.alarm(5)
    signal.alarm(int(saved))
'''

# CATCH-ALL 2 (codex #2): a direct save inside a TUPLE assignment (`saved, other = signal.alarm(0), 0`),
# then a constant own-arm, then a verbatim restore, is a certain verbatim re-arm and emits a certain-shape
# advisory (exit 0).
_TUPLE_SAVE_CERTAIN_SRC = '''\
import signal
def borrow():
    saved, other = signal.alarm(0), 0
    signal.alarm(5)
    signal.alarm(saved)
'''

# CATCH-ALL 3 (codex additional): the own-arm and the identity restore sit in MUTUALLY EXCLUSIVE branches
# (arm in the `if` body, restore in the `else`), so no single execution path runs save -> arm -> restore;
# this is not the certain shape and must NOT emit a certain-shape advisory (exit 0; a resist-static WARN is acceptable).
_CROSS_BRANCH_CLEAN_SRC = '''\
import signal
def inspect(borrow):
    saved = signal.alarm(0)
    if borrow:
        signal.alarm(5)
    else:
        signal.alarm(saved)
'''


# R9 (round-9 QA, inline-container restore): the saved name remains syntactically present through an inline
# container (`[saved][0]`) at the re-arm, so this is not the modelled certain identity flow, yet a
# save-plus-restore scope is visible; the r9-inline-container fixture asserts a resist-static WARN advisory (exit 0) for this input.
_INLINE_CONTAINER_WARN_SRC = '''\
import signal
def borrow():
    saved = signal.alarm(0)
    signal.alarm(5)
    signal.alarm([saved][0])
'''


# A certain-shape (verbatim re-arm) whose SCOPE NAME carries a non-ASCII character (a valid Python
# identifier), so its advisory message embeds a non-ASCII name and exercises the encoding-safe emitter under
# an ASCII stdout. Written with a \u escape so this test source stays ASCII-only; the fixture file on disk
# carries the real character (utf-8).
_NONASCII_ADVISORY_SRC = '''\
import signal
def r\u00e9arm():
    saved = signal.getitimer(signal.ITIMER_REAL)
    signal.setitimer(signal.ITIMER_REAL, 5.0)
    try:
        do_work()
    finally:
        signal.setitimer(signal.ITIMER_REAL, saved[0], saved[1])
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
        tmp = Path(tempfile.mkdtemp(prefix="aiqt-timer-restore-selftest-"))
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
            ("deny", _CERTAIN_SRC, "certain"),
            ("pass", _PASS_SRC, "clean"),
            ("warn", _WARN_SRC, "warn"),
            ("alias-deny", _ALIAS_CERTAIN_SRC, "certain"),
            ("handler-only", _HANDLER_ONLY_SRC, "clean"),
            ("copy-deny", _COPY_CERTAIN_SRC, "certain"),
            ("ann-deny", _ANN_CERTAIN_SRC, "certain"),
            ("walrus-deny", _WALRUS_CERTAIN_SRC, "certain"),
            ("tuple-alias-deny", _TUPLE_ALIAS_CERTAIN_SRC, "certain"),
            ("overwrite-clean", _OVERWRITE_CLEAN_SRC, "clean"),
            ("helper-rebind-clean", _HELPER_REBIND_CLEAN_SRC, "clean"),
            ("alarm-arm-deny", _ALARM_ARM_CERTAIN_SRC, "certain"),
            ("loop-rebind-clean", _LOOP_REBIND_CLEAN_SRC, "clean"),
            ("shadowed-receiver-clean", _SHADOWED_RECEIVER_CLEAN_SRC, "clean"),
            ("arm-before-save-clean", _ARM_BEFORE_SAVE_SRC, "clean-or-warn"),
            ("shadowed-cast-clean", _SHADOWED_CAST_CLEAN_SRC, "clean-or-warn"),
            ("real-cast-deny", _REAL_CAST_CERTAIN_SRC, "certain"),
            ("tuple-save-deny", _TUPLE_SAVE_CERTAIN_SRC, "certain"),
            ("cross-branch-clean", _CROSS_BRANCH_CLEAN_SRC, "clean-or-warn"),
            ("r9-inline-container-restore", _INLINE_CONTAINER_WARN_SRC, "warn"),
        ]
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

        # 4a. an unparseable declared input is a located cannot-evaluate (exit 2, fail-closed), never an
        # uncaught error escaping as exit 1. This leg asserts exit 2 AND the located "does not parse
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

        # 4b. an unreadable declared input is a located cannot-evaluate (exit 2, fail-closed), never an
        # uncaught error escaping as exit 1. This leg asserts exit 2 AND the located "cannot read declared
        # input" diagnostic, not just the exit code. Skipped if chmod-0 stays readable (root).
        rel, p = write(base, "tools/case_unread.py", "import signal\n")
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

        # 4c. a missing declared directory is a located cannot-evaluate via _scan_set. This leg asserts the
        # located "cannot list required directory" diagnostic, not merely a non-empty error list.
        empty = tmp / "empty"
        empty.mkdir()
        errs = []
        _scan_set(empty, errs)
        if not any("cannot list required directory" in msg for _, _, msg in errs):
            failures.append("missing-dirs: expected the located cannot-list cannot-evaluate diagnostic, got {}"
                            .format([msg for _, _, msg in errs]))

        # 4d. a declared input that is valid UTF-8 and PARSES but whose AST is so deep the recursive
        # dataflow pass exceeds the interpreter's recursion limit is a cannot-evaluate (exit 2, fail-closed),
        # never an uncaught RecursionError escaping as exit 1. This leg asserts exit 2 AND the located
        # capacity ("exceeds the analyzer's capacity (cannot evaluate)") diagnostic, not just the exit code.
        deep_src = "x = a" + ".b" * 20000 + "\n"
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

        # 4e. a NUL-byte source is a malformed declared input: ast.parse raises SyntaxError on CPython >=
        # 3.12 and ValueError on < 3.12, and either way it is a located cannot-evaluate (exit 2, fail-closed),
        # never an uncaught error escaping as exit 1. Written as raw bytes so the NUL survives. This leg
        # asserts exit 2 AND the located "does not parse" diagnostic, not just the exit code. Call scan()
        # directly to inspect the located diagnostic; run_quiet_files discards the messages this leg asserts on.
        rel = "tools/case_nul.py"
        p = base / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"import signal\nx = 0\x00\n")
        with redirect_stdout(io.StringIO()):
            nul_errors, _, _ = scan(base, [(rel, p)])
        nul_code = 2 if nul_errors else 0
        if nul_code != 2:
            failures.append("nul-byte: expected exit 2 (cannot-evaluate), got {}".format(nul_code))
        if not any("does not parse" in msg for _, _, msg in nul_errors):
            failures.append("nul-byte: expected the located parse cannot-evaluate diagnostic, got {}"
                            .format([msg for _, _, msg in nul_errors]))

        # 4f. SUBPROCESS regression legs: run the gate as a CHILD PROCESS over a fixture repo root,
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

        # deep-AST fixture -> cannot-evaluate (exit 2), never exit 1.
        rc = child_exit(make_child_root("child_deep", "case_deep.py", text=deep_src))
        if rc == 1:
            failures.append("deep-ast (subprocess): child exited 1 (uncaught error escaped)")
        elif rc != 2:
            failures.append("deep-ast (subprocess): expected exit 2, got {}".format(rc))

        # null-byte fixture -> cannot-evaluate (exit 2), never exit 1.
        rc = child_exit(make_child_root("child_nul", "case_nul.py", raw=b"import signal\nx = 0\x00\n"))
        if rc == 1:
            failures.append("nul-byte (subprocess): child exited 1 (uncaught error escaped)")
        elif rc != 2:
            failures.append("nul-byte (subprocess): expected exit 2, got {}".format(rc))

        # ASCII-locale advisory rendering: a fixture whose advisory carries a NON-ASCII scope name, rendered
        # under an ASCII stdout. Encoding-safe rendering keeps the advisory emitted and the exit 0 (never 1);
        # only an unrecoverable output failure routes to exit 2.
        rc = child_exit(make_child_root("child_ascii", "case_nonascii.py", text=_NONASCII_ADVISORY_SRC),
                        extra_env={"LC_ALL": "C", "PYTHONIOENCODING": "ascii"}, ascii_stdout=True)
        if rc == 1:
            failures.append("ascii-locale (subprocess): child exited 1 (advisory rendering not encoding-safe)")
        elif rc not in (0, 2):
            failures.append("ascii-locale (subprocess): expected exit 0 or 2, got {}".format(rc))

        # 4g. RUN-PATH ADVISORY-EMISSION leg: exercise the REAL run() entry point in an isolated child over a
        # /dev/shm fixture root (TMPDIR routes tmp there) and assert BOTH the child exits 0 AND a located
        # "WARN:" advisory is actually EMITTED on stdout, so a regression in the reporting/exit-code path
        # cannot pass unseen. The r9-inline-container leg above exercises scan() alone (not run() or its
        # rendered output); this companion drives run() end to end over the same inline-container fixture.
        def child_run_out(fixture_root):
            boot = ("import sys\n"
                    + "sys.path.insert(0, {!r})\n".format(this_dir)
                    + "from pathlib import Path\n"
                    + "import {} as _m\n".format(module_name)
                    + "sys.exit(_m.run(Path({!r})))\n".format(str(fixture_root)))
            proc = subprocess.run([sys.executable, "-I", "-B", "-c", boot],
                                  capture_output=True, env=dict(os.environ))
            return proc.returncode, proc.stdout.decode("utf-8", "replace")

        rc, out = child_run_out(make_child_root("child_warn_inline", "case_inline_container.py",
                                                text=_INLINE_CONTAINER_WARN_SRC))
        if rc != 0:
            failures.append("run-emit inline-container (subprocess): expected exit 0, got {}".format(rc))
        expected_rel = "tools/case_inline_container.py"
        if not _located_warn(out, expected_rel):
            failures.append("run-emit inline-container (subprocess): expected a LOCATED WARN advisory "
                            "(WARN: {}:<line>: <diagnostic>) with a positive line number and nonempty "
                            "diagnostic text, got {!r}".format(expected_rel, out))
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
          "that run() emits a located WARN advisory; these are the fixture-bound invariants the suite "
          "exercises, not a general guarantee of the detector's behaviour{}"
          .format(tail))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
