#!/usr/bin/env python3
"""The shared fail-closed namespace-mutation predicate, the ONE source for two gates.

tools/check_manifest.py holds the D-400 special-file precheck copies (tools/_gen_common.py and
opf/tools/_containment.py) to this predicate as DEFENCE IN DEPTH ONLY: a module NAME whose value a
syntactic scan wants to trust is trusted only while the module holds NO reference through which a
binding could change invisibly to that scan, and no spelling list makes that trust COMPLETE (the
RESIDUAL below). Extracted into this one module because two statements of the same rule had
already diverged once; then widened; then made POSITIVE (each earlier version
enumerated bad spellings, and each review found siblings outside the list -- aliases of
the banned callables, mapping methods such as __dict__.update, frame globals, getattr on
builtins, from-imports and accessor calls -- so the rule was inverted instead of extending
the list); and then DEMOTED (reviews of the positive rule
reached the same capability through attribute chains and source evaluators the
inverted rule still does not name, so tools/check_release_cut.py no longer rests ANY
exemption or package-member proof on this predicate; it counts every os.open site and
refuses every package-member proof instead, and only this gate's copy comparison keeps the
predicate, as one defensive layer among its others, never as a proof).
The positive rule set, applied in EVERY scope, Load positions included:
- no Load, in ANY position (not only as a call), of a REFLECTION NAME
  (REFLECTION_NAME_LOADS below);
- no Load of an attribute named __dict__, __setattr__, __delattr__, f_globals, f_locals,
  modules, _getframe, setitem or delitem, whatever the base (sys.modules, os.__dict__,
  sys._getframe().f_globals and operator.setitem among them);
- no import of builtins, importlib or inspect under any name, no from-import FROM any of
  them, no from-import OF a banned name from any module, no import alias that rebinds a
  banned name, and no star import;
- no store or delete to an ATTRIBUTE of anything (an os.O_NONBLOCK patch, an os.walk swap);
- no store or delete through a __dict__ or sys.modules subscript (subsumed by the
  attribute-Load rule, kept for its specific message).
RESIDUAL, stated plainly: this is a
refusal of reflection SPELLINGS, never a proof of their absence at runtime. SAME-MODULE
reflection that reaches a namespace through an ATTRIBUTE CHAIN this list does not name
(os.__builtins__, site.builtins, f.__globals__, object.__getattribute__ among them) or that
runs source through an EVALUATOR MODULE (timeit, code, pickle among them) is OUTSIDE this
spelling scan, and so is a mutation of os, of builtins or of this module object performed by
ANOTHER module at RUNTIME; no spelling rule closes either, which is why no gate may rest an
EXEMPTION or a member proof on this predicate (it narrows the review surface as defence in
depth; it proves nothing). hasattr (a read-only probe that cannot rebind anything) stays
allowed. Offline, stdlib only, pure.
"""
import ast


# Names whose Load in ANY position forfeits every proof (round 5, positive rule): the
# callables that can mutate a namespace or evaluate source this scan has not parsed, the
# builtins module under both spellings (every banned callable is an attribute of it), and
# the importlib and inspect modules (module loaders and frame accessors). A Load, not only
# a call: `f = setattr`, `b = builtins` and `getattr(builtins, "setattr")` each alias the
# same capability without calling it at the aliasing site.
REFLECTION_NAME_LOADS = frozenset((
    "setattr", "delattr", "globals", "vars", "locals", "getattr", "__import__",
    "exec", "eval", "compile", "builtins", "__builtins__", "importlib", "inspect",
))

# Attributes whose Load on ANY base forfeits every proof: the object/module namespace
# mapping (__dict__), the direct attribute mutators (__setattr__, __delattr__), the frame
# namespaces (f_globals, f_locals), the interpreter module table (sys.modules, matched as
# the attribute name "modules" on any base, fail-closed), the frame accessor _getframe,
# and the operator-module item mutators (setitem, delitem).
REFLECTION_ATTRIBUTE_LOADS = frozenset((
    "__dict__", "__setattr__", "__delattr__", "f_globals", "f_locals",
    "modules", "_getframe", "setitem", "delitem",
))

