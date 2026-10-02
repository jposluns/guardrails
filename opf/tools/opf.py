#!/usr/bin/env python3
"""opf: the OPFiles (OPF) reference-tooling dispatcher (OPF core-tooling, skeleton from U1).

  opf.py --self-test                run every registered OPF helper self-test (Linux CI leg)
  opf.py <verb> [--root DIR] ...    a store verb (default --root: the cwd product repository root)

This is the dispatcher the OPF core-tooling units grow into. U1 lands it with the store-side helper
self-test wired in; store verbs that have not yet landed are recognized names that report
NOT-YET-IMPLEMENTED and fail closed (exit 2) until their unit lands, so a stub can never read as a
passing operation. `render` HAS landed (PR-A): the `opf render` CLI requires exactly one of
`--check | --write` (a bare `render` is a usage error, exit 2); `--check` is the read-only drift check
(forwarding to the U4 engine) and `--write` (VC-4/PR-C) is the mutating half: it gathers the inert git-derived
observations caller-side (_opf_observe.gather) and hands them to the U4 engine, which composes the EXISTING U6
`validate_store` store-integrity gate and permits writing when source integrity holds. Post-write validation
checks source integrity and regenerated outputs; other deliverable failures can remain. `doctor` HAS landed: `opf doctor
[--root DIR] [--require-store]` RESOLVES the store, gathers the inert git-derived observations
(_opf_observe.gather: tracked, actual_remote, prior), and runs the U6 `validate_store` store-integrity engine
over them, returning that engine's 0/1/2 contract (a NOT-ADOPTED root reports NOT APPLICABLE and exits 0;
with `--require-store`, the enforcement-pack CI floor, it is a cannot-evaluate and exits 2 instead, so a
repository whose store was removed cannot pass CI vacuously). Doctor is read-only; its
observation gather is the caller-side git seam validate_store itself never touches. `upgrade` HAS landed
(spec 9.2): `opf upgrade [--root DIR]` is the in-place, additive, idempotent
upgrade from 1.0.0 or 1.1.0 to 1.2.0. The 1.1.0 schema delta changes only spec_version; declared views
are then regenerated, so a stale committed view can change. The 1.0.0 path also applies the earlier
schema delta
(base-table and discovery-token rename, decision_support retirement, type and view declarations,
DECISIONS.md source widening, counters, and missing indexes). Neither creates init.toml provenance.
It refuses a store above the tooling spec. When migrating a 1.0.0 or 1.1.0 store, its preconditions
include readable, canonical manifest/counters matching a recognised origin shape, cleanliness over
planned schema/render destinations and index collision candidates (including ignored files there),
and acquisition of its lease. An untracked or ignored lease is excepted from the cleanliness check
and handled separately by lease acquisition. It then applies the schema delta,
renders declared views, and requires a full doctor VALID before offering the uncommitted change for
review and merge. A store already at the
tooling spec_version is a byte no-op when doctor-VALID and exits 2 otherwise; a NOT-ADOPTED root reports
NOT APPLICABLE and exits 0. `import`'s former
modes, `opf import [--root DIR] (--scan --set FILE | --plan --set FILE | --review <run-id> --actor NAME
(--decisions FILE | --interactive) | --apply <run-id>)`, are RETIRED (spec 14.1) and their engine is
removed: the verb refuses every argument list on every root, a NOT-ADOPTED one included, at exit 2 with a
pointer to adoption and the prompt pack, parsing no argument, reading no input file and writing nothing.

`init` HAS landed: `opf init [--root DIR]` creates validated store sources, a pointer, and a starter
`CHANGELOG.md` when none exists, without git writes or rendering.
`absorb` HAS landed: `opf absorb [--root DIR] [--covers TOKEN] [--freeze-digest]`
prints a changelog draft or freeze digest without writing files.
`record` HAS landed (spec 8.8): `opf record create`, `transition`, `done-with-receipt`, and `worklog-append`
author one change (with its own worklog entry, and for done-with-receipt the one-to-one done receipt)
through one journaled publication, then render and require doctor VALID, leaving the change uncommitted.
The one exception to doctor VALID is a status change (transition or done-with-receipt): doctor may then
report only its cannot-evaluate for exactly that record and from/to pair, never a finding, and it keeps
reporting that cannot-evaluate until the change is committed.
`adopt` HAS landed (OPF-ADOPT K9a, the read-only half): `opf adopt plan --inputs FILE [--root DIR]`
freezes and PRINTS the inert adoption proposal through the adoption planner (_opf_adopt_plan), writing
nothing -- a VALID plan is a digest-bound PROPOSAL, never permission or readiness to apply (the approval
lives in the run's evidence, a later PR) -- and `opf adopt status [--root DIR]` reports the adoption
state read-only (the adoption evidence bundles and the adoption journal; with neither present it reports
that no adoption run exists). The mutating subcommands `approve`, `apply`, `complete`, and `reconcile`
are recognized and refuse (exit 2) until the mutating adoption engine lands in a later PR.

Adopter-rooted, like doctor.py/migrate.py/conformance.py: an OPF verb operates on a PRODUCT repository
root named by --root (default: the cwd), never on this pack's own tree via `_gen_common.repo_root()`.
The pack is a readable non-adopter root, so a live `opf.py render --root . --check` here reports NOT
APPLICABLE; the assurance rides the `--self-test` leg over synthetic stores (spec-honest, mirroring the
crosswalk/doctor/migrate legs in run_all_checks.sh).

Deliberately NOT named tools/gen_*.py: the generated-source registry (gen_gensrc.py) discovers gen_*.py
and validates fixed repo-relative targets, but OPF renders into an adopter --root with no fixed
repo-relative target, so this family gates as self-tests instead (the U1 build-plan section 5 rule).

Launched isolated (-I -B) per the Python-launcher-isolation gate; sibling helpers are imported through
the sys.path insert idiom the repo's tools share.
"""
import json
import os
import stat
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # for the guarded _opf_* helper bootstrap below

EXIT_OK = 0
EXIT_FINDING = 1
EXIT_MALFORMED = 2


def _bootstrap():
    """Import the non-stdlib _opf_* helper modules the dispatch and self-test legs use, binding each to a
    module global. Called FIRST in main() so a broken or PARTIAL install -- a helper that cannot be imported
    (ImportError) or read (OSError) -- maps to a located cannot-evaluate (EXIT_MALFORMED / 2), never an
    uncaught ImportError that Python would surface as its default exit 1 and that a direct `opf render
    --check` would then read as a false DRIFT (the exit-1=drift conflation, reached here BEFORE the render
    dispatcher's own fail-closed handler). Only ImportError and OSError (the broken/partial-install signals)
    are caught; a broader error propagates rather than being masked as bootstrap. The imports moved OFF module
    top for exactly this reason -- an eager top-level import failed before main()'s contract could apply.
    Idempotent: a re-import of an already-loaded module is a cheap no-op, so main() may call it on every
    invocation. Returns EXIT_OK on success, or EXIT_MALFORMED with a located diagnostic naming the helper
    that could not be brought in."""
    global _opf_store, _opf_schema, _opf_release, _opf_changelog, _opf_check
    global _opf_emit, _opf_views, _opf_fuzz, _opf_observe, _opf_absorb
    global _opf_worklog, _opf_write_guard, _opf_record, _opf_adopt_apply, _opf_adopt_plan
    try:
        import _opf_worklog     # manifest-selected worklog intake + WL reference grammar
        import _opf_store       # U1: store resolution + discovery + manifest base/profile schema
        import _opf_schema      # U2: record envelope + baseline type schemas + status/transition + counters
        import _opf_release     # U3: version.toml + worklog.toml + span tiling + coverage digests + release cut
        import _opf_changelog   # U5: changelog range-coverage + freeze gates over version.toml + CHANGELOG.md
        import _opf_check       # U6: store-level integrity validator (validate_store; engine for opf doctor)
        import _opf_emit        # U8: the constrained-subset TOML emitter (canonical, byte-canon-clean)
        import _opf_views       # U4: deterministic view generators + the closed transform vocabulary
        import _opf_fuzz        # adversarial input-hardening proof (membership/type-guard class closure)
        import _opf_observe     # PR-B: caller-side git-derived observations for the doctor verb (validate_store)
        import _opf_absorb      # OPF-CHANGELOG-ABSORB: read-only CHANGELOG.md drafter (composes on U5)
        import _opf_write_guard  # the in-place writers' shared cleanliness gate and single-writer lease
        import _opf_record      # OPF-RECORD: the record-authoring verb (spec 8.8)
        import _opf_adopt_apply  # OPF-ADOPT U1: the apply shell (zero executable ops)
        import _opf_adopt_plan   # OPF-ADOPT K9a: read-only investigation + plan freeze (the adopt planner)
    except ImportError as exc:
        print("opf: cannot bootstrap: {} (cannot evaluate)".format(exc.name or exc), file=sys.stderr)
        return EXIT_MALFORMED
    except OSError as exc:
        print("opf: cannot bootstrap: a helper module could not be read ({!r}) (cannot evaluate)".format(
            exc), file=sys.stderr)
        return EXIT_MALFORMED
    return EXIT_OK

# F-SELFTEST-NO-MAIN: the one entry a module that binds self_test must carry, matched by AST structure (so
# spacing, quoting, parentheses and comments do not matter). _ENTRY_TEST is the only accepted test of the entry
# block (that operand order only); _ENTRY_STATEMENT must be that block's first statement, with nothing else in
# its body and no else. Any argument handling a module has beyond exactly `--self-test` follows it.
_ENTRY_TEST = '__name__ == "__main__"'
_ENTRY_STATEMENT = 'if sys.argv[1:] == ["--self-test"]:\n    sys.exit(self_test())\n'


def _module_scope(tree, classes=False):
    """Yield every node of a parsed module that runs in the module's own scope. Function, lambda and class
    bodies (their own scopes) are skipped; decorators, defaults, annotations, bases and comprehension
    iterables, which the module scope evaluates, are kept. A comprehension or generator expression is walked
    as a for statement is, except that a plain name its targets bind (a tuple, list or starred one included)
    is the comprehension's own and is skipped: an attribute or subscript target (`sys.exit`, `sys.argv[1:]`)
    is kept, as are its element and conditions (a walrus there binds the enclosing scope). With `classes`,
    class bodies are walked too: a class body runs when its class statement does (one inside a function is
    still skipped)."""
    import ast
    stack = [tree]
    while stack:
        node = stack.pop()
        yield node
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            stack += node.decorator_list + [node.args] + ([node.returns] if node.returns else [])
        elif isinstance(node, ast.ClassDef):
            stack += node.decorator_list + node.bases + node.keywords + (node.body if classes else [])
        elif isinstance(node, ast.Lambda):
            stack.append(node.args)
        elif isinstance(node, ast.comprehension):
            stack += [node.iter] + node.ifs
            targets = [node.target]
            while targets:
                target = targets.pop()
                if isinstance(target, (ast.Tuple, ast.List)):
                    targets += target.elts
                elif isinstance(target, ast.Starred):
                    targets.append(target.value)
                elif not isinstance(target, ast.Name):
                    stack.append(target)
        else:
            stack += ast.iter_child_nodes(node)


def _binds(node, name):
    """Return how many times one module-scope node binds `name` (0 when it does not): once for a def, async
    def or class so named, for each Name stored or deleted (an assignment, augmented, annotated, for, with,
    walrus or del target, tuple targets included, or a type-alias name), for each alias of an import or
    from-import that binds it (`import a as self_test, b as self_test` is two; a star import counts, as it
    may), and for an except, match or match-rest name. A `global` binds nothing by itself; _self_test_entry_gap
    counts each `name` a `global` names, found anywhere with ast.walk."""
    import ast
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return int(node.name == name)
    if isinstance(node, ast.Name):
        return int(node.id == name and isinstance(node.ctx, (ast.Store, ast.Del)))
    if isinstance(node, ast.Import):
        return sum((alias.asname or alias.name.partition(".")[0]) == name for alias in node.names)
    if isinstance(node, ast.ImportFrom):
        return sum((alias.asname or alias.name) in (name, "*") for alias in node.names)
    if isinstance(node, (ast.ExceptHandler, ast.MatchAs, ast.MatchStar)):
        return int(node.name == name)
    if isinstance(node, ast.MatchMapping):
        return int(node.rest == name)
    return 0


# The only methods of `sys.argv` a statement before the entry may call: each reads the list and changes
# nothing. A call of ANY other attribute of it there (`sys.argv.<name>(...)`, a dunder such as __init__ or
# __setitem__ included) is an argv change, whatever the method does. This is the read-only set; no list of
# the methods that mutate is kept.
_ARGV_READERS = frozenset(("count", "index", "copy", "__len__", "__getitem__", "__contains__", "__iter__"))


def _main_tests(tree):
    """Return, in line order, a (line, node) pair for every test of `__name__` against "__main__" that runs at
    import: each if, while, conditional expression, and/or operation, comprehension condition or match at
    module scope or in a class body (_module_scope with classes) whose test (an and/or's operands, a
    comprehension's conditions, a match's subject and cases) mentions both the name `__name__` and the string
    "__main__" anywhere inside it, however they are combined. A test inside another counted test is not
    counted again."""
    import ast

    def mentions(parts):
        nodes = [sub for part in parts for sub in ast.walk(part)]
        return (any(isinstance(sub, ast.Name) and sub.id == "__name__" for sub in nodes)
                and any(isinstance(sub, ast.Constant) and type(sub.value) is str and sub.value == "__main__"
                        for sub in nodes))

    tests, inside = [], set()
    for node in _module_scope(tree, classes=True):
        if id(node) in inside:
            continue
        if isinstance(node, (ast.If, ast.While, ast.IfExp)):
            parts = [node.test]
        elif isinstance(node, ast.BoolOp):
            parts = node.values
        elif isinstance(node, ast.comprehension):
            parts = node.ifs
        elif isinstance(node, ast.Match):
            parts = [node.subject] + [part for case in node.cases for part in (case.pattern, case.guard) if part]
        else:
            continue
        if mentions(parts):
            tests.append((parts[0].lineno if isinstance(node, ast.comprehension) else node.lineno, node))
            inside.update(id(sub) for part in parts for sub in ast.walk(part))
    return sorted(tests, key=lambda test: test[0])


def _stores(statements):
    """Return (node, name, attribute) for each store to or delete of an attribute or subscript in `statements`
    (top-level ones), at module scope or in a class body (an assignment, augmented or annotated assignment,
    del, or for, with or comprehension target). Following the target's attributes, subscripts and calls
    inward, `name` is the name it starts from, or None when it starts from any other expression (a list,
    tuple, conditional expression or walrus, say), and `attribute` the first attribute taken of where it
    starts, or None when there is none: `sys.modules[__name__].self_test` is through sys and modules,
    `__builtins__["len"]` is through __builtins__ and None, and `[sys][0].exit` is through None and exit."""
    import ast
    found = []
    for statement in statements:
        for node in _module_scope(statement, classes=True):
            if not (isinstance(node, (ast.Attribute, ast.Subscript)) and isinstance(node.ctx, (ast.Store, ast.Del))):
                continue
            part, attribute = node, None
            while isinstance(part, (ast.Attribute, ast.Subscript, ast.Call)):
                if isinstance(part, ast.Attribute):
                    attribute = part.attr
                part = part.func if isinstance(part, ast.Call) else part.value
            found.append((node, part.id if isinstance(part, ast.Name) else None, attribute))
    return found


# The attributes of sys, besides argv, that a store or delete through (_stores) before the entry may not be made
# through: modules holds every loaded module (the running one included), and __dict__ is sys's own namespace.
_SYS_THROUGH = ("modules", "__dict__")


def _sys_change(statements):
    """Return (node, attribute, through) for the first node, by position, in `statements` (top-level ones), at
    module scope or in a class body, that changes the sys module, or a value reached through it, through the
    name sys: a store to, delete of or augmented assignment to any attribute of it (`sys.<attribute>`, an
    assignment, augmented or annotated assignment, del, or for, with or comprehension target), any such store
    or delete through argv or one of _SYS_THROUGH (_stores: `sys.argv[1:]`, `sys.modules[__name__].self_test`,
    `sys.__dict__["exit"]`), or a call of any attribute of `sys.argv` outside _ARGV_READERS. `attribute` is
    the attribute of sys the change is made to or through, and `through` is True for a store or delete through
    one of _SYS_THROUGH (a change to argv, or through it, is argv's). A store through any other attribute
    (`sys.path[:] = saved`) is not counted. None when there is none."""
    import ast

    def is_argv(node):
        return (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "sys"
                and node.attr == "argv")

    found = [(node, attribute, attribute in _SYS_THROUGH and not direct)
             for node, name, attribute in _stores(statements) if name == "sys" and attribute is not None
             for direct in [isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)]
             if direct or attribute == "argv" or attribute in _SYS_THROUGH]
    found += [(node, "argv", False) for statement in statements for node in _module_scope(statement, classes=True)
              if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
              and node.func.attr not in _ARGV_READERS and is_argv(node.func.value)]
    return min(found, key=lambda change: (change[0].lineno, change[0].col_offset), default=None)


# The names a store or delete through (_stores) before the entry may not be spelled from: builtins and
# __builtins__ hold the builtins the module's code reads, and __main__ is the running module's own namespace.
_NAMESPACE_ROOTS = ("builtins", "__builtins__", "__main__")


def _namespace_store(statements):
    """Return (node, name) for the first store or delete, by position, in `statements` (top-level ones), at
    module scope or in a class body, through one of _NAMESPACE_ROOTS (_stores: `builtins.len = None`,
    `__builtins__["len"] = None`, `__main__.self_test = int`), or None when there is none."""
    found = [(node, name) for node, name, _attribute in _stores(statements) if name in _NAMESPACE_ROOTS]
    return min(found, key=lambda store: (store[0].lineno, store[0].col_offset), default=None)


def _self_test_or_expression_store(statements):
    """Return (node, name) for the first store or delete, by position, in `statements` (top-level ones), at
    module scope or in a class body, whose target (_stores) starts from the name self_test
    (`self_test.__code__ = ...`, `self_test.__new__ = ...`; `name` is "self_test") or from an expression rather
    than a name (`[sys][0].exit = print`, `(s := sys).exit = print`; `name` is None), or None when there is
    none. The rule is conservative: such a store is counted whatever it reaches."""
    found = [(node, name) for node, name, _attribute in _stores(statements) if name in (None, "self_test")]
    return min(found, key=lambda store: (store[0].lineno, store[0].col_offset), default=None)


def _unconditional_binding(statements):
    """Return the last of `statements` (top-level ones) that binds self_test UNCONDITIONALLY, or None: a def,
    async def or class named self_test, an import or from-import binding it (a star import counts), or an
    assignment, or annotated assignment with a value, storing it. A top-level try's body runs, so its statements
    count (recursively); a binding only inside if, for, while, with, match, an except handler, a try's else or
    finally, or a function does not."""
    import ast
    found = None
    for node in statements:
        if isinstance(node, (ast.Try, getattr(ast, "TryStar", ast.Try))):
            found = _unconditional_binding(node.body) or found
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Import, ast.ImportFrom)):
            found = node if _binds(node, "self_test") else found
        elif isinstance(node, ast.Assign):
            found = node if any(_binds(part, "self_test") for target in node.targets
                                for part in ast.walk(target)) else found
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            found = node if _binds(node.target, "self_test") else found
    return found


def _self_test_entry_gap(tree):
    """Return (exposes, reason) for one parsed module. `exposes` is whether it binds self_test at module scope
    by any form _binds counts, or by a `global self_test` anywhere (a function can bind it so). An exposing
    module's `reason` is None only when exactly one test of `__name__` against "__main__" runs at import
    (_main_tests: an if, while, conditional expression, and/or, comprehension condition or match, at module
    scope or in a class body, whose test mentions the name `__name__` and the string "__main__"), that test is
    an `if` that is the module's last top-level statement, tests exactly _ENTRY_TEST and has no else, the
    module imports sys at top level, the block's first statement is _ENTRY_STATEMENT, the block binds
    self_test nowhere, the module has exactly one binding of self_test, counted by name occurrence (each
    binding _binds counts in a module-scope node, so every alias of one import, and each self_test a `global`
    anywhere names, is one), a statement before the block makes that binding unconditionally
    (_unconditional_binding), when it is a def it is neither an async def nor one whose own body (not a nested
    function, lambda or class) yields, and that own body has at least one `return <expression>` and no bare
    `return` or `return None`, the module has no star import at module scope (one may bind `__name__`, sys or
    self_test), it binds `__name__` nowhere at module scope (counted the same way, the block included), every
    module-scope binding of sys (and each sys a `global` anywhere names) is a plain `import sys`, it binds
    `__builtins__` nowhere at module scope (counted as `__name__` is), no statement before the block changes
    sys through the name sys (_sys_change: a store to, delete of or augmented assignment to any
    `sys.<attribute>`, a store or delete through `sys.modules` or `sys.__dict__`, or an argv change), none
    stores to or deletes through `builtins`, `__builtins__` or `__main__` (_namespace_store), and none stores
    to or deletes through the name self_test or through a target that starts from an expression rather than
    a name (_self_test_or_expression_store); otherwise it names the first rule broken."""
    import ast
    import _optlevel

    def bindings_of(name, imported=False):
        # The line of each binding of `name` _binds counts at module scope and of each `global` naming it;
        # with `imported`, an alias of a plain `import <name>` (no `as`) is not counted.
        plain = lambda node: sum(alias.name == name and alias.asname is None for alias in node.names) if (
            imported and isinstance(node, ast.Import)) else 0
        return [node.lineno for node in _module_scope(tree) for _ in range(_binds(node, name) - plain(node))] + [
            node.lineno for node in ast.walk(tree) if isinstance(node, ast.Global) for each in node.names
            if each == name]

    bindings = bindings_of("self_test")
    if not bindings:
        return False, None
    tests = _main_tests(tree)
    if not tests:
        return True, 'it has no `if __name__ == "__main__":` block'
    if len(tests) > 1:
        return True, "{} tests of `__name__` against \"__main__\" run at import (lines {}); exactly one is " \
                     "allowed".format(len(tests), ", ".join(str(line) for line, _test in tests))
    line, block = tests[0]
    if not any(node is block for node in tree.body):
        return True, "its `__main__` test (line {}) is nested in another statement".format(line)
    if not isinstance(block, ast.If):
        return True, "its `__main__` test (line {}) is a `{}` statement, not an `if`".format(
            line, type(block).__name__.lower())
    if ast.dump(block.test) != ast.dump(_optlevel.parse(_ENTRY_TEST, mode="eval").body):
        return True, "its `__main__` block (line {}) does not test exactly `{}`".format(block.lineno, _ENTRY_TEST)
    if block.orelse:
        return True, "its `__main__` block (line {}) has an else".format(block.lineno)
    if tree.body[-1] is not block:
        return True, "a top-level statement follows its `__main__` block (line {})".format(block.lineno)
    if not any(isinstance(node, ast.Import) and any(alias.name == "sys" and alias.asname is None
                                                    for alias in node.names) for node in tree.body):
        return True, "it does not `import sys` at top level"
    entry = ast.dump(_optlevel.parse(_ENTRY_STATEMENT).body[0])
    if ast.dump(block.body[0]) != entry:
        if any(ast.dump(statement) == entry for statement in block.body[1:]):
            return True, "the canonical `--self-test` statement is not the first in its `__main__` block"
        return True, "its `__main__` block starts with `{}`, not the canonical `--self-test` statement".format(
            ast.unparse(block.body[0]).splitlines()[0][:60])
    if any(_binds(node, "self_test") for node in _module_scope(block)):
        return True, "its `__main__` block (line {}) binds self_test".format(block.lineno)
    if len(bindings) > 1:
        return True, "self_test has {} bindings at module scope (lines {}); exactly one binding is allowed".format(
            len(bindings), ", ".join(str(lineno) for lineno in sorted(bindings)))
    binding = _unconditional_binding(tree.body[:-1])
    if binding is None:
        return True, "self_test is bound only conditionally (inside a compound statement other than a try " \
                     "body, or by a function's `global`), never unconditionally before its `__main__` block"
    if isinstance(binding, ast.AsyncFunctionDef):
        return True, "its self_test (line {}) is an `async def`: the call returns a coroutine, so the " \
                     "suite never runs".format(binding.lineno)
    if isinstance(binding, ast.FunctionDef) and any(isinstance(node, (ast.Yield, ast.YieldFrom)) for statement
                                                    in binding.body for node in _module_scope(statement)):
        return True, "its self_test (line {}) is a generator (its body yields): the call returns a " \
                     "generator, so the suite never runs".format(binding.lineno)
    if isinstance(binding, ast.FunctionDef):
        returns = [node for statement in binding.body for node in _module_scope(statement)
                   if isinstance(node, ast.Return)]
        empty = [node.lineno for node in returns if node.value is None
                 or (isinstance(node.value, ast.Constant) and node.value.value is None)]
        if empty:
            return True, "its self_test (line {}) returns None (a bare `return` or `return None`, line {}): " \
                         "the required return shape is a `return <expression>`".format(
                             binding.lineno, min(empty))
        if not returns:
            return True, "its self_test (line {}) lacks the required return shape: no `return <expression>` " \
                         "in its own scope".format(binding.lineno)
    stars = [node.lineno for node in _module_scope(tree) if isinstance(node, ast.ImportFrom)
             and any(alias.name == "*" for alias in node.names)]
    if stars:
        return True, "it has a star import (line {}): a star import may bind `__name__`, `sys` or " \
                     "`self_test`".format(min(stars))
    renames = bindings_of("__name__")
    if renames:
        return True, "it binds `__name__` at module scope (line {}), so its `__main__` test no longer " \
                     "tells a run from an import".format(min(renames))
    rebinds = bindings_of("sys", imported=True)
    if rebinds:
        return True, "it binds `sys` other than by `import sys` (line {}), so the entry may not reach the sys " \
                     "module".format(min(rebinds))
    shadows = bindings_of("__builtins__")
    if shadows:
        return True, "it binds `__builtins__` at module scope (line {}), so the builtins its code reads may not " \
                     "be Python's".format(min(shadows))
    change = _sys_change(tree.body[:-1])
    if change is not None and change[2]:
        return True, "it stores through `sys.{}` (line {}) before its `__main__` block, so a module or value " \
                     "the entry reaches may not be the one it names".format(change[1], change[0].lineno)
    if change is not None:
        return True, "it changes `sys.{}` (line {}) before its `__main__` block, so the entry may not see " \
                     "`--self-test` or exit with self_test's result".format(change[1], change[0].lineno)
    store = _namespace_store(tree.body[:-1])
    if store is not None:
        return True, "it stores through `{}` (line {}) before its `__main__` block, so a builtin or a global " \
                     "the entry uses may not be the one it names".format(store[1], store[0].lineno)
    store = _self_test_or_expression_store(tree.body[:-1])
    if store is not None and store[1] == "self_test":
        return True, "it stores to or deletes through `self_test` (line {}) before its `__main__` block, so " \
                     "the self_test the entry calls may not be the one it binds".format(store[0].lineno)
    if store is not None:
        return True, "it stores to or deletes through an expression, not a name (line {}), before its " \
                     "`__main__` block; the guard does not follow such a target".format(store[0].lineno)
    return True, None


def _self_test_entry_gaps(directory, required=()):
    """Check the F-SELFTEST-NO-MAIN class STATICALLY: no module code is executed. Returns (gaps, exposers):
    `exposers` is the set of *.py names directly in `directory` that bind self_test at module scope, and `gaps`
    maps a name to why it fails. Every exposer must carry the one canonical entry, exactly:

        if __name__ == "__main__":
            if sys.argv[1:] == ["--self-test"]:
                sys.exit(self_test())
            ...  # any other argument handling the module has

    as its last top-level statement, with that operand order, sys imported at top level, the inner `if` holding
    nothing else and no else, no other test of `__name__` against "__main__" that runs at import (an if, while,
    conditional expression, and/or, comprehension condition or match, at module scope or in a class body, whose
    test mentions both), exactly one module-scope binding of self_test counted by name occurrence (each alias
    of an import or from-import, each assignment, for, with, walrus or del target, each def, class, except or
    match name, a star import, and each self_test a `global` anywhere names, is one), that binding made
    unconditionally by a statement before the block (a top-level def, class, import, from-import or
    assignment, or one in a top-level try body), the block itself binding it nowhere, a def'd self_test
    neither async nor a generator and with at least one `return <expression>` and no bare `return` or
    `return None` in its own body, no module-scope star import, no module-scope binding of `__name__` or
    `__builtins__`, no module-scope binding of sys but a plain `import sys`, no statement before the block
    changing sys through the name sys (a store to, delete of or augmented assignment to any `sys.<attribute>`,
    a store or delete through `sys.modules` or `sys.__dict__`, or an argv change), none storing to or
    deleting through `builtins`, `__builtins__` or `__main__`, and none storing to or deleting through the
    name self_test or through a target that starts from an expression rather than a name
    (_self_test_entry_gap gives the rules and each reason). It is a gap too when a *.py entry is not a
    regular file or cannot be read or parsed, when a `required` name is not found binding self_test, and when
    no module binds it at all. The listing is os.listdir, so a missing or unreadable directory is a gap, not
    an empty scan; it is not recursive, so _vendor/ is not scanned.

    Guarantee. The guard checks the static shape of the self_test binding and of the entry, and nothing else.
    The forms it catches are exactly these. self_test has exactly one binding at module scope, counted by name
    occurrence (each alias of an import or from-import, each Name stored or deleted, each def, class, except or
    match name, a star import, and each self_test a `global` anywhere names, is one), made unconditionally
    before the entry (a try-body binding counts even if an earlier statement there raises), and the block
    binds it nowhere. The entry is the single canonical one: the module's last statement and the only test of
    `__name__` against "__main__" that runs at import in a counted form (an if, while, conditional expression,
    and/or, comprehension condition or match, at module scope or in a class body, whose test mentions the name
    `__name__` and the string "__main__"). The module has no star import at module scope (a star import may
    bind `__name__`, sys or self_test, so it is a gap whatever it binds). `__name__` is bound nowhere at module
    scope by any of the counted forms (`__name__ = "helper"`, `from os import __name__`,
    `import os as __name__`, `del __name__`, a `global __name__` anywhere; a class-body `__name__` is the
    class's own and is not counted), and neither is `__builtins__` (`__builtins__ = {}`). sys is imported at
    top level, and every module-scope binding of it, and each sys a `global` names, is a plain `import sys`
    (`sys = None` or `import os as sys` is a gap). No statement before the entry, at module scope or in a class
    body, stores to, deletes or augments any attribute of sys spelled `sys.<attribute>` (`sys.exit = print`,
    `sys.stdout = None`, `del sys.argv`), stores to or deletes anything through `sys.argv`, `sys.modules` or
    `sys.__dict__` (`sys.argv[1:] = []`, `sys.modules[__name__].self_test = int`,
    `sys.__dict__["exit"] = print`), calls any attribute of sys.argv (`sys.argv.<name>(...)`) outside the
    read-only _ARGV_READERS (count, index, copy, __len__, __getitem__, __contains__, __iter__), or stores to or
    deletes anything through the name builtins, `__builtins__` or `__main__` (`builtins.len = None`,
    `__builtins__["len"] = None`, `__main__.self_test = int`), or stores to or deletes anything through the
    name self_test itself (`self_test.__code__ = ...`, `self_test.__defaults__ = ...`, a class self_test's
    `self_test.__new__ = ...`, `del self_test.__kwdefaults__`). A store through a name is one whose target,
    followed back through its attributes, subscripts and calls, is spelled from that name (_stores). A store
    or delete before the entry whose target, so followed, starts from anything other than a plain name is a
    gap whatever it reaches (`[sys][0].exit = print`, `(sys,)[0].modules[__name__].self_test = int`,
    `(sys if sys else None).argv[1:] = []`, `(s := sys).exit = print`): the guard does not follow such a
    target, so it counts every one, a harmless one included. Those rules read a comprehension or generator
    expression as they read a for statement: an attribute or subscript target
    (`[None for sys.exit in [print]]`, `[None for sys.argv[1:] in [[]]]`) is a store, a walrus in one binds
    the enclosing scope (`[(__name__ := "x") for _ in "x"]`), and only a plain name target is the
    comprehension's own and not counted. When the binding is a def it is a plain function: not async; its own
    body, not a nested function, lambda or class, yields nothing and holds at least one `return <expression>`
    and no bare `return` or `return None`; its decorators are not inspected.

    Residual. Everything else that changes, at run time, what a name the entry uses means (sys, sys.argv,
    sys.exit, self_test, `__name__`, a builtin, or the module namespace) is outside the guard. The families:
    reflective stores (setattr or getattr then a store, `setattr(self_test, "__code__", ...)`,
    `globals()["self_test"] = int`, `globals().update(...)`, `vars(...)`, a namespace's `__dict__` reached
    other than through sys, builtins, `__builtins__` or `__main__`, and a store through any attribute of sys
    other than argv, modules and __dict__, which the guard does not count: `sys.path[:] = saved` is one);
    dynamic binding (a module `__getattr__`, importlib or `__import__`, a binding made where the guard does not
    look); exec, eval and compile; aliasing (`import sys as s` then `s.exit = print`, `(s := sys)` then a later
    `s.exit = print`, `import builtins as b` then `b.len = None`, `import __main__ as m` then
    `m.self_test = int`, `run = self_test` then `run.__code__ = ...`, `from sys import argv`, `argv = sys.argv`,
    `modules = sys.modules`, a method taken from sys.argv and called later, another module's reference to
    sys, `import os` then `os.sys.exit = print` say, and a function's `__globals__` or `__builtins__`,
    `helper.__globals__["sys"] = None` say); and calls into other code that
    runs before the entry (a function, a decorator, a method, one on an attribute of sys other than argv
    included, `sys.stdout.close()` say, a method reached through a class, `sys.argv.__class__.clear(sys.argv)`
    say, a store spelled from a name through a call, `holder()[0].exit = print`
    say, which the guard counts as through that name, or another module's import-time code). Nor does it
    catch a statement that ends the run before the entry or never returns (a top-level sys.exit or os._exit,
    including one under a `__main__` test it does not count, such as one in a function body or one reached
    through a held value). For example, _opf_adopt_observe's sys.path setup holds its `__name__` comparison in
    a name, so its `if` is not counted as a `__main__` test. Nor does it catch exit subversion after the call
    (an atexit hook, os._exit, a SystemExit handler, a stateful self_test).

    Beyond the return shape above, the guard does not check the value self_test returns at run time, or how
    sys.exit treats that value. How sys.exit treats a value is platform- and version-dependent, and no rule
    for it is stated here. Examples, measured on
    CPython 3.14.4 on Linux x86_64 (examples only, not a rule): `sys.exit(value)` exited 0 for None, 0,
    False, an int subclass's 512, 256, -256, 2**31, 2**32, 2**32 + 256, 2**63 - 256, -2**63, and the tuples
    (), (None,), (0,) and (256,); it exited 255 for 2**63, 2**64, 2**100, -2**63 - 256 and -2**64; and it
    exited 1 for 1, True, (1,), 0.0, [], "", 0j and (0, 0), printing the value first for the last five ("" as
    an empty line). An imported, assigned or class self_test is not inspected beyond being bound (it may be a
    callable returning 0, `self_test = int` say), and a def'd self_test is inspected only for the shapes above
    (one that falls off its end on some path, or returns a value sys.exit treats as success, is not caught). A
    binding made dynamically is not recognized, so such a module is not an exposer unless it also binds
    statically.

    Run with --self-test, a module that passes and stays outside the residual calls whatever self_test is
    bound to and exits with its result: if self_test is unbound there (a try body that raises before binding
    it) the run exits 1 with NameError, and a non-callable exits 1 with TypeError. It does NOT hold that a
    module in the residual runs the suite or exits non-zero: it can exit 0 without running it. The residual is
    a code-review matter: the guard is for an accidental missing or miswired entry. The exact-form rule is
    conservative: a working entry in any other form is a gap."""
    import ast
    import _optlevel
    gaps, exposers = {}, set()
    try:
        names = sorted(os.listdir(directory))
    except OSError as exc:
        return {str(directory): "the directory cannot be listed ({})".format(type(exc).__name__)}, exposers
    for name in names:
        if not name.endswith(".py"):
            continue
        path = os.path.join(str(directory), name)
        try:
            regular = stat.S_ISREG(os.lstat(path).st_mode)
        except OSError as exc:
            gaps[name] = "it cannot be examined ({})".format(type(exc).__name__)
            continue
        if not regular:
            gaps[name] = "it is not a regular file"
            continue
        try:
            with open(path, "rb") as handle:
                source = handle.read()
        except OSError as exc:
            gaps[name] = "it cannot be read ({})".format(type(exc).__name__)
            continue
        try:
            exposes, reason = _self_test_entry_gap(_optlevel.parse(source, path))
        except (SyntaxError, ValueError, RecursionError, MemoryError) as exc:
            gaps[name] = "it cannot be parsed ({})".format(type(exc).__name__)
            continue
        if exposes:
            exposers.add(name)
        if reason is not None:
            gaps[name] = reason
    for name in required:
        if name not in exposers and name not in gaps:
            gaps[name] = "expected to bind self_test, but the scan did not find it"
    if not exposers:
        gaps.setdefault(str(directory), "no module in it binds self_test")
    return gaps, exposers


# Registered self-tests that are not a module's own self_test. Each is a suite of this dispatcher itself,
# defined in this file and run only through `opf.py --self-test`, which main() dispatches; opf.py binds no
# self_test, so it is not an exposer and these have no module entry to check.
_ENTRY_FLOOR_EXEMPT = {
    "opf-watchdog-isolation": "the dispatcher's watchdog isolation suite (_watchdog_isolation_self_test)",
    "opf-watchdog-regressions": "the dispatcher's watchdog regression suite (_watchdog_regression_self_test)",
    "opf-aggregator": "this aggregator's own suite (_aggregator_self_test)",
    "opf-retained-close-offpath": "the dispatcher's off-path released-close sweep (#377, "
                                  "_retained_close_offpath_self_test)",
    "opf-close-exc-safe-vectors": "the dispatcher's in-flight-exception close vectors (#377, "
                                  "_close_exc_safe_vectors_self_test)",
    "opf-cli": "the dispatcher's command-line suite (_cli_self_test)",
}


def _self_test_floor(registry, directory, exempt):
    """Derive the scan's required floor from the registry BY MODULE. Returns (required, faults): `required` is
    the set of *.py names, directly in `directory`, whose module's self_test is a registry entry's callable.
    A fault is an empty registry; an entry that is not exempt whose callable has no defining module file in
    `directory`, or is not that module's self_test; an exempt entry not defined in this file; and an exemption
    naming no registry entry."""
    registry = tuple(registry)
    required, faults, labels = set(), [], set()
    if not registry:
        faults.append("the self-test registry is empty")
    for label, fn in registry:
        labels.add(label)
        module = sys.modules.get(getattr(fn, "__module__", None))
        if label in exempt:
            if module is not sys.modules.get(__name__):
                faults.append("{}: exempt, but not defined in opf.py".format(label))
            continue
        path = getattr(module, "__file__", None)
        if not path:
            faults.append("{}: its callable has no defining module file".format(label))
        elif Path(path).resolve().parent != Path(directory).resolve():
            faults.append("{}: its module {} is not in {}".format(label, path, directory))
        elif getattr(module, "self_test", None) is not fn:
            faults.append("{}: its callable is not {}.self_test".format(label, module.__name__))
        else:
            required.add(Path(path).name)
    for label in sorted(set(exempt) - labels):
        faults.append("{}: exempt, but no registry entry has that label".format(label))
    return required, faults


# Synthetic modules for the scan's own flips: (name, source, binds self_test, the reason it must be named for,
# or None for a clean module). Beside these, directory.py is a directory, unreadable.py a mode-0 file (only
# when not root), absent.py a required name that does not exist (canonical.py and canonical_import.py are
# required too, so the floor does not over-reject), and absent/ and empty/ are a missing and an empty directory.
_ENTRY_HEAD = 'import sys\n\n\ndef self_test():\n    return 0\n\n\n'
_ENTRY_BODY = '    if sys.argv[1:] == ["--self-test"]:\n        sys.exit(self_test())\n'
_ENTRY_MAIN = 'if __name__ == "__main__":\n' + _ENTRY_BODY
_ENTRY_NONE = 'no `if __name__ == "__main__":` block'
_ENTRY_FIRST = "not the canonical `--self-test` statement"
_ENTRY_BLOCK_BINDS = ") binds self_test"
_ENTRY_CONDITIONAL = "bound only conditionally"
_ENTRY_GENERATOR = "is a generator"
_ENTRY_TWO_TESTS = "exactly one is allowed"
_ENTRY_ARGV = "changes `sys.argv`"
_ENTRY_REBOUND = "bindings at module scope"
_ENTRY_NO_RETURN = "no `return <expression>` in its own scope"
_ENTRY_RETURNS_NONE = "returns None (a bare"
_ENTRY_NAME = "binds `__name__` at module scope"
_ENTRY_SYS = "binds `sys` other than by `import sys`"
_ENTRY_STAR = "a star import may bind `__name__`, `sys` or `self_test`"
_ENTRY_BUILTINS = "binds `__builtins__` at module scope"
_ENTRY_SELF_TEST_STORE = "stores to or deletes through `self_test`"
_ENTRY_EXPRESSION_STORE = "through an expression, not a name"
_ENTRY_FIXTURES = (
    ("canonical.py", _ENTRY_HEAD + "def main(argv):\n    return 2\n\n\n" + _ENTRY_MAIN
     + "    sys.exit(main(sys.argv[1:]))\n", True, None),
    ("canonical_import.py", "import sys\nfrom _no_such_module import self_test\n\n" + _ENTRY_MAIN, True, None),
    ("canonical_spaced.py", _ENTRY_HEAD + "if (__name__ == '__main__'):  # entry\n    if sys.argv[1 :] == [\n"
     "            '--self-test',]:\n        sys.exit(  self_test( ) )\n", True, None),
    ("plain.py", 'if __name__ == "__main__":\n    raise SystemExit(0)\n', False, None),
    ("local_only.py", "import holder\n\n\ndef helper():\n    self_test = 1\n    return self_test\n\n\n"
     "class Holder:\n    self_test = 0\n\n\nholder.self_test = [self_test for self_test in ()]\n"
     "print(lambda self_test: self_test)\n", False, None),
    ("no_entry.py", "def self_test():\n    return 0\n", True, _ENTRY_NONE),
    ("bind_async.py", "async def self_test():\n    return 0\n", True, _ENTRY_NONE),
    ("bind_class.py", "class self_test:\n    pass\n", True, _ENTRY_NONE),
    ("bind_assign.py", "self_test = lambda: 0\n", True, _ENTRY_NONE),
    ("bind_tuple.py", "main, (self_test, *rest) = 1, (2, 3)\n", True, _ENTRY_NONE),
    ("bind_annotated.py", "self_test: object\n", True, _ENTRY_NONE),
    ("bind_import.py", "import _no_such_module as self_test\n", True, _ENTRY_NONE),
    ("bind_from.py", "from _no_such_module import self_test\n", True, _ENTRY_NONE),
    ("bind_star.py", "from _no_such_module import *\n", True, _ENTRY_NONE),
    ("bind_try.py", "try:\n    import _no_such_module\nexcept ImportError:\n    def self_test():\n        return 0\n",
     True, _ENTRY_NONE),
    ("bind_with.py", "with open(__file__) as self_test:\n    pass\n", True, _ENTRY_NONE),
    ("bind_for.py", "for self_test in ():\n    pass\n", True, _ENTRY_NONE),
    ("bind_walrus.py", "if (self_test := len):\n    pass\n", True, _ENTRY_NONE),
    ("bind_global.py", "def install():\n    global self_test\n    self_test = len\n", True, _ENTRY_NONE),
    ("bind_del.py", "del self_test\n", True, _ENTRY_NONE),
    ("not_equal.py", _ENTRY_HEAD + 'if __name__ != "__main__":\n' + _ENTRY_BODY, True, "does not test exactly"),
    ("reversed.py", _ENTRY_HEAD + 'if "__main__" == __name__:\n' + _ENTRY_BODY, True, "does not test exactly"),
    ("pass_body.py", _ENTRY_HEAD + 'if __name__ == "__main__":\n    pass\n', True, _ENTRY_FIRST),
    ("via_main.py", _ENTRY_HEAD + 'def main():\n    return self_test()\n\n\nif __name__ == "__main__":\n'
     "    sys.exit(main())\n", True, _ENTRY_FIRST),
    ("drops_result.py", _ENTRY_HEAD + 'if __name__ == "__main__":\n    if sys.argv[1:] == ["--self-test"]:\n'
     "        self_test()\n", True, _ENTRY_FIRST),
    ("inner_else.py", _ENTRY_HEAD + _ENTRY_MAIN + "    else:\n        sys.exit(2)\n", True, _ENTRY_FIRST),
    ("inner_extra.py", _ENTRY_HEAD + 'if __name__ == "__main__":\n    if sys.argv[1:] == ["--self-test"]:\n'
     '        print("self-test")\n        sys.exit(self_test())\n', True, _ENTRY_FIRST),
    ("not_first.py", _ENTRY_HEAD + 'if __name__ == "__main__":\n    print("entry")\n' + _ENTRY_BODY, True,
     "is not the first"),
    ("two_mains.py", _ENTRY_HEAD + 'if __name__ == "__main__":\n    pass\n' + _ENTRY_MAIN, True,
     "exactly one is allowed"),
    ("nested_main.py", _ENTRY_HEAD + "try:\n    " + _ENTRY_MAIN.replace("\n    ", "\n        ")
     + "except ImportError:\n    pass\n", True, "is nested in another statement"),
    ("main_else.py", _ENTRY_HEAD + _ENTRY_MAIN + "else:\n    pass\n", True, "has an else"),
    ("not_last.py", _ENTRY_HEAD + _ENTRY_MAIN + "\n\ndef later():\n    pass\n", True, "follows its"),
    ("no_sys.py", _ENTRY_HEAD.replace("import sys\n", "") + _ENTRY_MAIN, True, "does not `import sys`"),
    ("unparseable.py", "def self_test(:\n    return 0\n", False, "cannot be parsed"),
    ("main_defines.py", "import sys\n\n\n" + _ENTRY_MAIN + "    def self_test():\n        return 0\n", True,
     _ENTRY_BLOCK_BINDS),
    ("main_rebinds.py", _ENTRY_HEAD + _ENTRY_MAIN + "    self_test = None\n", True, _ENTRY_BLOCK_BINDS),
    ("platform_only.py", 'import sys\n\nif sys.platform == "win32":\n    def self_test():\n        return 0\n\n\n'
     + _ENTRY_MAIN, True, _ENTRY_CONDITIONAL),
    ("except_only.py", "import sys\n\ntry:\n    import _no_such_module\nexcept ImportError:\n"
     "    def self_test():\n        return 0\n\n\n" + _ENTRY_MAIN, True, _ENTRY_CONDITIONAL),
    ("async_def.py", _ENTRY_HEAD.replace("def self_test", "async def self_test") + _ENTRY_MAIN, True,
     "is an `async def`"),
    ("generator_def.py", _ENTRY_HEAD.replace("return 0", "yield 0") + _ENTRY_MAIN, True, _ENTRY_GENERATOR),
    ("generator_from.py", "import sys\n\n\ndef self_test():\n    if sys:\n        yield from ()\n    return 0\n\n\n"
     + _ENTRY_MAIN, True, _ENTRY_GENERATOR),
    ("nested_generator.py", "import sys\n\n\ndef self_test():\n    def rows():\n        yield 0\n\n"
     "    async def feed():\n        yield 0\n\n    class Rows:\n        pull = lambda: (yield)\n\n"
     "    pull = lambda: (yield)\n    return sum(rows())\n\n\n" + _ENTRY_MAIN, True, None),
    ("try_import.py", "import sys\n\ntry:\n    from _no_such_module import self_test\nexcept ImportError:\n"
     "    pass\n\n\n" + _ENTRY_MAIN, True, None),
    ("tuple_main.py", _ENTRY_HEAD + 'if __name__ in ("__main__",):\n    sys.exit(0)\n\n\n' + _ENTRY_MAIN, True,
     _ENTRY_TWO_TESTS),
    ("class_main.py", _ENTRY_HEAD + 'class Entry:\n    if __name__ == "__main__":\n        sys.exit(0)\n\n\n'
     + _ENTRY_MAIN, True, _ENTRY_TWO_TESTS),
    ("and_main.py", _ENTRY_HEAD + '__name__ == "__main__" and sys.exit(0)\n' + _ENTRY_MAIN, True, _ENTRY_TWO_TESTS),
    ("ifexp_main.py", _ENTRY_HEAD + 'sys.exit(0) if __name__ == "__main__" else None\n' + _ENTRY_MAIN, True,
     _ENTRY_TWO_TESTS),
    ("while_main.py", _ENTRY_HEAD + 'while __name__ == "__main__":\n    sys.exit(0)\n' + _ENTRY_MAIN, True,
     _ENTRY_TWO_TESTS),
    ("match_main.py", _ENTRY_HEAD + 'match __name__:\n    case "__main__":\n        sys.exit(0)\n' + _ENTRY_MAIN,
     True, _ENTRY_TWO_TESTS),
    ("comprehension_main.py", _ENTRY_HEAD + '[sys.exit(0) for _ in "x" if __name__ == "__main__"]\n'
     + _ENTRY_MAIN, True, _ENTRY_TWO_TESTS),
    ("while_entry.py", _ENTRY_HEAD + 'while __name__ == "__main__":\n' + _ENTRY_BODY, True, "not an `if`"),
    ("argv_del.py", _ENTRY_HEAD + "del sys.argv[1:]\n\n\n" + _ENTRY_MAIN, True, _ENTRY_ARGV),
    ("argv_append.py", _ENTRY_HEAD + 'sys.argv.append("--quiet")\n\n\n' + _ENTRY_MAIN, True, _ENTRY_ARGV),
    ("argv_class.py", _ENTRY_HEAD + 'class Quiet:\n    sys.argv += ["--quiet"]\n\n\n' + _ENTRY_MAIN, True,
     _ENTRY_ARGV),
    ("argv_local.py", _ENTRY_HEAD + "def reset():\n    del sys.argv[1:]\n\n\n" + _ENTRY_MAIN
     + "    sys.argv.pop()\n", True, None),
    ("two_bindings.py", _ENTRY_HEAD + "def main():\n    return 0\n\n\nself_test = main\n\n\n" + _ENTRY_MAIN, True,
     _ENTRY_REBOUND),
    ("star_after_def.py", _ENTRY_HEAD + "from _no_such_module import *\n\n\n" + _ENTRY_MAIN, True,
     _ENTRY_REBOUND),
    ("global_rebind.py", _ENTRY_HEAD + "def install():\n    global self_test\n    self_test = int\n\n\n"
     + _ENTRY_MAIN, True, _ENTRY_REBOUND),
    ("import_aliases.py", "import sys\nimport _no_such_module as self_test, _no_such_other as self_test\n\n"
     + _ENTRY_MAIN, True, _ENTRY_REBOUND),
    ("from_aliases.py", "import sys\nfrom _no_such_module import main as self_test, self_test\n\n" + _ENTRY_MAIN,
     True, _ENTRY_REBOUND),
    ("argv_init.py", _ENTRY_HEAD + 'sys.argv.__init__(["opf"])\n\n\n' + _ENTRY_MAIN, True, _ENTRY_ARGV),
    ("argv_setitem.py", _ENTRY_HEAD + "sys.argv.__setitem__(slice(1, None), [])\n\n\n" + _ENTRY_MAIN, True,
     _ENTRY_ARGV),
    ("argv_count.py", _ENTRY_HEAD + 'QUIET = sys.argv.count("x")\n\n\n' + _ENTRY_MAIN, True, None),
    ("no_return.py", "import sys\n\n\ndef self_test():\n    def suite():\n        return 1\n\n"
     "    class Suite:\n        def run(self):\n            return 1\n\n    check = lambda: 1\n"
     "    suite() or check()\n\n\n" + _ENTRY_MAIN, True, _ENTRY_NO_RETURN),
    ("return_bare.py", "import sys\n\n\ndef self_test():\n    if sys:\n        return\n\n\n" + _ENTRY_MAIN, True,
     _ENTRY_RETURNS_NONE),
    ("return_none.py", "import sys\n\n\ndef self_test():\n    if not sys:\n        return 1\n    return None\n\n\n"
     + _ENTRY_MAIN, True, _ENTRY_RETURNS_NONE),
    ("return_value.py", "import sys\n\n\ndef self_test():\n    failures = 0\n    if failures:\n        return 1\n"
     "    return failures\n\n\n" + _ENTRY_MAIN, True, None),
    ("name_assign.py", _ENTRY_HEAD + '__name__ = "helper"\n\n\n' + _ENTRY_MAIN, True, _ENTRY_NAME),
    ("name_from.py", _ENTRY_HEAD + "from os import __name__\n\n\n" + _ENTRY_MAIN, True, _ENTRY_NAME),
    ("name_import.py", _ENTRY_HEAD + "import os as __name__\n\n\n" + _ENTRY_MAIN, True, _ENTRY_NAME),
    ("name_global.py", _ENTRY_HEAD + "def rename():\n    global __name__\n    del __name__\n\n\n" + _ENTRY_MAIN,
     True, _ENTRY_NAME),
    ("name_class.py", _ENTRY_HEAD + 'class Helper:\n    __name__ = "helper"\n\n\n' + _ENTRY_MAIN, True, None),
    ("sys_rebind.py", _ENTRY_HEAD + "sys = None\n\n\n" + _ENTRY_MAIN, True, _ENTRY_SYS),
    ("sys_import_as.py", _ENTRY_HEAD + "import os as sys\n\n\n" + _ENTRY_MAIN, True, _ENTRY_SYS),
    ("sys_reimport.py", _ENTRY_HEAD + "try:\n    import os, sys\nexcept ImportError:\n    pass\n"
     'sys.path.insert(0, "lib")\n\n\n' + _ENTRY_MAIN, True, None),
    ("sys_exit_print.py", _ENTRY_HEAD + "sys.exit = print\n\n\n" + _ENTRY_MAIN, True, "changes `sys.exit`"),
    ("sys_stdout_none.py", _ENTRY_HEAD + "sys.stdout = None\n\n\n" + _ENTRY_MAIN, True, "changes `sys.stdout`"),
    ("sys_del_attr.py", _ENTRY_HEAD + "class Quiet:\n    del sys.exit\n\n\n" + _ENTRY_MAIN, True,
     "changes `sys.exit`"),
    ("sys_augmented.py", _ENTRY_HEAD + 'sys.ps1 += "x"\n\n\n' + _ENTRY_MAIN, True, "changes `sys.ps1`"),
    ("comp_sys_exit.py", _ENTRY_HEAD + "[None for sys.exit in [print]]\n\n\n" + _ENTRY_MAIN, True,
     "changes `sys.exit`"),
    ("comp_argv.py", _ENTRY_HEAD + '[None for sys.argv in [["tool"]]]\n\n\n' + _ENTRY_MAIN, True, _ENTRY_ARGV),
    ("comp_argv_slice.py", _ENTRY_HEAD + "[None for sys.argv[1:] in [[]]]\n\n\n" + _ENTRY_MAIN, True, _ENTRY_ARGV),
    ("comp_tuple_target.py", _ENTRY_HEAD + "{0 for (main, *sys.exit) in [(1, print)]}\n\n\n" + _ENTRY_MAIN, True,
     "changes `sys.exit`"),
    ("genexp_sys_exit.py", _ENTRY_HEAD + "list(None for sys.exit in [print])\n\n\n" + _ENTRY_MAIN, True,
     "changes `sys.exit`"),
    ("class_comp_sys_exit.py", _ENTRY_HEAD + "class Quiet:\n    [None for sys.exit in [print]]\n\n\n"
     + _ENTRY_MAIN, True, "changes `sys.exit`"),
    ("comp_walrus_name.py", _ENTRY_HEAD + '[(__name__ := "x") for _ in "x"]\n\n\n' + _ENTRY_MAIN, True,
     _ENTRY_NAME),
    ("comp_walrus_sys.py", _ENTRY_HEAD + '[(sys := None) for _ in "x"]\n\n\n' + _ENTRY_MAIN, True, _ENTRY_SYS),
    ("comp_walrus_self_test.py", _ENTRY_HEAD + '[(self_test := int) for _ in "x"]\n\n\n' + _ENTRY_MAIN, True,
     _ENTRY_REBOUND),
    ("comp_local_names.py", _ENTRY_HEAD + '[sys for sys in [1]]\n{0 for (*sys, __name__) in [(1, 2)]}\n'
     "class Rows:\n    rows = [self_test for self_test in ()]\n\n\n" + _ENTRY_MAIN, True, None),
    ("star_only.py", "import sys\nfrom _no_such_module import *\n\n\n" + _ENTRY_MAIN, True, _ENTRY_STAR),
    ("builtins_bind.py", _ENTRY_HEAD + '__builtins__ = {"len": len}\n\n\n' + _ENTRY_MAIN, True, _ENTRY_BUILTINS),
    ("builtins_store.py", _ENTRY_HEAD + "import builtins\nbuiltins.len = None\n\n\n" + _ENTRY_MAIN, True,
     "stores through `builtins`"),
    ("builtins_subscript.py", _ENTRY_HEAD + '__builtins__["len"] = None\n\n\n' + _ENTRY_MAIN, True,
     "stores through `__builtins__`"),
    ("main_store.py", _ENTRY_HEAD + "import __main__\n__main__.self_test = int\n\n\n" + _ENTRY_MAIN, True,
     "stores through `__main__`"),
    ("sys_modules_store.py", _ENTRY_HEAD + "sys.modules[__name__].self_test = int\n\n\n" + _ENTRY_MAIN, True,
     "stores through `sys.modules`"),
    ("sys_dict_store.py", _ENTRY_HEAD + 'sys.__dict__["exit"] = print\n\n\n' + _ENTRY_MAIN, True,
     "stores through `sys.__dict__`"),
    ("sys_path_restore.py", _ENTRY_HEAD + "saved = list(sys.path)\nsys.path[:] = saved\n\n\n" + _ENTRY_MAIN, True,
     None),
    ("self_test_code.py", _ENTRY_HEAD + "self_test.__code__ = (lambda: 0).__code__\n\n\n" + _ENTRY_MAIN, True,
     _ENTRY_SELF_TEST_STORE),
    ("self_test_defaults.py", _ENTRY_HEAD + "class Patch:\n    self_test.__defaults__ = ()\n\n\n" + _ENTRY_MAIN,
     True, _ENTRY_SELF_TEST_STORE),
    ("self_test_del.py", _ENTRY_HEAD + "del self_test.__kwdefaults__\n\n\n" + _ENTRY_MAIN, True,
     _ENTRY_SELF_TEST_STORE),
    ("self_test_new.py", "import sys\n\n\nclass self_test:\n    pass\n\n\nself_test.__new__ = lambda cls: 0\n\n\n"
     + _ENTRY_MAIN, True, _ENTRY_SELF_TEST_STORE),
    ("expression_list.py", _ENTRY_HEAD + "[sys][0].exit = print\n\n\n" + _ENTRY_MAIN, True,
     _ENTRY_EXPRESSION_STORE),
    ("expression_tuple.py", _ENTRY_HEAD + "(sys,)[0].modules[__name__].self_test = int\n\n\n" + _ENTRY_MAIN, True,
     _ENTRY_EXPRESSION_STORE),
    ("expression_ifexp.py", _ENTRY_HEAD + "(sys if sys else None).argv[1:] = []\n\n\n" + _ENTRY_MAIN, True,
     _ENTRY_EXPRESSION_STORE),
    ("expression_walrus.py", _ENTRY_HEAD + "(s := sys).exit = print\n\n\n" + _ENTRY_MAIN, True,
     _ENTRY_EXPRESSION_STORE),
    ("expression_comp.py", _ENTRY_HEAD + "[None for [sys][0].exit in [print]]\n\n\n" + _ENTRY_MAIN, True,
     _ENTRY_EXPRESSION_STORE),
    ("name_root_store.py", _ENTRY_HEAD + 'rows = {}\nrows[len(rows)] = 0\nrows.copy()["x"] = 1\n\n\n'
     + _ENTRY_MAIN, True, None),
)


def _self_test_entry_probe(tmp):
    """Run the scan over the synthetic modules written into `tmp`. Returns a list of the discrepancies: a red
    case not named, named for another reason, or a clean one named, and any exposure the scan got wrong."""
    root = Path(tmp)
    expected = {name: reason for name, _source, _binds, reason in _ENTRY_FIXTURES if reason is not None}
    expected.update({"directory.py": "not a regular file", "absent.py": "the scan did not find it",
                     "absent/": "cannot be listed", "empty/": "no module in it binds self_test"})
    for name, source, _binds, _reason in _ENTRY_FIXTURES:
        (root / name).write_text(source, encoding="utf-8")
    (root / "directory.py").mkdir()
    (root / "empty").mkdir()
    if hasattr(os, "geteuid") and os.geteuid() != 0:   # chmod cannot make a file unreadable to root
        (root / "unreadable.py").write_text(_ENTRY_HEAD + _ENTRY_MAIN, encoding="utf-8")
        (root / "unreadable.py").chmod(0)
        expected["unreadable.py"] = "cannot be read"
    try:
        named, exposers = _self_test_entry_gaps(root, ("canonical.py", "canonical_import.py", "absent.py"))
    finally:
        if (root / "unreadable.py").exists():
            (root / "unreadable.py").chmod(0o600)
    for label in ("absent/", "empty/"):
        found = _self_test_entry_gaps(root / label.rstrip("/"))[0]
        if found:
            named[label] = "; ".join(found.values())
    faults = ["{} not named".format(name) for name in sorted(set(expected) - set(named))]
    faults += ["{} named wrongly ({})".format(name, named[name]) for name in sorted(set(named) - set(expected))]
    faults += ["{} named for another reason ({})".format(name, named[name]) for name in sorted(expected)
               if name in named and expected[name] not in named[name]]
    binders = {name for name, _source, binds, _reason in _ENTRY_FIXTURES if binds}
    faults += ["{} exposure misjudged".format(name) for name in sorted(binders ^ exposers)]
    return faults


def _self_test_floor_probe(registry, directory, elsewhere):
    """Flip the registry floor: each synthetic registry must yield its fault, and every module the real
    registry requires must be named when the scan runs over `elsewhere`, a directory that lacks them. Returns
    a list of the discrepancies."""
    misses = []
    required = _self_test_floor(registry, directory, _ENTRY_FLOOR_EXEMPT)[0]
    external = next(label for label, _fn in registry if label not in _ENTRY_FLOOR_EXEMPT)
    cases = (
        ("empty registry", (), _ENTRY_FLOOR_EXEMPT, "the self-test registry is empty"),
        ("empty iterator registry", iter(()), _ENTRY_FLOOR_EXEMPT, "the self-test registry is empty"),
        ("not a module's self_test", registry + (("synthetic-lambda", lambda: 0),), _ENTRY_FLOOR_EXEMPT,
         "synthetic-lambda: its callable is not"),
        ("exempt but external", registry, dict(_ENTRY_FLOOR_EXEMPT, **{external: "synthetic"}),
         external + ": exempt, but not defined in"),
        ("stale exemption", registry, dict(_ENTRY_FLOOR_EXEMPT, **{"synthetic-stale": "synthetic"}),
         "synthetic-stale: exempt, but no registry entry"),
    )
    for label, table, exempt, want in cases:
        if not any(want in fault for fault in _self_test_floor(table, directory, exempt)[1]):
            misses.append("floor case {!r} not named".format(label))
    gaps = _self_test_entry_gaps(elsewhere, required)[0]
    lost = sorted(name for name in required if "the scan did not find it" not in gaps.get(name, ""))
    if not required or lost:
        misses.append("registry modules missing from the scan not named: {}".format(
            ", ".join(lost) or "the registry requires none"))
    return misses


def _aggregator_self_test():
    """Guard the aggregator's fail-closed return-vocabulary check (MAJOR 3). A helper returning a value
    OUTSIDE the {0,1,2} int vocabulary must fail the aggregate CLOSED (a non-zero worst), never be
    admitted as clean because a bool or float compares equal to an allowed int (False == 0, True == 1,
    0.0 == 0). Returns 0 clean, 1 on a failure. Registered below so `opf.py --self-test` exercises it;
    the store legs did not, letting a helper returning False produce an aggregate exit 0. It also holds
    the F-SELFTEST-NO-MAIN class empty by static form (_self_test_entry_gaps, which runs no module code): every
    module in this directory that binds self_test must carry the canonical `--self-test` entry; the floor
    (_self_test_floor) requires every module whose self_test is registered below and fails on an empty
    registry, an empty scan, or a registered callable that is not its module's self_test and not exempt; and
    the synthetic modules and registries prove each red case is named, for its reason, and no clean one is."""
    import tempfile
    if _bootstrap() != EXIT_OK:
        return EXIT_MALFORMED
    here = Path(__file__).resolve().parent
    registry = _self_tests()
    required, faults = _self_test_floor(registry, here, _ENTRY_FLOOR_EXEMPT)
    gaps, exposers = _self_test_entry_gaps(here, required)
    with tempfile.TemporaryDirectory(prefix="opf-entry-scan-") as tmp:
        missed = _self_test_entry_probe(tmp) + _self_test_floor_probe(registry, here, tmp)
    if gaps or faults or missed:
        print("opf aggregator self-test: FAIL (self_test modules without the canonical `--self-test` entry: {}; "
              "registry floor faults: {}; synthetic probe discrepancies: {})".format(
                  "; ".join("{} ({})".format(k, v) for k, v in sorted(gaps.items())) or "none",
                  "; ".join(faults) or "none", "; ".join(missed) or "none"), file=sys.stderr)
        return EXIT_FINDING
    ok = True
    # A helper returning False (bool, == 0) must NOT aggregate to clean.
    if run_self_tests((("synthetic-false", lambda: False),)) == EXIT_OK:
        ok = False
    # A helper returning 0.0 (float, == 0) must NOT aggregate to clean.
    if run_self_tests((("synthetic-zero-float", lambda: 0.0),)) == EXIT_OK:
        ok = False
    # An out-of-range int (3) must NOT aggregate to clean.
    if run_self_tests((("synthetic-three", lambda: 3),)) == EXIT_OK:
        ok = False
    # A genuine clean int (0) still aggregates to clean: the check does not over-reject.
    if run_self_tests((("synthetic-zero", lambda: 0),)) != EXIT_OK:
        ok = False
    if not ok:
        print("opf aggregator self-test: FAIL (fail-closed vocabulary check admitted a bad return)",
              file=sys.stderr)
        return EXIT_FINDING
    print("opf aggregator self-test: PASS (fail-closed on non-int / out-of-range helper returns; each of the "
          "{} self_test modules carries the canonical --self-test entry, {} of them required by the registry)".format(
              len(exposers), len(required)))
    return EXIT_OK


def _watchdog_timer_case(label, mode):
    """Private subprocess fixture: its timers belong to this process alone."""
    import contextlib
    import io
    import os
    import signal
    import time
    from unittest.mock import patch

    if _bootstrap() != EXIT_OK:
        return EXIT_MALFORMED
    functions = {
        "changelog": _opf_changelog.self_test, "views": _opf_views.self_test,
        "store": _opf_store.self_test, "check": _opf_check.self_test,
        "emit": _opf_emit.self_test,
        "window": lambda: 0 if _opf_emit.run_bounded(
            lambda: (time.sleep(2), "RETURNED")[1], timeout_s=10) == "RETURNED" else 1}
    which = {"virtual": signal.ITIMER_VIRTUAL, "prof": signal.ITIMER_PROF}.get(
        mode, signal.ITIMER_REAL)
    signum = {signal.ITIMER_REAL: signal.SIGALRM,
              signal.ITIMER_VIRTUAL: signal.SIGVTALRM,
              signal.ITIMER_PROF: signal.SIGPROF}[which]
    fired = []
    def handler(sig, frame):
        fired.append(sig)
    signal.signal(signum, handler)
    if mode == "expiry":
        # This private subprocess must deliver the expiry even if its launcher blocked SIGALRM.
        # Set the fixture's initial mask before arming/snapshotting; never change the launcher.
        signal.pthread_sigmask(signal.SIG_UNBLOCK, {signum})
    interval = 1800.0 if mode == "periodic" else 0.0
    value = 0.0 if mode == "inactive" else (1.0 if mode == "expiry" else 3600.0)
    signal.setitimer(which, value, interval)
    if mode == "pending":
        signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGALRM})
        signal.raise_signal(signal.SIGALRM)
    before = signal.getitimer(which)
    mask = signal.pthread_sigmask(signal.SIG_BLOCK, set())
    pending = signal.sigpending()
    pid, mutations = os.getpid(), []

    shields = []

    def forbid_parent(name, original):
        def call(*args, **kwargs):
            # An empty SIG_BLOCK is only a mask query. The supervision layer's
            # OWN cancellation masking (fix 2w) is sanctioned, verified as an
            # exact pair: a SIG_BLOCK of {SIGINT, SIGTERM} followed by a
            # SIG_SETMASK restoring the exact mask captured at the block; any
            # other mutation, and any unbalanced or inexact restore, is a
            # borrowed-state violation.
            query = (name == "pthread_sigmask" and len(args) == 2
                     and args[0] == signal.SIG_BLOCK and not args[1] and not kwargs)
            sanctioned = False
            if (name == "pthread_sigmask" and len(args) == 2 and not kwargs
                    and args[0] == signal.SIG_BLOCK
                    and args[1] == {signal.SIGINT, signal.SIGTERM}):
                sanctioned = True
                shields.append(original(signal.SIG_BLOCK, set()))
            elif (name == "pthread_sigmask" and len(args) == 2 and not kwargs
                    and args[0] == signal.SIG_SETMASK and shields
                    and args[1] == shields[-1]):
                sanctioned = True
                shields.pop()
            if os.getpid() == pid and not (query or sanctioned):
                mutations.append(name)
                raise AssertionError("caller signal mutation: " + name)
            return original(*args, **kwargs)
        return call

    output = io.StringIO()
    with contextlib.ExitStack() as stack:
        for name in ("setitimer", "alarm", "signal", "pthread_sigmask"):
            stack.enter_context(patch.object(
                signal, name, forbid_parent(name, getattr(signal, name))))
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            rc = functions[label]()
    after = signal.getitimer(which)
    ok = (rc == EXIT_OK and not mutations and not shields and after[1] == before[1]
          and signal.getsignal(signum) is handler
          and signal.pthread_sigmask(signal.SIG_BLOCK, set()) == mask)
    if mode == "expiry":
        ok = ok and after == (0.0, 0.0) and fired == [signum]
    elif mode == "inactive":
        ok = ok and before == after == (0.0, 0.0) and not fired
    else:
        # No syscall can reset the countdown or phase; no narrow wall-clock
        # assertion. CPU timers continue in their own kernel clock domains.
        ok = ok and 0 < after[0] <= before[0] and not fired
    if mode == "pending":
        ok = ok and signal.SIGALRM in pending and signal.SIGALRM in signal.sigpending()
    if not ok:
        print(label, mode, rc, before, after, mutations, fired, output.getvalue())
    # No save/restore or teardown mutation: the subprocess exits with its timer.
    return EXIT_OK if ok else EXIT_FINDING


def _watchdog_isolation_self_test():
    """Exercise actual callers in fresh processes, without borrowing our timer."""
    import subprocess
    import signal
    from _opf_emit import run_status_owned
    # These fixtures own their children's statuses; never borrow the caller's disposition.
    if signal.getsignal(signal.SIGCHLD) != signal.SIG_DFL:
        print("opf watchdog isolation: FAIL (unowned SIGCHLD disposition)")
        return EXIT_FINDING
    if _bootstrap() != EXIT_OK:
        return EXIT_MALFORMED
    required = ("ITIMER_REAL", "ITIMER_VIRTUAL", "ITIMER_PROF", "pthread_sigmask", "sigpending")
    if not hasattr(os, "fork") or not all(hasattr(signal, name) for name in required):
        print("opf watchdog isolation: FAIL (required POSIX facilities unavailable)")
        return EXIT_FINDING
    cases = [(label, mode) for label in ("changelog", "views", "store", "check", "emit")
             for mode in ("inactive", "single", "periodic", "virtual", "prof", "pending")]
    cases.append(("window", "expiry"))
    failed = False
    for label, mode in cases:
        code = ("import sys; sys.path.insert(0, " + repr(str(Path(__file__).resolve().parent))
                + "); import opf; return opf._watchdog_timer_case("
                + repr(label) + ", " + repr(mode) + ")")
        try:
            result = run_status_owned([sys.executable, "-I", "-B", "-c", code],
                                    fixture_id="timer/" + label + "/" + mode,
                                    capture_output=True, text=True, timeout=180)
            ok = result.returncode == EXIT_OK
            detail = result.stdout + result.stderr
        except (RuntimeError, subprocess.CalledProcessError) as exc:
            ok, detail = False, str(exc)
        except subprocess.TimeoutExpired:
            ok, detail = False, "fixture exceeded its 180s process bound"
        if not ok:
            failed = True
            print("opf watchdog isolation: FAIL", label, mode, detail, file=sys.stderr)
    for name in ("_fixture_setpgid", "_fixture_owned_fds", "release_fd"):
        if hasattr(_opf_emit, name) or hasattr(_opf_emit._FixtureProcess, name):
            failed = True
            print("opf watchdog isolation: FAIL (deleted supervision machinery still "
                  "present: " + name + ")", file=sys.stderr)
    if hasattr(_opf_store, "snapshot_caller_alarm") or hasattr(_opf_store, "restore_caller_alarm"):
        failed = True
        print("opf watchdog isolation: FAIL (borrow helper still present)", file=sys.stderr)
    if not failed:
        print("opf watchdog isolation: PASS (caller timers and signal state untouched)")
    return EXIT_FINDING if failed else EXIT_OK


# F-335-LOAD-FLAKE-PIPE-STALL: the execution-latency ceiling is a
# scheduling bound, not a correctness deadline. Worst observed
# `cancelled - start` for the pipe-stall and transient-census cases under
# 24 dedicated CPU-pressure workers on a 16-CPU host already running at
# loadavg ~16-37 (12 runs, 2026-09-28): 0.343 s against the requested
# 0.25 s timeout -- a worst scheduling overshoot of 0.093 s. The bound
# allows 50x that measured overshoot (0.25 + 50 x 0.093 ~= 4.9, rounded
# to 5.0 s). It still discriminates a late parent deadline: the
# deadline-flips mutant forces a timeout ABOVE this bound and must stay
# red, and the per-case subprocess budget still bounds a hang.
_DEADLINE_EXECUTION_BOUND = 5.0


def _watchdog_deadline_case(mode):
    """Private subprocess: at-fork hooks cannot be unregistered, so never install them in the runner."""
    import signal
    import time
    from unittest.mock import patch
    import _opf_emit
    import json
    import tempfile

    real_fork, real_pipe = os.fork, os.pipe
    started_r, started_w = real_pipe()
    caller = os.getpid()
    # The enclosing run_status_owned deadline rescues a regressed run_bounded.
    # It owns the full tree, so this fixture never races it with another signal.
    child, pipe = [], []

    def fork():
        pid = real_fork()
        if pid:
            child.append(pid)
        return pid

    def capture_pipe():
        # Caller-side only: the guardian also calls os.pipe (the subject
        # acknowledgment pipe), and capturing that pair in the guardian's copy
        # would hand the thunk the ack descriptors instead of run_bounded's pipe.
        pair = real_pipe()
        if os.getpid() == caller:
            pipe[:] = pair
        return pair

    def stall():
        while True:
            time.sleep(60)

    def startup():
        os.write(started_w, b"started")
        if mode == "delayed-start":
            time.sleep(2)
        else:
            stall()

    def thunk():
        if mode in ("pipe-stall", "exit-stall", "transient-census"):
            # Defeat the independent child timer deliberately: exercise the PARENT deadline.
            signal.setitimer(signal.ITIMER_REAL, 0)
            os.write(started_w, b"started")
            os.write(pipe[1], b"BUFFERED-TOKEN")
            if mode == "exit-stall":
                os.close(pipe[1])                       # EOF before exit must not permit an unbounded wait
            stall()
        return "RETURNED"

    if mode in ("delayed-start", "stuck-start"):
        os.register_at_fork(after_in_child=startup)
    real_signal = _opf_emit._fixture_signal
    real_drain, real_children = _opf_emit._fixture_drain, _opf_emit._fixture_children
    empty_since = [None]
    events = tempfile.TemporaryFile()

    def record(row):
        os.write(events.fileno(), (json.dumps(row) + "\n").encode("ascii"))

    def cancel(pid, signum, *args, **kwargs):
        sent = real_signal(pid, signum, *args, **kwargs)
        if sent and signum == signal.SIGKILL:
            record(["cancel", time.monotonic()])
        return sent

    def drain(*args, **kwargs):
        try:
            return real_drain(*args, **kwargs)
        finally:
            record(["drain", time.monotonic(), kwargs["deadline"]])

    def census():
        if mode == "transient-census":
            if empty_since[0] is None:
                empty_since[0] = time.monotonic()
            if time.monotonic() - empty_since[0] < 1.6:
                return []
        return real_children()

    result, elapsed, reaped = None, None, False
    try:
        start = time.monotonic()
        with patch.object(os, "fork", fork), patch.object(os, "pipe", capture_pipe), \
                patch.object(_opf_emit, "_fixture_signal", cancel), \
                patch.object(_opf_emit, "_fixture_drain", drain), \
                patch.object(_opf_emit, "_fixture_children", census):
            # The thunk writes started_w and the guardian-side drain/cancel hooks
            # write the events file: both must be DECLARED under the fd allowlist.
            result = _opf_emit.run_bounded(thunk, timeout_s=0.25,
                                           keep_fds=(started_w, events.fileno()))
        finished = time.monotonic()
        elapsed = finished - start
        events.seek(0)
        rows = [json.loads(line) for line in events]
        cancellations = [row[1] for row in rows if row[0] == "cancel"]
        drains = [row for row in rows if row[0] == "drain"]
        cancelled = min(cancellations) if cancellations else None
        # Execution latency ends at the observed signal, never at cleanup end.
        execution_ok = (cancelled is not None and
                        0.25 <= cancelled - start < _DEADLINE_EXECUTION_BOUND)
        # Cleanup is checked against its own declared deadline. One second is
        # allowed separately for receipt delivery and parent scheduling.
        cleanup_limit = max([row[2] for row in drains] or
                            [(cancelled or start) + _opf_emit._FIXTURE_CLEANUP_GRACE])
        cleanup_ok = (all(row[1] <= row[2] for row in drains)
                      and finished <= cleanup_limit + 1)
        if mode == "transient-census":
            # The old combined 1.5 s assertion rejects this passing cleanup.
            assert elapsed > 1.5 and drains, (elapsed, rows)
        # The helper owns cleanup. A diagnostic never sends another signal.
        reaped = _opf_emit._fixture_child_reaped(child[0])
        os.set_blocking(started_r, False)
        reached = os.read(started_r, 200) == b"started"
        ok = (result == "TIMEOUT" and execution_ok and cleanup_ok
              and reached and reaped)
    finally:
        os.close(started_r)
        os.close(started_w)
        events.close()
    print("opf watchdog deadline:", mode, "PASS" if ok else "FAIL",
          result, "execution", None if cancelled is None else cancelled - start,
          "total", elapsed, "cleanup", cleanup_ok, "reaped", reaped)
    return EXIT_OK if ok else EXIT_FINDING


def _watchdog_safety_case(mode):
    """Private process: exercise ownership, descriptor bounds, and the reap discriminator."""
    import errno
    import signal
    import time
    from unittest.mock import patch
    import _opf_emit

    real_pipe, real_fork, real_wait, real_waitid = os.pipe, os.fork, os.waitpid, os.waitid
    pipes, children, kills, stolen = [], [], [], []
    saved = signal.getsignal(signal.SIGCHLD)

    def pipe():
        pair = real_pipe()
        pipes.extend(pair)
        return pair

    def fork():
        pid = real_fork()
        if pid:
            children.append(pid)
        return pid

    def reaper(*_):
        try:
            pid, _status = real_wait(-1, os.WNOHANG)
            if pid:
                stolen.append(pid)
        except ChildProcessError:
            pass

    def steal_wait(pid, options):
        if options == os.WNOHANG:
            stolen.append(real_wait(pid, 0)[0])
        return real_wait(pid, options)                  # real ECHILD after a competing reap

    def steal_cleanup(*args):
        stolen.append(real_wait(children[0], 0)[0])
        return real_waitid(*args)                       # real ECHILD at the cleanup ownership probe

    def closed():
        for fd in pipes:
            try:
                os.fstat(fd)
            except OSError as exc:
                if exc.errno == errno.EBADF:
                    continue
                raise
            return False
        return True

    if mode == "lost-cleanup":
        return _watchdog_completion_case("no-signal-echild")

    if mode == "liveness-permission":
        real_kill = os.kill
        def denied_probe(pid, sig):
            if sig == 0:
                raise PermissionError("recycled PID")
            return real_kill(pid, sig)
        with patch.object(os, "kill", denied_probe):
            return _watchdog_deadline_case("pipe-stall")

    if mode == "missing-reap":
        original = _opf_emit.run_bounded
        def skip_reap(child):
            # Close the control channel but leave the guardian unreaped: the
            # bounded-close collector is exactly what this mutant removes.
            child.control.close()
            child.peer.close()
        def skip_poll(child):
            return None
        def mutant(*args, **kwargs):
            with patch.object(_opf_emit._FixtureProcess, "close", skip_reap), \
                    patch.object(_opf_emit._FixtureProcess, "poll", skip_poll):
                return original(*args, **kwargs)
        with patch.object(_opf_emit, "run_bounded", mutant):
            return (EXIT_OK if _watchdog_deadline_case("pipe-stall") == EXIT_FINDING
                    else EXIT_FINDING)

    with patch.object(os, "pipe", pipe), patch.object(os, "fork", fork):
        if mode in ("ignored-chld", "reaper-chld", "lost-reap", "lost-cleanup"):
            disposition = (signal.SIG_IGN if mode == "ignored-chld"
                           else reaper if mode == "reaper-chld" else signal.SIG_DFL)
            signal.signal(signal.SIGCHLD, disposition)
            try:
                from contextlib import ExitStack
                with ExitStack() as stack:
                    stack.enter_context(patch.object(
                        os, "kill", lambda *args: kills.append(("kill", args))))
                    stack.enter_context(patch.object(
                        os, "killpg", lambda *args: kills.append(("killpg", args))))
                    if mode == "lost-reap":
                        stack.enter_context(patch.object(os, "waitpid", steal_wait))
                    elif mode == "lost-cleanup":
                        stack.enter_context(patch.object(os, "waitpid", return_value=(0, 0)))
                        stack.enter_context(patch.object(os, "waitid", steal_cleanup))
                    result = _opf_emit.run_bounded(lambda: "OK", timeout_s=0.1)
                expected = ("SETUP-ERROR:ChildStatusUnavailable:" if mode.startswith("lost-")
                            else "SETUP-ERROR:ChildOwnership")
                matches = result.startswith(expected) if mode.startswith("lost-") else result == expected
                ok = (matches and not kills and closed()
                      and signal.getsignal(signal.SIGCHLD) == disposition)
                ok = ok and (stolen == children and len(children) == 1 if mode.startswith("lost-")
                             else not children and not pipes)
            finally:
                signal.signal(signal.SIGCHLD, saved)
        elif mode == "high-fd":
            fds = []
            try:
                while len(fds) <= 1024 or fds[-1] <= 1024:
                    fds.append(os.open(os.devnull, os.O_RDONLY))
                result = _opf_emit.run_bounded(lambda: "OK", timeout_s=1)
                ok = result == "OK" and min(pipes) > 1024 and closed()
            finally:
                for fd in fds:
                    os.close(fd)
        elif mode == "huge-timeout":
            result = _opf_emit.run_bounded(lambda: "UNBOUNDED", timeout_s=10**400)
            ok = result.startswith("SETUP-ERROR:") and not pipes and not children and closed()
        elif mode == "fork-error":
            with patch.object(os, "fork", side_effect=RuntimeError("fork setup")):
                result = _opf_emit.run_bounded(lambda: "UNBOUNDED")
            ok = result == "SETUP-ERROR:RuntimeError:fork setup" and len(pipes) == 2 and closed()
        elif mode == "poll-error":
            import select
            real_poll, calls = select.poll, []
            def poll_fault():
                calls.append(None)
                if len(calls) == 2:  # collection, after guardian startup
                    raise RuntimeError("poll setup")
                return real_poll()
            with patch.object(select, "poll", poll_fault):
                try:
                    _opf_emit.run_bounded(lambda: "OK")
                except RuntimeError:
                    result = "ERROR"
                else:
                    result = "RETURNED"
            reaped = False
            try:
                real_wait(children[0], os.WNOHANG)
            except ChildProcessError:
                reaped = True
            ok = result == "ERROR" and reaped and closed()
        else:
            raise ValueError("unknown watchdog safety case: " + mode)
    print("opf watchdog safety:", mode, "PASS" if ok else "FAIL", result, kills)
    return EXIT_OK if ok else EXIT_FINDING


def _watchdog_launcher_case(label, disposition):
    """Inject exit-37 subjects into both watchdog matrices and the completion launcher."""
    import contextlib
    import io
    import signal
    import subprocess
    from unittest.mock import patch

    from _opf_emit import run_status_owned

    def shared():
        try:
            run_status_owned([sys.executable, "-I", "-B", "-c", "raise SystemExit(37)"],
                             fixture_id="launcher/exit-37", check=True, capture_output=True, timeout=5)
        except (RuntimeError, subprocess.CalledProcessError):
            return EXIT_FINDING
        return EXIT_OK

    runner = {"isolation": _watchdog_isolation_self_test,
              "regression": _watchdog_regression_self_test, "shared": shared}[label]
    import _opf_emit
    real_run = _opf_emit._run_fixture_process
    statuses = []

    def failure(*args, **kwargs):
        result = real_run([sys.executable, "-I", "-B", "-c", "raise SystemExit(37)"],
                          timeout=5)
        statuses.append(result.returncode)
        if kwargs.get("check") and result.returncode:
            raise subprocess.CalledProcessError(result.returncode, result.args)
        return result

    saved = signal.getsignal(signal.SIGCHLD)
    hostile = signal.SIG_IGN if disposition == "ignored" else lambda *_: None
    try:
        signal.signal(signal.SIGCHLD, signal.SIG_DFL)
        with patch.object(_opf_emit, "_run_fixture_process", failure), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            control = runner()
            control_ok = control == EXIT_FINDING and bool(statuses) and set(statuses) == {37}
            statuses.clear()
            signal.signal(signal.SIGCHLD, hostile)
            result = runner()
        ok = (control_ok and result == EXIT_FINDING and not statuses
              and signal.getsignal(signal.SIGCHLD) == hostile)
    finally:
        signal.signal(signal.SIGCHLD, saved)
    print("opf watchdog launcher:", label, disposition, "PASS" if ok else "FAIL",
          "refused", result, "launched statuses", statuses)
    return EXIT_OK if ok else EXIT_FINDING


def _watchdog_completion_case(mode):
    """Run each irreversible audit-hook/disposition experiment in its own fixture process."""
    import json
    import signal
    import subprocess
    import tempfile
    import threading
    from unittest.mock import patch
    import _opf_emit as emit
    import _optlevel

    command = [sys.executable, "-I", "-B", "-c", "return 0"]

    def launch(code="return 0", **kwargs):
        return emit.run_status_owned([*command[:4], code],
                                     fixture_id="completion/" + mode, timeout=10, **kwargs)

    def refuses(error, call):
        try:
            call()
        except error:
            return
        raise AssertionError("fixture was accepted: " + mode)

    if mode == "guardian-error":
        import errno
        caller, real_pidfd = os.getpid(), emit._fixture_pidfd

        def fail_pidfd(target):
            if os.getpid() != caller:
                raise OSError(errno.EIO, "injected guardian exception")
            return real_pidfd(target)

        def diagnose():
            try:
                launch()
            except emit.ChildStatusUnavailable as exc:
                message = str(exc)
                assert "OSError" in message and '"errno": 5' in message, message
                assert "injected guardian exception" in message, message
                assert "raw wait status=32000" in message and "exitcode=125" in message, message
                return
            raise AssertionError("guardian failure was accepted")

        with patch.object(emit, "_fixture_pidfd", fail_pidfd):
            diagnose()
            # Flip the caller's reader back to the old evidence-discarding path.
            with patch.object(emit._FixtureProcess, "_read_report",
                              side_effect=emit.ChildStatusUnavailable("fixture guardian failed")):
                refuses(AssertionError, diagnose)
        with patch.object(emit, "_fixture_subreaper",
                          side_effect=OSError(errno.EIO, "injected guardian exception")):
            diagnose()  # before READY must carry the same evidence
    elif mode == "bounded-diagnostics":
        def check():
            result = emit.run_bounded(lambda: "OK", timeout_s=2)
            assert result.startswith("SETUP-ERROR:ChildStatusUnavailable:"), result
            for text in ("OSError", '"errno": 5', "QA15-CAUSE",
                         "raw wait status=32000", "exitcode=125"):
                assert text in result, result

        for boundary in ("_fixture_subreaper", "_fixture_close_all_except"):
            with patch.object(emit, boundary, side_effect=OSError(5, "QA15-CAUSE")):
                check()
                with patch.object(emit, "_bounded_setup_error",
                                  side_effect=lambda exc: "SETUP-ERROR:" + type(exc).__name__):
                    refuses(AssertionError, check)
        # Also retain evidence from close(), not only startup and poll().
        real_close = emit._FixtureProcess.close
        def fail_close(child):
            real_close(child)
            raise emit.ChildStatusUnavailable("raw wait status=32000; QA15-CLOSE")
        def check_close():
            result = emit.run_bounded(lambda: "OK", timeout_s=2)
            assert "QA15-CLOSE" in result and "raw wait status=32000" in result, result
        with patch.object(emit._FixtureProcess, "close", fail_close):
            check_close()
            with patch.object(emit, "_bounded_setup_error",
                              side_effect=lambda exc: "SETUP-ERROR:" + type(exc).__name__):
                refuses(AssertionError, check_close)
    elif mode == "cleanup-budget":
        import time
        # A controlled clock discriminates the remaining-deadline policy without
        # making the test itself depend on host scheduling.
        def policy():
            with patch.object(time, "monotonic", return_value=100):
                assert emit._fixture_cleanup_deadline(120) == 120
                assert emit._fixture_cleanup_deadline(99) == 100 + emit._FIXTURE_CLEANUP_GRACE
                assert emit._fixture_cleanup_deadline() == 100 + emit._FIXTURE_CLEANUP_GRACE
        policy()
        with patch.object(emit, "_fixture_cleanup_deadline",
                          side_effect=lambda deadline=None: time.monotonic() + 5):
            refuses(AssertionError, policy)
        # Observe forwarding and error-path reuse in the real guardian. Fail the
        # first drain; let the second perform real cleanup under the SAME bound.
        with tempfile.TemporaryDirectory(prefix="opf-budget-") as budget_dir:
            # The instrumented budget/drain run in the GUARDIAN, which does not
            # inherit this caller's descriptors (fd allowlist): log by PATH.
            log_path = Path(budget_dir, "log")
            def append(entry):
                with open(log_path, "a", encoding="ascii") as sink:
                    sink.write(entry + "\n")
            real_drain = emit._fixture_drain
            attempts = []
            real_budget = emit._fixture_cleanup_deadline
            def budget(execution_deadline=None):
                bound = real_budget(execution_deadline)
                append(json.dumps([execution_deadline, bound]))
                return bound
            def retry(subject, subject_fd=None, *, deadline=None):
                attempts.append(deadline)
                append(repr(deadline))
                if len(attempts) == 1:
                    raise OSError(5, "QA15-RETRY")
                return real_drain(subject, subject_fd, deadline=deadline)
            with patch.object(emit, "_fixture_drain", retry), \
                    patch.object(emit, "_fixture_cleanup_deadline", budget):
                refuses(emit.ChildStatusUnavailable, launch)
            bounds = [json.loads(line)
                      for line in log_path.read_text(encoding="ascii").splitlines()]
            assert len(bounds) == 3 and bounds[0][0] is not None, bounds
            assert bounds[0][1] == bounds[1] == bounds[2], bounds
        # Empty census never licenses completion, even when its budget expires.
        with patch.object(os, "waitid", return_value=None), \
                patch.object(emit, "_fixture_signal", return_value=True), \
                patch.object(emit, "_fixture_children", return_value=[]), \
                patch.object(time, "monotonic", side_effect=[100, 102]), \
                patch.object(time, "sleep"):
            refuses(emit.ChildStatusUnavailable,
                    lambda: emit._fixture_drain(123, deadline=101))
    elif mode == "deadline-flips":
        real_run = emit.run_bounded
        def late(thunk, **kwargs):
            # The forced timeout sits ABOVE the measured execution bound,
            # so the late parent deadline stays red under any load the
            # bound itself tolerates (F-335-LOAD-FLAKE-PIPE-STALL).
            return real_run(thunk, **dict(
                kwargs, timeout_s=_DEADLINE_EXECUTION_BOUND + 1))
        with patch.object(emit, "run_bounded", late):
            assert _watchdog_deadline_case("pipe-stall") == EXIT_FINDING
        import time
        with patch.object(emit, "_fixture_cleanup_deadline",
                          side_effect=lambda deadline=None: time.monotonic() - 1):
            assert _watchdog_deadline_case("pipe-stall") == EXIT_FINDING
        real_signal, first = emit._fixture_signal, [True]
        def census_only(*args, **kwargs):
            if first[0]:
                first[0] = False
                return False
            return real_signal(*args, **kwargs)
        # The swallowed first send provably delays the observed cancel past
        # the 1.6 s transient-census window, so THIS red keys on that
        # window, not on the load-tolerant shared bound: pinning the bound
        # to the window keeps the mutant red under any load (load only
        # delays the retried send further; F-335-LOAD-FLAKE-PIPE-STALL).
        import sys as sys_module
        runner_module = sys_module.modules[_watchdog_deadline_case.__module__]
        with patch.object(emit, "_fixture_signal", census_only), \
                patch.object(runner_module, "_DEADLINE_EXECUTION_BOUND", 1.6):
            assert _watchdog_deadline_case("transient-census") == EXIT_FINDING
    elif mode == "subject-setup":
        real_subreaper, real_setsid = emit._fixture_subreaper, os.setsid
        in_guardian = [False]
        def mark():
            real_subreaper()
            in_guardian[0] = True
        def fail_subject():
            if in_guardian[0]:
                raise OSError(5, "QA15-SUBJECT")
            return real_setsid()

        def check():
            with tempfile.TemporaryFile() as err:
                saved = os.dup(2)
                try:
                    os.dup2(err.fileno(), 2)
                    with patch.object(emit, "_fixture_subreaper", mark), \
                            patch.object(os, "setsid", fail_subject):
                        assert emit.run_bounded(lambda: "OK", timeout_s=2) == "CHILD-DIED"
                finally:
                    os.dup2(saved, 2)
                    os.close(saved)
                err.seek(0)
                diagnostic = err.read()
                assert b"Traceback" in diagnostic and b"OSError: [Errno 5] QA15-SUBJECT" in diagnostic
        check()
        import traceback
        with patch.object(traceback, "print_exc"):
            refuses(AssertionError, check)
    elif mode == "empty-children":
        real_read, real_drain = Path.read_text, emit._fixture_drain

        def empty(path, *args, **kwargs):
            if str(path).endswith("/children"):
                return ""
            return real_read(path, *args, **kwargs)

        first = [True]
        def old_census(subject, subject_fd=None, **kwargs):
            if first[0]:
                first[0] = False
                children = Path("/proc/self/task/{}/children".format(os.getpid()))
                if not children.read_text(encoding="ascii").split():
                    raise emit.ChildStatusUnavailable("child census disagrees with waitid")
            return real_drain(subject, subject_fd, **kwargs)

        with patch.object(Path, "read_text", empty):
            # Flip: the old immediate-fatal census rejects this exact stimulus.
            with patch.object(emit, "_fixture_drain", old_census):
                refuses(emit.ChildStatusUnavailable, launch)
            assert launch().returncode == 0
        # A transiently empty PPID census also needs a retry, never a clean pass.
        first, real_children = [True], emit._fixture_children
        def transient():
            if first[0]:
                first[0] = False
                return []
            return real_children()
        with patch.object(emit, "_fixture_children", transient):
            assert launch().returncode == 0
    elif mode == "audit-ignore":
        # A hostile os.fork audit hook can no longer flip SIGCHLD from the fork
        # itself: the fork runs on the private launcher thread, where CPython
        # refuses signal.signal outright (the old hijack is dead by construction).
        # The surviving window is the caller (main) thread between the
        # precondition check and GO: the hook arms on os.fork and the flip lands
        # at the READY read, where the launch-window re-check must refuse it.
        events = []

        def ignore(event, args):
            if event == "os.fork":
                events.append(event)

        sys.addaudithook(ignore)
        real_read = os.read

        def hijack(fd, size):
            if events and signal.getsignal(signal.SIGCHLD) == signal.SIG_DFL:
                signal.signal(signal.SIGCHLD, signal.SIG_IGN)
            return real_read(fd, size)

        with patch.object(os, "read", hijack):
            refuses(emit.ChildStatusUnavailable, lambda: launch("import os; os._exit(37)"))
        assert events and signal.getsignal(signal.SIGCHLD) == signal.SIG_IGN
    elif mode == "reaper":
        real_wait = emit._fixture_wait
        for code in ("return 0", "import os; os._exit(37)"):
            stolen = []

            def compete(pid, flags):
                if not stolen:
                    worker = threading.Thread(target=lambda: stolen.append(os.waitpid(pid, 0)))
                    worker.start()
                    worker.join(5)
                    assert not worker.is_alive(), "reaper fixture exceeded its bound"
                return real_wait(pid, flags)

            with patch.object(emit, "_fixture_wait", compete):
                refuses(emit.ChildStatusUnavailable, lambda: launch(code))
            assert len(stolen) == 1 and os.waitstatus_to_exitcode(stolen[0][1]) == 0
            assert signal.getsignal(signal.SIGCHLD) == signal.SIG_DFL
    elif mode in ("nonce", "fixture-id", "status"):
        real_run = emit._run_fixture_process
        seen = []

        def alter(argv, **kwargs):
            payload = json.loads(argv[-1])
            seen.append(payload[0])
            if mode == "status":
                record = "\nOPF-FIXTURE " + json.dumps(payload[:2]) + "\n"
                argv = [*command[:4], "import sys; sys.stdout.write(" + repr(record)
                        + "); sys.stdout.flush(); raise SystemExit(37)"]
            else:
                payload[0 if mode == "nonce" else 1] += "-wrong"
                argv = [*argv[:-1], json.dumps(payload)]
            return real_run(argv, **kwargs)

        with patch.object(emit, "_run_fixture_process", alter):
            for _ in range(2):
                refuses(subprocess.CalledProcessError if mode == "status" else emit.FixtureIncomplete,
                        launch)
        assert len(set(seen)) == 2, "each launch needs a fresh nonce"
    elif mode == "early-exit":
        real_run = emit._run_fixture_process
        with tempfile.TemporaryDirectory(prefix="opf-site-exit-") as directory:
            Path(directory, "sitecustomize.py").write_text(
                "import os\nprint('EARLY', flush=True)\nos._exit(0)\n", encoding="utf-8")
            env = dict(os.environ, PYTHONPATH=directory)
            result = launch("print('RAN'); return 0", env=env)
            assert result.stdout == b"RAN\n"

            def unisolated(argv, **kwargs):
                return real_run([arg for arg in argv if arg != "-I"], **kwargs)

            # Removing isolation really reaches sitecustomize; the missing completion
            # must still refuse its zero exit. This also discriminates the record check.
            with patch.object(emit, "_run_fixture_process", unisolated):
                refuses(emit.FixtureIncomplete, lambda: launch(env=env))
        refuses(emit.FixtureIncomplete, lambda: launch("import os; os._exit(0)"))
    elif mode == "premature-exit":
        launch("return 0")  # normal-return control
        for code in ("raise SystemExit()", "raise SystemExit(0)",
                     "raise RuntimeError('before postconditions')"):
            refuses(emit.FixtureIncomplete, lambda: launch(code))
        # CLI exit-status subjects have an explicit, separately supervised path.
        launch("raise SystemExit(2)", process_fixture=True, expected_returncode=2)
    elif mode in ("nested-timeout", "nested-cancel"):
        import time
        with tempfile.TemporaryDirectory(prefix="opf-tree-") as directory:
            markers = [Path(directory, name) for name in ("subject", "descendant")]
            # Deliberate fork/session escape is a tree-cleanup stimulus, not a verdict.
            subject = ("import os, time; from pathlib import Path; "
                       "pid = os.fork(); "
                       "os.setsid() if pid == 0 else None; "
                       "name = 'descendant' if pid == 0 else 'subject'; "
                       "scratch = Path(" + repr(directory) + ", name + '.tmp'); "
                       "scratch.write_text(str(os.getpid()), encoding='ascii'); "
                       "scratch.rename(Path(" + repr(directory) + ", name)); "  # atomic: never a partial PID
                       "time.sleep(60)")
            # Writer half: a tripwire against an accidental edit of the marker
            # lines above, not a proof of atomic publication. It requires the
            # name + '.tmp' scratch path, the prefix scratch.write_text( and the
            # scratch.rename to the final name once each and in that order, and
            # no other occurrence of the text write_text( or Path(directory, name)
            # anywhere in the subject; it does not otherwise check the write's
            # arguments. It matches exact text only, so anything spelled
            # differently is outside it: another spelling of a call, the final
            # path or a binding (spacing, an alias, a rebinding of scratch), a
            # write by any method other than write_text, an os-level call, exec
            # or a shell.
            text = subject.replace(repr(directory), "directory")  # TMPDIR-blind
            steps = ["scratch = Path(directory, name + '.tmp'); ",
                     "scratch.write_text(", "scratch.rename(Path(directory, name)); "]
            found = [text.find(step) for step in steps]
            if not (all(text.count(step) == 1 for step in steps)
                    and -1 < found[0] < found[1] < found[2]
                    and text.count("write_text(") == 1
                    and text.count("Path(directory, name)") == 1):
                raise AssertionError("marker write lines changed: " + text)

            class Cancelled(RuntimeError):
                """The nested-cancel stimulus; no other error may stand in for it."""

            real_wait = emit._fixture_wait
            observed = []

            def observe(pid, flags):
                if not observed:
                    try:  # a marker counts only once its pid parses; empty is not ready yet
                        pids = [int(path.read_text(encoding="ascii")) for path in markers]
                    except (FileNotFoundError, ValueError):
                        pids = []
                    observed.extend(pids)
                    if pids and mode == "nested-cancel":
                        raise Cancelled("cancel with nested subject running")
                return real_wait(pid, flags)

            # Marker-race control: a marker that exists but is still empty (created,
            # pid not yet written) is no started subject; the observer keeps waiting.
            markers[0].write_text(str(os.getpid()), encoding="ascii")
            markers[1].touch()
            refuses(emit.ChildStatusUnavailable, lambda: observe(os.getpid(), os.WNOHANG))
            assert not observed, "an empty marker was read as a started subject"
            for path in markers:
                path.unlink()
            with patch.object(emit, "_fixture_wait", observe):
                refuses(subprocess.TimeoutExpired if mode == "nested-timeout" else Cancelled,
                        lambda: emit.run_status_owned(
                            [*command[:4], subject], fixture_id="tree/" + mode,
                            process_fixture=True, timeout=2))
            assert len(observed) == 2, "nested subject never started"
            for pid in observed:
                assert not Path("/proc", str(pid)).exists(), "descendant survived/unreaped"
    elif mode == "no-signal-echild":
        import time
        # The immediately-exiting subject leaves the exited guardian as the raw
        # zombie stimulus for the ownership boundary.
        child = emit._FixtureProcess(time.monotonic() + 5, subject=lambda: None)
        pid = child.start()
        os.waitid(os.P_PID, pid, os.WEXITED | os.WNOWAIT)
        # Positive control: an owned leader permits signalling.
        with patch.object(os, "kill") as kill:
            assert emit._fixture_signal(pid, signal.SIGKILL, group=False)
            assert kill.called
        os.waitpid(pid, 0)
        child.collected = True  # deliberate external collection of the guardian
        child.close()
        # Negative control covers numeric, group AND pidfd paths after real ECHILD.
        with patch.object(os, "getpgid", return_value=pid), \
                patch.object(os, "kill") as kill, patch.object(os, "killpg") as killpg, \
                patch.object(signal, "pidfd_send_signal") as pidfd_signal:
            assert emit._fixture_signal(pid, signal.SIGKILL) is False
            assert emit._fixture_signal(pid, signal.SIGKILL, 123) is False
            assert not kill.called and not killpg.called and not pidfd_signal.called
    elif mode == "cleanup-cancel":
        real_wait = emit._fixture_wait
        seen = []

        def cancel(pid, flags):
            if not seen:
                seen.append(pid)
                raise RuntimeError("cancel collection")
            return real_wait(pid, flags)

        with patch.object(emit, "_fixture_wait", cancel):
            refuses(RuntimeError, lambda: launch("import time; time.sleep(60)"))
        pid = seen[0]
        try:
            waited, _ = os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:
            waited = None
        assert waited is None, "cancelled collector abandoned its fixture"
    elif mode == "cleanup-reaped":
        import time
        child = emit._FixtureProcess(time.monotonic() + 5, subject=lambda: None)
        pid = child.start()                   # deliberate zombie stimulus, not a test verdict
        os.waitid(os.P_PID, pid, os.WEXITED | os.WNOWAIT)
        with patch.object(os, "kill") as kill:
            assert emit._fixture_child_reaped(pid) is False
        child.collected = True  # the assertion above deliberately consumed its status
        child.close()
        assert not kill.called, "cleanup signalled a PID after its probe reaped it"
        assert emit._fixture_child_reaped(pid) is True
        with patch.object(os, "waitpid", side_effect=OSError(5, "fixture EIO")), \
                patch.object(os, "kill") as kill:
            refuses(emit.ChildStatusUnavailable, lambda: emit._fixture_child_reaped(pid))
        assert not kill.called
    elif mode == "overdue-success":
        import time
        # QA16 F1: an exit first observed after the deadline is TIMEOUT, whatever
        # order the guardian and the collector were scheduled in. The guardian
        # stops itself before supervision; the collector is gated before its
        # first poll; the subject finishes only after the recorded deadline has
        # decisively expired. Barriers order every step: no wall-clock race.
        caller = os.getpid()
        with tempfile.TemporaryDirectory(prefix="opf-overdue-") as directory:
            release = Path(directory, "release")
            guardian_file = Path(directory, "guardian")
            recorded = []
            real_init = emit._FixtureProcess.__init__

            def record_init(child, deadline, **kwargs):
                recorded.append(deadline)
                return real_init(child, deadline, **kwargs)

            real_ack = emit._fixture_ack_subject

            def stop_guardian(fd):
                # The subject is RELEASED (receipt sent, ownership acknowledged)
                # before the guardian stops, so it can complete while the guardian
                # is not supervising.
                real_ack(fd)
                if os.getpid() != caller:
                    scratch = Path(directory, "guardian.tmp")
                    scratch.write_text(str(os.getpid()), encoding="ascii")
                    scratch.rename(guardian_file)  # atomic: never a partial PID
                    os.kill(os.getpid(), signal.SIGSTOP)

            gate = threading.Event()
            real_poll = emit._FixtureProcess.poll

            def gated_poll(child):
                if os.getpid() == caller and not gate.is_set():
                    assert gate.wait(30), "collector gate was never released"
                return real_poll(child)

            def state(target):
                try:
                    stat = Path("/proc", str(target), "stat").read_bytes()
                except (FileNotFoundError, ProcessLookupError):
                    return None
                return stat.rsplit(b")", 1)[1].split()[0].decode("ascii")

            def await_state(target, wanted, note):
                bound = time.monotonic() + 30
                while state(target) not in wanted:
                    assert time.monotonic() < bound, note
                    time.sleep(0.005)

            body = ("import os, time\n"
                    "bound = time.monotonic() + 30\n"
                    "while not os.path.exists(" + repr(str(release)) + "):\n"
                    "    if time.monotonic() >= bound:\n"
                    "        return 1\n"
                    "    time.sleep(0.005)\n"
                    "return 0")
            outcome = []

            def run():
                try:
                    outcome.append(("returned", emit.run_status_owned(
                        [*command[:4], body], fixture_id="completion/" + mode, timeout=1)))
                except BaseException as exc:  # noqa: BLE001 - the verdict channel
                    outcome.append(("raised", exc))

            worker = threading.Thread(target=run, daemon=True)
            with patch.object(emit._FixtureProcess, "__init__", record_init), \
                    patch.object(emit._FixtureProcess, "poll", gated_poll), \
                    patch.object(emit, "_fixture_ack_subject", stop_guardian):
                worker.start()
                try:
                    bound = time.monotonic() + 30
                    while not guardian_file.exists():
                        assert time.monotonic() < bound, "guardian never reached its stop point"
                        time.sleep(0.005)
                    gpid = int(guardian_file.read_text(encoding="ascii"))
                    await_state(gpid, {"T"}, "guardian did not stop")
                    assert recorded, "the fixture deadline was not observed"
                    while time.monotonic() < recorded[0] + 0.2:
                        time.sleep(0.005)
                    release.write_text("go", encoding="ascii")
                    children = Path("/proc", str(gpid), "task", str(gpid), "children")
                    subject = int(children.read_text(encoding="ascii").split()[0])
                    await_state(subject, {"Z"}, "subject did not finish after release")
                    os.kill(gpid, signal.SIGCONT)
                    await_state(gpid, {"Z", None}, "guardian did not exit after resume")
                finally:
                    gate.set()
                worker.join(30)
            assert not worker.is_alive(), "the collector never returned"
            assert outcome and outcome[0][0] == "raised" and isinstance(
                outcome[0][1], subprocess.TimeoutExpired), (
                "an overdue completion was accepted as success: " + repr(outcome))
    elif mode == "launch-ownership":
        import time
        # QA16 F2 / QA17 F3: fork-to-ownership is uninterruptible by interpreter
        # construction -- the fork runs on a private launcher thread, where CPython
        # never raises asynchronous signal exceptions -- so a cancellation delivered
        # at ANY caller-side bytecode still reaps the guardian to ECHILD. The old
        # single-statement packing was broken between CALL and STORE_ATTR by a real
        # SIGINT; no statement shape can close that window, only thread ownership.
        real_fork = os.fork
        fork_threads = []

        def observing_fork():
            fork_threads.append(threading.current_thread() is threading.main_thread())
            return real_fork()

        injected = []

        def interrupt(frame, event, arg):
            if event != "call" or frame.f_code.co_name != "_start":
                return None

            def local(frame, event, arg):
                if event == "line" and not injected:
                    target = frame.f_locals.get("self")
                    # Once the launcher has recorded the fork result, the next
                    # caller bytecode is exactly where a real SIGINT would land.
                    if target is not None and target.pid is not None:
                        injected.append(target.pid)
                        raise KeyboardInterrupt("cancel after launch, before READY")
                return local

            return local

        child = emit._FixtureProcess(time.monotonic() + 30, subject=lambda: None)
        with patch.object(os, "fork", observing_fork):
            sys.settrace(interrupt)
            try:
                refuses(KeyboardInterrupt, child.start)
            finally:
                sys.settrace(None)
        assert fork_threads == [False], "the fork did not run on a private launcher thread"
        assert injected, "the launch window was never reached"
        gpid = injected[0]
        assert emit._fixture_child_reaped(gpid) is True, "cancellation leaked the guardian"

        # Flip: run the fork INLINE on the caller thread (bypassing the parked
        # launcher) and the old interruptible fork-to-store window comes back --
        # a cancellation between the fork and its store leaks a guardian that
        # close() can never know.
        forked = []

        def inline_interrupt(frame, event, arg):
            if event != "call" or frame.f_code.co_name != "_launch_fork":
                return None

            def local(frame, event, arg):
                if event == "line" and not forked:
                    pid = frame.f_locals.get("pid")
                    if pid and frame.f_locals["self"].pid is None:
                        forked.append(pid)
                        raise KeyboardInterrupt("cancel between fork and its store")
                return local

            return local

        flip_child = emit._FixtureProcess(time.monotonic() + 30, subject=lambda: None)
        sys.settrace(inline_interrupt)
        try:
            refuses(KeyboardInterrupt, flip_child._launch_fork)
        finally:
            sys.settrace(None)
        assert forked, "the inline flip never reached the fork boundary"
        leaked_pid = forked[0]
        assert flip_child.pid is None, "the inline flip still recorded ownership"
        leaked = True
        try:
            os.waitid(os.P_PID, leaked_pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
        except ChildProcessError:
            leaked = False
        if leaked:
            os.kill(leaked_pid, signal.SIGKILL)  # hygiene for the demonstrated leak
            os.waitpid(leaked_pid, 0)
        assert leaked, "the inline flip did not reproduce the interruptible window"
        flip_child.close()
    elif mode == "launch-cancel":
        import time
        # QA18 codex F1: a cancellation delivered while the launcher thread is
        # being CREATED (inside threading.Thread.start, before its bootstrap runs)
        # must neither be replaced by a cleanup RuntimeError, nor leak the control
        # sockets, nor leak a guardian. The launcher is parked at construction and
        # released by flags, so close() never joins an unstarted thread, and a
        # cancelled construction aborts the launch and re-raises the cancellation.
        real_start = threading.Thread.start

        def open_fds():
            live = set()
            for name in os.listdir("/proc/self/fd"):
                try:
                    os.fstat(int(name))
                except OSError:
                    continue
                live.add(int(name))
            return live

        def no_children():
            try:
                os.waitid(os.P_ALL, 0, os.WEXITED | os.WNOHANG | os.WNOWAIT)
            except ChildProcessError:
                return True
            return False

        def interrupted_start(thread):
            if thread.name == "opf-fixture-launcher":
                # A REAL SIGINT: the launcher is created under the masked
                # bracket (fix 2x), so the KeyboardInterrupt stays pending
                # through Thread.start and materializes at the bracket's
                # restore -- still inside construction, which must abort the
                # launch and re-raise the cancellation unreplaced.
                signal.raise_signal(signal.SIGINT)
            return real_start(thread)

        def cancelled_run():
            # The caught exception is RETURNED (its traceback pins the half-built
            # fixture), so descriptor parity below proves the machinery itself
            # released everything, not a later refcount finalizer.
            try:
                emit.run_bounded(lambda: "UNREACHED", timeout_s=5)
            except KeyboardInterrupt as exc:
                return exc
            except BaseException as exc:  # noqa: BLE001 - the verdict channel
                raise AssertionError("the cancellation was replaced: " + repr(exc))
            raise AssertionError("the cancellation was swallowed")

        assert no_children(), "children live before the launch-cancel probe"
        # Prime the one-time guardian-dependency preload (a first ctypes import
        # keeps a libffi mapping descriptor open for the process's life) so the
        # descriptor parity below measures ONLY what a cancelled launch leaks.
        emit._fixture_preload()
        before = open_fds()
        with patch.object(threading.Thread, "start", interrupted_start):
            held = cancelled_run()
        assert open_fds() == before, "a cancelled launch leaked descriptors"
        assert no_children(), "a cancelled launch leaked a child"
        held = None

        # Thread exhaustion is the SETUP-ERROR sentinel (the launcher analog of
        # the pinned fork-error discipline), never a raw cleanup RuntimeError.
        def exhausted_start(thread):
            if thread.name == "opf-fixture-launcher":
                raise RuntimeError("can't start new thread")
            return real_start(thread)

        with patch.object(threading.Thread, "start", exhausted_start):
            result = emit.run_bounded(lambda: "UNREACHED", timeout_s=5)
        assert result == "SETUP-ERROR:RuntimeError:can't start new thread", result
        assert open_fds() == before and no_children(), "thread exhaustion leaked"

        # Flip: without the construction abort, the cancellation leaks this
        # call's control sockets and report.
        flipped = []
        with patch.object(emit, "_fixture_abort_launch", flipped.append):
            with patch.object(threading.Thread, "start", interrupted_start):
                held = cancelled_run()
        leaked = sorted(open_fds() - before)
        assert leaked, "the flip did not reproduce the construction leak"
        held = None
        # Hygiene: under the masked launcher-creation bracket the cancellation
        # lands only after Thread.start returned, so the flip strands a PARKED
        # launcher whose bound target pins the fixture (and its descriptors)
        # beyond any refcount release; the real abort releases both.
        for fixture in flipped:
            emit._fixture_abort_launch(fixture)
        assert open_fds() == before, "the flip hygiene did not complete"
    elif mode == "fd-hygiene-total":
        import errno
        import time
        # QA16 MINOR-1 + QA17 F4/MINOR-2: EVERY undeclared caller descriptor is
        # closed before a subject can run -- a bare pipe, a plain open file, an
        # UNSTARTED sibling call's endpoints (the construction window: there is no
        # publication step left to race), and a started sibling's -- while this
        # call's declared keep_fds survive and stay usable. Identity, not mere
        # openness: a swept number may be legitimately reused inside the subject.
        bare_r, bare_w = os.pipe()
        keep_r, keep_w = os.pipe()
        plain = tempfile.TemporaryFile()
        unstarted = emit._FixtureProcess(time.monotonic() + 30, subject=lambda: None)
        started = emit._FixtureProcess(time.monotonic() + 30,
                                       subject=lambda: time.sleep(60))
        started.start()
        undeclared = dict((("bare-r", bare_r), ("bare-w", bare_w),
                           ("plain-file", plain.fileno()),
                           ("unstarted-control", unstarted.control.fileno()),
                           ("unstarted-peer", unstarted.peer.fileno()),
                           ("unstarted-report", unstarted.report.fileno()),
                           ("started-control", started.control.fileno()),
                           ("started-report", started.report.fileno())))
        identity = dict((name, (os.fstat(fd).st_dev, os.fstat(fd).st_ino))
                        for name, fd in undeclared.items())
        try:
            def probe():
                leaked = []
                for name, fd in sorted(undeclared.items()):
                    try:
                        stat = os.fstat(fd)
                    except OSError as exc:
                        if exc.errno != errno.EBADF:
                            raise
                        continue
                    if (stat.st_dev, stat.st_ino) == identity[name]:
                        leaked.append(name)
                if leaked:
                    return "LEAKED:" + " ".join(leaked)
                # Positive control: the declared keep_fds are still usable.
                os.write(keep_w, b"K")
                return "CLEAN" if os.read(keep_r, 1) == b"K" else "KEPT-PIPE-BROKEN"

            result = emit.run_bounded(probe, timeout_s=10, keep_fds=(keep_r, keep_w))
            assert result == "CLEAN", result
            # The sweep is guardian-side only: the caller's descriptors stay open.
            os.write(bare_w, b"S")
            assert os.read(bare_r, 1) == b"S", "the bare pipe was closed in the caller"
            # Flip: without the close-all sweep every undeclared descriptor leaks.
            with patch.object(emit, "_fixture_close_all_except", lambda keep: None):
                flipped = emit.run_bounded(probe, timeout_s=10, keep_fds=(keep_r, keep_w))
            assert flipped.startswith("LEAKED:"), flipped
            for name in undeclared:
                assert name in flipped, (name, flipped)
        finally:
            started.close()
            unstarted.close()
            plain.close()
            for fd in (bare_r, bare_w, keep_r, keep_w):
                os.close(fd)
    elif mode == "nested-keep":
        import time
        # QA17 F2, migrated to the explicit-contract refusal: a caller descriptor a
        # NESTED run_bounded thunk relies on is a deterministic ERROR:OSError unless
        # declared through keep_fds, in which case it works -- never a
        # sometimes-working reuse of a stale registry entry.
        with tempfile.TemporaryDirectory(prefix="opf-nested-keep-") as directory:
            probe_path = Path(directory, "probe")
            probe_path.write_text("x", encoding="utf-8")

            def outer(declared):
                fd = os.open(str(probe_path), os.O_RDONLY)
                try:
                    def inner():
                        os.fstat(fd)
                        return "OPEN"
                    return emit.run_bounded(inner, timeout_s=10,
                                            keep_fds=(fd,) if declared else ())
                finally:
                    os.close(fd)

            undeclared = emit.run_bounded(lambda: outer(False), timeout_s=30)
            assert undeclared == "ERROR:OSError", undeclared
            declared = emit.run_bounded(lambda: outer(True), timeout_s=30)
            assert declared == "OPEN", declared
            # Flip: no-op the sweep and the undeclared descriptor leaks into the
            # nested subject.
            with patch.object(emit, "_fixture_close_all_except", lambda keep: None):
                leaked = emit.run_bounded(lambda: outer(False), timeout_s=30)
            assert leaked == "OPEN", leaked
    elif mode == "fd-census":
        import errno
        caller = os.getpid()
        # QA18 codex F4: without an authoritative /proc/self/fd census the sweep
        # cannot be proven complete (a soft-RLIMIT bound does not enumerate
        # descriptors already open above it), so the guardian must REFUSE startup,
        # never run the subject behind a partial sweep.
        spare = os.open(os.devnull, os.O_RDONLY)
        try:
            identity = (os.fstat(spare).st_dev, os.fstat(spare).st_ino)

            def probe():
                try:
                    stat = os.fstat(spare)
                except OSError:
                    return "SWEPT"
                return "LEAKED" if (stat.st_dev, stat.st_ino) == identity else "SWEPT"

            real_listdir = os.listdir

            def denied(path="."):
                # Guardian-side only: the caller's own censuses stay usable.
                if os.getpid() != caller and str(path) == "/proc/self/fd":
                    raise PermissionError(errno.EACCES, "injected census denial")
                return real_listdir(path)

            with patch.object(os, "listdir", denied):
                result = emit.run_bounded(probe, timeout_s=10)
                assert result.startswith("SETUP-ERROR:ChildStatusUnavailable:"), result
                assert "descriptor census unavailable" in result, result
                # Flip: sweeping past the failed census runs the subject with the
                # caller's descriptors intact.
                with patch.object(emit, "_fixture_close_all_except", lambda keep: None):
                    flipped = emit.run_bounded(probe, timeout_s=10)
                assert flipped == "LEAKED", flipped
            # Positive control: with the census available the same undeclared
            # descriptor never reaches the subject.
            assert emit.run_bounded(probe, timeout_s=10) == "SWEPT"
        finally:
            os.close(spare)
    elif mode == "pdeathsig":
        # D2: the subject arms PR_SET_PDEATHSIG(SIGKILL) before any subject code
        # runs (partial coverage for the wedged-guardian residual; the behavioral
        # leg rides the subject-receipt case). PR_GET_PDEATHSIG reads it back.
        def query():
            import ctypes
            value = ctypes.c_int(-1)
            libc = ctypes.CDLL(None, use_errno=True)
            if libc.prctl(2, ctypes.byref(value), 0, 0, 0) != 0:  # PR_GET_PDEATHSIG
                return "PRCTL-ERROR"
            return str(value.value)

        assert emit.run_bounded(query, timeout_s=10) == str(int(signal.SIGKILL))
        # Flip: without the arm the subject reports no parent-death signal (the
        # flag is cleared on fork, so an inherited value can never mask the flip).
        with patch.object(emit, "_fixture_pdeathsig", lambda: None):
            assert emit.run_bounded(query, timeout_s=10) == "0"
    elif mode == "subject-gc":
        # QA18 gemini F3: the guardian disables gc for its own forked-heap safety,
        # but the SUBJECT runs arbitrary test code and must get the collector back
        # before its callable runs, or every cycle it builds leaks for the
        # callable's whole life.
        def probe():
            import gc
            return "GC-ON" if gc.isenabled() else "GC-OFF"

        assert emit.run_bounded(probe, timeout_s=10) == "GC-ON"
        # Flip: without the re-enable the subject inherits the disabled collector.
        with patch.object(emit, "_fixture_enable_gc", lambda: None):
            assert emit.run_bounded(probe, timeout_s=10) == "GC-OFF"
    elif mode == "guardian-preload":
        import time
        # QA18 codex F5: the guardian forks from the launcher thread, so a cold
        # guardian-side import could block on an import or module lock some other
        # caller thread held at fork time. Construction preloads the whole guardian
        # dependency set, making every guardian-side import a lock-free
        # sys.modules hit; the documented restriction that remains is thunk-side.
        # QA19 F4: socket.send_fds/recv_fds cold-import array inside their own
        # bodies (importing socket alone does not load it), so the receipt path
        # needs array preloaded too.
        def pop_cold():
            popped = dict()
            for name in list(sys.modules):
                if name in ("ctypes", "array") or name.startswith("ctypes."):
                    popped[name] = sys.modules.pop(name)
            return popped

        popped = pop_cold()
        try:
            child = emit._FixtureProcess(time.monotonic() + 5, subject=lambda: None)
            try:
                missing = [name for name in emit._FIXTURE_GUARDIAN_MODULES
                           if name not in sys.modules]
                assert not missing, missing
                assert "array" in sys.modules, \
                    "the receipt path's send_fds/recv_fds array import stayed cold"
            finally:
                child.close()
            # Flip: without the preload the guardian dependency set stays cold.
            pop_cold()
            with patch.object(emit, "_fixture_preload", lambda: None):
                flip_child = emit._FixtureProcess(time.monotonic() + 5,
                                                  subject=lambda: None)
                try:
                    assert "ctypes" not in sys.modules, "the flip still preloaded"
                    assert "array" not in sys.modules, "the flip still preloaded array"
                finally:
                    flip_child.close()
        finally:
            for name, module in popped.items():
                sys.modules.setdefault(name, module)
    elif mode == "subject-receipt":
        import time
        caller = os.getpid()
        # The receipt (subject pid + pidfd) is sent between the subject fork and
        # supervision and stays BUFFERED on the control socket: the caller collects
        # it even after the guardian's death, so escalation always knows the
        # subject. The subject's PR_SET_PDEATHSIG(SIGKILL) partial coverage is
        # observed behaviorally on the same stimulus (D2).
        with tempfile.TemporaryDirectory(prefix="opf-receipt-") as directory:
            guardian_file = Path(directory, "guardian")
            real_send = emit._fixture_send_subject

            def wedge(peer, subject, subject_fd, send=True):
                if send:
                    real_send(peer, subject, subject_fd)
                if os.getpid() != caller:
                    scratch = Path(directory, "guardian.tmp")
                    scratch.write_text(str(os.getpid()), encoding="ascii")
                    scratch.rename(guardian_file)  # atomic: never a partial PID
                    os.kill(os.getpid(), signal.SIGSTOP)

            def state(target):
                try:
                    stat = Path("/proc", str(target), "stat").read_bytes()
                except (FileNotFoundError, ProcessLookupError):
                    return None
                return stat.rsplit(b")", 1)[1].split()[0].decode("ascii")

            def await_state(target, wanted, note):
                bound = time.monotonic() + 30
                while state(target) not in wanted:
                    assert time.monotonic() < bound, note
                    time.sleep(0.005)

            def launch_wedged(hook):
                guardian_file.unlink(missing_ok=True)
                child = emit._FixtureProcess(time.monotonic() + 3600,
                                             subject=lambda: time.sleep(3600))
                with patch.object(emit, "_fixture_send_subject", hook):
                    child.start()
                bound = time.monotonic() + 30
                while not guardian_file.exists():
                    assert time.monotonic() < bound, "the guardian never reached its stop point"
                    time.sleep(0.005)
                gpid = int(guardian_file.read_text(encoding="ascii"))
                assert gpid == child.pid, "the hook stopped an unexpected process"
                await_state(gpid, ("T",), "the guardian did not stop")
                subject = int(Path("/proc", str(gpid), "task", str(gpid), "children")
                              .read_text(encoding="ascii").split()[0])
                return child, subject

            # Leg 1: guardian SIGKILLed while wedged -> the buffered receipt still
            # arrives, and the subject dies without running its callable (pdeathsig,
            # and independently the acknowledgment EOF refusal).
            child, subject = launch_wedged(wedge)
            os.kill(child.pid, signal.SIGKILL)
            failures = []
            with patch.object(emit, "_FIXTURE_CLEANUP_GRACE", 1.0):
                try:
                    child.close()
                except emit.ChildStatusUnavailable as exc:
                    failures.append(str(exc))
            assert child.subject_pid == subject, (child.subject_pid, subject)
            assert failures, "a SIGKILLed guardian was read as clean"
            # A dead orphan reparents to the nearest subreaper ancestor (the
            # outer guardian, under the regression runner) and lingers as a
            # zombie until that ancestor's drain: Z is dead, not surviving.
            await_state(subject, (None, "Z"), "pdeathsig did not kill the orphaned subject")

            # Flip: skip the send -> escalation degrades to guardian-only and the
            # refusal must say the subject cleanup is INCOMPLETE, never claim a
            # killed tree. The unacknowledged subject (pdeathsig coverage also
            # removed) still refuses by ITSELF on the acknowledgment EOF once the
            # escalation SIGKILLs the wedged guardian: no manual hygiene remains.
            def skip_send(peer, subject, subject_fd):
                wedge(peer, subject, subject_fd, send=False)

            with patch.object(emit, "_fixture_pdeathsig", lambda: None):
                child, subject = launch_wedged(skip_send)
            failures = []
            with patch.object(emit, "_FIXTURE_CLEANUP_GRACE", 1.0):
                try:
                    child.close()
                except emit.ChildStatusUnavailable as exc:
                    failures.append(str(exc))
            assert child.subject_pid is None, "a skipped send still delivered a receipt"
            assert failures and "escalat" in failures[0], failures
            assert "incomplete" in failures[0].lower(), failures
            await_state(subject, (None, "Z"),
                        "the unacknowledged subject outlived its guardian")
    elif mode == "subject-ack":
        import time
        caller = os.getpid()
        # QA18 codex F2: the subject may not run user code (so cannot create
        # descendants) before the guardian has delivered its receipt and
        # acknowledged ownership; a guardian that dies before the acknowledgment
        # must yield a subject that refuses to run, even without pdeathsig.
        with tempfile.TemporaryDirectory(prefix="opf-ack-") as directory:
            guardian_file = Path(directory, "guardian")
            marker = Path(directory, "marker")
            real_ack = emit._fixture_ack_subject

            def wedge_ack(fd):
                # Wedge AFTER the receipt send, BEFORE the acknowledgment.
                if os.getpid() != caller:
                    scratch = Path(directory, "guardian.tmp")
                    scratch.write_text(str(os.getpid()), encoding="ascii")
                    scratch.rename(guardian_file)  # atomic: never a partial PID
                    os.kill(os.getpid(), signal.SIGSTOP)
                real_ack(fd)

            def subject_body():
                marker.write_text("ran", encoding="ascii")
                import time as clock
                clock.sleep(3600)

            def state(target):
                try:
                    stat = Path("/proc", str(target), "stat").read_bytes()
                except (FileNotFoundError, ProcessLookupError):
                    return None
                return stat.rsplit(b")", 1)[1].split()[0].decode("ascii")

            def await_state(target, wanted, note):
                bound = time.monotonic() + 30
                while state(target) not in wanted:
                    assert time.monotonic() < bound, note
                    time.sleep(0.005)

            def launch_wedged():
                guardian_file.unlink(missing_ok=True)
                marker.unlink(missing_ok=True)
                child = emit._FixtureProcess(time.monotonic() + 3600,
                                             subject=subject_body)
                with patch.object(emit, "_fixture_ack_subject", wedge_ack):
                    child.start()
                bound = time.monotonic() + 30
                while not guardian_file.exists():
                    assert time.monotonic() < bound, "the guardian never stopped"
                    time.sleep(0.005)
                gpid = int(guardian_file.read_text(encoding="ascii"))
                assert gpid == child.pid, "the hook stopped an unexpected process"
                await_state(gpid, ("T",), "the guardian did not stop")
                subject = int(Path("/proc", str(gpid), "task", str(gpid), "children")
                              .read_text(encoding="ascii").split()[0])
                return child, subject

            # An unacknowledged subject refuses on guardian death by ITSELF:
            # pdeathsig is removed so only the acknowledgment EOF can end it.
            with patch.object(emit, "_fixture_pdeathsig", lambda: None):
                child, subject = launch_wedged()
                os.kill(child.pid, signal.SIGKILL)
                await_state(subject, (None, "Z"),
                            "an unacknowledged subject survived its guardian")
                assert not marker.exists(), "the subject ran before the ack"
                failures = []
                with patch.object(emit, "_FIXTURE_CLEANUP_GRACE", 1.0):
                    try:
                        child.close()
                    except emit.ChildStatusUnavailable as exc:
                        failures.append(str(exc))
                assert child.subject_pid == subject, (child.subject_pid, subject)
                assert failures, "a SIGKILLed guardian was read as clean"

            # Flip: bypass the subject-side gate and the pre-receipt window is
            # back: the subject runs its callable while the guardian is wedged
            # short of the acknowledgment.
            with patch.object(emit, "_fixture_pdeathsig", lambda: None), \
                    patch.object(emit, "_fixture_await_ack", lambda fd: None):
                child, subject = launch_wedged()
                bound = time.monotonic() + 30
                while not marker.exists():
                    assert time.monotonic() < bound, "the flip subject never ran"
                    time.sleep(0.005)
                os.kill(child.pid, signal.SIGKILL)
                failures = []
                with patch.object(emit, "_FIXTURE_CLEANUP_GRACE", 1.0):
                    try:
                        child.close()
                    except emit.ChildStatusUnavailable as exc:
                        failures.append(str(exc))
                assert failures, "the flip close was read as clean"
                await_state(subject, (None, "Z"), "the flip cleanup did not complete")
    elif mode == "subject-orphan":
        import time
        # QA18 codex F3: pdeathsig is not retroactive, so a subject first scheduled
        # after its guardian died arms too late; it must detect the reparenting and
        # refuse to run. Independently, close() must kill and prove a
        # receipt-identified subject on ANY guardian failure, not only after an
        # escalation. The delayed schedule is modeled as a file-gated wait, not a
        # SIGSTOP: a pre-setsid subject stopped in the dying guardian's group would
        # get the kernel's orphaned-group SIGHUP+SIGCONT, an environment-dependent
        # coincidence (hosts ignoring SIGHUP keep the orphan alive) that this
        # regression must not depend on.
        with tempfile.TemporaryDirectory(prefix="opf-orphan-") as directory:
            waiting = Path(directory, "waiting")
            release = Path(directory, "release")
            marker = Path(directory, "marker")
            real_pdeathsig = emit._fixture_pdeathsig

            def hold(arm):
                # Subject-side: publish the pid, wait for the release gate BEFORE
                # the (optional) arm, arm only after the gate opens.
                import time as clock
                scratch = Path(directory, "waiting.tmp")
                scratch.write_text(str(os.getpid()), encoding="ascii")
                scratch.rename(waiting)  # atomic: never a partial PID
                bound = clock.monotonic() + 120
                while not release.exists():
                    if clock.monotonic() >= bound:
                        os._exit(96)  # the gate never opened: fail loudly
                    clock.sleep(0.005)
                if arm:
                    real_pdeathsig()

            def subject_body():
                marker.write_text("ran", encoding="ascii")
                import time as clock
                clock.sleep(3600)

            def state(target):
                try:
                    stat = Path("/proc", str(target), "stat").read_bytes()
                except (FileNotFoundError, ProcessLookupError):
                    return None
                return stat.rsplit(b")", 1)[1].split()[0].decode("ascii")

            def await_state(target, wanted, note):
                bound = time.monotonic() + 30
                while state(target) not in wanted:
                    assert time.monotonic() < bound, note
                    time.sleep(0.005)

            def launch_held(arm=True):
                import select
                waiting.unlink(missing_ok=True)
                release.unlink(missing_ok=True)
                marker.unlink(missing_ok=True)
                child = emit._FixtureProcess(time.monotonic() + 3600,
                                             subject=subject_body)
                with patch.object(emit, "_fixture_pdeathsig", lambda: hold(arm)):
                    child.start()
                bound = time.monotonic() + 30
                while not waiting.exists():
                    assert time.monotonic() < bound, "the subject never reached its gate"
                    time.sleep(0.005)
                subject = int(waiting.read_text(encoding="ascii"))
                # The gated subject publishes its pid BEFORE the guardian sends the
                # receipt: wait for the receipt to be buffered on the control socket
                # before any leg kills the guardian, so every leg exercises a
                # DELIVERED receipt (the no-receipt path is subject-receipt's leg).
                assert select.select([child.control], [], [], 30)[0], \
                    "the subject receipt never arrived"
                return child, subject

            def cancel(child, failures):
                with patch.object(emit, "_FIXTURE_CLEANUP_GRACE", 1.0):
                    try:
                        child.close()
                    except emit.ChildStatusUnavailable as exc:
                        failures.append(str(exc))

            # Leg 1: the guardian dies while the subject is still gated pre-arm;
            # the released subject arms, sees a changed parent, and refuses.
            child, subject = launch_held()
            os.kill(child.pid, signal.SIGKILL)
            await_state(child.pid, ("Z",), "the killed guardian did not exit")
            release.write_text("go", encoding="ascii")
            await_state(subject, (None, "Z"), "the orphaned subject kept running")
            assert not marker.exists(), "an orphaned subject ran its callable"
            failures = []
            cancel(child, failures)
            assert child.subject_pid == subject, (child.subject_pid, subject)
            assert failures, "a SIGKILLed guardian was read as clean"

            # Leg 2: same stimulus, subject NEVER released: the guardian failure is
            # collected without an escalation, and close() itself must kill the
            # receipt-identified subject and prove its exit.
            child, subject = launch_held()
            os.kill(child.pid, signal.SIGKILL)
            failures = []
            cancel(child, failures)
            assert failures, "a SIGKILLed guardian was read as clean"
            await_state(subject, (None, "Z"),
                        "close() left the live subject on a guardian failure")
            assert not marker.exists(), "the gated subject ran its callable"

            # Flip A: without the parent check (and with pdeathsig disarmed, which
            # a post-death arm cannot help anyway) the released orphan runs its
            # callable: the QA18 codex F3 defect, reproduced.
            with patch.object(emit, "_fixture_check_parent", lambda expected: None):
                child, subject = launch_held(arm=False)
                os.kill(child.pid, signal.SIGKILL)
                await_state(child.pid, ("Z",), "the killed guardian did not exit")
                release.write_text("go", encoding="ascii")
                bound = time.monotonic() + 30
                while not marker.exists():
                    assert time.monotonic() < bound, "the flip orphan never ran"
                    time.sleep(0.005)
                failures = []
                cancel(child, failures)  # close() still kills the receipt subject
                assert failures, "the flip close was read as clean"
                await_state(subject, (None, "Z"), "the flip cleanup did not complete")

            # Flip B: without the close-side subject kill, a live subject survives
            # a guardian failure and close() names the surviving pid.
            child, subject = launch_held()
            os.kill(child.pid, signal.SIGKILL)
            failures = []
            with patch.object(emit, "_fixture_escalate_subject",
                              lambda subject, fd: None):
                cancel(child, failures)
            assert failures and "could not confirm subject exit" in failures[0], failures
            assert state(subject) not in (None, "Z"), \
                "flip: the subject died without its kill"
            os.kill(subject, signal.SIGKILL)  # hygiene for the demonstrated leak
            await_state(subject, (None, "Z"), "the flip hygiene did not complete")
    elif mode == "escalation-subject":
        import time
        # QA16 MINOR-2 + QA17 F1: cancelling a wedged guardian is bounded AND kills
        # and observes the receipt-identified subject tree -- freeze the guardian,
        # then the subject's group and pidfd, then the guardian -- never a stranded
        # sleeper the test has to clean up. The same-group descendant must die with
        # the subject; descendants that LEAVE the group are the documented
        # wedged-guardian residual, out of contract here.
        def subject_tree():
            import time as clock
            if os.fork() == 0:
                clock.sleep(3600)          # same-group descendant
            clock.sleep(3600)              # subject: outlives everything unless cancelled

        def state(target):
            try:
                stat = Path("/proc", str(target), "stat").read_bytes()
            except (FileNotFoundError, ProcessLookupError):
                return None
            return stat.rsplit(b")", 1)[1].split()[0].decode("ascii")

        def children_of(owner):
            pids = []
            for entry in os.listdir("/proc"):
                if entry.isdecimal():
                    try:
                        stat = Path("/proc", entry, "stat").read_bytes()
                    except (FileNotFoundError, ProcessLookupError):
                        continue
                    if int(stat.rsplit(b")", 1)[1].split()[1]) == owner:
                        pids.append(int(entry))
            return pids

        def await_child(owner, note):
            bound = time.monotonic() + 30
            while True:
                assert time.monotonic() < bound, note
                listed = children_of(owner)
                if listed:
                    return listed[0]
                time.sleep(0.005)

        def exercise(flip):
            from contextlib import ExitStack
            with ExitStack() as stack:
                stack.enter_context(patch.object(emit, "_FIXTURE_CLEANUP_GRACE", 1.0))
                if flip:
                    # Flip: no-op the escalation's subject-kill step (and the
                    # subject's pdeathsig partial coverage, which must not rescue
                    # the flip) -> the subject tree must survive and the
                    # disappearance proof must fail.
                    stack.enter_context(patch.object(
                        emit, "_fixture_escalate_subject",
                        lambda subject, fd, **license: None))
                    stack.enter_context(patch.object(
                        emit, "_fixture_pdeathsig", lambda: None))
                child = emit._FixtureProcess(time.monotonic() + 3600, subject=subject_tree)
                pid = child.start()
                subject = await_child(pid, "the guardian subject never appeared")
                descendant = await_child(subject, "the subject descendant never appeared")
                # A stopped guardian models a cleanup that cannot make progress: it
                # can never exit, so an unbounded close() would block until resumed.
                os.kill(pid, signal.SIGSTOP)
                bound = time.monotonic() + 30
                while state(pid) != "T":
                    assert time.monotonic() < bound, "the guardian did not stop"
                    time.sleep(0.005)
                sequence, failures = [], []
                real_pidfd_signal = signal.pidfd_send_signal
                real_escalate = emit._fixture_escalate_subject
                real_members = emit._fixture_kill_group_members
                guardian_fd = child.pidfd

                def rec_pidfd(fd, signum, *args):
                    sequence.append(("pidfd", fd, signum))
                    return real_pidfd_signal(fd, signum, *args)

                def rec_killpg(pgid, signum):
                    # The layer must never issue a numeric group kill (QA21
                    # codex F3); any call is a loud wrong-owner-class failure.
                    raise AssertionError(
                        "numeric killpg is banned: " + repr((pgid, signum)))

                def rec_members(group, signum, anchors, leader=None):
                    delivered, skipped, unverifiable = real_members(
                        group, signum, anchors, leader=leader)
                    sequence.append(("members", group, signum, tuple(delivered),
                                     tuple(skipped or ()) + tuple(unverifiable),
                                     leader))
                    return delivered, skipped, unverifiable

                def rec_escalate(target, target_fd, **license):
                    sequence.append(("escalate", target))
                    return real_escalate(target, target_fd, **license)

                def cancel():
                    try:
                        child.close()
                    except emit.ChildStatusUnavailable as exc:
                        failures.append(str(exc))
                    except BaseException as exc:  # noqa: BLE001 - the verdict channel
                        failures.append("unexpected: " + repr(exc))

                worker = threading.Thread(target=cancel, daemon=True)
                begun = time.monotonic()
                with patch.object(signal, "pidfd_send_signal", rec_pidfd), \
                        patch.object(os, "killpg", rec_killpg), \
                        patch.object(emit, "_fixture_escalate_subject",
                                     rec_escalate), \
                        patch.object(emit, "_fixture_kill_group_members",
                                     rec_members):
                    worker.start()
                    worker.join(20)
                    blocked = worker.is_alive()
                    if blocked:
                        os.kill(pid, signal.SIGCONT)  # unwedge the leak before failing
                        worker.join(30)
                elapsed = time.monotonic() - begun
                assert not blocked, "close() blocked past its bounded cleanup budget"
                assert failures and "escalat" in failures[0], failures
                assert emit._fixture_child_reaped(pid) is True
                assert elapsed < 15, elapsed
                return pid, guardian_fd, subject, descendant, sequence, failures

        pid, guardian_fd, subject, descendant, sequence, failures = exercise(flip=False)
        # Kill order: freeze first, then the subject's tree (per-member
        # verified pidfds -- the banned-killpg recorder proves no numeric
        # group kill ran, QA21 codex F3), then the guardian.
        assert sequence and sequence[0][0] == "pidfd" and sequence[0][2] == signal.SIGSTOP, sequence
        subject_kills = [index for index, entry in enumerate(sequence)
                         if entry[0] == "members" and entry[1] == subject
                         and entry[2] == signal.SIGKILL]
        guardian_kills = [index for index, entry in enumerate(sequence)
                          if entry[0] == "pidfd" and entry[1] == guardian_fd
                          and entry[2] == signal.SIGKILL]
        assert subject_kills, (sequence, subject)
        assert descendant in sequence[subject_kills[0]][3], (sequence, descendant)
        # Fix 2y (codex F1/F2): the census is licensed by the frozen
        # guardian's subreaper ownership, EXCLUDES the leader (members die
        # first; the leader dies LAST through its held pidfd), and accounts
        # every member -- the leader never appears as a census delivery and
        # nothing was skipped.
        assert subject not in sequence[subject_kills[0]][3], sequence
        assert sequence[subject_kills[0]][4] == (), sequence
        assert sequence[subject_kills[0]][5] == subject, sequence
        assert not guardian_kills or subject_kills[0] < guardian_kills[0], sequence
        # The whole tree is observed dead: no manual sleeper hygiene remains in
        # this test. A dead orphan reparents to the nearest subreaper ancestor
        # (the outer guardian, under the regression runner) and lingers as a
        # zombie until that ancestor's drain: Z is dead, not surviving.
        dead = (None, "Z")
        bound = time.monotonic() + 30
        while state(subject) not in dead or state(descendant) not in dead:
            assert time.monotonic() < bound, (state(subject), state(descendant))
            time.sleep(0.005)
        # Flip: with the subject-kill step removed the subject tree survives and
        # close() itself names the surviving pid.
        pid, guardian_fd, subject, descendant, sequence, failures = exercise(flip=True)
        assert state(subject) not in dead, "flip: the subject died without its kill step"
        assert "could not confirm subject exit" in failures[0], failures
        os.killpg(subject, signal.SIGKILL)  # clean up the deliberately-leaked tree
        bound = time.monotonic() + 30
        while state(subject) not in dead or state(descendant) not in dead:
            assert time.monotonic() < bound, "the flip cleanup did not complete"
            time.sleep(0.005)
    elif mode == "escalate-reaped":
        import time
        from contextlib import ExitStack
        caller = os.getpid()
        # QA18 gemini F1: escalation must reach the receipt-identified
        # subject's same-group members even when the guardian already REAPED
        # the subject: the frozen subreaper guardian still parents the
        # surviving descendant (the census pin), which is then addressed
        # through its OWN verified pidfd -- never a numeric killpg of the
        # freed, recyclable pgid (QA21 codex F3).
        with tempfile.TemporaryDirectory(prefix="opf-reaped-") as directory:
            wedged = Path(directory, "wedged")
            grandchild_file = Path(directory, "grandchild")

            def subject_body():
                # Fork a same-group descendant, then exit: the guardian reaps this
                # subject while the descendant lives on.
                pid = os.fork()
                if pid == 0:
                    import time as clock
                    clock.sleep(3600)
                scratch = Path(directory, "grandchild.tmp")
                scratch.write_text(str(pid), encoding="ascii")
                scratch.rename(grandchild_file)  # atomic: never a partial PID

            real_drain = emit._fixture_drain

            def reap_then_wedge(subject, subject_fd=None, *, deadline=None):
                # Guardian-side: reap the exited subject, then wedge BEFORE any
                # group kill: the state a drain stalled mid-cleanup leaves behind.
                if os.getpid() != caller:
                    os.waitpid(subject, 0)
                    scratch = Path(directory, "wedged.tmp")
                    scratch.write_text(str(os.getpid()), encoding="ascii")
                    scratch.rename(wedged)  # atomic: never a partial PID
                    os.kill(os.getpid(), signal.SIGSTOP)
                return real_drain(subject, subject_fd, deadline=deadline)

            def state(target):
                try:
                    stat = Path("/proc", str(target), "stat").read_bytes()
                except (FileNotFoundError, ProcessLookupError):
                    return None
                return stat.rsplit(b")", 1)[1].split()[0].decode("ascii")

            def await_state(target, wanted, note):
                bound = time.monotonic() + 30
                while state(target) not in wanted:
                    assert time.monotonic() < bound, note
                    time.sleep(0.005)

            def exercise(flip):
                wedged.unlink(missing_ok=True)
                grandchild_file.unlink(missing_ok=True)
                child = emit._FixtureProcess(time.monotonic() + 3600,
                                             subject=subject_body)
                with patch.object(emit, "_fixture_drain", reap_then_wedge):
                    child.start()
                bound = time.monotonic() + 30
                while not (wedged.exists() and grandchild_file.exists()):
                    assert time.monotonic() < bound, "the drain wedge was not reached"
                    time.sleep(0.005)
                gpid = int(wedged.read_text(encoding="ascii"))
                assert gpid == child.pid, "the hook stopped an unexpected process"
                await_state(gpid, ("T",), "the guardian did not stop")
                grandchild = int(grandchild_file.read_text(encoding="ascii"))
                assert state(grandchild) not in (None, "Z"), "the descendant died early"
                failures = []
                with ExitStack() as stack:
                    stack.enter_context(
                        patch.object(emit, "_FIXTURE_CLEANUP_GRACE", 1.0))
                    if flip:
                        # Flip: skip the group kill for the already-reaped subject
                        # (the old probe-gated early return): the descendant must
                        # survive again.
                        stack.enter_context(patch.object(
                            emit, "_fixture_escalate_subject",
                            lambda subject, fd, **license: None))
                    try:
                        child.close()
                    except emit.ChildStatusUnavailable as exc:
                        failures.append(str(exc))
                assert failures and "escalat" in failures[0], failures
                assert child.subject_pid is not None, "the subject receipt was lost"
                return child.subject_pid, grandchild

            subject, grandchild = exercise(flip=False)
            await_state(grandchild, (None, "Z"),
                        "escalation stranded the reaped subject's same-group descendant")
            subject, grandchild = exercise(flip=True)
            assert state(grandchild) not in (None, "Z"), \
                "flip: the descendant died without the group kill"
            os.killpg(subject, signal.SIGKILL)  # hygiene for the demonstrated leak
            await_state(grandchild, (None, "Z"), "the flip hygiene did not complete")
    elif mode == "poll-collected":
        import time
        # QA19 F1: a guardian failure FIRST collected by poll() -- the layer's
        # primary collection path (run_bounded and _run_fixture_process poll on
        # every loop iteration) -- must still get close()'s receipt kill,
        # disappearance proof and loud refusal: poll() records the failure and
        # close() re-raises it after addressing the subject. pdeathsig is
        # disarmed so only close()'s own kill can end the orphaned subject tree.
        with tempfile.TemporaryDirectory(prefix="opf-pollfail-") as directory:
            descendant_file = Path(directory, "descendant")

            def subject_body():
                import time as clock
                pid = os.fork()
                if pid == 0:
                    clock.sleep(3600)          # same-group descendant
                scratch = Path(directory, "descendant.tmp")
                scratch.write_text(str(pid), encoding="ascii")
                scratch.rename(descendant_file)  # atomic: never a partial PID
                clock.sleep(3600)              # the subject outlives its guardian

            def state(target):
                try:
                    stat = Path("/proc", str(target), "stat").read_bytes()
                except (FileNotFoundError, ProcessLookupError):
                    return None
                return stat.rsplit(b")", 1)[1].split()[0].decode("ascii")

            def await_state(target, wanted, note):
                bound = time.monotonic() + 30
                while state(target) not in wanted:
                    assert time.monotonic() < bound, note
                    time.sleep(0.005)

            def launch_killed():
                # Launch, wait for the running subject tree, then SIGKILL the
                # guardian and collect it through poll(), never through close().
                descendant_file.unlink(missing_ok=True)
                with patch.object(emit, "_fixture_pdeathsig", lambda: None):
                    child = emit._FixtureProcess(time.monotonic() + 3600,
                                                 subject=subject_body)
                    child.start()
                bound = time.monotonic() + 30
                while not descendant_file.exists():
                    assert time.monotonic() < bound, "the subject tree never appeared"
                    time.sleep(0.005)
                descendant = int(descendant_file.read_text(encoding="ascii"))
                subject = int(Path("/proc", str(child.pid), "task", str(child.pid),
                                   "children").read_text(encoding="ascii").split()[0])
                os.kill(child.pid, signal.SIGKILL)
                polled = []
                bound = time.monotonic() + 30
                while True:
                    try:
                        status = child.poll()
                    except emit.ChildStatusUnavailable as exc:
                        polled.append(str(exc))
                        break
                    assert status is None, status
                    assert time.monotonic() < bound, \
                        "poll() never collected the killed guardian"
                    time.sleep(0.005)
                assert child.collected, "poll() did not record the collection"
                assert "guardian failed" in polled[0], polled
                return child, subject, descendant

            child, subject, descendant = launch_killed()
            # Fix 2y (D2): the guardian is DEAD when poll() collects, so the
            # collection-time receipt kill is SUBJECT-ONLY through the held
            # pidfd -- NO member census, NO new pidfds. The surviving
            # same-group descendant is the documented orphan-escape residual
            # (pdeathsig is disarmed here to expose it), which close()
            # DISCLOSES and never claims killed. The pre-fix layer anchored a
            # census on the subject's bare NUMERIC pid here (the
            # recycled-anchor class, QA22 codex F1) and killed the descendant.
            assert child._subject_kill == "subject-only", child._subject_kill
            closed = []
            with patch.object(emit, "_FIXTURE_CLEANUP_GRACE", 1.0):
                try:
                    child.close()
                except emit.ChildStatusUnavailable as exc:
                    closed.append(str(exc))
            assert closed and "guardian failed" in closed[0], \
                ("close() did not re-raise the poll-recorded failure", closed)
            assert "descendants, if any, unaddressed" in closed[0], closed
            # Fix 2z (gemini F3): with a dead guardian NO census ran at all,
            # so the disclosure covers EVERY unaddressed descendant --
            # narrowing it to "same-group" falsely implied the other-group
            # ones were addressed or could not exist.
            assert "same-group" not in closed[0], closed
            assert "tree killed" not in closed[0], closed
            assert child.subject_pid == subject, (child.subject_pid, subject)
            # A dead orphan reparents to the nearest subreaper ancestor (the
            # outer guardian, under the regression runner) and lingers as a
            # zombie until that ancestor's drain: Z is dead, not surviving.
            await_state(subject, (None, "Z"),
                        "close() left the poll-collected failure's subject running")
            assert state(descendant) not in (None, "Z"), \
                "the no-guardian path census-killed the orphaned descendant"
            os.kill(descendant, signal.SIGKILL)  # hygiene for the disclosed residual
            await_state(descendant, (None, "Z"),
                        "the residual hygiene did not complete")

            # Flip (red-on-revert, D2): reintroduce the removed no-guardian
            # census -- a member kill anchored on the subject's bare numeric
            # pid after one freeze probe -- and the orphaned descendant dies
            # through a freshly opened pidfd on this guardian-dead path,
            # exactly the unowned kill fix 2y removed.
            def census_kill(target, fd, **license):
                signal.pidfd_send_signal(fd, signal.SIGSTOP)
                emit._fixture_kill_group_members(
                    target, signal.SIGKILL, {target}, leader=target)
                signal.pidfd_send_signal(fd, signal.SIGKILL)
                return ("subject-only", None)

            with patch.object(emit, "_fixture_escalate_subject", census_kill):
                child, subject, descendant = launch_killed()
            await_state(subject, (None, "Z"), "the flip subject survived")
            await_state(descendant, (None, "Z"),
                        "flip: the reintroduced census did not reach the descendant")
            with patch.object(emit, "_FIXTURE_CLEANUP_GRACE", 1.0):
                try:
                    child.close()
                except emit.ChildStatusUnavailable:
                    pass

            # Flip: erase everything fix 2w acts on -- no collection-time kill,
            # no recorded failure, and a FORGED validated status (the pre-fix
            # close believed exactly this state) -> close() returns SILENTLY
            # and the subject tree survives.
            with patch.object(emit._FixtureProcess, "_address_failed_subject",
                              lambda fixture: None):
                child, subject, descendant = launch_killed()
            child._failure = None
            child.status = 0
            child.unresolved = False
            closed = []
            with patch.object(emit, "_FIXTURE_CLEANUP_GRACE", 1.0):
                try:
                    child.close()
                except emit.ChildStatusUnavailable as exc:
                    closed.append(str(exc))
            assert not closed, closed
            assert state(subject) not in (None, "Z"), \
                "flip: the subject died without close()'s kill"
            assert state(descendant) not in (None, "Z"), \
                "flip: the descendant died without close()'s kill"
            os.killpg(subject, signal.SIGKILL)  # hygiene for the demonstrated leak
            await_state(subject, (None, "Z"), "the flip hygiene did not complete")
            await_state(descendant, (None, "Z"), "the flip hygiene did not complete")
    elif mode == "close-cancel":
        import time
        # QA19 F2 + QA20 codex F2 / gemini / claude F2 + QA21 claude
        # F1/F3/F4 (masked design, fix 2w/2x): a real SIGINT landing anywhere
        # inside close()'s ownership decision, transfer, collection or
        # cleanup stays PENDING (SIGINT and SIGTERM are blocked as the FIRST
        # statement of close()'s restoring try, and the launcher thread is
        # created with the pair blocked, so a single-threaded caller has no
        # thread the kernel could deliver to) and is delivered AT the restore
        # inside close()'s own finally: the guardian and subject tree are
        # cleaned or handed to a live owner FIRST, then close() raises the
        # delivered KeyboardInterrupt itself with a refusal surviving on its
        # context chain (or the refusal propagates directly when no interrupt
        # is pending), and the caller's prior mask is restored exactly.
        # Windows: (1) the completion wait, fork unrecorded; (2) the gap
        # before the launcher release; (3) entering the ownership transfer;
        # (4) the collection's bounded reap wait; (5) the collection entry
        # boundary; (6) a PROCESS-directed SIGINT (the real Ctrl-C delivery
        # shape) during the wedged completion wait; (7) a cancellation raised
        # during the mask installation itself.
        real_fork = os.fork
        cancellation = {signal.SIGINT, signal.SIGTERM}
        masked_at = {}
        real_abandon = emit._FixtureProcess._abandon_unfinished_launch
        real_finish = emit._FixtureProcess._finish_close

        def open_fds():
            live = set()
            for name in os.listdir("/proc/self/fd"):
                try:
                    os.fstat(int(name))
                except OSError:
                    continue
                live.add(int(name))
            return live

        def no_children():
            try:
                os.waitid(os.P_ALL, 0, os.WEXITED | os.WNOHANG | os.WNOWAIT)
            except ChildProcessError:
                return True
            return False

        def state(target):
            try:
                stat = Path("/proc", str(target), "stat").read_bytes()
            except (FileNotFoundError, ProcessLookupError):
                return None
            return stat.rsplit(b")", 1)[1].split()[0].decode("ascii")

        def await_state(target, wanted, note):
            bound = time.monotonic() + 30
            while state(target) not in wanted:
                assert time.monotonic() < bound, note
                time.sleep(0.005)

        def await_subject(child):
            bound = time.monotonic() + 30
            while True:
                listed = Path("/proc", str(child.pid), "task", str(child.pid),
                              "children").read_text(encoding="ascii").split()
                if listed:
                    return int(listed[0])
                assert time.monotonic() < bound, "the subject never appeared"
                time.sleep(0.005)

        def observe_abandon(fixture):
            masked_at["transfer"] = signal.pthread_sigmask(signal.SIG_BLOCK, set())
            return real_abandon(fixture)

        def observe_finish(fixture):
            masked_at["collect"] = signal.pthread_sigmask(signal.SIG_BLOCK, set())
            return real_finish(fixture)

        def interrupt_wait(child):
            # A REAL SIGINT raised inside the masked sequence: the kernel keeps
            # it pending until close() restores the caller's mask.
            real_wait, fired = child._launched.wait, []

            def wait(timeout=None):
                if not fired:
                    fired.append(True)
                    signal.raise_signal(signal.SIGINT)
                return real_wait(timeout)

            child._launched.wait = wait

        def drive(child):
            # close() under a pending injected SIGINT: the sequence completes
            # FIRST and the interrupt arrives after it; a refusal survives
            # directly or on the delivered interrupt's context chain.
            before_mask = signal.pthread_sigmask(signal.SIG_BLOCK, set())
            refusals, delivered = [], []
            try:
                try:
                    child.close()
                except emit.ChildStatusUnavailable as exc:
                    refusals.append(str(exc))
                bound = time.monotonic() + 10
                while time.monotonic() < bound:
                    pass
                raise AssertionError("the pending interrupt was never delivered")
            except KeyboardInterrupt as exc:
                delivered.append(True)
                context = exc.__context__
                while context is not None and not refusals:
                    if isinstance(context, emit.ChildStatusUnavailable):
                        refusals.append(str(context))
                        break
                    context = context.__context__
            assert delivered, "the pending interrupt was never delivered"
            assert signal.pthread_sigmask(signal.SIG_BLOCK, set()) == before_mask, \
                "close() did not restore the exact prior mask"
            return refusals

        gate = threading.Event()
        launcher_masks = []

        def wedged_fork():
            if threading.current_thread().name == "opf-fixture-launcher":
                launcher_masks.append(
                    signal.pthread_sigmask(signal.SIG_BLOCK, set()))
                assert gate.wait(60), "the fork gate was never released"
            return real_fork()

        emit._fixture_preload()
        assert no_children(), "children live before the close-cancel probe"
        before = open_fds()

        # Leg 1: fork unrecorded (wedged), SIGINT pending from inside the
        # completion wait -> the wait runs its full bounded budget, the launch
        # is abandoned UNDER THE LAUNCH LOCK (masked, observed), the ownership
        # refusal survives, and the launcher collects its own fork.
        with patch.object(os, "fork", wedged_fork), \
                patch.object(emit, "_FIXTURE_CLEANUP_GRACE", 1.0), \
                patch.object(emit._FixtureProcess, "_abandon_unfinished_launch",
                             observe_abandon):
            child = emit._FixtureProcess(time.monotonic() + 0.1, subject=lambda: None)
            refuses(TimeoutError, child._start)
            interrupt_wait(child)
            launcher = child._launcher
            refusals = drive(child)
            assert refusals and "ownership unknown" in refusals[0], refusals
            assert child._abandoned, "the interrupted close did not abandon the launch"
            assert cancellation <= masked_at["transfer"], \
                ("the ownership transfer ran unmasked", masked_at)
            gate.set()
            launcher.join(30)
            assert not launcher.is_alive(), "the abandoned launcher never finished"
            assert child.pid is not None, "the launcher lost its fork"
            assert emit._fixture_child_reaped(child.pid) is True, \
                "the abandoned launcher did not collect its own fork"
        assert open_fds() == before, "the interrupted close leaked descriptors"
        assert no_children(), "the interrupted close leaked a child"

        # Leg 2: SIGINT pending from the gap BEFORE the launcher release ->
        # the launcher is released (never parked), the cancelled launch never
        # forks, and the interrupt lands only after close() returned.
        child = emit._FixtureProcess(time.monotonic() + 30, subject=lambda: None)
        launcher = child._launcher
        real_set, fired = child._go.set, []

        def raising_set():
            if not fired:
                fired.append(True)
                signal.raise_signal(signal.SIGINT)
            return real_set()

        child._go.set = raising_set
        refusals = drive(child)
        assert not refusals, refusals
        launcher.join(30)
        assert not launcher.is_alive(), "the gap cancellation left the launcher parked"
        assert fired and child.pid is None, "the cancelled launch forked anyway"
        assert open_fds() == before and no_children(), "the gap cancellation leaked"

        # Leg 3: launch recorded, SIGINT pending from the completion wait ->
        # close() stays the owner and the WHOLE collection (EOF, drain,
        # receipt validation), masked and observed, completes before the
        # interrupt lands.
        with patch.object(emit._FixtureProcess, "_finish_close", observe_finish):
            child = emit._FixtureProcess(time.monotonic() + 30,
                                         subject=lambda: time.sleep(3600))
            child.start()
            gpid = child.pid
            subject = await_subject(child)
            interrupt_wait(child)
            refusals = drive(child)
        assert not refusals, refusals
        assert cancellation <= masked_at["collect"], \
            ("the collection sequence ran unmasked", masked_at)
        assert child.collected and emit._fixture_child_reaped(gpid) is True, \
            "the interrupted owner did not collect its guardian"
        assert child.status is not None and child.cleaned, \
            "the masked collection did not finish validating"
        await_state(subject, (None, "Z"), "the interrupted owner left the subject")
        assert open_fds() == before and no_children(), "the interrupted owner leaked"

        # Leg 4: SIGINT pending from inside the collection's bounded reap wait
        # -> the reap loop keeps running, collection and validation complete,
        # the interrupt lands after.
        child = emit._FixtureProcess(time.monotonic() + 3600,
                                     subject=lambda: time.sleep(3600))
        child.start()
        gpid = child.pid
        subject = await_subject(child)
        real_waitpid, fired = os.waitpid, []

        def interrupted_waitpid(wpid, flags):
            if not fired and wpid == gpid:
                fired.append(True)
                signal.raise_signal(signal.SIGINT)
            return real_waitpid(wpid, flags)

        with patch.object(os, "waitpid", interrupted_waitpid):
            refusals = drive(child)
        assert fired, "the reap wait was never reached"
        assert not refusals, refusals
        assert child.collected and child.status is not None \
            and emit._fixture_child_reaped(gpid) is True, \
            "the interrupted collection did not reap and validate the guardian"
        await_state(subject, (None, "Z"), "the interrupted collection left the subject")
        assert open_fds() == before and no_children(), "the interrupted collection leaked"

        # Leg 5: SIGINT raised ENTERING the ownership transfer (the QA20 codex
        # F2 gap, before _abandoned is recorded) -> masked, the transfer still
        # records the abandonment under the lock before the interrupt can land.
        gate.clear()

        def inject_abandon(fixture):
            signal.raise_signal(signal.SIGINT)
            return real_abandon(fixture)

        with patch.object(os, "fork", wedged_fork), \
                patch.object(emit, "_FIXTURE_CLEANUP_GRACE", 1.0), \
                patch.object(emit._FixtureProcess, "_abandon_unfinished_launch",
                             inject_abandon):
            child = emit._FixtureProcess(time.monotonic() + 0.1, subject=lambda: None)
            refuses(TimeoutError, child._start)
            launcher = child._launcher
            refusals = drive(child)
            assert refusals and "ownership unknown" in refusals[0], refusals
            assert child._abandoned, \
                "the transfer-entry interrupt lost the abandonment record"
            gate.set()
            launcher.join(30)
            assert not launcher.is_alive(), "the abandoned launcher never finished"
            assert emit._fixture_child_reaped(child.pid) is True, \
                "the abandoned launcher did not collect its own fork"
        assert open_fds() == before and no_children(), "the transfer interrupt leaked"

        # Leg 6 (QA21 claude F1): a PROCESS-directed SIGINT -- the real
        # Ctrl-C delivery shape, os.kill(getpid()), which the kernel may hand
        # to ANY thread with it unblocked -- injected during the wedged-fork
        # window. The launcher is created with the pair blocked, so with this
        # single-threaded caller NO thread can take the delivery: the signal
        # stays kernel-pending through the whole masked sequence (the
        # observation spin completes uninterrupted), the abandonment is
        # recorded, the refusal survives on the delivered interrupt's chain,
        # and the launcher's own mask provably holds the pair.
        gate.clear()
        survived = []

        def process_interrupt_wait(child):
            real_wait, fired = child._launched.wait, []

            def wait(timeout=None):
                if not fired:
                    fired.append(True)
                    os.kill(os.getpid(), signal.SIGINT)  # process-directed
                    spin = time.monotonic() + 2.0
                    while time.monotonic() < spin:
                        pass
                    survived.append(True)
                return real_wait(timeout)

            child._launched.wait = wait

        with patch.object(os, "fork", wedged_fork), \
                patch.object(emit, "_FIXTURE_CLEANUP_GRACE", 1.0):
            child = emit._FixtureProcess(time.monotonic() + 0.1, subject=lambda: None)
            refuses(TimeoutError, child._start)
            process_interrupt_wait(child)
            launcher = child._launcher
            refusals = drive(child)
            assert survived, \
                "the process-directed interrupt materialized inside the masked close"
            assert refusals and "ownership unknown" in refusals[0], refusals
            assert child._abandoned, \
                "the process-directed interrupt lost the abandonment record"
            assert launcher_masks and all(cancellation <= mask
                                          for mask in launcher_masks), \
                ("the launcher thread leaves the cancellation signals "
                 "unblocked", launcher_masks)
            gate.set()
            launcher.join(30)
            assert not launcher.is_alive(), "the abandoned launcher never finished"
            assert emit._fixture_child_reaped(child.pid) is True, \
                "the abandoned launcher did not collect its own fork"
        assert open_fds() == before and no_children(), \
            "the process-directed leg leaked"

        # Leg 7 (QA21 claude F3 / codex F1 / gemini F1): a cancellation whose
        # interpreter flag trips during the mask INSTALLATION itself (modeled:
        # the seam installs the real block, then raises). The prior mask is
        # captured by query before the restoring try and the block is the
        # try's first statement, so the aborted close() restores the caller's
        # exact mask and disposes nothing: a second close() still owns and
        # collects the guardian.
        real_mask = emit._fixture_mask_cancellation

        def mask_then_cancel():
            real_mask()
            raise KeyboardInterrupt("cancelled during the mask installation")

        premask = signal.pthread_sigmask(signal.SIG_BLOCK, set())
        child = emit._FixtureProcess(time.monotonic() + 30,
                                     subject=lambda: time.sleep(3600))
        child.start()
        gpid = child.pid
        with patch.object(emit, "_fixture_mask_cancellation", mask_then_cancel):
            refuses(KeyboardInterrupt, child.close)
        assert signal.pthread_sigmask(signal.SIG_BLOCK, set()) == premask, \
            "the aborted mask installation leaked the cancellation block"
        assert not child.collected, "the aborted close decided the collection"
        child.close()
        assert child.collected and child.status is not None \
            and emit._fixture_child_reaped(gpid) is True, \
            "the rerun close did not collect the guardian"
        assert open_fds() == before and no_children(), "the mask-abort leg leaked"

        # Flip A: report the abandonment WITHOUT recording it under the launch
        # lock (the pre-fix close left _abandoned unset) -> the launcher
        # completes with nothing telling it to collect, and the guardian leaks.
        gate.clear()
        with patch.object(os, "fork", wedged_fork), \
                patch.object(emit, "_FIXTURE_CLEANUP_GRACE", 1.0):
            child = emit._FixtureProcess(time.monotonic() + 0.1, subject=lambda: None)
            refuses(TimeoutError, child._start)
            interrupt_wait(child)
            launcher = child._launcher
            with patch.object(emit._FixtureProcess, "_abandon_unfinished_launch",
                              lambda child: True):
                refusals = drive(child)
            assert refusals and "ownership unknown" in refusals[0], refusals
            assert not child._abandoned, "the flip still recorded the abandonment"
            gate.set()
            launcher.join(30)
            assert not launcher.is_alive(), "the flip launcher never finished"
            assert child.pid is not None, "the flip launcher lost its fork"
            assert emit._fixture_child_reaped(child.pid) is False, \
                "the flip did not reproduce the owner-less guardian"
            os.kill(child.pid, signal.SIGKILL)  # hygiene for the demonstrated leak
            os.waitpid(child.pid, 0)
            emit._fixture_abort_launch(child)   # hygiene: releases control/peer/report
            if child.pidfd is not None:
                os.close(child.pidfd)
                child.pidfd = None
        assert open_fds() == before and no_children(), "the flip hygiene did not complete"

        # Flip B: REMOVE the masking (no-op the mask seam) -> the injected
        # SIGINT materializes INSIDE close() again: the sequence runs unmasked
        # (observed at the collection entry) and close() itself raises the
        # KeyboardInterrupt instead of completing before delivery.
        with patch.object(emit, "_fixture_mask_cancellation",
                          lambda: signal.pthread_sigmask(signal.SIG_BLOCK, set())), \
                patch.object(emit._FixtureProcess, "_finish_close", observe_finish):
            child = emit._FixtureProcess(time.monotonic() + 30,
                                         subject=lambda: time.sleep(3600))
            child.start()
            gpid = child.pid
            subject = await_subject(child)
            interrupt_wait(child)
            refuses(KeyboardInterrupt, child.close)
        assert not (cancellation & masked_at["collect"]), \
            ("the flip still masked the collection", masked_at)
        assert child.collected and emit._fixture_child_reaped(gpid) is True, \
            "the flip backstops did not collect the guardian"
        await_state(subject, (None, "Z"), "the flip backstops left the subject")
        assert open_fds() == before and no_children(), "the flip hygiene did not complete"

        # Fail closed: without pthread_sigmask the fixture REFUSES to launch at
        # all rather than ever run an unmaskable close()/poll() sequence.
        with patch.object(emit, "_fixture_mask_available", lambda: False):
            refuses(emit.ChildStatusUnavailable,
                    lambda: emit._FixtureProcess(time.monotonic() + 5,
                                                 subject=lambda: None))
        assert open_fds() == before and no_children(), "the refused launch leaked"
    elif mode == "escalate-degraded":
        import time
        from contextlib import ExitStack
        caller = os.getpid()
        # QA19 F3: with no pidfd available the escalation can neither freeze the
        # guardian nor address the pid-only receipt's subject, so the refusal
        # must say guardian-only/unverified -- never claim a frozen guardian or
        # a killed subject tree it could not address.
        with tempfile.TemporaryDirectory(prefix="opf-degraded-") as directory:
            wedged = Path(directory, "wedged")
            real_ack = emit._fixture_ack_subject

            def wedge_ack(fd):
                # Wedge AFTER the pid-only receipt send, BEFORE the acknowledgment:
                # the unacknowledged subject refuses by itself on guardian death.
                if os.getpid() != caller:
                    scratch = Path(directory, "wedged.tmp")
                    scratch.write_text(str(os.getpid()), encoding="ascii")
                    scratch.rename(wedged)  # atomic: never a partial PID
                    os.kill(os.getpid(), signal.SIGSTOP)
                real_ack(fd)

            def state(target):
                try:
                    stat = Path("/proc", str(target), "stat").read_bytes()
                except (FileNotFoundError, ProcessLookupError):
                    return None
                return stat.rsplit(b")", 1)[1].split()[0].decode("ascii")

            def await_state(target, wanted, note):
                bound = time.monotonic() + 30
                while state(target) not in wanted:
                    assert time.monotonic() < bound, note
                    time.sleep(0.005)

            def by_pid(child_self, failure):
                # The pre-fix suffix chose by subject_pid and always claimed a
                # frozen guardian and a killed subject tree.
                if child_self.subject_pid is not None:
                    raise emit.ChildStatusUnavailable(
                        str(failure) + "; after bounded-close escalation (guardian "
                        "frozen, subject tree killed, guardian SIGKILL)") from failure
                raise failure

            def exercise(flip):
                wedged.unlink(missing_ok=True)
                with ExitStack() as stack:
                    stack.enter_context(patch.object(emit, "_fixture_pidfd",
                                                     lambda pid: None))
                    stack.enter_context(patch.object(emit, "_fixture_ack_subject",
                                                     wedge_ack))
                    child = emit._FixtureProcess(time.monotonic() + 3600,
                                                 subject=lambda: time.sleep(3600))
                    child.start()
                bound = time.monotonic() + 30
                while not wedged.exists():
                    assert time.monotonic() < bound, \
                        "the guardian never reached its stop point"
                    time.sleep(0.005)
                gpid = int(wedged.read_text(encoding="ascii"))
                assert gpid == child.pid, "the hook stopped an unexpected process"
                await_state(gpid, ("T",), "the guardian did not stop")
                subject = int(Path("/proc", str(gpid), "task", str(gpid), "children")
                              .read_text(encoding="ascii").split()[0])
                failures = []
                with ExitStack() as stack:
                    stack.enter_context(patch.object(emit, "_FIXTURE_CLEANUP_GRACE", 1.0))
                    if flip:
                        stack.enter_context(patch.object(
                            emit._FixtureProcess, "_escalation_refusal", by_pid))
                    try:
                        child.close()
                    except emit.ChildStatusUnavailable as exc:
                        failures.append(str(exc))
                assert failures and "escalat" in failures[0], failures
                assert child.subject_pid == subject, (child.subject_pid, subject)
                await_state(subject, (None, "Z"),
                            "the unacknowledged subject outlived the degraded escalation")
                return failures[0]

            message = exercise(flip=False)
            assert "subject cleanup unverified" in message, message
            assert "guardian not frozen" in message, message
            assert "subject tree killed" not in message, message
            # Flip: the by-pid suffix claims the frozen guardian and killed tree
            # this degraded escalation never performed.
            flipped = exercise(flip=True)
            assert "subject tree killed" in flipped, \
                ("the flip did not reproduce the false claim", flipped)
    elif mode == "poll-masked":
        import time
        # QA20 codex F1 + claude F1 (masked design, fix 2w): poll()'s whole
        # collection-to-failure-recording step runs MASKED and kills the
        # receipt-identified subject pin-first AT COLLECTION time; a validation
        # that still dies inside that window leaves an UNRESOLVED collection
        # (guardian reaped, status never validated, no failure recorded) that
        # close() treats as a failure of its own, and the last-resort interrupt
        # collector addresses the subject of a collected-but-uncleaned guardian
        # instead of returning early. On a pidfd-absent host the un-escalated
        # re-raise names the unverifiable cleanup (QA20 claude F3).
        cancellation = {signal.SIGINT, signal.SIGTERM}
        with tempfile.TemporaryDirectory(prefix="opf-pollmask-") as directory:
            descendant_file = Path(directory, "descendant")

            def subject_body():
                import time as clock
                pid = os.fork()
                if pid == 0:
                    clock.sleep(3600)          # same-group descendant
                scratch = Path(directory, "descendant.tmp")
                scratch.write_text(str(pid), encoding="ascii")
                scratch.rename(descendant_file)  # atomic: never a partial PID
                clock.sleep(3600)              # the subject outlives its guardian

            def state(target):
                try:
                    stat = Path("/proc", str(target), "stat").read_bytes()
                except (FileNotFoundError, ProcessLookupError):
                    return None
                return stat.rsplit(b")", 1)[1].split()[0].decode("ascii")

            def await_state(target, wanted, note):
                bound = time.monotonic() + 30
                while state(target) not in wanted:
                    assert time.monotonic() < bound, note
                    time.sleep(0.005)

            def launch_killed(patches=()):
                # Launch, wait for the running subject tree, then SIGKILL the
                # guardian; the caller collects through poll(), never close().
                from contextlib import ExitStack
                descendant_file.unlink(missing_ok=True)
                with ExitStack() as stack:
                    stack.enter_context(
                        patch.object(emit, "_fixture_pdeathsig", lambda: None))
                    for target, name, value in patches:
                        stack.enter_context(patch.object(target, name, value))
                    child = emit._FixtureProcess(time.monotonic() + 3600,
                                                 subject=subject_body)
                    child.start()
                bound = time.monotonic() + 30
                while not descendant_file.exists():
                    assert time.monotonic() < bound, "the subject tree never appeared"
                    time.sleep(0.005)
                descendant = int(descendant_file.read_text(encoding="ascii"))
                subject = int(Path("/proc", str(child.pid), "task", str(child.pid),
                                   "children").read_text(encoding="ascii").split()[0])
                os.kill(child.pid, signal.SIGKILL)
                return child, subject, descendant

            def poll_failure(child):
                refusals = []
                bound = time.monotonic() + 30
                while not refusals:
                    assert time.monotonic() < bound, "poll never collected"
                    try:
                        assert child.poll() is None
                    except emit.ChildStatusUnavailable as exc:
                        refusals.append(str(exc))
                        break
                    time.sleep(0.005)
                return refusals

            def close_grace(child):
                closed = []
                with patch.object(emit, "_FIXTURE_CLEANUP_GRACE", 1.0):
                    try:
                        child.close()
                    except emit.ChildStatusUnavailable as exc:
                        closed.append(str(exc))
                return closed

            # Leg 1: a SIGINT raised INSIDE the recording step stays pending:
            # poll() still records the failure, kills the subject tree
            # pin-first at collection time, and the interrupt lands only after
            # the masked step restored the caller's mask.
            child, subject, descendant = launch_killed()
            captured = []
            real_read = emit._FixtureProcess._read_report

            def read_hook(fixture, raw):
                captured.append(signal.pthread_sigmask(signal.SIG_BLOCK, set()))
                signal.raise_signal(signal.SIGINT)
                return real_read(fixture, raw)

            refusals, delivered = [], []
            with patch.object(emit._FixtureProcess, "_read_report", read_hook):
                bound = time.monotonic() + 30
                try:
                    while not refusals:
                        assert time.monotonic() < bound, "poll never collected"
                        try:
                            assert child.poll() is None
                        except emit.ChildStatusUnavailable as exc:
                            refusals.append(str(exc))
                            spin = time.monotonic() + 10
                            while time.monotonic() < spin:
                                pass
                            raise AssertionError(
                                "the pending interrupt was never delivered")
                        time.sleep(0.005)
                except KeyboardInterrupt as exc:
                    delivered.append(True)
                    context = exc.__context__
                    while context is not None and not refusals:
                        if isinstance(context, emit.ChildStatusUnavailable):
                            refusals.append(str(context))
                            break
                        context = context.__context__
            assert delivered, "the pending interrupt was never delivered"
            assert captured and cancellation <= captured[0], \
                ("the recording step ran unmasked", captured)
            assert refusals and "guardian failed" in refusals[0], refusals
            assert child.collected and child._failure is not None, \
                "the masked step did not record the failure"
            assert child._subject_kill == "subject-only", child._subject_kill
            assert child.subject_pid == subject, (child.subject_pid, subject)
            # The collection-time kill addressed the SUBJECT before close();
            # the descendant is the disclosed orphan-escape residual (fix 2y,
            # D2: the guardian is dead, so no census runs on this path).
            await_state(subject, (None, "Z"),
                        "poll() left the failed guardian's subject running")
            assert state(descendant) not in (None, "Z"), \
                "the no-guardian path census-killed the orphaned descendant"
            closed = close_grace(child)
            assert closed and "guardian failed" in closed[0], closed
            assert "descendants, if any, unaddressed" in closed[0], closed
            os.kill(descendant, signal.SIGKILL)  # hygiene for the disclosed residual
            await_state(descendant, (None, "Z"),
                        "the residual hygiene did not complete")

            # Leg 2: a validation that dies BETWEEN collection and recording
            # (the pre-mask-flag residual, modeled with a synthetic
            # BaseException) leaves an UNRESOLVED collection: the last-resort
            # interrupt collector still kills the subject tree, and close()
            # treats the unresolved state as a failure and re-raises loudly.
            class Cancelled(BaseException):
                pass

            def poll_cancelled(child):
                with patch.object(emit._FixtureProcess, "_read_report",
                                  side_effect=Cancelled()):
                    bound = time.monotonic() + 30
                    while True:
                        assert time.monotonic() < bound, "poll never collected"
                        try:
                            assert child.poll() is None
                        except Cancelled:
                            break
                        time.sleep(0.005)

            child, subject, descendant = launch_killed()
            poll_cancelled(child)
            assert child.collected and child._failure is None \
                and child.status is None and child.unresolved, \
                "the interrupted validation did not leave an unresolved collection"
            assert state(subject) not in (None, "Z"), \
                "the unresolved leg lost its live subject early"
            child._interrupt_collect()  # the backstop's collected-but-uncleaned path
            await_state(subject, (None, "Z"),
                        "the interrupt collector left the unresolved subject running")
            # Subject-only on the collected (guardian-dead) path: the
            # descendant is the disclosed orphan-escape residual (fix 2y, D2).
            assert state(descendant) not in (None, "Z"), \
                "the interrupt collector census-killed the orphaned descendant"
            closed = close_grace(child)
            assert closed and "supervision unresolved" in closed[0], closed
            assert "descendants, if any, unaddressed" in closed[0], closed
            os.kill(descendant, signal.SIGKILL)  # hygiene for the disclosed residual
            await_state(descendant, (None, "Z"),
                        "the residual hygiene did not complete")

            # Flip: forge a VALIDATED status (the state the pre-fix close
            # believed blindly) -> close() is silent, the tree survives.
            child, subject, descendant = launch_killed()
            poll_cancelled(child)
            child.status = 0
            child.unresolved = False
            closed = close_grace(child)
            assert not closed, closed
            assert state(subject) not in (None, "Z"), \
                "flip: the subject died without close()'s kill"
            os.killpg(subject, signal.SIGKILL)  # hygiene for the demonstrated leak
            await_state(subject, (None, "Z"), "the flip hygiene did not complete")
            await_state(descendant, (None, "Z"), "the flip hygiene did not complete")

            # Leg 2b (QA21 claude F2): an asynchronous exception landing
            # BETWEEN poll()'s two collection-state writes -- after the reap,
            # at the SECOND write's line, located from the live source so the
            # injection tracks the code -- must leave close() OWNING the
            # collection: `unresolved` is written FIRST, so the interruption
            # leaves collected=False and close()'s own reap refuses loudly
            # ("guardian ownership lost"), never the pre-fix silent state
            # (collected recorded, unresolved lost, no failure recorded, the
            # subject surviving a silent close()).
            import inspect

            class TraceCancelled(BaseException):
                pass

            poll_code = emit._FixtureProcess.poll.__code__
            source, start_line = inspect.getsourcelines(emit._FixtureProcess.poll)
            writes = [start_line + index for index, line in enumerate(source)
                      if line.strip() in ("self.collected = True",
                                          "self.unresolved = True")]
            assert len(writes) == 2, writes
            target_line = max(writes)  # the second of the two adjacent writes

            def line_tracer(frame, event, arg):
                if event == "line" and frame.f_lineno == target_line:
                    sys.settrace(None)
                    frame.f_trace = None
                    raise TraceCancelled
                return line_tracer

            def call_tracer(frame, event, arg):
                if event == "call" and frame.f_code is poll_code:
                    return line_tracer
                return None

            def poll_trace_cancelled(child):
                await_state(child.pid, ("Z",), "the killed guardian did not exit")
                cancelled = []
                sys.settrace(call_tracer)
                try:
                    child.poll()
                except TraceCancelled:
                    cancelled.append(True)
                finally:
                    sys.settrace(None)
                assert cancelled, "the trace injection never fired"

            child, subject, descendant = launch_killed()
            poll_trace_cancelled(child)
            assert child.unresolved and not child.collected, \
                ("the interrupted state writes lost close()'s ownership",
                 child.collected, child.unresolved)
            closed = close_grace(child)
            assert closed and "guardian ownership lost" in closed[0], \
                ("close() did not refuse the half-recorded collection loudly",
                 closed)
            # Fix 2y (claude Finding 2): the lost-ownership refusal ATTEMPTS
            # the held receipt's subject kill and disappearance proof instead
            # of raising bare -- the subject dies through its held pidfd, the
            # refusal names the proven kill, and the descendant remains the
            # disclosed subject-only residual.
            assert "receipt subject killed" in closed[0] \
                and "exit proven" in closed[0], closed
            # Fix 2z (gemini F3): the lost-ownership disclosure covers every
            # unaddressed descendant, never just the same-group ones.
            assert "same-group" not in closed[0], closed
            await_state(subject, (None, "Z"),
                        "the lost-ownership refusal left the subject running")
            assert state(descendant) not in (None, "Z"), \
                "the lost-ownership path census-killed the orphaned descendant"
            os.kill(descendant, signal.SIGKILL)  # hygiene for the disclosed residual
            await_state(descendant, (None, "Z"), "the 2b hygiene did not complete")

            # Flip: forge the pre-fix write order's post-interruption state
            # (collected recorded, unresolved lost, nothing else) -> close()
            # is silent and the subject tree survives.
            child, subject, descendant = launch_killed()
            poll_trace_cancelled(child)
            child.collected, child.unresolved = True, False
            closed = close_grace(child)
            assert not closed, closed
            assert state(subject) not in (None, "Z"), \
                "flip: the subject died without close()'s kill"
            os.killpg(subject, signal.SIGKILL)  # hygiene for the demonstrated leak
            await_state(subject, (None, "Z"), "the flip hygiene did not complete")
            await_state(descendant, (None, "Z"), "the flip hygiene did not complete")

            # Leg 3 (QA20 claude F3): pidfd-absent host -- a pid-only receipt's
            # poll-collected failure re-raises through close() NAMING the
            # unverifiable cleanup, and the known subject pid is never
            # group-killed unpinned: the surviving tree is the documented
            # residual, cleaned here by hygiene.
            child, subject, descendant = launch_killed(
                patches=((emit, "_fixture_pidfd", lambda pid: None),))
            refusals = poll_failure(child)
            assert refusals and "guardian failed" in refusals[0], refusals
            assert child.subject_pid == subject and child.subject_pidfd is None, \
                (child.subject_pid, child.subject_pidfd)
            closed = close_grace(child)
            assert closed and "guardian failed" in closed[0], closed
            assert "WITHOUT a subject pidfd" in closed[0] \
                and "subject cleanup unverified" in closed[0], closed
            assert "subject tree killed" not in closed[0], closed
            assert state(subject) not in (None, "Z"), \
                "an unpinned pid-only subject was signalled anyway"
            os.killpg(subject, signal.SIGKILL)  # hygiene for the documented residual
            await_state(subject, (None, "Z"), "the residual hygiene did not complete")
            await_state(descendant, (None, "Z"), "the residual hygiene did not complete")

            # Flip: the pre-fix bare re-raise names nothing.
            def bare(fixture, failure):
                raise failure

            child, subject, descendant = launch_killed(
                patches=((emit, "_fixture_pidfd", lambda pid: None),))
            refusals = poll_failure(child)
            assert refusals, refusals
            closed = []
            with patch.object(emit, "_FIXTURE_CLEANUP_GRACE", 1.0), \
                    patch.object(emit._FixtureProcess, "_unverified_refusal", bare):
                try:
                    child.close()
                except emit.ChildStatusUnavailable as exc:
                    closed.append(str(exc))
            assert closed and "subject cleanup unverified" not in closed[0], closed
            os.killpg(subject, signal.SIGKILL)  # hygiene for the demonstrated blindness
            await_state(subject, (None, "Z"), "the flip hygiene did not complete")
            await_state(descendant, (None, "Z"), "the flip hygiene did not complete")
    elif mode == "unpinned-kill":
        import time
        # QA20 claude F1 + QA21 codex F3: NEVER signal a numeric pid or pgid
        # whose ownership is not pinned -- a held pidfd does not pin the
        # NUMBER once the process is reaped, and SIGSTOP delivery to a ZOMBIE
        # leader cannot stop its parent reaping it and the freed pgid being
        # recycled. The layer therefore issues NO numeric killpg at all (the
        # recorder below turns any into a loud failure): group members are
        # addressed one by one through per-member pidfds, each verified
        # against /proc (group AND an anchored parent chain) AFTER the pidfd
        # is opened. Pin-first: only a leader frozen ALIVE (its pidfd
        # unreadable after the freeze) or a frozen subreaper guardian still
        # parenting a member (the census pin, QA18 gemini F1) anchors the
        # member kill; a reaped or ZOMBIE leader with no census license is
        # reported unpinned and its group members are left disclosed, never
        # guessed at. The subject's own pidfd SIGKILL always runs against the
        # pinned identity.
        recorded = []
        real_pidfd_signal = signal.pidfd_send_signal

        def rec_killpg(pgid, signum):
            recorded.append(("killpg", pgid, signum))
            raise AssertionError(
                "numeric killpg is banned: " + repr((pgid, signum)))

        def rec_pidfd(fd, signum, *args):
            recorded.append(("pidfd", fd, signum))
            return real_pidfd_signal(fd, signum, *args)

        def state(target):
            try:
                stat = Path("/proc", str(target), "stat").read_bytes()
            except (FileNotFoundError, ProcessLookupError):
                return None
            return stat.rsplit(b")", 1)[1].split()[0].decode("ascii")

        def await_state(target, wanted, note):
            bound = time.monotonic() + 30
            while state(target) not in wanted:
                assert time.monotonic() < bound, note
                time.sleep(0.005)

        # Leg 1 (fix 2y, codex F1 under D2): NO GUARDIAN -> NO census. A live
        # leader with a live same-group descendant, addressed WITHOUT
        # guardian ownership (the guardian-dead path), gets a SUBJECT-ONLY
        # kill: SIGSTOP then SIGKILL through the already-held pidfd, the
        # member census NEVER runs (the recorder below turns any call into a
        # loud failure), no descriptor other than the held pidfd is
        # signalled, and the descendant survives as the disclosed
        # orphan-escape residual. The pre-fix layer took one freeze probe as
        # a pin, anchored a census on the subject's bare NUMERIC pid (the
        # recycled-anchor class) and killed the descendant here.
        def banned_census(group, signum, anchors, leader=None):
            raise AssertionError(
                "the no-guardian path ran a member census: "
                + repr((group, signum, anchors, leader)))

        with tempfile.TemporaryDirectory(prefix="opf-noguardian-") as directory:
            descendant_file = Path(directory, "descendant")
            leader = os.fork()
            if leader == 0:
                os.setsid()
                pid = os.fork()
                if pid == 0:
                    time.sleep(3600)           # same-group descendant
                    os._exit(0)
                scratch = Path(directory, "descendant.tmp")
                scratch.write_text(str(pid), encoding="ascii")
                scratch.rename(descendant_file)  # atomic: never a partial PID
                time.sleep(3600)
                os._exit(0)
            bound = time.monotonic() + 30
            while not descendant_file.exists():
                assert time.monotonic() < bound, "the leg-1 tree never appeared"
                time.sleep(0.005)
            descendant = int(descendant_file.read_text(encoding="ascii"))
            fd = os.pidfd_open(leader)
            with patch.object(os, "killpg", rec_killpg), \
                    patch.object(signal, "pidfd_send_signal", rec_pidfd), \
                    patch.object(emit, "_fixture_kill_group_members",
                                 banned_census):
                assert emit._fixture_escalate_subject(leader, fd) \
                    == ("subject-only", None), "no-guardian was not subject-only"
            assert recorded[0] == ("pidfd", fd, signal.SIGSTOP), recorded
            assert not [entry for entry in recorded if entry[0] == "killpg"], recorded
            assert ("pidfd", fd, signal.SIGKILL) in recorded, recorded
            assert all(entry[1] == fd for entry in recorded
                       if entry[0] == "pidfd"), \
                ("a descriptor other than the held subject pidfd was "
                 "signalled", recorded)
            waited, status_raw = os.waitpid(leader, 0)
            assert waited == leader and os.WIFSIGNALED(status_raw) \
                and os.WTERMSIG(status_raw) == signal.SIGKILL, (waited, status_raw)
            os.close(fd)
            assert state(descendant) not in (None, "Z"), \
                "the no-guardian path killed the orphaned descendant"
            os.kill(descendant, signal.SIGKILL)  # hygiene for the disclosed residual
            await_state(descendant, (None, "Z"),
                        "the leg-1 hygiene did not complete")

        # Leg 2: REAPED leader -> nothing pins the freed number against
        # reuse: the member kill is SKIPPED and reported unpinned; the pidfd
        # kill still runs against the pinned identity.
        leader = os.fork()
        if leader == 0:
            os._exit(0)
        fd = os.pidfd_open(leader)
        os.waitpid(leader, 0)  # reaped: the number is free to be recycled
        recorded.clear()
        with patch.object(os, "killpg", rec_killpg), \
                patch.object(signal, "pidfd_send_signal", rec_pidfd):
            assert emit._fixture_escalate_subject(leader, fd) \
                == ("subject-only", None), "a reaped leader licensed a census"
        assert not [entry for entry in recorded if entry[0] == "killpg"], \
            ("an unpinned group was signalled", recorded)
        assert ("pidfd", fd, signal.SIGKILL) in recorded, recorded
        os.close(fd)
        reaped_leader = leader

        # Leg 3 (QA21 codex F3): a ZOMBIE leader accepts the pidfd SIGSTOP --
        # delivery proves only that it exists unreaped NOW -- but its parent
        # can still reap it and the freed pgid can be recycled mid-sequence,
        # so a zombie leader licenses NO member kill: escalation reports
        # unpinned and the surviving same-group descendant is left disclosed
        # (the documented residual), never reached through the recyclable
        # number. The pre-fix code took bare SIGSTOP delivery as the pin and
        # killpg'd the number; the revert flip is the banned-killpg recorder
        # firing and the descendant dying without a pin.
        with tempfile.TemporaryDirectory(prefix="opf-zombie-") as directory:
            descendant_file = Path(directory, "descendant")
            leader = os.fork()
            if leader == 0:
                os.setsid()
                pid = os.fork()
                if pid == 0:
                    time.sleep(3600)           # same-group descendant
                    os._exit(0)
                scratch = Path(directory, "descendant.tmp")
                scratch.write_text(str(pid), encoding="ascii")
                scratch.rename(descendant_file)  # atomic: never a partial PID
                os._exit(0)                    # dies UNREAPED: a zombie leader
            bound = time.monotonic() + 30
            while not descendant_file.exists():
                assert time.monotonic() < bound, "the zombie-leg tree never appeared"
                time.sleep(0.005)
            descendant = int(descendant_file.read_text(encoding="ascii"))
            fd = os.pidfd_open(leader)
            await_state(leader, ("Z",), "the leader never became a zombie")
            assert state(descendant) not in (None, "Z"), "the descendant died early"
            recorded.clear()
            with patch.object(os, "killpg", rec_killpg), \
                    patch.object(signal, "pidfd_send_signal", rec_pidfd):
                assert emit._fixture_escalate_subject(leader, fd) \
                    == ("subject-only", None), "a zombie leader licensed a census"
            assert not [entry for entry in recorded if entry[0] == "killpg"], \
                ("the recyclable freed number was signalled", recorded)
            assert recorded and recorded[0] == ("pidfd", fd, signal.SIGSTOP), recorded
            assert state(descendant) not in (None, "Z"), \
                "an unpinned zombie-leader group was signalled anyway"
            os.kill(descendant, signal.SIGKILL)  # hygiene for the disclosed residual
            os.waitpid(leader, 0)
            os.close(fd)
            await_state(descendant, (None, "Z"),
                        "the zombie-leg hygiene did not complete")

        # Leg 4: the per-member census itself refuses foreign parentage: a
        # live group whose members' parent chains reach NO anchor is never
        # signalled (a recycled pgid names exactly such a group), while the
        # same member dies once the true owner anchors the census.
        decoy = os.fork()
        if decoy == 0:
            os.setsid()
            time.sleep(3600)
            os._exit(0)
        bound = time.monotonic() + 30
        while True:
            try:
                if os.getpgid(decoy) == decoy:
                    break
            except ProcessLookupError:
                pass
            assert time.monotonic() < bound, "the decoy never took its group"
            time.sleep(0.005)
        # Fix 2y (codex F2): the census ACCOUNTS the live member it refused
        # to signal -- ([], [decoy]) -- so no caller can read a refused
        # member as an addressed tree.
        assert emit._fixture_kill_group_members(
            decoy, signal.SIGKILL, {reaped_leader}) == ([], [decoy], []), \
            "an unanchored group member was signalled or unaccounted"
        assert state(decoy) not in (None, "Z"), \
            "the census killed a member outside its anchors"
        delivered, skipped, unverifiable = emit._fixture_kill_group_members(
            decoy, signal.SIGKILL, {os.getpid()})
        assert decoy in delivered and skipped == [] and unverifiable == [], \
            (delivered, skipped, unverifiable)
        waited, status_raw = os.waitpid(decoy, 0)
        assert waited == decoy and os.WIFSIGNALED(status_raw) \
            and os.WTERMSIG(status_raw) == signal.SIGKILL, (waited, status_raw)

        # Leg 5: reaped leader, frozen-guardian census -> a surviving
        # same-group descendant, adopted by the stopped subreaper guardian,
        # pins the group and the member kill still reaches it through its own
        # verified pidfd (QA18 gemini F1 preserved under the pin rule).
        with tempfile.TemporaryDirectory(prefix="opf-pin-") as directory:
            leader_file = Path(directory, "leader")
            grandchild_file = Path(directory, "grandchild")
            frozen_file = Path(directory, "frozen")
            opened = Path(directory, "opened")

            guardian = os.fork()
            if guardian == 0:
                try:
                    import ctypes
                    libc = ctypes.CDLL(None, use_errno=True)
                    if libc.prctl(36, 1, 0, 0, 0) != 0:  # PR_SET_CHILD_SUBREAPER
                        os._exit(125)
                    leader = os.fork()
                    if leader == 0:
                        os.setsid()
                        grandchild = os.fork()
                        if grandchild == 0:
                            time.sleep(3600)  # same-group descendant
                            os._exit(0)
                        scratch = Path(directory, "grandchild.tmp")
                        scratch.write_text(str(grandchild), encoding="ascii")
                        scratch.rename(grandchild_file)
                        time.sleep(3600)      # until the guardian kills it
                        os._exit(0)
                    scratch = Path(directory, "leader.tmp")
                    scratch.write_text(str(leader), encoding="ascii")
                    scratch.rename(leader_file)
                    bound = time.monotonic() + 30
                    while not opened.exists():  # the caller holds the pidfd now
                        if time.monotonic() >= bound:
                            os._exit(125)
                        time.sleep(0.005)
                    os.kill(leader, signal.SIGKILL)
                    os.waitpid(leader, 0)       # REAPED: the number is unpinned
                    scratch = Path(directory, "frozen.tmp")
                    scratch.write_text("stopping", encoding="ascii")
                    scratch.rename(frozen_file)
                    os.kill(os.getpid(), signal.SIGSTOP)
                    os._exit(0)
                except BaseException:
                    os._exit(125)

            def await_file(path, note):
                bound = time.monotonic() + 30
                while not path.exists():
                    assert time.monotonic() < bound, note
                    time.sleep(0.005)

            await_file(leader_file, "the model leader never appeared")
            leader = int(leader_file.read_text(encoding="ascii"))
            fd = os.pidfd_open(leader)
            await_file(grandchild_file, "the model grandchild never appeared")
            grandchild = int(grandchild_file.read_text(encoding="ascii"))
            scratch = Path(directory, "opened.tmp")
            scratch.write_text("y", encoding="ascii")
            scratch.rename(opened)
            await_file(frozen_file, "the model guardian never froze")
            await_state(guardian, ("T",), "the model guardian did not stop")
            assert state(grandchild) not in (None, "Z"), "the descendant died early"
            recorded.clear()
            with patch.object(os, "killpg", rec_killpg), \
                    patch.object(signal, "pidfd_send_signal", rec_pidfd):
                assert emit._fixture_escalate_subject(
                    leader, fd, guardian_pid=guardian) == ("tree", []), \
                    "the frozen-guardian census did not address the group"
            assert not [entry for entry in recorded if entry[0] == "killpg"], recorded
            assert [entry for entry in recorded
                    if entry[0] == "pidfd" and entry[2] == signal.SIGKILL], \
                ("the census-pinned member kill never ran", recorded)
            await_state(grandchild, (None, "Z"),
                        "the census-pinned kill stranded the descendant")
            os.close(fd)
            os.kill(guardian, signal.SIGCONT)
            os.waitpid(guardian, 0)

        # Leg 6 (fix 2y, claude Finding 1): the escalation contract must hold
        # with a subject pidfd AT OR ABOVE FD_SETSIZE (1024). The layer polls
        # pidfds with select.poll everywhere; the pre-fix freeze probe used
        # select.select, whose fd_set raises ValueError there, replacing the
        # loud refusal contract with a bare ValueError and stranding the
        # already-frozen leader unkilled.
        import fcntl
        import resource
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        if soft <= 1024:
            resource.setrlimit(resource.RLIMIT_NOFILE, (min(4096, hard), hard))

        def high_leader():
            pid = os.fork()
            if pid == 0:
                os.setsid()
                time.sleep(3600)
                os._exit(0)
            low = os.pidfd_open(pid)
            high = fcntl.fcntl(low, fcntl.F_DUPFD, 1024)
            os.close(low)
            assert high >= 1024, high
            return pid, high

        leader, fd = high_leader()
        recorded.clear()
        with patch.object(os, "killpg", rec_killpg), \
                patch.object(signal, "pidfd_send_signal", rec_pidfd):
            assert emit._fixture_escalate_subject(leader, fd) \
                == ("subject-only", None), \
                "the high-fd escalation lost its contract"
        assert recorded[0] == ("pidfd", fd, signal.SIGSTOP), recorded
        assert ("pidfd", fd, signal.SIGKILL) in recorded, recorded
        waited, status_raw = os.waitpid(leader, 0)
        assert waited == leader and os.WIFSIGNALED(status_raw) \
            and os.WTERMSIG(status_raw) == signal.SIGKILL, (waited, status_raw)
        os.close(fd)

        # Flip (red-on-revert): the pre-fix select.select probe on the same
        # high descriptor raises ValueError BEFORE any kill, leaving the
        # frozen leader stranded -- the exact fix-2x regression signature.
        import select as select_module

        def legacy_escalate(subject, subject_fd, **license):
            signal.pidfd_send_signal(subject_fd, signal.SIGSTOP)
            if not select_module.select([subject_fd], [], [], 0)[0]:
                pass
            signal.pidfd_send_signal(subject_fd, signal.SIGKILL)
            return ("subject-only", None)

        leader, fd = high_leader()
        refuses(ValueError, lambda: legacy_escalate(leader, fd))
        await_state(leader, ("T",),
                    "the reverted probe did not strand the frozen leader")
        os.kill(leader, signal.SIGKILL)  # hygiene for the demonstrated strand
        os.waitpid(leader, 0)
        os.close(fd)

        # Leg 7 (fix 2y, codex F1/F2 on the GUARDIAN path): with a frozen
        # subreaper guardian and a LIVE leader, the census is anchored on the
        # GUARDIAN alone (never the subject's bare numeric pid), EXCLUDES the
        # leader (members die first; the leader dies LAST through its held
        # pidfd), addresses the descendant, and accounts nothing skipped --
        # only then may the outcome claim the tree ("tree", []).
        with tempfile.TemporaryDirectory(prefix="opf-order-") as directory:
            leader_file = Path(directory, "leader")
            grandchild_file = Path(directory, "grandchild")
            frozen_file = Path(directory, "frozen")

            guardian = os.fork()
            if guardian == 0:
                try:
                    import ctypes
                    libc = ctypes.CDLL(None, use_errno=True)
                    if libc.prctl(36, 1, 0, 0, 0) != 0:  # PR_SET_CHILD_SUBREAPER
                        os._exit(125)
                    leader = os.fork()
                    if leader == 0:
                        os.setsid()
                        grandchild = os.fork()
                        if grandchild == 0:
                            time.sleep(3600)  # same-group descendant
                            os._exit(0)
                        scratch = Path(directory, "grandchild.tmp")
                        scratch.write_text(str(grandchild), encoding="ascii")
                        scratch.rename(grandchild_file)
                        time.sleep(3600)      # LIVE leader, killed last
                        os._exit(0)
                    scratch = Path(directory, "leader.tmp")
                    scratch.write_text(str(leader), encoding="ascii")
                    scratch.rename(leader_file)
                    scratch = Path(directory, "frozen.tmp")
                    scratch.write_text("stopping", encoding="ascii")
                    scratch.rename(frozen_file)
                    os.kill(os.getpid(), signal.SIGSTOP)
                    os._exit(0)
                except BaseException:
                    os._exit(125)

            def await_file(path, note):
                bound = time.monotonic() + 30
                while not path.exists():
                    assert time.monotonic() < bound, note
                    time.sleep(0.005)

            await_file(leader_file, "the leg-7 leader never appeared")
            leader = int(leader_file.read_text(encoding="ascii"))
            await_file(grandchild_file, "the leg-7 grandchild never appeared")
            grandchild = int(grandchild_file.read_text(encoding="ascii"))
            fd = os.pidfd_open(leader)
            await_file(frozen_file, "the leg-7 guardian never froze")
            await_state(guardian, ("T",), "the leg-7 guardian did not stop")
            member_calls = []
            real_members = emit._fixture_kill_group_members

            def rec_members(group, signum, anchors, leader=None):
                result = real_members(group, signum, anchors, leader=leader)
                member_calls.append((group, signum, set(anchors), leader, result))
                return result

            recorded.clear()
            with patch.object(os, "killpg", rec_killpg), \
                    patch.object(signal, "pidfd_send_signal", rec_pidfd), \
                    patch.object(emit, "_fixture_kill_group_members",
                                 rec_members):
                assert emit._fixture_escalate_subject(
                    leader, fd, guardian_pid=guardian) == ("tree", []), \
                    "the live-leader guardian census did not address the tree"
            assert len(member_calls) == 1, member_calls
            group, signum, anchors, excluded, result = member_calls[0]
            assert group == leader and signum == signal.SIGKILL, member_calls
            assert anchors == {guardian}, \
                ("the census took a bare numeric subject anchor", member_calls)
            assert excluded == leader, member_calls
            delivered, skipped, unverifiable = result
            assert grandchild in delivered and leader not in delivered \
                and skipped == [] and unverifiable == [], member_calls
            # The leader dies LAST, through the held pidfd, after the census.
            assert recorded[-1] == ("pidfd", fd, signal.SIGKILL), recorded
            await_state(leader, (None, "Z"), "the leg-7 leader survived")
            await_state(grandchild, (None, "Z"), "the leg-7 descendant survived")
            os.close(fd)
            os.kill(guardian, signal.SIGCONT)
            os.waitpid(guardian, 0)

        # Leg 8 (fix 2y, codex F3): TimeoutError/InterruptedError -- OSError
        # subclasses carrying deadline/cancellation semantics -- PROPAGATE
        # out of the member census and its /proc field reads instead of
        # silently reading as "no members" / "not pinned", which would let a
        # cancelled cleanup carry on as if the census had run.
        sentinel = os.fork()
        if sentinel == 0:
            os.setsid()
            time.sleep(3600)
            os._exit(0)
        bound = time.monotonic() + 30
        while True:
            try:
                if os.getpgid(sentinel) == sentinel:
                    break
            except ProcessLookupError:
                pass
            assert time.monotonic() < bound, "the sentinel never took its group"
            time.sleep(0.005)
        zombie = os.fork()
        if zombie == 0:
            os._exit(0)  # stays UNREAPED below: a stopped-or-zombie "guardian"
        await_state(zombie, ("Z",), "the leg-8 zombie never appeared")

        def raising_listdir(path):
            raise TimeoutError("census deadline")

        with patch.object(os, "listdir", raising_listdir):
            refuses(TimeoutError, lambda: emit._fixture_kill_group_members(
                sentinel, signal.SIGKILL, {os.getpid()}))
            refuses(TimeoutError, lambda: emit._fixture_group_pinned(
                sentinel, zombie))

        real_os_open = os.open

        def raising_open(path, flags, *args, **kwargs):
            # The census reads /proc through os.open (fix 8: no stdlib
            # context manager hides the close from the boundary).
            if str(path).startswith("/proc/"):
                raise InterruptedError("census read interrupted")
            return real_os_open(path, flags, *args, **kwargs)

        with patch.object(os, "open", raising_open):
            refuses(InterruptedError, lambda: emit._fixture_kill_group_members(
                sentinel, signal.SIGKILL, {os.getpid()}))
            refuses(InterruptedError, lambda: emit._fixture_group_pinned(
                sentinel, zombie))
        os.kill(sentinel, signal.SIGKILL)
        os.waitpid(sentinel, 0)
        os.waitpid(zombie, 0)
    elif mode == "census-verify":
        import time
        import types
        # Fix 2z (premise change) + maintainer ruling
        # PD-335-TREE-CLAIM-STALL: "subject tree killed" rests on
        # OBSERVATION -- while the guardian is still frozen, a bounded
        # post-kill verification census must see NO live, signalable group
        # member in TWO CONSECUTIVE clean passes -- never on the kill sends
        # alone, and the claim is worded as that observation, never a proof.
        # Anything
        # the escalation cannot OBSERVE dead downgrades the claim: an
        # unreadable /proc entry is accounted, never read as exited (codex
        # BLOCKER 2 / gemini F2); a leader SIGKILL failing with anything but
        # ProcessLookupError names the surviving leader (gemini F1); a
        # member forked past the kill census's snapshot is observed by the
        # verification census and named (claude F1).
        def state(target):
            try:
                stat = Path("/proc", str(target), "stat").read_bytes()
            except (FileNotFoundError, ProcessLookupError):
                return None
            return stat.rsplit(b")", 1)[1].split()[0].decode("ascii")

        def await_state(target, wanted, note):
            bound = time.monotonic() + 30
            while state(target) not in wanted:
                assert time.monotonic() < bound, note
                time.sleep(0.005)

        def await_file(path, note):
            bound = time.monotonic() + 30
            while not path.exists():
                assert time.monotonic() < bound, note
                time.sleep(0.005)

        def frozen_tree(directory, forker):
            # Frozen subreaper guardian -> setsid leader -> one same-group
            # grandchild; with `forker`, the grandchild forks one more
            # same-group child when cued through the cue file.
            leader_file = Path(directory, "leader")
            grandchild_file = Path(directory, "grandchild")
            frozen_file = Path(directory, "frozen")
            cue = Path(directory, "cue")
            forked_file = Path(directory, "forked")
            guardian = os.fork()
            if guardian == 0:
                try:
                    import ctypes
                    libc = ctypes.CDLL(None, use_errno=True)
                    if libc.prctl(36, 1, 0, 0, 0) != 0:  # PR_SET_CHILD_SUBREAPER
                        os._exit(125)
                    leader = os.fork()
                    if leader == 0:
                        os.setsid()
                        grandchild = os.fork()
                        if grandchild == 0:
                            if forker:
                                bound = time.monotonic() + 30
                                while not cue.exists():
                                    if time.monotonic() >= bound:
                                        os._exit(125)
                                    time.sleep(0.002)
                                forked = os.fork()
                                if forked == 0:
                                    time.sleep(3600)  # forked past the snapshot
                                    os._exit(0)
                                scratch = Path(directory, "forked.tmp")
                                scratch.write_text(str(forked), encoding="ascii")
                                scratch.rename(forked_file)
                            time.sleep(3600)          # same-group descendant
                            os._exit(0)
                        scratch = Path(directory, "grandchild.tmp")
                        scratch.write_text(str(grandchild), encoding="ascii")
                        scratch.rename(grandchild_file)
                        time.sleep(3600)              # LIVE leader, killed last
                        os._exit(0)
                    scratch = Path(directory, "leader.tmp")
                    scratch.write_text(str(leader), encoding="ascii")
                    scratch.rename(leader_file)
                    scratch = Path(directory, "frozen.tmp")
                    scratch.write_text("stopping", encoding="ascii")
                    scratch.rename(frozen_file)
                    os.kill(os.getpid(), signal.SIGSTOP)
                    os._exit(0)
                except BaseException:
                    os._exit(125)
            await_file(leader_file, "the model leader never appeared")
            leader = int(leader_file.read_text(encoding="ascii"))
            await_file(grandchild_file, "the model grandchild never appeared")
            grandchild = int(grandchild_file.read_text(encoding="ascii"))
            await_file(frozen_file, "the model guardian never froze")
            await_state(guardian, ("T",), "the model guardian did not stop")
            assert state(grandchild) not in (None, "Z"), "the descendant died early"
            return guardian, leader, grandchild, cue, forked_file

        def release(guardian):
            os.kill(guardian, signal.SIGCONT)
            os.waitpid(guardian, 0)

        # Leg 1 (codex BLOCKER 2 / gemini F2): an unreadable /proc entry is
        # NEVER proof of exit: the member is accounted (named), unsignalled,
        # and the claim downgrades to "partial". The pre-fix census read the
        # PermissionError as an exited process and claimed ("tree", []) with
        # the member alive.
        with tempfile.TemporaryDirectory(prefix="opf-unread-") as directory:
            guardian, leader, grandchild, _cue, _forked = frozen_tree(
                Path(directory), forker=False)
            fd = os.pidfd_open(leader)
            real_os_open = os.open
            blocked = str(Path("/proc", str(grandchild), "stat"))

            def unreadable(path, flags, *args, **kwargs):
                if str(path) == blocked:
                    raise PermissionError(13, "injected unreadable census entry")
                return real_os_open(path, flags, *args, **kwargs)

            with patch.object(os, "open", unreadable):
                outcome = emit._fixture_escalate_subject(
                    leader, fd, guardian_pid=guardian)
            assert outcome == ("partial", ([], [grandchild])), (
                "an unreadable entry was read as exited, or accounted as an "
                "established member (round 24, claude F1)", outcome)
            # The refusal never asserts membership the census did not
            # establish: the unreadable entry is disclosed as an
            # unverifiable POSSIBLE member (round 24, claude F1). The
            # pre-fix wording claimed "members [pid] unaddressed" for a
            # possibly-foreign unreadable entry.
            fake = types.SimpleNamespace(
                pidfd=None, subject_pidfd=None,
                _subject_kill="partial", _subject_skipped=outcome[1])
            try:
                emit._FixtureProcess._escalation_refusal(
                    fake, emit.ChildStatusUnavailable("recorded failure"))
            except emit.ChildStatusUnavailable as exc:
                named = str(exc)
            else:
                raise AssertionError("the refusal did not raise")
            assert ("entries unverifiable (possible members): [{}]"
                    .format(grandchild)) in named, named
            assert "members [" not in named, named
            assert state(grandchild) not in (None, "Z"), (
                "the unaccounted member was signalled anyway")
            await_state(leader, (None, "Z"), "the leader kill never landed")
            os.close(fd)
            os.kill(grandchild, signal.SIGKILL)  # hygiene for the NAMED member
            await_state(grandchild, (None, "Z"),
                        "the leg-1 hygiene did not complete")
            release(guardian)
        # Leg 2 (gemini F1): a leader SIGKILL failing with anything but
        # ProcessLookupError NAMES the surviving leader and never claims the
        # tree. The pre-fix code swallowed the failure and, with a clean
        # census, still claimed ("tree", []) over the live leader.
        with tempfile.TemporaryDirectory(prefix="opf-leaderfail-") as directory:
            guardian, leader, grandchild, _cue, _forked = frozen_tree(
                Path(directory), forker=False)
            fd = os.pidfd_open(leader)
            real_pidfd_signal = signal.pidfd_send_signal

            def failing_leader_kill(target_fd, signum, *args):
                if target_fd == fd and signum == signal.SIGKILL:
                    raise PermissionError(1, "injected leader kill failure")
                return real_pidfd_signal(target_fd, signum, *args)

            with patch.object(signal, "pidfd_send_signal", failing_leader_kill):
                outcome = emit._fixture_escalate_subject(
                    leader, fd, guardian_pid=guardian)
            assert outcome == ("partial", ([leader], [])), (
                "the failed leader kill was not named", outcome)
            assert state(leader) == "T", (
                "the frozen leader died without its kill", state(leader))
            await_state(grandchild, (None, "Z"),
                        "the census did not address the grandchild")
            signal.pidfd_send_signal(fd, signal.SIGKILL)  # hygiene: the real kill
            await_state(leader, (None, "Z"), "the leg-2 hygiene did not complete")
            os.close(fd)
            release(guardian)

        # Leg 3 (claude F1, the fix-2z premise change): a member FORKED
        # between the kill census's /proc snapshot and its kills is invisible
        # to the snapshot but OBSERVED by the verification census: the claim
        # downgrades to "partial" NAMING the live survivor. The
        # stale-snapshot listdir wrapper makes the ms-scale fork window
        # deterministic. The pre-fix escalation returned ("tree", []) --
        # "every member addressed" -- with the forked member alive and
        # unaccounted.
        with tempfile.TemporaryDirectory(prefix="opf-forkrace-") as directory:
            guardian, leader, grandchild, cue, forked_file = frozen_tree(
                Path(directory), forker=True)
            fd = os.pidfd_open(leader)
            real_listdir = os.listdir
            proc_listings = []

            def stale_listdir(path="."):
                if str(path) != "/proc":
                    return real_listdir(path)
                listing = real_listdir(path)
                proc_listings.append(True)
                if len(proc_listings) == 2:
                    # The kill census's snapshot (the first listing is the
                    # pin check's): cue the fork AFTER the listing is taken
                    # and return the pre-fork (stale) snapshot.
                    scratch = Path(directory, "cue.tmp")
                    scratch.write_text("go", encoding="ascii")
                    scratch.rename(cue)
                    bound = time.monotonic() + 30
                    while not forked_file.exists():
                        assert time.monotonic() < bound, (
                            "the raced fork never appeared")
                        time.sleep(0.002)
                return listing

            with patch.object(emit, "_FIXTURE_CLEANUP_GRACE", 1.0), (
                    patch.object(os, "listdir", stale_listdir)):
                outcome = emit._fixture_escalate_subject(
                    leader, fd, guardian_pid=guardian)
            forked = int(forked_file.read_text(encoding="ascii"))
            assert (outcome[0] == "partial" and outcome[1]
                    and forked in outcome[1][0]), (
                "the fork-raced member was not observed and named",
                outcome, forked)
            assert state(forked) not in (None, "Z"), (
                "the raced member should survive as the NAMED remains")
            await_state(leader, (None, "Z"), "the leg-3 leader survived")
            await_state(grandchild, (None, "Z"), "the leg-3 grandchild survived")
            os.close(fd)
            os.kill(forked, signal.SIGKILL)  # hygiene for the NAMED survivor
            await_state(forked, (None, "Z"), "the leg-3 hygiene did not complete")
            release(guardian)

        # Leg 4 (maintainer ruling PD-335-TREE-CLAIM-STALL): the "tree"
        # observation needs TWO CONSECUTIVE clean censuses -- a /proc scan
        # cannot prove the whole tree died, and a survivor missing from one
        # snapshot can appear in the next. The pre-fix verifier accepted a
        # single clean pass, so a survivor appearing only on the second
        # census was claimed dead under ("tree", []).
        survivor = os.fork()
        if survivor == 0:
            os.setsid()
            time.sleep(3600)
            os._exit(0)
        bound = time.monotonic() + 30
        while True:
            try:
                if os.getpgid(survivor) == survivor:
                    break
            except ProcessLookupError:
                pass
            assert time.monotonic() < bound, "the survivor never took its group"
            time.sleep(0.005)
        real_listdir = os.listdir
        hidden = []

        def hiding_listdir(path="."):
            listing = real_listdir(path)
            if str(path) == "/proc" and not hidden:
                # Only the FIRST census misses the survivor: the second,
                # back-to-back census must catch it.
                hidden.append(True)
                return [name for name in listing if name != str(survivor)]
            return listing

        with patch.object(emit, "_FIXTURE_CLEANUP_GRACE", 1.0), (
                patch.object(os, "listdir", hiding_listdir)):
            outcome = emit._fixture_verify_group_kill(survivor)
        assert outcome == ("partial", ([survivor], [])), (
            "a survivor appearing only on the second census was claimed "
            "dead", outcome)
        assert state(survivor) not in (None, "Z"), (
            "the observation-only verifier signalled the survivor")
        os.kill(survivor, signal.SIGKILL)  # hygiene for the NAMED survivor
        os.waitpid(survivor, 0)
    elif mode == "census-exception":
        import time
        import types
        # Fix 2z (codex BLOCKER 3): cleanup is exception-safe. The subject's
        # held-pidfd SIGKILL runs even if the member census raises, the
        # guardian's SIGKILL runs even if the subject cleanup raises, the
        # exception still propagates, and the recorded accounting ("partial",
        # members unknown) makes the refusal name exactly what ran. The
        # pre-fix escalation let an ordinary census failure strand a frozen
        # guardian and a frozen, unkilled subject. Round 24 extends the
        # contract to the freeze sites (both SIGSTOPs run inside the kill
        # protection), to cancellations raised by the direct guardian
        # backstop (they propagate, chaining the helper failure), and to the
        # double-fault refusal wording ("attempted", never "sent"). QA25
        # (codex) adds pending-cancellation priority: a cancellation ALREADY
        # propagating into the guardian-kill finally stays the outward
        # exception, an ordinary kill-helper failure chained beneath it.
        # QA26 (codex) closes that priority as a CLASS: one exception
        # boundary spans the whole cleanup, direct backstop included, so
        # EVERY later failure -- helper or backstop, ordinary or
        # cancellation -- stays beneath a pending cancellation; QA26
        # (claude MINOR 1) keeps the cancellation's own pre-existing chain
        # intact while doing so.
        def state(target):
            try:
                stat = Path("/proc", str(target), "stat").read_bytes()
            except (FileNotFoundError, ProcessLookupError):
                return None
            return stat.rsplit(b")", 1)[1].split()[0].decode("ascii")

        def await_state(target, wanted, note):
            bound = time.monotonic() + 30
            while state(target) not in wanted:
                assert time.monotonic() < bound, note
                time.sleep(0.005)

        def await_file(path, note):
            bound = time.monotonic() + 30
            while not path.exists():
                assert time.monotonic() < bound, note
                time.sleep(0.005)

        def await_pgid(target, note):
            bound = time.monotonic() + 30
            while os.getpgid(target) != target:
                assert time.monotonic() < bound, note
                time.sleep(0.005)

        def frozen_pair(directory):
            leader_file = Path(directory, "leader")
            frozen_file = Path(directory, "frozen")
            guardian = os.fork()
            if guardian == 0:
                try:
                    import ctypes
                    libc = ctypes.CDLL(None, use_errno=True)
                    if libc.prctl(36, 1, 0, 0, 0) != 0:  # PR_SET_CHILD_SUBREAPER
                        os._exit(125)
                    leader = os.fork()
                    if leader == 0:
                        os.setsid()
                        time.sleep(3600)
                        os._exit(0)
                    scratch = Path(directory, "leader.tmp")
                    scratch.write_text(str(leader), encoding="ascii")
                    scratch.rename(leader_file)
                    scratch = Path(directory, "frozen.tmp")
                    scratch.write_text("stopping", encoding="ascii")
                    scratch.rename(frozen_file)
                    os.kill(os.getpid(), signal.SIGSTOP)
                    os._exit(0)
                except BaseException:
                    os._exit(125)
            await_file(leader_file, "the model leader never appeared")
            await_file(frozen_file, "the model guardian never froze")
            await_state(guardian, ("T",), "the model guardian did not stop")
            # The guardian publishes the leader pid at fork, but the
            # leader runs its own setsid: the caller's subject freeze
            # could land FIRST, pinning the leader in this test
            # process's group for good, so the guardian-anchored
            # census found no group member, no census ran, and the
            # injected census failure was never raised -- the
            # intermittent "fixture was accepted" flake (fix 16,
            # QA37 claude). Hand the pair out only once the leader
            # holds its own group.
            leader = int(leader_file.read_text(encoding="ascii"))
            await_pgid(leader, "the model leader never took its own group")
            return guardian, leader

        # Leg 1: the held-pidfd subject SIGKILL survives a raising census;
        # the census exception still propagates. The pre-fix escalation
        # propagated BEFORE the kill and stranded the frozen leader.
        with tempfile.TemporaryDirectory(prefix="opf-cexc-") as directory:
            guardian, leader = frozen_pair(Path(directory))
            fd = os.pidfd_open(leader)
            with patch.object(emit, "_fixture_kill_group_members",
                              side_effect=RuntimeError("injected census failure")):
                refuses(RuntimeError, lambda: emit._fixture_escalate_subject(
                    leader, fd, guardian_pid=guardian))
            await_state(leader, (None, "Z"),
                        "the raising census stranded the frozen subject")
            os.close(fd)
            os.kill(guardian, signal.SIGCONT)
            os.waitpid(guardian, 0)

        # Leg 2: at the _escalate tier the guardian SIGKILL survives the
        # same failure, the kill that DID run is recorded ("partial",
        # members unknown), and the refusal names it -- never a killed tree.
        # The pre-fix tier propagated before the guardian SIGKILL and
        # recorded nothing.
        with tempfile.TemporaryDirectory(prefix="opf-cexc2-") as directory:
            guardian, leader = frozen_pair(Path(directory))
            guardian_fd = os.pidfd_open(guardian)
            leader_fd = os.pidfd_open(leader)
            fake = types.SimpleNamespace(
                pid=guardian, pidfd=guardian_fd,
                subject_pid=leader, subject_pidfd=leader_fd,
                _subject_kill=None, _subject_skipped=None)
            with patch.object(emit, "_fixture_kill_group_members",
                              side_effect=RuntimeError("injected census failure")):
                refuses(RuntimeError,
                        lambda: emit._FixtureProcess._escalate(fake))
            assert (fake._subject_kill == "partial"
                    and fake._subject_skipped is None), (
                "the interrupted accounting was not recorded",
                fake._subject_kill, fake._subject_skipped)
            bound = time.monotonic() + 30
            while True:
                waited, raw = os.waitpid(guardian, os.WNOHANG)
                if waited == guardian:
                    break
                assert time.monotonic() < bound, (
                    "the raising cleanup stranded the frozen guardian")
                time.sleep(0.005)
            assert os.WIFSIGNALED(raw) and os.WTERMSIG(raw) == signal.SIGKILL, raw
            await_state(leader, (None, "Z"),
                        "the escalate tier stranded the frozen subject")
            try:
                emit._FixtureProcess._escalation_refusal(
                    fake, emit.ChildStatusUnavailable("recorded failure"))
            except emit.ChildStatusUnavailable as exc:
                named = str(exc)
            else:
                raise AssertionError("the refusal did not raise")
            assert "census incomplete" in named and "unknown" in named, named
            assert "tree killed" not in named, named
            os.close(guardian_fd)
            os.close(leader_fd)

        # Leg 3 (fix 2z, claude F4): a census kill RECORDED by a prior
        # interrupted close ("tree"/"partial") is disclosed as that census
        # kill when a later close re-raises the recorded failure -- never as
        # a subject-only kill this close did not run (the pre-fix branch
        # keyed only on `_subject_kill is not None` and relabelled the
        # census kill "subject-only ... no ownership licenses a member
        # census").
        import socket
        import tempfile as tempfile_module
        dead = os.fork()
        if dead == 0:
            os._exit(0)
        dead_fd = os.pidfd_open(dead)  # polls readable once the child exits
        control, peer = socket.socketpair()
        report = tempfile_module.TemporaryFile()
        fake = types.SimpleNamespace(
            pid=os.getpid(), pidfd=None, collected=True, armed=True,
            unresolved=False, cleaned=False, timed_out=False, status=None,
            subject_pid=dead, subject_pidfd=dead_fd,
            _subject_kill="tree", _subject_skipped=[],
            _failure=emit.ChildStatusUnavailable("recorded failure"),
            control=control, peer=peer, report=report)
        # The class methods _finish_close consults, bound to the fake: the
        # receipt is already collected and the recorded kill gates the
        # idempotent re-kill, so both collection helpers are no-ops here;
        # the refusal builder is the REAL one -- it is what this leg tests.
        fake._recv_subject = lambda: None
        fake._address_failed_subject = lambda: None
        fake._interrupt_collect = lambda: None
        fake._escalation_refusal = (
            lambda failure: emit._FixtureProcess._escalation_refusal(fake, failure))
        try:
            emit._FixtureProcess._finish_close(fake)
        except emit.ChildStatusUnavailable as exc:
            relabel = str(exc)
        else:
            raise AssertionError("the recorded failure was not re-raised")
        finally:
            os.waitpid(dead, 0)
        assert "subject tree killed" in relabel, (
            "the recorded census kill was not disclosed", relabel)
        assert "subject-only kill" not in relabel, (
            "the census kill was relabelled subject-only", relabel)
        # Maintainer ruling PD-335-TREE-CLAIM-STALL: the tree claim is
        # worded as an observation with its residual, never a proof.
        assert "an observation, not a proof" in relabel, (
            "the tree claim was not worded as an observation", relabel)
        # QA26 claude MINOR 4: pin the narrowed claim and its residual.
        # Reverting to the stronger "every member addressed" wording or
        # dropping the ruling-mandated residual disclosure turns this red.
        assert "every observed member addressed" in relabel, (
            "the observed-members claim was dropped", relabel)
        assert "every member addressed" not in relabel, (
            "the tree claim regressed to the complete-member wording",
            relabel)
        assert ("a continuously forking chain or pid wraparound can "
                "evade it") in relabel, (
            "the residual disclosure was dropped", relabel)

        # Leg 4 (round 24, codex boundary, subject freeze site): the subject
        # SIGSTOP runs INSIDE the kill protection, so a raising freeze -- a
        # non-OSError delivery fault here -- still reaches the held-pidfd
        # SIGKILL. The pre-fix freeze sat before the try: the exception
        # skipped the kill and stranded the live subject.
        with tempfile.TemporaryDirectory(prefix="opf-freeze-") as directory:
            guardian, leader = frozen_pair(Path(directory))
            fd = os.pidfd_open(leader)
            real_pidfd_signal = signal.pidfd_send_signal

            def freeze_fault(target_fd, signum, *args):
                if target_fd == fd and signum == signal.SIGSTOP:
                    raise RuntimeError("injected freeze failure")
                return real_pidfd_signal(target_fd, signum, *args)

            with patch.object(signal, "pidfd_send_signal", freeze_fault):
                refuses(RuntimeError, lambda: emit._fixture_escalate_subject(
                    leader, fd, guardian_pid=guardian))
            await_state(leader, (None, "Z"),
                        "the freeze exception skipped the held-pidfd "
                        "subject SIGKILL")
            os.close(fd)
            os.kill(guardian, signal.SIGCONT)
            os.waitpid(guardian, 0)

        # Leg 5 (round 24, codex boundary): at the _escalate tier the same
        # subject-freeze fault records only what actually ran -- the
        # held-pidfd SIGKILL now provably runs inside the helper's
        # protection, so the recorded ("partial", None) is honest and the
        # guardian still gets its SIGKILL. The pre-fix tier recorded the
        # partial kill while the freeze exception had SKIPPED the subject
        # SIGKILL, so the retry gate stranded the unkilled subject.
        guardian = os.fork()
        if guardian == 0:
            time.sleep(3600)
            os._exit(0)
        subject = os.fork()
        if subject == 0:
            time.sleep(3600)
            os._exit(0)
        guardian_fd = os.pidfd_open(guardian)
        subject_fd = os.pidfd_open(subject)
        fake = types.SimpleNamespace(
            pid=guardian, pidfd=guardian_fd,
            subject_pid=subject, subject_pidfd=subject_fd,
            _subject_kill=None, _subject_skipped=None)
        real_pidfd_signal = signal.pidfd_send_signal

        def subject_freeze_fault(target_fd, signum, *args):
            if target_fd == subject_fd and signum == signal.SIGSTOP:
                raise RuntimeError("injected freeze failure")
            return real_pidfd_signal(target_fd, signum, *args)

        with patch.object(signal, "pidfd_send_signal", subject_freeze_fault):
            refuses(RuntimeError,
                    lambda: emit._FixtureProcess._escalate(fake))
        assert (fake._subject_kill == "partial"
                and fake._subject_skipped is None), (
            fake._subject_kill, fake._subject_skipped)
        await_state(subject, (None, "Z"),
                    "the recorded subject kill never ran (round 24: the "
                    "freeze fault skipped the held-pidfd SIGKILL)")
        os.waitpid(subject, 0)
        bound = time.monotonic() + 30
        while True:
            waited, raw = os.waitpid(guardian, os.WNOHANG)
            if waited == guardian:
                break
            assert time.monotonic() < bound, (
                "the escalate tier stranded the guardian")
            time.sleep(0.005)
        assert os.WIFSIGNALED(raw) and os.WTERMSIG(raw) == signal.SIGKILL, raw
        os.close(guardian_fd)
        os.close(subject_fd)

        # Leg 6 (round 24, codex boundary, guardian freeze site): the
        # guardian SIGSTOP runs INSIDE the guardian-kill protection, so a
        # raising freeze still reaches the finally's guardian SIGKILL --
        # and records NOTHING, because the subject cleanup never ran and
        # the retry still owns it. The pre-fix freeze sat before the try:
        # the exception skipped both kills.
        guardian = os.fork()
        if guardian == 0:
            time.sleep(3600)
            os._exit(0)
        subject = os.fork()
        if subject == 0:
            time.sleep(3600)
            os._exit(0)
        guardian_fd = os.pidfd_open(guardian)
        subject_fd = os.pidfd_open(subject)
        fake = types.SimpleNamespace(
            pid=guardian, pidfd=guardian_fd,
            subject_pid=subject, subject_pidfd=subject_fd,
            _subject_kill=None, _subject_skipped=None)

        def guardian_freeze_fault(target_fd, signum, *args):
            if target_fd == guardian_fd and signum == signal.SIGSTOP:
                raise RuntimeError("injected freeze failure")
            return real_pidfd_signal(target_fd, signum, *args)

        with patch.object(signal, "pidfd_send_signal", guardian_freeze_fault):
            refuses(RuntimeError,
                    lambda: emit._FixtureProcess._escalate(fake))
        assert fake._subject_kill is None and fake._subject_skipped is None, (
            "a cleanup that never ran was recorded",
            fake._subject_kill, fake._subject_skipped)
        bound = time.monotonic() + 30
        while True:
            waited, raw = os.waitpid(guardian, os.WNOHANG)
            if waited == guardian:
                break
            assert time.monotonic() < bound, (
                "the freeze exception stranded the unkilled guardian")
            time.sleep(0.005)
        assert os.WIFSIGNALED(raw) and os.WTERMSIG(raw) == signal.SIGKILL, (
            "the guardian was not SIGKILLed", raw)
        assert state(subject) not in (None, "Z"), (
            "the subject was addressed by a cleanup that never ran")
        os.kill(subject, signal.SIGKILL)  # hygiene: the retry owns this subject
        os.waitpid(subject, 0)
        os.close(guardian_fd)
        os.close(subject_fd)

        # Leg 7 (round 24, codex BLOCKER 2): a cancellation raised by the
        # direct held-pidfd guardian backstop PROPAGATES, chaining the kill
        # helper's failure as its context. The pre-fix fallback caught every
        # OSError -- TimeoutError/InterruptedError included -- and discarded
        # the cancellation, re-raising only the earlier ordinary failure.
        guardian = os.fork()
        if guardian == 0:
            time.sleep(3600)
            os._exit(0)
        guardian_fd = os.pidfd_open(guardian)
        fake = types.SimpleNamespace(
            pid=guardian, pidfd=guardian_fd,
            subject_pid=None, subject_pidfd=None,
            _subject_kill=None, _subject_skipped=None)

        def cancelled_backstop(target_fd, signum, *args):
            if target_fd == guardian_fd and signum == signal.SIGKILL:
                raise InterruptedError("injected cancellation")
            return real_pidfd_signal(target_fd, signum, *args)

        with patch.object(emit, "_fixture_signal",
                          side_effect=RuntimeError("injected helper failure")), (
                patch.object(signal, "pidfd_send_signal", cancelled_backstop)):
            try:
                emit._FixtureProcess._escalate(fake)
            except InterruptedError as exc:
                assert isinstance(exc.__context__, RuntimeError), (
                    "the helper failure was not chained", exc.__context__)
            except RuntimeError:
                raise AssertionError(
                    "the direct guardian backstop swallowed the "
                    "cancellation (round 24, codex BLOCKER 2)")
            else:
                raise AssertionError("the escalation did not propagate")
        # Both kill paths were injected to fail: the frozen guardian remains
        # for this hygiene kill.
        os.kill(guardian, signal.SIGKILL)
        os.waitpid(guardian, 0)
        os.close(guardian_fd)

        # Leg 8 (round 24, claude F2): under a double fault -- the member
        # census raises AND the held-pidfd subject SIGKILL fails with a
        # non-lookup error -- the recorded ("partial", None) refusal words
        # the kill as ATTEMPTED through the held pidfd, never as "sent":
        # the send failed and the frozen subject survives. The pre-fix
        # wording claimed "subject SIGKILL sent".
        with tempfile.TemporaryDirectory(prefix="opf-dfault-") as directory:
            guardian, leader = frozen_pair(Path(directory))
            guardian_fd = os.pidfd_open(guardian)
            leader_fd = os.pidfd_open(leader)
            fake = types.SimpleNamespace(
                pid=guardian, pidfd=guardian_fd,
                subject_pid=leader, subject_pidfd=leader_fd,
                _subject_kill=None, _subject_skipped=None)

            def denied_subject_kill(target_fd, signum, *args):
                if target_fd == leader_fd and signum == signal.SIGKILL:
                    raise PermissionError(1, "injected subject kill failure")
                return real_pidfd_signal(target_fd, signum, *args)

            with patch.object(emit, "_fixture_kill_group_members",
                              side_effect=RuntimeError("injected census failure")), (
                    patch.object(signal, "pidfd_send_signal",
                                 denied_subject_kill)):
                refuses(RuntimeError,
                        lambda: emit._FixtureProcess._escalate(fake))
            assert (fake._subject_kill == "partial"
                    and fake._subject_skipped is None), (
                fake._subject_kill, fake._subject_skipped)
            # The subject freeze's SIGSTOP is queued when
            # pidfd_send_signal returns, but /proc shows "T" only once
            # the sleeping leader is next scheduled and completes the
            # group-stop, so an immediate read can still observe
            # "S"/"R" (QA36 codex: one intact run failed here while
            # the diagnostic re-read already showed "T"). Wait bounded
            # for the observed stopped state; a subject the denied
            # SIGKILL nevertheless killed would show None/"Z" and
            # fail this wait instead.
            await_state(leader, ("T",),
                        "the denied SIGKILL should leave the frozen "
                        "subject stopped")
            fake.subject_pidfd = None  # a later pidfd-less close re-raises
            try:
                emit._FixtureProcess._escalation_refusal(
                    fake, emit.ChildStatusUnavailable("recorded failure"))
            except emit.ChildStatusUnavailable as exc:
                named = str(exc)
            else:
                raise AssertionError("the refusal did not raise")
            assert ("subject SIGKILL attempted through its held "
                    "pidfd") in named, named
            assert "SIGKILL sent" not in named, named
            os.kill(leader, signal.SIGKILL)  # hygiene for the surviving subject
            await_state(leader, (None, "Z"),
                        "the leg-8 hygiene did not complete")
            os.waitpid(guardian, 0)  # SIGKILLed by the escalate finally
            os.close(guardian_fd)
            os.close(leader_fd)

        # Leg 9 (QA25 codex): a cancellation ALREADY propagating into the
        # guardian-kill finally stays the OUTWARD exception when the
        # ownership-checked kill helper fails with an ordinary error: the
        # helper failure is chained beneath it, never promoted over it. The
        # pre-fix finally re-raised the ordinary helper failure, demoting
        # the pending cancellation to its __context__.
        guardian = os.fork()
        if guardian == 0:
            time.sleep(3600)
            os._exit(0)
        guardian_fd = os.pidfd_open(guardian)
        fake = types.SimpleNamespace(
            pid=guardian, pidfd=guardian_fd,
            subject_pid=None, subject_pidfd=None,
            _subject_kill=None, _subject_skipped=None)

        def cancelled_freeze(target_fd, signum, *args):
            if target_fd == guardian_fd and signum == signal.SIGSTOP:
                raise TimeoutError("injected pending cancellation")
            return real_pidfd_signal(target_fd, signum, *args)

        with patch.object(emit, "_fixture_signal",
                          side_effect=RuntimeError("injected helper failure")), (
                patch.object(signal, "pidfd_send_signal", cancelled_freeze)):
            try:
                emit._FixtureProcess._escalate(fake)
            except TimeoutError as exc:
                assert isinstance(exc.__cause__, RuntimeError), (
                    "the helper failure was not chained beneath the "
                    "pending cancellation", exc.__cause__)
            except RuntimeError:
                raise AssertionError(
                    "an ordinary helper failure displaced the pending "
                    "cancellation (QA25 codex)")
            else:
                raise AssertionError("the escalation did not propagate")
        assert fake._subject_kill is None and fake._subject_skipped is None, (
            "a pending cancellation recorded accounting",
            fake._subject_kill, fake._subject_skipped)
        # The direct held-pidfd backstop ran unpatched for SIGKILL: collect
        # the killed guardian.
        bound = time.monotonic() + 30
        while True:
            waited, raw = os.waitpid(guardian, os.WNOHANG)
            if waited == guardian:
                break
            assert time.monotonic() < bound, (
                "the direct guardian backstop never delivered its SIGKILL")
            time.sleep(0.005)
        assert os.WIFSIGNALED(raw) and os.WTERMSIG(raw) == signal.SIGKILL, raw
        os.close(guardian_fd)

        # Leg 10 (QA26 codex BLOCKER; QA26 claude MINOR 1; QA27 codex):
        # ONE exception boundary spans the guardian-kill cleanup, direct
        # backstop included. The full matrix -- pending (none, ordinary,
        # cancellation with an implicit context, cancellation with an
        # EXPLICIT __cause__) x helper (ok, ordinary, cancellation) x
        # backstop (ok, OSError, non-OSError, cancellation), 48
        # combinations -- proves a pending cancellation ALWAYS stays the
        # outward exception with every later failure kept REACHABLE
        # beneath it as an exception object AND with its own pre-existing
        # chain intact (a triple fault must not unlink it), while every
        # no-pending combination without a helper cancellation keeps its
        # round-24 outward exception. A cancellation the HELPER raises is
        # pending for the direct backstop (fix 7, QA28 codex BLOCKER 1),
        # so it is the boundary exception for EVERY backstop kind, the
        # backstop failure kept reachable beneath IT. The
        # pre-fix finally excluded cancellation-typed helper failures from
        # restoration and let a non-OSError backstop fault escape its
        # OSError-only handler (both displaced the pending cancellation),
        # its raise-from rewrote the cancellation's __context__, and with
        # an explicit __cause__ it kept only a repr note, never the
        # failure object (QA27 codex chain retention).
        guardian = os.fork()
        if guardian == 0:
            time.sleep(3600)
            os._exit(0)
        guardian_fd = os.pidfd_open(guardian)

        def signature(exc):
            if exc is None:
                return None
            return type(exc).__name__ + ": " + str(exc)

        def boundary_signature(boundary):
            if boundary == "helper ordinary":
                return "RuntimeError: helper ordinary"
            if boundary == "helper cancellation":
                return "InterruptedError: helper cancellation"
            if boundary == "backstop oserror":
                return "PermissionError: [Errno 1] backstop OSError"
            if boundary == "backstop non-oserror":
                return "IndexError: backstop non-OSError"
            if boundary == "backstop cancellation":
                return "InterruptedError: backstop cancellation"
            return None

        def run_combo(pending_kind, helper_kind, backstop_kind):
            fake = types.SimpleNamespace(
                pid=guardian, pidfd=guardian_fd,
                subject_pid=None, subject_pidfd=None,
                _subject_kill=None, _subject_skipped=None)
            backstop_calls = []

            def stub_pidfd_signal(target_fd, signum, *args):
                assert target_fd == guardian_fd, (target_fd, signum)
                if signum == signal.SIGSTOP:
                    if pending_kind == "ordinary":
                        raise RuntimeError("pending ordinary")
                    if pending_kind == "cancellation":
                        try:
                            raise ValueError("pre-existing chain")
                        except ValueError:
                            raise TimeoutError("pending cancellation")
                    if pending_kind == "cancellation-cause":
                        raise TimeoutError("pending cancellation") from (
                            ValueError("explicit cause"))
                    return None
                assert signum == signal.SIGKILL, signum
                backstop_calls.append(True)
                if backstop_kind == "oserror":
                    raise PermissionError(1, "backstop OSError")
                if backstop_kind == "non-oserror":
                    raise IndexError("backstop non-OSError")
                if backstop_kind == "cancellation":
                    raise InterruptedError("backstop cancellation")
                return None

            def stub_helper(pid, signum, pidfd=None, *, group=True):
                if helper_kind == "ordinary":
                    raise RuntimeError("helper ordinary")
                if helper_kind == "cancellation":
                    raise InterruptedError("helper cancellation")
                return True

            outward = None
            with patch.object(emit, "_fixture_signal", stub_helper), (
                    patch.object(signal, "pidfd_send_signal",
                                 stub_pidfd_signal)):
                try:
                    emit._FixtureProcess._escalate(fake)
                except BaseException as exc:
                    outward = exc
            combo = (pending_kind, helper_kind, backstop_kind)
            assert (fake._subject_kill is None
                    and fake._subject_skipped is None), (
                combo, fake._subject_kill, fake._subject_skipped)
            assert len(backstop_calls) == (0 if helper_kind == "ok" else 1), (
                combo, backstop_calls)
            return combo, outward

        # The explicit-cause rows compare the cancellation's own
        # __context__ against the no-failure control row's: whatever the
        # ambient handled exception was at construction, the boundary must
        # never rewrite it.
        explicit_context = [None]
        for pending_kind in ("none", "ordinary", "cancellation",
                             "cancellation-cause"):
            for helper_kind in ("ok", "ordinary", "cancellation"):
                for backstop_kind in ("ok", "oserror", "non-oserror",
                                      "cancellation"):
                    combo, outward = run_combo(
                        pending_kind, helper_kind, backstop_kind)
                    if helper_kind == "ok":
                        boundary = None
                    elif (helper_kind == "cancellation"
                          or backstop_kind == "ok"
                          or (backstop_kind == "oserror"
                              and pending_kind in ("none", "ordinary"))):
                        # Fix 7 (QA28 codex BLOCKER 1): a cancellation the
                        # helper raised is pending for the direct
                        # backstop, so it stays the boundary exception
                        # over ANY backstop failure; only an ordinary
                        # helper failure yields to a raising backstop.
                        # Fix 14 (QA35 codex MAJOR): with a cancellation
                        # pending -- at the backstop's own boundary or
                        # the enclosing finally's -- an OSError backstop
                        # fault is no longer discarded: it is re-raised
                        # and attached, so with an ordinary helper
                        # failure and a pending cancellation the
                        # ATTACHED failure is the backstop's, the
                        # helper failure reachable as its __context__.
                        boundary = "helper " + helper_kind
                    else:
                        boundary = "backstop " + backstop_kind
                    boundary_sig = boundary_signature(boundary)
                    if pending_kind in ("cancellation",
                                        "cancellation-cause"):
                        expected = "TimeoutError: pending cancellation"
                    elif boundary_sig is not None:
                        expected = boundary_sig
                    elif pending_kind == "ordinary":
                        expected = "RuntimeError: pending ordinary"
                    else:
                        expected = None
                    assert signature(outward) == expected, (
                        "the outward exception was displaced (QA26 codex)",
                        combo, signature(outward), expected)
                    if pending_kind == "cancellation":
                        assert (signature(outward.__context__)
                                == "ValueError: pre-existing chain"), (
                            "the pending cancellation's pre-existing chain "
                            "was rewritten (QA26 claude MINOR 1)", combo,
                            signature(outward.__context__))
                        if boundary is None:
                            assert outward.__cause__ is None, (
                                combo, signature(outward.__cause__))
                        else:
                            assert (signature(outward.__cause__)
                                    == boundary_sig), (
                                "the cleanup failure was not chained "
                                "beneath the pending cancellation", combo,
                                signature(outward.__cause__), boundary_sig)
                            if boundary.startswith("backstop "):
                                helper_sig = boundary_signature(
                                    "helper " + helper_kind)
                                assert (signature(
                                            outward.__cause__.__context__)
                                        == helper_sig), (
                                    "the helper failure was dropped from "
                                    "the chain", combo,
                                    signature(outward.__cause__.__context__))
                    elif pending_kind == "cancellation-cause":
                        # QA27 codex: an EXPLICIT __cause__ on the pending
                        # cancellation survives untouched, and a cleanup
                        # failure stays reachable as an EXCEPTION OBJECT,
                        # appended at the tail of the pre-existing chain,
                        # never reduced to a repr note.
                        assert (signature(outward.__cause__)
                                == "ValueError: explicit cause"), (
                            "the explicit cause was rewritten", combo,
                            signature(outward.__cause__))
                        if boundary is None:
                            explicit_context[0] = signature(
                                outward.__context__)
                            assert outward.__cause__.__context__ is None, (
                                combo,
                                signature(outward.__cause__.__context__))
                        else:
                            assert (signature(outward.__context__)
                                    == explicit_context[0]), (
                                "the pending cancellation's own "
                                "__context__ was rewritten", combo,
                                signature(outward.__context__),
                                explicit_context[0])
                            assert (signature(outward.__cause__.__context__)
                                    == boundary_sig), (
                                "the cleanup failure was not kept "
                                "reachable beneath the explicit cause "
                                "(QA27 codex)", combo,
                                signature(outward.__cause__.__context__),
                                boundary_sig)
                            if boundary.startswith("backstop "):
                                helper_sig = boundary_signature(
                                    "helper " + helper_kind)
                                assert (signature(
                                            outward.__cause__
                                            .__context__.__context__)
                                        == helper_sig), (
                                    "the helper failure was dropped from "
                                    "the chain", combo,
                                    signature(outward.__cause__
                                              .__context__.__context__))
                    elif pending_kind == "ordinary" and boundary is not None:
                        # Reachability over BOTH edges (fix 7): with a
                        # helper cancellation the backstop failure now
                        # occupies its __cause__ slot, so the ordinary
                        # pending failure sits on its __context__.
                        chain, frontier, seen = [], [outward], set()
                        while frontier:
                            node = frontier.pop()
                            if node is None or id(node) in seen:
                                continue
                            seen.add(id(node))
                            chain.append(signature(node))
                            frontier.extend((node.__cause__,
                                             node.__context__))
                        assert "RuntimeError: pending ordinary" in chain, (
                            "the pending ordinary failure was dropped from "
                            "the chain", combo, chain)
                    if (helper_kind == "cancellation"
                            and backstop_kind in ("oserror",
                                                  "non-oserror",
                                                  "cancellation")):
                        # Fix 7 (QA28 codex BLOCKER 1): the cleanup-born
                        # cancellation stays over the backstop failure,
                        # which attaches beneath IT -- the OSError kind
                        # included (fix 14, QA35 codex MAJOR: it was
                        # discarded outright).
                        if pending_kind in ("none", "ordinary"):
                            helper_node = outward
                        elif pending_kind == "cancellation":
                            helper_node = outward.__cause__
                        else:
                            helper_node = outward.__cause__.__context__
                        backstop_sig = (
                            "PermissionError: [Errno 1] backstop OSError"
                            if backstop_kind == "oserror"
                            else "IndexError: backstop non-OSError"
                            if backstop_kind == "non-oserror"
                            else "InterruptedError: backstop cancellation")
                        assert signature(helper_node) == boundary_sig, (
                            combo, signature(helper_node))
                        assert (signature(helper_node.__cause__)
                                == backstop_sig), (
                            "the backstop failure was not kept beneath "
                            "the cleanup-born cancellation (fix 7, QA28 "
                            "codex BLOCKER 1)", combo,
                            signature(helper_node.__cause__))
        # Every guardian-directed signal was stubbed, none delivered: the
        # guardian survives for this hygiene kill.
        os.kill(guardian, signal.SIGKILL)
        waited, raw = os.waitpid(guardian, 0)
        assert waited == guardian and os.WIFSIGNALED(raw), (waited, raw)
        os.close(guardian_fd)

        # Leg 11 (fix 6, premise change; fix 7, QA28; fix 8, QA29 codex
        # BLOCKER 2 / claude MAJOR 1, MINOR 2 / gemini BLOCKER; maintainer
        # ruling PD-335, narrow and disclose; fix 9, QA30 codex BLOCKER
        # 1/2 / claude MINOR 1/2/3; fix 11, QA32, maintainer ruling
        # PD-335-TAIL option 2): the pending-cancellation boundary is
        # checked structurally as a TRIPWIRE over RECOGNIZED SHAPES --
        # not a whole-language proof. Python's open grammar
        # (reflection, dynamic namespaces, pattern bindings) means a
        # static scan cannot enumerate every way module-owned cleanup
        # could be hidden or a capture subverted; the GUARANTEE
        # therefore rests on the BEHAVIOURAL matrix (leg 19 below),
        # which drives every module-owned cleanup CALL SITE in the
        # computed closure with a real pending cancellation plus a
        # real cleanup fault, the site list DERIVED from that closure
        # so a new call site without a behavioural case fails the
        # self-test. Within the
        # shapes it recognizes, this leg's scope is COMPUTED over the
        # whole close lifecycle and the computation FAILS CLOSED on
        # every call edge it cannot resolve. The GUARANTEE is scoped to cleanup the
        # module OWNS: every module-owned cleanup reachable from the
        # lifecycle entry points (_FixtureProcess.close, _finish_close,
        # _interrupt_collect, _escalate, _address_failed_subject) routes
        # through _cleanup_boundary. DISCLOSED RESIDUAL (PD-335): calls
        # INTO the standard library or other external code -- builtins,
        # declared stdlib modules, the enumerated external-object
        # primitives below, and the launch lock's with-statement
        # __exit__ -- run cleanup this leg cannot see; they are disclosed,
        # never an enforced property, and the module keeps them off its
        # own cleanup paths (its /proc reads go through os.open/os.read
        # with the close as a boundary step, never a hidden stdlib
        # context manager, QA29 codex BLOCKER 1). Inside the closure the
        # leg fails, as cannot-evaluate, anything it cannot statically
        # clear: a call edge it cannot resolve -- an alias, a computed
        # attribute, an undisclosed object method (QA29 claude MAJOR 1)
        # -- a with statement other than the disclosed launch-lock
        # shape, a finally that is not exactly one _cleanup_boundary
        # call, a boundary pending argument hard-wired to None, a
        # boundary step it cannot resolve (every resolved step and every
        # module function passed BY REFERENCE is also traversed, QA29
        # codex BLOCKER 2), a handler exception type it cannot resolve,
        # or unprotected work inside a cancellation-capable handler.
        # Handler hardening: a handler that can catch a cancellation
        # never CONSTRUCTS a new exception (re-raising a captured
        # exception object stays legal: the deferred-re-raise pattern),
        # and must re-raise EVERY cancellation type it can catch before
        # any unprotected work -- by being a bare re-raise, opening with
        # the isinstance guard ON ITS OWN BOUND NAME (QA29 claude MINOR
        # 2), standing after a bare-raising handler that already covers
        # those types, ending in a raise while calling nothing but
        # isinstance and _cleanup_boundary (a final raise may name only
        # the handler's own bound exception or an alias of it, fix 9,
        # QA30 claude MINOR 1), or capturing its exception for a
        # deferred re-raise while doing nothing else but boundary-routed
        # work -- and the capture is held to its promise STRUCTURALLY
        # (fix 9, QA30 codex BLOCKER 1): after a capturing handler,
        # every statement that can run while the captured object may be
        # pending calls nothing but the boundary and isinstance, never
        # rebinds the captured name, and every path re-raises exactly
        # THAT captured object, else cannot-evaluate FAILURE. Receiver
        # identity (fix 9, QA30 codex BLOCKER 2 / claude MINOR 3): a
        # disclosed method name clears a call only on a receiver PROVEN
        # external -- a builtin-typed value or an object built by a call
        # into an imported module -- a proven module class instance is
        # resolved INTO the closure, no disclosed name may shadow a
        # module method, and any receiver that could be module-owned is
        # a cannot-evaluate FAILURE. Fix 10 (QA31 codex BLOCKER 1/2 /
        # gemini BLOCKER b/c) and fix 11 (QA32) hold both promises at
        # the width of the MODELED shapes: after a capture, every
        # boundary call that can run while the capture may be pending
        # passes exactly the CAPTURED NAME as its pending argument,
        # and EVERY boundary call in the closure passes a pending
        # argument of a recognized shape -- a bare name, or the
        # module's cancellation-conditional expression -- any other
        # shape is a cannot-evaluate FAILURE (QA32 claude BLOCKER 2:
        # shape recognition is the tripwire; that the name holds the
        # RIGHT object at runtime is what leg 19 checks at every
        # site). The captured name must not be rebound by any binding
        # form the walk MODELS: assignment in every modeled syntactic
        # shape (a bare annotation with no value binds nothing and is
        # NOT a rebinding, QA32 codex MINOR), a walrus anywhere in an
        # expression, a nested def or class name, and a
        # nonlocal/global of the captured name ANYWHERE in the
        # function, nested defs included (QA32 claude BLOCKER 1) --
        # while the statement forms the walk does not model
        # (for/with/except/del/import targets among them) fail
        # closed, as does a call-free yield/await suspension point
        # after a capture (QA32 claude MINOR 2). A try that follows a
        # capture is walked in FULL (body, else and finally), the
        # capturing try's OWN finally is walked as a successor (QA32
        # codex BLOCKER 1), and a nested def or lambda after a
        # capture has its definition-time work -- decorators and
        # parameter defaults; NOT its parameter/return annotations,
        # which PEP 649 defers on the pinned 3.14 interpreter so
        # nothing spelled inside one runs at definition time (fix
        # 14, QA35 codex/claude MINOR) -- checked like any other
        # call site (QA32 codex BLOCKER 2; a class statement after a
        # capture already fails closed), and the deferral makes
        # the READ the execution point (fix 15, QA36 codex MAJOR).
        # Of that read-triggered deferred-evaluation grammar this
        # leg recognizes EXACTLY one shape: a spelled
        # __annotations__/__annotate__ attribute access, rejected
        # wherever annotation_reads runs -- over a capture's
        # successors, inside a boundary-routed cleanup step, and in
        # a cancellation-capable handler body -- while an UNREAD
        # annotation stays accepted. An INDIRECT evaluation path
        # (getattr, vars, an annotationlib/inspect/typing reader)
        # is itself a call, rejected only where a call scan
        # actually runs -- a recognized capture handler's
        # boundary-call arguments are exempt from that scan -- and
        # every OTHER read-triggered lazy construct (PEP 695 type
        # parameters and aliases among them) is not recognized at
        # all: both limits are DISCLOSED open-grammar residual
        # classes below, held by leg 19, not by this leg (fix 16,
        # QA37 codex/claude MAJOR). A
        # handler whose capture
        # target collides with its own `as` name, and a handler that
        # overwrites its bound name or an alias of it before its
        # final raise, are FAILURES (QA32 codex BLOCKER 3). An
        # imported name counts as external only while the import is
        # its only binding AMONG THE BINDING AND MUTATION FORMS THE
        # SCAN BELOW MODELS and no modeled attribute assignment or
        # unqualified setattr/delattr targets the module object -- a
        # shadowing or poisoning binding drops the name back into the
        # receiver-origin proof, where it resolves into the closure
        # or is a cannot-evaluate FAILURE (name-level
        # OVER-approximation: one poisoning site anywhere
        # disqualifies the name module-wide, fail-closed by
        # construction), and a boundary-step os/signal primitive
        # clears only through an unpoisoned imported module name.
        # DISCLOSED OPEN-GRAMMAR RESIDUAL CLASSES (fix 11, QA32,
        # PD-335-TAIL option 2) -- shapes this tripwire does NOT
        # recognize and does NOT enforce against; each could hide
        # module-owned cleanup from this leg or launder it as
        # external, so they are disclosed here, where leg 11 is
        # defined, and leg 19's behavioural matrix -- not this leg --
        # is what holds every known cleanup site to the guarantee:
        #   - match/case pattern bindings (capture, sequence and
        #     mapping patterns bind names past both binding scanners,
        #     QA32 codex BLOCKER 4);
        #   - reflective module or namespace mutation: a qualified
        #     setattr (builtins.setattr), sys.modules stores,
        #     importlib / __import__, and stores through
        #     globals()/vars()/module __dict__ (QA32 codex BLOCKER 4
        #     / claude MAJOR 2; QA31 gemini c);
        #   - one module imported under TWO names: poisoning one
        #     alias leaves the other cleared, though both reference
        #     the same mutated module object (QA32 codex BLOCKER 4);
        #   - escape of an imported module as a VALUE (alias = os):
        #     mutation through the escaped reference never poisons
        #     the imported name (QA32 gemini c);
        #   - a module self-import: a plain import of the emit module
        #     itself makes module-owned code clear as "external"
        #     (QA32 claude MAJOR 1);
        #   - module-level shadowing of a BUILTIN name: resolve_call's
        #     builtin clearance checks function-local bindings only
        #     (QA32 codex BLOCKER 5 / claude MINOR 1);
        #   - deferred evaluation AT LARGE: annotation_reads
        #     recognizes only the spelled __annotations__/
        #     __annotate__ attribute access, so any OTHER
        #     read-triggered lazy construct evades it -- a PEP 695
        #     type parameter's __bound__/__constraints__/__default__
        #     read, a `type` alias's __value__ read, and whatever
        #     lazy grammar a later interpreter adds -- executing
        #     deferred expressions with no call and no recognized
        #     attribute at the read site (fix 16, QA37 codex/claude
        #     MAJOR; both reproductions are pinned below as
        #     accepted-here, caught-by-leg-19 vectors);
        #   - calls inside a RECOGNIZED capture handler's
        #     boundary-call arguments: the capture branch hands the
        #     handler to deferred_capture_sound and moves on before
        #     the handler-body call scan runs, so an indirect
        #     annotation reader -- or any other call -- spelled in
        #     those arguments runs while the captured exception is
        #     in flight (fix 16, QA37 codex MAJOR; pinned below the
        #     same way).
        # These classes are RESIDUAL, not enforced: their absence
        # from the emit module is a review invariant, and a mutation
        # inside one of them evades this leg while leg 19 still holds
        # every existing boundary CALL SITE in the computed
        # close-lifecycle closure -- each driven at its own pending
        # point, the cancellations born inside owned-handle cleanup
        # included (fix 12, QA33 claude/codex BLOCKER) -- to its
        # runtime behaviour; a boundary call OUTSIDE that closure
        # (_fixture_children's guardian-side census) is outside
        # leg 19's hold (fix 13, QA34 gemini MINOR).
        import ast
        import builtins
        import inspect
        import textwrap

        # MODELED INTERPRETER (fix 14, QA35 gemini, orchestrator
        # decision reconciling QA35 gemini with the QA35 codex/claude
        # annotation finding): this leg models the annotation
        # semantics of the interpreter the project runs -- CPython
        # 3.14 (CI pins python-version 3.14), where PEP 649 defers
        # every def parameter/return annotation and a function-local
        # AnnAssign annotation evaluates nothing on ANY target shape,
        # name, attribute or subscript alike (and yield/await/walrus
        # are SyntaxErrors in every annotation position). On an OLDER
        # interpreter annotations in defs and on non-name AnnAssign
        # targets WOULD execute exactly where this leg no longer
        # scans them, so the leg refuses to run there instead of
        # running with the wrong model.
        def leg11_interpreter_pinned(version_info):
            assert version_info >= (3, 14), (
                "leg 11 models PEP 649 (Python 3.14) annotation "
                "semantics: on this interpreter annotations in defs "
                "and on non-name AnnAssign targets WOULD execute "
                "where the leg does not scan them -- cannot-evaluate "
                "FAILURE (fix 14, QA35 gemini; CI pins "
                "python-version 3.14)", tuple(version_info[:3]))

        leg11_interpreter_pinned(sys.version_info)

        # A level-0 parse keeps docstrings under -OO: members of this tree are
        # indexed, spliced and recompiled below, so they must not follow
        # the interpreter's optimization level.
        module_tree = _optlevel.parse(inspect.getsource(emit))
        module_functions, module_methods, module_classes = {}, {}, {}
        for stmt in module_tree.body:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                module_functions[stmt.name] = stmt
            elif isinstance(stmt, ast.ClassDef):
                module_classes[stmt.name] = stmt
                for inner in stmt.body:
                    if isinstance(inner, (ast.FunctionDef,
                                          ast.AsyncFunctionDef)):
                        module_methods.setdefault(inner.name, []).append(
                            (stmt.name + "." + inner.name, inner))

        # Names bound by a plain `import X` anywhere in the module: a
        # call through one is a call INTO another module -- external
        # code, the DISCLOSED residual (PD-335), never enforced. Fix 10
        # (QA31 codex BLOCKER 2 / gemini BLOCKER c): the import must be
        # the name's ONLY binding -- any other binding form anywhere in
        # the module (assignment in every syntactic shape, a for/with/
        # except/comprehension target, a walrus, a def/class/lambda
        # name or parameter, a from-import, del, global/nonlocal)
        # SHADOWS the name, and an attribute assignment (any name
        # inside an assignment target's subtree counts, so `X.attr =`
        # and `X[i].attr =` both reach X) or an UNQUALIFIED
        # setattr/delattr call POISONS the module object itself.
        # Either disqualifies the name here, so a call through it
        # falls back into the receiver-origin proof and fails closed
        # instead of hiding module-owned cleanup behind an imported
        # name. This scan models exactly the forms its walk below
        # enumerates and NO MORE (fix 11, QA32 codex BLOCKER 4 /
        # gemini c): pattern bindings, reflective mutation (a
        # qualified setattr, sys.modules, importlib, __import__,
        # globals()/vars()/__dict__ stores), a second import alias of
        # the same module, escape of the module as a value, and a
        # self-import of the emit module are the DISCLOSED residual
        # classes at the leg 11 header, not enforced here.
        def external_import_names(tree):
            imported, shadowed = set(), set()

            def shadow_targets(target):
                for leaf in ast.walk(target):
                    if isinstance(leaf, ast.Name):
                        shadowed.add(leaf.id)

            def shadow_params(spec):
                for arg in (spec.posonlyargs + spec.args + spec.kwonlyargs
                            + ([spec.vararg] if spec.vararg else [])
                            + ([spec.kwarg] if spec.kwarg else [])):
                    shadowed.add(arg.arg)

            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        imported.add(
                            alias.asname or alias.name.split(".")[0])
                elif isinstance(node, ast.Assign):
                    for target in node.targets:
                        shadow_targets(target)
                elif isinstance(node, (ast.AnnAssign, ast.AugAssign,
                                       ast.NamedExpr)):
                    shadow_targets(node.target)
                elif isinstance(node, (ast.For, ast.AsyncFor)):
                    shadow_targets(node.target)
                elif isinstance(node, ast.comprehension):
                    shadow_targets(node.target)
                elif isinstance(node, (ast.With, ast.AsyncWith)):
                    for item in node.items:
                        if item.optional_vars is not None:
                            shadow_targets(item.optional_vars)
                elif isinstance(node, ast.Delete):
                    for target in node.targets:
                        shadow_targets(target)
                elif isinstance(node, ast.ExceptHandler):
                    if node.name:
                        shadowed.add(node.name)
                elif isinstance(node, (ast.FunctionDef,
                                       ast.AsyncFunctionDef)):
                    shadowed.add(node.name)
                    shadow_params(node.args)
                elif isinstance(node, ast.Lambda):
                    shadow_params(node.args)
                elif isinstance(node, ast.ClassDef):
                    shadowed.add(node.name)
                elif isinstance(node, ast.ImportFrom):
                    for alias in node.names:
                        shadowed.add(alias.asname or alias.name)
                elif isinstance(node, (ast.Global, ast.Nonlocal)):
                    shadowed.update(node.names)
                elif (isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Name)
                        and node.func.id in ("setattr", "delattr")
                        and node.args
                        and isinstance(node.args[0], ast.Name)):
                    shadowed.add(node.args[0].id)
            return imported - shadowed

        imported_modules = external_import_names(module_tree)
        # A poisoning regression that silently disqualifies a name the
        # closure's disclosed primitives ride on must go red HERE,
        # loudly, never surface as a cryptic receiver failure inside
        # the scope walk.
        assert set(["os", "signal", "select", "time", "errno",
                    "threading"]) <= imported_modules, (
            "an imported name the close lifecycle depends on lost its "
            "external status (fix 10)", sorted(imported_modules))

        # The boundary machinery is the verified primitive the structure
        # routes through (legs 10 and 12..18 prove it dynamically); it is
        # not itself a site these structural rules apply to.
        boundary_internals = {"_cleanup_boundary", "_attach_beneath",
                              "_chain_ids"}
        pending_cancellations = (TimeoutError, InterruptedError,
                                 KeyboardInterrupt)
        step_primitives = {("os", "close"),
                           ("signal", "pidfd_send_signal"),
                           ("signal", "pthread_sigmask")}
        # DISCLOSED external-object touchpoints (PD-335): standard-library
        # code on objects this module constructed. The boundary protects
        # the module-owned CALL SITE; the callee's internals are the
        # disclosed residual. A call this table does not name is a
        # cannot-evaluate FAILURE until it is made in-module or disclosed
        # here.
        external_self_calls = {
            ("control", "close"), ("peer", "close"),
            ("report", "close"), ("report", "seek"), ("report", "read"),
            ("_go", "set"), ("_launched", "wait"), ("_launched", "is_set"),
        }
        # Methods on module-local VALUES, disclosed by NAME -- and, fix 9
        # (QA30 codex BLOCKER 2 / claude MINOR 3), a name only counts
        # when the receiver is PROVEN external: a builtin-typed value (a
        # literal, a display, a fresh-value builtin call, a disclosed
        # method's result, or a local bound only to those) or an object
        # built by a call into an imported module (a select poller),
        # whose methods are the same disclosed external-code residual as
        # the module call that built it. A receiver that could be
        # module-owned -- self, a module class instance, a parameter, an
        # unknown -- is resolved into the closure (a proven module class
        # instance) or is a cannot-evaluate FAILURE. "register" and
        # "poll" left the name set: a poller call is cleared by its
        # proven receiver origin, never by name, and no disclosed name
        # may shadow a module method (asserted below), so a module-owned
        # method can no longer hide behind a stdlib method name (the
        # QA30 .poll() collision).
        external_builtin_methods = frozenset([
            "append", "add", "join", "format", "split", "rsplit",
            "isdecimal", "decode",
        ])
        assert not (external_builtin_methods & set(module_methods)), (
            "a disclosed external method name shadows a module method: "
            "a module-owned method could hide behind it (fix 9, QA30 "
            "claude MINOR 3)",
            sorted(external_builtin_methods & set(module_methods)))
        # Builtin callables whose result is always a FRESH builtin-typed
        # value -- never one of their arguments (unlike min/max/next), so
        # a module-owned object cannot flow through them into a proven
        # receiver.
        builtin_value_makers = frozenset([
            "list", "set", "dict", "tuple", "frozenset", "sorted", "str",
            "bytes", "bytearray", "int", "float", "bool", "repr", "len",
            "range", "sum", "abs",
        ])

        def local_value_kinds(function):
            # Receiver-origin PROOF (fix 9, QA30 codex BLOCKER 2): map
            # each local name to "builtin" (always a builtin-typed
            # value), "extmodule" (an object built by a call into an
            # imported module), or ("modclass", name) (a proven module
            # class instance, resolved into the closure); anything else
            # stays unproven and its method calls are cannot-evaluate
            # FAILURES. A name is proven only when EVERY binding proves
            # the same origin and nothing poisons it: a parameter, a
            # with-item, a handler name, an unsplittable tuple target,
            # del/global/nonlocal. A for-target or comprehension target
            # over a proven iterable enters as "builtin", never
            # "extmodule": an element the module may itself have placed
            # in a container is cleared only through the NAMED methods,
            # which no module method may shadow (asserted above).
            bindings = dict()
            poisoned = set()

            def bind(name, entry):
                bindings.setdefault(name, []).append(entry)

            def poison_names(target):
                for leaf in ast.walk(target):
                    if isinstance(leaf, ast.Name):
                        poisoned.add(leaf.id)

            for node in ast.walk(function):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                                     ast.Lambda)):
                    spec = node.args
                    for arg in (spec.posonlyargs + spec.args
                                + spec.kwonlyargs
                                + ([spec.vararg] if spec.vararg else [])
                                + ([spec.kwarg] if spec.kwarg else [])):
                        poisoned.add(arg.arg)
                elif isinstance(node, ast.Assign):
                    targets = node.targets
                    if (len(targets) == 1
                            and isinstance(targets[0], ast.Tuple)
                            and isinstance(node.value, (ast.Tuple,
                                                        ast.List))
                            and len(targets[0].elts)
                            == len(node.value.elts)
                            and all(isinstance(elt, ast.Name)
                                    for elt in targets[0].elts)):
                        for elt, value in zip(targets[0].elts,
                                              node.value.elts):
                            bind(elt.id, ("value", value))
                    else:
                        for target in targets:
                            if isinstance(target, ast.Name):
                                bind(target.id, ("value", node.value))
                            else:
                                poison_names(target)
                elif isinstance(node, ast.AugAssign):
                    if isinstance(node.target, ast.Name):
                        bind(node.target.id, ("value", node.value))
                elif isinstance(node, ast.AnnAssign):
                    if isinstance(node.target, ast.Name):
                        if node.value is not None:
                            bind(node.target.id, ("value", node.value))
                        else:
                            poisoned.add(node.target.id)
                elif isinstance(node, ast.NamedExpr):
                    bind(node.target.id, ("value", node.value))
                elif isinstance(node, (ast.For, ast.AsyncFor)):
                    if isinstance(node.target, ast.Name):
                        bind(node.target.id, ("iter", node.iter))
                    else:
                        poison_names(node.target)
                elif isinstance(node, ast.comprehension):
                    if isinstance(node.target, ast.Name):
                        bind(node.target.id, ("iter", node.iter))
                    else:
                        poison_names(node.target)
                elif isinstance(node, (ast.With, ast.AsyncWith)):
                    for item in node.items:
                        if item.optional_vars is not None:
                            poison_names(item.optional_vars)
                elif isinstance(node, ast.ExceptHandler):
                    if node.name:
                        poisoned.add(node.name)
                elif isinstance(node, (ast.Global, ast.Nonlocal)):
                    poisoned.update(node.names)
                elif isinstance(node, ast.Delete):
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            poisoned.add(target.id)
            kinds = dict()

            def value_kind(node):
                if isinstance(node, ast.Constant):
                    return "builtin"
                if isinstance(node, (ast.List, ast.Tuple, ast.Set,
                                     ast.Dict, ast.ListComp, ast.SetComp,
                                     ast.DictComp, ast.GeneratorExp,
                                     ast.JoinedStr)):
                    return "builtin"
                if isinstance(node, ast.Name):
                    kind = kinds.get(node.id)
                    return (kind if kind in ("builtin", "extmodule")
                            else None)
                if isinstance(node, ast.BinOp):
                    return ("builtin"
                            if value_kind(node.left)
                            and value_kind(node.right) else None)
                if isinstance(node, ast.IfExp):
                    return ("builtin"
                            if value_kind(node.body)
                            and value_kind(node.orelse) else None)
                if isinstance(node, ast.Subscript):
                    # an element or slice OF a proven external value:
                    # it re-enters as "builtin", so only the NAMED
                    # methods (none of which a module method may
                    # shadow) are cleared on it
                    return ("builtin" if value_kind(node.value)
                            else None)
                if isinstance(node, ast.Call):
                    func = node.func
                    if (isinstance(func, ast.Name)
                            and func.id in builtin_value_makers):
                        return "builtin"
                    if isinstance(func, ast.Attribute):
                        base = func.value
                        if (isinstance(base, ast.Name)
                                and base.id in imported_modules
                                and base.id not in bindings
                                and base.id not in poisoned):
                            # fix 10 (QA31 codex BLOCKER 2): a name
                            # this function binds ANYWHERE is not the
                            # imported module here -- the receiver
                            # stays unproven
                            return "extmodule"
                        if (isinstance(base, ast.Attribute)
                                and isinstance(base.value, ast.Name)
                                and base.value.id in ("self", "cls")
                                and (base.attr, func.attr)
                                in external_self_calls):
                            return "builtin"
                        if isinstance(base, ast.Constant):
                            return "builtin"
                        base_kind = value_kind(base)
                        if base_kind == "extmodule":
                            return "builtin"
                        if (base_kind == "builtin" and func.attr
                                in external_builtin_methods):
                            return "builtin"
                return None

            def modclass_of(node):
                if (isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Name)
                        and node.func.id in module_classes):
                    return node.func.id
                return None

            for _ in range(8):
                changed = False
                for name, entries in bindings.items():
                    if name in poisoned or name in kinds:
                        continue
                    proofs = []
                    for tag, value in entries:
                        if tag == "iter":
                            proofs.append("builtin" if value_kind(value)
                                          else None)
                        else:
                            cls = modclass_of(value)
                            proofs.append(("modclass", cls) if cls
                                          else value_kind(value))
                    if any(proof is None for proof in proofs):
                        continue
                    if all(isinstance(proof, tuple) for proof in proofs):
                        classes = set(proof[1] for proof in proofs)
                        if len(classes) == 1:
                            kinds[name] = ("modclass", classes.pop())
                            changed = True
                        continue
                    if any(isinstance(proof, tuple) for proof in proofs):
                        continue  # mixed module/external: unproven
                    kinds[name] = ("extmodule" if all(
                        proof == "extmodule" for proof in proofs)
                        else "builtin")
                    changed = True
                if not changed:
                    break
            for name in poisoned:
                kinds.pop(name, None)
            # Fix 10 (QA31 codex BLOCKER 2): every name this function
            # binds or poisons keeps an entry, so a local rebinding of
            # an imported module name stays VISIBLE to resolve_call --
            # "local" marks bound-but-unproven, which no clearing
            # branch accepts.
            for name in set(bindings) | poisoned:
                kinds.setdefault(name, "local")
            return kinds, value_kind

        def resolve_call(func, where, nested, kinds, value_kind):
            # Returns the in-module (key, node) targets a call edge
            # reaches (empty when it stays inside this scope), or None
            # when the callee is DISCLOSED external code; anything it
            # cannot resolve is a cannot-evaluate FAILURE (fix 8, QA29
            # claude MAJOR 1), never a silent pass. A method call on a
            # local value resolves through the receiver-origin proof
            # (fix 9): disclosed names count only on proven-external
            # receivers, a proven module class instance resolves into
            # the closure, and an unproven receiver is a FAILURE.
            if isinstance(func, ast.Name):
                name = func.id
                if name in boundary_internals or name in nested:
                    return []
                if name in module_functions:
                    return [("f:" + name, module_functions[name])]
                if name in module_classes:
                    return [("m:" + module_classes[name].name + "."
                             + inner.name, inner)
                            for inner in module_classes[name].body
                            if isinstance(inner, (ast.FunctionDef,
                                                  ast.AsyncFunctionDef))]
                if (callable(getattr(builtins, name, None))
                        and name not in kinds):
                    # fix 10 (QA31 class width): a builtin-named call
                    # clears only while the function itself never
                    # binds that name -- the same shadowing discipline
                    # as the imported-module names. A module-LEVEL
                    # rebinding of a builtin name is NOT caught here:
                    # it is one of the disclosed open-grammar residual
                    # classes at the leg 11 header (fix 11, QA32 codex
                    # BLOCKER 5 / claude MINOR 1) -- it could route
                    # module-owned cleanup through a cleared builtin
                    # name, and only the behavioural matrix (leg 19)
                    # holds such a site to the guarantee.
                    return None
                raise AssertionError((
                    "cannot resolve a call edge in the close-lifecycle "
                    "closure: FAILURE, never a silent pass (fix 8, QA29 "
                    "claude MAJOR 1)", where, ast.dump(func)))
            if isinstance(func, ast.Attribute):
                base = func.value
                if isinstance(base, ast.Name):
                    if base.id in ("self", "cls"):
                        targets = module_methods.get(func.attr, ())
                        assert targets, (
                            "cannot resolve a self-method call in the "
                            "close-lifecycle closure: FAILURE (fix 8)",
                            where, ast.dump(func))
                        return [("m:" + qual, node)
                                for qual, node in targets]
                    if (base.id in imported_modules
                            and base.id not in kinds):
                        # fix 10 (QA31 codex BLOCKER 2 / gemini BLOCKER
                        # c): the imported-name clearance holds only
                        # while the import is the receiver's only
                        # binding -- imported_modules already excludes
                        # every module-wide shadowed or poisoned name,
                        # and any function-local binding (an entry in
                        # kinds, proven or "local") drops the receiver
                        # into the origin proof below
                        return None
                    kind = kinds.get(base.id)
                    if kind == "extmodule":
                        # every method on an object a call into an
                        # imported module built is the same disclosed
                        # residual as that call itself (fix 9)
                        return None
                    if (kind == "builtin"
                            and func.attr in external_builtin_methods):
                        return None
                    if isinstance(kind, tuple):
                        # a PROVEN module class instance: the method is
                        # module-owned and joins the closure (fix 9,
                        # QA30 codex BLOCKER 2)
                        cls = module_classes[kind[1]]
                        targets = [("m:" + cls.name + "." + inner.name,
                                    inner)
                                   for inner in cls.body
                                   if isinstance(inner,
                                                 (ast.FunctionDef,
                                                  ast.AsyncFunctionDef))
                                   and inner.name == func.attr]
                        assert targets, (
                            "cannot resolve a module-class method call "
                            "in the close-lifecycle closure: FAILURE "
                            "(fix 9)", where, ast.dump(func))
                        return targets
                    raise AssertionError((
                        "cannot prove a method receiver external in "
                        "the close-lifecycle closure: FAILURE, never a "
                        "silent pass (fix 9, QA30 codex BLOCKER 2)",
                        where, ast.dump(func)))
                if (isinstance(base, ast.Attribute)
                        and isinstance(base.value, ast.Name)
                        and base.value.id in ("self", "cls")
                        and (base.attr, func.attr) in external_self_calls):
                    return None
                if isinstance(base, ast.Constant):
                    return None  # a str/bytes literal method is pure
                base_kind = value_kind(base)
                if base_kind == "extmodule":
                    return None
                if (base_kind == "builtin"
                        and func.attr in external_builtin_methods):
                    return None
                raise AssertionError((
                    "cannot prove a method receiver external in the "
                    "close-lifecycle closure: FAILURE, never a silent "
                    "pass (fix 9, QA30 codex BLOCKER 2)", where,
                    ast.dump(func)))
            raise AssertionError((
                "cannot resolve a computed call in the close-lifecycle "
                "closure: FAILURE (fix 8, QA29 claude MAJOR 1)", where,
                ast.dump(func)))

        def scope_edges(key, function):
            nested = {inner.name for inner in ast.walk(function)
                      if isinstance(inner, (ast.FunctionDef,
                                            ast.AsyncFunctionDef))
                      and inner is not function}
            kinds, value_kind = local_value_kinds(function)
            for node in ast.walk(function):
                if isinstance(node, ast.Call):
                    targets = resolve_call(node.func, key, nested, kinds,
                                           value_kind)
                    if targets:
                        yield from targets
                elif (isinstance(node, ast.Name)
                        and isinstance(node.ctx, ast.Load)
                        and node.id in module_functions
                        and node.id not in boundary_internals):
                    # A module function REFERENCED without a call --
                    # passed as a boundary step or any other function
                    # reference -- is traversed too: what it reaches is
                    # reachable (fix 8, QA29 codex BLOCKER 2).
                    yield "f:" + node.id, module_functions[node.id]
                elif (isinstance(node, ast.Attribute)
                        and isinstance(node.ctx, ast.Load)
                        and isinstance(node.value, ast.Name)
                        and node.value.id in ("self", "cls")):
                    for qual, target in module_methods.get(node.attr, ()):
                        yield "m:" + qual, target

        def method_node(qualname):
            for candidate, node in module_methods.get(
                    qualname.split(".")[1], ()):
                if candidate == qualname:
                    return node
            raise AssertionError(
                ("a lifecycle entry point is missing (fix 7/8)", qualname))

        scope = {}
        frontier = [
            ("m:_FixtureProcess." + name,
             method_node("_FixtureProcess." + name))
            for name in ("close", "_finish_close", "_interrupt_collect",
                         "_escalate", "_address_failed_subject")
        ]
        while frontier:
            key, node = frontier.pop()
            if key in scope:
                continue
            scope[key] = node
            frontier.extend(scope_edges(key, node))
        # A resolution regression that silently SHRINKS the computed
        # scope must go red, never pass vacuously: these members are
        # known reachable today.
        assert {"f:_fixture_escalate_subject",
                "f:_fixture_kill_group_members",
                "f:_fixture_group_pinned", "f:_fixture_verify_group_kill",
                "f:_fixture_signal", "f:_fixture_stat_fields",
                "f:_fixture_pidfd", "f:_fixture_mask_cancellation",
                "m:_FixtureProcess.close",
                "m:_FixtureProcess._close_masked",
                "m:_FixtureProcess._close_coordinated",
                "m:_FixtureProcess._abandon_unfinished_launch",
                "m:_FixtureProcess._finish_close",
                "m:_FixtureProcess._interrupt_collect",
                "m:_FixtureProcess._escalate",
                "m:_FixtureProcess._address_failed_subject",
                "m:_FixtureProcess._recv_subject",
                "m:_FixtureProcess._read_report",
                "m:_FixtureProcess._record_failure",
                "m:_FixtureProcess._escalation_refusal",
                "m:_FixtureProcess._unverified_refusal",
                "m:_FixtureProcess._ownership_lost_refusal"} <= set(
                    scope), (
            "the computed close-lifecycle closure lost known members "
            "(fix 7/8)", sorted(scope))

        def resolve_exception_classes(node, where):
            elts = node.elts if isinstance(node, ast.Tuple) else [node]
            classes = []
            for element in elts:
                resolved = None
                if isinstance(element, ast.Name):
                    if element.id == "_PENDING_CANCELLATIONS":
                        classes.extend(pending_cancellations)
                        continue
                    resolved = getattr(builtins, element.id, None)
                    if resolved is None and element.id in module_classes:
                        resolved = getattr(emit, element.id, None)
                assert (isinstance(resolved, type)
                        and issubclass(resolved, BaseException)), (
                    "cannot evaluate a close-lifecycle exception type "
                    "statically: FAILURE, never a pass (fix 7)", where,
                    ast.dump(element))
                classes.append(resolved)
            return classes

        def catchable_cancellations(handler, where):
            if handler.type is None:
                return set(pending_cancellations)
            classes = resolve_exception_classes(handler.type, where)
            return {kind for kind in pending_cancellations
                    if any(issubclass(kind, cls) for cls in classes)}

        def bare_raise_only(body):
            return (len(body) == 1 and isinstance(body[0], ast.Raise)
                    and body[0].exc is None)

        def rebound_names(stmt):
            # Every name the statement subtree may BIND (fix 11, QA32
            # codex BLOCKER 3): assignment targets in every modeled
            # shape, a walrus, for/with/except/del/import targets,
            # def/class names, global/nonlocal declarations. A bare
            # annotation with no value binds nothing (QA32 codex
            # MINOR). The walk descends into nested defs;
            # over-collection only ever REMOVES an alias, so it fails
            # closed.
            bound = set()

            def target_names(target):
                for sub in ast.walk(target):
                    if isinstance(sub, ast.Name):
                        bound.add(sub.id)

            for leaf in ast.walk(stmt):
                if isinstance(leaf, ast.Assign):
                    for target in leaf.targets:
                        target_names(target)
                elif isinstance(leaf, (ast.AugAssign, ast.NamedExpr)):
                    target_names(leaf.target)
                elif isinstance(leaf, ast.AnnAssign):
                    if leaf.value is not None:
                        target_names(leaf.target)
                elif isinstance(leaf, (ast.For, ast.AsyncFor)):
                    target_names(leaf.target)
                elif isinstance(leaf, ast.comprehension):
                    target_names(leaf.target)
                elif isinstance(leaf, (ast.With, ast.AsyncWith)):
                    for item in leaf.items:
                        if item.optional_vars is not None:
                            target_names(item.optional_vars)
                elif isinstance(leaf, ast.ExceptHandler):
                    if leaf.name:
                        bound.add(leaf.name)
                elif isinstance(leaf, (ast.FunctionDef,
                                       ast.AsyncFunctionDef,
                                       ast.ClassDef)):
                    bound.add(leaf.name)
                elif isinstance(leaf, (ast.Import, ast.ImportFrom)):
                    for alias in leaf.names:
                        bound.add(alias.asname
                                  or alias.name.split(".")[0])
                elif isinstance(leaf, (ast.Global, ast.Nonlocal)):
                    bound.update(leaf.names)
                elif isinstance(leaf, ast.Delete):
                    for target in leaf.targets:
                        target_names(target)
            return bound

        def ends_in_raise(body, bound):
            # A bare re-raise, or the deferred re-raise of the handler's
            # OWN captured object: a final `raise <name>` counts only
            # when <name> is the handler's bound name or was assigned
            # from it in this handler (fix 9, QA30 claude MINOR 1:
            # any-Name acceptance let a handler end in a raise of an
            # unrelated pre-existing object while swallowing the caught
            # cancellation) -- and the alias must SURVIVE to the raise:
            # any later binding of an alias by anything but a fresh
            # alias-of-alias assignment DISCARDS it, so a handler that
            # overwrites its bound name or an alias before the final
            # raise no longer counts (fix 11, QA32 codex BLOCKER 3:
            # `interrupted: object = None; raise interrupted` raised
            # the overwritten object while swallowing the caught
            # cancellation).
            last = body[-1]
            if not isinstance(last, ast.Raise):
                return False
            if last.exc is None:
                return True
            if not isinstance(last.exc, ast.Name) or bound is None:
                return False
            aliases = set([bound])
            for stmt in body:
                if (isinstance(stmt, ast.Assign)
                        and len(stmt.targets) == 1
                        and isinstance(stmt.targets[0], ast.Name)
                        and isinstance(stmt.value, ast.Name)
                        and stmt.value.id in aliases):
                    aliases.add(stmt.targets[0].id)
                    continue
                aliases -= rebound_names(stmt)
                if not aliases:
                    return False
            return last.exc.id in aliases

        def guard_covers(stmt, required, bound, where):
            # `if isinstance(<bound>, (...)): raise` as the FIRST
            # statement re-raises the named cancellations before any
            # other work; the subject must be the handler's OWN bound
            # name (fix 8, QA29 claude MINOR 2).
            if bound is None:
                return False
            if not (isinstance(stmt, ast.If) and not stmt.orelse
                    and bare_raise_only(stmt.body)):
                return False
            test = stmt.test
            if not (isinstance(test, ast.Call)
                    and isinstance(test.func, ast.Name)
                    and test.func.id == "isinstance"
                    and len(test.args) == 2
                    and isinstance(test.args[0], ast.Name)
                    and test.args[0].id == bound):
                return False
            classes = resolve_exception_classes(test.args[1], where)
            return all(any(issubclass(kind, cls) for cls in classes)
                       for kind in required)

        def capture_shape(body, bound):
            # `except ... as exc: <boundary-routed work>; captured = exc`
            # -- the deferred-re-raise pattern (_close_coordinated): the
            # handler captures the exception and does nothing else but
            # boundary-routed calls. Returns the CAPTURED name so the
            # enclosing code can be held to the promise structurally
            # (fix 9, QA30 codex BLOCKER 1: this shape used to be
            # accepted on the handler alone, so cleanup after the
            # capture could run outside the boundary and displace the
            # captured cancellation), or None when the shape does not
            # match.
            if bound is None:
                return None
            captured = None
            for stmt in body:
                if (isinstance(stmt, ast.Assign)
                        and len(stmt.targets) == 1
                        and isinstance(stmt.targets[0], ast.Name)
                        and isinstance(stmt.value, ast.Name)
                        and stmt.value.id == bound):
                    if captured is not None:
                        return None
                    captured = stmt.targets[0].id
                    assert captured != bound, (
                        "a capture reuses the handler's own bound "
                        "name: Python DELETES that binding when the "
                        "handler exits, so every later use of the "
                        "capture reads an unbound name and the "
                        "cancellation is lost -- FAILURE (fix 11, "
                        "QA32 codex BLOCKER 3)")
                    continue
                if (isinstance(stmt, ast.Expr)
                        and isinstance(stmt.value, ast.Call)
                        and call_target(stmt.value)[:2]
                        == ("name", "_cleanup_boundary")):
                    call = stmt.value
                    if captured is not None and not (
                            call.args
                            and isinstance(call.args[0], ast.Name)
                            and call.args[0].id == captured):
                        # fix 10 (QA31 codex BLOCKER 1): once the
                        # handler has captured, a boundary call in it
                        # must pass the captured object itself as
                        # pending -- any other shape is NOT the
                        # deferred-re-raise pattern
                        return None
                    if any(isinstance(leaf, ast.NamedExpr)
                           and isinstance(leaf.target, ast.Name)
                           and leaf.target.id == captured
                           for leaf in ast.walk(stmt)):
                        # a walrus hidden in the boundary call's own
                        # arguments rebinds the capture (fix 10)
                        return None
                    continue
                return None
            return captured

        def direct_calls(body):
            # Calls this block itself executes: a nested def runs only
            # when invoked, so its BODY is checked where it is routed
            # -- but the definition statement itself EXECUTES its
            # decorators and parameter defaults in the enclosing
            # scope, so those are walked here (fix 11, QA32 codex
            # BLOCKER 2: a default expression ran unprotected
            # cleanup right after a capture). Its parameter and
            # return annotations are NOT definition-time work on the
            # pinned interpreter: PEP 649 defers them until an
            # explicit annotation access, so nothing spelled inside
            # one runs when the def executes, and they are not
            # walked (fix 14, QA35 codex/claude MINOR: modeling them
            # as immediate calls falsely rejected a harmless
            # annotated nested def; the fix-11-era claim that they
            # "execute now" was pre-3.14 semantics). The deferral
            # moves the execution point to the READ:
            # annotation_reads below recognizes that access, and
            # every scan that restricts calls rejects it too
            # (fix 15, QA36 codex MAJOR).
            stack = list(body)
            while stack:
                node = stack.pop()
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                                     ast.Lambda)):
                    spec = node.args
                    stack.extend(spec.defaults)
                    stack.extend(default for default in spec.kw_defaults
                                 if default is not None)
                    if not isinstance(node, ast.Lambda):
                        for decorator in node.decorator_list:
                            # applying a decorator CALLS it with the
                            # function -- with no ast.Call node when
                            # the decorator is bare (fix 12, QA33
                            # codex MAJOR) -- so the application is
                            # modeled as a call of the decorator
                            # expression itself; a call-shaped
                            # decorator (whose RESULT is applied) has
                            # an unresolvable callee and fails closed
                            stack.append(ast.Call(func=decorator,
                                                  args=[],
                                                  keywords=[]))
                    continue
                if isinstance(node, ast.AnnAssign):
                    # a LOCAL annotation expression never evaluates on
                    # the pinned interpreter, so a call spelled inside
                    # it runs nothing: walking it over-rejected an
                    # unevaluated spelling that annotation_reads below
                    # already skips (fix 17, QA38 codex/claude MINOR)
                    # -- only the target and the value can execute
                    # here, so only they are walked
                    stack.append(node.target)
                    if node.value is not None:
                        stack.append(node.value)
                    continue
                if isinstance(node, ast.Call):
                    yield node
                stack.extend(ast.iter_child_nodes(node))

        annotation_evaluators = ("__annotations__", "__annotate__")

        def annotation_reads(body):
            # Under PEP 649 a def's annotations are deferred and their
            # READ is the execution point (fix 15, QA36 codex MAJOR):
            # touching a function's __annotations__ (or __annotate__,
            # whose call evaluates them) runs every expression spelled
            # in its annotations, with NO ast.Call at the read site
            # for the call scans to see. This walks the same
            # executes-now region as direct_calls -- a nested def's or
            # lambda's body, its still-unread annotations, and a
            # LOCAL AnnAssign's annotation expression (which never
            # evaluates on the pinned interpreter and is skipped by
            # BOTH scanners: fix 16, QA37 codex MINOR; fix 17, QA38
            # codex/claude MINOR) stay deferred and accepted -- and
            # yields
            # every attribute access spelled with an evaluating
            # name, in ANY expression context (a store or delete is
            # suspect too: fail closed). That one attribute shape is
            # the WHOLE recognized grammar: an INDIRECT evaluation
            # path -- getattr, vars, an annotationlib/inspect/typing
            # reader -- is a call, rejected only where a call scan
            # actually runs (a recognized capture handler's
            # boundary-call arguments are exempt from that scan),
            # and every OTHER read-triggered lazy construct (PEP 695
            # type parameters and aliases among them) is not
            # recognized at all -- both are DISCLOSED residual
            # classes at the head of this leg, held by leg 19
            # (fix 16, QA37 codex/claude MAJOR).
            stack = list(body)
            while stack:
                node = stack.pop()
                if isinstance(node, (ast.FunctionDef,
                                     ast.AsyncFunctionDef, ast.Lambda)):
                    spec = node.args
                    stack.extend(spec.defaults)
                    stack.extend(default for default in spec.kw_defaults
                                 if default is not None)
                    if not isinstance(node, ast.Lambda):
                        stack.extend(node.decorator_list)
                    continue
                if isinstance(node, ast.AnnAssign):
                    # a LOCAL annotation expression never evaluates
                    # (fix 12/14): scanning it over-rejected an
                    # unevaluated deferred-read spelling (fix 16,
                    # QA37 codex MINOR) -- only the target and the
                    # value can execute here, so only they are
                    # walked
                    stack.append(node.target)
                    if node.value is not None:
                        stack.append(node.value)
                    continue
                if (isinstance(node, ast.Attribute)
                        and node.attr in annotation_evaluators):
                    yield node
                stack.extend(ast.iter_child_nodes(node))

        def call_target(call):
            func = call.func
            if isinstance(func, ast.Name):
                return ("name", func.id)
            if (isinstance(func, ast.Attribute)
                    and isinstance(func.value, ast.Name)):
                return ("attr", func.value.id, func.attr)
            if (isinstance(func, ast.Attribute)
                    and isinstance(func.value, ast.Attribute)
                    and isinstance(func.value.value, ast.Name)
                    and func.value.value.id in ("self", "cls")):
                return ("selfattr", func.value.attr, func.attr)
            return ("opaque", ast.dump(func))

        def boundary_pending_shape(pending_arg, key):
            # fix 11 (QA32 claude BLOCKER 2, PD-335-TAIL option 2): the
            # pending argument of EVERY boundary call in the closure
            # must be a RECOGNIZED shape -- a bare name, or exactly the
            # module's cancellation-conditional expression `<name> if
            # isinstance(<same name>, _PENDING_CANCELLATIONS) else
            # <None or bare name>`. Anything else -- an inverted
            # conditional, a conditional guarding a DIFFERENT name, a
            # computed expression -- is a cannot-evaluate FAILURE.
            # This recognizes the SHAPE only; that the name holds the
            # right object at runtime is what the behavioural matrix
            # (leg 19) checks at every site.
            if isinstance(pending_arg, ast.Name):
                return
            if (isinstance(pending_arg, ast.IfExp)
                    and isinstance(pending_arg.body, ast.Name)
                    and isinstance(pending_arg.test, ast.Call)
                    and isinstance(pending_arg.test.func, ast.Name)
                    and pending_arg.test.func.id == "isinstance"
                    and len(pending_arg.test.args) == 2
                    and not pending_arg.test.keywords
                    and isinstance(pending_arg.test.args[0], ast.Name)
                    and pending_arg.test.args[0].id
                    == pending_arg.body.id
                    and isinstance(pending_arg.test.args[1], ast.Name)
                    and pending_arg.test.args[1].id
                    == "_PENDING_CANCELLATIONS"
                    and (isinstance(pending_arg.orelse, ast.Name)
                         or (isinstance(pending_arg.orelse,
                                        ast.Constant)
                             and pending_arg.orelse.value is None))):
                return
            raise AssertionError((
                "a boundary call's pending argument is not a "
                "recognized shape: cannot evaluate what is pending "
                "at this site -- FAILURE (fix 11, QA32 claude "
                "BLOCKER 2)", key, ast.dump(pending_arg)[:160]))

        def successors_after(function, target, key):
            # The statements that can run after `target` completes,
            # walking out through every enclosing block in execution
            # order (fix 9, QA30 codex BLOCKER 1). Fails closed on an
            # enclosing construct this walk does not model: a loop can
            # re-run statements before the capture.
            def blocks(stmt):
                found = []
                for field in ("body", "orelse", "finalbody"):
                    inner = getattr(stmt, field, None)
                    if inner and isinstance(inner, list):
                        found.append((field, inner))
                for handler in getattr(stmt, "handlers", None) or ():
                    found.append(("handler", handler.body))
                return found

            def find(body):
                for index, stmt in enumerate(body):
                    if stmt is target:
                        # fix 11 (QA32 codex BLOCKER 1): the capturing
                        # try's OWN finally runs after its handler
                        # completes, while the capture may be pending
                        # -- it is a successor under the same rules
                        # (its else is not: with the capture taken,
                        # the else body cannot have run)
                        return [list(getattr(target, "finalbody",
                                             None) or ()),
                                body[index + 1:]]
                    for field, inner in blocks(stmt):
                        path = find(inner)
                        if path is None:
                            continue
                        assert not isinstance(
                            stmt, (ast.For, ast.AsyncFor,
                                   ast.While)), (
                            "a capturing handler sits inside a loop: "
                            "cannot evaluate what re-runs while the "
                            "captured object is pending -- FAILURE "
                            "(fix 9)", key)
                        if isinstance(stmt, try_nodes):
                            if field == "body":
                                path.append(stmt.orelse)
                                path.append(stmt.finalbody)
                            elif field in ("handler", "orelse"):
                                path.append(stmt.finalbody)
                        path.append(body[index + 1:])
                        return path
                return None

            path = find(function.body)
            assert path is not None, (
                "a capturing try was not found in its function "
                "(fix 9)", key)
            return [stmt for block in path for stmt in block]

        def deferred_capture_sound(function, successors, captured,
                                   key):
            # Fix 9 (QA30 codex BLOCKER 1 / claude MINOR 1): after a
            # capturing handler, walk everything that can still run.
            # While the captured object may be pending, a statement may
            # call nothing but _cleanup_boundary and isinstance, never
            # rebinds the captured name, and a raise may raise only THAT
            # captured object; an `if <captured> is not None` guard whose
            # body ends in that re-raise discharges the capture, so the
            # code after it runs only with the capture empty. A
            # STATEMENT form this walk does not model and a call-free
            # yield/await suspension point (fix 11, QA32 claude MINOR
            # 2) are cannot-evaluate FAILURES, and a path that could
            # complete with the capture still pending is a FAILURE:
            # the function would swallow the captured cancellation by
            # returning normally. A nonlocal/global of the captured
            # name ANYWHERE in the function -- a pre-capture nested
            # def used as a boundary step can rebind the capture with
            # zero post-capture binding leaves -- is a FAILURE too
            # (fix 11, QA32 claude BLOCKER 1).
            for leaf in ast.walk(function):
                assert not (isinstance(leaf, (ast.Global, ast.Nonlocal))
                            and captured in leaf.names), (
                    "a nonlocal/global reach-back can rebind the "
                    "captured name from anywhere in the function "
                    "(fix 11, QA32 claude BLOCKER 1)", key)

            def protected_calls(node):
                for call in direct_calls([node]):
                    called = call_target(call)
                    assert called[0] == "name" and called[1] in (
                        "isinstance", "_cleanup_boundary"), (
                        "an unprotected call runs after a capture while "
                        "the captured exception may be pending (fix 9, "
                        "QA30 codex BLOCKER 1)", key,
                        ast.dump(call.func))
                    if called[1] == "_cleanup_boundary":
                        # fix 10 (QA31 codex BLOCKER 1): while the
                        # capture may be pending, the boundary must
                        # receive exactly the captured object -- any
                        # other pending argument re-opens the
                        # displacement the capture promised away
                        assert (call.args
                                and isinstance(call.args[0], ast.Name)
                                and call.args[0].id == captured), (
                            "a boundary call after a capture does not "
                            "pass the captured object as its pending "
                            "argument (fix 10, QA31 codex BLOCKER 1)",
                            key, ast.dump(call)[:160])
                for access in annotation_reads([node]):
                    raise AssertionError((
                        "an annotation READ follows a capture: it "
                        "evaluates deferred PEP 649 annotations -- "
                        "code no call scan saw -- while the captured "
                        "exception may be pending (fix 15, QA36 codex "
                        "MAJOR)", key, access.attr))

            def is_none_guard(stmt):
                return (isinstance(stmt, ast.If) and not stmt.orelse
                        and isinstance(stmt.test, ast.Compare)
                        and isinstance(stmt.test.left, ast.Name)
                        and stmt.test.left.id == captured
                        and len(stmt.test.ops) == 1
                        and isinstance(stmt.test.ops[0], ast.IsNot)
                        and len(stmt.test.comparators) == 1
                        and isinstance(stmt.test.comparators[0],
                                       ast.Constant)
                        and stmt.test.comparators[0].value is None)

            def walk_block(stmts, state):
                for stmt in stmts:
                    if state in ("clear", "terminated"):
                        break
                    leaves = [(stmt, False)]
                    while leaves:
                        # fix 10 (QA31 codex BLOCKER 1): no binding
                        # form may touch the captured name while it may
                        # be pending -- a walrus hides inside any
                        # expression that executes HERE, and a nested
                        # def reaches the name only through
                        # nonlocal/global, scanned over the WHOLE
                        # statement subtree; a def named like the
                        # capture is caught below, and the statement
                        # forms the walk does not model
                        # (for/with/except/del/import/class targets)
                        # already fail closed
                        leaf, nested_def = leaves.pop()
                        # fix 13 (QA34 codex/claude MAJOR): only a
                        # nested def's or lambda's BODY is deferred to
                        # its own call -- its decorators, parameter
                        # defaults and every other definition-time
                        # expression evaluate NOW, in THIS function,
                        # and stay scanned here; and a LOCAL
                        # annotation expression never executes at all
                        # on the pinned PEP 649 interpreter -- on ANY
                        # AnnAssign target shape: name, attribute or
                        # subscript (fix 13, QA34 codex MINOR: nothing
                        # spelled inside one runs or binds; fix 14,
                        # QA35 gemini: pre-3.14, where a non-name
                        # target's annotation WOULD run, the
                        # interpreter pin fails the leg closed) -- so
                        # it is not walked
                        if isinstance(leaf, (ast.FunctionDef,
                                             ast.AsyncFunctionDef)):
                            deferred = set(map(id, leaf.body))
                        elif isinstance(leaf, ast.Lambda):
                            deferred = set((id(leaf.body),))
                        else:
                            deferred = set()
                        skipped = (id(leaf.annotation)
                                   if isinstance(leaf, ast.AnnAssign)
                                   else None)
                        leaves.extend(
                            (child,
                             nested_def or id(child) in deferred)
                            for child in ast.iter_child_nodes(leaf)
                            if id(child) != skipped)
                        # a walrus inside a nested def's or lambda's
                        # BODY binds THAT function's local, never the
                        # enclosing captured name (fix 13, QA34 codex
                        # MINOR); reaching back needs nonlocal/global,
                        # rejected function-wide above and on every
                        # leaf below
                        assert nested_def or not (
                            isinstance(leaf, ast.NamedExpr)
                            and isinstance(leaf.target, ast.Name)
                            and leaf.target.id == captured), (
                            "the captured name is rebound before its "
                            "re-raise (fix 9/10, QA31 codex "
                            "BLOCKER 1)", key)
                        assert not (isinstance(leaf, (ast.Global,
                                                      ast.Nonlocal))
                                    and captured in leaf.names), (
                            "the captured name is rebound before its "
                            "re-raise (fix 9/10, QA31 codex "
                            "BLOCKER 1)", key)
                        # a yield/await INSIDE a nested def's or
                        # lambda's BODY suspends THAT function when
                        # it is called, never this one, so it is no
                        # suspension point here (fix 12, QA33 codex
                        # MINOR; the body is checked where it is
                        # routed) -- but one in a parameter default
                        # or any other definition-time expression
                        # runs in THIS function and generator-
                        # converts it, so only the body is exempt
                        # (fix 13, QA34 codex/claude MAJOR)
                        assert nested_def or not isinstance(
                            leaf, (ast.Yield, ast.YieldFrom,
                                   ast.Await)), (
                            "a suspension point follows a capture: "
                            "cannot evaluate what runs -- or never "
                            "runs, for a dropped generator or "
                            "coroutine -- while the captured "
                            "exception is pending: FAILURE (fix 11, "
                            "QA32 claude MINOR 2)", key)
                    if isinstance(stmt, ast.Raise):
                        assert (isinstance(stmt.exc, ast.Name)
                                and stmt.exc.id == captured
                                and (stmt.cause is None
                                     or isinstance(stmt.cause,
                                                   ast.Name))), (
                            "after a capture, raising anything but the "
                            "captured object could swallow the pending "
                            "cancellation (fix 9, QA30 codex BLOCKER 1 "
                            "/ claude MINOR 1)", key)
                        state = "terminated"
                    elif isinstance(stmt, ast.If):
                        protected_calls(stmt.test)
                        if is_none_guard(stmt):
                            assert walk_block(stmt.body,
                                              "pending") == "terminated", (
                                "an `if <captured> is not None` guard "
                                "does not end by re-raising the captured "
                                "object (fix 9)", key)
                            state = "clear"
                        else:
                            body_state = walk_block(stmt.body, "pending")
                            else_state = (walk_block(stmt.orelse,
                                                     "pending")
                                          if stmt.orelse else "pending")
                            falls = [leg for leg in (body_state,
                                                     else_state)
                                     if leg != "terminated"]
                            state = ("terminated" if not falls
                                     else "clear" if all(
                                         leg == "clear" for leg in falls)
                                     else "pending")
                    elif isinstance(stmt, try_nodes):
                        assert not stmt.handlers, (
                            "a try with handlers follows a capture: "
                            "cannot evaluate the pending path -- FAILURE "
                            "(fix 9)", key)
                        # fix 10 (QA31 gemini BLOCKER b): the else and
                        # finally bodies are walked in FULL under the
                        # same rules -- the old call-only scan of the
                        # finalbody let a zero-call finally rebind the
                        # captured name or raise a foreign exception
                        # over the in-flight re-raise
                        body_state = walk_block(stmt.body, "pending")
                        if stmt.orelse and body_state != "terminated":
                            body_state = walk_block(stmt.orelse,
                                                    body_state)
                        final_state = (walk_block(stmt.finalbody,
                                                  "pending")
                                       if stmt.finalbody
                                       else "pending")
                        state = ("terminated"
                                 if "terminated" in (body_state,
                                                     final_state)
                                 else "clear"
                                 if "clear" in (body_state,
                                                final_state)
                                 else "pending")
                    elif isinstance(stmt, (ast.Assign, ast.AugAssign,
                                           ast.AnnAssign, ast.Expr)):
                        # fix 10 (QA31 codex BLOCKER 1): EVERY
                        # assignment form and EVERY target shape is
                        # scanned -- an AnnAssign with a value, an
                        # AugAssign, and any name inside a tuple,
                        # star, subscript or attribute target; a bare
                        # annotation with NO value binds nothing at
                        # runtime and is not a rebinding (fix 11,
                        # QA32 codex MINOR)
                        for target in (stmt.targets
                                       if isinstance(stmt, ast.Assign)
                                       else []
                                       if isinstance(stmt, ast.Expr)
                                       or (isinstance(stmt,
                                                      ast.AnnAssign)
                                           and stmt.value is None)
                                       else [stmt.target]):
                            for leaf in ast.walk(target):
                                assert not (isinstance(leaf, ast.Name)
                                            and leaf.id == captured), (
                                    "the captured name is rebound "
                                    "before its re-raise (fix 9/10, "
                                    "QA31 codex BLOCKER 1)", key)
                        if isinstance(stmt, ast.AnnAssign):
                            # a LOCAL annotation expression never
                            # executes (fix 12, QA33 codex MINOR: a
                            # call spelled inside a bare AnnAssign's
                            # annotation runs nothing); only the
                            # value -- and a non-simple target's
                            # subexpressions -- can run here
                            if not isinstance(stmt.target, ast.Name):
                                protected_calls(stmt.target)
                            if stmt.value is not None:
                                protected_calls(stmt.value)
                        else:
                            protected_calls(stmt)
                    elif isinstance(stmt, (ast.Pass, ast.FunctionDef,
                                           ast.AsyncFunctionDef)):
                        assert not (isinstance(
                                        stmt, (ast.FunctionDef,
                                               ast.AsyncFunctionDef))
                                    and stmt.name == captured), (
                            "the captured name is rebound before its "
                            "re-raise (fix 9/10, QA31 codex "
                            "BLOCKER 1)", key)
                        if isinstance(stmt, (ast.FunctionDef,
                                             ast.AsyncFunctionDef)):
                            # fix 11 (QA32 codex BLOCKER 2): the
                            # definition EXECUTES its decorators and
                            # defaults now, while the capture may be
                            # pending -- checked like any other call
                            # site (direct_calls yields exactly that
                            # definition-time work; its annotations
                            # run NOTHING under PEP 649 and are not
                            # checked, fix 14, QA35 codex/claude
                            # MINOR)
                            protected_calls(stmt)
                    else:
                        raise AssertionError((
                            "cannot evaluate a statement that follows a "
                            "capture while the captured exception may be "
                            "pending: FAILURE (fix 9)", key,
                            ast.dump(stmt)[:160]))
                return state

            assert walk_block(successors, "pending") != "pending", (
                "a capturing function can complete without re-raising "
                "the captured object (fix 9, QA30 codex BLOCKER 1)",
                key)

        try_nodes = ((ast.Try, ast.TryStar) if hasattr(ast, "TryStar")
                     else (ast.Try,))
        for key in sorted(scope):
            function = scope[key]
            nested = {inner.name: inner for inner in ast.walk(function)
                      if isinstance(inner, (ast.FunctionDef,
                                            ast.AsyncFunctionDef))
                      and inner is not function}
            for node in ast.walk(function):
                if isinstance(node, (ast.With, ast.AsyncWith)):
                    assert (isinstance(node, ast.With)
                            and len(node.items) == 1
                            and node.items[0].optional_vars is None
                            and isinstance(node.items[0].context_expr,
                                           ast.Attribute)
                            and isinstance(
                                node.items[0].context_expr.value,
                                ast.Name)
                            and node.items[0].context_expr.value.id
                            == "self"
                            and node.items[0].context_expr.attr
                            == "_launch_lock"), (
                        "a with statement entered the close-lifecycle "
                        "closure: its __exit__ is cleanup this leg cannot "
                        "statically verify -- cannot-evaluate FAILURE; "
                        "the one DISCLOSED shape is the launch-lock "
                        "with statement (threading lock release, stdlib "
                        "internals, PD-335 residual)", key)
                if (isinstance(node, ast.Call)
                        and call_target(node)[:2]
                        == ("name", "_cleanup_boundary")):
                    assert len(node.args) >= 2, (key, ast.dump(node))
                    pending_arg = node.args[0]
                    assert not (isinstance(pending_arg, ast.Constant)
                                and pending_arg.value is None), (
                        "a boundary call hard-wires pending=None (fix 7)",
                        key)
                    boundary_pending_shape(pending_arg, key)
                    step = node.args[1]
                    assert isinstance(step, ast.Name), (
                        "cannot resolve a boundary step statically: "
                        "FAILURE, never a pass (fix 7)", key,
                        ast.dump(step))
                    target = (nested.get(step.id)
                              or module_functions.get(step.id))
                    assert target is not None, (
                        "a boundary step does not resolve to an in-module "
                        "function: FAILURE, never a pass (fix 7)", key,
                        step.id)
                    for call in direct_calls(target.body):
                        called = call_target(call)
                        assert (called[0] == "name" and (
                                    called[1] in ("isinstance",
                                                  "_cleanup_boundary")
                                    or called[1] in module_functions
                                    or called[1] in module_classes
                                    or called[1] in nested)
                                or called[0] == "attr"
                                and (called[1], called[2])
                                in step_primitives
                                and called[1] in imported_modules
                                or called[0] == "attr"
                                and called[1] in ("self", "cls")
                                and called[2] in module_methods
                                or called[0] == "selfattr"
                                and (called[1], called[2])
                                in external_self_calls), (
                            "a boundary-routed cleanup step calls "
                            "something that is neither in-module code "
                            "nor a disclosed primitive: FAILURE, never "
                            "a pass (fix 7/8)", key, ast.dump(call.func))
                    for access in annotation_reads(target.body):
                        raise AssertionError((
                            "a boundary-routed cleanup step READS "
                            "deferred annotations: the evaluation runs "
                            "code the call scan above cannot see "
                            "(fix 15, QA36 codex MAJOR)", key,
                            access.attr))
                if not isinstance(node, try_nodes):
                    continue
                if node.finalbody:
                    assert (len(node.finalbody) == 1
                            and isinstance(node.finalbody[0], ast.Expr)
                            and isinstance(node.finalbody[0].value,
                                           ast.Call)
                            and call_target(node.finalbody[0].value)[:2]
                            == ("name", "_cleanup_boundary")), (
                        "a close-lifecycle finally does not route "
                        "through the shared boundary (fix 6/7/8)", key)
                for handler in node.handlers:
                    required = catchable_cancellations(handler, key)
                    if not required:
                        continue
                    for inner in ast.walk(handler):
                        if (isinstance(inner, ast.Raise)
                                and inner.exc is not None):
                            assert isinstance(inner.exc, ast.Name), (
                                "a cancellation-capable close-lifecycle "
                                "handler CONSTRUCTS a new exception over "
                                "a possibly-pending cancellation "
                                "(fix 6/8)", key)
                    earlier = node.handlers[:node.handlers.index(handler)]
                    guarded_before = any(
                        bare_raise_only(h.body) and required
                        <= catchable_cancellations(h, key)
                        for h in earlier)
                    if (bare_raise_only(handler.body) or guarded_before
                            or guard_covers(handler.body[0], required,
                                            handler.name, key)):
                        continue
                    for access in annotation_reads(handler.body):
                        raise AssertionError((
                            "an annotation READ inside a "
                            "cancellation-capable handler evaluates "
                            "deferred annotations over a "
                            "possibly-pending exception (fix 15, QA36 "
                            "codex MAJOR)", key, access.attr))
                    captured = capture_shape(handler.body, handler.name)
                    if captured is not None:
                        # Fix 9 (QA30 codex BLOCKER 1): the capture is a
                        # PROMISE, now checked structurally -- everything
                        # that can run after the capturing try while the
                        # captured object may be pending routes through
                        # the boundary, and every path re-raises exactly
                        # that object.
                        deferred_capture_sound(
                            function,
                            successors_after(function, node, key),
                            captured, key)
                        continue
                    assert ends_in_raise(handler.body, handler.name), (
                        "a close-lifecycle handler can swallow or "
                        "replace a pending cancellation without the "
                        "boundary (fix 6/7)", key)
                    for call in direct_calls(handler.body):
                        called = call_target(call)
                        assert called[0] == "name" and called[1] in (
                            "isinstance", "_cleanup_boundary"), (
                            "an unprotected call inside a "
                            "cancellation-capable handler could displace "
                            "a pending cancellation (fix 7, QA28 codex "
                            "BLOCKER 1)", key, ast.dump(call.func))

        # Leg 11 NEGATIVE VECTORS (fix 10, QA31; fix 11, QA32): each
        # reproduces an in-memory QA mutation and must be REJECTED by
        # the structural machinery above -- by its named check, never
        # accepted and never a crash. Every QA31 vector was verified
        # ACCEPTED (red) by the fix-9 machinery at 7df50fda; every
        # QA32 vector was verified ACCEPTED (red) by the fix-10
        # machinery at 7182a86e.
        def leg11_vector(source):
            function = _optlevel.parse(textwrap.dedent(source)).body[0]
            capturing = next(node for node in function.body
                             if isinstance(node, try_nodes))
            return function, capturing

        def leg11_vector_rejected(label, check):
            try:
                check()
            except AssertionError:
                return
            raise AssertionError((
                "a QA31/QA32 mutation vector was ACCEPTED by leg 11 "
                "(fix 10/11)", label))

        # QA31 codex BLOCKER 1, mutation 1: the abandonment boundary's
        # pending argument swapped off the captured name -- while the
        # capture may be pending, a boundary call that passes ANY
        # other name must be rejected.
        function, capturing = leg11_vector("""
            def mutant(self):
                interrupted = None
                launched = False
                abandoned = False
                try:
                    launched = wait()
                except BaseException as exc:
                    interrupted = exc
                if not launched and _cleanup_boundary(
                        abandoned, abandon_unfinished,
                        "unfinished-launch abandonment"):
                    abandoned = True
                if interrupted is not None:
                    raise interrupted
            """)
        leg11_vector_rejected(
            "codex mutation 1: boundary pending argument swapped to "
            "another name",
            lambda function=function, capturing=capturing:
                deferred_capture_sound(
                    function,
                    successors_after(function, capturing,
                                     "vector:codex-1"),
                    "interrupted", "vector:codex-1"))

        # QA31 codex BLOCKER 1, mutation 2: an AnnAssign rebinds the
        # captured name after the capture -- every assignment form
        # that can touch the captured name is a FAILURE.
        function, capturing = leg11_vector("""
            def mutant(self):
                try:
                    wait()
                except BaseException as exc:
                    interrupted = exc
                interrupted: object = None
                if interrupted is not None:
                    raise interrupted
            """)
        leg11_vector_rejected(
            "codex mutation 2: AnnAssign rebinds the captured name",
            lambda function=function, capturing=capturing:
                deferred_capture_sound(
                    function,
                    successors_after(function, capturing,
                                     "vector:codex-2"),
                    "interrupted", "vector:codex-2"))

        # Class-width companion (fix 10): a walrus hidden inside the
        # boundary call's own arguments rebinds the captured name with
        # zero calls for the old scan to see.
        function, capturing = leg11_vector("""
            def mutant(self):
                try:
                    wait()
                except BaseException as exc:
                    interrupted = exc
                _cleanup_boundary((interrupted := None) or interrupted,
                                  finish_close, "owner collection")
                if interrupted is not None:
                    raise interrupted
            """)
        leg11_vector_rejected(
            "walrus companion: boundary argument rebinds the captured "
            "name",
            lambda function=function, capturing=capturing:
                deferred_capture_sound(
                    function,
                    successors_after(function, capturing,
                                     "vector:walrus"),
                    "interrupted", "vector:walrus"))

        # QA31 gemini BLOCKER b: a zero-call finally after the capture
        # rebinds the captured name and raises a bare exception class
        # over the in-flight re-raise.
        function, capturing = leg11_vector("""
            def mutant(self):
                try:
                    wait()
                except BaseException as exc:
                    captured = exc
                try:
                    raise captured
                finally:
                    captured = None
                    raise Exception
            """)
        leg11_vector_rejected(
            "gemini finally mutation: rebinding and foreign raise "
            "hidden in a finalbody",
            lambda function=function, capturing=capturing:
                deferred_capture_sound(
                    function,
                    successors_after(function, capturing,
                                     "vector:gemini-b"),
                    "captured", "vector:gemini-b"))

        # QA31 codex BLOCKER 2: a local rebinding shadows an imported
        # module name -- the receiver is NOT the module, and the
        # origin proof must FAIL it, never clear it as external.
        shadowing = _optlevel.parse(textwrap.dedent("""
            def mutant(self):
                os = _qa31_object
                os.poll()
            """)).body[0]
        vector_kinds, vector_value_kind = local_value_kinds(shadowing)
        shadowed_call = next(
            node for node in ast.walk(shadowing)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute))
        leg11_vector_rejected(
            "codex receiver mutation: shadowed import cleared as "
            "external",
            lambda call=shadowed_call, kinds=vector_kinds,
                   value_kind=vector_value_kind:
                resolve_call(call.func, "vector:codex-receiver",
                             set(), kinds, value_kind))
        assert vector_value_kind(shadowed_call) is None, (
            "value_kind cleared a call through a shadowed import as "
            "external (fix 10, QA31 codex BLOCKER 2)")

        # QA31 gemini BLOCKER c: an attribute assignment (and a
        # setattr) on an imported module poisons the NAME module-wide
        # -- module-owned cleanup monkey-patched onto os must never
        # clear as external.
        poisoned_tree = _optlevel.parse(textwrap.dedent("""
            import os

            class _Vector:
                def _close(self):
                    os.sneak_cleanup = self._module_owned_cleanup
                    try:
                        wait()
                    except TimeoutError:
                        os.sneak_cleanup()
                        raise
            """))
        assert "os" not in external_import_names(poisoned_tree), (
            "an attribute assignment on an imported module did not "
            "poison the imported name: module-owned cleanup can hide "
            "on the module object (fix 10, QA31 gemini BLOCKER c)")
        setattr_tree = _optlevel.parse(textwrap.dedent("""
            import os
            setattr(os, "sneak_cleanup", _fixture_signal)
            """))
        assert "os" not in external_import_names(setattr_tree), (
            "a setattr on an imported module did not poison the "
            "imported name (fix 10, QA31 gemini BLOCKER c)")
        assert "os" in imported_modules, (
            "the emit module itself poisons os: the disclosed os-level "
            "primitives would fail closed, not clear (fix 10)")

        # QA32 codex BLOCKER 1: the capturing try's OWN finally is a
        # successor -- a boundary call there with a swapped pending
        # argument must be rejected while the capture may be pending.
        function, capturing = leg11_vector("""
            def mutant(self):
                abandoned = False
                try:
                    wait()
                except BaseException as exc:
                    interrupted = exc
                finally:
                    _cleanup_boundary(abandoned, abandon_unfinished,
                                      "capture finally")
                if interrupted is not None:
                    raise interrupted
            """)
        leg11_vector_rejected(
            "codex QA32 finally vector: swapped pending argument in "
            "the capturing try's own finalbody",
            lambda function=function, capturing=capturing:
                deferred_capture_sound(
                    function,
                    successors_after(function, capturing,
                                     "vector:codex32-1"),
                    "interrupted", "vector:codex32-1"))

        # QA32 codex BLOCKER 2: a nested def's parameter DEFAULT
        # executes at the definition, right after the capture.
        function, capturing = leg11_vector("""
            def mutant(self):
                try:
                    wait()
                except BaseException as exc:
                    interrupted = exc
                def sneak(arg=abandon_unfinished()):
                    pass
                if interrupted is not None:
                    raise interrupted
            """)
        leg11_vector_rejected(
            "codex QA32 default vector: unprotected cleanup in a "
            "nested def's default after a capture",
            lambda function=function, capturing=capturing:
                deferred_capture_sound(
                    function,
                    successors_after(function, capturing,
                                     "vector:codex32-2"),
                    "interrupted", "vector:codex32-2"))

        # QA32 codex BLOCKER 3, form 1: the capture target collides
        # with the handler's own `as` name -- Python deletes that
        # binding at handler exit, so the later re-raise reads an
        # unbound name and the cancellation is lost.
        function, capturing = leg11_vector("""
            def mutant(self):
                interrupted = None
                try:
                    wait()
                except BaseException as interrupted:
                    interrupted = interrupted
                if interrupted is not None:
                    raise interrupted
            """)
        leg11_vector_rejected(
            "codex QA32 as-name vector: capture reuses the handler's "
            "bound name",
            lambda handler=capturing.handlers[0]:
                capture_shape(handler.body, handler.name))

        # QA32 codex BLOCKER 3, form 2: an overwrite between the
        # capture alias and the final raise -- ends_in_raise must
        # discard the overwritten alias, never keep trusting it.
        function, capturing = leg11_vector("""
            def mutant(self):
                try:
                    wait()
                except BaseException as exc:
                    interrupted = exc
                    interrupted: object = None
                    raise interrupted
            """)
        overwriting = capturing.handlers[0]
        assert not ends_in_raise(overwriting.body, overwriting.name), (
            "a handler that overwrites its capture alias before the "
            "final raise was accepted (fix 11, QA32 codex BLOCKER 3)")

        # QA32 claude BLOCKER 1: a pre-capture nested def -- usable as
        # a boundary step -- reaches the captured name back through
        # nonlocal and rebinds it with zero post-capture binding
        # leaves and zero calls for the old scans to see.
        function, capturing = leg11_vector("""
            def mutant(self):
                interrupted = None
                def abandon_step():
                    nonlocal interrupted
                    interrupted = None
                    return abandon_unfinished()
                try:
                    wait()
                except BaseException as exc:
                    interrupted = exc
                _cleanup_boundary(interrupted, abandon_step,
                                  "unfinished-launch abandonment")
                if interrupted is not None:
                    raise interrupted
            """)
        leg11_vector_rejected(
            "claude QA32 nonlocal vector: a nested def rebinds the "
            "captured name through nonlocal",
            lambda function=function, capturing=capturing:
                deferred_capture_sound(
                    function,
                    successors_after(function, capturing,
                                     "vector:claude32-1"),
                    "interrupted", "vector:claude32-1"))

        # QA32 claude BLOCKER 2: the pending argument at ANY boundary
        # call must be a recognized shape. The two real shapes stay
        # accepted; the inverted conditional and a conditional
        # guarding a DIFFERENT name are rejected.
        def vector_pending_arg(source):
            return _optlevel.parse(
                textwrap.dedent(source)).body[0].value.args[0]

        boundary_pending_shape(vector_pending_arg("""
            _cleanup_boundary(
                exc if isinstance(exc, _PENDING_CANCELLATIONS)
                else None, release_launcher, "parked launcher release")
            """), "vector:claude32-2-accept")
        boundary_pending_shape(vector_pending_arg("""
            _cleanup_boundary(
                tail_exc if isinstance(tail_exc,
                                       _PENDING_CANCELLATIONS)
                else pending, close_report, "report channel close")
            """), "vector:claude32-2-accept")
        leg11_vector_rejected(
            "claude QA32 inversion vector: inverted conditional "
            "pending argument",
            lambda arg=vector_pending_arg("""
                _cleanup_boundary(
                    None if isinstance(exc, _PENDING_CANCELLATIONS)
                    else exc, release_launcher,
                    "parked launcher release")
                """): boundary_pending_shape(arg, "vector:claude32-2a"))
        leg11_vector_rejected(
            "claude QA32 swapped-guard vector: the conditional guards "
            "a different name than it passes",
            lambda arg=vector_pending_arg("""
                _cleanup_boundary(
                    pending if isinstance(exc, _PENDING_CANCELLATIONS)
                    else None, release_launcher,
                    "parked launcher release")
                """): boundary_pending_shape(arg, "vector:claude32-2b"))

        # QA32 claude MINOR 2: a call-free suspension point after a
        # capture parks the function while the capture is pending -- a
        # dropped generator or coroutine would swallow the
        # cancellation without any call for the walk to see.
        function, capturing = leg11_vector("""
            def mutant(self):
                try:
                    wait()
                except BaseException as exc:
                    interrupted = exc
                yield
                if interrupted is not None:
                    raise interrupted
            """)
        leg11_vector_rejected(
            "claude QA32 yield vector: call-free suspension after a "
            "capture",
            lambda function=function, capturing=capturing:
                deferred_capture_sound(
                    function,
                    successors_after(function, capturing,
                                     "vector:claude32-3"),
                    "interrupted", "vector:claude32-3"))
        function, capturing = leg11_vector("""
            async def mutant(self):
                try:
                    wait()
                except BaseException as exc:
                    interrupted = exc
                await self._parked
                if interrupted is not None:
                    raise interrupted
            """)
        leg11_vector_rejected(
            "claude QA32 await vector: call-free suspension after a "
            "capture",
            lambda function=function, capturing=capturing:
                deferred_capture_sound(
                    function,
                    successors_after(function, capturing,
                                     "vector:claude32-4"),
                    "interrupted", "vector:claude32-4"))

        # QA32 codex MINOR (over-rejection pin): a bare annotation
        # with no value binds nothing at runtime -- it must be
        # ACCEPTED, with the guarded re-raise still discharging the
        # capture.
        function, capturing = leg11_vector("""
            def mutant(self):
                try:
                    wait()
                except BaseException as exc:
                    interrupted = exc
                interrupted: object
                if interrupted is not None:
                    raise interrupted
            """)
        deferred_capture_sound(
            function,
            successors_after(function, capturing,
                             "vector:codex32-minor"),
            "interrupted", "vector:codex32-minor")

        # QA33 codex MAJOR (fix 12): applying a BARE decorator right
        # after the capture calls it with the function -- with no
        # ast.Call node anywhere in the tree -- so the
        # definition-time work check must model the application
        # itself and reject it like any other unprotected call.
        function, capturing = leg11_vector("""
            def mutant(self):
                try:
                    wait()
                except BaseException as exc:
                    interrupted = exc
                @cleanup_decorator
                def extra():
                    pass
                if interrupted is not None:
                    raise interrupted
            """)
        leg11_vector_rejected(
            "codex QA33 decorator vector: a bare decorator applied "
            "after a capture",
            lambda function=function, capturing=capturing:
                deferred_capture_sound(
                    function,
                    successors_after(function, capturing,
                                     "vector:codex33-major"),
                    "interrupted", "vector:codex33-major"))

        # QA33 codex MINOR (fix 12, over-rejection pins): a LOCAL
        # annotation never evaluates its annotation expression, so a
        # call spelled inside a bare AnnAssign's annotation runs
        # nothing and must be ACCEPTED; a yield inside a NESTED def
        # suspends that function when it is called, never the
        # enclosing one, so defining such a generator after the
        # capture must be ACCEPTED too.
        function, capturing = leg11_vector("""
            def mutant(self):
                try:
                    wait()
                except BaseException as exc:
                    interrupted = exc
                interrupted: BaseException | type(None)
                if interrupted is not None:
                    raise interrupted
            """)
        deferred_capture_sound(
            function,
            successors_after(function, capturing,
                             "vector:codex33-minor-annotation"),
            "interrupted", "vector:codex33-minor-annotation")
        function, capturing = leg11_vector("""
            def mutant(self):
                try:
                    wait()
                except BaseException as exc:
                    interrupted = exc
                def unused_generator():
                    yield 1
                if interrupted is not None:
                    raise interrupted
            """)
        deferred_capture_sound(
            function,
            successors_after(function, capturing,
                             "vector:codex33-minor-yield"),
            "interrupted", "vector:codex33-minor-yield")

        # QA34 codex/claude MAJOR (fix 13, a fix-12 regression): a
        # nested def's or lambda's parameter DEFAULT is
        # definition-time work -- it evaluates in the ENCLOSING
        # function, and a yield spelled there generator-converts
        # THAT function, so its body (every boundary and re-raise
        # included) never runs when it is called. The nested-body
        # exemption must not cover it: the default, keyword-default
        # and lambda-default spellings are all rejected as
        # suspension points.
        for label, source in (
                ("default", """
            def mutant(self):
                try:
                    wait()
                except BaseException as exc:
                    interrupted = exc
                def unused(value=(yield 1)):
                    pass
                if interrupted is not None:
                    raise interrupted
            """),
                ("kwdefault", """
            def mutant(self):
                try:
                    wait()
                except BaseException as exc:
                    interrupted = exc
                def unused(*, value=(yield 1)):
                    pass
                if interrupted is not None:
                    raise interrupted
            """),
                ("lambda", """
            def mutant(self):
                try:
                    wait()
                except BaseException as exc:
                    interrupted = exc
                unused = lambda value=(yield 1): value
                if interrupted is not None:
                    raise interrupted
            """),
        ):
            function, capturing = leg11_vector(source)
            leg11_vector_rejected(
                "codex/claude QA34 " + label + " vector: a yield in "
                "a nested definition-time default suspends the "
                "enclosing function",
                lambda function=function, capturing=capturing,
                       label=label:
                    deferred_capture_sound(
                        function,
                        successors_after(
                            function, capturing,
                            "vector:codex34-major-" + label),
                        "interrupted",
                        "vector:codex34-major-" + label))

        # Companion rejection pin (fix 13): a walrus in that same
        # definition-time default binds in the ENCLOSING scope, so
        # the nested-body walrus exemption must not cover it either
        # (already rejected at f4200b43; this pins the seam between
        # the two fix-13 exemptions).
        function, capturing = leg11_vector("""
            def mutant(self):
                try:
                    wait()
                except BaseException as exc:
                    interrupted = exc
                def unused(value=(interrupted := None)):
                    pass
                if interrupted is not None:
                    raise interrupted
            """)
        leg11_vector_rejected(
            "fix 13 companion: a walrus in a nested definition-time "
            "default rebinds the captured name",
            lambda function=function, capturing=capturing:
                deferred_capture_sound(
                    function,
                    successors_after(
                        function, capturing,
                        "vector:codex34-walrus-default"),
                    "interrupted", "vector:codex34-walrus-default"))

        # QA34 codex MINOR (fix 13, over-rejection pin): a walrus in
        # a nested def's own BODY binds that function's local -- it
        # cannot rebind the enclosing captured name without a
        # nonlocal/global reach-back, which stays rejected
        # function-wide -- so defining it after the capture must be
        # ACCEPTED.
        function, capturing = leg11_vector("""
            def mutant(self):
                try:
                    wait()
                except BaseException as exc:
                    interrupted = exc
                def unused():
                    return (interrupted := None)
                if interrupted is not None:
                    raise interrupted
            """)
        deferred_capture_sound(
            function,
            successors_after(function, capturing,
                             "vector:codex34-minor-walrus-local"),
            "interrupted", "vector:codex34-minor-walrus-local")

        # QA34 codex MINOR (fix 13, over-rejection pin): a LOCAL
        # annotation expression never executes, so a walrus spelled
        # inside it -- reachable only through a nested lambda's body
        # on this Python -- binds nothing and must be ACCEPTED.
        function, capturing = leg11_vector("""
            def mutant(self):
                try:
                    wait()
                except BaseException as exc:
                    interrupted = exc
                interrupted: (lambda: (interrupted := None))
                if interrupted is not None:
                    raise interrupted
            """)
        deferred_capture_sound(
            function,
            successors_after(
                function, capturing,
                "vector:codex34-minor-walrus-annotation"),
            "interrupted", "vector:codex34-minor-walrus-annotation")

        # QA34 gemini a (fix 13, acceptance pin): a bare local
        # annotation nested inside the guard body after the capture
        # is dispatched by walk_block's own AnnAssign arm -- never a
        # whole-block call scan -- and stays ACCEPTED.
        function, capturing = leg11_vector("""
            def mutant(self):
                try:
                    wait()
                except BaseException as exc:
                    interrupted = exc
                if interrupted is not None:
                    interrupted: BaseException | type(None)
                    raise interrupted
            """)
        deferred_capture_sound(
            function,
            successors_after(function, capturing,
                             "vector:gemini34-annotation-in-if"),
            "interrupted", "vector:gemini34-annotation-in-if")

        # QA35 codex/claude MINOR (fix 14, acceptance pin): under
        # PEP 649 a nested def's parameter and return annotations
        # are deferred -- nothing spelled inside one runs at
        # definition time -- so an otherwise-harmless annotated def
        # after the capture must be ACCEPTED (the fix-13 scanner
        # modeled the annotations as immediate calls and rejected
        # it naming `type`).
        function, capturing = leg11_vector("""
            def mutant(self):
                try:
                    wait()
                except BaseException as exc:
                    interrupted = exc
                def unused(value: type(None)) -> type(None):
                    pass
                if interrupted is not None:
                    raise interrupted
            """)
        deferred_capture_sound(
            function,
            successors_after(function, capturing,
                             "vector:codex35-annotated-def"),
            "interrupted", "vector:codex35-annotated-def")

        # QA35 gemini (fix 14, rejection pin): the interpreter pin
        # is a named FAILURE, never a silent model mismatch -- a
        # pre-3.14 version tuple must be refused.
        leg11_vector_rejected(
            "gemini QA35 interpreter vector: leg 11 ran on a "
            "pre-PEP-649 interpreter without failing closed",
            lambda: leg11_interpreter_pinned((3, 13, 0)))

        # QA36 codex MAJOR (fix 15): the PEP 649 deferral has an
        # execution point -- READING the annotated def's
        # __annotations__ invokes its generated __annotate__ and
        # runs the module-owned cleanup spelled in the annotation,
        # with no ast.Call anywhere at the read site. The read after
        # the capture must be rejected while the UNREAD annotated
        # def (the fix-14 acceptance pin above) stays accepted.
        function, capturing = leg11_vector("""
            def mutant(self):
                try:
                    wait()
                except BaseException as exc:
                    interrupted = exc
                def unused(value: abandon_unfinished()):
                    pass
                unused.__annotations__
                if interrupted is not None:
                    raise interrupted
            """)
        leg11_vector_rejected(
            "codex QA36 annotation-read vector: reading a nested "
            "def's __annotations__ after a capture evaluates its "
            "deferred annotation",
            lambda function=function, capturing=capturing:
                deferred_capture_sound(
                    function,
                    successors_after(function, capturing,
                                     "vector:codex36-annotation-read"),
                    "interrupted", "vector:codex36-annotation-read"))

        # QA37 codex MINOR (fix 16, over-rejection pin): a LOCAL
        # AnnAssign's annotation expression never evaluates on the
        # pinned interpreter, so a deferred-read spelling inside it
        # runs nothing and must be ACCEPTED -- while the same
        # spelling in an AnnAssign's VALUE executes at the statement
        # and stays rejected.
        unevaluated = _optlevel.parse(textwrap.dedent("""
            def step():
                marker: step.__annotations__
            """)).body[0]
        assert not list(annotation_reads(unevaluated.body)), (
            "an unevaluated local annotation was scanned as a "
            "deferred-annotation READ (fix 16, QA37 codex MINOR)")
        evaluated = _optlevel.parse(textwrap.dedent("""
            def step():
                marker: object = step.__annotations__
            """)).body[0]
        assert [access.attr for access
                in annotation_reads(evaluated.body)] \
            == ["__annotations__"], (
            "an annotation READ in an AnnAssign VALUE executes at "
            "the statement and must stay rejected (fix 15/16)")

        # QA38 codex/claude MINOR (fix 17, over-rejection pin): the
        # CALL scan holds the same region -- direct_calls used to
        # walk the AnnAssign annotation annotation_reads skips, so a
        # call spelled in an unevaluated local annotation
        # (`marker: getattr(kill_leader, "__annotations__")`) was
        # over-rejected though it runs nothing. Both scanners now
        # skip the annotation, and both still see the VALUE, which
        # executes at the statement.
        unevaluated_call = _optlevel.parse(textwrap.dedent("""
            def step():
                marker: getattr(step, "__annotations__")
            """)).body[0]
        assert not list(direct_calls(unevaluated_call.body)), (
            "a call inside an unevaluated local annotation was "
            "scanned as an executing call (fix 17, QA38 "
            "codex/claude MINOR)")
        evaluated_call = _optlevel.parse(textwrap.dedent("""
            def step():
                marker: object = getattr(step, "__annotations__")
            """)).body[0]
        assert [call_target(call)[:2] for call
                in direct_calls(evaluated_call.body)] \
            == [("name", "getattr")], (
            "a call in an AnnAssign VALUE executes at the statement "
            "and must stay seen (fix 17)")

        # QA37 codex/claude MAJOR (fix 16): DISCLOSED-RESIDUAL pins.
        # Under PD-335-TAIL option 2 and the fix-16 scope decision,
        # leg 11 is a tripwire over an OPEN grammar and the two
        # QA37 classes are disclosed, not enforced (scanner
        # hardening for them is recorded follow-up work in the
        # maintainer backlog, not a fix-16 change). Each pin
        # therefore proves BOTH halves of the disclosure: the
        # mutation shape is ACCEPTED by this leg's machinery (the
        # tripwire does not see it), and executing that shape with
        # a cancellation pending and the module-owned cleanup
        # replaced by an injected fault reproduces exactly the
        # displacement leg 19's matrix catches (behavioural_case
        # refuses any run whose outward exception is not the
        # pending cancellation).
        pep695_read_source = """
            def mutant(self):
                try:
                    wait()
                except BaseException as exc:
                    interrupted = exc
                def unused[T: abandon_unfinished()]():
                    pass
                unused.__type_params__[0].__bound__
                if interrupted is not None:
                    raise interrupted
            """
        handler_argument_source = """
            def mutant(self):
                def unused(value: abandon_unfinished()):
                    pass
                interrupted = None
                try:
                    wait()
                except BaseException as exc:
                    _cleanup_boundary(
                        exc if isinstance(exc, _PENDING_CANCELLATIONS)
                        else None,
                        release_launcher, "parked launcher release",
                        **(getattr(unused, "__annotations__") and {}))
                    interrupted = exc
                if interrupted is not None:
                    raise interrupted
            """

        # ACCEPTED by leg 11 (claude QA37): the PEP 695 lazy read is
        # attribute-shaped but spells neither recognized name, and
        # the type parameter's bound is walked by no call scan, so
        # the successor walk accepts the whole shape.
        function, capturing = leg11_vector(pep695_read_source)
        assert not list(annotation_reads(function.body)), (
            "the PEP 695 lazy read is expected to evade "
            "annotation_reads -- it is DISCLOSED, not enforced "
            "(fix 16, QA37 claude MAJOR)")
        deferred_capture_sound(
            function,
            successors_after(function, capturing,
                             "vector:claude37-pep695-read"),
            "interrupted", "vector:claude37-pep695-read")

        # ACCEPTED by leg 11 (codex QA37): the handler is the
        # RECOGNIZED capture shape -- exactly that recognition
        # exempts its boundary-call arguments from the handler-body
        # call scan, so the indirect reader spelled there is never
        # seen.
        function, capturing = leg11_vector(handler_argument_source)
        handler = capturing.handlers[0]
        assert not list(annotation_reads(handler.body)), (
            "the indirect reader in the boundary-call arguments is "
            "expected to evade annotation_reads -- it is a call, "
            "not a recognized attribute read; DISCLOSED, not "
            "enforced (fix 16, QA37 codex MAJOR)")
        assert capture_shape(handler.body, handler.name) \
            == "interrupted", (
            "the mutated handler must still be the RECOGNIZED "
            "capture shape: that recognition is what exempts its "
            "boundary-call arguments from the handler-body call "
            "scan (fix 16, QA37 codex MAJOR)")
        deferred_capture_sound(
            function,
            successors_after(function, capturing,
                             "vector:codex37-handler-args"),
            "interrupted", "vector:codex37-handler-args")

        # CAUGHT by leg 19: run each accepted shape for real, the
        # way the behavioural matrix drives a site -- a cancellation
        # pending, the module-owned cleanup replaced by an injected
        # fault -- and require the fault to DISPLACE the
        # cancellation, which is precisely the outcome
        # behavioural_case (leg 19) refuses on every closure site.
        def displaced_outward(source):
            def wait():
                raise TimeoutError("pending cancellation")

            def abandon_unfinished():
                raise RuntimeError("injected cleanup fault")

            namespace = {
                "_cleanup_boundary": emit._cleanup_boundary,
                "_PENDING_CANCELLATIONS":
                    emit._PENDING_CANCELLATIONS,
                "wait": wait,
                "abandon_unfinished": abandon_unfinished,
                "release_launcher": lambda: None,
            }
            exec(compile(textwrap.dedent(source),
                         "<leg 11 QA37 vector>", "exec"), namespace)
            try:
                namespace["mutant"](None)
            except BaseException as exc:
                return exc
            raise AssertionError(
                "the disclosed QA37 mutation shape completed "
                "without raising (fix 16)")

        for label, source in (
                ("claude37-pep695-read", pep695_read_source),
                ("codex37-handler-args", handler_argument_source)):
            outward = displaced_outward(source)
            assert (type(outward) is RuntimeError
                    and str(outward) == "injected cleanup fault"), (
                "a disclosed QA37 residual shape did not reproduce "
                "the displacement leg 19 catches (fix 16, QA37 "
                "codex/claude MAJOR)", label, repr(outward))

        # Leg 12 (QA27 codex BLOCKER 1): a TimeoutError raised at the
        # subject SIGSTOP stays the outward exception when the held-pidfd
        # subject SIGKILL in the finally itself fails -- with an ordinary
        # RuntimeError or with a cancellation-typed InterruptedError --
        # and the kill failure is chained beneath it. The pre-fix finally
        # had no boundary: the later failure replaced the pending
        # cancellation (both variants reproduced at 74334f09).
        for kill_failure in (RuntimeError, InterruptedError):
            sent = []

            def sequence_stub(target_fd, signum, *args, _fail=kill_failure):
                assert target_fd == 1099, (target_fd, signum)
                sent.append(signum)
                if signum == signal.SIGSTOP:
                    raise TimeoutError("pending cancellation")
                assert signum == signal.SIGKILL, signum
                raise _fail("subject kill failure")

            with patch.object(signal, "pidfd_send_signal", sequence_stub):
                try:
                    emit._fixture_escalate_subject(11999, 1099)
                except TimeoutError as exc:
                    assert type(exc.__cause__) is kill_failure, (
                        "the subject kill failure was not chained beneath "
                        "the pending cancellation", exc.__cause__)
                except BaseException as exc:
                    raise AssertionError(
                        "the subject kill failure displaced the pending "
                        "cancellation (QA27 codex BLOCKER 1)",
                        kill_failure.__name__, repr(exc))
                else:
                    raise AssertionError(
                        "the escalation did not propagate")
            assert sent == [signal.SIGSTOP, signal.SIGKILL], sent

        # Leg 13 (QA27 codex BLOCKER 1, matrix gap): the same sequence at
        # the _escalate tier WITH a supplied subject receipt. The
        # cancellation propagates out of the subject cleanup with the
        # kill failure beneath it, records NO subject accounting (the
        # interrupt owner re-sends the idempotent receipt kill), and the
        # guardian cleanup still runs. The pre-fix tier let the
        # RuntimeError escape the subject helper and recorded "partial"
        # for a cleanup a cancellation had interrupted.
        helper_kills = []
        fake = types.SimpleNamespace(
            pid=11888, pidfd=1098, subject_pid=11999, subject_pidfd=1099,
            _subject_kill=None, _subject_skipped=None)

        def receipt_stub(target_fd, signum, *args):
            if target_fd == 1098:
                return None  # the guardian freeze succeeds
            assert target_fd == 1099, (target_fd, signum)
            if signum == signal.SIGSTOP:
                raise TimeoutError("pending cancellation")
            assert signum == signal.SIGKILL, signum
            raise RuntimeError("subject kill failure")

        def record_helper(pid, signum, pidfd=None, *, group=True):
            helper_kills.append((pid, signum))
            return True

        with patch.object(signal, "pidfd_send_signal", receipt_stub), (
                patch.object(emit, "_fixture_signal", record_helper)):
            try:
                emit._FixtureProcess._escalate(fake)
            except TimeoutError as exc:
                assert type(exc.__cause__) is RuntimeError, (
                    "the subject kill failure was not chained",
                    exc.__cause__)
            except BaseException as exc:
                raise AssertionError(
                    "the subject kill failure displaced the pending "
                    "cancellation at the _escalate tier (QA27 codex "
                    "BLOCKER 1)", repr(exc))
            else:
                raise AssertionError("the escalation did not propagate")
        assert (fake._subject_kill is None
                and fake._subject_skipped is None), (
            "a cancellation-interrupted subject cleanup was recorded",
            fake._subject_kill, fake._subject_skipped)
        assert helper_kills == [(11888, signal.SIGKILL)], helper_kills

        # Leg 14 (QA27 codex BLOCKER 2): with an EXPLICIT __cause__ on
        # the pending cancellation, the attachment appends the cleanup
        # failure at the tail of the pre-existing chain -- the exception
        # OBJECT, never a repr -- and no diagnostic runs outside the
        # boundary's protection: a cleanup failure whose __repr__ raises
        # cannot displace the cancellation. The pre-fix diagnostic path
        # ran repr(suppressed) in the open and the injected ValueError
        # became the outward exception despite a successful backstop.
        class _HostileRepr(RuntimeError):
            def __repr__(self):
                raise ValueError("hostile repr")

        hostile = _HostileRepr("helper failure")
        fake = types.SimpleNamespace(
            pid=11888, pidfd=1098, subject_pid=None, subject_pidfd=None,
            _subject_kill=None, _subject_skipped=None)

        def caused_freeze(target_fd, signum, *args):
            if signum == signal.SIGSTOP:
                raise TimeoutError("pending cancellation") from (
                    IndexError("explicit cause"))
            assert signum == signal.SIGKILL, signum
            return None  # the direct backstop succeeds

        with patch.object(signal, "pidfd_send_signal", caused_freeze), (
                patch.object(emit, "_fixture_signal",
                             side_effect=hostile)):
            try:
                emit._FixtureProcess._escalate(fake)
            except TimeoutError as exc:
                assert type(exc.__cause__) is IndexError, (
                    "the explicit cause was rewritten", exc.__cause__)
                assert exc.__cause__.__context__ is hostile, (
                    "the cleanup failure object was not kept reachable "
                    "beneath the explicit cause (QA27 codex)",
                    exc.__cause__.__context__)
            except BaseException as exc:
                raise AssertionError(
                    "a raising __repr__ displaced the pending "
                    "cancellation (QA27 codex BLOCKER 2)",
                    type(exc).__name__)
            else:
                raise AssertionError("the escalation did not propagate")

        # Leg 15 (QA27 codex BLOCKER 2, degraded path): when NO acyclic
        # attachment exists (the pending cancellation's own chain is
        # cyclic), the boundary degrades to a note -- and even there
        # every diagnostic runs inside the protection, so the raising
        # __repr__ only degrades the note text, never the outward
        # exception.
        hostile = _HostileRepr("helper failure")
        fake = types.SimpleNamespace(
            pid=11888, pidfd=1098, subject_pid=None, subject_pidfd=None,
            _subject_kill=None, _subject_skipped=None)

        def cyclic_freeze(target_fd, signum, *args):
            if signum == signal.SIGSTOP:
                explicit = IndexError("explicit cause")
                cancellation = TimeoutError("pending cancellation")
                explicit.__context__ = cancellation
                raise cancellation from explicit
            assert signum == signal.SIGKILL, signum
            return None

        with patch.object(signal, "pidfd_send_signal", cyclic_freeze), (
                patch.object(emit, "_fixture_signal",
                             side_effect=hostile)):
            try:
                emit._FixtureProcess._escalate(fake)
            except TimeoutError as exc:
                notes = getattr(exc, "__notes__", [])
                assert any("unrepresentable _HostileRepr" in note
                           for note in notes), (
                    "the degraded note fallback did not name the "
                    "suppressed failure", notes)
            except BaseException as exc:
                raise AssertionError(
                    "the degraded diagnostic path displaced the pending "
                    "cancellation (QA27 codex BLOCKER 2)",
                    type(exc).__name__)
            else:
                raise AssertionError("the escalation did not propagate")

        # Leg 16 (fix 7, QA28 codex BLOCKER 1): a cancellation born
        # INSIDE the guardian cleanup -- raised by the ownership-checked
        # kill helper itself, after a successful freeze -- becomes the
        # pending cancellation for the direct held-pidfd backstop, so a
        # backstop failure attaches BENEATH it and can never replace it.
        # All three pending-cancellation types are covered. The pre-fix
        # handler ran the backstop with no boundary of its own while the
        # outer boundary held pending=None, so the backstop's
        # RuntimeError displaced the cleanup-born cancellation
        # (reproduced at b4e42add for all three types).
        for born in (TimeoutError, InterruptedError, KeyboardInterrupt):
            fake = types.SimpleNamespace(
                pid=11888, pidfd=1098, subject_pid=None, subject_pidfd=None,
                _subject_kill=None, _subject_skipped=None)
            backstop_sent = []

            def borne_backstop(target_fd, signum, *args):
                assert target_fd == 1098, (target_fd, signum)
                if signum == signal.SIGSTOP:
                    return None  # the guardian freeze succeeds
                assert signum == signal.SIGKILL, signum
                backstop_sent.append(True)
                raise RuntimeError("backstop failure")

            def borne_helper(pid, signum, pidfd=None, *, group=True,
                             _born=born):
                raise _born("cleanup-born cancellation")

            with patch.object(signal, "pidfd_send_signal",
                              borne_backstop), (
                    patch.object(emit, "_fixture_signal", borne_helper)):
                try:
                    emit._FixtureProcess._escalate(fake)
                except BaseException as exc:
                    assert type(exc) is born, (
                        "a backstop failure displaced the cleanup-born "
                        "cancellation (fix 7, QA28 codex BLOCKER 1)",
                        born.__name__, repr(exc))
                    assert type(exc.__cause__) is RuntimeError, (
                        "the backstop failure was not kept beneath the "
                        "cleanup-born cancellation", born.__name__,
                        repr(exc.__cause__))
                else:
                    raise AssertionError(
                        "the escalation did not propagate")
            assert backstop_sent == [True], backstop_sent
            assert (fake._subject_kill is None
                    and fake._subject_skipped is None), (
                born.__name__, fake._subject_kill, fake._subject_skipped)

        # Leg 17 (fix 7, QA28 codex BLOCKER 2 / claude BLOCKER 1): the
        # member census's per-pidfd close is an escalation-path cleanup
        # too. A cancellation raised at the member SIGKILL send crosses
        # that close, so a failing os.close must attach beneath it, never
        # replace it. The pre-fix close was a bare finally outside both
        # the boundary and the structural scope: the close failure became
        # the outward exception (reproduced at b4e42add).
        member_fields = [b"S", b"11888", b"11999"]
        real_close = os.close

        def member_close(fd):
            if fd == 4242:
                raise InterruptedError("member close failure")
            return real_close(fd)

        def member_send(target_fd, signum, *args):
            assert target_fd == 4242 and signum == signal.SIGKILL, (
                target_fd, signum)
            raise TimeoutError("pending cancellation")

        with patch.object(os, "listdir", lambda path: ["7001"]), (
                patch.object(emit, "_fixture_stat_fields",
                             lambda target: list(member_fields))), (
                patch.object(emit, "_fixture_pidfd", lambda pid: 4242)), (
                patch.object(signal, "pidfd_send_signal", member_send)), (
                patch.object(os, "close", member_close)):
            try:
                emit._fixture_kill_group_members(
                    11999, signal.SIGKILL, {11888}, leader=11999)
            except TimeoutError as exc:
                assert type(exc.__cause__) is InterruptedError, (
                    "the member close failure was not kept beneath the "
                    "pending cancellation", repr(exc.__cause__))
            except BaseException as exc:
                raise AssertionError(
                    "the member pidfd close displaced the pending "
                    "cancellation (fix 7, QA28 codex BLOCKER 2)",
                    repr(exc))
            else:
                raise AssertionError("the member census did not propagate")

        # The same displacement at the _escalate tier ALSO mis-recorded
        # "partial" for a cleanup a cancellation had interrupted, gating
        # the interrupt owner's idempotent retry: now the cancellation
        # propagates with the close failure beneath it and NOTHING is
        # recorded, while the guardian cleanup still runs.
        helper_kills = []
        fake = types.SimpleNamespace(
            pid=11888, pidfd=1098, subject_pid=11999, subject_pidfd=1099,
            _subject_kill=None, _subject_skipped=None)

        def close_oserror(fd):
            if fd == 4242:
                raise OSError(5, "member close failure")
            return real_close(fd)

        def census_sequence(target_fd, signum, *args):
            if target_fd in (1098, 1099):
                return None  # freezes and held-pidfd kills succeed
            assert target_fd == 4242 and signum == signal.SIGKILL, (
                target_fd, signum)
            raise TimeoutError("pending cancellation")

        def census_helper(pid, signum, pidfd=None, *, group=True):
            helper_kills.append((pid, signum))
            return True

        with patch.object(os, "listdir", lambda path: ["7001"]), (
                patch.object(emit, "_fixture_stat_fields",
                             lambda target: list(member_fields))), (
                patch.object(emit, "_fixture_pidfd", lambda pid: 4242)), (
                patch.object(emit, "_fixture_group_pinned",
                             lambda group, guardian_pid: True)), (
                patch.object(signal, "pidfd_send_signal",
                             census_sequence)), (
                patch.object(os, "close", close_oserror)), (
                patch.object(emit, "_fixture_signal", census_helper)):
            try:
                emit._FixtureProcess._escalate(fake)
            except TimeoutError as exc:
                assert type(exc.__cause__) is OSError, (
                    "the member close failure was not kept beneath the "
                    "pending cancellation", repr(exc.__cause__))
            except BaseException as exc:
                raise AssertionError(
                    "the member pidfd close displaced the pending "
                    "cancellation at the _escalate tier (fix 7, QA28 "
                    "codex BLOCKER 2)", repr(exc))
            else:
                raise AssertionError("the escalation did not propagate")
        assert (fake._subject_kill is None
                and fake._subject_skipped is None), (
            "a cancellation-interrupted member cleanup was recorded",
            fake._subject_kill, fake._subject_skipped)
        assert helper_kills == [(11888, signal.SIGKILL)], helper_kills

        # Leg 18 (fix 7, QA28 claude MINOR 3): the cancellation tiers are
        # harmonized. A KeyboardInterrupt interrupting the subject cleanup
        # records NOTHING, exactly like its sibling cancellations, so the
        # interrupt owner's retry still owns the idempotent receipt kill.
        # The pre-fix recording tier keyed on (TimeoutError,
        # InterruptedError) alone: a KI-interrupted cleanup recorded
        # "partial" and the `_subject_kill is None` gate then blocked the
        # retry (reproduced at b4e42add).
        helper_kills = []
        fake = types.SimpleNamespace(
            pid=11888, pidfd=1098, subject_pid=11999, subject_pidfd=1099,
            _subject_kill=None, _subject_skipped=None)

        def ki_freeze(target_fd, signum, *args):
            if target_fd == 1098:
                return None  # the guardian freeze succeeds
            assert target_fd == 1099, (target_fd, signum)
            if signum == signal.SIGSTOP:
                raise KeyboardInterrupt("pending cancellation")
            assert signum == signal.SIGKILL, signum
            return None  # the held-pidfd subject SIGKILL succeeds

        def ki_helper(pid, signum, pidfd=None, *, group=True):
            helper_kills.append((pid, signum))
            return True

        with patch.object(signal, "pidfd_send_signal", ki_freeze), (
                patch.object(emit, "_fixture_signal", ki_helper)):
            try:
                emit._FixtureProcess._escalate(fake)
            except KeyboardInterrupt:
                pass
            except BaseException as exc:
                raise AssertionError(
                    "the KeyboardInterrupt was displaced or swallowed "
                    "(fix 7, QA28 claude MINOR 3)", repr(exc))
            else:
                raise AssertionError("the escalation did not propagate")
        assert (fake._subject_kill is None
                and fake._subject_skipped is None), (
            "a KeyboardInterrupt-interrupted subject cleanup was "
            "recorded (fix 7, QA28 claude MINOR 3)",
            fake._subject_kill, fake._subject_skipped)
        assert helper_kills == [(11888, signal.SIGKILL)], helper_kills

        # Leg 19 (fix 11, QA32, maintainer ruling PD-335-TAIL option
        # 2; fix 12, QA33 claude/codex BLOCKER): the BEHAVIOURAL
        # guarantee leg 11's tripwire defers to. For EVERY
        # _cleanup_boundary CALL SITE in the computed close-lifecycle
        # closure -- keyed (member, site-string, source-order
        # ordinal), so two calls sharing one label inside one member
        # hold SEPARATE cases -- drive the member with each
        # pending-cancellation type raised at that call's OWN pending
        # point AND an ordinary fault injected into that cleanup
        # step, and assert the ORIGINAL cancellation object
        # propagates outward with the cleanup fault reachable in its
        # chain. For _finish_close's two exceptional-path calls the
        # pending point is a cancellation BORN inside the
        # owned-handle cleanup itself (raised by close_subject_pidfd
        # / close_guardian_pidfd), the displacement class QA33 found
        # unpinned under the old (member, label) keying. The site
        # list is DERIVED from the closure leg 11 computed, so a
        # boundary call added to the lifecycle without a case here --
        # a NEW call reusing an existing label included -- fails the
        # coverage assert below. Red-on-revert: replacing a boundary
        # call with a direct cleanup call makes its case fail (the
        # injected fault displaces the cancellation) at every call
        # site where no enclosing boundary carries the same pending
        # value; at _finish_close's two normal-path calls the
        # enclosing boundary does, so a revert there leaves the
        # driven behaviour intact -- what rejects the direct call
        # there is this leg's OWN coverage assert, not leg 11's
        # tripwire (fix 13, QA34 claude MINOR: leg 11 covers the two
        # exceptional-path calls only, and dropping a normal-path
        # site removes or shifts its label's source-order ordinals,
        # stranding a case as stale). Reproduced for the
        # unfinished-launch abandonment site during fix 11 and for
        # both owned-handle cleanup sites during fix 12. fix 17
        # (QA38 codex BLOCKER) made each case run under fixture
        # states derived from its member's own code; fixes 18-30
        # (QA39-51, maintainer decisions 2026-09-29/30) keep that
        # derivation a BOUNDED, DISCLOSED guarantee whose claim
        # matches its code EXACTLY, and converge it by construction:
        # the bound below is stated verbatim at derived_overrides
        # too, and the two copies are asserted identical. Pinned red
        # after the matrix: the QA38 state-conditioned mutation; the
        # two QA39 vectors outside the bound (a condition read
        # through a local, a multi-attribute condition), each fired
        # by its one targeted state; the QA40 and QA41 spellings,
        # each driven only by states the derivation itself returns;
        # and the fix 30 entry vectors (a special attribute, an
        # unlisted and a stale driver overwrite, an unmangled
        # private name) with the QA51 private-name mutation, driven
        # by its mangled derived state; the fix 31 vectors (a driver
        # clearing its state, planted drifts of the two bound
        # copies); and the fix 32 vectors (a driver entering once
        # with the deviation, then through a cached member, on the
        # real member and on the QA38 mutant; a driver suspending
        # the observation; no free tool id; each state copy; a lost
        # indentation and a marker's trailing whitespace); and the
        # fix 33 vectors (an entry callback made to raise by its
        # fixture, a caller's preregistered previous callback, a
        # freed-then-retaken shared-name id); and the fix 34
        # vectors (a flag-only callback failure and the flag
        # store's bytecode, every event's preregistered
        # callback restored around a case, a failing
        # registration and a failing save, the bound's
        # live-case uniqueness wording); and the fix 35 vectors
        # (the entry callback's cell-free prelude, the save
        # loop's non-allocating slot store, an injected restore
        # failure abandoning later callbacks, a pre-existing
        # local-event mask surviving a case, a planted
        # interrupt right after the claim); and the fix 36
        # vectors (a real SIGINT before the save loop's first
        # slot store and one in the release, each repeated with
        # the mask removed, and the single allocator/audit/async
        # exclusion wording).
        # -- leg 19 bound (two identical copies, fix 30) --
        # derived_overrides IS the authoritative grammar. Every case
        # runs under every fixture state derived_overrides returns
        # for its member -- the default state plus single-attribute
        # deviations derived from the member's own committed AST, a
        # private name keyed as the compiler mangles it in a method
        # of the member's class; a site whose key is not an m:
        # method key derives none -- with every pending-cancellation
        # type; each driver gets its own copy of the state. Under a
        # deviation the member must be entered, and at each entry --
        # each start of the member's own code object while its
        # driver runs, however the member is reached, observed on a
        # free sys.monitoring tool id taken under a name unique to
        # the case among live cases (a completed case's name may
        # recur); every event's callback on that id is saved before
        # the case takes it, each held by a non-allocating store
        # into a preallocated slot as its removal returns it, and
        # the member's local-event mask on the id is saved before
        # the case arms its entry event; once its driver has
        # returned, an id still carrying that name has that mask
        # restored exactly as saved and is freed -- freeing clears
        # the id's callbacks and global events, not local events
        # on other code objects -- and every saved callback is
        # re-registered on it, exactly, every remaining slot even
        # if one registration raises, the first such error
        # re-raised; setup that fails re-registers only the
        # callbacks it had saved, never frees an id it did not
        # take, and releases an id it did take even when
        # interrupted after the claim; the save loop through
        # the claim and its publication, and the whole
        # release, mask SIGINT and every signal the
        # process currently has a Python-level handler for
        # on the calling thread, a best-effort narrowing
        # of the interruption window, never a guarantee:
        # a real signal can still be delivered before the
        # mask is in place, be delivered through another
        # thread and run its handler inside the section,
        # or interrupt the masking call itself and leave
        # the caller's prior mask widened -- deliveries
        # the exclusion below places outside this bound
        # (where pthread_sigmask is unavailable the
        # sections run unmasked); an id
        # freed and retaken under another name is left alone; a run
        # whose observation cannot be held or is not intact after
        # its driver returns, or with an exception raised inside its
        # entry callback, fails -- the callback captures no variable
        # into a cell, allocates nothing before its protected region,
        # and stores a preallocated failure flag first, without
        # allocating, and the flag alone
        # fails the run -- the entry frame's
        # self is the fixture, whose attribute under the deviation's
        # key, read once, must hold its value (type-aware) and the
        # key must be a name the member's compiled code, nested code
        # included, loads as an attribute; a deviation its driver
        # deliberately replaces is listed in behavioural_overwrites
        # and must instead be loaded with no entry holding its
        # value, and any other deviation failing the check fails the
        # suite. An interpreter allocation failure, an audit hook
        # raising, any other asynchronous exception (for
        # example one injected into the thread), and
        # asynchronous signal delivery -- a real signal
        # arriving at any point in setup, the case or
        # release, delivery through another thread
        # included -- are outside this bound; the
        # guarantee that does hold is no silent pass and
        # no unobserved entry: the suite fails loudly
        # rather than passing silently. Only the
        # default state must fire both the pending
        # point and the injected fault. Under a deviation, a run
        # whose pending point does not fire is checked at entry
        # only; one whose pending point fires must still raise that
        # cancellation outward, keeping the fault in its chain if
        # the fault fired. NO guarantee for multi-attribute
        # combinations, for any spelling outside the grammar, for
        # conditions carried through locals or other data flow, such
        # as a load of the name from an object other than the
        # fixture, or a member read after entry that yields a value
        # other than the entry read's, or for an entry the
        # interpreter does not report to that tool id as a PY_START
        # of the member's own code object: one made inside a trace,
        # profile or monitoring callback, one made while a driver
        # suspends the observation and restores it, one of a copy of
        # the member's code object, one made in another process, a
        # forked child included, whose interpreter reports it only
        # to that process's own copy of the observation, or a
        # generator or coroutine resumption, which the interpreter
        # reports as PY_RESUME or, resumed by throw, PY_THROW (not a
        # local event), never PY_START -- only the first entry of a
        # generator or coroutine is a start this check observes.
        # -- end of leg 19 bound --
        lifecycle_sites = set()
        for member_key in scope:
            member_calls = [
                node for node in ast.walk(scope[member_key])
                if (isinstance(node, ast.Call)
                    and call_target(node)[:2]
                    == ("name", "_cleanup_boundary"))]
            member_calls.sort(
                key=lambda node: (node.lineno, node.col_offset))
            label_ordinals = {}
            for node in member_calls:
                assert (len(node.args) >= 3
                        and isinstance(node.args[2], ast.Constant)
                        and isinstance(node.args[2].value, str)), (
                    "a boundary site label is not a string "
                    "literal: the behavioural matrix cannot name "
                    "it (fix 11)", member_key)
                label = node.args[2].value
                ordinal = label_ordinals.get(label, 0)
                label_ordinals[label] = ordinal + 1
                lifecycle_sites.add((member_key, label, ordinal))

        def chain_members(exc):
            seen, frontier, members = set(), [exc], []
            while frontier:
                node = frontier.pop()
                if node is None or id(node) in seen:
                    continue
                seen.add(id(node))
                members.append(node)
                frontier.extend((node.__cause__, node.__context__))
            return members

        flip_stand_in = types.SimpleNamespace()

        def derived_overrides(member_key, member=None):
            # fix 17 (QA38 codex BLOCKER): the driven fixture states
            # are DERIVED from the site's own code, never hand-listed;
            # the code below IS the grammar (fixes 19-20, 30). A pin
            # passes `member` to derive from a mutant or probe body;
            # the matrix itself always derives from the member's own
            # committed AST.
            # -- leg 19 bound (two identical copies, fix 30) --
            # derived_overrides IS the authoritative grammar. Every case
            # runs under every fixture state derived_overrides returns
            # for its member -- the default state plus single-attribute
            # deviations derived from the member's own committed AST, a
            # private name keyed as the compiler mangles it in a method
            # of the member's class; a site whose key is not an m:
            # method key derives none -- with every pending-cancellation
            # type; each driver gets its own copy of the state. Under a
            # deviation the member must be entered, and at each entry --
            # each start of the member's own code object while its
            # driver runs, however the member is reached, observed on a
            # free sys.monitoring tool id taken under a name unique to
            # the case among live cases (a completed case's name may
            # recur); every event's callback on that id is saved before
            # the case takes it, each held by a non-allocating store
            # into a preallocated slot as its removal returns it, and
            # the member's local-event mask on the id is saved before
            # the case arms its entry event; once its driver has
            # returned, an id still carrying that name has that mask
            # restored exactly as saved and is freed -- freeing clears
            # the id's callbacks and global events, not local events
            # on other code objects -- and every saved callback is
            # re-registered on it, exactly, every remaining slot even
            # if one registration raises, the first such error
            # re-raised; setup that fails re-registers only the
            # callbacks it had saved, never frees an id it did not
            # take, and releases an id it did take even when
            # interrupted after the claim; the save loop through
            # the claim and its publication, and the whole
            # release, mask SIGINT and every signal the
            # process currently has a Python-level handler for
            # on the calling thread, a best-effort narrowing
            # of the interruption window, never a guarantee:
            # a real signal can still be delivered before the
            # mask is in place, be delivered through another
            # thread and run its handler inside the section,
            # or interrupt the masking call itself and leave
            # the caller's prior mask widened -- deliveries
            # the exclusion below places outside this bound
            # (where pthread_sigmask is unavailable the
            # sections run unmasked); an id
            # freed and retaken under another name is left alone; a run
            # whose observation cannot be held or is not intact after
            # its driver returns, or with an exception raised inside its
            # entry callback, fails -- the callback captures no variable
            # into a cell, allocates nothing before its protected region,
            # and stores a preallocated failure flag first, without
            # allocating, and the flag alone
            # fails the run -- the entry frame's
            # self is the fixture, whose attribute under the deviation's
            # key, read once, must hold its value (type-aware) and the
            # key must be a name the member's compiled code, nested code
            # included, loads as an attribute; a deviation its driver
            # deliberately replaces is listed in behavioural_overwrites
            # and must instead be loaded with no entry holding its
            # value, and any other deviation failing the check fails the
            # suite. An interpreter allocation failure, an audit hook
            # raising, any other asynchronous exception (for
            # example one injected into the thread), and
            # asynchronous signal delivery -- a real signal
            # arriving at any point in setup, the case or
            # release, delivery through another thread
            # included -- are outside this bound; the
            # guarantee that does hold is no silent pass and
            # no unobserved entry: the suite fails loudly
            # rather than passing silently. Only the
            # default state must fire both the pending
            # point and the injected fault. Under a deviation, a run
            # whose pending point does not fire is checked at entry
            # only; one whose pending point fires must still raise that
            # cancellation outward, keeping the fault in its chain if
            # the fault fired. NO guarantee for multi-attribute
            # combinations, for any spelling outside the grammar, for
            # conditions carried through locals or other data flow, such
            # as a load of the name from an object other than the
            # fixture, or a member read after entry that yields a value
            # other than the entry read's, or for an entry the
            # interpreter does not report to that tool id as a PY_START
            # of the member's own code object: one made inside a trace,
            # profile or monitoring callback, one made while a driver
            # suspends the observation and restores it, one of a copy of
            # the member's code object, one made in another process, a
            # forked child included, whose interpreter reports it only
            # to that process's own copy of the observation, or a
            # generator or coroutine resumption, which the interpreter
            # reports as PY_RESUME or, resumed by throw, PY_THROW (not a
            # local event), never PY_START -- only the first entry of a
            # generator or coroutine is a start this check observes.
            # -- end of leg 19 bound --
            if not member_key.startswith("m:"):
                return [{}]
            if member is None:
                member = scope[member_key]
            owner = member_key[2:].rpartition(".")[0].lstrip("_")

            def compiled(attr):
                # the name the compiler emits for self.<attr> in a
                # method of the member's class: a private name is
                # mangled (fix 30, QA51 claude/codex MAJOR -- keyed
                # unmangled, `self.__x` derived a state on "__x"
                # while the member read "_FixtureProcess__x")
                if (owner and attr.startswith("__")
                        and not attr.endswith("__")):
                    return "_" + owner + attr
                return attr

            def spelled_constants(side):
                # the constants a comparison side spells (fix 19):
                # a bare ast.Constant, an ast.UnaryOp ast.UAdd or
                # ast.USub over a numeric non-bool ast.Constant
                # with the sign applied (fix 20), or an
                # ast.Tuple/ast.List/ast.Set of those (a
                # frozenset() call with exactly that one literal
                # argument and no keywords included)
                def spelled(item):
                    if isinstance(item, ast.Constant):
                        return [item.value]
                    if (isinstance(item, ast.UnaryOp)
                            and isinstance(item.op,
                                           (ast.UAdd, ast.USub))
                            and isinstance(item.operand,
                                           ast.Constant)
                            and isinstance(item.operand.value,
                                           (int, float, complex))
                            and not isinstance(item.operand.value,
                                               bool)):
                        if isinstance(item.op, ast.USub):
                            return [-item.operand.value]
                        return [+item.operand.value]
                    return []

                bare = spelled(side)
                if bare:
                    return bare
                container = side
                if (isinstance(container, ast.Call)
                        and isinstance(container.func, ast.Name)
                        and container.func.id == "frozenset"
                        and len(container.args) == 1
                        and not container.keywords):
                    container = container.args[0]
                if isinstance(container, (ast.Tuple, ast.List,
                                          ast.Set)):
                    members = [spelled(item)
                               for item in container.elts]
                    if all(members):
                        return [value for member in members
                                for value in member]
                return []

            truthy, consts = set(), {}
            for node in ast.walk(member):
                if not isinstance(node, (ast.If, ast.While,
                                         ast.IfExp)):
                    continue
                in_compare = set()
                for leaf in ast.walk(node.test):
                    if not isinstance(leaf, ast.Compare):
                        continue
                    sides = [leaf.left, *leaf.comparators]
                    side_ids = set(id(side) for side in sides)
                    for side in sides:
                        in_compare.update(
                            id(part) for part in ast.walk(side))
                    values = [value for side in sides
                              for value in spelled_constants(side)]
                    for side in sides:
                        for part in ast.walk(side):
                            if not (isinstance(part, ast.Attribute)
                                    and isinstance(part.value,
                                                   ast.Name)
                                    and part.value.id == "self"):
                                continue
                            if id(part) not in side_ids:
                                truthy.add(compiled(part.attr))
                            bucket = consts.setdefault(
                                compiled(part.attr), [])
                            for value in values:
                                # type-aware: 2 never collapses
                                # into 2.0, nor True into 1
                                # (fix 20)
                                if not any(value is known
                                           or (type(value)
                                               is type(known)
                                               and value == known)
                                           for known in bucket):
                                    bucket.append(value)
                for leaf in ast.walk(node.test):
                    if (isinstance(leaf, ast.Attribute)
                            and isinstance(leaf.value, ast.Name)
                            and leaf.value.id == "self"
                            and id(leaf) not in in_compare):
                        truthy.add(compiled(leaf.attr))
            overrides = [{}]
            for attr in sorted(set(truthy) | set(consts)):
                states = [False, True] if attr in truthy else []
                for value in consts.get(attr, ()):
                    # type-aware: a spelled 1 stays distinct from
                    # the truthiness True (fix 20)
                    if not any(value is known
                               or (type(value) is type(known)
                                   and value == known)
                               for known in states):
                        states.append(value)
                if any(value is None
                       for value in consts.get(attr, ())):
                    states.append(flip_stand_in)
                overrides.extend({attr: value} for value in states)
            return overrides

        def behavioural_case(label, driver, cancellation, fault,
                             state):
            # fix 17 (QA38 codex BLOCKER): each case runs under every
            # derived fixture state. A raise binds a traceback, so
            # whether the pending point and the injected step actually
            # FIRED under this state is observable on the objects
            # themselves. A FIRED cancellation must be the outward
            # exception -- a state-conditioned displacement is exactly
            # what this refuses -- and a FIRED fault must stay
            # reachable in its chain; a flipped state that routes
            # around the pending point makes that run vacuous, and the
            # DEFAULT state must never be vacuous: there both must
            # fire, which keeps the pre-fix-17 strictness.
            outward = None
            try:
                # the driver gets its own copy of the state (fix 31)
                driver(cancellation, fault, dict(state))
            except BaseException as exc:
                outward = exc
            if cancellation.__traceback__ is not None:
                assert outward is not None, (
                    "the pending cancellation was swallowed (fix 11)",
                    label, type(cancellation).__name__,
                    sorted(state))
                assert outward is cancellation, (
                    "the injected cleanup fault displaced the pending "
                    "cancellation (fix 11, behavioural guarantee)",
                    label, type(cancellation).__name__,
                    repr(outward), sorted(state))
                if fault.__traceback__ is not None:
                    assert any(node is fault
                               for node in chain_members(outward)), (
                        "the injected cleanup fault was dropped from "
                        "the cancellation's chain (fix 11)", label,
                        type(cancellation).__name__, sorted(state))
            assert state or (cancellation.__traceback__ is not None
                             and fault.__traceback__ is not None), (
                "the default-state case went vacuous: its pending "
                "point or its injected fault never fired (fix 17)",
                label)

        def stat_close_driver(cancellation, fault, state):
            # pending point: the /proc stat read; cleanup: the
            # descriptor close routed as the boundary step.
            def fake_open(path, flags):
                return 987001

            def fake_read(fd, size):
                raise cancellation

            def fake_close(fd):
                raise fault

            with patch.object(os, "open", fake_open), (
                    patch.object(os, "read", fake_read)), (
                    patch.object(os, "close", fake_close)):
                emit._fixture_stat_fields(4321)

        def member_close_driver(cancellation, fault, state):
            # pending point: the verified member send; cleanup: that
            # member's pidfd close.
            def fake_listdir(path):
                assert str(path) == "/proc", path
                return ["4242"]

            def fake_stat_fields(target):
                return [b"S", b"7777", b"6060"]

            def fake_pidfd(target):
                return 987002

            def fake_send(fd, signum, *args):
                assert fd == 987002, (fd, signum)
                raise cancellation

            def fake_close(fd):
                assert fd == 987002, fd
                raise fault

            with patch.object(os, "listdir", fake_listdir), (
                    patch.object(emit, "_fixture_stat_fields",
                                 fake_stat_fields)), (
                    patch.object(emit, "_fixture_pidfd",
                                 fake_pidfd)), (
                    patch.object(signal, "pidfd_send_signal",
                                 fake_send)), (
                    patch.object(os, "close", fake_close)):
                emit._fixture_kill_group_members(6060, signal.SIGKILL,
                                                 set([7777]))

        def subject_kill_driver(cancellation, fault, state):
            # pending point: the subject freeze; cleanup: the
            # held-pidfd SIGKILL (the leg 12 shape, all three types).
            def fake_send(fd, signum, *args):
                assert fd == 987003, (fd, signum)
                if signum == signal.SIGSTOP:
                    raise cancellation
                assert signum == signal.SIGKILL, signum
                raise fault

            with patch.object(signal, "pidfd_send_signal", fake_send):
                emit._fixture_escalate_subject(11999, 987003)

        def escalate_fake(state):
            fake = types.SimpleNamespace(
                pid=11888, pidfd=987004, subject_pid=None,
                subject_pidfd=None, _subject_kill=None,
                _subject_skipped=None)
            vars(fake).update(state)
            return fake

        def fake_escalate_subject(subject, subject_fd, *,
                                  guardian_pid=None):
            # a flipped subject_pidfd state walks into the subject
            # escalation before this site's own cleanup runs: keep
            # that path hermetic -- no real signals -- and
            # outcome-free (fix 17, QA38 codex BLOCKER)
            return None

        def backstop_driver(cancellation, fault, state):
            # pending point: the kill helper raises the cancellation,
            # which is pending for the backstop step; cleanup: the
            # direct backstop send.
            def fake_send(fd, signum, *args):
                if signum == signal.SIGSTOP:
                    return None  # the freeze succeeds
                assert signum == signal.SIGKILL, signum
                raise fault

            def fake_helper(pid, signum, pidfd=None, *, group=True):
                raise cancellation

            with patch.object(signal, "pidfd_send_signal",
                              fake_send), (
                    patch.object(emit, "_fixture_signal",
                                 fake_helper)), (
                    patch.object(emit, "_fixture_escalate_subject",
                                 fake_escalate_subject)):
                emit._FixtureProcess._escalate(escalate_fake(state))

        def guardian_kill_driver(cancellation, fault, state):
            # pending point: the guardian freeze; cleanup: the
            # guardian-kill step (helper fault, backstop delivers).
            def fake_send(fd, signum, *args):
                if signum == signal.SIGSTOP:
                    raise cancellation
                assert signum == signal.SIGKILL, signum
                return None  # the direct backstop succeeds

            def fake_helper(pid, signum, pidfd=None, *, group=True):
                raise fault

            with patch.object(signal, "pidfd_send_signal",
                              fake_send), (
                    patch.object(emit, "_fixture_signal",
                                 fake_helper)), (
                    patch.object(emit, "_fixture_escalate_subject",
                                 fake_escalate_subject)):
                emit._FixtureProcess._escalate(escalate_fake(state))

        def mask_restore_driver(cancellation, fault, state):
            # pending point: the masked close body; cleanup: the
            # sigmask restore.
            def fake_sigmask(how, mask):
                if how == signal.SIG_BLOCK:
                    return set()
                assert how == signal.SIG_SETMASK, how
                raise fault

            def raising_close_masked():
                raise cancellation

            fake = types.SimpleNamespace(
                _close_masked=raising_close_masked)
            vars(fake).update(state)
            with patch.object(signal, "pthread_sigmask",
                              fake_sigmask), (
                    patch.object(emit, "_fixture_mask_cancellation",
                                 lambda: None)):
                emit._FixtureProcess.close(fake)

        def masked_fake(cancellation, state, launcher=True):
            # _close_masked's pending point on every path: the
            # coordinated close raises the cancellation into the
            # backstop handler.
            def raising_coordinated():
                raise cancellation

            fake = types.SimpleNamespace(
                _launcher=object() if launcher else None,
                _abandoned=False,
                _go=types.SimpleNamespace(set=lambda: None),
                _close_coordinated=raising_coordinated,
                # a flipped state can route past the step under test
                # into the other owner steps: they must then behave,
                # never blow up on a missing attribute (fix 17)
                _abandon_unfinished_launch=lambda: False,
                _interrupt_collect=lambda: None)
            vars(fake).update(state)
            return fake

        def masked_release_driver(cancellation, fault, state):
            fake = masked_fake(cancellation, state)

            def raising_set():
                raise fault

            fake._go = types.SimpleNamespace(set=raising_set)
            emit._FixtureProcess._close_masked(fake)

        def masked_abandon_driver(cancellation, fault, state):
            fake = masked_fake(cancellation, state)

            def raising_abandon():
                raise fault

            fake._abandon_unfinished_launch = raising_abandon
            emit._FixtureProcess._close_masked(fake)

        def masked_interrupt_driver(cancellation, fault, state):
            fake = masked_fake(cancellation, state, launcher=False)

            def raising_interrupt():
                raise fault

            fake._interrupt_collect = raising_interrupt
            emit._FixtureProcess._close_masked(fake)

        def coordinated_fake(cancellation, abandon, state):
            # _close_coordinated's pending point: the launch-completion
            # wait raises the cancellation into the capturing handler.
            def raising_wait(timeout):
                raise cancellation

            fake = types.SimpleNamespace(
                _launcher=object(), _launch_lock=threading.Lock(),
                _abandoned=False, _cancelled=False,
                _go=types.SimpleNamespace(set=lambda: None),
                _launched=types.SimpleNamespace(wait=raising_wait),
                _abandon_unfinished_launch=abandon,
                # a state flipped off the launcher path falls through
                # to the plain collection finish (fix 17)
                _finish_close=lambda: None)
            vars(fake).update(state)
            return fake

        def coordinated_release_driver(cancellation, fault, state):
            released = []

            def go_set():
                released.append(True)
                if len(released) > 1:
                    raise fault  # the handler's parked-launcher re-set

            fake = coordinated_fake(cancellation, lambda: False,
                                    state)
            fake._go = types.SimpleNamespace(set=go_set)
            emit._FixtureProcess._close_coordinated(fake)

        def coordinated_abandon_driver(cancellation, fault, state):
            def raising_abandon():
                raise fault

            fake = coordinated_fake(cancellation, raising_abandon,
                                    state)
            emit._FixtureProcess._close_coordinated(fake)

        def coordinated_refusal_driver(cancellation, fault, state):
            def raising_refusal(message):
                raise fault

            fake = coordinated_fake(cancellation, lambda: True,
                                    state)
            with patch.object(emit, "ChildStatusUnavailable",
                              raising_refusal):
                emit._FixtureProcess._close_coordinated(fake)

        def coordinated_finish_driver(cancellation, fault, state):
            def raising_finish():
                raise fault

            fake = coordinated_fake(cancellation, lambda: False,
                                    state)
            fake._finish_close = raising_finish
            emit._FixtureProcess._close_coordinated(fake)

        def finish_fake(cancellation, state, report_close=None,
                        pidfd=None, subject_pidfd=None,
                        interrupt=None):
            # _finish_close's collection-side pending point: the
            # receipt read raises the cancellation as the
            # collection's first step; cancellation=None drives a
            # normally-completing collection, for the cases whose
            # cancellation is BORN in the owned-handle cleanup
            # (fix 12, QA33 claude/codex BLOCKER).
            def raising_recv():
                if cancellation is not None:
                    raise cancellation

            closing = types.SimpleNamespace(close=lambda: None)
            fake = types.SimpleNamespace(
                pid=None, collected=True, armed=False,
                unresolved=False, pidfd=pidfd,
                subject_pidfd=subject_pidfd, subject_pid=None,
                _subject_kill=None, _subject_skipped=None,
                _failure=None, cleaned=False,
                control=closing, peer=closing,
                _recv_subject=raising_recv,
                _interrupt_collect=interrupt or (lambda: None),
                # a flipped state (unresolved=True) walks the
                # failure-recording path a default-state run never
                # takes: record-and-return, subject addressed as a
                # no-op (fix 17)
                _record_failure=lambda failure: failure,
                _address_failed_subject=lambda: None,
                report=types.SimpleNamespace(
                    close=report_close or (lambda: None)))
            vars(fake).update(state)
            return fake

        def finish_report_driver(cancellation, fault, state):
            # the normal-path "report channel close" call: the
            # cancellation comes from the collection, the fault from
            # the report close.
            def raising_report_close():
                raise fault

            emit._FixtureProcess._finish_close(
                finish_fake(cancellation, state,
                            report_close=raising_report_close))

        def finish_tail_pending_driver(cancellation, fault, state):
            # the exceptional-path "report channel close" call: the
            # cancellation is BORN in the subject pidfd close (the
            # owned-handle cleanup itself), the fault in the
            # boundary's step, the report close (fix 12, QA33
            # claude/codex BLOCKER).
            def fake_close(fd):
                if fd == 987007:
                    raise cancellation
                return None  # a flip-introduced fd closes cleanly

            def raising_report_close():
                raise fault

            fake = finish_fake(None, state, subject_pidfd=987007,
                               report_close=raising_report_close)
            with patch.object(os, "close", fake_close):
                emit._FixtureProcess._finish_close(fake)

        def finish_subject_driver(cancellation, fault, state):
            # the normal-path "subject pidfd and report close" call:
            # the cancellation comes from the collection, the fault
            # from the subject pidfd close.
            def fake_close(fd):
                if fd == 987007:
                    raise fault
                return None  # a flip-introduced fd closes cleanly

            fake = finish_fake(cancellation, state,
                               subject_pidfd=987007)
            with patch.object(os, "close", fake_close):
                emit._FixtureProcess._finish_close(fake)

        def finish_head_pending_driver(cancellation, fault, state):
            # the exceptional-path "subject pidfd and report close"
            # call: the cancellation is BORN in the guardian pidfd
            # close, the fault in the boundary's step -- the subject
            # pidfd close inside the tail (fix 12, QA33 claude/codex
            # BLOCKER).
            def fake_close(fd):
                if fd == 987006:
                    raise cancellation
                if fd == 987007:
                    raise fault
                return None  # a flip-introduced fd closes cleanly

            fake = finish_fake(None, state, pidfd=987006,
                               subject_pidfd=987007)
            with patch.object(os, "close", fake_close):
                emit._FixtureProcess._finish_close(fake)

        def finish_interrupt_driver(cancellation, fault, state):
            def raising_interrupt():
                raise fault

            emit._FixtureProcess._finish_close(
                finish_fake(cancellation, state,
                            interrupt=raising_interrupt))

        def finish_handles_driver(cancellation, fault, state):
            def fake_close(fd):
                if fd == 987006:
                    raise fault
                return None  # a flip-introduced fd closes cleanly

            fake = finish_fake(cancellation, state, pidfd=987006)
            with patch.object(os, "close", fake_close):
                emit._FixtureProcess._finish_close(fake)

        behavioural_drivers = dict()
        behavioural_drivers[
            ("f:_fixture_stat_fields",
             "stat descriptor close", 0)] = stat_close_driver
        behavioural_drivers[
            ("f:_fixture_kill_group_members",
             "member pidfd close", 0)] = member_close_driver
        behavioural_drivers[
            ("f:_fixture_escalate_subject",
             "held-pidfd subject SIGKILL", 0)] = subject_kill_driver
        behavioural_drivers[
            ("m:_FixtureProcess._escalate",
             "direct guardian SIGKILL backstop", 0)] = backstop_driver
        behavioural_drivers[
            ("m:_FixtureProcess._escalate",
             "guardian-kill cleanup", 0)] = guardian_kill_driver
        behavioural_drivers[
            ("m:_FixtureProcess.close",
             "cancellation mask restore", 0)] = mask_restore_driver
        behavioural_drivers[
            ("m:_FixtureProcess._close_masked",
             "parked launcher release", 0)] = masked_release_driver
        behavioural_drivers[
            ("m:_FixtureProcess._close_masked",
             "unfinished-launch abandonment",
             0)] = masked_abandon_driver
        behavioural_drivers[
            ("m:_FixtureProcess._close_masked",
             "interrupt-owner collection",
             0)] = masked_interrupt_driver
        behavioural_drivers[
            ("m:_FixtureProcess._close_coordinated",
             "parked launcher release",
             0)] = coordinated_release_driver
        behavioural_drivers[
            ("m:_FixtureProcess._close_coordinated",
             "unfinished-launch abandonment",
             0)] = coordinated_abandon_driver
        behavioural_drivers[
            ("m:_FixtureProcess._close_coordinated",
             "abandonment refusal", 0)] = coordinated_refusal_driver
        behavioural_drivers[
            ("m:_FixtureProcess._close_coordinated",
             "owner collection finish",
             0)] = coordinated_finish_driver
        behavioural_drivers[
            ("m:_FixtureProcess._finish_close",
             "report channel close", 0)] = finish_tail_pending_driver
        behavioural_drivers[
            ("m:_FixtureProcess._finish_close",
             "report channel close", 1)] = finish_report_driver
        behavioural_drivers[
            ("m:_FixtureProcess._finish_close",
             "subject pidfd and report close",
             0)] = finish_head_pending_driver
        behavioural_drivers[
            ("m:_FixtureProcess._finish_close",
             "subject pidfd and report close",
             1)] = finish_subject_driver
        behavioural_drivers[
            ("m:_FixtureProcess._finish_close",
             "interrupt-owner collection",
             0)] = finish_interrupt_driver
        behavioural_drivers[
            ("m:_FixtureProcess._finish_close",
             "held descriptor and report close",
             0)] = finish_handles_driver
        assert lifecycle_sites == set(behavioural_drivers), (
            "the behavioural matrix does not cover the computed "
            "boundary call-site list exactly: every module-owned "
            "cleanup call site needs a fault-injection case -- a "
            "NEW call reusing an existing label included -- and "
            "every case must name a real call site (fix 11/12, "
            "PD-335-TAIL option 2)",
            sorted(lifecycle_sites
                   ^ set(behavioural_drivers)))
        # fix 30 (QA51 claude/codex MAJOR; orchestrator premise review
        # 2026-09-30): the matrix CHECKS each deviation at entry instead
        # of disclosing where a deviation is lost. The deliberate
        # driver overwrites, keyed (case, attribute): each driver
        # replaces its attribute after the state is installed, so a
        # deviation there is never the value the member reads; every
        # entry is checked below to still be replaced.
        behavioural_overwrites = frozenset([
            (("m:_FixtureProcess._close_masked",
              "parked launcher release", 0), "_go"),
            (("m:_FixtureProcess._close_masked",
              "unfinished-launch abandonment", 0),
             "_abandon_unfinished_launch"),
            (("m:_FixtureProcess._close_masked",
              "interrupt-owner collection", 0), "_interrupt_collect"),
            (("m:_FixtureProcess._close_coordinated",
              "parked launcher release", 0), "_go"),
            (("m:_FixtureProcess._close_coordinated",
              "owner collection finish", 0), "_finish_close"),
        ])
        ineffective_deviation = (
            "a generated deviation fails the entry check -- its name "
            "is not loaded, or the fixture does not hold its value "
            "at entry: derive the name its compiled code loads, or "
            "list a deliberate driver overwrite (fix 30)")
        stale_overwrite = (
            "a listed driver overwrite no longer replaces a value the "
            "member reads at entry (fix 30)")
        import dis
        import signal
        unobserved_entries = (
            "the member's entries were not all observed: no free "
            "sys.monitoring tool id, the observation was not intact "
            "when the driver returned, or the entry callback itself "
            "failed (fix 32/33/34)")
        monitoring = sys.monitoring
        entry_event = monitoring.events.PY_START
        entry_tool_name = "opf leg 19 entry check"
        # the two tool ids CPython assigns no role first, then the rest;
        # an id already in use -- a caller's debugger, coverage,
        # profiler or optimizer included -- is never taken
        entry_tool_ids = (3, 4, 0, 1, 2, 5)
        # fix 34 (QA55 codex MAJOR): every single-bit event a
        # callback can be registered for, saved and restored
        # around a case; NO_EVENTS carries none, and the
        # deprecated BRANCH alias writes through to BRANCH_LEFT
        # and BRANCH_RIGHT, so saving it too would corrupt their
        # restoration
        callback_events = tuple(sorted(
            value for name, value in vars(monitoring.events).items()
            if name != "BRANCH" and isinstance(value, int)
            and value > 0 and not value & (value - 1)))

        def mask_handled_signals():
            # fix 36 (QA57 codex MAJOR x2), rescoped by fix 37
            # (QA58): the save loop through the claim, and the
            # whole release, run with SIGINT and every signal
            # this process currently has a Python-level
            # handler for blocked on the calling thread. A
            # best-effort narrowing of the interruption
            # window, never a guarantee: a real signal can be
            # delivered before this helper blocks anything, a
            # delivery inside this helper can leave the
            # caller's prior mask widened, and a thread with
            # the signal unblocked can receive it while Python
            # still runs the handler on the main thread,
            # inside the section. The bound discloses every
            # such delivery as outside it; the caller puts the
            # returned prior mask back in a finally, best
            # effort on the same terms. Where pthread_sigmask
            # is unavailable the section runs unmasked, as the
            # bound also discloses.
            if not hasattr(signal, "pthread_sigmask"):
                return None
            handled = {signal.SIGINT}
            for number in signal.valid_signals():
                try:
                    handler = signal.getsignal(number)
                except (OSError, ValueError):
                    continue
                if callable(handler):
                    handled.add(number)
            return signal.pthread_sigmask(signal.SIG_BLOCK,
                                          handled)

        def unmask_handled_signals(previous):
            if previous is not None:
                signal.pthread_sigmask(signal.SIG_SETMASK,
                                       previous)

        def entry_checked_case(label, driver, cancellation, fault,
                               state, overwrites):
            # the entry check, resolved through the member's OWN
            # compiled code: a deviation's key must be an attribute
            # name that code (nested code objects included) loads --
            # so a private name counts only as the compiler mangled
            # it -- and the fixture must yield the deviation's value
            # under that name each time the member is entered
            # (read once per entry: which object a load reads, and
            # what a later read yields, lie outside it). fix 31 (QA52
            # codex BLOCKER): the check keeps its own immutable copy of
            # the state; behavioural_case gives the driver a separate copy.
            # fix 32 (QA53 codex/claude BLOCKER): an entry is each
            # PY_START of the member's own code object while the driver
            # runs, observed on a free sys.monitoring tool id, and the
            # fixture is the entry frame's own self -- the pre-fix
            # wrapper patched on the class saw only the calls made
            # through it, so a driver entering once with the deviation
            # and then through a member cached before the patch went
            # unchecked
            state = types.MappingProxyType(dict(state))
            if not state:
                behavioural_case(label, driver, cancellation, fault,
                                 state)
                return
            assert label[0].startswith("m:"), (
                "a deviation was generated for a site without an m: "
                "method key (fix 30)", label, sorted(state))
            owner_name, member_name = label[0][2:].rsplit(".", 1)
            owner = getattr(emit, owner_name)
            member = vars(owner)[member_name]
            member_code = member.__code__
            loads, codes = set(), [member_code]
            while codes:
                code = codes.pop()
                codes.extend(const for const in code.co_consts
                             if isinstance(const, types.CodeType))
                loads.update(
                    instruction.argval
                    for instruction in dis.get_instructions(code)
                    if instruction.opname in ("LOAD_ATTR",
                                              "LOAD_METHOD"))
            absent = object()
            entries = []
            observation_failures = []
            # fix 34 (QA55 codex BLOCKER): the callback's failure
            # flag -- a one-slot list preallocated here, written
            # by an item store of a singleton, which allocates
            # nothing
            observation_incomplete = [None]
            # fix 33 (QA54 codex MAJOR, claude MINOR 2): the case
            # OWNS its id under a name unique to this case for as
            # long as the case is live -- entries lives until the
            # case completes, so no two live cases share
            # id(entries); a completed case's name may recur
            # (fix 34, QA55 codex MINOR) -- so a freed-then-
            # retaken id -- the shared prefix included -- or a
            # nested case's id is never mistaken for its own
            case_tool_name = "%s %d" % (entry_tool_name, id(entries))

            def entered(started, offset):
                # called in the entry frame: sys._getframe(1) is the
                # member's own frame, its arguments already bound.
                # fix 33 (QA54 codex BLOCKER): a failure HERE -- an
                # attribute read raising, anything at all -- must
                # never pass silently; it is recorded, and the check
                # below fails the run on the record. fix 35 (QA56
                # codex BLOCKER): the callback captures NOTHING and
                # allocates nothing before the try -- the pre-fix
                # generator expression captured `fixture`, whose
                # cell was allocated before the protected region,
                # where an allocation failure skipped the flag --
                # so each attribute is read inline
                try:
                    frame = sys._getframe(1)
                    fixture = absent
                    if frame.f_code is started:
                        fixture = frame.f_locals.get("self", absent)
                    entry = {}
                    for attr in state:
                        entry[attr] = getattr(fixture, attr, absent)
                    entries.append(entry)
                except BaseException as exc:
                    # fix 34 (QA55 codex BLOCKER): the flag
                    # FIRST, by a store that allocates nothing,
                    # so a failure recording the detail below
                    # cannot lose the record
                    observation_incomplete[0] = True
                    observation_failures.append(exc)

            def restore_saved(target, slots):
                # fix 35 (QA56 codex MAJOR): every saved slot is
                # put back even if one registration raises; the
                # first error is re-raised once every slot has
                # been tried, so a failing restore cannot
                # abandon the callbacks saved after it
                error = None
                for at, event in enumerate(callback_events):
                    callback = slots[at]
                    if callback is absent:
                        continue
                    try:
                        monitoring.register_callback(target, event,
                                                     callback)
                    except BaseException as exc:
                        if error is None:
                            error = exc
                if error is not None:
                    raise error

            tool, saved_callbacks = None, ()
            outcome, intact, registered = None, False, False
            saved_local_events = None
            try:
                for candidate in entry_tool_ids:
                    if monitoring.get_tool(candidate) is not None:
                        continue
                    # fix 35 (QA56 codex MAJOR): the save slots
                    # are preallocated, and each event's previous
                    # callback is held by an item store -- which
                    # allocates nothing -- the instant its
                    # removal returns it, so an allocation
                    # failure cannot lose a removed callback;
                    # `absent` marks a slot the save never
                    # reached
                    saved = [absent] * len(callback_events)
                    taken = False
                    # fix 36 (QA57 codex MAJOR): a real SIGINT
                    # between a removal returning and its slot
                    # store loses that callback -- the slot
                    # still holds `absent`, which restore_saved
                    # skips -- so the save loop, the
                    # publication, the claim and the
                    # failure-path restore all run with handled
                    # signals blocked on this thread; best
                    # effort only (fix 37, QA58): a delivery
                    # before the mask is in place, inside the
                    # masking call, or through another thread
                    # still interrupts, as the bound discloses;
                    # the prior mask goes back in the outermost
                    # finally, when the release below is armed
                    unmasked = mask_handled_signals()
                    try:
                        try:
                            # fix 34 (QA55 codex MAJOR): EVERY
                            # event's callback on the id --
                            # registration needs no claimed id -- is
                            # saved before the case takes it, to be
                            # restored exactly on release;
                            # free_tool_id clears them all
                            for at, event in enumerate(
                                    callback_events):
                                saved[at] = monitoring.register_callback(
                                    candidate, event, None)
                            try:
                                # fix 35 (QA56 gemini): the id and
                                # its saved callbacks are published
                                # BEFORE the claim and the taken
                                # flag, so an interrupt landing
                                # after the claim finds the release
                                # below armed and never leaves a
                                # taken id unreleased
                                tool, saved_callbacks = candidate, saved
                                monitoring.use_tool_id(candidate,
                                                       case_tool_name)
                                taken = True
                            except ValueError:
                                tool, saved_callbacks = None, ()
                        finally:
                            if not taken:
                                # fix 34 (QA55 codex MAJOR): setup
                                # that fails puts back only the
                                # callbacks it saved and never frees
                                # an id it did not take
                                restore_saved(candidate, saved)
                    finally:
                        unmask_handled_signals(unmasked)
                    if taken:
                        break
                if tool is None:
                    raise AssertionError(unobserved_entries, label,
                                         "no free tool id")
                monitoring.register_callback(tool, entry_event,
                                             entered)
                registered = True
                # fix 35 (QA56 codex MAJOR): the member's
                # pre-existing local-event mask on this id -- an
                # unclaimed id keeps its per-code masks -- is
                # saved before setup overwrites it, to be
                # restored exactly at release
                saved_local_events = monitoring.get_local_events(
                    tool, member_code)
                monitoring.set_local_events(tool, member_code,
                                            entry_event)
                try:
                    behavioural_case(label, driver, cancellation,
                                     fault, state)
                except AssertionError as exc:
                    outcome = exc
                intact = (monitoring.get_tool(tool) == case_tool_name
                          and monitoring.get_local_events(
                              tool, member_code) == entry_event)
            finally:
                # fix 36 (QA57 codex MAJOR): the WHOLE release
                # runs with handled signals blocked -- a real
                # SIGINT after the member's mask went back and
                # before free_tool_id and restore_saved leaves
                # the id claimed and no callback restored -- the
                # prior mask put back in a finally; best effort
                # only (fix 37, QA58): the masking call itself
                # starts unmasked, and a delivery before it
                # completes or through another thread still
                # interrupts the release, as the bound
                # discloses
                unmasked = mask_handled_signals()
                try:
                    # release exactly the id this case took; an id a
                    # driver freed, and someone else then took -- under
                    # the shared name included -- is left alone (fix 33)
                    if (tool is not None
                            and monitoring.get_tool(tool)
                            == case_tool_name):
                        # fix 35 (QA56 codex MAJOR, claude MINOR):
                        # the member's local-event mask goes back
                        # exactly as saved -- a mask never saved was
                        # never overwritten -- and freeing clears no
                        # local events on other code objects, so
                        # none need saving
                        if saved_local_events is not None:
                            monitoring.set_local_events(
                                tool, member_code, saved_local_events)
                        released = (monitoring.register_callback(
                            tool, entry_event, None) if registered
                            else None)
                        monitoring.free_tool_id(tool)
                        # fix 33/34 (QA54/QA55 codex MAJOR):
                        # free_tool_id clears the id's callbacks, so
                        # every callback saved before the case took
                        # the id is put back afterwards, restoring
                        # the id exactly as this case found it; a
                        # registration that failed is not treated as
                        # a release
                        restore_saved(tool, saved_callbacks)
                        intact = intact and released is entered
                finally:
                    unmask_handled_signals(unmasked)
            # fix 34 (QA55 codex BLOCKER): the flag, checked
            # before any other outcome; the detailed record may
            # be absent if recording it failed
            if observation_incomplete[0] is not None:
                raise AssertionError(unobserved_entries, label,
                                     "the entry callback failed",
                                     observation_failures)
            if not intact:
                raise AssertionError(unobserved_entries, label,
                                     sorted(state))
            for attr in sorted(state):
                value = state[attr]
                read = attr in loads and bool(entries)
                matches = [seen is value
                           or (type(seen) is type(value)
                               and seen == value)
                           for seen in (entry[attr]
                                        for entry in entries)]
                if (label, attr) in overwrites:
                    if not read or any(matches):
                        raise AssertionError(stale_overwrite, label,
                                             attr)
                elif not (read and all(matches)):
                    raise AssertionError(ineffective_deviation, label,
                                         attr, repr(value))
            if outcome is not None:
                raise outcome

        def drive_matrix(case_labels, overrides, faults,
                         overwrites=behavioural_overwrites):
            for case_label in case_labels:
                for override in overrides[case_label[0]]:
                    for cancellation_type in pending_cancellations:
                        for fault in faults():
                            entry_checked_case(
                                case_label,
                                behavioural_drivers[case_label],
                                cancellation_type(
                                    "pending cancellation"),
                                fault, dict(override), overwrites)

        site_overrides = dict(
            (member_key, derived_overrides(member_key))
            for member_key in set(
                site[0] for site in lifecycle_sites))
        assert any("armed" in override for override in site_overrides[
                "m:_FixtureProcess._finish_close"]), (
            "the derived _finish_close state list lost the armed "
            "flip that catches the pinned QA38 state-conditioned "
            "displacement (fix 17)")
        drive_matrix(sorted(behavioural_drivers), site_overrides,
                     lambda: [RuntimeError("injected cleanup fault")])
        # fix 14 (QA35 codex MAJOR): a RuntimeError fault crosses the
        # two SIGKILL steps' own `except (ProcessLookupError, OSError)`
        # / OSError filters untouched, so the matrix above never saw
        # the module-owned handling of a realistic syscall failure --
        # the held-pidfd subject SIGKILL recorded an EPERM-class fault
        # in its survivor flag only, and the direct guardian backstop
        # discarded it outright; both dropped it from the pending
        # cancellation's chain. Drive those two sites with
        # OSError-family faults too -- PermissionError (EPERM) and a
        # plain OSError (EBADF), the realistic pidfd_send_signal
        # failures -- for every cancellation type. ProcessLookupError
        # is deliberately NOT such a case: at both sites it proves the
        # target already exited, so there is no kill failure to keep
        # (the narrowed docstring promise at each site).
        syscall_fault_sites = (
            ("f:_fixture_escalate_subject",
             "held-pidfd subject SIGKILL", 0),
            ("m:_FixtureProcess._escalate",
             "direct guardian SIGKILL backstop", 0))
        for case_label in syscall_fault_sites:
            assert case_label in behavioural_drivers, case_label
        drive_matrix(syscall_fault_sites, site_overrides,
                     lambda: [PermissionError(1,
                                              "injected cleanup fault"),
                              OSError(9, "injected cleanup fault")])
        # fix 30: every listed overwrite still happens -- a fresh
        # object planted on its attribute must not be the value the
        # member reads at entry
        for case_label, attr in sorted(behavioural_overwrites):
            assert case_label in behavioural_drivers, (
                "a listed driver overwrite names no matrix case "
                "(fix 30)", case_label, attr)
            drive_matrix(
                [case_label],
                dict([(case_label[0], [dict([(attr, object())])])]),
                lambda: [RuntimeError("injected cleanup fault")])

        # fix 17 (QA38 codex BLOCKER, reproduced by the
        # orchestrator): the state-conditioned displacement itself,
        # pinned. Rebuild _finish_close from the module's own AST
        # with its capture `pending = exc` replaced by the QA38
        # mutation `pending = exc if not self.armed else None`, and
        # require the armed-state run of the held-descriptor case to
        # go RED against it: with the fixture armed the mutant
        # captures nothing, so the injected close fault displaces
        # the pending cancellation -- the exact escape the derived
        # states above exist to catch. The same armed run is green
        # on the real member, driven in the matrix above.
        import copy
        mutant_member = copy.deepcopy(
            scope["m:_FixtureProcess._finish_close"])
        capture_assigns = [
            node for node in ast.walk(mutant_member)
            if isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == "pending"
            and isinstance(node.value, ast.Name)
            and node.value.id == "exc"]
        assert len(capture_assigns) == 1, (
            "the pinned QA38 mutation site (`pending = exc` inside "
            "_finish_close) is no longer unique (fix 17)",
            len(capture_assigns))
        capture_assigns[0].value = _optlevel.parse(
            "exc if not self.armed else None", mode="eval").body
        mutant_namespace = dict(vars(emit))
        exec(compile(ast.fix_missing_locations(ast.Module(
                body=[mutant_member], type_ignores=[])),
             "<fix 17 QA38 pinned mutant>", "exec", optimize=0),
             mutant_namespace)
        qa38_mutant = mutant_namespace["_finish_close"]  # fix 32 reuses it
        try:
            with patch.object(emit._FixtureProcess, "_finish_close",
                              qa38_mutant):
                behavioural_case(
                    ("m:_FixtureProcess._finish_close",
                     "held descriptor and report close", 0),
                    finish_handles_driver,
                    TimeoutError("pending cancellation"),
                    RuntimeError("injected cleanup fault"),
                    dict(armed=True))
        except AssertionError:
            pass
        else:
            raise AssertionError(
                "the pinned QA38 state-conditioned mutation was NOT "
                "caught by the armed-state behavioural run (fix 17)")

        # fix 18 (QA39 claude BLOCKER / gemini BLOCKER, maintainer
        # decision 2026-09-29): the two found state-conditioned
        # displacements OUTSIDE derived_overrides' disclosed bound,
        # pinned as vectors that must go RED. Each rebuilds
        # _finish_close from the module's own AST with the mutation
        # inserted right after the same unique capture `pending =
        # exc` the QA38 pin locates -- the capture line itself stays
        # pristine and unique for that pin -- and drives the
        # held-descriptor case under the ONE targeted fixture state
        # that fires the displacement, a state the derived matrix
        # never drives (that is the escape): (a) the condition read
        # through a LOCAL (`ok = self._failure is None`), fired by
        # _failure set -- never derived, because _failure is read in
        # an IfExp BODY and branched on via the `failure` local, not
        # spelled in any branch test; (b) the MULTI-ATTRIBUTE
        # condition (`if self.armed and self.unresolved`), fired by
        # armed and unresolved together -- each attribute is derived
        # alone, but the matrix runs single-attribute deviations
        # only. Each targeted state is first proven green on the
        # real member, so a RED against the mutant is attributable
        # to the mutation alone.
        pinned_vectors = (
            ("ok = self._failure is None\n"
             "if not ok:\n"
             "    pending = None",
             dict(_failure=RuntimeError("recorded failure")),
             "locals-conditioned (QA39 claude)"),
            ("if self.armed and self.unresolved:\n"
             "    pending = None",
             dict(armed=True, unresolved=True),
             "multi-attribute-conditioned (QA39 gemini)"),
        )
        for mutation_source, pin_state, vector in pinned_vectors:
            behavioural_case(
                ("m:_FixtureProcess._finish_close",
                 "held descriptor and report close", 0),
                finish_handles_driver,
                TimeoutError("pending cancellation"),
                RuntimeError("injected cleanup fault"),
                dict(pin_state))
            mutant_member = copy.deepcopy(
                scope["m:_FixtureProcess._finish_close"])
            capture_assigns = [
                node for node in ast.walk(mutant_member)
                if isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id == "pending"
                and isinstance(node.value, ast.Name)
                and node.value.id == "exc"]
            assert len(capture_assigns) == 1, (
                "the pinned QA39 mutation site (`pending = exc` "
                "inside _finish_close) is no longer unique "
                "(fix 18)", len(capture_assigns))
            holders = [
                node for node in ast.walk(mutant_member)
                if isinstance(getattr(node, "body", None), list)
                and capture_assigns[0] in node.body]
            assert len(holders) == 1, (
                "the pinned QA39 capture's holding body is not "
                "unique (fix 18)", len(holders))
            at = holders[0].body.index(capture_assigns[0])
            holders[0].body[at + 1:at + 1] = _optlevel.parse(
                mutation_source).body
            mutant_namespace = dict(vars(emit))
            exec(compile(ast.fix_missing_locations(ast.Module(
                    body=[mutant_member], type_ignores=[])),
                 "<fix 18 QA39 pinned mutant>", "exec", optimize=0),
                 mutant_namespace)
            try:
                with patch.object(
                        emit._FixtureProcess, "_finish_close",
                        mutant_namespace["_finish_close"]):
                    behavioural_case(
                        ("m:_FixtureProcess._finish_close",
                         "held descriptor and report close", 0),
                        finish_handles_driver,
                        TimeoutError("pending cancellation"),
                        RuntimeError("injected cleanup fault"),
                        dict(pin_state))
            except AssertionError:
                pass
            else:
                raise AssertionError(
                    "the pinned QA39 " + vector + " mutation was "
                    "NOT caught by its targeted behavioural run "
                    "(fix 18)")

        # fix 19 (QA40 claude/codex/gemini BLOCKER, maintainer
        # decision 2026-09-29): the recognizer missed two ORDINARY
        # spellings of a DIRECT single-attribute branch test that
        # the fix 18 disclosure claimed were driven -- a
        # constant-tuple membership test (comparison values were
        # taken from bare constant sides only, so container members
        # were never derived) and an attribute nested in a call on
        # a comparison side (neither named nor truthy). Both
        # spellings sit INSIDE the disclosed bound, so each must be
        # driven by the ORDINARY matrix, never by a hand-picked
        # state: each pin first derives the fixture states from a
        # probe member holding ONLY the mutated branch and requires
        # the spelling's own states among them -- red-on-revert for
        # the recognizer clause itself -- then rebuilds
        # _finish_close from the module's own AST with the mutation
        # inserted right after the same unique capture `pending =
        # exc` (the capture line stays pristine and unique for the
        # QA38/QA39 pins), requires the firing states among the
        # states derived from that mutant member (what the matrix
        # would drive if this branch were committed code), and
        # requires every firing state to turn the mutant RED. Each
        # firing state is first proven green on the real member, so
        # a RED against the mutant is attributable to the mutation
        # alone.
        fix19_vectors = (
            ("if self._subject_kill in (\"tree\", \"partial\"):\n"
             "    pending = None",
             (dict(_subject_kill="tree"),
              dict(_subject_kill="partial")),
             (dict(_subject_kill="tree"),
              dict(_subject_kill="partial")),
             "container-membership (QA40)"),
            ("if bool(self.armed) == True:\n"
             "    pending = None",
             (dict(armed=False), dict(armed=True)),
             (dict(armed=True),),
             "call-nested-attribute (QA40)"),
        )
        for (mutation_source, derived_states, firing_states,
             vector) in fix19_vectors:
            probe_member = _optlevel.parse(
                "def probe(self):\n" + "".join(
                    "    " + line + "\n"
                    for line in
                    mutation_source.splitlines())).body[0]
            probe_overrides = derived_overrides(
                "m:probe", probe_member)
            for derived_state in derived_states:
                assert derived_state in probe_overrides, (
                    "the derivation no longer recognizes this "
                    "ordinary branch-test spelling: the leg 19 "
                    "claim would again be broader than its code "
                    "(fix 19)", vector, derived_state)
            mutant_member = copy.deepcopy(
                scope["m:_FixtureProcess._finish_close"])
            capture_assigns = [
                node for node in ast.walk(mutant_member)
                if isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id == "pending"
                and isinstance(node.value, ast.Name)
                and node.value.id == "exc"]
            assert len(capture_assigns) == 1, (
                "the pinned QA40 mutation site (`pending = exc` "
                "inside _finish_close) is no longer unique "
                "(fix 19)", len(capture_assigns))
            holders = [
                node for node in ast.walk(mutant_member)
                if isinstance(getattr(node, "body", None), list)
                and capture_assigns[0] in node.body]
            assert len(holders) == 1, (
                "the pinned QA40 capture's holding body is not "
                "unique (fix 19)", len(holders))
            at = holders[0].body.index(capture_assigns[0])
            holders[0].body[at + 1:at + 1] = _optlevel.parse(
                mutation_source).body
            mutant_overrides = derived_overrides(
                "m:_FixtureProcess._finish_close", mutant_member)
            for firing_state in firing_states:
                assert firing_state in mutant_overrides, (
                    "the ordinary matrix would not drive this "
                    "QA40 firing state on a member containing "
                    "the mutated branch (fix 19)", vector,
                    firing_state)
            mutant_namespace = dict(vars(emit))
            exec(compile(ast.fix_missing_locations(ast.Module(
                    body=[mutant_member], type_ignores=[])),
                 "<fix 19 QA40 pinned mutant>", "exec", optimize=0),
                 mutant_namespace)
            for pin_state in firing_states:
                behavioural_case(
                    ("m:_FixtureProcess._finish_close",
                     "held descriptor and report close", 0),
                    finish_handles_driver,
                    TimeoutError("pending cancellation"),
                    RuntimeError("injected cleanup fault"),
                    dict(pin_state))
                try:
                    with patch.object(
                            emit._FixtureProcess, "_finish_close",
                            mutant_namespace["_finish_close"]):
                        behavioural_case(
                            ("m:_FixtureProcess._finish_close",
                             "held descriptor and report close",
                             0),
                            finish_handles_driver,
                            TimeoutError("pending cancellation"),
                            RuntimeError("injected cleanup fault"),
                            dict(pin_state))
                except AssertionError:
                    pass
                else:
                    raise AssertionError(
                        "the pinned QA40 " + vector + " mutation "
                        "was NOT caught by its derived-state "
                        "behavioural run (fix 19)")

        # fix 20 (QA41 codex BLOCKER / claude MAJOR, maintainer
        # decision 2026-09-29): the recognizer missed two more
        # ORDINARY spellings of a DIRECT single-attribute branch
        # test the disclosure's plain reading said were driven --
        # a unary-minus numeric constant (`-1` parses as an
        # ast.UnaryOp, and the pre-fix spelled_constants accepted
        # only ast.Constant, so a bare `-1` side or a container
        # holding one derived nothing), and a type-distinct equal
        # constant (both deduplications compared with plain ==, so
        # a spelled 2 collapsed into an already-collected 2.0 and
        # was never driven -- exactly the state an
        # isinstance-guarded displacement needs). Both spellings
        # sit INSIDE the disclosed bound, so each pin follows the
        # fix 19 shape unchanged: derive from a probe member
        # holding ONLY the mutated branch and require the
        # spelling's own states among them (red-on-revert for the
        # recognizer clause), rebuild _finish_close from the
        # module's own AST with the mutation inserted right after
        # the same unique capture `pending = exc` (the capture
        # line stays pristine and unique for the QA38/QA39/QA40
        # pins), require the firing states among the states
        # derived from that mutant member, prove every firing
        # state green on the real member first, and require it to
        # turn the mutant RED. State membership is checked
        # TYPE-AWARE here too -- dict == compares values with
        # plain ==, the very collapse under pin, so a plain `in`
        # would accept {'pid': 2.0} as {'pid': 2}.
        def driven_state(state, overrides):
            return any(
                set(state) == set(override)
                and all(override[attr] is state[attr]
                        or (type(override[attr])
                            is type(state[attr])
                            and override[attr] == state[attr])
                        for attr in state)
                for override in overrides)

        fix20_vectors = (
            ("if self._subject_kill == -1:\n"
             "    pending = None",
             (dict(_subject_kill=-1),),
             (dict(_subject_kill=-1),),
             "unary-minus-constant (QA41)"),
            ("if self.pid in (2.0, 2) and isinstance(self.pid, "
             "int):\n"
             "    pending = None",
             (dict(pid=2.0), dict(pid=2)),
             (dict(pid=2),),
             "type-collapsed-constant (QA41)"),
        )
        for (mutation_source, derived_states, firing_states,
             vector) in fix20_vectors:
            probe_member = _optlevel.parse(
                "def probe(self):\n" + "".join(
                    "    " + line + "\n"
                    for line in
                    mutation_source.splitlines())).body[0]
            probe_overrides = derived_overrides(
                "m:probe", probe_member)
            for derived_state in derived_states:
                assert driven_state(derived_state,
                                    probe_overrides), (
                    "the derivation no longer recognizes this "
                    "ordinary branch-test spelling: the leg 19 "
                    "claim would again be broader than its code "
                    "(fix 20)", vector, derived_state)
            mutant_member = copy.deepcopy(
                scope["m:_FixtureProcess._finish_close"])
            capture_assigns = [
                node for node in ast.walk(mutant_member)
                if isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id == "pending"
                and isinstance(node.value, ast.Name)
                and node.value.id == "exc"]
            assert len(capture_assigns) == 1, (
                "the pinned QA41 mutation site (`pending = exc` "
                "inside _finish_close) is no longer unique "
                "(fix 20)", len(capture_assigns))
            holders = [
                node for node in ast.walk(mutant_member)
                if isinstance(getattr(node, "body", None), list)
                and capture_assigns[0] in node.body]
            assert len(holders) == 1, (
                "the pinned QA41 capture's holding body is not "
                "unique (fix 20)", len(holders))
            at = holders[0].body.index(capture_assigns[0])
            holders[0].body[at + 1:at + 1] = _optlevel.parse(
                mutation_source).body
            mutant_overrides = derived_overrides(
                "m:_FixtureProcess._finish_close", mutant_member)
            for firing_state in firing_states:
                assert driven_state(firing_state,
                                    mutant_overrides), (
                    "the ordinary matrix would not drive this "
                    "QA41 firing state on a member containing "
                    "the mutated branch (fix 20)", vector,
                    firing_state)
            mutant_namespace = dict(vars(emit))
            exec(compile(ast.fix_missing_locations(ast.Module(
                    body=[mutant_member], type_ignores=[])),
                 "<fix 20 QA41 pinned mutant>", "exec", optimize=0),
                 mutant_namespace)
            for pin_state in firing_states:
                behavioural_case(
                    ("m:_FixtureProcess._finish_close",
                     "held descriptor and report close", 0),
                    finish_handles_driver,
                    TimeoutError("pending cancellation"),
                    RuntimeError("injected cleanup fault"),
                    dict(pin_state))
                try:
                    with patch.object(
                            emit._FixtureProcess, "_finish_close",
                            mutant_namespace["_finish_close"]):
                        behavioural_case(
                            ("m:_FixtureProcess._finish_close",
                             "held descriptor and report close",
                             0),
                            finish_handles_driver,
                            TimeoutError("pending cancellation"),
                            RuntimeError("injected cleanup fault"),
                            dict(pin_state))
                except AssertionError:
                    pass
                else:
                    raise AssertionError(
                        "the pinned QA41 " + vector + " mutation "
                        "was NOT caught by its derived-state "
                        "behavioural run (fix 20)")

        # fix 30 (QA51 claude/codex MAJOR; orchestrator premise review
        # 2026-09-30): the entry check itself, pinned. Each planted
        # deviation is driven through drive_matrix, the matrix's own
        # path, and must go RED for exactly the entry reason: a
        # special attribute whose read ignores the fixture's instance
        # dictionary, a driver overwrite missing from the list (the
        # same state passes while listed), and a listed overwrite that
        # no longer happens. Then the QA51 private-name mutation:
        # _finish_close rebuilt INSIDE a class named _FixtureProcess,
        # so the compiler mangles it as it mangles the committed
        # member, with a private-name branch inserted right after the
        # same unique capture `pending = exc`. The derivation must
        # return the MANGLED firing state, which is first proven green
        # on the real member and must then turn the mutant RED
        # behaviourally; the unmangled key -- the state the pre-fix
        # derivation returned, which passed the mutant -- must go red
        # at entry.
        # fix 31 (QA52): a driver that clears its state and then drives
        # a member cached before the case must go red at entry (codex
        # BLOCKER; fix 32 observes that cached entry, so the driver now
        # enters it under the cleared state); the special attribute is
        # planted on the QA51 mutant, which also loads self.__class__,
        # so only the value check can catch it (claude MINOR). fix 32
        # (QA53 codex/claude BLOCKER): a driver entering once through
        # the class attribute with the deviation, then through a member
        # cached before the case under another value, must go red at
        # entry, on the real member and on the QA38 mutant; so must a
        # driver suspending the observation, and a case finding no
        # free tool id; a caller's tool id and a nested case stay
        # undisturbed, every tool id is released exactly, and each
        # state copy is pinned (claude MINOR 1).
        def entry_red(case_labels, overrides, overwrites, reason,
                      vector):
            try:
                drive_matrix(case_labels, overrides,
                             lambda: [RuntimeError(
                                 "injected cleanup fault")],
                             overwrites)
            except AssertionError as exc:
                entry_reasons = ((ineffective_deviation,),
                                 (stale_overwrite,),
                                 (unobserved_entries,))
                if reason is None:
                    assert exc.args[:1] not in entry_reasons, (
                        "the planted fix 30 vector went red at entry, "
                        "not behaviourally", vector, exc.args)
                else:
                    assert exc.args[:1] == (reason,), (
                        "the planted fix 30 vector went red for the "
                        "wrong reason", vector, exc.args)
            else:
                raise AssertionError(
                    "the planted fix 30 " + vector + " vector was NOT "
                    "caught by the matrix (fix 30)")

        held_case = ("m:_FixtureProcess._finish_close",
                     "held descriptor and report close", 0)
        release_case = ("m:_FixtureProcess._close_masked",
                        "parked launcher release", 0)
        abandon_case = ("m:_FixtureProcess._close_masked",
                        "unfinished-launch abandonment", 0)
        drive_matrix([release_case],
                     dict([(release_case[0], [dict(_go=True)])]),
                     lambda: [RuntimeError("injected cleanup fault")])
        entry_red([release_case],
                  dict([(release_case[0], [dict(_go=True)])]),
                  frozenset(), ineffective_deviation,
                  "unlisted-overwrite")
        entry_red([abandon_case],
                  dict([(abandon_case[0], [dict(
                      _go=types.SimpleNamespace(set=lambda: None))])]),
                  frozenset([(abandon_case, "_go")]), stale_overwrite,
                  "stale-overwrite")
        tool_table = [monitoring.get_tool(tool) for tool in entry_tool_ids]
        cached_finish = vars(emit._FixtureProcess)["_finish_close"]

        def clearing_driver(cancellation, fault, state):
            state.clear()

            def fake_close(fd):
                if fd == 987006:
                    raise fault

            with patch.object(os, "close", fake_close):
                cached_finish(finish_fake(cancellation, state,
                                          pidfd=987006))

        with patch.dict(behavioural_drivers,
                        dict([(held_case, clearing_driver)])):
            entry_red([held_case],
                      dict([(held_case[0], [dict(armed=True)])]),
                      behavioural_overwrites, ineffective_deviation,
                      "state-clearing driver")
        received = []

        def receiving_driver(cancellation, fault, state):
            received.append(state)
            finish_handles_driver(cancellation, fault, state)

        with patch.dict(behavioural_drivers,
                        dict([(held_case, receiving_driver)])):
            drive_matrix([held_case],
                         dict([(held_case[0], [dict(armed=True)])]),
                         lambda: [RuntimeError("injected cleanup fault")])
        assert received and all(type(got) is dict for got in received), (
            "a driver no longer gets its own copy of the state (fix 32, "
            "QA53 claude MINOR 1)", [type(got) for got in received])
        given = dict(armed=True)

        def given_clearing_driver(cancellation, fault, state):
            given.clear()
            finish_handles_driver(cancellation, fault, given)

        try:
            entry_checked_case(held_case, given_clearing_driver,
                               TimeoutError("pending cancellation"),
                               RuntimeError("injected cleanup fault"),
                               given, behavioural_overwrites)
        except AssertionError as exc:
            assert exc.args[:1] == (ineffective_deviation,), exc.args
        else:
            raise AssertionError(
                "the entry check no longer keeps its own copy of the "
                "state (fix 32, QA53 claude MINOR 1)")

        def wrapped_then_cached(cached):
            def driver(cancellation, fault, state):
                # the first entry, through the class attribute where
                # the pre-fix wrapper sat, holds the deviation and
                # fires nothing; the cached entry drives the case
                emit._FixtureProcess._finish_close(
                    finish_fake(None, state))
                state["armed"] = False

                def fake_close(fd):
                    if fd == 987006:
                        raise fault

                with patch.object(os, "close", fake_close):
                    cached(finish_fake(cancellation, state,
                                       pidfd=987006))

            return driver

        for cached, vector in ((cached_finish, "wrapped-then-cached"),
                               (qa38_mutant,
                                "wrapped-then-cached QA38 mutant")):
            with patch.object(emit._FixtureProcess, "_finish_close",
                              cached), (
                    patch.dict(behavioural_drivers, dict(
                        [(held_case, wrapped_then_cached(cached))]))):
                entry_red([held_case],
                          dict([(held_case[0], [dict(armed=True)])]),
                          behavioural_overwrites, ineffective_deviation,
                          vector)

        def suspending_driver(cancellation, fault, state):
            for tool in entry_tool_ids:
                name = monitoring.get_tool(tool)
                if name is not None and name.startswith(
                        entry_tool_name):
                    monitoring.set_local_events(
                        tool, cached_finish.__code__, 0)
            clearing_driver(cancellation, fault, state)

        with patch.dict(behavioural_drivers,
                        dict([(held_case, suspending_driver)])):
            entry_red([held_case],
                      dict([(held_case[0], [dict(armed=True)])]),
                      behavioural_overwrites, unobserved_entries,
                      "observation-suspending driver")
        occupied = []
        try:
            for tool in entry_tool_ids:
                if monitoring.get_tool(tool) is None:
                    monitoring.use_tool_id(tool, "fix 32 occupant")
                    occupied.append(tool)
            entry_red([held_case],
                      dict([(held_case[0], [dict(armed=True)])]),
                      behavioural_overwrites, unobserved_entries,
                      "no free tool id")
        finally:
            for tool in occupied:
                monitoring.free_tool_id(tool)
        caller_tool = next(tool for tool in entry_tool_ids
                           if monitoring.get_tool(tool) is None)
        caller_entries = []

        def caller_callback(started, offset):
            caller_entries.append(started)

        def nesting_driver(cancellation, fault, state):
            entry_checked_case(held_case, finish_handles_driver,
                               type(cancellation)("nested cancellation"),
                               RuntimeError("nested cleanup fault"),
                               state, behavioural_overwrites)
            finish_handles_driver(cancellation, fault, state)

        monitoring.use_tool_id(caller_tool, "fix 32 caller")
        try:
            monitoring.register_callback(caller_tool, entry_event,
                                         caller_callback)
            monitoring.set_local_events(caller_tool,
                                        cached_finish.__code__,
                                        entry_event)
            with patch.dict(behavioural_drivers,
                            dict([(held_case, nesting_driver)])):
                drive_matrix([held_case],
                             dict([(held_case[0], [dict(armed=True)])]),
                             lambda: [RuntimeError(
                                 "injected cleanup fault")])
            caller_intact = (
                monitoring.get_tool(caller_tool) == "fix 32 caller"
                and monitoring.get_local_events(
                    caller_tool, cached_finish.__code__) == entry_event)
        finally:
            monitoring.set_local_events(caller_tool,
                                        cached_finish.__code__, 0)
            caller_released = monitoring.register_callback(
                caller_tool, entry_event, None)
            monitoring.free_tool_id(caller_tool)
        assert (caller_intact and caller_released is caller_callback
                and len(caller_entries)
                == 2 * len(pending_cancellations)), (
            "the entry check disturbed a caller's sys.monitoring tool id "
            "or a nested case (fix 32)", caller_tool, len(caller_entries))
        assert [monitoring.get_tool(tool)
                for tool in entry_tool_ids] == tool_table, (
            "the entry check did not release its tool ids exactly "
            "(fix 32)", tool_table)
        # fix 33 (QA54 codex BLOCKER): a deviation attribute whose
        # read raises INSIDE the entry callback must fail the case.
        # Pre-fix the entry went unrecorded, the raise surfaced as
        # the driver's outward exception, and a nonempty deviation
        # let the run pass silently.
        class _QA54BrokenFixture(types.SimpleNamespace):
            def __getattribute__(self, name):
                if name == "armed":
                    raise RuntimeError(
                        "entry attribute read failed (fix 33)")
                return super().__getattribute__(name)

        def callback_faulting_driver(cancellation, fault, state):
            # the first entry holds the deviation; the second enters
            # through a fixture whose read raises in the callback
            emit._FixtureProcess._finish_close(
                finish_fake(None, state))

            def fake_close(fd):
                if fd == 987006:
                    raise fault

            broken = _QA54BrokenFixture(**vars(
                finish_fake(cancellation, state, pidfd=987006)))
            with patch.object(os, "close", fake_close):
                cached_finish(broken)

        with patch.dict(behavioural_drivers,
                        dict([(held_case, callback_faulting_driver)])):
            entry_red([held_case],
                      dict([(held_case[0], [dict(armed=True)])]),
                      behavioural_overwrites, unobserved_entries,
                      "raising entry callback")
        # fix 33 (QA54 codex MAJOR): a PY_START callback a caller
        # registered on the id before the case -- registration needs
        # no claimed id -- must be back, exactly, once the case has
        # released the id; pre-fix it was discarded on registration
        # and cleared on release
        preregistered_entries = []

        def preregistered_callback(started, offset):
            preregistered_entries.append(started)

        pre_tool = next(tool for tool in entry_tool_ids
                        if monitoring.get_tool(tool) is None)
        monitoring.register_callback(pre_tool, entry_event,
                                     preregistered_callback)
        try:
            drive_matrix([held_case],
                         dict([(held_case[0], [dict(armed=True)])]),
                         lambda: [RuntimeError(
                             "injected cleanup fault")])
        finally:
            restored = monitoring.register_callback(pre_tool,
                                                    entry_event, None)
        assert restored is preregistered_callback, (
            "the caller's previous PY_START callback was not "
            "restored exactly when the case released its tool id "
            "(fix 33, QA54)", pre_tool, restored)
        # fix 33 (QA54 codex MAJOR, claude MINOR 2): an id the driver
        # frees and retakes under the SHARED name is not this case's
        # id; the release leaves it alone (the run itself still goes
        # red: its observation is gone). Pre-fix the release compared
        # the shared name and freed the retaken id.
        retaken = []

        def retaking_driver(cancellation, fault, state):
            for tool in entry_tool_ids:
                name = monitoring.get_tool(tool)
                if (name is not None
                        and name.startswith(entry_tool_name)):
                    monitoring.set_local_events(
                        tool, cached_finish.__code__, 0)
                    monitoring.register_callback(tool, entry_event,
                                                 None)
                    monitoring.free_tool_id(tool)
                    monitoring.use_tool_id(tool, entry_tool_name)
                    retaken.append(tool)
            finish_handles_driver(cancellation, fault, state)

        try:
            with patch.dict(behavioural_drivers,
                            dict([(held_case, retaking_driver)])):
                entry_red([held_case],
                          dict([(held_case[0], [dict(armed=True)])]),
                          behavioural_overwrites, unobserved_entries,
                          "freed-then-retaken id")
            assert retaken and all(
                monitoring.get_tool(tool) == entry_tool_name
                for tool in retaken), (
                "the release freed an id the driver had freed and "
                "retaken under the shared name (fix 33, QA54)",
                retaken,
                [monitoring.get_tool(tool) for tool in retaken])
        finally:
            for tool in retaken:
                if monitoring.get_tool(tool) == entry_tool_name:
                    monitoring.free_tool_id(tool)
        # fix 34 (QA55 codex BLOCKER): the callback stores its
        # preallocated failure flag FIRST, by a store that
        # allocates nothing, and the check fails the run on the
        # flag before any other outcome -- so an allocation
        # failure while recording the failure detail cannot lose
        # the record. Pinned two ways: the flag alone, with no
        # detail recorded and no entry made, must fail the run as
        # unobserved (a check done after the state checks would
        # report an ineffective deviation instead), and the
        # handler's bytecode from its entry to the flag store must
        # stay inside a fixed non-allocating repertoire, with the
        # detail record only after the store.
        captured_callbacks = []

        def flag_setting_driver(cancellation, fault, state):
            for tool in entry_tool_ids:
                name = monitoring.get_tool(tool)
                if (name is not None
                        and name.startswith(entry_tool_name)):
                    callback = monitoring.register_callback(
                        tool, entry_event, None)
                    monitoring.register_callback(tool, entry_event,
                                                 callback)
                    code = callback.__code__
                    assert ("observation_incomplete"
                            in code.co_freevars), (
                        "the entry callback no longer holds the "
                        "preallocated failure flag (fix 34, QA55)",
                        code.co_freevars)
                    flag = callback.__closure__[
                        code.co_freevars.index(
                            "observation_incomplete")].cell_contents
                    flag[0] = True
                    captured_callbacks.append(callback)
            assert captured_callbacks, (
                "the flag vector found no live entry-check tool "
                "id (fix 34, QA55)")

        with patch.dict(behavioural_drivers,
                        dict([(held_case, flag_setting_driver)])):
            entry_red([held_case],
                      dict([(held_case[0], [dict(armed=True)])]),
                      behavioural_overwrites, unobserved_entries,
                      "flag-only callback failure")
        flag_instructions = list(dis.get_instructions(
            captured_callbacks[-1].__code__))
        handler_at = next(
            at for at, instruction in enumerate(flag_instructions)
            if instruction.opname == "PUSH_EXC_INFO")
        flag_at = next(
            at for at, instruction in enumerate(flag_instructions)
            if instruction.argval == "observation_incomplete")
        detail_at = next(
            at for at, instruction in enumerate(flag_instructions)
            if instruction.argval == "observation_failures")
        store_at = next(
            at for at in range(flag_at, len(flag_instructions))
            if flag_instructions[at].opname == "STORE_SUBSCR")
        assert handler_at < flag_at and store_at < detail_at, (
            "the failure flag is not stored first in the entry "
            "callback's handler (fix 34, QA55)",
            handler_at, flag_at, store_at, detail_at)
        allocation_free = frozenset((
            "PUSH_EXC_INFO", "CHECK_EXC_MATCH", "POP_JUMP_IF_FALSE",
            "NOT_TAKEN", "STORE_FAST", "LOAD_FAST",
            "LOAD_FAST_BORROW", "LOAD_DEREF", "LOAD_CONST",
            "LOAD_SMALL_INT", "LOAD_GLOBAL", "STORE_SUBSCR", "COPY",
            "SWAP", "NOP"))
        handler_ops = [instruction.opname for instruction
                       in flag_instructions[handler_at:store_at + 1]]
        assert set(handler_ops) <= allocation_free, (
            "an operation before the failure flag store may "
            "allocate (fix 34, QA55)",
            sorted(set(handler_ops) - allocation_free))
        # fix 34 (QA55 codex MAJOR): a callback a caller
        # registered on the id for ANY event -- not only PY_START
        # -- must be back, exactly, once the case has released the
        # id; pre-fix free_tool_id wiped every other event's
        # callback
        def preregistered_for(event):
            def callback(*args):
                pass
            callback.fix34_event = event
            return callback

        every_tool = next(tool for tool in entry_tool_ids
                          if monitoring.get_tool(tool) is None)
        preregistered_all = [(event, preregistered_for(event))
                             for event in callback_events]
        for event, callback in preregistered_all:
            monitoring.register_callback(every_tool, event,
                                         callback)
        try:
            drive_matrix([held_case],
                         dict([(held_case[0], [dict(armed=True)])]),
                         lambda: [RuntimeError(
                             "injected cleanup fault")])
        finally:
            restored_all = [
                (event, monitoring.register_callback(
                    every_tool, event, None))
                for event, callback in preregistered_all]
        assert restored_all == preregistered_all, (
            "a caller's previous callback for an event other than "
            "PY_START was not restored exactly when the case "
            "released its tool id (fix 34, QA55)", every_tool,
            [event for (event, callback), (_, restored)
             in zip(preregistered_all, restored_all)
             if restored is not callback])
        # fix 34 (QA55 codex MAJOR): setup that raises restores
        # exactly what the case had replaced and never frees an id
        # it did not take. A registration failure after the id was
        # taken frees the id and puts every saved callback back; a
        # save failure before the id was taken puts back only the
        # callbacks already saved and leaves the id untaken.
        setup_tool = next(tool for tool in entry_tool_ids
                          if monitoring.get_tool(tool) is None)

        def setup_prior(started, offset):
            pass

        real_register = monitoring.register_callback

        def entered_rejecting(tool, event, func):
            if getattr(func, "__name__", None) == "entered":
                raise RuntimeError(
                    "injected registration failure (fix 34)")
            return real_register(tool, event, func)

        monitoring.register_callback(setup_tool, entry_event,
                                     setup_prior)
        try:
            with patch.object(monitoring, "register_callback",
                              entered_rejecting):
                try:
                    drive_matrix(
                        [held_case],
                        dict([(held_case[0], [dict(armed=True)])]),
                        lambda: [RuntimeError(
                            "injected cleanup fault")])
                except RuntimeError as exc:
                    assert ("injected registration failure"
                            in str(exc)), exc
                else:
                    raise AssertionError(
                        "the injected registration failure did "
                        "not propagate (fix 34, QA55)")
        finally:
            setup_restored = monitoring.register_callback(
                setup_tool, entry_event, None)
        assert (setup_restored is setup_prior
                and monitoring.get_tool(setup_tool) is None), (
            "a failing registration destroyed the caller's "
            "previous PY_START callback, or left the id claimed "
            "(fix 34, QA55)", setup_tool, setup_restored,
            monitoring.get_tool(setup_tool))
        raise_event = monitoring.events.RAISE
        assert (any(event < raise_event for event in callback_events)
                and any(event > raise_event
                        for event in callback_events)), callback_events

        def save_rejecting(tool, event, func):
            if func is None and event == raise_event:
                raise RuntimeError("injected save failure (fix 34)")
            return real_register(tool, event, func)

        save_prior = [(event, preregistered_for(event))
                      for event in callback_events]
        for event, callback in save_prior:
            monitoring.register_callback(setup_tool, event,
                                         callback)
        try:
            with patch.object(monitoring, "register_callback",
                              save_rejecting):
                try:
                    drive_matrix(
                        [held_case],
                        dict([(held_case[0], [dict(armed=True)])]),
                        lambda: [RuntimeError(
                            "injected cleanup fault")])
                except RuntimeError as exc:
                    assert "injected save failure" in str(exc), exc
                else:
                    raise AssertionError(
                        "the injected save failure did not "
                        "propagate (fix 34, QA55)")
            assert monitoring.get_tool(setup_tool) is None, (
                "a failing save claimed or freed an id the case "
                "never took (fix 34, QA55)", setup_tool,
                monitoring.get_tool(setup_tool))
        finally:
            save_after = [
                (event, monitoring.register_callback(
                    setup_tool, event, None))
                for event, callback in save_prior]
        assert save_after == save_prior, (
            "a failing save did not put back exactly the "
            "callbacks it had removed (fix 34, QA55)", setup_tool,
            [event for (event, callback), (_, after)
             in zip(save_prior, save_after)
             if after is not callback])
        # fix 35 (QA56 codex BLOCKER): the entry callback
        # captures NOTHING -- a captured variable's cell is
        # allocated by MAKE_CELL before the try, where an
        # allocation failure skips the flag -- and every
        # instruction before the protected region's first
        # LOAD_GLOBAL comes from a fixed non-allocating
        # prelude
        entered_code = captured_callbacks[-1].__code__
        assert entered_code.co_cellvars == (), (
            "the entry callback captures a variable into a "
            "cell, allocated before its protected region "
            "(fix 35, QA56)", entered_code.co_cellvars)
        prelude_free = frozenset(("RESUME", "COPY_FREE_VARS",
                                  "NOP", "NOT_TAKEN"))
        prelude_ops = []
        for instruction in dis.get_instructions(entered_code):
            if instruction.opname == "LOAD_GLOBAL":
                break
            prelude_ops.append(instruction.opname)
        assert set(prelude_ops) <= prelude_free, (
            "an operation before the entry callback's protected "
            "region may allocate (fix 35, QA56)",
            sorted(set(prelude_ops) - prelude_free))
        # fix 35 (QA56 codex MAJOR): the save loop holds each
        # removed callback by an item store into its
        # preallocated slot, with nothing between the removal
        # returning and the store -- an allocating record
        # there (the pre-fix append of a fresh tuple) can lose
        # the callback the removal just returned
        case_instructions = list(dis.get_instructions(
            entry_checked_case.__code__))
        held_ops = frozenset(("LOAD_FAST", "LOAD_FAST_BORROW",
                              "LOAD_FAST_LOAD_FAST",
                              "LOAD_FAST_BORROW_LOAD_FAST_BORROW",
                              "COPY", "SWAP", "NOP", "NOT_TAKEN"))
        held_saves = []
        for at, instruction in enumerate(case_instructions):
            if instruction.opname != "STORE_SUBSCR":
                continue
            back = at
            while case_instructions[back - 1].opname in held_ops:
                back -= 1
            if (case_instructions[back - 1].opname == "CALL"
                    and any(prior.argval == "register_callback"
                            for prior in case_instructions[
                                max(0, back - 9):back])):
                held_saves.append(at)
        assert held_saves, (
            "the save loop no longer holds each removed "
            "callback by a non-allocating item store into its "
            "preallocated slot (fix 35, QA56)")
        # fix 35 (QA56 codex MAJOR): a restoration failure at
        # release must not abandon the saved callbacks after
        # it -- every remaining slot is still put back and the
        # first error propagates
        restore_tool = next(tool for tool in entry_tool_ids
                            if monitoring.get_tool(tool) is None)
        restore_prior = [(event, preregistered_for(event))
                         for event in callback_events]
        for event, callback in restore_prior:
            monitoring.register_callback(restore_tool, event,
                                         callback)
        restore_rejected = []

        def restore_rejecting(tool, event, func):
            if (event == raise_event and not restore_rejected
                    and getattr(func, "fix34_event", None)
                    == raise_event):
                restore_rejected.append(func)
                raise RuntimeError(
                    "injected restore failure (fix 35)")
            return real_register(tool, event, func)

        try:
            with patch.object(monitoring, "register_callback",
                              restore_rejecting):
                try:
                    drive_matrix(
                        [held_case],
                        dict([(held_case[0], [dict(armed=True)])]),
                        lambda: [RuntimeError(
                            "injected cleanup fault")])
                except RuntimeError as exc:
                    assert ("injected restore failure"
                            in str(exc)), exc
                else:
                    raise AssertionError(
                        "the injected restore failure did not "
                        "propagate (fix 35, QA56)")
        finally:
            restore_after = [
                (event, monitoring.register_callback(
                    restore_tool, event, None))
                for event, callback in restore_prior]
        assert (restore_rejected
                and monitoring.get_tool(restore_tool) is None), (
            "the injected restore failure never fired, or left "
            "the id claimed (fix 35, QA56)",
            monitoring.get_tool(restore_tool))
        restore_abandoned = [
            event for (event, callback), (_, after)
            in zip(restore_prior, restore_after)
            if after is not callback and event != raise_event]
        assert not restore_abandoned, (
            "a failing restore abandoned the saved callbacks "
            "after it when the case released its tool id "
            "(fix 35, QA56)", restore_abandoned)
        # fix 35 (QA56 codex MAJOR, claude MINOR): a
        # pre-existing local-event mask -- an unclaimed id
        # keeps its per-code masks -- survives a case exactly:
        # the member's mask is saved before setup arms the
        # entry event and restored at release, and freeing
        # clears no local events on other code objects
        def mask_probe():
            pass

        return_event = monitoring.events.PY_RETURN
        mask_tool = next(tool for tool in entry_tool_ids
                         if monitoring.get_tool(tool) is None)
        member_finish = vars(
            emit._FixtureProcess)["_finish_close"].__code__
        monitoring.use_tool_id(mask_tool, "opf fix 35 mask pin")
        try:
            monitoring.set_local_events(mask_tool, member_finish,
                                        return_event)
            monitoring.set_local_events(mask_tool,
                                        mask_probe.__code__,
                                        return_event)
        finally:
            monitoring.free_tool_id(mask_tool)
        try:
            drive_matrix([held_case],
                         dict([(held_case[0], [dict(armed=True)])]),
                         lambda: [RuntimeError(
                             "injected cleanup fault")])
        finally:
            mask_after = (
                monitoring.get_local_events(mask_tool,
                                            member_finish),
                monitoring.get_local_events(mask_tool,
                                            mask_probe.__code__))
            monitoring.use_tool_id(mask_tool, "opf fix 35 mask pin")
            try:
                monitoring.set_local_events(mask_tool,
                                            member_finish, 0)
                monitoring.set_local_events(mask_tool,
                                            mask_probe.__code__, 0)
            finally:
                monitoring.free_tool_id(mask_tool)
        assert mask_after == (return_event, return_event), (
            "a pre-existing local-event mask did not survive "
            "the case exactly (fix 35, QA56)", mask_after,
            return_event)
        # fix 35 (QA56 gemini): an interrupt landing right
        # after the claim -- after use_tool_id returns, before
        # setup finishes -- must not leave the id taken with
        # its callbacks cleared: the id and its saved
        # callbacks are published before the claim, so the
        # release still runs
        class PlantedInterrupt(BaseException):
            pass

        real_use = monitoring.use_tool_id
        claim_interrupted = []

        def interrupting_use(tool, name):
            result = real_use(tool, name)
            if (not claim_interrupted
                    and name.startswith(entry_tool_name)):
                claim_interrupted.append(tool)
                raise PlantedInterrupt
            return result

        interrupt_tool = next(tool for tool in entry_tool_ids
                              if monitoring.get_tool(tool) is None)
        interrupt_prior = [(event, preregistered_for(event))
                           for event in callback_events]
        for event, callback in interrupt_prior:
            monitoring.register_callback(interrupt_tool, event,
                                         callback)
        try:
            with patch.object(monitoring, "use_tool_id",
                              interrupting_use):
                try:
                    drive_matrix(
                        [held_case],
                        dict([(held_case[0], [dict(armed=True)])]),
                        lambda: [RuntimeError(
                            "injected cleanup fault")])
                except PlantedInterrupt:
                    pass
                else:
                    raise AssertionError(
                        "the planted interrupt right after the "
                        "claim did not propagate (fix 35, QA56)")
        finally:
            interrupt_after = [
                (event, monitoring.register_callback(
                    interrupt_tool, event, None))
                for event, callback in interrupt_prior]
        assert claim_interrupted == [interrupt_tool], (
            "the planted interrupt fired on an unexpected id "
            "(fix 35, QA56)", claim_interrupted, interrupt_tool)
        assert monitoring.get_tool(interrupt_tool) is None, (
            "an interrupt right after the claim left the id "
            "taken and unreleased (fix 35, QA56)", interrupt_tool,
            monitoring.get_tool(interrupt_tool))
        assert interrupt_after == interrupt_prior, (
            "an interrupt right after the claim lost a saved "
            "callback (fix 35, QA56)",
            [event for (event, callback), (_, after)
             in zip(interrupt_prior, interrupt_after)
             if after is not callback])
        # fix 36 (QA57 codex MAJOR x2): one REAL SIGINT, delivered
        # by an opcode trace at the reported instruction -- once
        # immediately before the save loop's first STORE_SUBSCR,
        # after register_callback(..., None) returned, and once in
        # the release at the entry-callback unregistration, after
        # the member's mask went back and before free_tool_id and
        # restore_saved -- must leave every saved callback
        # restored and the id unclaimed, the pending SIGINT
        # delivered only after the masked section -- the
        # single-threaded, inside-the-section timing where
        # the best-effort mask does hold (fix 37, QA58: not
        # a general promise). Each delivery
        # is then repeated with the mask removed --
        # signal.pthread_sigmask stubbed out, the reviewed-HEAD
        # behaviour -- and must go red through this vector's own
        # restoration assertions.
        if hasattr(signal, "pthread_sigmask"):
            save_store_offset = case_instructions[
                held_saves[0]].offset
            release_calls = [
                at for at, instruction in enumerate(
                    case_instructions)
                if instruction.opname == "CALL"
                and any(prior.argval == "register_callback"
                        for prior in case_instructions[
                            max(0, at - 9):at])
                and any(later.opname == "STORE_FAST"
                        and later.argval == "released"
                        for later in case_instructions[
                            at + 1:at + 5])]
            # the compiler may emit the release finally twice
            # (a normal-path and an exception-path copy): every
            # match must be that one source statement
            assert (release_calls and len(release_calls) <= 2
                    and len(set(
                        case_instructions[at].positions.lineno
                        for at in release_calls)) == 1), (
                "the release's entry-callback unregistration is "
                "no longer a uniquely traceable statement "
                "(fix 36, QA57)", release_calls)
            release_offsets = tuple(
                case_instructions[at].offset
                for at in release_calls)

            def sigint_delivery(target_offsets):
                # one real SIGINT at one of target_offsets
                # in entry_checked_case, the QA57 technique:
                # tracing is disabled before the signal is sent
                signal_tool = next(
                    tool for tool in entry_tool_ids
                    if monitoring.get_tool(tool) is None)
                prior = [(event, preregistered_for(event))
                         for event in callback_events]
                for event, callback in prior:
                    monitoring.register_callback(
                        signal_tool, event, callback)
                fired = []

                def traced_opcode(frame, event, arg):
                    if (event == "opcode" and not fired
                            and frame.f_lasti in target_offsets):
                        fired.append(frame.f_lasti)
                        sys.settrace(None)
                        frame.f_trace = None
                        os.kill(os.getpid(), signal.SIGINT)
                    return traced_opcode

                def traced_call(frame, event, arg):
                    if frame.f_code is entry_checked_case.__code__:
                        frame.f_trace_opcodes = True
                        return traced_opcode
                    return None

                try:
                    sys.settrace(traced_call)
                    try:
                        drive_matrix(
                            [held_case],
                            dict([(held_case[0],
                                   [dict(armed=True)])]),
                            lambda: [RuntimeError(
                                "injected cleanup fault")])
                    except KeyboardInterrupt:
                        pass
                    else:
                        raise AssertionError(
                            "the real SIGINT was not delivered "
                            "outward (fix 36, QA57)",
                            target_offsets)
                finally:
                    sys.settrace(None)
                    after = [
                        (event, monitoring.register_callback(
                            signal_tool, event, None))
                        for event, callback in prior]
                    still_claimed = monitoring.get_tool(
                        signal_tool)
                    if still_claimed is not None:
                        monitoring.free_tool_id(signal_tool)
                assert (len(fired) == 1
                        and fired[0] in target_offsets), (
                    "the SIGINT vector never reached its target "
                    "instruction (fix 36, QA57)", target_offsets,
                    fired)
                assert still_claimed is None, (
                    "a real SIGINT left the tool id claimed "
                    "(fix 36, QA57)", signal_tool, still_claimed)
                lost = [event
                        for (event, callback), (_, restored)
                        in zip(prior, after)
                        if restored is not callback]
                assert not lost, (
                    "a real SIGINT lost saved callbacks "
                    "(fix 36, QA57)", target_offsets, lost)

            for target_offsets in ((save_store_offset,),
                                   release_offsets):
                sigint_delivery(target_offsets)
                # the proving mutation: the mask removed --
                # pthread_sigmask stubbed to block nothing, the
                # reviewed-HEAD behaviour -- must go red through
                # the vector's own assertion, not a NameError
                unfixed = None
                try:
                    with patch.object(
                            signal, "pthread_sigmask",
                            lambda how, signals: None):
                        sigint_delivery(target_offsets)
                except AssertionError as exc:
                    unfixed = exc
                assert (unfixed is not None
                        and "fix 36" in str(unfixed.args[0])), (
                    "removing the signal mask did not turn the "
                    "SIGINT vector red through its own assertion "
                    "(fix 36, QA57)", target_offsets, unfixed)
        mutant_member = copy.deepcopy(
            scope["m:_FixtureProcess._finish_close"])
        capture_assigns = [
            node for node in ast.walk(mutant_member)
            if isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == "pending"
            and isinstance(node.value, ast.Name)
            and node.value.id == "exc"]
        assert len(capture_assigns) == 1, (
            "the pinned QA51 mutation site (`pending = exc` inside "
            "_finish_close) is no longer unique (fix 30)",
            len(capture_assigns))
        holders = [
            node for node in ast.walk(mutant_member)
            if isinstance(getattr(node, "body", None), list)
            and capture_assigns[0] in node.body]
        assert len(holders) == 1, (
            "the pinned QA51 capture's holding body is not unique "
            "(fix 30)", len(holders))
        at = holders[0].body.index(capture_assigns[0])
        holders[0].body[at + 1:at + 1] = _optlevel.parse(
            "try:\n"
            "    if self.__qa51 == 7:\n"
            "        pending = None\n"
            "except AttributeError:\n"
            "    pass\n"
            "if self.__class__ is True:\n"
            "    pending = None").body
        mangled_state = dict(_FixtureProcess__qa51=7)
        mutant_overrides = derived_overrides(
            "m:_FixtureProcess._finish_close", mutant_member)
        assert (driven_state(mangled_state, mutant_overrides)
                and not any("__qa51" in override
                            for override in mutant_overrides)), (
            "the derivation no longer keys a private name as the "
            "compiler mangles it (fix 30, QA51)", mutant_overrides)
        assert driven_state(dict([("__class__", True)]),
                            mutant_overrides), mutant_overrides
        mutant_class = _optlevel.parse(
            "class _FixtureProcess:\n    pass").body[0]
        mutant_class.body = [mutant_member]
        mutant_namespace = dict(vars(emit))
        exec(compile(ast.fix_missing_locations(ast.Module(
                body=[mutant_class], type_ignores=[])),
             "<fix 30 QA51 pinned mutant>", "exec", optimize=0),
             mutant_namespace)
        behavioural_case(held_case, finish_handles_driver,
                         TimeoutError("pending cancellation"),
                         RuntimeError("injected cleanup fault"),
                         dict(mangled_state))
        with patch.object(
                emit._FixtureProcess, "_finish_close",
                vars(mutant_namespace["_FixtureProcess"])[
                    "_finish_close"]):
            entry_red([held_case],
                      dict([(held_case[0], [mangled_state])]),
                      behavioural_overwrites, None,
                      "mangled private-name mutation")
            entry_red([held_case],
                      dict([(held_case[0], [dict([("__qa51", 7)])])]),
                      behavioural_overwrites, ineffective_deviation,
                      "unmangled private-name")
            entry_red([held_case],
                      dict([(held_case[0], [dict([("__class__", True)])])]),
                      behavioural_overwrites, ineffective_deviation,
                      "special-attribute")

        # fix 30: the leg 19 bound is stated twice, at the leg 19 head
        # and at derived_overrides, and the two copies must stay
        # identical, so neither can drift from the other. fix 31
        # (QA52): the file must hold exactly two terminated blocks,
        # every block line carrying its start marker's indentation
        # and the rest byte-identical; planted drifts are pinned red.
        # fix 32 (QA53 claude MINOR 2): each drift must be caught for
        # its own reason, a lost indentation and a marker line's
        # trailing whitespace included
        bound_marks = (("# -- leg 19 bound (two identical copies, "
                        "fix 30) --").encode(),
                       ("# -- end of leg 19 "
                        "bound --").encode())

        def bound_copy_fault(data):
            lines = data.split(b"\n")
            marks = [(at, line.lstrip(b" "))
                     for at, line in enumerate(lines)
                     if any(mark in line for mark in bound_marks)]
            if [body for at, body in marks] != list(bound_marks) * 2:
                return "not exactly two terminated leg 19 bound blocks"
            copies = []
            for (start, body), (end, _) in zip(marks[::2], marks[1::2]):
                indent = lines[start][:-len(body)]
                block = lines[start:end + 1]
                if not all(line.startswith(indent) for line in block):
                    return "a leg 19 bound line lost its indentation"
                copies.append([line[len(indent):] for line in block])
            if copies[0] != copies[1]:
                return "the two copies of the leg 19 bound differ"
            return None

        own_text = Path(__file__).read_bytes()
        assert bound_copy_fault(own_text) is None, (
            bound_copy_fault(own_text), "(fix 30/31)")
        # fix 34 (QA55 codex MINOR): both copies qualify the case
        # name's uniqueness to live cases
        live_needle = b"among live " + b"cases"
        assert own_text.count(live_needle) == 2, (
            "the leg 19 bound no longer qualifies the case name's "
            "uniqueness to live cases in both copies "
            "(fix 34, QA55)", own_text.count(live_needle))
        # fix 36 (QA57 codex MINOR): the general exclusion is the
        # single disclosure -- its wording appears exactly once
        # per copy, and the removed narrower allocator-detail
        # qualification stays gone; fix 37 (QA58): the rescoped
        # disclosure names the signal-delivery exclusion
        # exactly once per copy too
        allocation_needle = b"interpreter " + b"allocation"
        async_needle = b"any other " + b"asynchronous exception"
        signal_needle = b"asynchronous signal " + b"delivery"
        assert (own_text.count(allocation_needle) == 2
                and own_text.count(async_needle) == 2
                and own_text.count(signal_needle) == 2), (
            "the allocator/audit/async/signal-delivery exclusion "
            "no longer appears exactly once per bound copy "
            "(fix 36/37, QA57/QA58)",
            own_text.count(allocation_needle),
            own_text.count(async_needle),
            own_text.count(signal_needle))
        mark_end = own_text.index(bound_marks[0]) + len(bound_marks[0])
        cut = own_text.index(b"\n", mark_end + 1)
        blocks_fault = "not exactly two terminated leg 19 bound blocks"
        for vector, data, reason in (
                ("trailing whitespace",
                 own_text[:cut] + b" " + own_text[cut:],
                 "the two copies of the leg 19 bound differ"),
                ("unterminated third marker", own_text + bound_marks[0],
                 blocks_fault),
                ("third terminated block",
                 own_text + b"\n".join(bound_marks), blocks_fault),
                ("lost indentation",
                 own_text[:mark_end + 1] + b"X" + own_text[mark_end + 2:],
                 "a leg 19 bound line lost its indentation"),
                ("marker trailing whitespace",
                 own_text[:mark_end] + b" " + own_text[mark_end:],
                 blocks_fault)):
            assert bound_copy_fault(data) == reason, (
                "the planted leg 19 bound drift was NOT caught by the "
                "copy self-check for its own reason (fix 31/32)", vector,
                bound_copy_fault(data))
    elif mode == "receipt-high-fd":
        import fcntl
        import resource
        import select
        import socket
        import time
        import types
        # Fix 2z (claude F2 / gemini F4): the buffered subject receipt must
        # survive a control socket AT OR ABOVE FD_SETSIZE (1024). The layer
        # polls descriptors with select.poll everywhere; the pre-fix
        # _recv_subject probed with select.select, whose fd_set raises
        # ValueError there, and the narrowed handler swallowed it -- the
        # buffered receipt (pid AND pidfd, queued and readable) was silently
        # lost, degrading every receipt-based cleanup to the no-receipt path
        # in exactly the high-descriptor callers the unpinned-kill high-fd
        # leg commits the layer to support.
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        if soft <= 1024:
            resource.setrlimit(resource.RLIMIT_NOFILE, (min(4096, hard), hard))
        subject = os.fork()
        if subject == 0:
            time.sleep(3600)
            os._exit(0)
        subject_fd = os.pidfd_open(subject)
        low, peer = socket.socketpair()
        high = fcntl.fcntl(low.fileno(), fcntl.F_DUPFD, 1024)
        assert high >= 1024, high
        control = socket.socket(fileno=high)
        low.close()
        emit._fixture_send_subject(peer, subject, subject_fd)
        # Flip (red-on-revert): the pre-fix probe on the same high
        # descriptor raises exactly the ValueError the old handler
        # swallowed.
        refuses(ValueError, lambda: select.select([control], [], [], 0))
        fake = types.SimpleNamespace(control=control, armed=True,
                                     subject_pid=None, subject_pidfd=None)
        emit._FixtureProcess._recv_subject(fake)
        assert fake.subject_pid == subject, (
            "the high-fd receipt was lost", fake.subject_pid)
        assert fake.subject_pidfd is not None, "the receipt pidfd was lost"
        os.close(fake.subject_pidfd)
        os.kill(subject, signal.SIGKILL)
        waited, raw = os.waitpid(subject, 0)
        assert (waited == subject and os.WIFSIGNALED(raw)
                and os.WTERMSIG(raw) == signal.SIGKILL), (waited, raw)
        os.close(subject_fd)
        control.close()
        peer.close()
    else:
        raise AssertionError("unknown completion fixture: " + mode)
    print("opf completion:", mode, "PASS")
    return EXIT_OK


def _watchdog_overlap_case(mode):
    """Fix the fork/close interleaving; no sleep decides which caller finishes first."""
    import select
    import threading
    from unittest.mock import patch
    import _opf_emit as emit

    caller = os.getpid()
    paused, resume = threading.Event(), threading.Event()
    sibling_started = threading.Event()
    release_r, release_w = os.pipe()
    entered_r, entered_w = os.pipe()
    real_start = emit._FixtureProcess.start
    results, owners, errors = {}, {}, []

    def start(owner):
        pid = real_start(owner)
        if os.getpid() == caller:
            name = threading.current_thread().name
            owners[name] = owner
            if name == "A":
                if mode == "success":
                    # A's entire tree has exited successfully, with bytes buffered,
                    # but its caller still holds wfd. B must fork in this window.
                    ended = os.waitid(os.P_PID, pid, os.WEXITED | os.WNOWAIT)
                    assert ended.si_code == os.CLD_EXITED and ended.si_status == 0
                paused.set()
                assert resume.wait(10), "A was not resumed"
            else:
                sibling_started.set()  # B's guardian has inherited A's writer
        return pid

    def blocked():
        os.write(entered_w, b"R")
        assert os.read(release_r, 1) == b"G"
        return "B-OK"

    def call(name):
        try:
            thunk = (lambda: "A-OK") if name == "A" else blocked
            timeout = 5 if name == "A" else 15
            if mode == "timeout":
                thunk = blocked if name == "A" else (lambda: "B-OK")
                timeout = 1 if name == "A" else 15
            # The blocked thunk handshakes over caller pipes: under the fd
            # allowlist they must be DECLARED to survive into the subject.
            results[name] = emit.run_bounded(thunk, timeout_s=timeout,
                                             keep_fds=(release_r, entered_w))
        except BaseException as exc:
            errors.append((name, repr(exc)))

    a = threading.Thread(target=call, args=("A",), name="A", daemon=True)
    b = threading.Thread(target=call, args=("B",), name="B", daemon=True)
    try:
        with patch.object(emit._FixtureProcess, "start", start):
            try:
                a.start()
                assert paused.wait(10), "A did not reach its pre-close barrier"
                b.start()
                assert sibling_started.wait(10), "B did not fork while A held wfd"
                ready, _, _ = select.select([entered_r], [], [], 10)
                assert ready and os.read(entered_r, 1) == b"R", "blocked thunk never ran"
                resume.set()
                a.join(10)
                assert not a.is_alive(), "A waited beyond its execution/cleanup budget"
            finally:
                resume.set()
                # B cannot finish the success case until A has returned. On the
                # old EOF-gated collector this forces A to report a false TIMEOUT.
                os.write(release_w, b"G")
                a.join(10)
                if b.ident is not None:
                    b.join(20)
        assert not a.is_alive() and not b.is_alive(), "overlap workers survived"
        assert not errors, errors
        assert set(owners) == {"A", "B"}, owners
        assert all(owner.collected and emit._fixture_child_reaped(owner.pid)
                   for owner in owners.values()), "overlap guardian not collected"
        if mode == "success":
            assert owners["A"].status == 0 and not owners["A"].timed_out
        expected = {"A": "A-OK" if mode == "success" else "TIMEOUT", "B": "B-OK"}
        print("opf watchdog overlap:", mode, results, "expected", expected)
        assert results == expected, results
    finally:
        os.close(release_r)
        os.close(release_w)
        os.close(entered_r)
        os.close(entered_w)
    return EXIT_OK


def _watchdog_regression_self_test():
    """R10: startup/collection/exit bounds and the registered runner under hostile inherited state."""
    import signal
    import subprocess
    from _opf_emit import run_status_owned
    if signal.getsignal(signal.SIGCHLD) != signal.SIG_DFL:
        print("opf watchdog regressions: FAIL (unowned SIGCHLD disposition)")
        return EXIT_FINDING
    if not hasattr(os, "register_at_fork") or not hasattr(signal, "pthread_sigmask"):
        print("opf watchdog regressions: FAIL (required POSIX facilities unavailable)")
        return EXIT_FINDING
    prefix = ("import sys; sys.path.insert(0, " + repr(str(Path(__file__).resolve().parent))
              + "); import opf; ")
    deadline_modes = ("delayed-start", "stuck-start", "pipe-stall", "exit-stall",
                      "transient-census")
    overlap_modes = ("success", "timeout")
    safety_modes = ("ignored-chld", "reaper-chld", "lost-reap", "lost-cleanup",
                    "high-fd", "huge-timeout", "fork-error", "poll-error", "missing-reap",
                    "liveness-permission")
    launcher_labels = ("isolation", "regression", "shared")
    launcher_dispositions = ("ignored", "handler")
    completion_modes = ("audit-ignore", "reaper", "nonce", "fixture-id", "status",
                        "early-exit", "cleanup-reaped", "cleanup-cancel", "premature-exit",
                        "nested-timeout", "nested-cancel", "no-signal-echild",
                        "guardian-error", "empty-children", "bounded-diagnostics",
                        "cleanup-budget", "deadline-flips", "subject-setup",
                        "overdue-success", "launch-ownership", "launch-cancel",
                        "fd-hygiene-total", "nested-keep", "fd-census",
                        "subject-gc", "guardian-preload", "subject-receipt",
                        "subject-ack", "subject-orphan", "pdeathsig",
                        "escalation-subject", "escalate-reaped",
                        "poll-collected", "close-cancel",
                        "escalate-degraded", "poll-masked",
                        "unpinned-kill", "census-verify",
                        "census-exception", "receipt-high-fd")
    # Each launcher case's bound DERIVES from its nested callee's sanctioned
    # budget, following the blocked-isolation convention below (fix 2z,
    # claude F3 / gemini F5): with _run_fixture_process patched, every nested
    # case is exactly one 5 s exit-37 launch and the callee runs TWICE
    # (control + hostile), plus launch margin -- never a flat empirical
    # constant sitting below what the callee itself is sanctioned to wait
    # for (the pre-QA kill-timeout-exceeds-callee-wait flake).
    nested_launches = {
        "isolation": 5 * 6 + 1,  # the timer matrix: 5 labels x 6 modes + window/expiry
        "regression": (len(deadline_modes) + len(overlap_modes)
                       + len(safety_modes)
                       + len(launcher_labels) * len(launcher_dispositions)
                       + len(completion_modes) + 1),  # + blocked-isolation
        "shared": 1,             # a single patched run_status_owned launch
    }
    launcher_bounds = {label: 2 * launches * 5 + 30
                       for label, launches in nested_launches.items()}
    cases = [(mode, prefix + "return opf._watchdog_deadline_case(" + repr(mode) + ")", 10)
             for mode in deadline_modes]
    cases.extend(("overlap-" + mode,
                  prefix + "return opf._watchdog_overlap_case(" + repr(mode) + ")", 60)
                 for mode in overlap_modes)
    cases.extend((mode, prefix + "return opf._watchdog_safety_case(" + repr(mode) + ")", 15)
                 for mode in safety_modes)
    cases.extend((label + "-" + disposition,
                  prefix + "return opf._watchdog_launcher_case("
                  + repr(label) + ", " + repr(disposition) + ")",
                  launcher_bounds[label])
                 for label in launcher_labels
                 for disposition in launcher_dispositions)
    cases.extend(("completion-" + mode,
                  prefix + "return opf._watchdog_completion_case(" + repr(mode) + ")", 40)
                 for mode in completion_modes)
    # Resolve the real registration, without recursively invoking this regression runner.
    # Both inherited disposition and mask are hostile; the outer runner's state is untouched.
    cases.append(("blocked-isolation", prefix
                  + "import signal; signal.signal(signal.SIGALRM, signal.SIG_IGN); "
                  + "signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGALRM}); "
                  + "rc = opf._bootstrap(); "
                  + "return rc if rc else dict(opf._self_tests())['opf-watchdog-isolation']()",
                  31 * 180 + 30))                       # the isolation matrix's full budget plus launch margin
    failed = False
    for label, code, timeout in cases:
        try:
            result = run_status_owned([sys.executable, "-I", "-B", "-c", code],
                                    fixture_id="watchdog/" + label,
                                    capture_output=True, text=True, timeout=timeout)
            ok, detail = result.returncode == EXIT_OK, result.stdout + result.stderr
        except (RuntimeError, subprocess.CalledProcessError) as exc:
            ok, detail = False, str(exc)
        except subprocess.TimeoutExpired:
            ok, detail = False, "fixture exceeded its independent process bound"
        print("opf watchdog regression:", label, "PASS" if ok else "FAIL", detail)
        failed = failed or not ok
    return EXIT_FINDING if failed else EXIT_OK


def _cmd_render(rest):
    """`opf render [--root DIR] (--check | --write)`: the store render verb.

    The READ-ONLY `--check` half forwards to the U4 engine `_opf_views.render`, whose 0/1/2 contract is
    exactly the required one (0 clean, 1 drift, 2 cannot-evaluate; a NOT-ADOPTED root reports NOT APPLICABLE
    and exits 0, the pack's own `--root .` case). The mutating `--write` half (VC-4/PR-C) gathers the inert
    git-derived observations caller-side (_opf_observe.gather over the RESOLVED store, exactly as doctor does)
    and hands them to the same engine, which composes the store-integrity gate and permits the write only
    when source integrity is sound, printing the findings/cannot-evaluates and returning 2 (writing nothing)
    otherwise, so
    a write can never read as a silent no-op. Exactly one of `--check`/`--write` is required: a bare
    `opf render` is a usage error (a preview never defaults into a write). The parser is the house fail-closed
    idiom (unknown token, an empty or option-looking or duplicate --root value -> exit 2), matching
    _opf_views.render's own parser."""
    root = None
    mode = None
    i = 0
    while i < len(rest):
        tok = rest[i]
        if tok in ("--check", "--write"):
            if mode is not None:
                print("opf render: give exactly one of --check / --write", file=sys.stderr)
                return EXIT_MALFORMED
            mode = "check" if tok == "--check" else "write"
            i += 1
        elif tok == "--root":
            if i + 1 >= len(rest):
                print("opf render: --root requires a directory argument", file=sys.stderr)
                return EXIT_MALFORMED
            if root is not None:
                print("opf render: --root given more than once", file=sys.stderr)
                return EXIT_MALFORMED
            val = rest[i + 1]
            if val == "" or val.startswith("-"):
                print("opf render: --root requires a non-empty directory argument, not {!r}".format(val),
                      file=sys.stderr)
                return EXIT_MALFORMED
            root = val
            i += 2
        else:
            print("opf render: unrecognized argument {!r}".format(tok), file=sys.stderr)
            return EXIT_MALFORMED
    if mode is None:
        print("opf render: give exactly one of --check / --write", file=sys.stderr)
        return EXIT_MALFORMED
    if mode == "write":
        # The mutating --write half composes the U6 store-integrity gate in the U4 engine. Resolve here to
        # gather the inert git-derived observations (tracked, actual_remote, prior) the gate consumes; the
        # engine re-resolves (idempotent) and owns the NOT-ADOPTED (0) / non-resolved (2) messaging and the
        # gate itself, so a NOT-ADOPTED or unresolved root needs no observations and never writes.
        argv = ["--write"] if root is None else ["--root", root, "--write"]
        try:
            res = _opf_store.resolve_store(Path(os.path.abspath(root if root is not None else ".")))
        except Exception as exc:  # noqa: BLE001  a resolver escape is cannot-evaluate, never a write
            print("opf render: cannot evaluate: unexpected error resolving the store ({!r}); failing closed "
                  "to exit 2".format(exc), file=sys.stderr)
            return EXIT_MALFORMED
        obs = None
        if res.status == _opf_store.RESOLVED:
            try:
                obs, notes = _opf_observe.gather(res)
            except Exception as exc:  # noqa: BLE001  a gather escape must not become a silent write; fail closed
                print("opf render: cannot evaluate: unexpected error gathering git observations ({!r}); "
                      "failing closed to exit 2".format(exc), file=sys.stderr)
                return EXIT_MALFORMED
            for note in notes:
                # Surface each honest observation gap so the gate's cannot-evaluate reads as an explained
                # disclosure, not silent store corruption (the doctor idiom).
                print("opf render: note: {}".format(note))
        try:
            return _opf_views.render(argv, observations=obs)
        except Exception as exc:  # noqa: BLE001  fail-closed backstop, never a false verdict or uncaught exit-1
            print("opf render: cannot evaluate: unexpected error in the render write ({!r}); failing closed "
                  "to exit 2".format(exc), file=sys.stderr)
            return EXIT_MALFORMED
    argv = ["--check"] if root is None else ["--root", root, "--check"]
    # Class-width backstop: the render dispatch forwards the U4 engine's defined 0/1/2 contract unchanged;
    # any residual, unforeseen error from it routes to a located cannot-evaluate (exit 2), never an uncaught
    # exit-1 escape. KeyboardInterrupt/SystemExit are BaseException and stay uncaught.
    try:
        return _opf_views.render(argv)
    except Exception as exc:  # noqa: BLE001  fail-closed backstop, never a false verdict or uncaught exit-1
        print("opf render: cannot evaluate: unexpected error in the render check ({!r}); failing closed to "
              "exit 2".format(exc), file=sys.stderr)
        return EXIT_MALFORMED


def _cmd_absorb(rest):
    """`opf absorb [--root DIR] [--covers TOKEN] [--freeze-digest]`: the read-only CHANGELOG.md drafter.

    Forwards to the OPF-CHANGELOG-ABSORB engine `_opf_absorb.run`, whose 0/2 contract is exactly the
    required one (0 draft or NOT APPLICABLE, 2 cannot-evaluate; a NOT-ADOPTED root reports NOT APPLICABLE and
    exits 0, the pack's own `--root .` case). The verb is READ-ONLY: the draft (or, with `--freeze-digest`,
    the freeze digest of the curated entry) goes to stdout and the framing to stderr, and the engine writes
    nothing, so there is no stage-then-promote surface. The parser is the house fail-closed idiom (unknown
    token, an empty or option-looking or duplicate --root/--covers value -> exit 2), matching _cmd_render."""
    root = None
    covers = None
    freeze = False
    i = 0
    while i < len(rest):
        tok = rest[i]
        if tok == "--freeze-digest":
            freeze = True
            i += 1
        elif tok == "--root":
            if i + 1 >= len(rest):
                print("opf absorb: --root requires a directory argument", file=sys.stderr)
                return EXIT_MALFORMED
            if root is not None:
                print("opf absorb: --root given more than once", file=sys.stderr)
                return EXIT_MALFORMED
            val = rest[i + 1]
            if val == "" or val.startswith("-"):
                print("opf absorb: --root requires a non-empty directory argument, not {!r}".format(val),
                      file=sys.stderr)
                return EXIT_MALFORMED
            root = val
            i += 2
        elif tok == "--covers":
            if i + 1 >= len(rest):
                print("opf absorb: --covers requires a value", file=sys.stderr)
                return EXIT_MALFORMED
            if covers is not None:
                print("opf absorb: --covers given more than once", file=sys.stderr)
                return EXIT_MALFORMED
            val = rest[i + 1]
            if val == "" or val.startswith("-"):
                print("opf absorb: --covers requires a non-empty value, not {!r}".format(val),
                      file=sys.stderr)
                return EXIT_MALFORMED
            covers = val
            i += 2
        else:
            print("opf absorb: unrecognized argument {!r}".format(tok), file=sys.stderr)
            return EXIT_MALFORMED
    covers_val = covers if covers is not None else _opf_absorb.UNRELEASED
    mode = "freeze-digest" if freeze else "draft"
    # Class-width backstop: the dispatch forwards the engine's defined 0/2 contract unchanged; any residual
    # error routes to a located cannot-evaluate (exit 2), never an uncaught exit-1 escape.
    try:
        return _opf_absorb.run(root if root is not None else ".", covers_val, mode)
    except Exception as exc:  # noqa: BLE001  fail-closed backstop, never a false verdict or uncaught exit-1
        print("opf absorb: cannot evaluate: unexpected error in the drafter ({!r}); failing closed to "
              "exit 2".format(exc), file=sys.stderr)
        return EXIT_MALFORMED


def _doctor_report(result):
    """Print a CONCISE doctor report: the ordered per-check verdict map, then the findings and
    cannot-evaluates, then a one-line residual count. NO diff-style dump (no-console-diff-dumps): this is a
    structured verdict list, not a wall of before/after lines."""
    print("opf doctor: store integrity: {}".format(result.status))
    for cid, verdict in result.checks.items():
        print("  {}: {}".format(cid, verdict))
    for f in result.findings:
        print("  FINDING: {}".format(f))
    for c in result.cannot_evaluate:
        print("  CANNOT-EVALUATE: {}".format(c))
    if result.triage:
        print("  partial-import triage entries: {}".format(len(result.triage)))
    print("  residuals (disclosed by-design, not gradeable): {}".format(len(result.residuals)))


def _cmd_doctor(rest):
    """`opf doctor [--root DIR] [--require-store]`: the store-integrity verb.

    Doctor RESOLVES the store at --root (default: the cwd product repository root), gathers the inert
    git-derived observations (_opf_observe.gather: tracked, actual_remote, prior), and runs the U6 whole-store
    integrity engine (_opf_check.validate_store) over them, returning that engine's own 0/1/2 contract via
    _opf_check.exit_code (0 VALID, 1 INVALID, 2 CANNOT-EVALUATE). A NOT-ADOPTED root reports NOT APPLICABLE
    and exits 0 (the pack's own `--root .` case, mirroring render); any other non-RESOLVED status is a located
    cannot-evaluate (exit 2). `--require-store` is the enforcement-pack CI floor (spec 1.3.0 14.1: the pack
    MUST provide CI checks): with it, a NOT-ADOPTED root is a located cannot-evaluate (exit 2) instead of NOT
    APPLICABLE, so a repository whose store was removed cannot pass CI vacuously; every other status keeps
    its unflagged outcome. Doctor is READ-ONLY: it makes no store change (SECI-preview-has-no-side-effects);
    the observation gather is git reads only. The parser is the house fail-closed idiom (unknown token, an
    empty or option-looking or duplicate --root value, a duplicate --require-store -> exit 2), matching
    _cmd_render's --root loop. Every
    residual escape from the resolver, the git gather, or the engine fails closed to exit 2 (never a false
    verdict), the same class-width backstop the render dispatch carries."""
    root = None
    require_store = False
    i = 0
    while i < len(rest):
        tok = rest[i]
        if tok == "--require-store":
            if require_store:
                print("opf doctor: --require-store given more than once", file=sys.stderr)
                return EXIT_MALFORMED
            require_store = True
            i += 1
        elif tok == "--root":
            if i + 1 >= len(rest):
                print("opf doctor: --root requires a directory argument", file=sys.stderr)
                return EXIT_MALFORMED
            if root is not None:
                print("opf doctor: --root given more than once", file=sys.stderr)
                return EXIT_MALFORMED
            val = rest[i + 1]
            if val == "" or val.startswith("-"):
                print("opf doctor: --root requires a non-empty directory argument, not {!r}".format(val),
                      file=sys.stderr)
                return EXIT_MALFORMED
            root = val
            i += 2
        else:
            print("opf doctor: unrecognized argument {!r}".format(tok), file=sys.stderr)
            return EXIT_MALFORMED
    root = root if root is not None else "."
    try:
        res = _opf_store.resolve_store(Path(os.path.abspath(root)))
    except Exception as exc:  # noqa: BLE001  fail-closed: a resolver escape is cannot-evaluate, never a verdict
        print("opf doctor: cannot evaluate: unexpected error resolving the store at {!r} ({!r}); failing "
              "closed to exit 2".format(root, exc), file=sys.stderr)
        return EXIT_MALFORMED
    if res.status == _opf_store.NOT_ADOPTED:
        if require_store:
            # The CI floor: an absent store is a cannot-evaluate, never a vacuous pass.
            print("opf doctor: cannot evaluate: --require-store was given but no OPF store was found ({}); "
                  "a repository with no store cannot pass the CI floor".format(res.detail), file=sys.stderr)
            return EXIT_MALFORMED
        print("opf doctor: NOT APPLICABLE ({})".format(res.detail))
        return EXIT_OK
    if res.status != _opf_store.RESOLVED:
        print("opf doctor: cannot evaluate: {}".format(res.detail), file=sys.stderr)
        return EXIT_MALFORMED
    try:
        obs, notes = _opf_observe.gather(res)
    except Exception as exc:  # noqa: BLE001  a gather escape must not become a false verdict; fail closed
        print("opf doctor: cannot evaluate: unexpected error gathering git observations ({!r}); failing "
              "closed to exit 2".format(exc), file=sys.stderr)
        return EXIT_MALFORMED
    for note in notes:
        # Surface each honest observation gap so an adopter's CI does not misread it as store corruption.
        print("opf doctor: note: {}".format(note))
    try:
        result = _opf_check.validate_store(res, observations=obs)
    except Exception as exc:  # noqa: BLE001  the engine contracts never to raise; a residual escape fails closed
        print("opf doctor: cannot evaluate: unexpected error validating the store ({!r}); failing closed to "
              "exit 2".format(exc), file=sys.stderr)
        return EXIT_MALFORMED
    _doctor_report(result)
    return _opf_check.exit_code(result)


_INIT_MAX_ENTRIES = 4096
_INIT_MAX_DEPTH = 32
_INIT_MAX_NAME_BYTES = 1 << 20


def _init_kind(st):
    if stat.S_ISDIR(st.st_mode):
        return "directory"
    if stat.S_ISREG(st.st_mode):
        return "file"
    if stat.S_ISLNK(st.st_mode):
        return "symlink"
    return "special"


def _init_inventory(root_fd):
    """Inventory .working without following links, including hidden entries and empty directories.

    Bounds cover entry count, accumulated path bytes, and directory depth. An exceeded bound or
    unreadable entry produces an explicitly incomplete report and refuses initialization. This is
    an observation, not a filesystem snapshot: concurrent changes after enumeration remain possible.
    """
    journal = _opf_store._journal
    report = {"entries": [], "complete": False}
    name_bytes = 0

    def add(relpath, st):
        nonlocal name_bytes
        name_bytes += len(os.fsencode(relpath))
        if (len(report["entries"]) >= _INIT_MAX_ENTRIES
                or name_bytes > _INIT_MAX_NAME_BYTES):
            raise RuntimeError("foreign inventory exceeds entry/path-byte bounds")
        report["entries"].append({"path": relpath, "kind": _init_kind(st)})

    def walk(fd, prefix, depth):
        with os.scandir(fd) as entries:
            for entry in entries:
                relpath = prefix + "/" + entry.name
                st = os.stat(entry.name, dir_fd=fd, follow_symlinks=False)
                add(relpath, st)
                if stat.S_ISDIR(st.st_mode):
                    if depth >= _INIT_MAX_DEPTH:
                        raise RuntimeError("foreign inventory exceeds directory-depth bound")
                    child_fd = os.open(
                        entry.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                    try:
                        walk(child_fd, relpath, depth + 1)
                    finally:
                        _opf_store._close_fd_exc_safe(child_fd)

    try:
        working = _opf_store.WORKING_DIRNAME
        st = journal._lstat_at(root_fd, working)
        if st is not None:
            if not stat.S_ISDIR(st.st_mode):
                add(working, st)
            else:
                working_fd = os.open(
                    working, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd)
                try:
                    walk(working_fd, working, 0)
                finally:
                    _opf_store._close_fd_exc_safe(working_fd)
        report["complete"] = True
    except Exception as exc:  # noqa: BLE001  an incomplete inventory never licenses a write
        report["error"] = ascii(exc)
    report["entries"].sort(key=lambda row: row["path"])
    return report


def _init_git(git, root, args):
    """Reuse the existing explicit -C, scrubbed-environment, timeout-bounded git boundary."""
    result = _opf_observe._run_git(git, root, args)
    if not result.completed or result.rc != 0:
        raise RuntimeError("git preflight/read failed at {!r}: {}".format(
            str(root), result.err))
    return result.out


def _init_repo(root):
    """Confirm a real non-bare worktree at or above root, without requiring a commit."""
    git = _opf_observe._git_path()
    if git is None:
        raise RuntimeError("git preflight: git not found on PATH")
    args = ["rev-parse", "--is-inside-work-tree", "--is-bare-repository", "--show-toplevel"]
    raw = _init_git(git, root, args)
    lines = raw.split(b"\n")
    if (len(lines) != 4 or lines[:2] != [b"true", b"false"]
            or lines[-1] != b"" or not os.path.isabs(os.fsdecode(lines[2]))):
        raise RuntimeError("git preflight: root is not a confirmed non-bare worktree")
    repo = Path(os.path.abspath(os.fsdecode(lines[2])))
    if root != repo and repo not in root.parents:
        raise RuntimeError("git preflight: reported repository does not contain root")
    repo_fd = _opf_store._open_dir_nofollow(repo)
    _opf_store._journal._close_fd_propagating(repo_fd)
    if _init_git(git, repo, args) != raw:
        raise RuntimeError("git preflight: repository identity changed during confirmation")
    return git, repo


def _init_untracked(git, repo, root, paths):
    """Read the index, refusing unreadable state or already-tracked planned destinations.

    Checking .working also detects tracked sources whose working-tree copies were deleted. Those
    paths cannot honestly be described as newly untracked sources. No HEAD observation is needed.
    """
    prefix = root.relative_to(repo)
    scoped = sorted(str(prefix / path) for path in paths)
    tracked = _init_git(
        git, repo, ["--literal-pathspecs", "ls-files", "--cached", "-z", "--"] + scoped)
    if tracked:
        raise RuntimeError("planned destination already git-tracked: {!r}".format(
            os.fsdecode(tracked)))


def _init_unignored(git, repo, root, paths):
    """Refuse a planned destination git would ignore: an ignored store cannot be staged or discovered.

    check-ignore reports at least one ignored path with rc 0, none ignored with rc 1, and an error
    with rc >= 128. The rc drives the three-way decision; the output is only for the diagnostic. Paths
    are passed as arguments (git check-ignore accepts neither -z without --stdin, which the run helper has
    no channel for, nor --literal-pathspecs, which it rejects as unsupported magic). Each scoped path is
    given a literal "./" prefix so a leading colon or other pathspec-magic sigil in an adopter-supplied
    --root prefix is read as a path, not as magic: without it a ":name/..." prefix has its colon consumed
    as an empty magic signature and the check silently bypasses (rc 1, the store looks unignored), while a
    recognized short-magic letter over-refuses (rc >= 128). The prefix is a plain string because Path
    would normalize "./x" back to "x". The output is then one path per line over the controlled,
    newline-free internal destinations.

    OPF-D2B: the probe runs under _opf_observe._run_git_config_discovery, which lets git DISCOVER the
    adopter's real global and system configuration through its default locations (unlike the default scrubbed
    observer environment, which neutralizes it), so a store ignored ONLY by the adopter's global or system
    core.excludesFile is caught here, matching what the adopter's own `git add` would honour
    (guard-input-soundness). Env-based config overrides are dropped and trace/fsmonitor are forced off, so no
    reachable configuration can turn the read-only probe into a write or a launched process. Lazy fetching is
    forced off so a missing indexed ignore blob cannot trigger a promisor fetch that would reach
    core.sshCommand. DISCLOSED RESIDUAL (disclose-guard-residuals): this rc-only check reads ignore rules
    exactly as git add does when git add can read them too. For an ignore input git cannot READ (a
    permission-denied core.excludesFile, ~/.config/git/ignore, or .git/info/exclude), git add is equally unable
    to read it and stages the destination, so reading not-ignored here matches git add and the store is never
    silently ignored. The one input where that parity BREAKS is a skip-worktree (or otherwise unmaterialized)
    .gitignore whose blob is absent in a partial clone: this probe forces lazy fetch off and reads no-rule, but
    the adopter's own git add fetches the blob and can then ignore the store. That case is NOT left as a
    residual: after an rc-1 result the availability of every applicable indexed .gitignore blob is checked
    (_opf_observe.indexed_ignore_availability), and an unavailable one is a cannot-evaluate that REFUSES rather
    than passing (guard-input-soundness). A fail-closed treatment of the unreadable-FILE arms above is
    deliberately not attempted, because git and the caller resolve config paths differently and refusing there
    would over-refuse a case git add itself skips. Called directly
    (not via _init_git, which raises on rc != 0) because rc 1 is the success case here; a timeout, launch
    failure, or unexpected rc fails closed and refuses. Matching `git add`, a benign git diagnostic on an
    rc-1 (not-ignored) result is not itself a refusal.
    """
    prefix = root.relative_to(repo)
    scoped = sorted("./" + str(prefix / path) for path in paths)
    result = _opf_observe._run_git_config_discovery(
        git, repo, ["check-ignore", "--"] + scoped)
    if not result.completed:
        raise RuntimeError(
            "git preflight: could not evaluate ignore status for planned destinations ({})".format(
                result.err))
    if result.rc == 0:
        ignored = [p for p in os.fsdecode(result.out).splitlines() if p]
        raise RuntimeError("planned destination is git-ignored: {!r}".format(ignored))
    if result.rc != 1:
        raise RuntimeError("git preflight: check-ignore failed (rc={}): {}".format(
            result.rc, result.err))
    # rc 1 (not ignored) is sound only when every applicable .gitignore is answerable WITHOUT a promisor
    # fetch. In a partial clone a skip-worktree (or otherwise unmaterialized) .gitignore whose blob is absent
    # reads as no-rule here (lazy fetch is forced off), yet the adopter's own `git add` fetches that blob and
    # can then silently ignore the store; an available blob is read identically by both (parity) and a
    # checked-out .gitignore is read from disk by both. So a missing indexed ignore blob is a cannot-evaluate
    # we refuse, not a clean pass (guard-input-soundness).
    unavailable = _opf_observe.indexed_ignore_availability(
        git, repo, _opf_write_guard._ignore_file_candidates(root.relative_to(repo), paths))
    if unavailable:
        raise RuntimeError(
            "git preflight: an indexed .gitignore blob is unavailable in this partial clone, so ignore "
            "status cannot be determined; `git add` would fetch it and could silently ignore the store "
            "({}). Check out or fetch the blob and retry.".format(sorted(unavailable)))


def _init_same_root(root, root_fd):
    check_fd = _opf_store._open_dir_nofollow(root)
    try:
        current = os.fstat(check_fd)
        opened = os.fstat(root_fd)
        if (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino):
            raise RuntimeError("root changed since its contained directory was opened")
    finally:
        _opf_store._close_fd_exc_safe(check_fd)


def _init_create(root_fd, relpath, data):
    """Create through the existing shared O_EXCL primitive; never replace or remove an entry.

    _journal._recreate_file uses descriptor-relative O_CREAT|O_EXCL|O_NOFOLLOW, writes and fsyncs
    the new inode, and performs no rollback. A write failure can therefore leave a partial file.
    Parent handles prevent symlink redirection; concurrent directory renames are not serialized.
    """
    journal = _opf_store._journal
    pfd, name = journal._open_parent(root_fd, relpath)
    try:
        try:
            journal._recreate_file(pfd, name, data, 0o644)
            os.fsync(pfd)
        except Exception as exc:
            raise RuntimeError("create-only publication refused {!r}: {!r}".format(
                relpath, exc)) from exc
    finally:
        _opf_store._close_fd_exc_safe(pfd)


def _init_observed(root_fd, directories, payloads):
    """Report the planned paths beneath the opened root, without inferring entry ownership."""
    journal = _opf_store._journal
    rows = []
    for relpath in list(directories) + list(payloads):
        row = {"path": relpath}
        try:
            st = journal._lstat_contained(root_fd, relpath)
            if st is None:
                row["state"] = "absent"
            elif relpath in directories:
                row["state"] = _init_kind(st)
            elif not stat.S_ISREG(st.st_mode):
                row["state"] = _init_kind(st)
            elif st.st_size != len(payloads[relpath]):
                row["state"] = "different-size"
                row["bytes"] = st.st_size
            else:
                pfd, name = journal._open_parent(root_fd, relpath)
                try:
                    data, opened = journal._read_at(
                        pfd, name, relpath, cap=len(payloads[relpath]) + 1)
                finally:
                    _opf_store._close_fd_exc_safe(pfd)
                row["state"] = (
                    "matches-payload" if opened.st_nlink == 1 and data == payloads[relpath]
                    else "different-content-or-link-count")
        except Exception as exc:  # noqa: BLE001  unknown is never reported as absent or complete
            row["state"] = "cannot-evaluate"
            row["error"] = ascii(exc)
        rows.append(row)
    return rows


def _cmd_init(rest):
    """Create validated store sources and a pointer, without git writes or rendering.

    Preflight is read-only. Publication is create-only and deliberately not transactional: a
    later failure reports observed planned paths and leaves them for review. Enumeration, root
    identity checks, and final rereads do not serialize concurrent writers or directory renames.
    No lock, lease, rollback, adoption policy, or whole-store success verdict is supplied here.
    """
    root = None
    i = 0
    while i < len(rest):
        tok = rest[i]
        if tok == "--root":
            if i + 1 >= len(rest):
                print("opf init: --root requires a directory argument", file=sys.stderr)
                return EXIT_MALFORMED
            if root is not None:
                print("opf init: --root given more than once", file=sys.stderr)
                return EXIT_MALFORMED
            val = rest[i + 1]
            if val == "" or val.startswith("-"):
                print("opf init: --root requires a non-empty directory argument, not {!r}".format(val),
                      file=sys.stderr)
                return EXIT_MALFORMED
            root = val
            i += 2
        else:
            print("opf init: unrecognized argument {!r}".format(tok), file=sys.stderr)
            return EXIT_MALFORMED
    root = root if root is not None else "."
    root_fd = None
    directories = []
    payloads = {}
    publishing = False
    stage = "preflight"
    try:
        import shlex
        import _opf_init

        journal = _opf_store._journal
        journal.require_containment()
        root = Path(os.path.abspath(root))
        root_fd = _opf_store._open_dir_nofollow(root)
        git, repo = _init_repo(root)

        inventory = _init_inventory(root_fd)
        if inventory["entries"] or not inventory["complete"]:
            print(json.dumps(dict(inventory, event="foreign-content", root=str(root)),
                             sort_keys=True))
        if not inventory["complete"]:
            raise RuntimeError("foreign .working inventory incomplete; refusing initialization")

        # Resolution detects stores through either pointer and through default discovery. Inventory
        # remains independent so malformed stores and foreign content also receive a concrete report.
        res = _opf_store.resolve_store(root)
        pointers = [
            name for name in (_opf_store.POINTER_REL, _opf_store.LOCAL_POINTER_REL)
            if journal._lstat_at(root_fd, name) is not None
        ]
        if pointers:
            raise RuntimeError("existing pointer(s): {}".format(", ".join(pointers)))
        if res.status == _opf_store.RESOLVED:
            raise RuntimeError("existing store: {}".format(res.detail))
        if inventory["entries"]:
            raise RuntimeError("foreign .working content; {}; refusing initialization".format(
                res.detail))
        if res.status != _opf_store.NOT_ADOPTED:
            # The resolver also refuses an existing, empty .working directory. Do not reinterpret a
            # CANNOT-EVALUATE result as permission to initialize a partial store.
            raise RuntimeError("store resolution refused: {}".format(res.detail))

        stage = "building and validating source payloads"
        working = _opf_store.WORKING_DIRNAME
        machine = working + "/" + _opf_store.DEFAULT_MACHINE_SUBDIR
        documents = [
            (_opf_store.MANIFEST_NAME, _opf_init.build_manifest()),
            (_opf_check.COUNTERS_NAME, _opf_init.build_counters()),
            (_opf_check.VERSION_NAME, _opf_init.build_version()),
            (_opf_check.WORKLOG_NAME, _opf_init.build_worklog()),
        ]
        documents.extend(
            (name + _opf_check.INDEX_SUFFIX, _opf_init.build_index(name))
            for name in _opf_init.INDEX_TYPES)
        payloads = {
            machine + "/" + name: text.encode("utf-8") for name, text in documents
        }
        payloads[_opf_store.POINTER_REL] = _opf_emit.emit_checked(
            {"store": {"target": "dir:."}}).encode("utf-8")
        if journal._lstat_at(root_fd, "CHANGELOG.md") is None:
            payloads["CHANGELOG.md"] = b"# Changelog\n"
        directories = [working, machine]

        stage = "checking the destination inventory"
        for relpath in directories + list(payloads):
            journal._check_rel(relpath)
            if journal._lstat_contained(root_fd, relpath) is not None:
                raise RuntimeError("destination already exists: {!r}".format(relpath))
        if journal._lstat_at(root_fd, _opf_store.LOCAL_POINTER_REL) is not None:
            raise RuntimeError("existing pointer: " + _opf_store.LOCAL_POINTER_REL)
        index_paths = set(payloads) | {working, _opf_store.LOCAL_POINTER_REL}
        _init_untracked(git, repo, root, index_paths)
        # The ignore check is scoped to the destinations init actually CREATES (store dirs plus the
        # committed pointer, the machine sources, and the optional CHANGELOG); the local pointer that
        # index_paths carries for _init_untracked's prior-state detection is a path init never creates
        # and adopters legitimately gitignore, so it must not make init refuse.
        _init_unignored(git, repo, root, set(directories) | set(payloads))
        _init_same_root(root, root_fd)

        publishing = True
        for relpath in directories:
            stage = "creating directory " + relpath
            pfd, name = journal._open_parent(root_fd, relpath)
            try:
                os.mkdir(name, 0o755, dir_fd=pfd)
                os.fsync(pfd)
            finally:
                _opf_store._close_fd_exc_safe(pfd)
        for relpath, data in payloads.items():
            stage = "creating " + relpath
            _init_same_root(root, root_fd)
            _init_create(root_fd, relpath, data)

        stage = "observing published source paths"
        observed = _init_observed(root_fd, directories, payloads)
        if any(row["state"] != (
                "directory" if row["path"] in directories else "matches-payload")
               for row in observed):
            raise RuntimeError("published paths do not match the validated payloads")
        final_inventory = _init_inventory(root_fd)
        expected_working = {machine} | {
            path for path in payloads if path.startswith(working + "/")
        }
        if (not final_inventory["complete"]
                or {row["path"] for row in final_inventory["entries"]} != expected_working):
            print(json.dumps(dict(final_inventory, event="post-publish-inventory", root=str(root)),
                             sort_keys=True))
            raise RuntimeError("working inventory changed during publication")
        if journal._lstat_at(root_fd, _opf_store.LOCAL_POINTER_REL) is not None:
            raise RuntimeError("local pointer appeared during publication")
        _init_same_root(root, root_fd)
        _init_untracked(git, repo, root, index_paths)

        print("opf init: store SOURCES created and {} pointer written.".format(
            _opf_store.POINTER_REL))
        print(json.dumps({"event": "created", "root": str(root), "paths": list(payloads)},
                         sort_keys=True))
        print("opf init: these created paths are NOT yet git-tracked (final index read).")
        print("Review the created files, then stage the reviewed paths:")
        print("  git -C {} --literal-pathspecs add -- {}".format(
            shlex.quote(str(root)), " ".join(shlex.quote(path) for path in payloads)))
        print("opf init: ignore eligibility, including the global and system core.excludesFile, was checked")
        print("  before creation; a later ignore or config change can still affect staging (git add -f).")
        print("Commit the reviewed init paths, then materialize the Markdown views:")
        print("  opf render --write --root {}".format(shlex.quote(str(root))))
        print("opf init: exit 0 means valid sources were created; tracking and rendering are pending.")
        return EXIT_OK
    except Exception as exc:  # noqa: BLE001  includes InitError and residual I/O/import errors
        print("opf init: cannot evaluate at {} during {}: {}; exit 2".format(
            ascii(str(root)), stage, ascii(exc)), file=sys.stderr)
        if publishing and root_fd is not None:
            try:
                _init_same_root(root, root_fd)
                binding = "same-root"
            except Exception as binding_exc:
                binding = "cannot-confirm-root: " + ascii(binding_exc)
            print(json.dumps({
                "event": "partial-publication",
                "root": str(root),
                "scope": "opened-root-descriptor",
                "root_binding": binding,
                "paths": _init_observed(root_fd, directories, payloads),
            }, sort_keys=True), file=sys.stderr)
            print("opf init: publication may be partial; review the observed state. No rollback performed.",
                  file=sys.stderr)
        else:
            print("opf init: preflight refused; no publication attempted.", file=sys.stderr)
        return EXIT_MALFORMED
    finally:
        if root_fd is not None:
            _opf_store._journal._close_fd_quietly(root_fd)


# --- opf upgrade: the store-schema upgrade to the tooling spec_version (spec 9.2) ---------------------

# The single 1.0.0 -> 1.1.0 upgrade this build implements. maintainer_decision and preference_pattern
# baseline (they were module-tier in 1.0.0); contribution is net-new; the decision_support module is
# retired (spec 8.1 note, spec 9.2). The delta is enumerated so the postcondition can assert the model
# diff equals EXACTLY it and nothing else (fail-closed on any stray change).
# _UPGRADE_TO is the literal the tooling implements; _cmd_upgrade asserts it equals the live
# _opf_store.SUPPORTED_SPEC_VERSION (bound only after _bootstrap), so a future spec bump cannot let this
# constant silently drift from the roster.
_UPGRADE_FROM = "1.0.0"
# OPF-D2B PR3a (PD-D2B-PR3-SCHEMA decision 5): base spec 1.2.0 admits the managed bootstrap provenance
# `.working/toml/init.toml` a coupled init writes. The 1.1.0 -> 1.2.0 schema delta is the spec_version
# bump ALONE: a 1.1.0 (D2a) store gains no init.toml (no provenance is ever fabricated for it), and a 1.0.0
# store takes the full 1.0.0 schema delta straight to 1.2.0. Declared views are then regenerated.
_UPGRADE_MID = "1.1.0"
_UPGRADE_TO = "1.2.0"
_UPGRADE_NEW_TYPES = ("contribution", "maintainer_decision", "preference_pattern")
_UPGRADE_RETIRED_MODULE = "decision_support"
_UPGRADE_NEW_VIEWS = ("CONTRIBUTIONS.md", "DECISIONS.toml")
# The 1.0.0 -> 1.1.0 delta also WIDENS the existing DECISIONS.md composed view's source set: the two new
# baseline decision types join the two 1.0.0 sources, so the migrated view matches the 1.1.0 required set
# (spec 9.2). Without this, a genuine 1.0.0 store's 2-source DECISIONS.md stays 2-source after the delta and
# fails the render/doctor gate. DECISIONS.toml is a NET-NEW view (in _UPGRADE_NEW_VIEWS) and already carries
# the full four-source set from NAMED_VIEWS.
_UPGRADE_WIDENED_VIEW = "DECISIONS.md"
_UPGRADE_VIEW_FROM_SOURCES = ("pending_decision", "autonomous_decision")

# FIX5 forward-drift PIN (guard-input-soundness): the 1.0.0-valid [modules] vocabulary is the four 1.0.0
# baseline modules PLUS the one module this 1.0.0 -> 1.1.0 delta retires (decision_support); their union is
# exactly the merge-base (1c90fbb) _opf_store.KNOWN_MODULES. _upgrade_plan DERIVES that set from the LIVE
# _opf_store.KNOWN_MODULES at the point of use, which is correct today but UNPINNED: a future KNOWN_MODULES
# edit would silently shift what counts as a valid 1.0.0 module. This FROZEN set pins the intended 1.0.0
# vocabulary (a plain literal, since _opf_store is bound only after _bootstrap); _upgrade_plan reconciles
# the live derivation against it and fails closed on any divergence, and check_opf_upgrade.py binds the two,
# so a future drift fails a gate rather than re-vocabularying the check unseen.
_VALID_1_0_0_MODULES = frozenset({
    "governance", "delivery_assurance", "operational_policy", "concurrent_operation", "decision_support"})


class _UpgradeError(Exception):
    """A fail-closed upgrade refusal carrying the operator-facing reason (mapped to exit 2)."""


def _upgrade_read_bytes(root_fd, relpath, control=False):
    """Read a contained store file's raw bytes no-follow; None when the path is absent. A control file
    (the manifest) is read singly-linked (a hardlink to an out-of-tree victim is refused)."""
    journal = _opf_store._journal
    try:
        data, _st = journal._read_contained(root_fd, relpath, require_single_link=control)
    except journal.JournalError as exc:
        text = str(exc)
        if "cannot read contained file" in text and ("No such file" in text or "FileNotFound" in text):
            return None
        raise _UpgradeError("cannot read {!r} ({})".format(relpath, exc))
    return data


def _upgrade_replace(root_fd, relpath, data):
    """Atomically replace an EXISTING contained regular file with `data` (bytes), no-follow, preserving
    the destination's mode: a fresh O_EXCL temp beneath the same parent fd is written and atomically
    renamed over the entry (never an O_TRUNC of the name), the same reopen-TOCTOU-safe idiom as the U4
    view writer, minus its render write-gate flag (this is the schema-upgrade writer, not a render)."""
    journal = _opf_store._journal
    pfd, name = journal._open_parent(root_fd, relpath)
    try:
        st = journal._lstat_at(pfd, name)
        if st is None or not stat.S_ISREG(st.st_mode):
            raise _UpgradeError("refusing to rewrite {!r}: destination is not an existing regular file "
                                "(a symlink or special file is never followed; fail-closed)".format(relpath))
        tmpname = ".{}.opf-upgrade.{}.{}".format(name, os.getpid(), os.urandom(8).hex())
        fd = os.open(tmpname, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600, dir_fd=pfd)
        renamed = False
        try:
            try:
                os.fchmod(fd, stat.S_IMODE(st.st_mode))
                journal._write_all(fd, data)
                os.fsync(fd)
            finally:
                _opf_store._close_fd_exc_safe(fd)
            os.rename(tmpname, name, src_dir_fd=pfd, dst_dir_fd=pfd)
            renamed = True
            os.fsync(pfd)
        finally:
            if not renamed:
                try:
                    os.unlink(tmpname, dir_fd=pfd)
                except OSError:
                    pass
    finally:
        _opf_store._close_fd_exc_safe(pfd)


def _upgrade_create_index(root_fd, relpath, data):
    """Create a missing empty index file create-only (O_EXCL, no-follow); an already-present index is
    LEFT untouched (governance-enabled MD/PP whose records are preserved byte-for-byte, spec 9.2).
    Returns True when this call created the file, False when it already existed."""
    journal = _opf_store._journal
    pfd, name = journal._open_parent(root_fd, relpath)
    try:
        if journal._lstat_at(pfd, name) is not None:
            return False
        journal._recreate_file(pfd, name, data, 0o644)
        os.fsync(pfd)
        return True
    finally:
        _opf_store._close_fd_exc_safe(pfd)


def _upgrade_plan(manifest_model, counters_model):
    """Apply EXACTLY the spec-9.2 1.0.0 -> 1.1.0 allowed delta to the parsed manifest and counters models,
    returning (new_manifest, new_counters, added_namespaces, origin). It validates UP FRONT the 1.0.0-shape
    preconditions it can cheaply check on the PARSED models -- the required/optional table shapes, the base
    table token and spec_version, KNOWN module keys with boolean values, the module<->baseline-type coupling,
    and the pre-declared type/view rows -- and REFUSES any that fail (a _UpgradeError, fail-closed). It is NOT
    a complete 1.0.0 doctor: a 1.0.0 store invalid in a way these preconditions do not inspect (e.g. a missing
    type row, or a type carrying a wrong or non-normative namespace) is caught AFTER mutation by the render /
    final-doctor gate; recovery restores tracked paths from HEAD and removes upgrade-created files
    within the checked scope -- the no-complete-pre-doctor
    residual disclosed on _cmd_upgrade. It is ORIGIN-AWARE: a governance-
    or decision_support-enabled 1.0.0 store already declares maintainer_decision / preference_pattern as a
    module-tier row (G1/G2), so the delta ADDS only the now-baseline types not already present and preserves
    a pre-declared row untouched; DECISIONS.md and the decision_support [modules] key are both OPTIONAL at
    1.0.0 (G3/G4), so an origin that omits them is migrated without inventing them. `_upgrade_postcondition`
    asserts the manifest AND counters model diffs equal EXACTLY the allowed delta (value-exact), so a stray
    mutation, a flipped retained boolean, a mutated retained row, or a lost high-water can never slip
    through."""
    import copy
    # The 1.0.0 input carries the RETIRED base table [devprocess] (PRIOR_STANDARD_TOKEN); the OPFiles
    # rebrand (1.1.0) renames it to [opf] (STANDARD_TOKEN) as part of this same allowed delta (spec 9.2).
    _prior_base = _opf_store.PRIOR_STANDARD_TOKEN
    # [devprocess] and [types] are REQUIRED for a migratable 1.0.0 store. [modules] and [views] are
    # OPTIONAL at 1.0.0: the merge-base doctor grades an ABSENT table valid-empty (_validate_modules(None)
    # / _validate_views(None) return CLEAN), so an origin that omits either migrates AS IF EMPTY rather
    # than being refused; a PRESENT-but-not-a-dict table stays a malformed refusal.
    for table in (_prior_base, "types"):
        if not isinstance(manifest_model.get(table), dict):
            raise _UpgradeError("manifest [{}] table is missing or malformed; not a store this "
                                "upgrade can migrate (fail-closed)".format(table))
    for table in ("modules", "views"):
        present = manifest_model.get(table)
        if present is not None and not isinstance(present, dict):
            raise _UpgradeError("manifest [{}] table is present but malformed (not a table); not a store "
                                "this upgrade can migrate (fail-closed)".format(table))
    if _opf_store.STANDARD_TOKEN in manifest_model:
        raise _UpgradeError("manifest already carries the renamed base table [{}]; store is not a clean "
                            "1.0.0 baseline (fail-closed)".format(_opf_store.STANDARD_TOKEN))
    if manifest_model[_prior_base].get("spec_version") != _UPGRADE_FROM:
        raise _UpgradeError("manifest spec_version is not {!r}; no known upgrade path".format(_UPGRADE_FROM))
    if manifest_model[_prior_base].get("standard") != _prior_base:
        raise _UpgradeError("manifest [{}].standard is not {!r}; not a store this upgrade migrates "
                            "(fail-closed)".format(_prior_base, _prior_base))
    if not isinstance(counters_model.get("counters"), dict):
        raise _UpgradeError("counters.toml [counters] table is missing or malformed (fail-closed)")

    modules = manifest_model.get("modules") or {}
    types = manifest_model["types"]
    views = manifest_model.get("views") or {}

    # --- ORIGIN FACTS (G1-G4): what this specific 1.0.0 origin family already carries. ---------------
    pre_declared = frozenset(t for t in _UPGRADE_NEW_TYPES if t in types)
    ds_key_present = _UPGRADE_RETIRED_MODULE in modules
    decisions_declared = _UPGRADE_WIDENED_VIEW in views
    origin = {"pre_declared": pre_declared, "ds_key_present": ds_key_present,
              "decisions_declared": decisions_declared}

    # --- PRECONDITIONS: refuse only a genuinely INVALID 1.0.0 input, fail-closed. --------------------
    # [modules] keys are OPTIONAL at 1.0.0 (default off, G3), so decision_support MAY be absent. Every
    # PRESENT module value must be a boolean: the merge-base doctor's _validate_modules grades a non-boolean
    # module value 1.0.0-INVALID, so a non-boolean on ANY module (not only the retired decision_support --
    # e.g. governance = "x") is refused upfront, fail-closed, keeping the docstring's up-front-validation
    # claim honest rather than silently carrying it through to a post-mutation
    # doctor failure. A doctor-VALID 1.0.0 store has only boolean module values, so this never rejects valid
    # input; every module key otherwise rides through untouched (the postcondition asserts value-exact).
    # Every [modules] KEY must be a KNOWN 1.0.0 module name (matching the merge-base _validate_modules, which
    # grades an unknown module key 1.0.0-INVALID). The 1.0.0-valid module vocabulary is the CURRENT baseline
    # module set PLUS the one module this delta RETIRES (decision_support), whose union is exactly the merge-
    # base KNOWN_MODULES; derived from the authoritative KNOWN_MODULES (guard-input-soundness), never a hard-
    # coded list. An unknown key (e.g. [modules].unknown_present) is refused UPFRONT, fail-closed, rather than
    # carried through to a post-mutation doctor failure -- keeping the docstring's up-front-validation claim
    # honest (a MISSING type row or WRONG namespace stays the render/doctor gate's job, disclosed on _cmd_upgrade).
    # FIX5: reconcile the LIVE derivation against the frozen pin (forward-drift guard). A KNOWN_MODULES edit
    # that shifts the 1.0.0 module vocabulary fails HERE (fail-closed), rather than silently re-scoping this
    # 1.0.0-validity check; the pin, not the live union, is then the vocabulary the check uses.
    _derived_1_0_0_modules = frozenset(_opf_store.KNOWN_MODULES) | {_UPGRADE_RETIRED_MODULE}
    if _derived_1_0_0_modules != _VALID_1_0_0_MODULES:
        raise _UpgradeError(
            "the derived 1.0.0 module vocabulary {} drifted from the pinned set {}; reconcile "
            "_VALID_1_0_0_MODULES with _opf_store.KNOWN_MODULES before upgrading (forward-drift pin, "
            "fail-closed)".format(sorted(_derived_1_0_0_modules), sorted(_VALID_1_0_0_MODULES)))
    _valid_1_0_0_modules = _VALID_1_0_0_MODULES
    for _mname in sorted(modules):
        if _mname not in _valid_1_0_0_modules:
            raise _UpgradeError("manifest [modules].{} is not a known 1.0.0 module (known: {}); not a valid "
                                "1.0.0 store (fail-closed)".format(
                                    _mname, ", ".join(sorted(_valid_1_0_0_modules))))
        if not isinstance(modules[_mname], bool):
            raise _UpgradeError("manifest [modules].{} is not a boolean; not a valid 1.0.0 store "
                                "(fail-closed)".format(_mname))
    # A now-baseline type PRE-DECLARED by a 1.0.0 module tier (G1/G2). contribution: no 1.0.0 tier
    # introduced it, so its presence is an impossible 1.0.0 shape -> always refuse. maintainer_decision:
    # only a governance-enabled store carried it; preference_pattern: only a decision_support-enabled
    # store; a pre-declared row whose gating module is not enabled is a module-inconsistent (1.0.0-INVALID)
    # shape, so refusing it keeps fail-closed on genuinely-invalid input. Each pre-declared row must be
    # EXACTLY {namespace = <normative>} (the 1.0.0 closed TYPE_KEYS + normative-namespace rule, G2).
    _pre_gate = {"contribution": None, "maintainer_decision": "governance",
                 "preference_pattern": _UPGRADE_RETIRED_MODULE}
    for tname in sorted(pre_declared):
        gate = _pre_gate[tname]
        if gate is None:
            raise _UpgradeError("manifest pre-declares baseline type {!r}, which no 1.0.0 module tier "
                                "introduced; an impossible 1.0.0 shape (fail-closed)".format(tname))
        if modules.get(gate) is not True:
            raise _UpgradeError("manifest declares type {!r} while its 1.0.0 module {!r} is not enabled; "
                                "a module-inconsistent 1.0.0 shape (fail-closed)".format(tname, gate))
        if types.get(tname) != {"namespace": _opf_store.BASELINE_TYPES[tname]}:
            raise _UpgradeError("manifest [types.{}] is not the exact 1.0.0 baseline row (namespace = "
                                "{!r}); not a clean 1.0.0 shape (fail-closed)".format(
                                    tname, _opf_store.BASELINE_TYPES[tname]))
    # SYMMETRIC module-coupling precondition (C-ROSTER, mirroring the merge-base 1.0.0 doctor). The loop
    # above refuses a DECLARED now-baseline type whose gating 1.0.0 module is not enabled; this refuses the
    # CONVERSE -- a 1.0.0 module that gates a now-baseline type (maintainer_decision<-governance,
    # preference_pattern<-decision_support) is ENABLED while that type's [types] row is ABSENT. Such an
    # origin is module-inconsistent, so the merge-base 1.0.0 doctor grades it NOT-VALID; without this the
    # delta would SILENTLY CURE it by adding the now-baseline row and exit 0, contradicting the docstring's
    # up-front-validation claim (this coupling IS one of the cheap parsed-model checks it promises). Refuse
    # it upfront, unmutated.
    for tname, gate in sorted(_pre_gate.items()):
        if gate is not None and modules.get(gate) is True and tname not in pre_declared:
            raise _UpgradeError("manifest enables 1.0.0 module {!r} but omits its required type {!r}; a "
                                "module-inconsistent 1.0.0 shape the delta must not silently cure "
                                "(fail-closed)".format(gate, tname))
    # The two NET-NEW 1.1.0 views could not exist at 1.0.0 (no 1.0.0 view renderer), so a store
    # pre-declaring either is 1.0.0-INVALID; refuse.
    for vname in _UPGRADE_NEW_VIEWS:
        if vname in views:
            raise _UpgradeError("manifest already declares view {!r}; store is not a clean 1.0.0 "
                                "baseline (fail-closed)".format(vname))
    # DECISIONS.md is OPTIONAL at 1.0.0 (G4): a store may omit it and stay valid, so an absent view is
    # neither widened nor created (M2). When DECLARED, its sources must be EXACTLY the two 1.0.0 decision
    # sources as a LIST with no duplicates and all strings (a doctor-VALID 1.0.0 store may order the pair
    # either way but never duplicates it, G4).
    if decisions_declared:
        _dv_old = views.get(_UPGRADE_WIDENED_VIEW)
        _srcs = _dv_old.get("sources") if isinstance(_dv_old, dict) else None
        if not (isinstance(_dv_old, dict) and isinstance(_srcs, list)
                and all(isinstance(s, str) for s in _srcs)
                and len(_srcs) == len(set(_srcs))
                and set(_srcs) == set(_UPGRADE_VIEW_FROM_SOURCES)):
            raise _UpgradeError("manifest [views.{!r}] is not the 1.0.0 baseline composed view (sources "
                                "must be the two 1.0.0 decision sources {}, no duplicates); not a clean "
                                "1.0.0 baseline (fail-closed)".format(
                                    _UPGRADE_WIDENED_VIEW, sorted(_UPGRADE_VIEW_FROM_SOURCES)))

    # --- DELTA APPLICATION (spec 9.2). --------------------------------------------------------------
    new_manifest = copy.deepcopy(manifest_model)
    # Rename the base table [devprocess] -> [opf] and its discovery token, and bump spec_version, all in
    # the one allowed delta (spec 9.2). The table body is otherwise carried over unchanged.
    _base = new_manifest.pop(_prior_base)
    _base["standard"] = _opf_store.STANDARD_TOKEN
    _base["spec_version"] = _UPGRADE_TO
    new_manifest[_opf_store.STANDARD_TOKEN] = _base
    if ds_key_present:
        del new_manifest["modules"][_UPGRADE_RETIRED_MODULE]
    for tname in _UPGRADE_NEW_TYPES:
        if tname not in pre_declared:
            new_manifest["types"][tname] = {"namespace": _opf_store.BASELINE_TYPES[tname]}
    # An origin that OMITTED [views] entirely (R2) migrates AS IF EMPTY: the table is created here to
    # carry the two net-new 1.1.0 views (an absent [modules] stays absent -- the delta only ever REMOVES a
    # modules key, so migrate-as-empty is a no-op there and no empty table is invented).
    new_manifest.setdefault("views", {})
    for vname in _UPGRADE_NEW_VIEWS:
        kind, sources, _renderer = _opf_views.NAMED_VIEWS[vname]
        new_manifest["views"][vname] = {
            "kind": kind,
            "sources": list(sources),
            "target": "{}/{}".format(_opf_store.WORKING_DIRNAME, vname),
        }
    # Widen the existing DECISIONS.md composed view to the 1.1.0 required source set ONLY when it is
    # declared (spec 9.2 widens "the existing DECISIONS.md composed view"; an absent view has no existence
    # to widen, G4). Set `sources` to the canonical ordered required set from NAMED_VIEWS so the migrated
    # row is byte-identical to a freshly initialized 1.1.0 store's row.
    if decisions_declared:
        new_manifest["views"][_UPGRADE_WIDENED_VIEW]["sources"] = list(
            _opf_views.NAMED_VIEWS[_UPGRADE_WIDENED_VIEW][1])

    new_counters = copy.deepcopy(counters_model)
    added = []
    for tname in _UPGRADE_NEW_TYPES:
        ns = _opf_store.BASELINE_TYPES[tname]
        if ns not in new_counters["counters"]:
            new_counters["counters"][ns] = 0
            added.append(ns)

    # POSTCONDITION (spec 9.2), value-exact: the manifest AND counters model diffs equal EXACTLY the
    # allowed delta and nothing else. Factored pure so it is directly unit-testable (m1).
    _upgrade_postcondition(manifest_model, new_manifest, counters_model, new_counters, origin)
    return new_manifest, new_counters, added, origin


def _upgrade_postcondition(old_manifest, new_manifest, old_counters, new_counters, origin):
    """The spec-9.2 upgrade postcondition, factored PURE so it is directly unit-testable (m1). It recomputes
    the EXPECTED new models from the OLD models plus the origin facts and compares wholesale, value-exact,
    with a per-table failure message for diagnosability. Raises _UpgradeError (fail-closed) on any
    divergence, so a stray mutation, a flipped retained module boolean, a mutated retained [types] row, a
    reordered or duplicated view source, or a lowered/raised/lost counter high-water can never slip through
    (fixes the set-only / key-set-only comparisons this replaced)."""
    import copy
    _prior_base = _opf_store.PRIOR_STANDARD_TOKEN
    _tok = _opf_store.STANDARD_TOKEN
    pre_declared = origin["pre_declared"]
    ds_key_present = origin["ds_key_present"]
    decisions_declared = origin["decisions_declared"]

    # Base table: renamed [devprocess] -> [opf], standard token flipped and spec_version bumped, every
    # other base key carried over value-exact.
    if _prior_base in new_manifest or _tok not in new_manifest:
        raise _UpgradeError("upgrade postcondition failed: base table not renamed [{}] -> [{}]".format(
            _prior_base, _tok))
    expected_base = dict(old_manifest[_prior_base])
    expected_base["standard"] = _tok
    expected_base["spec_version"] = _UPGRADE_TO
    if new_manifest.get(_tok) != expected_base:
        raise _UpgradeError("upgrade postcondition failed: base table [{}] changed beyond the standard-"
                            "token rename and spec_version bump".format(_tok))

    # [modules]: exactly the retired decision_support key removed when it was present, every other key
    # (name AND boolean value) carried over value-exact -- so a flipped retained boolean now refuses.
    expected_modules = {k: v for k, v in (old_manifest.get("modules") or {}).items()
                        if not (ds_key_present and k == _UPGRADE_RETIRED_MODULE)}
    if new_manifest.get("modules", {}) != expected_modules:
        raise _UpgradeError("upgrade postcondition failed: [modules] is not exactly the origin table with "
                            "the retired {!r} key removed".format(_UPGRADE_RETIRED_MODULE))

    # [types]: exactly the origin rows plus the now-baseline rows NOT already pre-declared, value-exact --
    # so a mutated namespace or a stray key in a retained row now refuses, and a pre-declared row is
    # admitted exactly.
    expected_types = dict(old_manifest["types"])
    for tname in _UPGRADE_NEW_TYPES:
        if tname not in pre_declared:
            expected_types[tname] = {"namespace": _opf_store.BASELINE_TYPES[tname]}
    if new_manifest["types"] != expected_types:
        raise _UpgradeError("upgrade postcondition failed: [types] is not exactly the origin rows plus the "
                            "added baseline rows")

    # [views]: origin rows, plus the two constructed new rows, plus (when declared) DECISIONS.md with its
    # sources replaced by the exact canonical ordered required list; value-exact, so the sources assertion
    # is order- AND duplicate-exact.
    expected_views = copy.deepcopy(old_manifest.get("views") or {})
    for vname in _UPGRADE_NEW_VIEWS:
        kind, sources, _renderer = _opf_views.NAMED_VIEWS[vname]
        expected_views[vname] = {
            "kind": kind,
            "sources": list(sources),
            "target": "{}/{}".format(_opf_store.WORKING_DIRNAME, vname),
        }
    if decisions_declared:
        expected_views[_UPGRADE_WIDENED_VIEW] = dict(expected_views[_UPGRADE_WIDENED_VIEW])
        expected_views[_UPGRADE_WIDENED_VIEW]["sources"] = list(
            _opf_views.NAMED_VIEWS[_UPGRADE_WIDENED_VIEW][1])
    if new_manifest["views"] != expected_views:
        raise _UpgradeError("upgrade postcondition failed: [views] is not exactly the origin rows plus the "
                            "two new views and the widened DECISIONS.md")

    # Every OTHER top-level table (store, providers, deliverables, archive, unmanaged, vendors, profiles,
    # ...) is byte-identical: value-exact deep equality over the whole remaining table set.
    _handled = {"modules", "types", "views", _prior_base, _tok}
    for table in set(old_manifest) | set(new_manifest):
        if table in _handled:
            continue
        if old_manifest.get(table) != new_manifest.get(table):
            raise _UpgradeError("upgrade postcondition failed: table [{}] changed but is not in the allowed "
                                "delta (fail-closed)".format(table))

    # Counters (m1): every existing high-water preserved value-exact, EXACTLY the CN/MD/PP namespaces
    # ABSENT from the origin added as zeros, the schema key and any other content untouched.
    if not isinstance(old_counters.get("counters"), dict):
        raise _UpgradeError("upgrade postcondition failed: origin counters [counters] table malformed")
    expected_counters = copy.deepcopy(old_counters)
    for tname in _UPGRADE_NEW_TYPES:
        ns = _opf_store.BASELINE_TYPES[tname]
        if ns not in expected_counters["counters"]:
            expected_counters["counters"][ns] = 0
    if new_counters != expected_counters:
        raise _UpgradeError("upgrade postcondition failed: counters is not exactly the origin high-waters "
                            "with the missing CN/MD/PP namespaces added as zeros")


def _upgrade_plan_minor(manifest_model, counters_model):
    """The 1.1.0 -> 1.2.0 allowed delta (spec 9.2, OPF-D2B PR3a): the [opf].spec_version bump ALONE.
    Preconditions (fail-closed): the current [opf] base table (never the retired [devprocess]) declaring
    standard "opf" and spec_version 1.1.0, and a [counters] table. No init.toml provenance is created
    (none is ever fabricated for an existing store), no type, view, module, index, or counter changes,
    and the postcondition asserts the manifest diff is EXACTLY the version field and the counters model
    is unchanged. Returns (new_manifest, new_counters, added_namespaces, origin) like _upgrade_plan."""
    import copy
    base = manifest_model.get(_opf_store.STANDARD_TOKEN)
    if not isinstance(base, dict) or _opf_store.PRIOR_STANDARD_TOKEN in manifest_model:
        raise _UpgradeError("manifest carries no [{}] base table (or still carries the retired [{}]); "
                            "not a {} store this upgrade migrates (fail-closed)".format(
                                _opf_store.STANDARD_TOKEN, _opf_store.PRIOR_STANDARD_TOKEN,
                                _UPGRADE_MID))
    if base.get("standard") != _opf_store.STANDARD_TOKEN or base.get("spec_version") != _UPGRADE_MID:
        raise _UpgradeError("manifest [{}] is not a {} base; no known upgrade path (fail-closed)".format(
            _opf_store.STANDARD_TOKEN, _UPGRADE_MID))
    if not isinstance(counters_model.get("counters"), dict):
        raise _UpgradeError("counters.toml [counters] table is missing or malformed (fail-closed)")
    new_manifest = copy.deepcopy(manifest_model)
    new_manifest[_opf_store.STANDARD_TOKEN]["spec_version"] = _UPGRADE_TO
    new_counters = copy.deepcopy(counters_model)
    expected = copy.deepcopy(manifest_model)
    expected[_opf_store.STANDARD_TOKEN] = dict(expected[_opf_store.STANDARD_TOKEN],
                                               spec_version=_UPGRADE_TO)
    if new_manifest != expected or new_counters != counters_model:
        raise _UpgradeError("upgrade postcondition failed: the {} -> {} delta is the spec_version bump "
                            "alone".format(_UPGRADE_MID, _UPGRADE_TO))
    origin = {"pre_declared": frozenset(), "ds_key_present": False, "decisions_declared": None,
              "from": _UPGRADE_MID}
    return new_manifest, new_counters, [], origin


def _cmd_upgrade(rest):
    """`opf upgrade [--root DIR]`: the in-place, additive, idempotent store-schema upgrade to the tooling
    spec_version {to} (spec 9.2). Two origins are supported: a 1.1.0 store takes the 1.1.0 -> {to} schema
    delta, changing only spec_version (no init.toml provenance is fabricated); a 1.0.0 store takes the
    full schema delta below, straight to {to}. Declared views are then regenerated, so a stale
    committed view can change.
    It RESOLVES the store at --root and refuses a store above the tooling spec. When migrating a 1.0.0
    or 1.1.0 store, its preconditions include readable, canonical manifest/counters matching a recognised
    origin shape, cleanliness over planned schema/render destinations and index collision candidates
    (including ignored files there), and acquisition of its lease. An untracked or ignored lease is
    excepted from the cleanliness check and handled separately by lease acquisition.
    It then applies EXACTLY the allowed delta as
    a model regeneration through the canonical new-document emitter (bump spec_version; drop the retired
    decision_support module WHERE PRESENT; add each contribution/maintainer_decision/preference_pattern type
    row NOT already declared by an enabled 1.0.0 module; add the two new view rows; WIDEN the DECISIONS.md
    composed view WHERE DECLARED; extend counters with zeros for any missing CN/MD/PP namespace,
    preserving existing high-waters; create the missing empty indexes, skipping any that already exist),
    RENDERS the declared views, and requires a full doctor VALID before offering the uncommitted change. It is
    ORIGIN-AWARE: a governance- or decision_support-enabled 1.0.0 store, and a store that omits the
    optional decision_support key or the DECISIONS.md view, each migrate correctly (spec 9.2, G1-G4).
    Before ANY write it enforces two fail-closed preconditions:
    PLANNED-DESTINATION CLEANLINESS (store and product roots), including ignored files, over planned
    destinations and collisions (HEAD preserves pre-existing tracked content,
    SECA-verified-restore-path), with an untracked or ignored lease handled separately, and a
    SINGLE-WRITER LEASE it claims atomically and holds across the mutation, render, and final doctor (spec
    5.7). It NEVER commits: the adopter reviews and merges. A store already at {to} returns without those
    migration preconditions: doctor-VALID yields a byte no-op (exit 0), otherwise it exits 2 without
    writing. A NOT-ADOPTED root is NOT APPLICABLE (exit 0), any other non-resolved status a located
    cannot-evaluate (exit 2). Two disclosed residuals: a killed run leaves the lease, which is
    spec-conformant (present only while held; a leftover is released through operator reconciliation, spec
    5.7) and is what the EEXIST refusal covers; and the lease is not made observable at a sync target
    before writes (spec 5.7) because this build has no sync runtime, so the guarantee is single-host
    single-writer. A third disclosed residual: this build has no pre-doctor for a 1.0.0 or 1.1.0 origin,
    so an older store invalid in a way the origin preconditions do not inspect fails only AFTER mutation
    (at the render or the final doctor). Under the single-writer contract, recovery restores pre-existing
    tracked content from HEAD and removes upgrade-created files within the checked store/product scope;
    pre-existing untracked or ignored content in that scope refuses before mutation.""".format(
        to=_UPGRADE_TO)
    root = None
    i = 0
    while i < len(rest):
        tok = rest[i]
        if tok == "--root":
            if i + 1 >= len(rest):
                print("opf upgrade: --root requires a directory argument", file=sys.stderr)
                return EXIT_MALFORMED
            if root is not None:
                print("opf upgrade: --root given more than once", file=sys.stderr)
                return EXIT_MALFORMED
            val = rest[i + 1]
            if val == "" or val.startswith("-"):
                print("opf upgrade: --root requires a non-empty directory argument, not {!r}".format(val),
                      file=sys.stderr)
                return EXIT_MALFORMED
            root = val
            i += 2
        else:
            print("opf upgrade: unrecognized argument {!r}".format(tok), file=sys.stderr)
            return EXIT_MALFORMED
    root = root if root is not None else "."
    try:
        return _upgrade_run(root)
    except (_UpgradeError, _opf_write_guard.WriteGuardError) as exc:
        print("opf upgrade: refused: {}; exit 2".format(exc), file=sys.stderr)
        return EXIT_MALFORMED
    except Exception as exc:  # noqa: BLE001  class-width fail-closed backstop, never a false success
        print("opf upgrade: cannot evaluate: unexpected error ({!r}); failing closed to exit 2".format(exc),
              file=sys.stderr)
        return EXIT_MALFORMED


def _upgrade_doctor(root):
    """Resolve the store at `root`, gather its git-derived observations, and run the full offline doctor
    (`validate_store`). Returns the _opf_check validate result (its `.status` is `_opf_store.VALID` on a
    clean store). Raises _UpgradeError, fail-closed, when the store no longer resolves."""
    dres = _opf_store.resolve_store(Path(os.path.abspath(root)))
    if dres.status != _opf_store.RESOLVED:
        raise _UpgradeError("the store at {!r} no longer resolves ({}); fail-closed".format(root, dres.detail))
    dobs, _dnotes = _opf_observe.gather(dres)
    return _opf_check.validate_store(dres, observations=dobs)


# --- STEP 3/4 upgrade preconditions: store cleanliness and the single-writer lease -------------------

_UPGRADE_NO_WHOLE_TREE = ("Never run a whole-tree restore (git restore . / git reset --hard): it would "
                          "destroy unrelated uncommitted work. Scope every recovery to the affected "
                          "store and product paths.")
# The planned scope the shared cleanliness gate names in its dirty-tree refusal (_opf_write_guard.check_clean).
_UPGRADE_CLEAN_SCOPE = "schema and render destinations and index collision candidates"


def _upgrade_partial_recovery_text(res, manifest_model):
    """A previous interrupted run has no trustworthy write plan in this process. Inspect under the
    resolved store root, but do not prescribe a subtree restore: it could discard unmanaged owner work."""
    import shlex
    r = shlex.quote(str(res.store_root))
    w = shlex.quote(_opf_store.WORKING_DIRNAME)
    try:
        scope = _upgrade_write_scope(res.machine_rel, manifest_model, True, _UPGRADE_NEW_TYPES)
        candidates = "Candidate store destinations under {} (current manifest; inspect only): {}.".format(
            r, " ".join(shlex.quote(p) for p in scope["store"]))
        if scope["product"]:
            product_root = res.product_root if res.product_root is not None else res.store_root
            candidates += "\nCandidate product destinations under {} (inspect only): {}.".format(
                shlex.quote(str(product_root)), " ".join(shlex.quote(p) for p in scope["product"]))
    except Exception as exc:  # Candidate advice must not hide the doctor findings.
        candidates = "Cannot derive candidate destinations from the current manifest: {}.".format(exc)
    return ("Inspect the store subtree (git -C {r} --literal-pathspecs status --ignored=matching "
            "--untracked-files=all -- {w}). Identify the earlier run's planned destinations and reconcile "
            "intervening owner edits before restoring individual tracked paths or removing files proven "
            "upgrade-created. Exclude unmanaged paths and the lease; reconcile a leftover lease only after "
            "confirming no run is live (spec 5.7). Reconcile product targets separately, then re-run. "
            "{no}\n{candidates} These candidates do not prove the earlier write scope or which files were "
            "created; reconcile them against the earlier run and owner edits before recovery.".format(
                r=r, w=w, no=_UPGRADE_NO_WHOLE_TREE, candidates=candidates))


def _upgrade_recovery_text(store_root, product_root, created_relpaths, product_relpaths, store_relpaths):
    """Recovery advice for a post-mutation failure (render or doctor): the planned set is KNOWN, and with the
    step-3 cleanliness precondition (including ignored files) and the single-writer contract, HEAD
    preserves pre-existing tracked content in the checked scope; remove upgrade-created files to recover
    previously absent paths. Inspect first for intervening owner edits. The DISTINCT roots are threaded
    (explicit-binding-over-ambient-context): tracked store paths
    in the explicit plan, and the upgrade-created untracked files, are recovered under the STORE root (where
    `.working` lives); a declared product-scope target rendered this run is recovered under the PRODUCT root.
    The two roots differ for a RELOCATED store (the pointer resolves `.working` to a store separate from the
    product tree), where using one root for both would aim the `.working` restore at the wrong repository.
    Never a whole-tree restore."""
    import shlex
    r = shlex.quote(str(store_root))
    w = " ".join(shlex.quote(p) for p in sorted(store_relpaths))
    tracked = " ".join(shlex.quote(p) for p in sorted(set(store_relpaths) - set(created_relpaths)))
    lines = ["opf upgrade: the uncommitted change is left for review; recover it scoped to the paths this run "
             "planned (tracked content was clean against HEAD; untracked and ignored content was refused). "
             "Confirm no opf run is live (spec 5.7), then inspect for intervening owner edits:"]
    if tracked:
        lines.append("  restore tracked store paths: git -C {} --literal-pathspecs restore --staged "
                     "--worktree -- {}".format(r, tracked))
    if created_relpaths:
        lines.append("  remove only these previously absent files if this run created them: rm -- " + " ".join(
            shlex.quote(os.path.join(str(store_root), p)) for p in sorted(created_relpaths)))
    pr = shlex.quote(str(product_root))
    for p in sorted(product_relpaths):
        lines.append("  product target rendered: git -C {} --literal-pathspecs restore --staged --worktree "
                     "-- {} (or remove it if this run created it)".format(pr, shlex.quote(p)))
    lines.append("  inspect first: git -C {} --literal-pathspecs status --ignored=matching "
                 "--untracked-files=all -- {}".format(r, w))
    lines.append("  " + _UPGRADE_NO_WHOLE_TREE)
    return "\n".join(lines)


def _upgrade_write_scope(machine_rel, manifest_model, counters_changed, index_types):
    """Plan the destinations of the schema delta and the subsequent declared-view render.
    Index candidates are included for collision checks even when create-only will leave them alone.
    Use the POST-delta manifest: it includes newly introduced views. The shared planner
    (_opf_write_guard.plan_write_scope) adds every declared render destination and refuses a declaration
    colliding with a managed destination before mutation, rather than licensing an overwrite. The lease
    is checked separately and is never a restore/staging target."""
    store = ["{}/{}".format(machine_rel, _opf_store.MANIFEST_NAME)]
    if counters_changed:
        store.append("{}/{}".format(machine_rel, _opf_check.COUNTERS_NAME))
    indexes = tuple("{}/{}{}".format(machine_rel, t, _opf_check.INDEX_SUFFIX) for t in index_types)
    store.extend(indexes)
    scope = _opf_write_guard.plan_write_scope(machine_rel, manifest_model, store, "upgrade")
    scope["indexes"] = indexes
    return scope


def _upgrade_run(root):
    import shlex
    import tomllib
    if _UPGRADE_TO != _opf_store.SUPPORTED_SPEC_VERSION:
        raise _UpgradeError("upgrade target {!r} does not match the tooling spec_version {!r}; refusing to "
                            "run a stale upgrade path (fail-closed)".format(
                                _UPGRADE_TO, _opf_store.SUPPORTED_SPEC_VERSION))
    try:
        # `opf upgrade` is the ONE caller that also accepts the retired 1.0.0 discovery token, so a legacy
        # [devprocess] store still resolves for migration (spec 9.2); every other tool keeps the sole
        # current-token discovery.
        res = _opf_store.resolve_store(
            Path(os.path.abspath(root)),
            accept_tokens=(_opf_store.STANDARD_TOKEN, _opf_store.PRIOR_STANDARD_TOKEN))
    except Exception as exc:  # noqa: BLE001  a resolver escape is cannot-evaluate, never a mutation
        raise _UpgradeError("unexpected error resolving the store at {!r} ({!r})".format(root, exc))
    if res.status == _opf_store.NOT_ADOPTED:
        print("opf upgrade: NOT APPLICABLE ({})".format(res.detail))
        return EXIT_OK
    if res.status != _opf_store.RESOLVED:
        print("opf upgrade: cannot evaluate: {}".format(res.detail), file=sys.stderr)
        return EXIT_MALFORMED

    machine_rel = res.machine_rel
    manifest_rel = "{}/{}".format(machine_rel, _opf_store.MANIFEST_NAME)
    counters_rel = "{}/{}".format(machine_rel, _opf_check.COUNTERS_NAME)
    root_fd = _opf_store._open_dir_nofollow(res.store_root)
    recovery = None
    try:
        manifest_bytes = _upgrade_read_bytes(root_fd, manifest_rel, control=True)
        counters_bytes = _upgrade_read_bytes(root_fd, counters_rel)
        if manifest_bytes is None or counters_bytes is None:
            raise _UpgradeError("store manifest or counters is absent; not a resolvable store to upgrade")
        try:
            manifest_model = tomllib.loads(manifest_bytes.decode("utf-8"))
            counters_model = tomllib.loads(counters_bytes.decode("utf-8"))
        except (UnicodeError, tomllib.TOMLDecodeError) as exc:
            raise _UpgradeError("store manifest or counters does not parse as TOML ({})".format(exc))

        # spec_version triage: current is an idempotent byte no-op; above-tooling fails closed; only the
        # single known 1.0.0 origin proceeds. The base table is [opf] on an already-migrated store and the
        # retired [devprocess] on a legacy 1.0.0 store, so read whichever the manifest carries.
        _base_model = manifest_model.get(_opf_store.STANDARD_TOKEN)
        if not isinstance(_base_model, dict):
            _base_model = manifest_model.get(_opf_store.PRIOR_STANDARD_TOKEN)
        # Worklog activation is independent of spec_version. Refuse before even
        # the cleanliness probe or lease can write; a later render fence is too late.
        # Normalize only the retired base-table name for the shared loader check.
        try:
            _opf_worklog.generation({"opf": _base_model})
        except _opf_worklog.WorklogError as exc:
            raise _UpgradeError(str(exc)) from exc
        sv = _base_model.get("spec_version") if isinstance(_base_model, dict) else None
        if sv == _UPGRADE_TO:
            # F2: a store already at the target is a no-op ONLY when it is genuinely doctor-VALID at 1.1.0.
            # A partial or interrupted migration leaves spec_version == 1.1.0 with an incomplete delta;
            # trusting the version marker alone would report false success over a broken store. Re-validate
            # and fail closed on anything short of VALID, so a partial migration is never reported complete.
            result = _upgrade_doctor(root)
            if result.status == _opf_store.VALID:
                print("opf upgrade: store is already at spec_version {} and doctor-VALID; nothing to "
                      "upgrade (no-op).".format(_UPGRADE_TO))
                return EXIT_OK
            print("opf upgrade: store declares spec_version {} but is NOT doctor-VALID: a partial or "
                  "interrupted migration is never reported complete (fail-closed, spec 9.2). {} Run "
                  "`opf doctor --root {}` for the findings, exit 2.".format(
                      _UPGRADE_TO, _upgrade_partial_recovery_text(res, manifest_model), root), file=sys.stderr)
            _doctor_report(result)
            return EXIT_MALFORMED
        try:
            sv_tuple = tuple(int(p) for p in sv.split(".")) if isinstance(sv, str) else None
        except ValueError:
            sv_tuple = None
        if sv_tuple is not None and sv_tuple > tuple(int(p) for p in _UPGRADE_TO.split(".")):
            raise _UpgradeError("store declares spec_version {!r} ABOVE the {} this tooling implements; "
                                "a newer store is never downgraded (fail-closed)".format(sv, _UPGRADE_TO))
        minor = sv == _UPGRADE_MID

        # PRECONDITION (spec 9.2): re-emitting the UNCHANGED parsed model reproduces the on-disk bytes
        # exactly, proving the file is canonical and comment-free so the bounded rewrite loses nothing.
        if _opf_emit.emit_checked(manifest_model).encode("utf-8") != manifest_bytes:
            raise _UpgradeError("manifest is not in canonical new-document form (hand-edited or comment-"
                                "bearing); refusing a model rewrite that could lose content (fail-closed)")
        if _opf_emit.emit_checked(counters_model).encode("utf-8") != counters_bytes:
            raise _UpgradeError("counters.toml is not in canonical new-document form; refusing (fail-closed)")

        new_manifest, new_counters, added_ns, origin = (
            _upgrade_plan_minor if minor else _upgrade_plan)(manifest_model, counters_model)
        origin_version = _UPGRADE_MID if minor else _UPGRADE_FROM
        new_manifest_bytes = _opf_emit.emit_checked(new_manifest).encode("utf-8")
        new_counters_bytes = _opf_emit.emit_checked(new_counters).encode("utf-8")

        # STEP 3: derive the scope from this delta and its POST-delta render declarations. The same
        # destinations drive the cleanliness gate, index creation, recovery, and staging advice.
        write_scope = _upgrade_write_scope(
            machine_rel, new_manifest, new_counters_bytes != counters_bytes,
            () if minor else _UPGRADE_NEW_TYPES)
        _opf_write_guard.check_clean(res, write_scope, "upgrade", _UPGRADE_CLEAN_SCOPE)
        product_targets = write_scope["product"]
        created_relpaths = []
        for relpath in write_scope["store"]:
            pfd, name = _opf_store._journal._open_parent(root_fd, relpath)
            try:
                entry = _opf_store._journal._lstat_at(pfd, name)
                if entry is None:
                    created_relpaths.append(relpath)
                elif not stat.S_ISREG(entry.st_mode):
                    raise _UpgradeError("upgrade destination {!r} is not a regular file "
                                        "(fail-closed)".format(relpath))
            finally:
                _opf_store._close_fd_exc_safe(pfd)
        # DISTINCT roots for the recovery/staging advice (R1): `.working` lives under the STORE root, product-
        # scope targets under the PRODUCT root; the two differ for a RELOCATED store.
        recovery_store_root = res.store_root
        recovery_product_root = res.product_root if res.product_root is not None else res.store_root
        absent_product_targets = []
        for relpath in product_targets:
            try:
                os.lstat(os.path.join(str(recovery_product_root), relpath))
            except FileNotFoundError:
                absent_product_targets.append(relpath)
        _opf_write_guard.check_ignored(recovery_store_root, created_relpaths, "upgrade")
        _opf_write_guard.check_ignored(recovery_product_root, absent_product_targets, "upgrade")
        # STEP 4 (M4): claim the single-writer lease atomically, then hold it across mutation, render, and
        # the final doctor; release it in the finally covering every exit after acquisition, EXCEPT the
        # success path releases FIRST (R5) so no success is reported over a still-held / failed-to-release
        # lease. `released` records that the success path already released, so the finally does not re-release.
        lease_payload = _opf_write_guard.acquire_lease(root_fd, machine_rel, "upgrade")
        recovery = (recovery_store_root, recovery_product_root, created_relpaths,
                    product_targets, write_scope["store"])
        released = False
        try:
            # Apply: rewrite manifest + counters (canonical bytes), create the missing empty indexes. The
            # 1.1.0 origin rewrites the manifest alone (its delta is the spec_version bump).
            _upgrade_replace(root_fd, manifest_rel, new_manifest_bytes)
            if new_counters_bytes != counters_bytes:
                _upgrade_replace(root_fd, counters_rel, new_counters_bytes)
            empty_index = _opf_emit.emit_checked(
                {"schema": _opf_schema.SUPPORTED_SCHEMA, "record": []}).encode("utf-8")
            created_indexes = []
            for idx_rel in write_scope["indexes"]:
                if _upgrade_create_index(root_fd, idx_rel, empty_index):
                    created_indexes.append(Path(idx_rel).name[:-len(_opf_check.INDEX_SUFFIX)])

            # Render the declared views (materializes the two new views and re-renders DECISIONS.md), then
            # require a full doctor VALID before offering the uncommitted change. Both run over the mutated
            # (uncommitted) tree, under the held lease (containment-clean; the mid-run doctor stays VALID).
            render_argv = ["--root", root, "--write"]
            try:
                rres = _opf_store.resolve_store(Path(os.path.abspath(root)))
                robs, _notes = _opf_observe.gather(rres) if rres.status == _opf_store.RESOLVED else (None, [])
                rc = _opf_views.render(render_argv, observations=robs)
            except Exception as exc:  # noqa: BLE001  a render escape must not read as a clean upgrade
                raise _UpgradeError("view render after the schema delta failed ({!r}); the uncommitted change is "
                                    "left for review".format(exc))
            if rc != EXIT_OK:
                print("opf upgrade: cannot evaluate: view render after the schema delta did not complete "
                      "cleanly (rc={}); exit 2.".format(rc), file=sys.stderr)
                print(_upgrade_recovery_text(recovery_store_root, recovery_product_root, created_relpaths,
                                             product_targets, write_scope["store"]), file=sys.stderr)
                recovery = None  # Already printed, even if the finally's lease release fails.
                return EXIT_MALFORMED

            result = _upgrade_doctor(root)
            if result.status != _opf_store.VALID:
                print("opf upgrade: the upgraded store is NOT doctor-VALID; refusing to offer the change "
                      "(fail-closed, spec 9.2). Run `opf doctor --root {}` for the findings, exit 2.".format(
                          root), file=sys.stderr)
                print(_upgrade_recovery_text(recovery_store_root, recovery_product_root, created_relpaths,
                                             product_targets, write_scope["store"]), file=sys.stderr)
                recovery = None  # Already printed, even if the finally's lease release fails.
                _doctor_report(result)
                return EXIT_MALFORMED

            # R5: release the single-writer lease BEFORE emitting the success report. A release failure
            # surfaces as exit 2 (never swallowed), and success is never printed over a still-held lease.
            # `released` is set FIRST so the finally never double-releases (a failed release legitimately
            # leaves the lease as a spec-conformant leftover for operator reconciliation).
            released = True
            # Doctor has validated the payload. A failed release can mean another holder is live;
            # automatic rollback advice is unsafe and is not required to make this payload valid.
            recovery = None
            try:
                _opf_write_guard.release_lease(root_fd, machine_rel, lease_payload, "upgrade")
            except BaseException:
                print("opf upgrade: the store reached doctor-VALID before lease release, but release "
                      "failed. Confirm no opf run is live (spec 5.7) and reconcile the lease before "
                      "any further action; no restore/removal commands are offered.", file=sys.stderr)
                raise
            print("opf upgrade: store schema upgraded {} -> {} and doctor-VALID (uncommitted, NOT staged or committed)."
                  .format(origin_version, _UPGRADE_TO))
            print(json.dumps({
                "event": "upgraded", "root": str(root), "from": origin_version, "to": _UPGRADE_TO,
                "created_indexes": sorted(created_indexes), "added_counters": sorted(added_ns),
                "pre_declared_types": sorted(origin["pre_declared"]),
                "decisions_view": "unchanged" if minor else (
                    "widened" if origin["decisions_declared"] else "not-declared")},
                sort_keys=True))
            print("opf upgrade: regenerated views reflect working-tree store content, including uncommitted "
                  "source edits outside the cleanliness scope. Before committing, review those edits and "
                  "commit the intended sources with their views, or set them aside and regenerate the views.")
            print("opf upgrade: review the uncommitted changes, then stage and commit the planned destinations "
                  "(never `add -A`, which would sweep in unrelated work). The commands use -f for "
                  "these named destinations because the safety probes neutralize global/system config "
                  "and core.excludesFile (including default HOME/XDG global ignores). They read "
                  "working-tree .gitignore files and .git/info/exclude; the absent-path probe does "
                  "not read indexed ignore rules:")
            print("  git -C {} --literal-pathspecs add -f -- {}".format(
                shlex.quote(str(recovery_store_root)),
                " ".join(shlex.quote(p) for p in write_scope["store"])))
            for _pt in product_targets:
                print("  git -C {} --literal-pathspecs add -f -- {}".format(
                    shlex.quote(str(recovery_product_root)), shlex.quote(_pt)))
            print("opf upgrade: exit 0 means the store is valid at {}; committing is the adopter's own "
                  "step.".format(_UPGRADE_TO))
            return EXIT_OK
        finally:
            if not released:
                # R5/FIX1: release the lease on every non-success exit. When a mid-run failure is ALREADY
                # propagating (a render/doctor escape after the manifest+counters were rewritten, which
                # carries its own "uncommitted change is left for review" recovery advice) and the release then
                # ALSO fails (its lease-replaced never-seize WriteGuardError), the release error must NOT
                # DISPLACE that original exception: the operator still needs the mid-run recovery advice, so
                # the lease-replaced note is surfaced ALONGSIDE it, never in place of it (exit 2 preserved,
                # peer lease left, never seized). A propagating KeyboardInterrupt/SystemExit is likewise not
                # masked by a release failure.
                pending = sys.exc_info()[1]
                if pending is None:
                    # A `return` (or normal fall-through) is passing through with no in-flight exception: a
                    # release failure legitimately becomes the surfaced outcome (exit 2), exactly as before.
                    _opf_write_guard.release_lease(root_fd, machine_rel, lease_payload, "upgrade")
                else:
                    try:
                        _opf_write_guard.release_lease(root_fd, machine_rel, lease_payload, "upgrade")
                    except (KeyboardInterrupt, SystemExit):
                        raise
                    except Exception as rel_exc:  # noqa: BLE001  surfaced, never displaces the original
                        print("opf upgrade: additionally, releasing the upgrade lease failed ({}); the peer "
                              "lease is LEFT in place (never seized, spec 5.7) and the original failure above "
                              "still governs (exit 2).".format(rel_exc), file=sys.stderr)
                        # returning from the except lets `pending` resume propagating (the finally completes
                        # without raising a new exception), so _cmd_upgrade surfaces the original refusal.
    except BaseException:
        # Cover every escape after acquisition, including writes, render/doctor and lease release.
        # Preserve the original exception and the existing never-seize release handling.
        if recovery is not None:
            print(_upgrade_recovery_text(*recovery), file=sys.stderr)
        raise
    finally:
        _opf_store._close_fd_exc_safe(root_fd)


# Spec 14.1: the former --scan, --plan, --review and --apply modes refuse with a pointer to adoption and the
# prompt pack. The pointer is in words only; it names no command this build lacks.
IMPORT_RETIRED = (
    "the ordinary import modes (scan, plan, review and apply) are retired: clean-start adoption (OPF spec "
    "14.1) is the only intake, and post-adoption import uses the approved prompt pack; nothing was written")


def _cmd_import(rest):
    """`opf import ...`: the retired store import verb. Its former modes were `opf import [--root DIR]
    (--scan --set FILE | --plan --set FILE | --review <run-id> --actor NAME (--decisions FILE |
    --interactive) | --apply <run-id>)`, with the root-ingest planner's companions and the review aids.

    RETIRED (spec 14.1), its engine removed: the verb prints IMPORT_RETIRED and exits 2 for EVERY argument
    list, whether none, `--help`, any former form (spelled in full, by prefix, joined or repeated) or any
    other token. No argument is parsed or validated, so no former form can meet a usage error instead of
    the pointer. Nothing reads the clock, stdin, a named file or the store, and nothing is written. The
    root is never resolved, so an unresolved / NOT-ADOPTED root refuses the same way (D7: import is a
    REQUESTED operation, so its refusal is a cannot-evaluate, never the NOT-APPLICABLE exit 0 that
    doctor/render/upgrade report on a non-adopter root).

    The CLI self-test checks this two ways. The verb's runtime behaviour, exit 2 with the pointer for
    every representative argument list, is what the RUNTIME probe observes; the STRUCTURAL checks are a
    tripwire against an accidental regression of this function, not a proof of its behaviour. Over this
    function's source as parsed from the file's bytes, decoded as the interpreter decodes them, they flag:
    a coding declaration other than utf-8 on line 1 or 2; a body after this docstring other than exactly
    the one print of the pointer to stderr and the one return of EXIT_MALFORMED, a reference to `rest`
    or any other call included; a binding of print, _cmd_import, sys, IMPORT_RETIRED, EXIT_MALFORMED or
    __builtins__ by a form the binding scan models, in any expression evaluated at module scope, a
    `global` of one of them, or a star import; and sys, IMPORT_RETIRED or EXIT_MALFORMED not bound by
    one direct top-level statement ahead of this def (one nested in an if, try, with, loop, match or
    function, or one after the def) (all _import_body_findings); a live `_cmd_import.__code__` other
    than the code compiled from that parsed definition (_import_code_findings); and a live function
    whose globals are not this module's namespace or whose builtins are not the interpreter's real
    builtins mapping (_import_namespace_findings). They do not cover reflective or dynamic changes
    (stores through globals() or vars(), setattr and attribute stores such as builtins.print or
    sys.stderr, exec, eval or compile, importlib), source-decoding and loader tricks beyond the
    coding-declaration check, this module's own import-time top-level code (which runs on every `opf
    import` before main() and which the runtime probe does not cover either, as it calls main() on the
    already-imported module), the _bootstrap() (the guarded _opf_* helper import) and dispatch main()
    runs before `return _cmd_import(rest)` (covered only by the runtime probe), calls through routes the
    probe does not patch (posix, _io, ctypes, already-open file objects or descriptors, a socket), or
    another module patching this one. The runtime probe covers only a representative set of argument
    lists: while each runs, a call to any probed filesystem read or write, process-spawn or stdin
    function through a module attribute the probe patches is recorded and refused, and the row fails; a
    route it does not patch is outside it."""
    # `rest` is deliberately never read: every argument list meets the pointer.
    print("opf import: {}".format(IMPORT_RETIRED), file=sys.stderr)
    return EXIT_MALFORMED


def _parse_unoptimized(source):
    """ast.parse(source) unoptimized on every supported Python (3.11+): optimize=0 where ast.parse has
    that parameter (3.13+), plain ast.parse before, which is equivalent because before 3.13 ast.parse
    never runs the AST optimizer, so it strips no docstring even under python -O or -OO. A bytes
    `source` is decoded as the interpreter decodes a source file, honouring a PEP 263 coding
    declaration on line 1 or 2. It delegates to _optlevel.parse, the one shared source of this policy."""
    import _optlevel

    return _optlevel.parse(source)


# The exact body _cmd_import must have after its docstring, compared by AST shape (no line numbers).
_IMPORT_BODY = ('print("opf import: {}".format(IMPORT_RETIRED), file=sys.stderr)\n'
                "return EXIT_MALFORMED\n")


def _import_body_findings(source):
    """The structural tripwire for the retired import verb over the module source `source` (the
    BYTES of opf.py itself in the CLI self-test; a str is parsed as given). Returns a list of findings,
    empty when clean; a clean result is no proof of the verb's behaviour, which the runtime probe
    observes. Bytes are parsed as the interpreter decodes the file, honouring a PEP 263 coding
    declaration, so the checked text is decoded as the interpreter decodes it; and any coding declaration
    other than utf-8 on line 1 or 2 is itself a finding (an absent one means utf-8), so a declaration that
    decodes a comment into a statement (raw_unicode_escape turns a backslash-u000a escape into a
    newline) is flagged both ways. It parses the source and flags a module that does not define
    `_cmd_import` once, undecorated, taking only `rest`; a body after the docstring other than exactly
    _IMPORT_BODY (one print of the pointer to sys.stderr, one return of EXIT_MALFORMED), any other
    statement, a reference to `rest` or a call other than that print and its str.format included; and
    a modeled module-level binding that changes a name the body uses (`sys` bound other than by one
    `import sys`, EXIT_MALFORMED other than to the literal 2, IMPORT_RETIRED other than to a string
    literal, `print` or `_cmd_import` rebound, `__builtins__` bound (a binding ahead of the def changes
    what print resolves to inside _cmd_import without binding print), a `global` of any of them, a star
    import, or sys, IMPORT_RETIRED or EXIT_MALFORMED not bound by one direct top-level statement ahead
    of the def). The binding scan walks every
    expression evaluated at module scope, the decorators, argument defaults, annotations, class bases
    and class keywords of a def or class included; inside a lambda or a comprehension any store to a
    watched name counts (a walrus there, a comprehension's loop target or a walrus in a lambda body
    alike), an over-approximation. It models exactly the binding forms its walk enumerates and NO
    MORE: every other change, the reflective or dynamic ones included, is in the residual class named in
    _cmd_import's docstring, not flagged here. The source is parsed unoptimized (no AST optimizer), so the
    docstring the body check skips survives under python -O and -OO. It reads no file."""
    import ast
    import codecs
    import re

    # A coding declaration other than utf-8 on line 1 or 2. Lines are split both at b"\n" (the
    # tokenizer's split) and by bytes.splitlines, and every `coding[:=]` on such a line holding a `#`
    # counts, an over-approximation of the tokenizer's first-match rule.
    findings = []
    raw = source if isinstance(source, bytes) else source.encode("utf-8", "surrogatepass")
    for lineno, line in list(enumerate(raw.split(b"\n")[:2], 1)) + list(enumerate(raw.splitlines()[:2], 1)):
        if b"#" not in line:
            continue
        for m in re.finditer(rb"coding[:=][ \t]*([-\w.]*)", line):
            try:
                name = codecs.lookup(m.group(1).decode("ascii")).name
            except (LookupError, UnicodeDecodeError):
                name = None
            finding = ("line {} carries a coding declaration other than utf-8 ({!r}), so the interpreter "
                       "decodes the file differently".format(lineno, m.group(1).decode("latin-1")))
            if name != "utf-8" and finding not in findings:
                findings.append(finding)
    try:
        tree = _parse_unoptimized(source)
    except (SyntaxError, ValueError) as exc:
        return findings + ["the source does not parse ({})".format(exc)]
    defs = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            and n.name == "_cmd_import"]
    if len(defs) != 1:
        return findings + ["the module defines _cmd_import {} times at module level (want once)".format(
            len(defs))]
    fn = defs[0]

    # Module-level bindings of the watched names. A nested function or class body is its own scope, but
    # its name binds here, and its decorators, argument defaults, annotations, bases and keywords are
    # evaluated here. A walrus inside a comprehension binds here too, and a lambda's defaults are
    # evaluated here; inside a lambda or comprehension every store to a watched name counts
    # (over-approximation: a comprehension's own loop target or a walrus in a lambda body is flagged
    # although it binds only in that inner scope).
    bindings = {name: [] for name in ("_cmd_import", "print", "sys", "EXIT_MALFORMED", "IMPORT_RETIRED",
                                      "__builtins__")}
    scoped = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
    own_scope = (ast.Lambda, ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)
    pending = list(tree.body)
    while pending:
        node = pending.pop()
        if isinstance(node, scoped):
            if node.name in bindings:
                bindings[node.name].append(node)
            pending.extend(node.decorator_list)
            if isinstance(node, ast.ClassDef):
                pending.extend(node.bases)
                pending.extend(node.keywords)
            else:
                a = node.args
                pending.extend(a.defaults)
                pending.extend(d for d in a.kw_defaults if d is not None)
                params = a.posonlyargs + a.args + a.kwonlyargs + [a.vararg, a.kwarg]
                pending.extend(p.annotation for p in params if p is not None and p.annotation is not None)
                if node.returns is not None:
                    pending.append(node.returns)
            pending.extend(getattr(node, "type_params", ()))
            continue
        if isinstance(node, own_scope):
            for sub in ast.walk(node):
                if isinstance(sub, ast.Name) and sub.id in bindings and not isinstance(sub.ctx, ast.Load):
                    bindings[sub.id].append(sub)
            continue
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                if alias.name == "*":
                    findings.append("a star import at line {} can rebind any name".format(node.lineno))
                bound = alias.asname or alias.name.split(".")[0]
                if bound in bindings:
                    bindings[bound].append(node)
        if isinstance(node, ast.Name) and node.id in bindings and not isinstance(node.ctx, ast.Load):
            bindings[node.id].append(node)
        if isinstance(node, (ast.ExceptHandler, ast.MatchAs, ast.MatchStar)) and node.name in bindings:
            bindings[node.name].append(node)
        if isinstance(node, ast.MatchMapping) and node.rest in bindings:
            bindings[node.rest].append(node)
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            # The Assign stands for its plain single-name target, so the literal check can see the value.
            if node.targets[0].id in bindings:
                bindings[node.targets[0].id].append(node)
                pending.append(node.value)
                continue
        pending.extend(ast.iter_child_nodes(node))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Global, ast.Nonlocal)):
            for name in node.names:
                if name in bindings:
                    findings.append("line {} declares {} global or nonlocal".format(node.lineno, name))
    if bindings["_cmd_import"] != [fn]:
        findings.append("_cmd_import is bound {} times at module level (want only its def)".format(
            len(bindings["_cmd_import"])))
    if bindings["print"]:
        findings.append("print is rebound at module level")
    if bindings["__builtins__"]:
        findings.append("__builtins__ is bound at module level (it changes what print resolves to)")
    sys_nodes = bindings["sys"]
    if not (len(sys_nodes) == 1 and isinstance(sys_nodes[0], ast.Import)
            and [(a.name, a.asname) for a in sys_nodes[0].names] == [("sys", None)]):
        findings.append("sys is not bound by exactly one plain `import sys` at module level")
    for name, ok in (("EXIT_MALFORMED", lambda v: type(v) is int and v == 2),
                     ("IMPORT_RETIRED", lambda v: type(v) is str)):
        nodes = bindings[name]
        if not (len(nodes) == 1 and isinstance(nodes[0], ast.Assign)
                and isinstance(nodes[0].value, ast.Constant) and ok(nodes[0].value.value)):
            findings.append("{} is not bound exactly once at module level to its literal".format(name))
    # Each watched binding must be a direct top-level statement ahead of the def: one nested in an if,
    # try, with, loop, match or function (a condition on sys.argv or the environment), or one after the
    # def, can leave the name unbound when the body reads it (a NameError, never the pointer).
    ahead = tree.body[:[i for i, n in enumerate(tree.body) if n is fn][0]]
    for name in ("sys", "EXIT_MALFORMED", "IMPORT_RETIRED"):
        if len(bindings[name]) == 1 and not any(n is bindings[name][0] for n in ahead):
            findings.append("{} is not bound by a direct top-level statement ahead of the def".format(name))

    if isinstance(fn, ast.AsyncFunctionDef) or fn.decorator_list or fn.returns is not None:
        findings.append("_cmd_import is async, decorated or annotated")
    a = fn.args
    if (a.posonlyargs or [p.arg for p in a.args] != ["rest"] or a.vararg or a.kwonlyargs or a.kwarg
            or a.defaults or a.kw_defaults or a.args[0].annotation is not None):
        findings.append("_cmd_import's signature is not exactly (rest)")
    body = fn.body
    if (body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)):
        body = body[1:]
    else:
        findings.append("_cmd_import has no docstring as its first statement")
    for node in body:
        for sub in ast.walk(node):
            if isinstance(sub, ast.Name) and sub.id == "rest":
                findings.append("_cmd_import references rest at line {}".format(sub.lineno))
    calls = [sub for node in body for sub in ast.walk(node) if isinstance(sub, ast.Call)]
    want = _parse_unoptimized(_IMPORT_BODY).body
    if len(body) != len(want):
        findings.append("_cmd_import has {} statements after its docstring (want {})".format(
            len(body), len(want)))
    if len(calls) != 2:
        findings.append("_cmd_import makes {} calls (want 2: the print and its str.format)".format(
            len(calls)))
    if [ast.dump(n) for n in body] != [ast.dump(n) for n in want]:
        findings.append("_cmd_import's body after its docstring is not exactly: {}".format(
            " ; ".join(_IMPORT_BODY.splitlines())))
    return findings


def _import_code_findings(source, func, filename):
    """The live-code tie for the retired import verb: compile the `_cmd_import` definition parsed from
    the module source `source` (parsed unoptimized; bytes are decoded as the interpreter decodes the
    file, honouring its coding declaration) on its own, under `filename` and at its own
    line numbers, with the source's __future__ flags only and at the interpreter's own optimization
    level (sys.flags.optimize, so under python -O or -OO both sides drop the same docstring), and
    require the function `func` (the live _cmd_import in the CLI self-test) to carry exactly that
    code: equal co_code, co_consts (compared by type and repr, nested
    code objects recursively under these same fields), co_names, co_varnames, co_freevars, co_cellvars,
    argument counts, co_flags, co_firstlineno, co_name, co_filename and co_exceptiontable, and no
    argument defaults. Returns a list of findings, empty when they match. Matching names alone never
    passes: a swapped `__code__` that differs in any of these fields is a finding. It reads no file."""
    import __future__
    import ast
    import types

    try:
        tree = _parse_unoptimized(source)
    except (SyntaxError, ValueError) as exc:
        return ["the source does not parse ({})".format(exc)]
    defs = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_cmd_import"]
    if len(defs) != 1:
        return ["the module defines _cmd_import {} times at module level (want once)".format(len(defs))]
    flags = 0
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module == "__future__":
            for alias in node.names:
                flags |= getattr(getattr(__future__, alias.name, None), "compiler_flag", 0)
    try:
        module_code = compile(ast.Module(body=defs, type_ignores=[]), filename, "exec", flags=flags,
                              dont_inherit=True, optimize=sys.flags.optimize)
    except (SyntaxError, ValueError) as exc:
        return ["the parsed _cmd_import does not compile ({})".format(exc)]
    want = [c for c in module_code.co_consts if isinstance(c, types.CodeType) and c.co_name == "_cmd_import"]
    live = getattr(func, "__code__", None)
    if len(want) != 1 or not isinstance(live, types.CodeType):
        return ["the live _cmd_import has no code object to compare with the parsed definition"]
    fields = ("co_code", "co_names", "co_varnames", "co_freevars", "co_cellvars", "co_argcount",
              "co_posonlyargcount", "co_kwonlyargcount", "co_flags", "co_firstlineno", "co_name",
              "co_filename", "co_exceptiontable")

    def shape(const):
        if isinstance(const, types.CodeType):
            return ("code",) + tuple(getattr(const, f, None) for f in fields) + (
                tuple(shape(c) for c in const.co_consts),)
        if isinstance(const, tuple):
            return ("tuple", tuple(shape(c) for c in const))
        if isinstance(const, frozenset):
            return ("frozenset", tuple(sorted(repr(shape(c)) for c in const)))
        return (type(const).__name__, repr(const))

    findings = []
    for f in fields:
        if getattr(live, f, None) != getattr(want[0], f, None):
            findings.append("the live _cmd_import's {} differs from the parsed definition's".format(f))
    if shape(live.co_consts) != shape(want[0].co_consts):
        findings.append("the live _cmd_import's co_consts differ from the parsed definition's")
    if getattr(func, "__defaults__", None) is not None or getattr(func, "__kwdefaults__", None) is not None:
        findings.append("the live _cmd_import carries argument defaults the parsed definition lacks")
    return findings


def _import_namespace_findings(func):
    """The live-namespace tie for the retired import verb: require the function `func` (the live
    _cmd_import in the CLI self-test) to resolve its global names through its own module's namespace
    (`func.__globals__ is sys.modules[func.__module__].__dict__`) and its builtins through the
    interpreter's real builtins mapping (`func.__builtins__ is builtins.__dict__`). A module-level
    `__builtins__` binding ahead of the def (a dict, a copy of the builtins with print replaced, or an
    imported module) changes what print resolves to inside the function without binding print, and
    neither the binding scan of print nor the code tie sees it; this check flags the function it
    produced. Returns a list of findings, empty when both hold. Reassigning an attribute of the real
    builtins module (builtins.print) leaves both identities intact and stays a DISCLOSED residual
    class. It reads no file."""
    import builtins

    findings = []
    if getattr(func, "__builtins__", None) is not builtins.__dict__:
        findings.append("the live _cmd_import's __builtins__ is not the interpreter's builtins mapping")
    module = sys.modules.get(getattr(func, "__module__", None))
    if module is None or getattr(func, "__globals__", None) is not getattr(module, "__dict__", None):
        findings.append("the live _cmd_import's __globals__ is not its module's namespace")
    return findings


def _cmd_record(rest):
    """`opf record <subcommand> ...` (spec 8.8): the record-authoring verb. `create`, `transition`,
    `done-with-receipt`, and `worklog-append` run the shared journaled operation sequence in _opf_record
    (byte-reproduction precondition, one id claim through the allocation seam, allowed-delta postcondition,
    cleanliness gate and lease, one journaled publication, render, final doctor VALID, lease release before
    the report). The final doctor's one exception is a status change (transition or done-with-receipt),
    which may leave only doctor's cannot-evaluate for exactly that record and from/to pair, never a finding;
    doctor keeps reporting it until the change is committed. Exit 0 recorded (left uncommitted), exit 2
    every refusal or cannot-evaluate; exit 1 is not used. Unlike the applicability-probe siblings a
    NOT-ADOPTED root is a cannot-evaluate (a requested operation, like import)."""
    try:
        return _opf_record.cli(rest)
    except Exception as exc:  # noqa: BLE001  class-width fail-closed backstop, never a false success
        print("opf record: cannot evaluate: unexpected error ({!r}); failing closed to exit 2".format(exc),
              file=sys.stderr)
        return EXIT_MALFORMED


def _adopt_exit(status):
    """Map a planning-layer status (an _opf_adopt_plan.AdoptResult or _opf_adopt.AdoptValidation carries
    the _opf_store VALID / INVALID / CANNOT-EVALUATE vocabulary) to the CLI 0/1/2 exit contract,
    fail-closed: a status outside the vocabulary (a first-party contract violation) is exit 2, never a
    false clean."""
    if status == _opf_store.VALID:
        return EXIT_OK
    if status == _opf_store.INVALID:
        return EXIT_FINDING
    return EXIT_MALFORMED   # CANNOT-EVALUATE, or any unexpected value, fails closed


def _adopt_read_inputs(path):
    """Read the `--inputs` adoption planning worksheet (a TOML file) for `opf adopt plan`, fail-closed.
    The worksheet is CALLER input, not a store artefact, so it may live outside the store and is read
    directly; a missing, unreadable, or malformed worksheet is a ValueError (the caller maps it to a
    cannot-evaluate exit 2, never a silent nothing-to-do): an input it cannot read or parse refuses.
    Shape: `schema = 1`, `product`, `expected_observation_digest`, a `bindings` table, and
    the optional `sources` / `targets` (arrays of relative path strings) and `decisions` / `ops` (arrays
    of tables). This reader validates STRUCTURE only, as a closed keyset; the planner and the schema
    layer own the SEMANTICS (_opf_adopt_plan.plan: the digest grammar and inventory binding, the exact
    _opf_adopt.PLAN_BINDING_INPUTS bindings keyset, per-row decision and op validation, the frozen-plan
    validation), never duplicated here. The read is BOUNDED, the planner's own read discipline: a
    no-follow, nonblocking open (every component is walked from the filesystem root with O_NOFOLLOW, the
    store root's _opf_store._open_dir_nofollow walk, so a symlinked parent, ancestor or final component
    refuses; and a FIFO returns at once rather than blocking), a SINGLY-LINKED (st_nlink == 1) regular file
    only (a hardlinked worksheet is a second name for another inode, refused class-consistent with the
    engine's own single-link control reads), and the planner's per-file byte bound
    (_opf_adopt_plan.MAX_FILE_BYTES) checked on the opened fd and again by the journal's capped reader, so
    a FIFO, device, directory, symlink, multiply-linked or oversized worksheet refuses. Returns the parsed
    table with the
    optional keys defaulted."""
    import tomllib
    cap = _opf_adopt_plan.MAX_FILE_BYTES
    try:
        parent, name = os.path.split(path if os.path.isabs(path) else os.path.join(os.getcwd(), path))
        pfd = _opf_store._open_dir_nofollow(parent)
        fd = None
        try:
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=pfd)
        finally:
            try:
                _opf_store._close_fd_exc_safe(pfd)
            except OSError:
                # A failing parent close must not leak the just-opened worksheet fd (round-5 defect 2)
                # or the parent fd itself (P1, #378): the parent close is _close_fd_exc_safe's single
                # os.close (#377) and the worksheet close the journal engine's, and close(2) has released
                # the number when it reports the error, so it is never touched again; the propagating
                # error still fails the read closed below.
                if fd is not None:
                    _opf_adopt_apply._journal._close_fd_quietly(fd)
                raise
    except FileNotFoundError:
        raise ValueError("--inputs worksheet not found: {}".format(path))
    except (OSError, ValueError) as exc:   # ELOOP/ENOTDIR: a symlinked worksheet or ancestor, never followed
        raise ValueError("--inputs worksheet unreadable ({}): {}".format(path, exc))
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise ValueError("--inputs worksheet is not a regular file (a FIFO, device or directory is "
                             "refused): {}".format(path))
        if st.st_nlink != 1:
            raise ValueError("--inputs worksheet has {} hard links; a multiply-linked worksheet (a "
                             "hardlink whose other name may be an out-of-tree victim) is refused, "
                             "class-consistent with the engine's singly-linked control reads: "
                             "{}".format(st.st_nlink, path))
        if st.st_size > cap:
            raise ValueError("--inputs worksheet exceeds the {}-byte bound: {}".format(cap, path))
        raw = _opf_adopt_apply._journal._read_fd(fd, cap=cap)
    except (OSError, _opf_adopt_apply._journal.JournalError) as exc:
        raise ValueError("--inputs worksheet unreadable ({}): {}".format(path, exc))
    finally:
        _opf_store._close_fd_exc_safe(fd)
    try:
        doc = tomllib.loads(raw.decode("utf-8"))
    except (ValueError, RecursionError) as exc:   # UnicodeDecodeError and TOMLDecodeError are ValueErrors
        raise ValueError("--inputs worksheet unreadable or malformed ({}): {}".format(path, exc))
    if not (isinstance(doc, dict) and type(doc.get("schema")) is int and doc.get("schema") == 1):
        raise ValueError("--inputs worksheet must be a TOML table carrying `schema = 1` (an integer 1, "
                         "not a bool or float)")
    allowed = frozenset(("schema", "product", "expected_observation_digest", "sources", "targets",
                         "decisions", "ops", "bindings"))
    extra = set(doc) - allowed
    if extra:
        raise ValueError("--inputs worksheet carries unknown key(s): {} (the worksheet keyset is closed: "
                         "schema, product, expected_observation_digest, sources, targets, decisions, "
                         "ops, bindings)".format(", ".join(sorted(extra))))
    if not (isinstance(doc.get("product"), str) and doc["product"]):
        raise ValueError("--inputs worksheet `product` must be a non-empty string")
    if not (isinstance(doc.get("expected_observation_digest"), str)
            and doc["expected_observation_digest"]):
        raise ValueError("--inputs worksheet `expected_observation_digest` must be a non-empty string")
    if not isinstance(doc.get("bindings"), dict):
        raise ValueError("--inputs worksheet `bindings` must be a table (the plan-v2 binding inputs)")
    for key in ("sources", "targets"):
        rows = doc.get(key, [])
        if not (isinstance(rows, list) and all(isinstance(s, str) and s for s in rows)):
            raise ValueError("--inputs worksheet `{}` must be an array of non-empty path strings when "
                             "present".format(key))
        doc[key] = rows
    for key in ("decisions", "ops"):
        rows = doc.get(key, [])
        if not (isinstance(rows, list) and all(isinstance(r, dict) for r in rows)):
            raise ValueError("--inputs worksheet `{}` must be an array of tables when present".format(key))
        doc[key] = rows
    return doc


def _cmd_adopt(rest):
    """`opf adopt <subcommand> ...` (spec 1, 14; OPF-ADOPT K9a): the adoption verb. The subcommand
    vocabulary is `plan`, `approve`, `apply`, `complete`, `reconcile` and `status`; K9a ships ONLY the
    two READ-ONLY subcommands, and every other recognized subcommand refuses fail-closed (exit 2) until
    the mutating adoption engine lands in a later PR, so a stub can never read as a passing operation.

      plan --inputs FILE [--root DIR] : freeze and PRINT the inert adoption proposal through the
          existing planner (_opf_adopt_plan.plan), writing NOTHING. FILE is the caller planning
          worksheet (see _adopt_read_inputs). `now` is read from the clock (timestamp-from-clock) and
          `run_nonce` from os.urandom, both injected into the planner (the deterministic run id composes
          them). Exit 0: VALID -- the frozen plan TOML on stdout (an inert, digest-bound PROPOSAL, never
          permission or readiness to apply; the approval lives in the run evidence, a later PR).
          Exit 1: INVALID (a schema-violating decision, op or plan). Exit 2: cannot-evaluate (an
          unreadable worksheet, a changed inventory, an unresolvable root, an unresolved disposition).
      status [--root DIR] : report the adoption state READ-ONLY, writing nothing: the adoption evidence
          bundles (the _opf_store adoption evidence home, each graded by the engine's own bundle
          validator) and the adoption journal (_opf_adopt_apply.JOURNAL_REL, classified by the engine's
          journal_state), both read contained and no-follow. Exit 0: clean (each verified run listed, or
          "no adoption run exists"). Exit 1: a finding (an open transaction or a held lock in the
          journal, a bundle the validator grades INVALID, or a directory at the evidence home that is not
          a run id). Exit 2: cannot-evaluate (a symlinked, dangling or wrong-type root or home, evidence-
          home entry or journal entry -- a journal entry other than a transaction directory or a
          regular, singly-linked `lock` / `lock.break` -- a root reached through a symlink, `..` included, or an unreadable
          journal or bundle). Like
          `plan`, a NOT-ADOPTED root is fine: adoption is the verb that PRECEDES a store, so neither
          subcommand requires store resolution (unlike import D7).

    The parser is the house fail-closed idiom: an unknown subcommand or token, an empty or
    option-looking or duplicate value -> exit 2. Every residual escape fails closed to exit 2 (never a
    false 0 or an uncaught exit-1), the same class-width backstop render and doctor carry."""
    subcommands = ("plan", "approve", "apply", "complete", "reconcile", "status")
    deferred = ("approve", "apply", "complete", "reconcile")
    if not rest:
        print("opf adopt: give a subcommand ({})".format(" / ".join(subcommands)), file=sys.stderr)
        return EXIT_MALFORMED
    sub, tail = rest[0], rest[1:]
    if sub in deferred:
        # Refused BEFORE any parse or filesystem read, so a deferred subcommand can never write; the
        # message names no command that does not exist (the K9b engine lands these).
        print("opf adopt {}: not yet available in this build (fail-closed); the mutating adoption engine "
              "lands in a later PR".format(sub), file=sys.stderr)
        return EXIT_MALFORMED
    if sub not in subcommands:
        print("opf adopt: unknown subcommand {!r}; subcommands: {}".format(
            sub, ", ".join(subcommands)), file=sys.stderr)
        return EXIT_MALFORMED

    root = None
    inputs_file = None

    def _need_value(flag, idx):
        if idx + 1 >= len(tail):
            print("opf adopt {}: {} requires an argument".format(sub, flag), file=sys.stderr)
            return None
        val = tail[idx + 1]
        if val == "" or val.startswith("-"):
            print("opf adopt {}: {} requires a non-empty argument, not {!r}".format(sub, flag, val),
                  file=sys.stderr)
            return None
        return val

    i = 0
    while i < len(tail):
        tok = tail[i]
        if tok == "--root":
            if root is not None:
                print("opf adopt {}: --root given more than once".format(sub), file=sys.stderr)
                return EXIT_MALFORMED
            val = _need_value(tok, i)
            if val is None:
                return EXIT_MALFORMED
            root = val
            i += 2
        elif tok == "--inputs" and sub == "plan":
            if inputs_file is not None:
                print("opf adopt plan: --inputs given more than once", file=sys.stderr)
                return EXIT_MALFORMED
            val = _need_value(tok, i)
            if val is None:
                return EXIT_MALFORMED
            inputs_file = val
            i += 2
        else:
            print("opf adopt {}: unrecognized argument {!r}".format(sub, tok), file=sys.stderr)
            return EXIT_MALFORMED
    if sub == "plan" and inputs_file is None:
        print("opf adopt plan: --inputs FILE is required (the planning worksheet)", file=sys.stderr)
        return EXIT_MALFORMED
    try:
        # The EFFECTIVE root is what the operator's traversal names: the --root value, or the current
        # directory when --root is omitted. Every refusal below names it (never the absent --root value).
        effective = root if root is not None else os.getcwd()
        root_abs = os.path.abspath(effective)
        # Two guard layers, both cannot-evaluate (exit 2). Layer 1: abspath collapses `..` LEXICALLY,
        # but the kernel resolves `DIR/link/..` to link's target's parent: where the physical resolution
        # differs from the lexical one's, a `..` crossed a symlink, so refuse rather than evaluate a
        # directory the operator did not name.
        physical = os.path.realpath(effective)
        lexical = os.path.realpath(root_abs)
    except (OSError, ValueError) as exc:   # e.g. a deleted current directory: cannot-evaluate, never exit 1
        print("opf adopt {}: cannot evaluate: cannot resolve the product root ({})".format(sub, exc),
              file=sys.stderr)
        return EXIT_MALFORMED
    if physical != lexical:
        print("opf adopt {}: cannot evaluate: the product root {!r} resolves physically to {!r}, not "
              "{!r} (a `..` after a symlink); a symlinked root or ancestor refuses".format(
                  sub, effective, physical, root_abs), file=sys.stderr)
        return EXIT_MALFORMED
    # Layer 2 (K9a round 3): the realpath comparison alone still ADMITS a `..` whose crossing lands back
    # on the collapsed path (R/link/.. with the link resolving inside R) and a `..` after a component
    # that does not exist (R/missing/..), so VALIDATE the ORIGINAL traversal too: every `..` must cross
    # a REAL directory -- present, and neither a symlink nor a non-directory -- or the root refuses.
    # A `..` through a real directory still works.
    at = os.sep
    try:
        walked = effective if os.path.isabs(effective) else os.path.join(os.getcwd(), effective)
        for comp in walked.split(os.sep):
            if comp in ("", "."):
                continue
            if comp != "..":
                at = os.path.join(at, comp)
                continue
            try:
                crossed = os.lstat(at)
            except (OSError, ValueError) as exc:
                print("opf adopt {}: cannot evaluate: the product root {!r} crosses `..` out of {!r}, "
                      "which cannot be read as a real directory ({}); a `..` may cross only a real "
                      "directory".format(sub, effective, at, exc), file=sys.stderr)
                return EXIT_MALFORMED
            if not stat.S_ISDIR(crossed.st_mode):
                print("opf adopt {}: cannot evaluate: the product root {!r} crosses `..` out of {!r}, "
                      "which is a symlink or not a directory; a `..` may cross only a real "
                      "directory".format(sub, effective, at), file=sys.stderr)
                return EXIT_MALFORMED
            at = os.path.dirname(at.rstrip(os.sep)) or os.sep
    except (OSError, ValueError) as exc:   # e.g. a deleted current directory while joining a relative root
        print("opf adopt {}: cannot evaluate: cannot resolve the product root ({})".format(sub, exc),
              file=sys.stderr)
        return EXIT_MALFORMED

    if sub == "plan":
        import datetime
        try:
            doc = _adopt_read_inputs(inputs_file)
            now = datetime.datetime.now(datetime.timezone.utc)
            res = _opf_adopt_plan.plan(
                root_abs, sources=doc["sources"], targets=doc["targets"],
                expected_observation_digest=doc["expected_observation_digest"],
                product=doc["product"], decisions=doc["decisions"], ops=doc["ops"],
                now=now, run_nonce=os.urandom(8).hex(), bindings=doc["bindings"])
            if res.status == _opf_store.VALID:
                sys.stdout.write(res.plan.decode("utf-8"))
                return EXIT_OK
            for f in res.findings:
                print("opf adopt plan: {}".format(f), file=sys.stderr)
            for source in res.unresolved:
                print("opf adopt plan: unresolved source disposition: {}".format(source), file=sys.stderr)
            return _adopt_exit(res.status)
        except ValueError as exc:
            # A fail-closed --inputs read error: cannot-evaluate (exit 2), never a silent skip.
            print("opf adopt plan: cannot evaluate: {}".format(exc), file=sys.stderr)
            return EXIT_MALFORMED
        except Exception as exc:  # noqa: BLE001  fail-closed backstop, never a false verdict or uncaught exit-1
            print("opf adopt plan: cannot evaluate: unexpected error ({!r}); failing closed to exit "
                  "2".format(exc), file=sys.stderr)
            return EXIT_MALFORMED

    # sub == "status": the read-only state report over the two adoption homes; ZERO writes. Both homes
    # are read ONLY through the engine's contained, no-follow primitives bound to ONE product-root fd (a
    # symlinked root or ancestor refuses, a `..` after a symlink included), the same ones verify_bundle
    # and journal_clean_or_refuse use; the journal's lock and entries are read beneath the one journal-
    # root fd opened from it, never by re-resolving a path: a symlinked, dangling or wrong-type home or
    # entry is cannot-evaluate, never followed, skipped or read as absent. A bundle is reported only when
    # the engine's own bundle validator grades it VALID, and the journal is read only through the engine's
    # own classification (journal_state).
    adopt, journal = _opf_adopt_apply, _opf_adopt_apply._journal
    try:
        journal.require_containment()
        root_fd = adopt._open_product_root(root_abs)
        try:
            findings = []
            runs = []
            evidence_rel = _opf_store.IMPORTED_REL + "/" + adopt.KIND
            home = journal._lstat_contained(root_fd, evidence_rel)
            dfd = None                       # no evidence home: no run was ever applied
            if home is not None:
                if not stat.S_ISDIR(home.st_mode):
                    raise adopt.AdoptApplyError("{} is not a directory (a symlinked, dangling or foreign "
                                                "entry at the reserved adoption evidence home)".format(
                                                    evidence_rel))
                dfd = journal._open_dir_contained(root_fd, evidence_rel)
            # K9a fix 4: the home descriptor that produced the listing is HELD through EVERY bundle
            # verification and passed into it, so the listing and each bundle bind to ONE home directory
            # identity: an evidence home swapped onto the pathname after the listing is never
            # re-resolved, and two homes neither of which is clean alone can never combine into one
            # clean report.
            try:
                entries = []
                if dfd is not None:
                    entries = [(name, journal._lstat_at(dfd, name)) for name in sorted(os.listdir(dfd))]
                for name, est in entries:
                    if est is None or not stat.S_ISDIR(est.st_mode):
                        raise adopt.AdoptApplyError("{}/{} is not a directory (a symlinked, dangling or "
                                                    "foreign entry at the reserved adoption evidence "
                                                    "home)".format(evidence_rel, name))
                    if not adopt.is_run_id(name):
                        findings.append("{}/{} does not match the adoption run-id grammar (foreign "
                                        "content at the reserved adoption evidence home)".format(
                                            evidence_rel, name))
                        continue
                    checked = adopt._verify_bundle_at(root_fd, name, adopt.evidence_home_rel(name),
                                                      home_fd=dfd)
                    if checked.status == _opf_store.VALID:
                        runs.append(name)
                    elif checked.status == _opf_store.INVALID:
                        findings.extend("adoption run {}: {}".format(name, f) for f in checked.findings)
                    else:
                        raise adopt.AdoptApplyError("adoption run {}: {}".format(
                            name, "; ".join(checked.findings)))
            finally:
                if dfd is not None:
                    _opf_store._close_fd_exc_safe(dfd)
            owner, opened = adopt.journal_state(root_fd, adopt._journal_root(root_abs))
        finally:
            _opf_store._close_fd_exc_safe(root_fd)
        journal_rel = adopt.JOURNAL_REL
        if owner is not None:
            findings.append("the adoption journal lock at {} is held (pid {}); status never seizes it "
                            "(reconcile once no adoption run is live)".format(journal_rel, owner.get("pid")))
        if opened:
            findings.append("the adoption journal at {} holds interrupted transaction(s) {} -- an "
                            "adoption transaction did not complete".format(journal_rel, ", ".join(opened)))
        for run_id in runs:
            print("opf adopt status: adoption run {}: evidence bundle at {}/{}".format(
                run_id, evidence_rel, run_id))
        for f in findings:
            print("opf adopt status: FINDING: {}".format(f), file=sys.stderr)
        if findings:
            return EXIT_FINDING
        if not runs:
            print("opf adopt status: no adoption run exists")
        return EXIT_OK
    except (_opf_adopt_apply.AdoptApplyError, _opf_adopt_apply._journal.JournalError, OSError) as exc:
        print("opf adopt status: cannot evaluate: {}".format(exc), file=sys.stderr)
        return EXIT_MALFORMED
    except Exception as exc:  # noqa: BLE001  fail-closed backstop, never a false verdict or uncaught exit-1
        print("opf adopt status: cannot evaluate: unexpected error ({!r}); failing closed to exit "
              "2".format(exc), file=sys.stderr)
        return EXIT_MALFORMED


def _cli_self_test():
    """Guard the dispatcher's render, doctor, import, record, adopt, and source-only init routes.
    Render/doctor cases below judge return codes; init also checks payload validation, refusal reasons,
    and preservation through check_opf_init._suite(main). Each case captures stdout and stderr.
    Cases: an unknown verb, no args, and every not-yet-wired KNOWN_VERB fail closed (exit 2); a bare `render`,
    both flags together, an unrecognized render flag, a `--root` with no value, and an empty `--root` are usage
    errors (exit 2); `render --check` and `render --write` FORWARD to the U4 engine -- a NOT-ADOPTED root
    returns 0 for each (the wiring discriminator: reverting the render wiring routes it to the fail-closed
    KNOWN_VERBS branch and returns 2, failing this case; render --write is never run against a mutating
    adopter store here, only NOT-ADOPTED / garbage synthetic roots) and a garbage store returns 2. For
    `doctor`: bad-flag / usage
    cases (a `--root` with no value, an unknown flag) fail closed (exit 2); a NOT-ADOPTED root returns 0 (the
    doctor wiring discriminator: reverting the doctor route routes `doctor` to the fail-closed KNOWN_VERBS
    branch and returns 2, failing this case); a garbage store returns 2; with `--require-store` (the CI
    floor) the same NOT-ADOPTED root returns 2 with the located flag message, and a duplicate flag is a usage
    error (exit 2). The render clean/drift 0/1
    discrimination rides check_opf_drift.py --self-test, and the doctor clean(0)/mutation(1) discrimination
    over a validate_store-VALID COMMITTED store rides check_opf_doctor.py --self-test, each driving the same
    wiring end to end. Returns 0 clean, 1 on a failure, 2 on a harness error.

    HARNESS fail-close (FIX 2): the fixture SETUP (tempfile.mkdtemp) and the fixture I/O (directory creation
    and writes) are the harness surface; an OSError from any of them is caught and returned as a located
    cannot-evaluate (exit 2), never allowed to escape uncaught (which Python would surface as exit 1). A final
    broad backstop routes any other residual error to exit 2 as well. Discriminating coverage injects an
    OSError at mkdtemp and at directory creation and asserts each routes to exit 2 (change-carries-check)."""
    import io
    import shutil
    import tempfile
    import contextlib

    try:
        failures = []

        def expect(argv, want):
            buf = io.StringIO()
            try:
                with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
                    got = main(list(argv))
            except BaseException as exc:                # a dispatcher crash is itself a failure
                failures.append("{!r} raised {!r}".format(argv, exc))
                return
            if got != want:
                failures.append("{!r} returned {!r} (expected {})".format(argv, got, want))

        # Routing cases that need no store on disk.
        expect([], EXIT_MALFORMED)
        expect(["frobnicate"], EXIT_MALFORMED)
        for verb in KNOWN_VERBS:
            if verb not in ("init", "import", "render", "doctor", "upgrade", "absorb", "record", "adopt"):
                expect([verb], EXIT_MALFORMED)          # a known but not-yet-wired verb fails closed
        expect(["render"], EXIT_MALFORMED)              # bare: exactly one of --check/--write required
        expect(["render", "--check", "--write"], EXIT_MALFORMED)   # both flags refused
        expect(["render", "--bogus"], EXIT_MALFORMED)   # unknown render flag
        expect(["render", "--root"], EXIT_MALFORMED)    # --root needs a value
        expect(["render", "--check", "--root", ""], EXIT_MALFORMED)   # empty root refused

        # doctor verb ROUTING (PR-B), judged on exit code only. Bad-flag / usage cases need no store on disk.
        expect(["doctor", "--root"], EXIT_MALFORMED)    # --root needs a value
        expect(["doctor", "--check", "--root", ""], EXIT_MALFORMED)   # unknown doctor flag (and empty root)
        expect(["doctor", "--bogus"], EXIT_MALFORMED)   # unknown doctor flag
        expect(["doctor", "--require-store", "--require-store"], EXIT_MALFORMED)   # duplicate CI-floor flag

        # absorb verb ROUTING (OPF-CHANGELOG-ABSORB), judged on exit code only. These grammar cases fail
        # closed in the parser BEFORE any store resolution, so they need no store on disk. The NOT-ADOPTED
        # (0) / garbage (2) discrimination over a real root rides _fixture_leg below (reverting the absorb
        # dispatch routes these to the fail-closed KNOWN_VERBS branch, returning 2 where 0 is expected).
        expect(["absorb", "--root"], EXIT_MALFORMED)             # --root needs a value
        expect(["absorb", "--root", ""], EXIT_MALFORMED)         # empty root refused
        expect(["absorb", "--covers"], EXIT_MALFORMED)           # --covers needs a value
        expect(["absorb", "--covers", ""], EXIT_MALFORMED)       # empty covers refused
        expect(["absorb", "--bogus"], EXIT_MALFORMED)            # unknown arg

        # import verb ROUTING (OPF-IMPORT-VERB), judged on exit code only. The retired verb parses no
        # argument, so every argument list, a bare `import` included, meets its retirement pointer at exit 2
        # before any store resolution and needs no store on disk (the exact pointer text, the structural
        # tripwire and the probed no-read and no-write rows are asserted in _import_leg below, the
        # wiring discriminator).
        _VALID_RID = "imp-20260101T000000Z-0123456789abcdef"   # the former run-id grammar; names no staged run
        expect(["import"], EXIT_MALFORMED)                       # bare
        expect(["import", "--help"], EXIT_MALFORMED)             # help is the pointer too
        expect(["import", "--scan", "--set", "s.toml"], EXIT_MALFORMED)   # a former --scan form
        expect(["import", "--review", _VALID_RID, "--actor", "x", "--interactive"], EXIT_MALFORMED)
        expect(["import", "--bogus"], EXIT_MALFORMED)            # an unknown flag

        # record verb ROUTING (OPF-RECORD), judged on exit code only. These grammar cases fail closed in the
        # parser BEFORE any store resolution, so they need no store on disk; the recorded 0 / refusal 2
        # discrimination over real stores rides check_opf_record.py --self-test.
        expect(["record"], EXIT_MALFORMED)                       # bare: a subcommand is required
        expect(["record", "frobnicate"], EXIT_MALFORMED)         # unknown subcommand
        expect(["record", "transition", "BI-1", "done"], EXIT_MALFORMED)   # missing --actor
        expect(["record", "transition", "BI-1", "done/proposed", "--actor", "assistant"],
               EXIT_MALFORMED)                                   # the qualifier is derived, never given
        expect(["record", "transition", "PD-1", "decided", "--actor", "maintainer", "--decision", "x"],
               EXIT_MALFORMED)                                   # the bundle options come together
        expect(["record", "done-with-receipt", "BI-1"], EXIT_MALFORMED)    # missing --actor
        expect(["record", "done-with-receipt", "BI-1", "--actor", "assistant"],
               EXIT_MALFORMED)                                   # maintainer-only, refused before the store
        expect(["record", "create"], EXIT_MALFORMED)             # missing --type/--title/--actor
        expect(["record", "create", "--root"], EXIT_MALFORMED)   # --root needs a value
        expect(["record", "worklog-append", "--kind", "added", "--summary", "s", "--actor", "importer"],
               EXIT_MALFORMED)                                   # an importer never authors through record

        # adopt verb ROUTING (OPF-ADOPT K9a), judged on exit code AND, where the code alone would not
        # discriminate, the located message: a bare `adopt` was an UNKNOWN verb before this PR, so it
        # already exited 2 (with an unknown-verb message that even quotes 'adopt'); the usage case
        # therefore asserts the verb's OWN located prefix, which only the wired _cmd_adopt emits (the
        # verb-name keyword assertion -- reverting the dispatch flips it red on the message, not the
        # code). The grammar cases fail closed in the parser BEFORE any store or filesystem read, so they
        # need no store on disk; the read-only plan/status discrimination over real fixtures rides
        # _adopt_leg below.
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            rc = main(["adopt"])
        if rc != EXIT_MALFORMED or "opf adopt: give a subcommand" not in buf.getvalue():
            failures.append("bare adopt: rc={!r} (expected 2 + the located `opf adopt:` usage "
                            "message)".format(rc))
        expect(["adopt", "frobnicate"], EXIT_MALFORMED)             # unknown subcommand
        expect(["adopt", "status", "--root"], EXIT_MALFORMED)       # --root needs a value
        expect(["adopt", "status", "--root", ""], EXIT_MALFORMED)   # empty root refused
        expect(["adopt", "status", "--root", ".", "--root", "."], EXIT_MALFORMED)   # duplicate --root
        expect(["adopt", "status", "--bogus"], EXIT_MALFORMED)      # unknown arg
        expect(["adopt", "status", "--inputs", "w.toml"], EXIT_MALFORMED)   # --inputs is plan-only
        expect(["adopt", "plan"], EXIT_MALFORMED)                   # plan requires --inputs FILE
        expect(["adopt", "plan", "--root", "."], EXIT_MALFORMED)    # --root alone: still no --inputs
        expect(["adopt", "plan", "--inputs"], EXIT_MALFORMED)       # --inputs needs a value
        expect(["adopt", "plan", "--inputs", ""], EXIT_MALFORMED)   # empty inputs refused
        expect(["adopt", "plan", "--inputs", "w.toml", "--inputs", "w.toml"], EXIT_MALFORMED)  # duplicate
        # The four deferred subcommands are RECOGNIZED and refuse fail-closed (exit 2) BEFORE any parse,
        # store resolution or write, each with its own located not-yet-available message (never the
        # unknown-verb or unknown-subcommand message, and naming no command that does not exist); the K9b
        # engine lands them.
        for deferred_sub in ("approve", "apply", "complete", "reconcile"):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
                rc = main(["adopt", deferred_sub])
            if rc != EXIT_MALFORMED or "opf adopt {}: not yet available".format(
                    deferred_sub) not in buf.getvalue():
                failures.append("adopt {}: rc={!r} (expected 2 + the located not-yet-available "
                                "message)".format(deferred_sub, rc))

        def _fixture_leg():
            """Build the on-disk fixtures and drive render --check over them. Assertion outcomes are recorded
            in `failures`; returns None on success or EXIT_MALFORMED on a HARNESS error. The tempdir creation
            and every fixture directory/file write are the harness surface: an OSError from any of them is a
            located cannot-evaluate (exit 2), never an uncaught escape that Python would surface as exit 1."""
            try:
                base = tempfile.mkdtemp(prefix="opf-cli-selftest-")
            except OSError as exc:
                print("opf cli self-test: harness error: could not create the fixture tempdir ({})".format(
                    exc), file=sys.stderr)
                return EXIT_MALFORMED
            try:
                try:
                    # A NOT-ADOPTED root (no .working/): render --check FORWARDS to the U4 engine and returns
                    # 0 (NOT APPLICABLE) -- the wiring discriminator (an unwired render verb returns 2 here).
                    not_adopted = os.path.join(base, "not-adopted")
                    os.mkdir(not_adopted)
                    # A garbage store (a discovered but unparseable manifest): render --check fails closed
                    # (exit 2).
                    broken = os.path.join(base, "broken")
                    os.makedirs(os.path.join(broken, ".working", "toml"))
                    with open(os.path.join(broken, ".working", "toml", "manifest.toml"),
                              "w", encoding="utf-8") as fh:
                        fh.write("this is not valid toml {{{\n")
                except OSError as exc:
                    print("opf cli self-test: harness error: could not build a fixture store ({})".format(
                        exc), file=sys.stderr)
                    return EXIT_MALFORMED
                expect(["render", "--check", "--root", not_adopted], EXIT_OK)
                expect(["render", "--check", "--root", broken], EXIT_MALFORMED)
                # render --write over synthetic roots ONLY (never a mutating adopter store, per the no-live-
                # write-in-CI posture): a NOT-ADOPTED root is NOT APPLICABLE and returns 0 writing nothing
                # (the write-wiring discriminator; an unwired --write routes to the fail-closed KNOWN_VERBS
                # branch and returns 2), and a garbage store fails closed (exit 2: gather + the U6 gate
                # refuse). The VALID/INVALID gate discrimination over a whole-store-valid fixture with
                # injected observations rides _opf_views.self_test end to end.
                expect(["render", "--write", "--root", not_adopted], EXIT_OK)
                expect(["render", "--write", "--root", broken], EXIT_MALFORMED)
                # doctor over the same synthetic roots: a NOT-ADOPTED root reports NOT APPLICABLE and returns
                # 0 -- the wiring discriminator (reverting the doctor route sends `doctor` to the fail-closed
                # KNOWN_VERBS branch, which returns 2 here, failing this case); a garbage store fails closed
                # (exit 2). The clean(0)/mutation(1) discrimination over a validate_store-VALID COMMITTED store
                # rides check_opf_doctor.py --self-test end to end (a committed HEAD is needed for the prior
                # observation), mirroring how the render clean/drift 0/1 rides check_opf_drift.py --self-test.
                expect(["doctor", "--root", not_adopted], EXIT_OK)
                expect(["doctor", "--root", broken], EXIT_MALFORMED)
                # The CI floor (enforcement pack, U16): with --require-store the SAME NOT-ADOPTED root is a
                # located cannot-evaluate (exit 2), while the unflagged case just above still returns 0. The
                # message is asserted too, because deleting the flag would also exit 2 (as an unrecognized
                # argument), so the code alone would not discriminate. Reverting the flag's NOT-ADOPTED branch
                # returns 0 here, failing this case. A garbage store keeps its unflagged exit 2.
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
                    rc = main(["doctor", "--require-store", "--root", not_adopted])
                if rc != EXIT_MALFORMED or "--require-store was given but no OPF store" not in buf.getvalue():
                    failures.append("doctor --require-store over a NOT-ADOPTED root: rc={!r} (expected 2 + "
                                    "the located no-store message)".format(rc))
                expect(["doctor", "--require-store", "--root", broken], EXIT_MALFORMED)
                # upgrade over the same synthetic roots: a NOT-ADOPTED root reports NOT APPLICABLE and
                # returns 0 -- the wiring discriminator (reverting the upgrade route sends `upgrade` to the
                # fail-closed KNOWN_VERBS branch, which returns 2 here, failing this case); a garbage store
                # fails closed (exit 2). The full 1.0.0 -> 1.1.0 migration discrimination (schema delta,
                # canonical-bytes precondition, doctor-VALID gate, idempotence, and the above-tooling refusal)
                # rides check_opf_upgrade.py --self-test end to end over a byte-pinned committed 1.0.0 store.
                expect(["upgrade", "--root", not_adopted], EXIT_OK)
                expect(["upgrade", "--root", broken], EXIT_MALFORMED)
                # the upgrade parser accepts only one non-empty --root: the retired --homes-plan, a missing
                # or empty --root, a repeated --root and an unknown option each exit 2, and the directory and
                # file names under the fixture root after the loop match those before it (names only, compared
                # once; the current directory an argument-less run would use is not checked)
                tree_before = sorted((d, sorted(dn), sorted(fn)) for d, dn, fn in os.walk(base))
                for bad in (["--homes-plan"], ["--root"], ["--root", ""],
                            ["--root", not_adopted, "--root", not_adopted], ["--unknown"]):
                    expect(["upgrade"] + bad, EXIT_MALFORMED)
                if sorted((d, sorted(dn), sorted(fn)) for d, dn, fn in os.walk(base)) != tree_before:
                    failures.append("a refused upgrade argument changed the fixture tree")
                # absorb over the same synthetic roots: a NOT-ADOPTED root reports NOT APPLICABLE and returns
                # 0 -- the wiring discriminator (reverting the absorb route sends `absorb` to the fail-closed
                # KNOWN_VERBS branch, which returns 2 here, failing this case); a garbage store fails closed
                # (exit 2). The OK-draft discrimination over a resolved store rides _opf_absorb.self_test end
                # to end (it builds its own valid synthetic stores).
                expect(["absorb", "--root", not_adopted], EXIT_OK)
                expect(["absorb", "--root", broken], EXIT_MALFORMED)
            finally:
                shutil.rmtree(base, ignore_errors=True)
            return None

        def _import_leg():
            """Drive the retired import verb (spec 14.1), judged on the exact output, the exit code AND
            observable side effects. Returns None on success or EXIT_MALFORMED on a harness (fixture I/O)
            error. _cmd_import parses no argument, so each argument list below must print exactly the
            retirement pointer on stderr (a literal pinned here, so a suffix appended to IMPORT_RETIRED
            is red), nothing on stdout, and exit 2. Vectors: no arguments,
            `--help` / `-h`, each of the 14 former flags alone, with a separate value, with a joined value
            and with an empty joined value; abbreviated and ambiguous prefixes, alone and joined; the
            former argparse review-aid forms (`--review -1 --show-review`, an empty `--root=`, a repeated
            `--review`, a separate value starting with `-`, the `--s` / `--d` abbreviations); the former
            full forms of every mode over an adopted store and a NOT-ADOPTED root, with malformed or
            missing named files; two modes; an unknown flag, a positional token and `--`. The pointer
            names adoption and the prompt pack in words and no command.

            The no-read contract is checked two ways. The verb's runtime behaviour, exit 2 with the
            pointer for every representative argument list, is what the RUNTIME probe below observes;
            the STRUCTURAL checks are a tripwire against an accidental regression of _cmd_import, not a
            proof. _import_body_findings over this file's BYTES (read in binary and parsed as the
            interpreter decodes them, so a coding declaration is honoured) flags a coding declaration
            other than utf-8 on line 1 or 2; a body after _cmd_import's docstring other than exactly the
            one print of the pointer to stderr and the one return of EXIT_MALFORMED, a reference to
            `rest` or any other call included; a modeled binding of print, _cmd_import, sys,
            IMPORT_RETIRED, EXIT_MALFORMED or __builtins__ in any expression evaluated at module scope;
            and sys, IMPORT_RETIRED or EXIT_MALFORMED not bound by one direct top-level statement ahead
            of the def. _import_code_findings flags a live `_cmd_import.__code__` other than the code
            compiled from that parsed definition (co_code, co_consts recursively, names, argument counts,
            flags, first line, filename); _import_namespace_findings flags a live function whose
            __globals__ is not this module's namespace or whose __builtins__ is not the interpreter's
            builtins mapping. Planted reads and
            writes in the body (a listing of an `--apply` root or of the cwd, io.FileIO reads, a shell
            `cat`, a read of the `--ingest-options` file, an os.mkdir of an argument), planted rebindings
            (a rebound `_cmd_import`, `print` or EXIT_MALFORMED, a walrus binding in a comprehension,
            generator expression, lambda, argument default, keyword default, decorator, class base or
            class keyword, and a `__builtins__` binding ahead of the def: a dict, a copy of the builtins
            with print replaced, an `import ... as __builtins__`; sys, IMPORT_RETIRED or EXIT_MALFORMED
            bound under a condition, in a try or past the def; a raw_unicode_escape coding declaration
            on line 2 hiding a print or an IMPORT_RETIRED binding after the def behind a comment opening
            with a backslash-u000a escape, flagged both by the bytes parse and as a declaration), a
            non-utf-8 declaration alone, a swapped `__code__` with the same
            argument count and names, the live code rebuilt over a scratch module holding each of those
            `__builtins__` values, and a function over a copy of this module's globals are each asserted
            flagged. The structural checks parse unoptimized and compile at the interpreter's level, so
            they are clean under python -O and -OO too. They do not cover reflective or dynamic changes
            (globals() or vars() stores, setattr and attribute stores such as builtins.print or
            sys.stderr, exec, eval or compile, importlib), source-decoding and loader tricks beyond the
            coding-declaration check, the module's own import-time top-level code (outside the runtime
            probe too), the _bootstrap() and dispatch main() runs before `return _cmd_import(rest)`
            (covered only by the runtime probe), calls through routes the probe does not patch (posix,
            _io, ctypes, already-open handles, sockets), or another module patching this one. The RUNTIME
            probe (representative only) runs every argument list above inside one probe context that
            records, and refuses, a call through the module attributes it patches (builtins, io, os, os.path,
            subprocess, pathlib.Path) to the open, stat, listing, walk, access, readlink, cwd, os.path
            existence and type, pathlib.Path read, query and write, filesystem write and remove (os.mkdir,
            makedirs, mkfifo, mknod, unlink, remove, removedirs, rename, renames, replace, rmdir, symlink,
            link, utime, chmod, chown, lchown, truncate, ftruncate), process-spawn
            (subprocess.Popen, os.system, os.popen, os.spawn*, os.posix_spawn*, os.exec*, os.fork) and
            stdin-read functions while the verb runs, whatever path or descriptor the call names. A route
            it does not patch is NOT refused: a read or write through an already-open file object or
            descriptor (os.write included), a socket, ctypes, or a call through the posix or _io modules
            directly (os.listdir is posix.listdir, and only the os attribute is patched). A row passes
            only if no probed call is made, and every probe is first asserted to record and refuse a
            call. The fixture tree is byte-unchanged. Flip: routing `import` to the fail-closed
            KNOWN_VERBS branch, restoring any argument check ahead of the pointer, or adding any read,
            write, spawn or other statement to _cmd_import turns rows red."""
            import ast
            import _optlevel
            import builtins as builtins_mod
            import pathlib
            import re
            import subprocess
            import types
            from unittest import mock

            # The expected text is pinned here, independent of IMPORT_RETIRED (never compared with itself).
            refusal = ("the ordinary import modes (scan, plan, review and apply) are retired: clean-start "
                       "adoption (OPF spec 14.1) is the only intake, and post-adoption import uses the "
                       "approved prompt pack; nothing was written")
            pointer = "opf import: {}\n".format(refusal)
            _RID = "imp-20260101T000000Z-0123456789abcdef"   # the former run-id grammar; names no run
            former_valued = ("--review", "--apply", "--root", "--set", "--actor", "--decisions",
                             "--dispositions", "--ingest-options", "--include", "--diff-review")
            former_valueless = ("--scan", "--plan", "--interactive", "--show-review")

            def run_cli(argv):
                out, err = io.StringIO(), io.StringIO()
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    rc = main(argv)
                return rc, out.getvalue(), err.getvalue()

            # The exact-text row is red on a changed pointer: a suffix appended to IMPORT_RETIRED must fail.
            g = _cmd_import.__globals__
            real, g["IMPORT_RETIRED"] = g["IMPORT_RETIRED"], IMPORT_RETIRED + "; see the adoption guide"
            try:
                planted_err = run_cli(["import"])[2]
            finally:
                g["IMPORT_RETIRED"] = real
            if planted_err == pointer:
                failures.append("import pointer check: a pointer with an appended suffix was not flagged")

            calls = []    # the probed calls made while the current argument list runs
            live = []     # non-empty only while main() runs one argument list under the probes

            def refused(argv):
                del calls[:]
                live.append(True)
                try:
                    rc, out, err = run_cli(["import"] + argv)
                except BaseException as exc:            # an escape is itself a failure, never a pass
                    failures.append("import {!r} raised {!r}".format(argv, exc))
                    rc = out = err = None
                finally:
                    del live[:]
                if calls:
                    failures.append("import {!r} made {} probed filesystem, process or stdin call(s) while "
                                    "the verb ran (want none): {!r}".format(argv, len(calls), calls[:4]))
                if rc is None:
                    return
                if rc != EXIT_MALFORMED or out != "" or err != pointer:
                    failures.append("import {!r}: rc={!r} stdout={!r} stderr={!r} (expected exactly the "
                                    "retirement pointer on stderr at exit 2)".format(argv, rc, out, err))

            def tree_snapshot(rootdir):
                snap = {}
                for dirpath, dirs, files in os.walk(rootdir):
                    for name in dirs:
                        snap[os.path.relpath(os.path.join(dirpath, name), rootdir)] = None
                    for name in files:
                        p = os.path.join(dirpath, name)
                        with open(p, "rb") as fh:
                            snap[os.path.relpath(p, rootdir)] = fh.read()
                return snap

            # The STRUCTURAL tripwire, independent of argument lists and no proof: it flags a body other
            # than exactly the print and the return, and a live function other than the checked one. The
            # source is read as BYTES and those bytes are parsed, so a coding declaration is honoured as
            # the interpreter honours it (a utf-8 text read would check other text); the mutants below
            # are built over its utf-8 text.
            try:
                with open(__file__, "rb") as fh:
                    own_source = fh.read()
            except OSError as exc:
                print("opf cli self-test: harness error: could not read {} for the import structural "
                      "check ({})".format(__file__, exc), file=sys.stderr)
                return EXIT_MALFORMED
            try:
                own_text = own_source.decode("utf-8")
            except UnicodeDecodeError as exc:
                failures.append("import structural check: {} is not utf-8 ({})".format(__file__, exc))
                own_text = own_source.decode("utf-8", "replace")
            for finding in _import_body_findings(own_source):
                failures.append("import structural check: {}".format(finding))
            # The live-code tie flags a live code object other than the one the parsed definition
            # compiles to.
            for finding in _import_code_findings(own_source, _cmd_import, __file__):
                failures.append("import structural check: {}".format(finding))
            # The live-namespace tie flags a live function that does not resolve names through this module
            # and the real builtins mapping (as after a `__builtins__` binding ahead of the def).
            for finding in _import_namespace_findings(_cmd_import):
                failures.append("import structural check: {}".format(finding))
            # A `__code__` swap keeping the argument count, varnames, names and filename (the former,
            # metadata-only tie accepted it) is flagged: this replacement parses its arguments.
            swap_src = ("def _cmd_import(rest):\n"
                        "    if rest == ['--x']:\n"
                        "        return 0\n"
                        "    print('opf import: {}'.format(IMPORT_RETIRED), file=sys.stderr)\n"
                        "    return EXIT_MALFORMED\n")
            swap_code = [c for c in compile(swap_src, __file__, "exec", dont_inherit=True).co_consts
                         if isinstance(c, types.CodeType)][0]
            live_code = _cmd_import.__code__
            if (swap_code.co_varnames != live_code.co_varnames
                    or frozenset(swap_code.co_names) != frozenset(live_code.co_names)
                    or swap_code.co_argcount != live_code.co_argcount):
                failures.append("import structural check: the planted __code__ swap does not keep the live "
                                "names and argument count (harness)")
            if not _import_code_findings(own_source, types.FunctionType(swap_code, {}), __file__):
                failures.append("import structural check: a __code__ swap with matching names was not "
                                "flagged")
            # The structural check flags each planted read or write (inserted ahead of the print) and each
            # rebinding (appended to the module); the unchanged source is clean, as asserted just above.
            fn_node = [n for n in _optlevel.parse(own_text).body
                       if isinstance(n, ast.FunctionDef) and n.name == "_cmd_import"][0]
            lines = own_text.splitlines(keepends=True)
            at = fn_node.body[1].lineno - 1
            planted_body = (
                'os.listdir(rest[rest.index("--root") + 1]) if "--apply" in rest and "--root" in rest '
                'else None',
                "os.listdir(os.getcwd())",
                "[__import__('io').FileIO(t).read() for t in rest if t.startswith('/')]",
                "__import__('subprocess').run('cat .working/toml/manifest.toml', shell=True)",
                "open(rest[rest.index('--ingest-options') + 1]).read() if '--ingest-options' in rest "
                "else None",
                "os.mkdir(rest[0]) if rest else None",
                "rest = list(rest)",
                "pass",
            )
            planted_tail = (
                "_cmd_import = lambda rest: open(rest[0]).read()",
                "print = lambda *a, **k: open(a[0]).read()",
                "EXIT_MALFORMED = 3",
                # A walrus in an expression evaluated at module scope (the round-7 reproductions first).
                "[(print := w) for _ in [0]]",
                "def _z(x=(print := w)): pass",
                "def _z(*, k=(sys := w)): pass",
                "@(print := w)\ndef _z(): pass",
                "class _Z((print := w)): pass",
                "class _Z(metaclass=(EXIT_MALFORMED := w)): pass",
                "_z = lambda x=(IMPORT_RETIRED := w): x",
                "_z = lambda: (print := w)",
                "(0 for _ in [(_cmd_import := w)])",
                "{k: (print := w) for k in [0]}",
            )
            mutants = [(t, "".join(lines[:at] + ["    " + t + "\n"] + lines[at:])) for t in planted_body]
            mutants += [(t, own_text + "\n" + t + "\n") for t in planted_tail]
            # A `__builtins__` binding ahead of the def changes what print resolves to inside _cmd_import
            # without binding print (the round-8 reproductions: a dict, a builtins copy, an import).
            planted_head = (
                '__builtins__ = {"print": p}',
                "__builtins__ = dict(vars(builtins), print=p)",
                "import evil as __builtins__",
            )
            def_at = fn_node.lineno - 1
            mutants += [(t, "".join(lines[:def_at] + [t + "\n"] + lines[def_at:])) for t in planted_head]
            for text, mutant in mutants:
                if not _import_body_findings(mutant):
                    failures.append("import structural check: a planted {!r} was not flagged".format(text))
            # A coding declaration hides a binding from a check over utf-8 text: under raw_unicode_escape a
            # backslash-u000a escape in a comment is a newline to the interpreter, so the rest of that line
            # is a statement (the round-10 reproduction; this file carries no such escape, so the mutant
            # decodes as intended, and the escape is built from chr(92)). Each mutant, as bytes, must be
            # flagged both ways: the bytes parse sees the hidden binding, and the declaration is itself a
            # finding.
            cookie = "# -*- coding: raw_unicode_escape -*-\n"
            for hidden, want in (("print = (lambda *a, **k: None)", "print is rebound"),
                                 ('IMPORT_RETIRED = "x"', "IMPORT_RETIRED is not bound exactly once")):
                mutant = "".join(lines[:1] + [cookie] + lines[1:fn_node.end_lineno]
                                 + ["#" + chr(92) + "u000a" + hidden + "\n"] + lines[fn_node.end_lineno:])
                got = _import_body_findings(mutant.encode("utf-8"))
                if not (any(want in f for f in got) and any("coding declaration" in f for f in got)):
                    failures.append("import structural check: a raw_unicode_escape declaration hiding {!r} "
                                    "after the def was not flagged both ways ({!r})".format(hidden, got))
            # A declaration other than utf-8 is a finding on its own (it hides nothing here); a utf-8 one
            # on line 2 is clean.
            for decl, flagged in (("# -*- coding: latin-1 -*-", True),
                                  ("# vim: set fileencoding=cp1252 :", True),
                                  ("# -*- coding: utf-8 -*-", False)):
                got = _import_body_findings("".join(lines[:1] + [decl + "\n"] + lines[1:]).encode("utf-8"))
                if any("coding declaration" in f for f in got) != flagged or (not flagged and got):
                    failures.append("import structural check: a line-2 {!r} gave {!r} (want {})".format(
                        decl, got, "a coding finding" if flagged else "none"))

            # A watched binding moved under a sys.argv or environment condition, into a try, or past the def
            # keeps its count and literal but can be unbound when the body runs: each is flagged.
            def nest(name, head, tail):
                s = [s for s in ast.parse(own_text).body if isinstance(s, (ast.Assign, ast.Import))
                     and name in [getattr(t, "id", None) for t in getattr(s, "targets", ())]
                     + [a.name for a in getattr(s, "names", ())]][0]
                a, z = s.lineno - 1, s.end_lineno
                if head is None:  # moved to the end of the module
                    return "".join(lines[:a] + lines[z:] + ["\n"] + lines[a:z])
                return "".join(lines[:a] + [head + "\n"] + ["    " + x for x in lines[a:z]] + [tail]
                               + lines[z:])
            for name, head, tail in (("IMPORT_RETIRED", 'if "--scan" not in sys.argv:', ""),
                                     ("IMPORT_RETIRED", 'if os.environ.get("OPF_IMPORT") is None:', ""),
                                     ("EXIT_MALFORMED", 'if "OPF_IMPORT" not in os.environ:', ""),
                                     ("EXIT_MALFORMED", "try:", "except Exception:\n    pass\n"),
                                     ("sys", 'if os.environ.get("OPF_IMPORT") is None:', ""),
                                     ("IMPORT_RETIRED", None, "")):
                if not any("direct top-level" in f for f in _import_body_findings(nest(name, head, tail))):
                    failures.append("import structural check: the binding of {} under {!r} was not "
                                    "flagged".format(name, head))
            # The live-namespace tie flags each of those bindings at run time. A def takes its builtins from
            # its module's `__builtins__` when it runs, so the live code is rebuilt as a function over a
            # scratch module registered in sys.modules (its globals pass) whose `__builtins__` holds each
            # planted value: a dict, a builtins copy with print replaced, an imported module. The builtins
            # module itself is the clean control and must pass; a function over a copy of this module's
            # globals is flagged too.
            shadow = types.ModuleType("_opf_import_shadow_builtins")
            shadow.print = lambda *a, **k: None
            planted_values = ((None, builtins_mod),
                              (planted_head[0], dict(print=shadow.print)),
                              (planted_head[1], dict(vars(builtins_mod), print=shadow.print)),
                              (planted_head[2], shadow))
            probe_name = "_opf_import_namespace_probe"
            saved = sys.modules.get(probe_name)
            try:
                for text, value in planted_values:
                    probe = types.ModuleType(probe_name)
                    probe.__builtins__ = value
                    sys.modules[probe_name] = probe
                    got = _import_namespace_findings(types.FunctionType(_cmd_import.__code__, probe.__dict__))
                    if text is None and got:
                        failures.append("import structural check: the clean namespace control was flagged "
                                        "(harness): {!r}".format(got))
                    if text is not None and not any("__builtins__" in f for f in got):
                        failures.append("import structural check: a planted {!r} ahead of the def was not "
                                        "flagged at run time".format(text))
            finally:
                if saved is None:
                    sys.modules.pop(probe_name, None)
                else:
                    sys.modules[probe_name] = saved
            copied = types.FunctionType(_cmd_import.__code__, dict(_cmd_import.__globals__))
            if not any("__globals__" in f for f in _import_namespace_findings(copied)):
                failures.append("import structural check: a function over a copy of the module globals was "
                                "not flagged")

            try:
                ibase = tempfile.mkdtemp(prefix="opf-cli-import-")
            except OSError as exc:
                print("opf cli self-test: harness error: could not create the import fixture tempdir "
                      "({})".format(exc), file=sys.stderr)
                return EXIT_MALFORMED
            try:
                try:
                    store = os.path.join(ibase, "store")
                    machine = os.path.join(store, ".working", "toml")
                    os.makedirs(machine)
                    manifest = "\n".join([
                        "[opf]", 'standard = "opf"',
                        'spec_version = "{}"'.format(_opf_store.SUPPORTED_SPEC_VERSION),
                        'layout = "inline"', 'posture = "required"', 'import_status = "none"',
                        "", "[store]", 'sync_target = ""',
                        "", "[modules]", "governance = true",
                        "", "[types.backlog_item]", 'namespace = "BI"',
                        "", "[vendors]", 'registered = []', "",
                    ]) + "\n"
                    with open(os.path.join(machine, "manifest.toml"), "w", encoding="utf-8") as fh:
                        fh.write(manifest)
                    with open(os.path.join(machine, "counters.toml"), "w", encoding="utf-8") as fh:
                        fh.write("schema = 1\n\n[counters]\nBI = 0\nLF = 0\nWL = 0\n")
                    with open(os.path.join(store, "a.txt"), "w", encoding="utf-8") as fh:
                        fh.write("first source body\n")
                    inputs = {
                        "set.toml": 'schema = 1\nsource = ["a.txt"]\n',
                        "not-toml.toml": "option = [\n",
                        "decisions.json": json.dumps({"schema": 1, "run_id": _RID, "decisions": []}),
                        "not-json.json": "{",
                    }
                    for name, body in inputs.items():
                        with open(os.path.join(ibase, name), "w", encoding="utf-8") as fh:
                            fh.write(body)
                    not_adopted = os.path.join(ibase, "not-adopted")
                    os.mkdir(not_adopted)
                except OSError as exc:
                    print("opf cli self-test: harness error: could not build the import fixture "
                          "({})".format(exc), file=sys.stderr)
                    return EXIT_MALFORMED

                def path(name):
                    return os.path.join(ibase, name)

                before = tree_snapshot(ibase)
                # A representative set of argument lists, store-free and over the fixture alike.
                vectors = [[], ["--help"], ["-h"]]
                for flag in former_valued + former_valueless:
                    vectors += [[flag], [flag, "x"], [flag + "=x"], [flag + "="]]
                vectors += [
                    # Abbreviated prefixes, unambiguous and ambiguous, alone and joined.
                    ["--rev", _RID], ["--ro", store], ["--sc"], ["--pl"], ["--ap", _RID], ["--rev=" + _RID],
                    ["--ro=" + store], ["--ro="], ["--r", _RID], ["--in=x"], ["--sh=x"], ["--show-reviews"],
                    ["--ac"], ["--ac=tester"],
                    # The former argparse review-aid forms.
                    ["--review", "-1", "--show-review"], ["--review", _RID, "--show-review", "--root="],
                    ["--review", _RID, "--review", _RID], ["--review", _RID, "--root", "-old"],
                    ["--review", _RID, "--s"], ["--review", _RID, "--d", _RID],
                    ["--review=-x", "--show-review"],
                    ["--review", _RID, "--show-review", "--root=--scan"],
                    ["--diff-review=--old", "--review", _RID],
                    ["--review", _RID, "--show-review", "--show-review"], ["--rev", _RID, "--review", _RID],
                    # Two modes, a repeated mode, and a valueless flag given a value.
                    ["--scan", "--plan"], ["--scan", "--scan"], ["--show-review=x"],
                    # An unknown flag, an unknown joined flag, a positional token, an empty token, and `--`.
                    ["--bogus"], ["--x=y"], ["frobnicate"], [""], ["--"], ["--", "--scan"],
                    ["--plan", "--", "--root", store],
                ]
                # Every former mode in its full form, over the adopted store and a NOT-ADOPTED root, with
                # well-formed, malformed and missing named files alike.
                for top in (store, not_adopted):
                    vectors += [
                        ["--scan", "--set", path("set.toml"), "--root", top],
                        ["--plan", "--set", path("not-toml.toml"), "--root", top],
                        ["--plan", "--dispositions", path("missing.toml"), "--ingest-options",
                         path("not-toml.toml"), "--include", "*.md", "--include", "docs/*", "--root", top],
                        ["--review", _RID, "--actor", "tester", "--decisions", path("decisions.json"),
                         "--root", top],
                        ["--review", _RID, "--actor", "tester", "--decisions", path("not-json.json"),
                         "--show-review", "--diff-review", _RID, "--root", top],
                        ["--review", _RID, "--actor", "tester", "--interactive", "--root", top],
                        ["--apply", _RID, "--root", top],
                        ["--apply", "not-a-run-id", "--set", path("missing.json"), "--root=" + top],
                    ]
                # The eight lists that alone ran under the earlier, narrower probe, kept beside the matrix.
                for top in (store, not_adopted):
                    vectors += [
                        ["--review", _RID, "--actor", "tester", "--interactive", "--root", top],
                        ["--scan", "--set", path("set.toml"), "--root", top],
                        ["--review", _RID, "--decisions", path("decisions.json"), "--root=" + top],
                    ]
                vectors += [[], ["--plan", "--dispositions", path("not-toml.toml")]]

                # The RUNTIME probe, representative only: every list above runs inside ONE probe context.
                # While main() runs a list, a call through any module attribute in `targets` below (and
                # any stdin read) is recorded and refused before it acts, whatever path or descriptor it
                # names, so a planted call through one of them is never performed; outside a run the
                # probes call straight through. A route not in `targets` is NOT refused: a write or read
                # through an already-open file object or descriptor (os.write included), a socket,
                # ctypes, or a call through the posix or _io modules directly (os.listdir is
                # posix.listdir, and only the os attribute is patched).
                def probe(label, real):
                    def wrapped(*args, **kwargs):
                        if not live:
                            return real(*args, **kwargs)
                        calls.append((label, repr(args[0])[:120] if args else ""))
                        raise PermissionError("opf import probe: " + label + " refused")
                    return wrapped

                class ProbeStdin(io.StringIO):
                    def _note(self, label):
                        if live:
                            calls.append(("sys.stdin." + label, ""))
                            raise PermissionError("opf import probe: sys.stdin." + label + " refused")

                    def read(self, *args):
                        self._note("read")
                        return super().read(*args)

                    def readline(self, *args):
                        self._note("readline")
                        return super().readline(*args)

                    def readlines(self, *args):
                        self._note("readlines")
                        return super().readlines(*args)

                    def __next__(self):
                        self._note("__next__")
                        return super().__next__()

                    def fileno(self):
                        self._note("fileno")
                        return super().fileno()

                    @property
                    def buffer(self):
                        self._note("buffer")
                        raise AttributeError("buffer")

                targets = [(builtins_mod, "open"), (builtins_mod, "input"), (io, "open"),
                           (io, "open_code"), (io, "FileIO"), (subprocess, "Popen")]
                targets += [(os, n) for n in (
                    "open", "read", "stat", "lstat", "fstat", "scandir", "listdir", "walk", "fwalk",
                    "access", "readlink", "getcwd", "chdir", "system", "popen", "fork", "forkpty",
                    "posix_spawn", "posix_spawnp", "spawnl", "spawnle", "spawnlp", "spawnlpe", "spawnv",
                    "spawnve", "spawnvp", "spawnvpe", "execl", "execle", "execlp", "execlpe", "execv",
                    "execve", "execvp", "execvpe",
                    # the filesystem write and remove functions (builtins / io open in any mode is above)
                    "mkdir", "makedirs", "mkfifo", "mknod", "unlink", "remove", "removedirs", "rename",
                    "renames", "replace", "rmdir", "symlink", "link", "utime", "chmod", "chown", "lchown",
                    "truncate", "ftruncate") if hasattr(os, n)]
                targets += [(os.path, n) for n in ("exists", "lexists", "isfile", "isdir", "islink",
                                                   "getsize")]
                targets += [(pathlib.Path, n) for n in (
                    "open", "read_text", "read_bytes", "exists", "iterdir", "stat", "is_file", "is_dir",
                    "write_text", "write_bytes", "touch", "mkdir", "unlink", "rmdir", "rename", "replace",
                    "symlink_to", "hardlink_to", "chmod") if hasattr(pathlib.Path, n)]
                real_stdin = sys.stdin
                sys.stdin = ProbeStdin("accept\n")
                try:
                    with contextlib.ExitStack() as stack:
                        for owner, attr in targets:
                            label = getattr(owner, "__name__", repr(owner)) + "." + attr
                            stack.enter_context(mock.patch.object(owner, attr, probe(label, getattr(owner, attr))))
                        # Every probe records and refuses a call while live (it never reaches the real
                        # function), so the list above is exactly what the probe refuses.
                        for owner, attr in targets:
                            del calls[:]
                            live.append(True)
                            try:
                                getattr(owner, attr)(path("probe-target"))
                                performed = True
                            except PermissionError:
                                performed = False
                            except BaseException as exc:  # noqa: BLE001  any other escape is a failure
                                performed = exc
                            finally:
                                del live[:]
                            if performed is not False or len(calls) != 1:
                                failures.append("the import probe on {}.{} did not record and refuse a "
                                                "call ({!r}, {} recorded)".format(
                                                    getattr(owner, "__name__", owner), attr, performed,
                                                    len(calls)))
                        del calls[:]
                        for argv in vectors:
                            refused(argv)
                    consumed = sys.stdin.tell() != 0
                finally:
                    sys.stdin = real_stdin
                if consumed:
                    failures.append("a refused import read stdin")
                if tree_snapshot(ibase) != before:
                    failures.append("a refused import changed the fixture tree")
                # The pointer is to adoption and the prompt pack in words, naming no command (spec 14.1).
                if not ("adoption (OPF spec 14.1)" in refusal and "prompt pack" in refusal
                        and not re.search(r"`|\bopf [a-z]", refusal)):
                    failures.append("the import retirement refusal does not point to adoption and the "
                                    "prompt pack in words only")
            finally:
                shutil.rmtree(ibase, ignore_errors=True)
            return None

        def _adopt_leg():
            """Build product-root fixtures and drive the READ-ONLY adopt subcommands end to end, judged
            on exit codes, messages AND snapshot equality (the K9a no-write contract). Returns None on
            success or EXIT_MALFORMED on a harness (fixture I/O) error. Each 0/1 vector is a deliberate
            flip: reverting the adopt dispatch routes it to the fail-closed KNOWN_VERBS branch (returning
            2 where 0/1 is expected -- the wiring discriminator), and a subcommand that writes flips the
            byte-identical snapshot red. Vectors: `status` over a clean root -> 0 + the no-adoption-run
            message, byte-identical; `status` over a root with an OPEN adoption-journal transaction -> 1
            (finding), byte-identical (status reports, never reconciles); `status` over a completed engine
            transaction or a nothing-opened entry -> 0, over a run-id directory the bundle validator grades
            INVALID -> 1, and over a symlinked, dangling or wrong-type root, home or entry (a journal entry
            included), a hardlinked lock.break, or a `--root DIR/link/..` -> 2; with the journal path
            swapped for a symlink after its contained open, the product's own lock state (never the
            decoy's); with the evidence home swapped right after its listing, the ORIGINAL home's findings
            (read through the held home descriptor, K9a fix 4); with a transaction directory swapped for
            an empty decoy after the journal enumeration, still the interrupted-transaction finding
            (classified through the held txn descriptor, K9a fix 4); an unresolvable cwd -> 2;
            `plan` with a missing, FIFO, oversized, symlinked, symlinked-parent or hardlinked
            worksheet -> 2 (the bounded, single-link, fail-closed read boundary); `plan` with a STALE (well-formed, non-matching) observation
            digest -> 2 (the inventory binding refuses BEFORE any op validation), byte-identical; `plan`
            with the FRESH digest and one SCHEMA-VIOLATING op row (a known op missing its required
            inputs) -> 1 (INVALID rides _opf_adopt.validate_op through the wired planner, the 0/1
            discriminator past the digest binding), byte-identical. The VALID freeze discrimination rides
            _opf_adopt_plan.self_test end to end (it builds its own decision-complete fixtures)."""
            import subprocess
            import tomllib
            journal = _opf_adopt_apply._journal
            adopt_j_rel = _opf_adopt_apply.JOURNAL_REL
            adopt_rid = "adopt-20260101T000000Z-0123456789abcdef"
            evidence_rel = _opf_store.IMPORTED_REL + "/" + _opf_adopt_apply.KIND

            def run_adopt(argv):
                buf = io.StringIO()
                try:
                    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
                        return main(list(argv)), buf.getvalue()
                except BaseException as exc:            # a dispatcher crash is itself a failure
                    return "raised {!r}".format(exc), buf.getvalue()

            def tree_snapshot(rootdir):
                snap = dict()
                for dirpath, _dirs, files in os.walk(rootdir):
                    for name in files:
                        p = os.path.join(dirpath, name)
                        with open(p, "rb") as fh:
                            snap[os.path.relpath(p, rootdir)] = fh.read()
                return snap

            try:
                abase = tempfile.mkdtemp(prefix="opf-cli-adopt-")
            except OSError as exc:
                print("opf cli self-test: harness error: could not create the adopt fixture tempdir "
                      "({})".format(exc), file=sys.stderr)
                return EXIT_MALFORMED
            try:
                try:
                    clean = os.path.join(abase, "clean")
                    os.mkdir(clean)
                    with open(os.path.join(clean, "note.txt"), "w", encoding="utf-8") as fh:
                        fh.write("adopter content\n")
                    # debris: an OPEN transaction (INTENT without a terminal frame), published through the
                    # journal's own writer, so the engine's classification reads it as interrupted.
                    debris = os.path.join(abase, "debris")
                    os.makedirs(os.path.join(debris, adopt_j_rel))
                    debris_fd = os.open(debris, os.O_RDONLY | os.O_DIRECTORY)
                    try:
                        jr_fd = journal.open_journal_root_fd(debris_fd, adopt_j_rel)
                        try:
                            os.mkdir("txn", dir_fd=jr_fd)
                            journal.publish(jr_fd, Path(debris, adopt_j_rel, "txn"), journal.F_INTENT,
                                            {"txn": "txn", "ops": []})
                        finally:
                            os.close(jr_fd)
                    finally:
                        os.close(debris_fd)
                    # done: a COMPLETED engine transaction (its journal entry stays, and its bundle is VALID).
                    done = os.path.join(abase, "done")
                    os.mkdir(done)
                    _opf_adopt_apply.run_adopt_transaction(done, adopt_rid, lambda ops: None)
                    # an empty (nothing-opened) journal entry, which the engine classifies as clean.
                    unopened = os.path.join(abase, "unopened")
                    os.makedirs(os.path.join(unopened, adopt_j_rel, "txn"))
                except (OSError, journal.JournalError, _opf_adopt_apply.AdoptApplyError) as exc:
                    print("opf cli self-test: harness error: could not build the adopt fixtures "
                          "({})".format(exc), file=sys.stderr)
                    return EXIT_MALFORMED

                # status over a CLEAN root -> 0 + the no-adoption-run message, and the tree byte-unchanged.
                before = tree_snapshot(clean)
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
                    rc = main(["adopt", "status", "--root", clean])
                if rc != EXIT_OK or "no adoption run exists" not in buf.getvalue():
                    failures.append("adopt status over a clean root: rc={!r} (expected 0 + the "
                                    "no-adoption-run message)".format(rc))
                if tree_snapshot(clean) != before:
                    failures.append("adopt status mutated the product root (status must be a pure read)")

                # status over adoption-journal DEBRIS -> 1 (an interrupted transaction is a finding), and
                # the tree byte-unchanged (status reports, never reconciles).
                before = tree_snapshot(debris)
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
                    rc = main(["adopt", "status", "--root", debris])
                if rc != EXIT_FINDING or "interrupted transaction(s) txn" not in buf.getvalue():
                    failures.append("adopt status over journal debris: rc={!r} (expected 1 + the journal "
                                    "finding)".format(rc))
                if tree_snapshot(debris) != before:
                    failures.append("adopt status (journal debris) mutated the product root")

                # status reads both homes through the ENGINE (K9a fix 1), each vector red on the K9a head:
                # a COMPLETED engine transaction is clean and its bundle verifies (was 1, "any journal
                # entry is interrupted"); a nothing-opened entry is clean (was 1); a run-id directory the
                # bundle validator grades INVALID is a finding (was 0, "evidence bundle"); and a symlinked,
                # dangling or wrong-type root, home or entry is cannot-evaluate, never followed (was 0/1
                # over the followed or absent target) and never read as absent (was 0).
                def fresh_root(name):
                    path = os.path.join(abase, name)
                    os.mkdir(path)
                    with open(os.path.join(path, "note.txt"), "w", encoding="utf-8") as fh:
                        fh.write("adopter content\n")
                    return path

                try:
                    outside = os.path.join(abase, "outside")
                    os.makedirs(os.path.join(outside, "imported", _opf_adopt_apply.KIND, adopt_rid))
                    stray = os.path.join(outside, "stray")
                    os.mkdir(stray)
                    # a foreign tree whose only entries are NOT run ids: an enumeration that followed the
                    # link would report them as findings (exit 1) rather than refuse (exit 2).
                    foreign = os.path.join(abase, "foreign")
                    os.makedirs(os.path.join(foreign, "imported", _opf_adopt_apply.KIND, "stray"))
                    status_vectors = [("completed engine transaction", done, EXIT_OK,
                                       "adoption run {}: evidence bundle at".format(adopt_rid)),
                                      ("nothing-opened journal entry", unopened, EXIT_OK,
                                       "no adoption run exists")]
                    root = fresh_root("nobundle")
                    os.makedirs(os.path.join(root, evidence_rel, adopt_rid))
                    status_vectors.append(("run-id directory with no inventory", root, EXIT_FINDING,
                                           "has no inventory"))
                    root = fresh_root("filebundle")
                    os.makedirs(os.path.join(root, evidence_rel))
                    with open(os.path.join(root, evidence_rel, adopt_rid), "w", encoding="utf-8") as fh:
                        fh.write("not a bundle\n")
                    status_vectors.append(("regular file with a run-id name", root, EXIT_MALFORMED,
                                           "cannot evaluate"))
                    for label, rel, target in (
                            ("symlinked evidence home", evidence_rel, outside),
                            ("symlinked .working ancestor, run-id bundle outside", ".working", outside),
                            ("symlinked .working ancestor, foreign dir outside", ".working", foreign),
                            ("dangling evidence home", evidence_rel, os.path.join(abase, "nowhere")),
                            ("symlinked evidence entry", evidence_rel + "/stray", stray),
                            ("symlinked journal home", adopt_j_rel, outside),
                            ("dangling journal home", adopt_j_rel, os.path.join(abase, "nowhere")),
                            ("symlinked journal entry", adopt_j_rel + "/txn", stray)):
                        root = fresh_root("link-{}".format(len(status_vectors)))
                        os.makedirs(os.path.dirname(os.path.join(root, rel)), exist_ok=True)
                        os.symlink(target, os.path.join(root, rel))
                        status_vectors.append((label, root, EXIT_MALFORMED, "cannot evaluate"))
                    os.symlink(clean, os.path.join(abase, "rootlink"))
                    status_vectors.append(("symlinked --root", os.path.join(abase, "rootlink"),
                                           EXIT_MALFORMED, "cannot evaluate"))
                    # K9a fix 2, each red on the fix-1 head: a wrong-type journal entry is cannot-evaluate
                    # (was 0, skipped by the engine's enumeration) while the engine's own regular
                    # lock.break stays clean; and a `--root DIR/link/..`, which the kernel resolves to
                    # link's target's parent (here the debris root), refuses (was 0, abspath collapsed it
                    # lexically onto DIR) while a `..` through a real directory still resolves.
                    root = fresh_root("filejournalentry")
                    os.makedirs(os.path.join(root, adopt_j_rel))
                    with open(os.path.join(root, adopt_j_rel, "txn"), "w", encoding="utf-8") as fh:
                        fh.write("not a transaction\n")
                    status_vectors.append(("regular-file journal entry", root, EXIT_MALFORMED,
                                           "wrong-type entry is refused"))
                    root = fresh_root("lockbreakdir")
                    os.makedirs(os.path.join(root, adopt_j_rel, "lock.break"))
                    status_vectors.append(("directory named lock.break", root, EXIT_MALFORMED,
                                           "wrong-type entry is refused"))
                    root = fresh_root("lockbreakfile")
                    os.makedirs(os.path.join(root, adopt_j_rel))
                    open(os.path.join(root, adopt_j_rel, "lock.break"), "w", encoding="utf-8").close()
                    status_vectors.append(("regular lock.break arbitration file", root, EXIT_OK,
                                           "no adoption run exists"))
                    # K9a fix 4, red on the fix-3 head (which read this root as 0): a HARDLINKED
                    # lock.break is a second name for a foreign inode, refused by the strict journal
                    # enumeration's nlink==1 identity guard, never accepted as the engine's own file.
                    root = fresh_root("lockbreaklinked")
                    os.makedirs(os.path.join(root, adopt_j_rel))
                    victim = os.path.join(abase, "lockbreak-victim")
                    with open(victim, "w", encoding="utf-8") as fh:
                        fh.write("victim\n")
                    os.link(victim, os.path.join(root, adopt_j_rel, "lock.break"))
                    status_vectors.append(("hardlinked lock.break arbitration file", root, EXIT_MALFORMED,
                                           "hard links"))
                    os.mkdir(os.path.join(debris, "child"))
                    root = fresh_root("dotdot")
                    os.symlink(os.path.join(debris, "child"), os.path.join(root, "link"))
                    status_vectors.append(("--root DIR/link/.. (the debris root physically)",
                                           os.path.join(root, "link", ".."), EXIT_MALFORMED,
                                           "resolves physically"))
                    status_vectors.append(("--root with a .. through a real directory",
                                           os.path.join(clean, "..", "clean"), EXIT_OK,
                                           "no adoption run exists"))
                    # K9a fix 3, each red on the fix-2 head, whose guard compared collapsed REALPATHS
                    # only (physical == lexical admitted both): a `..` whose preceding component is a
                    # symlink is refused even when the link resolves INSIDE the root (the kernel still
                    # crossed a directory the operator never named), and a `..` after a component that
                    # does not exist is refused rather than collapsed away.
                    root = fresh_root("insidelink")
                    os.mkdir(os.path.join(root, "childdir"))
                    os.symlink(os.path.join(root, "childdir"), os.path.join(root, "inlink"))
                    status_vectors.append(("--root DIR/link/.. with the link resolving inside DIR",
                                           os.path.join(root, "inlink", ".."), EXIT_MALFORMED,
                                           "crosses `..` out of"))
                    root = fresh_root("missingdotdot")
                    status_vectors.append(("--root DIR/missing/..",
                                           os.path.join(root, "missing", ".."), EXIT_MALFORMED,
                                           "crosses `..` out of"))
                except OSError as exc:
                    print("opf cli self-test: harness error: could not build the adopt status fixtures "
                          "({})".format(exc), file=sys.stderr)
                    return EXIT_MALFORMED
                for label, root, want, needle in status_vectors:
                    rc, out = run_adopt(["adopt", "status", "--root", root])
                    if rc != want or needle not in out:
                        failures.append("adopt status over a {}: rc={!r} (expected {} + {!r})".format(
                            label, rc, want, needle))

                # the journal lock is read beneath the HELD contained journal-root fd (K9a fix 2), red on
                # the fix-1 head, which re-opened the journal by PATH: the journal path is swapped for a
                # symlink to a decoy right AFTER the contained open (a deterministic stand-in for a
                # concurrent writer), so a held product lock is still reported (was 0, the empty decoy)
                # and a decoy's lock never is (was 1, the decoy's pid).
                def lock_record(pid):
                    owner = dict(uid=os.getuid(), pid=pid, session="self-test", utc="2026-01-01T00:00:00Z")
                    owner["pid-start"] = ""
                    return json.dumps(owner).encode("utf-8")

                real_open_jr = journal.open_journal_root_fd
                race_results = []
                try:
                    for label, product_pid, decoy_pid, want, needle in (
                            ("held product lock, empty decoy", 1111, None, EXIT_FINDING, "(pid 1111)"),
                            ("no product lock, decoy lock", None, 4242, EXIT_OK, "no adoption run exists")):
                        root = fresh_root("race-{}".format(len(race_results)))
                        decoy = os.path.join(abase, "decoy-{}".format(len(race_results)))
                        os.makedirs(os.path.join(root, adopt_j_rel))
                        os.mkdir(decoy)
                        for where, pid in ((os.path.join(root, adopt_j_rel), product_pid), (decoy, decoy_pid)):
                            if pid is not None:
                                with open(os.path.join(where, "lock"), "wb") as fh:
                                    fh.write(lock_record(pid))

                        swap_fired = []

                        def racing_open(root_fd, rel, root=root, decoy=decoy, fired=swap_fired):
                            fd = real_open_jr(root_fd, rel)
                            fired.append(rel)   # the injection provably ran (K9a fix 4)
                            os.rename(os.path.join(root, rel), os.path.join(root, rel) + ".moved")
                            os.symlink(decoy, os.path.join(root, rel))
                            return fd

                        journal.open_journal_root_fd = racing_open
                        try:
                            race_results.append((label, run_adopt(["adopt", "status", "--root", root]), want,
                                                 needle, swap_fired))
                        finally:
                            journal.open_journal_root_fd = real_open_jr
                except OSError as exc:
                    print("opf cli self-test: harness error: could not build the adopt journal-swap fixtures "
                          "({})".format(exc), file=sys.stderr)
                    return EXIT_MALFORMED
                for label, (rc, out), want, needle, fired in race_results:
                    if rc != want or needle not in out or not fired:
                        failures.append("adopt status with the journal swapped after its contained open "
                                        "({}): rc={!r}, fired={!r} (expected {} + {!r} with the swap "
                                        "injected)".format(label, rc, bool(fired), want, needle))

                # K9a fix 4 (round-4 BLOCKER), red on the fix-3 head: the evidence home's directory
                # identity is HELD from the status listing through EVERY bundle verification, so a home
                # swapped onto `.working/imported/adoption` right after its listing is never re-resolved:
                # the report stays the ORIGINAL home's (its payload-drift finding, exit 1), never a
                # combination of the original's listing with the replacement's bundles (the fix-3 head
                # exited 0 here, reporting the replacement bundle as verified while the replacement's
                # own foreign entry went unlisted). Each home alone is a finding (exit 1), asserted
                # around the swap; the hook asserts its swap actually fired.
                try:
                    swaproot = fresh_root("homeswap")
                    home_abs = os.path.join(swaproot, evidence_rel)
                    payload_rel = evidence_rel + "/" + adopt_rid + "/payload.txt"
                    good_inv = _opf_adopt_apply.emit_inventory(
                        adopt_rid, [_opf_adopt_apply.inventory_row(payload_rel, b"GOOD!")])
                    os.makedirs(os.path.join(home_abs, adopt_rid))
                    with open(os.path.join(home_abs, adopt_rid, "inventory.toml"), "wb") as fh:
                        fh.write(good_inv)
                    with open(os.path.join(home_abs, adopt_rid, "payload.txt"), "wb") as fh:
                        fh.write(b"BAD!!")                       # drifted: the original home is exit 1
                    repl_abs = os.path.join(swaproot, ".working", "imported", "adoption-replacement")
                    os.makedirs(os.path.join(repl_abs, adopt_rid))
                    with open(os.path.join(repl_abs, adopt_rid, "inventory.toml"), "wb") as fh:
                        fh.write(good_inv)
                    with open(os.path.join(repl_abs, adopt_rid, "payload.txt"), "wb") as fh:
                        fh.write(b"GOOD!")                       # verifies, but beside a foreign entry
                    os.mkdir(os.path.join(repl_abs, "zzz-foreign"))
                except (OSError, _opf_adopt_apply.AdoptApplyError) as exc:
                    print("opf cli self-test: harness error: could not build the adopt home-swap fixtures "
                          "({})".format(exc), file=sys.stderr)
                    return EXIT_MALFORMED
                orig_rc, orig_out = run_adopt(["adopt", "status", "--root", swaproot])
                if orig_rc != EXIT_FINDING or "payload drift" not in orig_out:
                    failures.append("adopt status over the drifted original home: rc={!r} (expected 1 + "
                                    "the payload-drift finding)".format(orig_rc))
                home_swap_fired = []
                real_listdir = os.listdir

                def swapping_listdir(target):
                    names = real_listdir(target)
                    # the FIRST descriptor listing that surfaces the run id is the status home listing
                    # (bound to the held home descriptor); swap the homes right after it returns.
                    if isinstance(target, int) and adopt_rid in names and not home_swap_fired:
                        home_swap_fired.append(target)
                        os.rename(home_abs, home_abs + ".aside")
                        os.rename(repl_abs, home_abs)
                    return names

                os.listdir = swapping_listdir
                try:
                    swap_rc, swap_out = run_adopt(["adopt", "status", "--root", swaproot])
                finally:
                    os.listdir = real_listdir
                if swap_rc != EXIT_FINDING or "payload drift" not in swap_out or not home_swap_fired:
                    failures.append("adopt status with the evidence home swapped after its listing: "
                                    "rc={!r}, fired={!r} (expected 1 + the ORIGINAL payload-drift "
                                    "finding, read through the held home descriptor)".format(
                                        swap_rc, bool(home_swap_fired)))
                repl_rc, repl_out = run_adopt(["adopt", "status", "--root", swaproot])
                if repl_rc != EXIT_FINDING or "does not match the adoption run-id grammar" not in repl_out:
                    failures.append("adopt status over the replacement home (now at the pathname): "
                                    "rc={!r} (expected 1 + the foreign-entry finding)".format(repl_rc))

                # K9a fix 4 (round-4 BLOCKER), red on the fix-3 head: each journal transaction
                # directory's identity is HELD from the journal enumeration through classification and
                # its frame reads, so an interrupted transaction renamed aside right after the
                # enumeration and replaced by an empty decoy directory of the same name is STILL
                # classified from its own INTENT frames (exit 1, the interrupted-transaction finding),
                # never reopened by name and read as nothing-opened (the fix-3 head exited 0 here,
                # reporting that no adoption run exists). The hook asserts its swap actually fired.
                try:
                    txnswap = fresh_root("txnswap")
                    os.makedirs(os.path.join(txnswap, adopt_j_rel))
                    ts_fd = os.open(txnswap, os.O_RDONLY | os.O_DIRECTORY)
                    try:
                        ts_jr = journal.open_journal_root_fd(ts_fd, adopt_j_rel)
                        try:
                            os.mkdir("txn", dir_fd=ts_jr)
                            journal.publish(ts_jr, Path(txnswap, adopt_j_rel, "txn"), journal.F_INTENT,
                                            {"txn": "txn", "ops": []})
                        finally:
                            os.close(ts_jr)
                    finally:
                        os.close(ts_fd)
                except (OSError, journal.JournalError) as exc:
                    print("opf cli self-test: harness error: could not build the adopt txn-swap fixture "
                          "({})".format(exc), file=sys.stderr)
                    return EXIT_MALFORMED
                txn_swap_fired = []
                real_txn_dirs = journal._journal_txn_dirs

                def swapping_txn_dirs(*args, **kwargs):
                    res = real_txn_dirs(*args, **kwargs)
                    if not txn_swap_fired:
                        txn_swap_fired.append(True)
                        jdir = os.path.join(txnswap, adopt_j_rel)
                        os.rename(os.path.join(jdir, "txn"), os.path.join(txnswap, "txn.aside"))
                        os.mkdir(os.path.join(jdir, "txn"))    # an empty same-name decoy
                    return res

                journal._journal_txn_dirs = swapping_txn_dirs
                try:
                    ts_rc, ts_out = run_adopt(["adopt", "status", "--root", txnswap])
                finally:
                    journal._journal_txn_dirs = real_txn_dirs
                if (ts_rc != EXIT_FINDING or "interrupted transaction(s) txn" not in ts_out
                        or not txn_swap_fired):
                    failures.append("adopt status with the transaction directory swapped after the "
                                    "journal enumeration: rc={!r}, fired={!r} (expected 1 + the "
                                    "interrupted-transaction finding, classified through the held txn "
                                    "descriptor)".format(ts_rc, bool(txn_swap_fired)))

                # root normalization sits INSIDE the fail-closed handling: an unresolvable current
                # directory (os.getcwd raising, as it does once the cwd is deleted) is exit 2, never an
                # uncaught FileNotFoundError (red on the K9a head, where abspath ran outside every handler).
                real_getcwd = os.getcwd

                def _no_cwd():
                    raise FileNotFoundError(2, "simulated deleted current directory")

                os.getcwd = _no_cwd
                try:
                    cwd_results = [run_adopt(argv) for argv in (
                        ["adopt", "status"], ["adopt", "plan", "--inputs", "w.toml"])]
                finally:
                    os.getcwd = real_getcwd
                for rc, out in cwd_results:
                    if rc != EXIT_MALFORMED or "cannot resolve the product root" not in out:
                        failures.append("adopt with an unresolvable cwd: rc={!r} (expected 2 + the "
                                        "cannot-resolve message)".format(rc))

                # the physical-vs-lexical refusal NAMES the effective root (K9a fix 3): with --root
                # omitted the effective root is the current directory, so the refusal must name that
                # directory, never format the absent --root value (the fix-2 head printed 'the product
                # root None'). The cwd is simulated as a `..`-after-symlink path, the deterministic
                # stand-in for a cwd concurrently swapped for a symlink.
                fake_cwd = os.path.join(abase, "dotdot", "link", "..")
                os.getcwd = lambda: fake_cwd
                try:
                    fake_rc, fake_out = run_adopt(["adopt", "status"])
                finally:
                    os.getcwd = real_getcwd
                if fake_rc != EXIT_MALFORMED or fake_cwd not in fake_out or "None" in fake_out:
                    failures.append("adopt status refusal with --root omitted: rc={!r} (expected 2 "
                                    "+ a message naming the effective root {!r} and never None; got "
                                    "{!r})".format(fake_rc, fake_cwd, fake_out.strip()))

                # plan with a MISSING worksheet -> 2 (fail-closed read boundary, the --set class).
                expect(["adopt", "plan", "--inputs", os.path.join(abase, "absent.toml"),
                        "--root", clean], EXIT_MALFORMED)

                bindings_toml = "\n".join([
                    "[bindings]",
                    'revision = "' + "0" * 40 + '"',
                    'skip_policy = "no-skip"',
                    "[bindings.release]",
                    'version = "1.0.0"',
                    'manifest_sha256 = "' + "0" * 64 + '"',
                    'anchor = "https://example.invalid/hashes.txt"',
                    'anchor_sha256 = "' + "0" * 64 + '"',
                    "[bindings.prompt_pack]",
                    'version = "1.0.0"',
                    'digest = "sha256:' + "0" * 64 + '"',
                    "[[bindings.enforcement]]",
                    'platform = "github-actions"',
                    "means = []",
                    "members = []",
                    "residuals = []",
                    "",
                ])
                # plan with a STALE (well-formed, non-matching) digest -> 2: the inventory binding refuses
                # BEFORE any op validation, and the root stays byte-unchanged (plan is a pure read).
                stale = os.path.join(abase, "stale.toml")
                with open(stale, "w", encoding="utf-8") as fh:
                    fh.write('schema = 1\nproduct = "opf"\n'
                             'expected_observation_digest = "sha256:' + "0" * 64 + '"\n' + bindings_toml)
                before = tree_snapshot(clean)
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
                    rc = main(["adopt", "plan", "--inputs", stale, "--root", clean])
                if rc != EXIT_MALFORMED or "inventory changed" not in buf.getvalue():
                    failures.append("adopt plan with a stale observation digest: rc={!r} (expected 2 + "
                                    "the inventory-binding message)".format(rc))
                if tree_snapshot(clean) != before:
                    failures.append("adopt plan (stale digest) mutated the product root")

                # the --inputs read is BOUNDED (K9a fix 1), each vector red on the K9a head: a FIFO refuses
                # at once (the head blocked in open(), so it runs in an isolated child under a timeout and a
                # regression fails rather than hangs), an oversized worksheet refuses at the planner's byte
                # bound (the head read it whole), and a symlinked worksheet is refused, never followed (the
                # head followed it to the stale worksheet and reported that worksheet's digest mismatch).
                try:
                    fifo = os.path.join(abase, "worksheet.fifo")
                    os.mkfifo(fifo)
                    big = os.path.join(abase, "big.toml")
                    with open(big, "w", encoding="utf-8") as fh:
                        fh.write("schema = 1\n#" + "x" * _opf_adopt_plan.MAX_FILE_BYTES + "\n")
                    linked = os.path.join(abase, "linked.toml")
                    os.symlink(stale, linked)
                    linkdir = os.path.join(abase, "linkdir")
                    os.symlink(abase, linkdir)
                    # K9a fix 4: a hardlinked worksheet (two names, one inode) refuses BEFORE a byte is
                    # read; a separate source file so the other fixtures stay singly-linked.
                    hardsrc = os.path.join(abase, "hardlink-src.toml")
                    with open(hardsrc, "w", encoding="utf-8") as fh:
                        fh.write('schema = 1\nproduct = "opf"\n'
                                 'expected_observation_digest = "sha256:' + "0" * 64 + '"\n' + bindings_toml)
                    hardlinked = os.path.join(abase, "hardlinked.toml")
                    os.link(hardsrc, hardlinked)
                except OSError as exc:
                    print("opf cli self-test: harness error: could not build the adopt --inputs fixtures "
                          "({})".format(exc), file=sys.stderr)
                    return EXIT_MALFORMED
                try:
                    child = subprocess.run(
                        [sys.executable, "-I", "-B", os.path.abspath(__file__), "adopt", "plan", "--inputs",
                         fifo, "--root", clean],
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=60)
                    fifo_result = (child.returncode, child.stdout + child.stderr)
                except subprocess.TimeoutExpired:
                    fifo_result = ("timed out (blocked on the FIFO)", "")
                for label, (rc, out), needle in (
                        ("a FIFO", fifo_result, "not a regular file"),
                        ("an oversized", run_adopt(["adopt", "plan", "--inputs", big, "--root", clean]),
                         "-byte bound"),
                        # K9a fix 4, red on the fix-3 head (which read the 2-link worksheet and reported
                        # its stale digest): a multiply-linked worksheet refuses before a byte is read.
                        ("a hardlinked", run_adopt(["adopt", "plan", "--inputs", hardlinked,
                                                    "--root", clean]),
                         "hard links"),
                        ("a symlinked", run_adopt(["adopt", "plan", "--inputs", linked, "--root", clean]),
                         "worksheet unreadable"),
                        # K9a fix 2: a symlinked PARENT is refused too (red on the fix-1 head, whose
                        # final-component O_NOFOLLOW followed it to the stale worksheet's digest mismatch).
                        ("a symlinked-parent", run_adopt(["adopt", "plan", "--inputs",
                                                          os.path.join(linkdir, "stale.toml"), "--root", clean]),
                         "worksheet unreadable")):
                    if rc != EXIT_MALFORMED or needle not in out:
                        failures.append("adopt plan with {} worksheet: rc={!r} (expected 2 + {!r})".format(
                            label, rc, needle))

                # Round-5 defect 2 (K9a fix 5): a parent-directory close that reports an error inside
                # the --inputs open must not leak the just-opened worksheet fd. Inject the failure at
                # the REAL close (the number is still released, as on Linux); the read still fails
                # closed (ValueError -> the cannot-evaluate exit) and the worksheet fd is closed
                # afterwards, proven on the recorded fd itself.
                _wl_real_walk = _opf_store._open_dir_nofollow
                _wl_real_open = os.open
                _wl_real_close = os.close
                _wl_seen = {}

                def _wl_walk(path):
                    fd = _wl_real_walk(path)
                    _wl_seen["pfd"] = fd
                    return fd

                def _wl_open(*a, **kw):
                    fd = _wl_real_open(*a, **kw)
                    if kw.get("dir_fd") is not None and kw.get("dir_fd") == _wl_seen.get("pfd"):
                        _wl_seen["wfd"] = fd
                    return fd

                def _wl_close(fd):
                    _wl_real_close(fd)
                    if fd == _wl_seen.get("pfd") and "fired" not in _wl_seen:
                        _wl_seen["fired"] = True
                        raise OSError(5, "injected close failure")

                _opf_store._open_dir_nofollow = _wl_walk
                os.open = _wl_open
                os.close = _wl_close
                try:
                    try:
                        _adopt_read_inputs(stale)
                        _wl_out = "returned"
                    except ValueError:
                        _wl_out = "valueerror"
                    except OSError:
                        _wl_out = "oserror"
                finally:
                    os.close = _wl_real_close
                    os.open = _wl_real_open
                    _opf_store._open_dir_nofollow = _wl_real_walk
                if "fired" not in _wl_seen or "wfd" not in _wl_seen:
                    failures.append("adopt --inputs close-injection harness did not observe the "
                                    "parent walk, the worksheet open, or the injected close")
                else:
                    if _wl_out != "valueerror":
                        failures.append("adopt --inputs with a failing parent close: expected the "
                                        "fail-closed ValueError, got {}".format(_wl_out))
                    try:
                        os.fstat(_wl_seen["wfd"])
                        failures.append("adopt --inputs leaked the worksheet fd (fd {} still open "
                                        "after the parent close failed)".format(_wl_seen["wfd"]))
                    except OSError:
                        pass

                # plan with the FRESH digest and one SCHEMA-VIOLATING op row (a known op missing its
                # required inputs) -> 1: INVALID rides _opf_adopt.validate_op through the wired planner
                # (the 0/1 wiring discriminator past the digest binding), and the root stays byte-
                # unchanged. The worksheet digest comes from the same read-only investigation the planner
                # re-runs (the observation is deterministic over an unchanged tree).
                obs = _opf_adopt_plan.investigate(os.path.abspath(clean), sources=())
                if obs.status != _opf_store.VALID:
                    print("opf cli self-test: harness error: could not observe the adopt fixture "
                          "({})".format("; ".join(obs.findings)), file=sys.stderr)
                    return EXIT_MALFORMED
                fresh_digest = tomllib.loads(obs.observation.decode("utf-8"))["observation_digest"]
                badop = os.path.join(abase, "badop.toml")
                with open(badop, "w", encoding="utf-8") as fh:
                    fh.write('schema = 1\nproduct = "opf"\n'
                             'expected_observation_digest = "' + fresh_digest + '"\n'
                             '[[ops]]\nop = "init-store"\n' + bindings_toml)
                before = tree_snapshot(clean)
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
                    rc = main(["adopt", "plan", "--inputs", badop, "--root", clean])
                if rc != EXIT_FINDING or "missing required input" not in buf.getvalue():
                    failures.append("adopt plan with a schema-violating op row: rc={!r} (expected 1 + the "
                                    "validate_op finding)".format(rc))
                if tree_snapshot(clean) != before:
                    failures.append("adopt plan (schema-violating op) mutated the product root")
            finally:
                shutil.rmtree(abase, ignore_errors=True)
            return None

        harness_rc = _fixture_leg()
        if harness_rc is not None:
            return harness_rc
        import_rc = _import_leg()
        if import_rc is not None:
            return import_rc
        adopt_rc = _adopt_leg()
        if adopt_rc is not None:
            return adopt_rc

        # Discriminating harness-path coverage (FIX 2): an injected OSError at fixture SETUP (mkdtemp) and at
        # fixture I/O (directory creation) must each route to the located cannot-evaluate (exit 2), never
        # escape uncaught (which Python surfaces as exit 1). Judged on the returned code only; each probe
        # restores the patched callable in a finally so no later leg runs under the injection.
        def _refuse(*_a, **_k):
            raise OSError("simulated harness I/O refusal")

        def _expect_harness(label, obj, attr):
            real = getattr(obj, attr)
            setattr(obj, attr, _refuse)
            try:
                with contextlib.redirect_stderr(io.StringIO()):
                    rc = _fixture_leg()
            finally:
                setattr(obj, attr, real)
            if rc != EXIT_MALFORMED:
                failures.append("harness {}: _fixture_leg returned {!r} (expected {})".format(
                    label, rc, EXIT_MALFORMED))

        _expect_harness("mkdtemp-oserror", tempfile, "mkdtemp")
        _expect_harness("makedirs-oserror", os, "makedirs")

        # The same fixture vectors drive this in-process dispatcher and the dedicated gate's
        # isolated child. A refusal must name its reason and preserve the fixture, not merely return 2.
        import check_opf_init
        init_rc = check_opf_init._suite(main)
        if init_rc == EXIT_MALFORMED:
            return EXIT_MALFORMED
        if init_rc != EXIT_OK:
            failures.append("source-only init fixture vectors failed")

        if failures:
            for f in failures:
                print("opf cli self-test: FAIL: {}".format(f), file=sys.stderr)
            return EXIT_FINDING
        print("opf cli self-test: PASS (verb routing: unknown/unwired verbs and render/doctor usage "
              "errors fail closed; render --check forwards to the U4 engine; doctor resolves + validates a "
              "store, NOT-ADOPTED -> 0 (2 with --require-store) and a garbage store -> 2; "
              "the retired import verb (spec 14.1) prints exactly its retirement pointer at exit 2 for every "
              "argument list tried (none, --help, each former flag alone, with a separate value, joined and "
              "with an empty joined value, unambiguous and ambiguous prefixes, the former review-aid forms, "
              "two modes, an unknown flag, --), over a NOT-ADOPTED root and "
              "over an adopted store, mutating nothing (the verb's runtime behaviour, which the runtime "
              "probe observes over those representative lists only, recording no call through the module "
              "attributes it patches to its probed filesystem read, write and remove, process-spawn or "
              "stdin functions); structural checks, a tripwire against accidental regression and not a "
              "proof, parse _cmd_import's source from the file's bytes as the interpreter decodes them "
              "and flag a coding declaration other than utf-8, a body other than "
              "exactly the pointer print and the exit-2 return (a use of its arguments or any other call "
              "included), a modeled module-scope binding of the names it uses or of __builtins__, a "
              "constant it uses not bound unconditionally ahead of it, a live code object other than the "
              "parsed definition's, and a live function not on this module's globals and the real "
              "builtins mapping; they do not cover reflective or dynamic changes (globals(), vars, "
              "setattr and attribute stores such as builtins.print or sys.stderr, exec/eval/compile, "
              "importlib), source-decoding and loader tricks beyond the coding "
              "declaration check, this module's import-time top-level code (outside the probe too), the "
              "_bootstrap() and verb dispatch main() runs first (covered only by the probe), calls "
              "through routes the probe does not patch (posix, _io, ctypes, already-open handles, "
              "sockets), or another module patching this one; "
              "adopt (K9a) wires the read-only plan/status subcommands onto the "
              "adoption planner -- bare/malformed usage and the deferred approve/apply/complete/reconcile "
              "fail closed to exit 2, status -> 0 no-run or verified run / 1 open-transaction or invalid-"
              "bundle finding / 2 symlinked, dangling or wrong-type home, plan -> 2 missing, FIFO, oversized "
              "or symlinked worksheet or stale digest / 1 schema-violating op, each mutating nothing; an "
              "unresolvable cwd -> 2; fixture-setup and fixture-I/O OSError fail closed to exit 2)")
        return EXIT_OK
    except Exception as exc:  # noqa: BLE001  final fail-closed backstop, never an uncaught exit-1 escape
        print("opf cli self-test: harness error: unexpected error ({!r}); failing closed to exit 2".format(
            exc), file=sys.stderr)
        return EXIT_MALFORMED


def _retained_close_offpath_self_test():
    """F-RETAINED-CLOSE-OFFPATH (part B), under P1: swept over every descriptor-closing helper family OFF
    the adopt status/plan paths K9a hardened. Each family's fixture call runs clean to count the os.close
    calls made from opf/tools code (an ExitStack callback is attributed to the code that registered it;
    _journal's own bare closes are part A's), then once per position N with the N-th such close releasing
    its descriptor and then raising OSError(EINTR), the only mode: on Linux a close that raises has
    released the number (man 2 close), so no mode models a close that keeps it. Whatever the call then
    returns or raises, every descriptor it opened (os.open / os.dup / os.pipe) must be closed afterwards:
    what this still catches is a raising close that skips or masks a sibling close (a raising first close
    in a two-close finally that skips the second). Returns 0 clean, 1 on a failing check, 2 on a harness
    error."""
    import contextlib
    import errno
    import gzip
    import io
    import shutil
    import tarfile
    import tempfile
    import types
    from unittest import mock
    import check_opf_prompt_pack
    import check_opf_upgrade
    import _opf_adopt_observe
    this = sys.modules[__name__]
    tools = os.path.dirname(os.path.abspath(__file__))
    real_open, real_dup, real_pipe, real_close = os.open, os.dup, os.pipe, os.close
    failures = []
    ran = []

    def tools_caller():
        frame = sys._getframe(2)
        while frame is not None and os.path.basename(frame.f_code.co_filename) == "contextlib.py":
            frame = frame.f_back
        if frame is None or os.path.dirname(os.path.abspath(frame.f_code.co_filename)) != tools:
            return False
        # _journal's own remaining bare closes (_read_at, _recreate_file, the path-based lock reader, the
        # _fsync_* helpers) are hardened by part A of this fix and are not injection points here; the two
        # _journal close helpers some part-B sites route through are.
        return (os.path.basename(frame.f_code.co_filename) != "_journal.py"
                or frame.f_code.co_name in ("_close_fd_propagating", "_close_fd_quietly"))

    def sweep(call):
        """(positions, survivors) of `call` under the injection at every close position."""
        state = types.SimpleNamespace(opened=[], seen=0, target=None, fired=False)

        def _open(*args, **kwargs):
            fd = real_open(*args, **kwargs)
            state.opened.append(fd)
            return fd

        def _dup(fd):
            new = real_dup(fd)
            state.opened.append(new)
            return new

        def _pipe():
            pair = real_pipe()
            state.opened.extend(pair)
            return pair

        def _close(fd):
            if tools_caller():
                state.seen += 1
                if state.seen - 1 == state.target:
                    state.fired = True
                    real_close(fd)
                    raise OSError(errno.EINTR, "injected released-close failure")
            real_close(fd)

        def run(target):
            state.opened, state.seen, state.target, state.fired = [], 0, target, False
            os.open, os.dup, os.pipe, os.close = _open, _dup, _pipe, _close
            os.supports_dir_fd.add(_open)     # _containment.probe keys off os.open's dir_fd support
            try:
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    try:
                        call()
                    except Exception:  # noqa: BLE001  graded on descriptor retention only
                        pass
            finally:
                os.supports_dir_fd.discard(_open)
                os.open, os.dup, os.pipe, os.close = real_open, real_dup, real_pipe, real_close
            left = []
            for fd in sorted(set(state.opened)):
                try:
                    os.fstat(fd)
                except OSError:
                    continue
                left.append(fd)
            for fd in left:                   # a pre-fix run leaks; release so the suite itself stays clean
                try:
                    real_close(fd)
                except OSError:
                    pass
            return left

        run(None)                             # warm any one-time probe so every counted run is identical
        survivors = [("clean", fd) for fd in run(None)]
        positions = state.seen
        for mode in ("released",):
            for target in range(positions):
                left = run(target)
                if not state.fired:
                    survivors.append((mode, target, "injection did not fire"))
                survivors.extend((mode, target, fd) for fd in left)
        return positions, survivors

    def expect(name, call):
        positions, survivors = sweep(call)
        ran.append(name)
        ok = positions > 0 and not survivors
        print("  {} {}: {} close positions, released mode; surviving descriptors: {!r} (first six)".format(
            "PASS" if ok else "FAIL", name, positions, survivors[:6]))
        if not ok:
            failures.append(name)

    base = Path(tempfile.mkdtemp(prefix="opf-retained-close-offpath-")).resolve()
    held = []
    try:
        # A render-clean empty-state store (the check_opf_drift fixture idiom), its views populated through
        # the engine's own planner, plus a store-control .gitignore for the write-guard reader.
        root = base / "store"
        machine_rel = _opf_store.WORKING_DIRNAME + "/" + _opf_store.DEFAULT_MACHINE_SUBDIR
        machine = root / machine_rel
        machine.mkdir(parents=True)
        types_block = "".join(
            "[types." + name + ']\nnamespace = "' + ns + '"\n' for name, ns in _opf_store.BASELINE_TYPES.items())
        views_block = "".join(
            _opf_views._view(name, kind, list(sources))
            for name, (kind, sources, _renderer) in _opf_views.NAMED_VIEWS.items())
        manifest = (
            "[opf]\n"
            'standard = "opf"\n'
            'spec_version = "' + _opf_store.SUPPORTED_SPEC_VERSION + '"\n'
            'layout = "inline"\n'
            'posture = "required"\n'
            'import_status = "none"\n'
            "\n[store]\n"
            'sync_target = ""\n'
            "\n[modules]\ngovernance = true\noperational_policy = true\nconcurrent_operation = true\n\n"
            + types_block + "\n[vendors]\nregistered = []\n\n" + views_block)
        (machine / _opf_store.MANIFEST_NAME).write_text(manifest, encoding="utf-8")
        for name in _opf_store.BASELINE_TYPES:
            if name != "worklog":
                (machine / (name + ".index.toml")).write_text("schema = 1\n", encoding="utf-8")
        (machine / "worklog.toml").write_text("schema = 1\n", encoding="utf-8")
        (machine / "version.toml").write_text(
            "schema = 1\n\n[[release]]\n"
            'version = "0.1.0"\n'
            'date = "2026-01-01T00:00:00Z"\n'
            "worklog_span = []\n"
            'coverage_digest = "' + _opf_release.coverage_digest([]) + '"\n', encoding="utf-8")
        (root / "CHANGELOG.md").write_text("# Changelog\n", encoding="utf-8")
        (root / _opf_store.WORKING_DIRNAME / ".gitignore").write_text("*.tmp\n", encoding="utf-8")
        store_view = None
        plan_fd = os.open(str(root), os.O_RDONLY | os.O_DIRECTORY)
        try:
            for _name, scope, dest_rel, text in _opf_views.plan_views(plan_fd, machine_rel):
                (root / dest_rel).write_text(text, encoding="utf-8")
                if scope == "store" and store_view is None:
                    store_view = dest_rel
        finally:
            os.close(plan_fd)
        res = _opf_store.resolve_store(root)
        if res.status != _opf_store.RESOLVED or store_view is None:
            print("opf retained-close offpath self-test: harness error: the fixture store did not resolve "
                  "with a store-scope view ({})".format(res.detail), file=sys.stderr)
            return EXIT_MALFORMED

        # _opf_store: the path-based resolve chain (resolve_store -> _resolve_at -> discover_machine_store
        # -> _immediate_subdirs) and load_manifest.
        expect("offpath-store-resolve-chain", lambda: _opf_store.load_manifest(_opf_store.resolve_store(root)))
        # _opf_views: the render check and write bodies, and the rollback restore.
        expect("offpath-views-render-check", lambda: _opf_views._render_resolved_store(root, res, True))

        def render_write():
            (root / store_view).write_text("stale\n", encoding="utf-8")
            _opf_views._render_resolved_store(root, res, False)

        expect("offpath-views-render-write", render_write)
        preimages = dict()
        preimages[("store", store_view)] = b"restored\n"
        expect("offpath-views-restore", lambda: _opf_views._restore_preimages(root, res, preimages))
        # _opf_absorb / _opf_changelog: the resolved-store input readers.
        expect("offpath-absorb-load-done", lambda: _opf_absorb._load_done(res, frozenset()))
        expect("offpath-changelog-load-inputs", lambda: _opf_changelog._load_inputs(res, root))
        # _opf_observe: git's open+fstat fallback probe.
        expect("offpath-observe-worktree-open",
               lambda: _opf_observe._worktree_open_succeeds(root / "CHANGELOG.md"))
        # _opf_write_guard: the gitignore reader and the lease acquire / held-lease message / release cycle.
        expect("offpath-write-guard-gitignore", lambda: _opf_write_guard._homes_read_gitignore(root, "render"))
        root_fd = os.open(str(root), os.O_RDONLY | os.O_DIRECTORY)
        held.append(root_fd)
        lease = root / machine_rel / _opf_check.LEASE_NAME

        def lease_cycle():
            with contextlib.suppress(FileNotFoundError):
                os.unlink(str(lease))
            payload = _opf_write_guard.acquire_lease(root_fd, machine_rel, "render")
            with contextlib.suppress(_opf_write_guard.WriteGuardError):
                _opf_write_guard.acquire_lease(root_fd, machine_rel, "render")    # held: names the holder
            _opf_write_guard.release_lease(root_fd, machine_rel, payload, "render")

        expect("offpath-write-guard-lease", lease_cycle)
        # check_opf_prompt_pack: the regular-file reader refusing a directory after its open.
        expect("offpath-prompt-pack-read-regular",
               lambda: check_opf_prompt_pack._read_regular(str(root), 16, "probe"))
        # _opf_adopt_observe: _open_directory's refusal teardown and the ExitStack descriptor callbacks.
        observe = base / "observe"
        (observe / "public").mkdir(parents=True)
        os.chmod(str(observe / "public"), 0o755)
        (observe / "archive.tar.gz").write_bytes(b"archive")
        observe_fd = os.open(str(observe), os.O_RDONLY | os.O_DIRECTORY)
        held.append(observe_fd)
        deadline = types.SimpleNamespace(left=lambda: 1.0)
        expect("offpath-adopt-observe-open-directory",
               lambda: _opf_adopt_observe._open_directory(observe_fd, "public", owner=types.SimpleNamespace()))

        def put_and_read():
            with contextlib.suppress(FileNotFoundError):
                os.unlink("member", dir_fd=observe_fd)
            _opf_adopt_observe._put(observe_fd, "member", b"payload", deadline)
            _opf_adopt_observe._read_archive(observe_fd, deadline)

        expect("offpath-adopt-observe-descriptor-stack", put_and_read)
        # #377 fix 2 (claude QA r1 coverage gaps): the quarantine stack's five exit-callback closes and the
        # rollback owner's parent reopen, then the unpacker's members descriptor and per-entry stacks.
        quarantine = base / "quarantine"
        quarantine.mkdir()

        def quarantine_cycle():
            owners = []
            try:
                with _opf_adopt_observe._quarantine(str(quarantine), owners):
                    pass
            finally:
                for owner in owners:
                    with contextlib.suppress(Exception):
                        owner.finish(False)

        def tar_member(name, kind, payload=b""):
            info = tarfile.TarInfo(name)
            info.type, info.mode, info.size = kind, 0o644, len(payload)
            return info.tobuf(format=tarfile.USTAR_FORMAT) + payload + b"\0" * ((-len(payload)) % 512)

        archive = gzip.compress(b"".join((
            tar_member("wrap/", tarfile.DIRTYPE), tar_member("wrap/d/", tarfile.DIRTYPE),
            tar_member("wrap/d/f", tarfile.REGTYPE, b"data"), b"\0" * 1024)), mtime=0)
        unpack_parent = base / "unpack"
        unpack_parent.mkdir(mode=0o700)
        unpack_fd = os.open(str(unpack_parent), os.O_RDONLY | os.O_DIRECTORY)
        held.append(unpack_fd)

        def unpack():
            shutil.rmtree(str(unpack_parent / "members"), ignore_errors=True)
            _opf_adopt_observe._unpack(unpack_fd, archive, deadline)

        for label, call in (("quarantine", quarantine_cycle), ("unpack", unpack)):
            call()                            # the fixture reaches every registration before it is swept
            expect("offpath-adopt-observe-" + label, call)
        # _opf_adopt_apply: the product-root closes of verify_bundle, reconcile, the default-store probe
        # and a transaction refused at the store posture (nothing written).
        run_id = "adopt-20260101T000000Z-0123456789abcdef"
        expect("offpath-adopt-apply-verify-bundle", lambda: _opf_adopt_apply.verify_bundle(str(root), run_id))
        expect("offpath-adopt-apply-reconcile", lambda: _opf_adopt_apply.reconcile(str(root)))
        expect("offpath-adopt-apply-default-store-probe",
               lambda: _opf_adopt_apply._default_store_present_without_manifest(str(root)))
        expect("offpath-adopt-apply-transaction-refusal",
               lambda: _opf_adopt_apply.run_adopt_transaction(str(root), run_id, lambda ops: None))
        # opf.py: the init inventory / root-binding / create-only / observation helpers and the upgrade
        # replace / create-index writers, beneath a held root.
        init = base / "init"
        (init / _opf_store.WORKING_DIRNAME / "a" / "b").mkdir(parents=True)
        (init / "VERSION").write_bytes(b"1.0.0\n")
        init_fd = os.open(str(init), os.O_RDONLY | os.O_DIRECTORY)
        held.append(init_fd)

        def init_helpers():
            with contextlib.suppress(FileNotFoundError):
                os.unlink("created.txt", dir_fd=init_fd)
            _init_inventory(init_fd)
            _init_same_root(init, init_fd)
            _init_create(init_fd, "created.txt", b"x\n")
            _init_observed(init_fd, [_opf_store.WORKING_DIRNAME], dict(VERSION=b"1.0.0\n"))

        expect("offpath-opf-init-helpers", init_helpers)

        def upgrade_helpers():
            with contextlib.suppress(FileNotFoundError):
                os.unlink("new.index.toml", dir_fd=init_fd)
            _upgrade_replace(init_fd, "VERSION", b"1.0.0\n")
            _upgrade_create_index(init_fd, "new.index.toml", b"schema = 1\n")

        expect("offpath-opf-upgrade-helpers", upgrade_helpers)
        # #377 fix 2 (claude QA r1 coverage gaps): _render_resolved_store's product-root open-failure branch,
        # _init_repo's repository-root probe (a real git worktree), _cmd_init's directory publication (its
        # git boundary stubbed), and _upgrade_run's destination loop and root close over the frozen 1.0.0
        # store (the cleanliness gate stubbed, stopped at the ignore probe before any write).
        missing = base / "no-such-product-root"
        with contextlib.redirect_stderr(io.StringIO()):
            if _opf_views._render_resolved_store(missing, res, True) != _opf_views.EXIT_CANNOT_EVALUATE:
                raise RuntimeError("the unopenable product root did not refuse")
        expect("offpath-views-product-root-unopenable",
               lambda: _opf_views._render_resolved_store(missing, res, True))
        repo = base / "repo"
        repo.mkdir()
        git = _opf_observe._git_path()
        made = None if git is None else _opf_observe._run_git(
            git, repo, ["-c", "init.templateDir=", "-c", "init.defaultBranch=main", "init", "-q"])
        if made is None or not made.completed or made.rc != 0:
            raise RuntimeError("the fixture worktree could not be initialized ({})".format(
                "git not found" if made is None else made.err))
        _init_repo(repo)
        expect("offpath-opf-init-repo", lambda: _init_repo(repo))
        fresh = base / "init-cmd"
        fresh.mkdir()

        def cmd_init():
            shutil.rmtree(str(fresh / _opf_store.WORKING_DIRNAME), ignore_errors=True)
            for name in (_opf_store.POINTER_REL, "CHANGELOG.md"):
                with contextlib.suppress(FileNotFoundError):
                    os.unlink(str(fresh / name))
            with mock.patch.object(this, "_init_repo", lambda root: ("git", root)), \
                    mock.patch.object(this, "_init_untracked", lambda *args: None), \
                    mock.patch.object(this, "_init_unignored", lambda *args: None):
                return _cmd_init(["--root", str(fresh)])

        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            if cmd_init() != EXIT_OK:
                raise RuntimeError("the stubbed init fixture did not publish")
        expect("offpath-opf-cmd-init", cmd_init)
        legacy = base / "upgrade"
        legacy_machine = legacy / _opf_store.WORKING_DIRNAME / _opf_store.DEFAULT_MACHINE_SUBDIR
        legacy_machine.mkdir(parents=True)
        for name, text in ((_opf_store.MANIFEST_NAME, check_opf_upgrade._FIX_MANIFEST),
                           (_opf_check.COUNTERS_NAME, check_opf_upgrade._FIX_COUNTERS),
                           (_opf_check.VERSION_NAME, check_opf_upgrade._FIX_VERSION),
                           (_opf_check.WORKLOG_NAME, check_opf_upgrade._FIX_WORKLOG)):
            (legacy_machine / name).write_text(text, encoding="utf-8")
        for name in check_opf_upgrade._FIX_INDEX_TYPES:
            (legacy_machine / (name + _opf_check.INDEX_SUFFIX)).write_text(
                check_opf_upgrade._FIX_INDEX, encoding="utf-8")
        (legacy / _opf_store.POINTER_REL).write_text('[store]\ntarget = "dir:."\n', encoding="utf-8")
        (legacy / "CHANGELOG.md").write_text("# Changelog\n", encoding="utf-8")

        class _StopBeforeWrite(Exception):
            pass

        def stop(*args):
            raise _StopBeforeWrite()

        def upgrade_run():
            with mock.patch.object(_opf_write_guard, "check_clean", lambda *args: None), \
                    mock.patch.object(_opf_write_guard, "check_ignored", stop):
                _upgrade_run(str(legacy))

        try:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                upgrade_run()
        except _StopBeforeWrite:
            pass
        else:
            raise RuntimeError("the frozen 1.0.0 upgrade fixture did not reach the ignore probe")
        expect("offpath-opf-upgrade-run", upgrade_run)
    except Exception as exc:  # noqa: BLE001  a fixture that cannot be built is a harness error, never a pass
        print("opf retained-close offpath self-test: harness error ({!r})".format(exc), file=sys.stderr)
        return EXIT_MALFORMED
    finally:
        for fd in held:
            os.close(fd)
        shutil.rmtree(str(base), ignore_errors=True)
    if failures:
        print("opf retained-close offpath self-test: FAIL: {} (failing {} of {} checks)".format(
            ", ".join(failures), len(failures), len(ran)), file=sys.stderr)
        return EXIT_FINDING
    print("opf retained-close offpath self-test: PASS ({} checks)".format(len(ran)))
    return EXIT_OK


def _close_exc_safe_vectors_self_test():
    """#377 fix 2, under P1 (one close): the in-flight-exception close vectors. Each vector drives one call
    site with the close it names releasing its descriptor and then raising (EINTR, and separately EIO, as
    close(2) does on Linux), and grades one property on its own assertion:
      body    the body's exception is in flight at the close: that SAME exception object must propagate;
      normal  nothing is in flight: the injected close error must propagate (fail-closed);
      caller  the normal vector run from inside a CALLER's `except` block: the close error must still
              propagate, since the caller's handled exception is not in flight at the close;
      quiet   a teardown close that swallows by design: the call must complete without the close error.
    Every vector also requires that no descriptor it opened survives. V1, deterministic reuse, on every
    vector: the fault also dup2s an unrelated file onto the freed number before it raises, and two more
    assertions hold: REUSE, that descriptor is still open with its own (st_dev, st_ino); PROBE, the number
    sees no further os.close and no os.fstat. A run whose reuse setup failed (the unrelated file never
    reached the number) is red by REUSE, never a pass. Five flips then re-run the vectors and must turn
    exactly their own vectors red, each by that vector's own assertion: MASK (every close helper always
    propagating) the body vectors; SWALLOW (always quiet) the normal and caller vectors; CALLER-FRAME
    (#377 fix 1's any-exception test in place of the calling-frame test, in both helpers, the ExitStack
    callback and the descriptor stack's close) the caller vectors; RECLOSE (the pre-P1 fstat-then-reclose
    recovery put back in #377's helpers, in _journal's _close_fd_quietly and _close_fd_propagating, and in
    _opf_check._close_fd_quietly) every V1 vector, by REUSE (PROBE may fail with it; nothing else may);
    and PROBE (#378's P flip: the same helpers making one close, then an fstat of the number when it
    fails, never a second close) every V1 vector, by PROBE alone. The six sites #377 routes through
    _journal's helpers (_opf_adopt_observe._open_directory's except, _opf_views._render_resolved_store's
    product-root refusal, both closes in _opf_views._restore_preimages' finally,
    _opf_write_guard.lease_held_message and opf._init_repo) carry the same V1 vectors; all but
    _open_directory run unflipped, under RECLOSE and under PROBE only (MASK, SWALLOW and CALLER-FRAME name
    #377's own helpers, which those sites do not call). V2 then drives each #377 helper, the descriptor
    stack and _opf_check._close_fd_quietly with a real second thread that takes the freed number before
    the close raises: that thread must still own it, and RECLOSE (by REUSE) and PROBE (by PROBE alone) must
    turn each V2 vector red. Last, a forced reuse-setup failure must turn a V1 row red by REUSE.
    Returns 0 clean, 1 on a failing check, 2 on a harness error."""
    import contextlib
    import errno
    import functools
    import io
    import shutil
    import tempfile
    import threading
    import types
    from unittest import mock
    import check_opf_prompt_pack
    import _opf_adopt_observe
    journal = _opf_store._journal
    real_open, real_dup, real_close, real_fstat, real_read = os.open, os.dup, os.close, os.fstat, os.read
    ANY = object()
    state = types.SimpleNamespace(opened=[], target=None, injected=None, err=errno.EIO, reuse=False,
                                  number=None, closes=0, probes=0, unrelated=None, want=None,
                                  break_reuse=False)

    def propagating(fd):
        """A P1 propagating close (the MASK and CALLER-FRAME stand-in): one os.close, its error raised."""
        os.close(fd)

    def quietly(fd):
        """A P1 quiet close (the SWALLOW and CALLER-FRAME stand-in): one os.close, its error swallowed."""
        with contextlib.suppress(OSError):
            os.close(fd)

    def reclose(fd, quiet):
        """RECLOSE: the pre-P1 confirm-then-reclose recovery, inlined so the flip outlives #378's _journal."""
        try:
            os.close(fd)
            return
        except OSError as exc:
            first = exc
        try:
            os.fstat(fd)
        except OSError:
            if quiet:
                return
            raise first
        with contextlib.suppress(OSError):
            os.close(fd)
        if not quiet:
            raise first

    def reclose_exc_safe(fd):
        tb = sys.exc_info()[2]
        reclose(fd, tb is not None and tb.tb_frame is sys._getframe(1))

    def probe(fd, quiet):
        """PROBE (#378's P flip): one os.close, then an fstat of the number when it fails, never a second
        close; the error rule is the helper's own, so the flip is red by PROBE alone."""
        try:
            os.close(fd)
            return
        except OSError as exc:
            first = exc
        with contextlib.suppress(OSError):
            os.fstat(fd)
        if not quiet:
            raise first

    def probe_exc_safe(fd):
        tb = sys.exc_info()[2]
        probe(fd, tb is not None and tb.tb_frame is sys._getframe(1))

    class _Body(BaseException):
        """The body's in-flight exception; a BaseException, so no site handler maps it."""

    class _CallerHandled(Exception):
        """The exception a CALLER is handling while it calls the site normally."""

    def arm(fd=ANY):
        """Make the next close (of `fd`, when given) release the descriptor and then raise."""
        state.target = fd

    def _open(*args, **kwargs):
        fd = real_open(*args, **kwargs)
        state.opened.append(fd)
        return fd

    def _dup(fd):
        new = real_dup(fd)
        state.opened.append(new)
        return new

    def _close(fd):
        if state.number is not None and fd == state.number:
            state.closes += 1                 # a second close of the injected number (PROBE)
        if state.target is ANY or (state.target is not None and state.target == fd):
            state.target = None
            real_close(fd)                    # RELEASED first, as close(2) does on Linux
            state.injected = OSError(state.err, "injected released-close failure")
            if state.reuse:
                try:                          # V1: an unrelated file takes the freed number
                    os.dup2(-1 if state.break_reuse else state.unrelated, fd)
                except OSError:
                    pass                      # the reuse setup failed: p1_fails reports it, never a pass
                else:
                    state.number = fd
            raise state.injected
        real_close(fd)

    def _fstat(fd, *args, **kwargs):
        if state.number is not None and fd == state.number:
            state.probes += 1                 # an fstat probe of the injected number (PROBE)
        return real_fstat(fd, *args, **kwargs)

    def holds_unrelated(fd):
        """Whether fd still names the unrelated file a V1 fault put on it (only then is it closed here)."""
        try:
            now = real_fstat(fd)
        except OSError:
            return False
        return (now.st_dev, now.st_ino) == (state.want.st_dev, state.want.st_ino)

    def p1_fails():
        """The REUSE and PROBE failures of the run just made; releases the reused number. A run that asked
        for reuse whose unrelated file never reached the injected number is red by REUSE: without it the
        REUSE and PROBE assertions have nothing to observe, so the row proves nothing."""
        fails = []
        if state.number is None:
            if state.reuse:
                fails.append("reuse: the reuse setup failed (no unrelated file was put on the injected "
                             "number{})".format("" if state.injected is not None else "; nothing fired"))
            return fails
        if state.closes or state.probes:
            fails.append("probe: the injected number saw {} more close(s) and {} fstat(s)".format(
                state.closes, state.probes))
        if not holds_unrelated(state.number):
            try:
                real_fstat(state.number)
            except OSError:
                fails.append("reuse: the unrelated descriptor on the reused number was closed")
            else:                             # another file holds the number now: never closed here
                fails.append("reuse: the reused number no longer holds the unrelated file")
        else:
            real_close(state.number)          # still the unrelated file this run put there
        return fails

    def run(call, reuse=False, err=errno.EIO):
        """(the exception `call` raised, or None; the descriptors it opened that are still open; its REUSE
        and PROBE failures)."""
        state.opened, state.target, state.injected = [], None, None
        state.reuse, state.err, state.number, state.closes, state.probes = reuse, err, None, 0, 0
        got = None
        os.open, os.dup, os.close, os.fstat = _open, _dup, _close, _fstat
        os.supports_dir_fd.add(_open)         # _containment.probe keys off os.open's dir_fd support
        try:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                call()
        except BaseException as exc:  # noqa: BLE001  graded by the vector
            got = exc
        finally:
            os.supports_dir_fd.discard(_open)
            os.open, os.dup, os.close, os.fstat = real_open, real_dup, real_close, real_fstat
            state.target = None
        left = []
        for fd in sorted(set(state.opened)):
            if fd == state.number and holds_unrelated(fd):
                continue                      # the unrelated file: graded by REUSE, never as a survivor
            try:
                real_fstat(fd)
            except OSError:
                continue
            left.append(fd)
        for fd in left:                       # a failing vector leaks; release so the suite stays clean
            with contextlib.suppress(OSError):
                real_close(fd)
        return got, left, p1_fails()

    def raiser(body, result=None):
        """A stand-in that arms the next close, then raises the body exception or returns `result`."""
        def hook(*args, **kwargs):
            arm()
            if body is not None:
                raise body
            return result
        return hook

    def first_call(obj, name, body, real):
        """Patch obj.name so its first call arms the next close and raises `body` (or delegates)."""
        calls = []

        def hook(*args, **kwargs):
            calls.append(None)
            if len(calls) == 1:
                arm()
                if body is not None:
                    raise body
            return real(*args, **kwargs)
        return mock.patch.object(obj, name, hook)

    def then_arm(obj, name, body):
        """Patch obj.name so it runs, THEN arms the next close and raises `body` (or returns its result)."""
        real = getattr(obj, name)

        def hook(*args, **kwargs):
            result = real(*args, **kwargs)
            arm()
            if body is not None:
                raise body
            return result
        return mock.patch.object(obj, name, hook)

    def dirfd(path):
        return real_open(str(path), os.O_RDONLY | os.O_DIRECTORY)

    tmp = Path(tempfile.mkdtemp(prefix="opf-close-exc-safe-")).resolve()
    res = types.SimpleNamespace(status=_opf_store.RESOLVED, store_root=tmp, machine_rel="m",
                                pointer_source="default")

    # Each case maps a body exception (None: the normal path) to the call a vector runs.
    def c_store_subdirs(body):
        def call():
            root = dirfd(tmp)
            try:
                with mock.patch.object(_opf_store, "_list_real_subdirs", raiser(body, [])):
                    _opf_store._immediate_subdirs(root, ".working")
            finally:
                real_close(root)
        return call

    def c_store_load_manifest(body):
        def call():
            with mock.patch.object(_opf_store, "_read_toml_contained", raiser(body, {})):
                _opf_store.load_manifest(res)
        return call

    def c_views_render(body):
        def call():
            with mock.patch.object(_opf_views, "_render_resolved", raiser(body, 0)):
                _opf_views._render_resolved_store(tmp, res, True)
        return call

    def c_views_write_temp(body):
        def call():
            root = dirfd(tmp)
            try:
                with mock.patch.object(_opf_views, "_WRITE_GATE_COMPOSED", True), \
                        then_arm(journal, "_write_all", body):
                    _opf_views._write_contained(root, "m/VIEW.md", "view\n", False)
            finally:
                with contextlib.suppress(FileNotFoundError):
                    os.unlink(str(tmp / "m" / "VIEW.md"))
                real_close(root)
        return call

    def c_adopt_apply_probe(body):
        def call():
            with mock.patch.object(_opf_store, "discover_machine_store", raiser(body, ("present", None, None))):
                _opf_adopt_apply._default_store_present_without_manifest(tmp)
        return call

    def c_adopt_apply_transaction(body):
        def call():
            product = Path(tempfile.mkdtemp(dir=str(tmp))).resolve()
            (product / "legacy").mkdir()
            (product / "legacy" / "RULES.md").write_bytes(b"old rules\n")
            digest = "sha256:" + _opf_adopt_apply._sha256(b"old rules\n")
            real_root = _opf_adopt_apply._open_product_root

            def open_root(product_root):
                fd = real_root(product_root)
                arm(fd)                       # only the product-root descriptor's close is injected
                return fd

            def compose(ops):
                ops.preserve("legacy/RULES.md", digest)
                if body is not None:
                    raise body
            try:
                with mock.patch.object(_opf_adopt_apply, "_open_product_root", open_root):
                    _opf_adopt_apply.run_adopt_transaction(
                        str(product), "adopt-20260101T000000Z-0123456789abcdef", compose)
            finally:
                shutil.rmtree(str(product), ignore_errors=True)
        return call

    def c_adopt_observe_open_directory(body):
        def call():
            parent = dirfd(tmp)
            try:
                with mock.patch.object(_opf_adopt_observe.planning, "_stamp", raiser(body)):
                    _opf_adopt_observe._open_directory(parent, "other")
            finally:
                real_close(parent)
        return call

    def c_adopt_observe_put(body):
        def call():
            parent = dirfd(tmp)
            name = "put-" + os.urandom(6).hex()
            try:
                _opf_adopt_observe._put(parent, name, b"data", types.SimpleNamespace(left=raiser(body)))
            finally:
                with contextlib.suppress(FileNotFoundError):
                    os.unlink(name, dir_fd=parent)
                real_close(parent)
        return call

    def c_adopt_observe_quarantine(body):
        def call():
            owners = []
            try:
                with _opf_adopt_observe._quarantine(str(tmp / "q"), owners):
                    arm()                     # the stack's first exit callback closes the quarantine fd
                    if body is not None:
                        raise body
            finally:
                for owner in owners:
                    with contextlib.suppress(Exception):
                        owner.finish(False)
        return call

    def c_changelog(body):
        def call():
            with mock.patch.object(_opf_store, "_read_toml_contained", raiser(body, None)):
                _opf_changelog._load_inputs(res, tmp)
        return call

    def c_absorb(body):
        def call():
            with mock.patch.object(_opf_store, "_read_toml_contained", raiser(body, {})):
                _opf_absorb._load_done(res, frozenset())
        return call

    def c_observe(body):
        def call():
            with first_call(os, "fstat", body, _fstat):
                _opf_observe._worktree_open_succeeds(tmp / "lease")
        return call

    def c_write_guard_read(body):
        def call():
            parent = dirfd(tmp)
            try:
                with first_call(os, "read", body, real_read):
                    _opf_write_guard.read_lease_payload(parent, "lease")
            finally:
                real_close(parent)
        return call

    def c_write_guard_acquire(body):
        def call():
            root = dirfd(tmp)
            try:
                with then_arm(journal, "_write_all", body):
                    _opf_write_guard.acquire_lease(root, "m", "render")
            finally:
                with contextlib.suppress(FileNotFoundError):
                    os.unlink(str(tmp / "m" / _opf_check.LEASE_NAME))
                real_close(root)
        return call

    def c_prompt_pack(body):
        if body is None:
            # The site's normal path hands the fd to fdopen, so its normal vector drives the helper itself.
            def call():
                fd = os.open(str(tmp / "lease"), os.O_RDONLY)
                arm()
                check_opf_prompt_pack._close_fd_exc_safe(fd)
            return call
        real_require = check_opf_prompt_pack._require

        def require(condition, *args, **kwargs):
            if not condition:
                arm()
                try:
                    real_require(condition, *args, **kwargs)
                except check_opf_prompt_pack._Refusal as exc:
                    body.want = exc           # the site's own refusal is the exception in flight
                    raise

        def call():
            with mock.patch.object(check_opf_prompt_pack, "_require", require):
                check_opf_prompt_pack._read_regular(str(tmp / "big"), 8, "vector")
        return call

    def c_opf_same_root(body):
        def call():
            root = dirfd(tmp)
            calls = []

            def fstat(fd, *args, **kwargs):
                calls.append(None)
                if len(calls) == 1:
                    arm()
                elif len(calls) == 2 and body is not None:
                    raise body
                return _fstat(fd, *args, **kwargs)
            try:
                with mock.patch.object(os, "fstat", fstat):
                    _init_same_root(tmp, root)
            finally:
                real_close(root)
        return call

    def c_opf_init_create(body):
        def call():
            root = dirfd(tmp)
            try:
                with then_arm(journal, "_recreate_file", body):
                    _init_create(root, "created.txt", b"x\n")
            finally:
                with contextlib.suppress(FileNotFoundError):
                    os.unlink(str(tmp / "created.txt"))
                real_close(root)
        return call

    def c_views_render_root_refused(body):
        def call():
            real_root = _opf_store._open_root_fd
            calls = []

            def open_root(path):
                calls.append(path)
                if len(calls) == 2:           # the product root: refused, so its handler closes the store fd
                    arm()
                    raise FileNotFoundError(errno.ENOENT, "self-test: product root refused")
                return real_root(path)
            with mock.patch.object(_opf_store, "_open_root_fd", open_root):
                code = _opf_views._render_resolved_store(tmp, res, True)
            if code != _opf_views.EXIT_CANNOT_EVALUATE:
                raise RuntimeError("the refused product root returned {!r}".format(code))
        return call

    def c_views_restore(which):
        """_restore_preimages with the `which`-th root it opens armed: 1, the store root; 2, the product root."""
        def case(body):
            def call():
                real_root = _opf_store._open_root_fd
                opened = []

                def open_root(path):
                    fd = real_root(path)
                    opened.append(fd)
                    if len(opened) == which:
                        arm(fd)
                    return fd
                with mock.patch.object(_opf_store, "_open_root_fd", open_root):
                    failed = _opf_views._restore_preimages(tmp, res, {})
                if failed:
                    raise RuntimeError("the empty rollback reported {!r}".format(failed))
            return call
        return case

    def c_write_guard_lease_held(body):
        def call():
            parent = dirfd(tmp)
            try:
                with then_arm(os, "open", None):  # the lease open arms its own close
                    message = _opf_write_guard.lease_held_message(parent, "held.toml", "m/held.toml", "render")
            finally:
                real_close(parent)
            if "held by 'vector'" not in message:
                raise RuntimeError("the held-lease message lost its holder ({!r})".format(message))
        return call

    def c_opf_init_repo(body):
        def call():
            real_nofollow = _opf_store._open_dir_nofollow

            def nofollow(path):
                fd = real_nofollow(path)
                arm(fd)                       # only the repository-root probe's close is injected
                return fd
            with mock.patch.object(_opf_store, "_open_dir_nofollow", nofollow):
                _init_repo(repo)
        return call

    # (site, case, vector kinds, the flips it runs under: None for every flip).
    every = ("body", "normal", "caller")
    journal_flips = (None, "RECLOSE", "PROBE")
    cases = (
        ("_opf_store._immediate_subdirs finally", c_store_subdirs, every, None),
        ("_opf_store.load_manifest finally", c_store_load_manifest, every, None),
        ("_opf_views._render_resolved_store two-close finally", c_views_render, every, None),
        ("_opf_views._write_contained temp-file finally", c_views_write_temp, every, None),
        ("_opf_adopt_apply._default_store_present_without_manifest finally", c_adopt_apply_probe, every, None),
        ("_opf_adopt_apply.run_adopt_transaction product-root finally", c_adopt_apply_transaction, every, None),
        # Its normal path returns the fd unclosed, so it has a body vector only. Its close is
        # _journal._close_fd_quietly (#377 routes it there); MASK covers it as the journal helper propagating.
        ("_opf_adopt_observe._open_directory except", c_adopt_observe_open_directory, ("body",), None),
        ("_opf_adopt_observe._put descriptor-stack exit callback", c_adopt_observe_put, every, None),
        ("_opf_adopt_observe._quarantine with-stack exit callback", c_adopt_observe_quarantine, every, None),
        ("_opf_changelog._load_inputs finally", c_changelog, every, None),
        ("_opf_absorb._load_done finally", c_absorb, every, None),
        ("_opf_observe._worktree_open_succeeds finally", c_observe, every, None),
        ("_opf_write_guard.read_lease_payload finally", c_write_guard_read, every, None),
        ("_opf_write_guard.acquire_lease payload-fd finally", c_write_guard_acquire, every, None),
        ("check_opf_prompt_pack._read_regular finally", c_prompt_pack, every, None),
        ("opf._init_same_root finally", c_opf_same_root, every, None),
        ("opf._init_create finally", c_opf_init_create, every, None),
        # The other five closes #377 routes through _journal's helpers: four quiet teardown closes
        # (_journal._close_fd_quietly) and _init_repo's normal-path probe close (_journal._close_fd_propagating).
        ("_opf_views._render_resolved_store product-root refusal", c_views_render_root_refused, ("quiet",),
         journal_flips),
        ("_opf_views._restore_preimages finally, store-root close", c_views_restore(1), ("quiet",),
         journal_flips),
        ("_opf_views._restore_preimages finally, product-root close", c_views_restore(2), ("quiet",),
         journal_flips),
        ("_opf_write_guard.lease_held_message finally", c_write_guard_lease_held, ("quiet",), journal_flips),
        ("opf._init_repo repository-root probe close", c_opf_init_repo, ("normal", "caller"), journal_flips),
    )

    def chain(exc):
        seen = []
        while exc is not None and all(exc is not e for e in seen):
            seen.append(exc)
            exc = exc.__cause__ if exc.__cause__ is not None else exc.__context__
        return seen

    def vector(kind, case, reuse, err):
        """The failure messages of one vector run (empty: green), each prefixed by the assertion it broke."""
        body = _Body("vector-original") if kind == "body" else None
        call = case(body)
        if kind == "caller":
            inner = call

            def call():
                try:
                    raise _CallerHandled("an exception the caller is handling")
                except _CallerHandled:
                    inner()
        got, left, p1 = run(call, reuse, err)
        fails = []
        if state.injected is None:
            fails.append("injection: the close injection never fired")
        elif kind == "body":
            if got is not getattr(body, "want", body):
                fails.append("body: the in-flight exception was replaced (got {!r})".format(got))
        elif kind == "quiet":
            if got is not None:
                fails.append("quiet: the teardown close did not complete quietly (got {!r})".format(got))
        elif all(e is not state.injected for e in chain(got)):
            fails.append("{}: the close error was not raised (got {!r})".format(kind, got))
        if left:
            fails.append("descriptors: {} survived".format(left))
        return fails + p1

    def fix1_close(fd):
        """#377 fix 1's test: ANY exception in sys.exc_info() counts as in flight."""
        if sys.exc_info()[1] is not None:
            quietly(fd)
        else:
            propagating(fd)

    def graded(fails, wanted, flip):
        """A vector is green when nothing is wanted; else red by the wanted assertion, and RECLOSE may also
        break PROBE, since the old body probes the number before it closes it again."""
        if wanted is None:
            return not fails
        allowed = (wanted, "probe") if flip == "RECLOSE" else (wanted,)
        return (any(f.startswith(wanted + ":") for f in fails)
                and all(f.split(":", 1)[0] in allowed for f in fails))

    def stack_close(fd):
        stack = _opf_adopt_observe._UnwindingStack()
        stack.push(functools.partial(_opf_store._close_fd_on_exit, fd))
        stack.close()

    # V2's helpers, each called through its module so a flip applies: (name, call, propagates on raise).
    v2_helpers = (
        ("_opf_store._close_fd_exc_safe", lambda fd: _opf_store._close_fd_exc_safe(fd), True),
        ("_opf_store._close_fd_on_exit", lambda fd: _opf_store._close_fd_on_exit(fd, None, None, None), True),
        ("check_opf_prompt_pack._close_fd_exc_safe",
         lambda fd: check_opf_prompt_pack._close_fd_exc_safe(fd), True),
        ("_opf_adopt_observe._UnwindingStack.close", stack_close, True),
        ("_opf_check._close_fd_quietly", lambda fd: _opf_check._close_fd_quietly(fd), False),
    )

    def second_thread(helper, propagates, err):
        """One V2 run (modelled on T-f8-2): the fault releases the number and sets an Event; a second thread
        opens a file on that number (forced with dup2 when its open lands elsewhere) and replies; only then
        does the close raise. Returns the failure messages (empty: green)."""
        fd = real_open(str(tmp / "lease"), os.O_RDONLY)
        released, replied = threading.Event(), threading.Event()
        seen = types.SimpleNamespace(injected=None, closes=0, probes=0, want=None, error=None)

        def take():
            if not released.wait(10):
                return
            try:
                new = real_open(str(tmp / "big"), os.O_RDONLY)
                if new != fd:
                    os.dup2(new, fd)
                    real_close(new)
                seen.want = real_fstat(fd)
            except OSError as exc:
                seen.error = exc
            finally:
                replied.set()

        def close(n):
            if n == fd:
                if seen.injected is not None:
                    seen.closes += 1
                else:
                    real_close(n)
                    seen.injected = OSError(err, "injected released-close failure; a second thread took it")
                    released.set()
                    replied.wait(10)
                    raise seen.injected
            real_close(n)

        def fstat(n, *args, **kwargs):
            if n == fd and seen.injected is not None:
                seen.probes += 1
            return real_fstat(n, *args, **kwargs)

        thread = threading.Thread(target=take, name="opf-close-v2-reuse", daemon=True)
        thread.start()
        got = None
        os.close, os.fstat = close, fstat
        try:
            helper(fd)
        except OSError as exc:
            got = exc
        finally:
            os.close, os.fstat = real_close, real_fstat
            released.set()                    # never leave the thread waiting
            thread.join(10)
        if seen.injected is None or seen.want is None:
            with contextlib.suppress(OSError):
                real_close(fd)
            return ["injection: the second thread never took the number ({!r})".format(seen.error)]
        fails = []
        if got is not (seen.injected if propagates else None):
            fails.append("rule: the helper's error rule broke (got {!r})".format(got))
        if seen.closes or seen.probes:
            fails.append("probe: the released number saw {} more close(s) and {} fstat(s)".format(
                seen.closes, seen.probes))
        try:
            now = real_fstat(fd)
        except OSError:
            fails.append("reuse: the second thread's descriptor was closed")
        else:
            if (now.st_dev, now.st_ino) != (seen.want.st_dev, seen.want.st_ino):
                fails.append("reuse: the second thread's number no longer holds its file")   # never closed here
            else:
                real_close(fd)                # still the second thread's file
        return fails

    flips = (
        (None, ()),
        ("MASK", ((_opf_store, "_close_fd_exc_safe", propagating),
                  (check_opf_prompt_pack, "_close_fd_exc_safe", propagating),
                  (_opf_store, "_close_fd_on_exit", lambda fd, *exc: propagating(fd)),
                  (journal, "_close_fd_quietly", propagating))),
        ("SWALLOW", ((_opf_store, "_close_fd_exc_safe", quietly),
                     (check_opf_prompt_pack, "_close_fd_exc_safe", quietly),
                     (_opf_store, "_close_fd_on_exit", lambda fd, *exc: quietly(fd)))),
        ("CALLER-FRAME", ((_opf_store, "_close_fd_exc_safe", fix1_close),
                          (check_opf_prompt_pack, "_close_fd_exc_safe", fix1_close),
                          (_opf_store, "_close_fd_on_exit", lambda fd, *exc: fix1_close(fd)),
                          (_opf_adopt_observe._UnwindingStack, "close",
                           lambda stack: stack.__exit__(*sys.exc_info())))),
        ("RECLOSE", ((_opf_store, "_close_fd_exc_safe", reclose_exc_safe),
                     (check_opf_prompt_pack, "_close_fd_exc_safe", reclose_exc_safe),
                     (_opf_store, "_close_fd_on_exit",
                      lambda fd, exc_type, exc, tb: reclose(fd, exc is not None)),
                     (_opf_check, "_close_fd_quietly", lambda fd: reclose(fd, True)),
                     (journal, "_close_fd_quietly", lambda fd: reclose(fd, True)),
                     (journal, "_close_fd_propagating", lambda fd: reclose(fd, False)))),
        ("PROBE", ((_opf_store, "_close_fd_exc_safe", probe_exc_safe),
                   (check_opf_prompt_pack, "_close_fd_exc_safe", probe_exc_safe),
                   (_opf_store, "_close_fd_on_exit", lambda fd, exc_type, exc, tb: probe(fd, exc is not None)),
                   (_opf_check, "_close_fd_quietly", lambda fd: probe(fd, True)),
                   (journal, "_close_fd_quietly", lambda fd: probe(fd, True)),
                   (journal, "_close_fd_propagating", lambda fd: probe(fd, False)))),
    )
    # The one assertion each flip must break, per vector kind; every other vector must stay green.
    reds = {None: {}, "MASK": {"body": "body"}, "SWALLOW": {"normal": "normal", "caller": "caller"},
            "CALLER-FRAME": {"caller": "caller"}}
    # RECLOSE breaks REUSE (PROBE may break with it) on every V1 vector, the journal-routed sites' included
    # (the flip puts the pre-P1 body back in _journal's helpers too); PROBE breaks PROBE alone on every one.
    reds["RECLOSE"] = dict(body="reuse", normal="reuse", caller="reuse", quiet="reuse")
    reds["PROBE"] = dict(body="probe", normal="probe", caller="probe", quiet="probe")
    failures = []
    checks = 0
    try:
        for sub in (".working", "other", "m", "q"):
            (tmp / sub).mkdir()
        (tmp / "lease").write_bytes(b"payload")
        (tmp / "big").write_bytes(b"x" * 64)
        (tmp / "unrelated").write_bytes(b"unrelated")
        (tmp / "held.toml").write_bytes(
            b'holder = "vector"\noperation = "render"\nacquired_at = "2026-01-01T00:00:00Z"\n')
        # The journal-routed sites resolve the helpers a flip patches through this one _journal module.
        if _opf_views._journal is not journal or _opf_adopt_observe.store._journal is not journal:
            raise RuntimeError("a journal-routed site does not resolve the patched _journal module")
        repo = tmp / "repo"
        repo.mkdir()
        git = _opf_observe._git_path()
        made = None if git is None else _opf_observe._run_git(
            git, repo, ["-c", "init.templateDir=", "-c", "init.defaultBranch=main", "init", "-q"])
        if made is None or not made.completed or made.rc != 0:
            raise RuntimeError("the fixture worktree could not be initialized ({})".format(
                "git not found" if made is None else made.err))
        state.unrelated = real_open(str(tmp / "unrelated"), os.O_RDONLY)
        state.want = real_fstat(state.unrelated)
        for flip, patches in flips:
            with contextlib.ExitStack() as patched:
                for obj, name, value in patches:
                    patched.enter_context(mock.patch.object(obj, name, value))
                for site, case, kinds, only in cases:
                    if only is not None and flip not in only:
                        continue
                    for kind in kinds:
                        for err in (errno.EINTR, errno.EIO):
                            fails = vector(kind, case, True, err)
                            wanted = reds[flip].get(kind)
                            ok = graded(fails, wanted, flip)
                            checks += 1
                            if not ok:
                                failures.append(
                                    (flip or "unflipped", kind, errno.errorcode[err], site, fails))
                            print("  {} {} {} {} [{}]: {}".format(
                                "PASS" if ok else "FAIL", flip or "unflipped", kind, errno.errorcode[err],
                                site, "; ".join(fails) if fails else "green"))
        for flip in (None, "RECLOSE", "PROBE"):
            with contextlib.ExitStack() as patched:
                for obj, name, value in dict(flips)[flip]:
                    patched.enter_context(mock.patch.object(obj, name, value))
                for name, helper, propagates in v2_helpers:
                    for err in (errno.EINTR, errno.EIO):
                        fails = second_thread(helper, propagates, err)
                        ok = graded(fails, {"RECLOSE": "reuse", "PROBE": "probe"}.get(flip), flip)
                        checks += 1
                        if not ok:
                            failures.append((flip or "unflipped", "V2", errno.errorcode[err], name, fails))
                        print("  {} {} V2 {} [{}]: {}".format(
                            "PASS" if ok else "FAIL", flip or "unflipped", errno.errorcode[err], name,
                            "; ".join(fails) if fails else "green"))
        # A V1 row whose reuse setup fails (the unrelated file never reaches the number: here dup2 is handed
        # an invalid source) must be red by REUSE, never pass because REUSE and PROBE had nothing to observe.
        for err in (errno.EINTR, errno.EIO):
            state.break_reuse = True
            try:
                fails = vector("body", c_store_subdirs, True, err)
            finally:
                state.break_reuse = False
            ok = any(f.startswith("reuse:") for f in fails)
            checks += 1
            if not ok:
                failures.append(("forced-reuse-setup-failure", "body", errno.errorcode[err],
                                 "_opf_store._immediate_subdirs finally", fails))
            print("  {} forced-reuse-setup-failure body {} [_opf_store._immediate_subdirs finally]: {}".format(
                "PASS" if ok else "FAIL", errno.errorcode[err], "; ".join(fails) if fails else "green"))
    except Exception as exc:  # noqa: BLE001  a fixture that cannot be built is a harness error, never a pass
        print("opf close exc-safe vectors self-test: harness error ({!r})".format(exc), file=sys.stderr)
        return EXIT_MALFORMED
    finally:
        if state.unrelated is not None:
            real_close(state.unrelated)
        shutil.rmtree(str(tmp), ignore_errors=True)
    if failures:
        print("opf close exc-safe vectors self-test: FAIL (failing {} of {} checks): {!r}".format(
            len(failures), checks, failures[:4]), file=sys.stderr)
        return EXIT_FINDING
    print("opf close exc-safe vectors self-test: PASS ({} checks)".format(checks))
    return EXIT_OK


# Registered helper self-tests, run by `opf.py --self-test`. Each is (label, callable) returning a
# 0/1/2 exit code (0 clean, 1 finding, 2 cannot-evaluate). Later units append their own helper here.
# Built by a function rather than a module-level tuple because the _opf_* helpers it references are bound by
# _bootstrap() inside main(), not at module import; it is called after _bootstrap() has run.
def _self_tests():
    """Return the registered (label, callable) helper self-tests run by `opf.py --self-test`. Called after
    _bootstrap() has bound the _opf_* helpers, so every referenced helper is present."""
    return (
    ("opf-store", _opf_store.self_test),
    ("opf-schema", _opf_schema.self_test),
    ("opf-release", _opf_release.self_test),
    ("opf-worklog", _opf_worklog.self_test),
    ("opf-changelog", _opf_changelog.self_test),
    ("opf-emit", _opf_emit.self_test),
    ("opf-views", _opf_views.self_test),
    ("opf-observe", _opf_observe.self_test),
    ("opf-absorb", _opf_absorb.self_test),
    ("opf-record", _opf_record.self_test),
    ("opf-adopt-apply", _opf_adopt_apply.self_test),
    ("opf-fuzz", _opf_fuzz.self_test),
    ("opf-check", _opf_check.self_test),
    ("opf-journal", _opf_store._journal.self_test),   # #378: the _close_fd_yielding vectors
    ("opf-watchdog-isolation", _watchdog_isolation_self_test),
    ("opf-watchdog-regressions", _watchdog_regression_self_test),
    ("opf-aggregator", _aggregator_self_test),
    ("opf-retained-close-offpath", _retained_close_offpath_self_test),
    ("opf-close-exc-safe-vectors", _close_exc_safe_vectors_self_test),
    ("opf-cli", _cli_self_test),
)

# The spec's command vocabulary (spec 1). Each lands in its own unit; until then a verb fails closed.
KNOWN_VERBS = ("init", "adopt", "import", "doctor", "render", "migrate", "sync", "upgrade", "absorb",
               "record")


# Helper self-tests that pin sys.set_int_max_str_digits(4300) inside a fixture and MUST restore the ambient
# value in a finally (the round-7 int-limit hermeticity work). run_self_tests guards that RESTORE half below.
_INT_LIMIT_SELF_TESTS = frozenset({"opf-release", "opf-emit", "opf-schema", "opf-fuzz"})


def run_self_tests(tests=None):
    """Keep caller HOME/XDG out of fixture reads, including in-process production helpers."""
    import tempfile
    from unittest.mock import patch
    with tempfile.TemporaryDirectory(prefix="opf-selftest-home-") as home:
        with patch.dict(os.environ, HOME=home, XDG_CONFIG_HOME=home,
                        GIT_CONFIG_NOSYSTEM="1"):
            return run_self_tests_isolated(tests)


def run_self_tests_isolated(tests=None):
    """Run every registered helper self-test in order, forwarding each result. The aggregate exit code
    is the WORST outcome (2 cannot-evaluate > 1 finding > 0 clean): one degraded or failing helper fails
    the whole leg, never masked by a later clean one. With no explicit `tests`, the registered set is built
    by _self_tests() at call time (after _bootstrap() has bound the _opf_* helpers), never a module-level
    default that would need those helpers imported at module top.

    Int-limit hermeticity guard (finding 8-4): each helper in _INT_LIMIT_SELF_TESTS pins the int-string
    conversion limit to 4300 inside its fixtures and must RESTORE the ambient value afterward. That restore
    had no fails-if-reverted check: under the DEFAULT ambient (already 4300) a dropped restore leaves 4300
    and is invisible. So around each such helper we set a distinct SENTINEL limit (!= 4300 and != the real
    ambient) and, after it runs, require the limit to STILL be that sentinel before restoring the real
    ambient; a dropped restore in any of those helpers leaves 4300 != sentinel and fails the leg closed.
    These helpers are hermetic w.r.t. the ambient int-limit by construction (they pin their own 4300), so
    running them under the sentinel is exactly the hostile-ambient contract they already satisfy."""
    if tests is None:
        tests = _self_tests()
    worst = EXIT_OK
    _idlimit_orig = sys.get_int_max_str_digits()
    _idlimit_sentinel = 271828 if _idlimit_orig != 271828 else 314159   # distinct from 4300 AND from ambient
    for label, fn in tests:
        print("== opf self-test: {} ==".format(label))
        _guard_idlimit = label in _INT_LIMIT_SELF_TESTS
        if _guard_idlimit:
            sys.set_int_max_str_digits(_idlimit_sentinel)
            try:
                code = fn()
            finally:
                _idlimit_after = sys.get_int_max_str_digits()
                sys.set_int_max_str_digits(_idlimit_orig)   # restore the real ambient regardless of outcome
            if _idlimit_after != _idlimit_sentinel:
                print("opf self-test: {} left sys.get_int_max_str_digits at {} (expected the sentinel {}); a "
                      "dropped int-limit restore is a hermeticity leak, failing closed (finding 8-4)".format(
                          label, _idlimit_after, _idlimit_sentinel), file=sys.stderr)
                worst = EXIT_MALFORMED
        else:
            code = fn()
        if not (type(code) is int and code in (EXIT_OK, EXIT_FINDING, EXIT_MALFORMED)):
            # A helper whose return is not an int of exactly {0,1,2} is itself a fault: fail closed (the
            # worst outcome) rather than letting an unrecognized code read as clean. `type(code) is int`
            # deliberately EXCLUDES bool (a subclass of int, where False == 0 and True == 1) and float
            # (0.0 == 0), so a helper returning False or 0.0 can never be admitted as a clean pass.
            print("opf self-test: {} returned out-of-range code {!r}; failing closed".format(label, code),
                  file=sys.stderr)
            worst = EXIT_MALFORMED
        elif code == EXIT_MALFORMED:
            worst = EXIT_MALFORMED
        elif code == EXIT_FINDING and worst != EXIT_MALFORMED:
            worst = EXIT_FINDING
    return worst


def main(argv=None):
    # Guarded helper bootstrap FIRST: a broken or partial install (an unimportable/unreadable _opf_* helper)
    # maps to a located cannot-evaluate (exit 2), never an uncaught ImportError escaping as Python's default
    # exit 1 that a direct `opf render --check` would read as a false drift. Idempotent, so the repeated
    # main() calls in the CLI self-test cost nothing once the helpers are loaded.
    rc = _bootstrap()
    if rc != EXIT_OK:
        return rc
    args = list(sys.argv[1:] if argv is None else argv)
    if args == ["--self-test"]:
        return run_self_tests()
    if not args or args[0] in ("-h", "--help"):
        print(__doc__, file=sys.stderr)
        return EXIT_MALFORMED
    verb = args[0]
    rest = args[1:]
    if verb == "render":
        return _cmd_render(rest)
    if verb == "doctor":
        return _cmd_doctor(rest)
    if verb == "init":
        return _cmd_init(rest)
    if verb == "upgrade":
        return _cmd_upgrade(rest)
    if verb == "import":
        return _cmd_import(rest)
    if verb == "absorb":
        return _cmd_absorb(rest)
    if verb == "record":
        return _cmd_record(rest)
    if verb == "adopt":
        return _cmd_adopt(rest)
    if verb in KNOWN_VERBS:
        # A recognized verb whose unit has not landed: fail closed (exit 2), never a silent success, so
        # a stub is never mistaken for a completed operation.
        print("opf {}: not yet implemented in this build (fail-closed)".format(verb), file=sys.stderr)
        return EXIT_MALFORMED
    print("opf: unknown verb {!r}; known verbs: {}".format(verb, ", ".join(KNOWN_VERBS)), file=sys.stderr)
    return EXIT_MALFORMED


if __name__ == "__main__":
    sys.exit(main())