# Modules no proof-seeking module may import under ANY alias: every attribute of builtins
# is a banned callable, importlib loads and reloads modules, and inspect reaches frames.
REFLECTION_MODULES = frozenset(("builtins", "importlib", "inspect"))

# Every name whose from-import or import-alias rebinding is refused.
_BANNED_BINDING_NAMES = REFLECTION_NAME_LOADS | REFLECTION_ATTRIBUTE_LOADS


def namespace_mutation_problems(tree, label):
    """One problem string per namespace-mutation shape in tree, empty when it holds none. The
    scan is whole-tree (every scope, every expression position) and POSITIVE since round 5:
    rather than enumerating bad call spellings, it refuses ANY Load of a reflection name or
    reflection attribute (the tables above), any import that binds or re-binds one, any star
    import, any attribute store or delete, and any __dict__ / sys.modules subscript store.
    It cares where a shape EXISTS, not whether it executes, because a syntactic caller cannot
    prove it never runs. The stated residual (the module docstring): same-module reflection
    through an attribute chain this list does not name (os.__builtins__, site.builtins,
    f.__globals__, object.__getattribute__) or through a source-evaluator module (timeit,
    code, pickle), and a mutation performed by ANOTHER module at runtime, are each OUTSIDE
    this spelling scan, so a caller may hold a module to it as defence in depth only, never
    as an exemption or a proof."""
    problems = []
    for sub in ast.walk(tree):
        if isinstance(sub, ast.Import):
            for alias in sub.names:
                if alias.name.split(".")[0] in REFLECTION_MODULES:
                    problems.append("{} imports {}, a reflection module this predicate refuses "
                                    "under every alias (round 5: any reflection reference "
                                    "forfeits the proof)".format(label, alias.name))
                if alias.asname in _BANNED_BINDING_NAMES:
                    problems.append("{} rebinds the reflection name {} by an import "
                                    "alias".format(label, alias.asname))
        elif isinstance(sub, ast.ImportFrom):
            for alias in sub.names:
                if alias.name == "*":
                    problems.append("{} uses a star import, which can rebind any compared name "
                                    "invisibly".format(label))
                    continue
                if alias.name in _BANNED_BINDING_NAMES:
                    problems.append("{} from-imports {}, a reflection name this predicate "
                                    "refuses under every alias".format(label, alias.name))
                if alias.asname in _BANNED_BINDING_NAMES:
                    problems.append("{} rebinds the reflection name {} by an import "
                                    "alias".format(label, alias.asname))
            if not sub.level and (sub.module or "").split(".")[0] in REFLECTION_MODULES:
                problems.append("{} imports from {}, which can rebind a reflection callable "
                                "under a new name".format(label, sub.module))
        elif isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Load) \
                and sub.id in REFLECTION_NAME_LOADS:
            problems.append("{} references {}, a reflection name through which a binding can "
                            "change invisibly to this scan (round 5: any reflection reference, "
                            "not only a call, forfeits the proof)".format(label, sub.id))
        elif isinstance(sub, ast.Attribute):
            if isinstance(sub.ctx, (ast.Store, ast.Del)):
                problems.append("{} stores to or deletes an attribute ({}.{}), which can rebind a "
                                "dependency (an os.walk swap) out from under the compared function".format(
                                    label, getattr(sub.value, "id", "<expr>"), sub.attr))
            elif isinstance(sub.ctx, ast.Load) and sub.attr in REFLECTION_ATTRIBUTE_LOADS:
                problems.append("{} references an attribute named {}, a namespace mapping or "
                                "frame accessor through which a binding can change invisibly to "
                                "this scan".format(label, sub.attr))
        elif isinstance(sub, ast.Subscript) and isinstance(sub.ctx, (ast.Store, ast.Del)):
            if any(isinstance(part, ast.Attribute) and part.attr in ("__dict__", "modules")
                   for part in ast.walk(sub.value)):
                problems.append("{} stores to or deletes through a __dict__ or sys.modules "
                                "subscript, which can rebind a compared name with no Name "
                                "binding this scan sees".format(label))
    return problems
