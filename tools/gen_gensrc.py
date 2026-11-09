#!/usr/bin/env python3
"""Generate the .aiqt/gensrc.json registry of generated outputs from each generator's own declaration.

Every tools/gen_*.py declares a module-level GENSRC_OUTPUTS constant naming what it generates (its
targets, their kind, the sources it derives them from, and how to regenerate them). This tool discovers
those generators by STATICALLY PARSING each tools/gen_*.py and evaluating only its GENSRC_OUTPUTS
literal (ast.parse + ast.literal_eval), never importing or executing a generator, so no import-time
side effect (a SystemExit, a dynamic __getattr__, a broken import) can influence discovery. It requires
the declaration, validates and unions the entries, and renders the single registry .aiqt/gensrc.json so
the pack has one machine-readable inventory of every generated artefact.

A generator's `sources` are the CONTENT-BEARING inputs the output DERIVES FROM: change a source and the
output bytes can change. It is deliberately NOT every path the generator reads. Validation-only reads,
where a generator reads a file to CHECK its declaration but never lets it affect the output bytes (for
example gen_rules reading .aiqt/standards/ to validate MAP_KEYS, or gen_hooks reading the corpus to
cross-check ids), are EXCLUDED, because "adding a mapping key never affects a rule's derived path"
(gen_rules.py). The registry records the derivation, not the full read set.

Fail-closed by construction: the required GENSRC_OUTPUTS declaration is the STRUCTURAL staleness guard
(a new generator cannot land without declaring its outputs). A generator with no module-level
GENSRC_OUTPUTS assignment, more than one, a non-literal right-hand side (literal_eval cannot evaluate
it), or an empty literal exits 2 naming the file, never a silent partial registry. The tools dir is
enumerated with os.scandir, which RAISES on an unreadable dir (a glob would silently yield nothing).
Every entry is validated (a canonical repo-relative target with no backslash, "./" prefix, "..", or
redundant "/"; a known kind; a non-empty tuple of existing repo-relative sources; a regenerate command;
and a trailing "/" that appears only on a tree target, never on a file or block), and targets are
deduplicated on their SLASH-STRIPPED canonical body so a file "X" and a tree "X/" (and "X" vs "./X")
all collide. The entries are sorted by target so the render is deterministic. The registry
lists its OWN output too, so .aiqt/gensrc.json appears in the inventory it produces. A source's
EXISTENCE is checked (the registry declares paths); a source's READABILITY is the generator's gen-time
concern, not the registry's, so no readability probing is done here. An unreadable input still fails
closed (exit 2) exactly like the sibling generators, never a silent clean.

An entry whose target is on the authoring-only roster (tools/gen_hooks.py AUTHORING_ONLY_REL, read
statically like the declarations) gains the optional key "scope": "authoring". gensrc_guard honours such an
entry only in a tree whose root carries the authoring marker .aiqt/authoring.toml, so an adopter tree holding
this registry keeps its own .claude/settings.json. The registry stays version 1: a guard that predates the
key tolerates it as an unknown key and honours the entry in every tree, as before it.

  gen_gensrc.py            regenerate .aiqt/gensrc.json
  gen_gensrc.py --check    fail (exit 1) on drift; exit 2 on a bad declaration or a read/write error
  gen_gensrc.py --self-test  build synthetic trees and assert the generator's own fail-closed invariants
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: gen_gensrc.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import ast
import json
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _gen_common import linked_dir_failures, repo_root, reconcile  # noqa: E402

# The registry this tool renders, repo-root-relative. It is listed in its own output (below), so the
# inventory is complete: the registry names itself alongside every other generated artefact.
REGISTRY_REL = ".aiqt/gensrc.json"
# This tool's own output declaration, unioned in like every generator's GENSRC_OUTPUTS. The sources are
# the declarations themselves, which live across the tools/ tree.
OWN_OUTPUTS = (
    {"target": REGISTRY_REL, "kind": "file",
     "sources": ("tools/",), "regenerate": "python3 tools/gen_gensrc.py"},
)

KINDS = {"file", "tree", "block"}
ENTRY_KEYS = {"target", "kind", "sources", "regenerate"}
SELF = "gen_gensrc"  # this module's stem, excluded from discovery (it declares its own output above)
# The authoring-only roster: tools/gen_hooks.py AUTHORING_ONLY_REL names the paths that exist only in the pack
# authoring tree (the authoring marker and the repository-local .claude/settings.json gated on it). This tool,
# not the generator, marks every registry entry on that roster "scope": "authoring", so gensrc_guard honours
# it only where the marker sits and an adopter tree holding the shipped registry keeps its own settings. The
# roster is read STATICALLY (read_authoring_roster), like every declaration here; a declaration carrying its
# own "scope" key is refused (ENTRY_KEYS), so the roster is the one source of the scope.
ROSTER_GEN = "gen_hooks.py"
ROSTER_NAME = "AUTHORING_ONLY_REL"
SCOPE_AUTHORING = "authoring"


def _reject_rebinding(tree, name, allowed, where, assignment):
    """Raise ValueError (naming `where`) when the parsed module `tree` binds `name` anywhere other than the
    Store-context Name node(s) whose id() is in `allowed`: the single-binding discipline _read_declaration
    (GENSRC_OUTPUTS) and read_authoring_roster (the roster and every name it uses) share. The WHOLE tree is
    walked, so a binding nested in an if, try, for, with, def or class body is refused like a top-level one.
    A single Store-/Del-context Name rule covers plain/augmented/annotated assignment, del, walrus, tuple/star
    unpack, and for/with targets (all bind through such a Name; so does a `type` alias statement); a second
    rule covers the string-name binders, whose bound name is a str attribute, not a Name node (dotted/wildcard
    import, def/class, except-as, match capture incl. MatchMapping.rest, a parameter, global/nonlocal).
    `assignment` names the one allowed form in the message ("literal", "plain")."""
    binder = ("{}: {} must not be rebound by {{}}; exactly one top-level {} assignment "
              "is allowed (fail-closed)").format(where, name, assignment)
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            if node.id == name and isinstance(node.ctx, (ast.Store, ast.Del)) and id(node) not in allowed:
                raise ValueError("{}: {} must not be rebound (a second/nested/conditional "
                                 "assignment, an augmented or annotated assignment, a del, a walrus, a "
                                 "tuple/star unpack, or a for/with target); exactly one top-level {} "
                                 "assignment is allowed (fail-closed)".format(where, name, assignment))
        elif isinstance(node, ast.alias):
            # An alias with no asname binds the ROOT of a (possibly dotted) name: `import X.child` binds `X`,
            # so compare on name.partition(".")[0]. A wildcard (`from x import *`, name == "*") can bind
            # `name` and is statically undecidable, so fail closed. `import X.child as y` binds only `y`.
            bound = node.asname if node.asname is not None else node.name.partition(".")[0]
            if bound == name or (node.asname is None and node.name == "*"):
                raise ValueError(binder.format("an import"))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name == name:
                raise ValueError(binder.format("a def/class"))
        elif isinstance(node, ast.ExceptHandler):
            if node.name == name:
                raise ValueError(binder.format("an except-as"))
        elif isinstance(node, (ast.MatchAs, ast.MatchStar)):
            if node.name == name:
                raise ValueError(binder.format("a match capture"))
        elif isinstance(node, ast.MatchMapping):
            if node.rest == name:  # `case {**name}` binds the rest to that name
                raise ValueError(binder.format("a match capture"))
        elif isinstance(node, ast.arg):
            if node.arg == name:  # a function/lambda parameter, incl. */**/kw-only
                raise ValueError(binder.format("a parameter"))
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            if name in node.names:
                raise ValueError(binder.format("a global/nonlocal declaration"))


def _parse_module(path):
    """Parse the module at path as the interpreter compiles it: from its BYTES, so a PEP 263 coding cookie
    decodes the source as an import would (a UTF-8 text read of a file declaring `# coding: utf-7` parses
    other statements than the module runs). The bytes must also decode as UTF-8 and parse as that text, so a
    file the text read refused (bytes that are not UTF-8, a leading BOM) stays refused. Raises OSError on a
    read fault, UnicodeDecodeError (a ValueError) or SyntaxError, and re-raises a parser stack overflow on very
    deep nesting (MemoryError or RecursionError, the classes gen_renderers._PARSE_ERRORS also maps) as
    SyntaxError; each caller maps them all to exit 2."""
    data = path.read_bytes()
    try:
        ast.parse(data.decode("utf-8"), filename=str(path))
        return ast.parse(data, filename=str(path))
    except (MemoryError, RecursionError) as exc:
        raise SyntaxError("too deeply nested to parse ({}: {})".format(type(exc).__name__, exc)) from exc


def _read_declaration(path, where):
    """Return the value of path's single module-level GENSRC_OUTPUTS literal, recovered by parsing the
    file to an AST (its bytes, as the interpreter does: _parse_module, so a coding cookie is honoured) and
    evaluating ONLY that literal with ast.literal_eval. The module is never imported
    or executed, so an import-time SystemExit, a dynamic __getattr__, or any other side effect cannot
    influence the result. Fail-closed: raises ValueError (naming `where`) when there is no single
    top-level literal Assign to GENSRC_OUTPUTS, when the right-hand side is non-literal, or when the
    literal is empty; the caller maps that to exit 2. An unreadable file raises OSError, also mapped to
    exit 2.

    Binding invariant (honestly bounded): this rejects the statically-detectable DIRECT binding of
    GENSRC_OUTPUTS to anything other than the one literal Assign, by walking the parsed tree. That covers
    a module-level or nested Store/Del-context Name (a second/nested/conditional assignment, an augmented
    or annotated assignment, a del, a walrus, a tuple/star unpack, a for/with target); an import alias,
    including a dotted-root `import GENSRC_OUTPUTS.child` and a fail-closed wildcard `from x import *`; a
    def/class; an except-as; and a match capture, including a MatchMapping `.rest`. A `type GENSRC_OUTPUTS
    = ...` statement needs no dedicated branch: its `.name` is itself a Store-context Name already caught
    by the Name rule.

    Disclosed residual (this does NOT claim to catch any binding anywhere): (a) GENSRC_OUTPUTS is a
    RESERVED declaration name, so the walk is CONSERVATIVE and will also reject an unrelated reuse of the
    name as a local, parameter, loop, or comprehension variable inside a function; that over-catch is
    harmless and fails in the safe direction (do not reuse the name). (b) Truly-DYNAMIC binding
    (globals()["GENSRC_OUTPUTS"] = ..., exec, setattr on the module) is beyond static analysis and out of
    scope; discovery never executes a generator, so such a binding cannot affect the static registry
    anyway. Static enumeration of a dynamic surface is unbounded; this guard covers
    the realistic and statically-detectable forms and discloses the rest. (c) A PEP-695 type
    parameter (def f[GENSRC_OUTPUTS](), class C[GENSRC_OUTPUTS], type X[GENSRC_OUTPUTS] = ...)
    binds only in the type-parameter scope, is not inspected, and cannot affect the module-level
    declaration: an accepted nested-scope residual."""
    try:
        tree = _parse_module(path)  # OSError -> caller's fail-closed try
    except SyntaxError as exc:
        raise ValueError("{}: cannot parse for its GENSRC_OUTPUTS declaration ({})".format(where, exc))
    # The ONLY valid declaration is a single top-level plain Assign to GENSRC_OUTPUTS.
    top_assigns = [node for node in tree.body
                   if isinstance(node, ast.Assign)
                   and any(isinstance(t, ast.Name) and t.id == "GENSRC_OUTPUTS" for t in node.targets)]
    the_assign = top_assigns[0] if len(top_assigns) == 1 else None
    # Walk the WHOLE tree and reject any binding of GENSRC_OUTPUTS OTHER than the GENSRC_OUTPUTS target(s) of
    # the_assign, closing the entire rebind class (_reject_rebinding, shared with read_authoring_roster). The
    # one allowed binding is those Store-Name target node(s), tracked by identity.
    allowed = set()
    if the_assign is not None:
        allowed = {id(t) for t in the_assign.targets
                   if isinstance(t, ast.Name) and t.id == "GENSRC_OUTPUTS"}
    _reject_rebinding(tree, "GENSRC_OUTPUTS", allowed, where, "literal")
    if the_assign is None:
        raise ValueError("{} does not declare GENSRC_OUTPUTS; a generator cannot ship without declaring "
                         "its outputs (fail-closed)".format(where))
    try:
        decl = ast.literal_eval(the_assign.value)  # never exec/import: a computed RHS raises here
    except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError) as exc:
        raise ValueError("{}: GENSRC_OUTPUTS must be a literal (a tuple/list of dict literals), not a "
                         "computed expression ({})".format(where, exc))
    if not isinstance(decl, (tuple, list)):
        raise ValueError("{}: GENSRC_OUTPUTS must be a tuple/list of entries".format(where))
    if not decl:
        raise ValueError("{}: GENSRC_OUTPUTS is empty; a generator must declare at least one output "
                         "(fail-closed)".format(where))
    return decl


def discover_declarations(tools_dir):
    """Statically read every tools/gen_*.py in tools_dir except this tool, in sorted stem order, and
    return a list of (stem, GENSRC_OUTPUTS-value). Each declaration is recovered by parsing, never by
    importing (see _read_declaration), so the import-execution surface is removed entirely. Fail-closed:
    a bad or missing declaration raises ValueError naming the file (the structural staleness guard),
    discovering NO generator at all raises ValueError (a registry-only repo is malformed, never a
    silent self-only registry), and os.scandir RAISES on an unreadable tools_dir instead of silently
    yielding nothing as a glob would; the caller maps all of these, and any OSError reading a file, to
    exit 2."""
    tools_dir = Path(tools_dir)
    stems = []
    with os.scandir(tools_dir) as it:  # OSError on an unreadable/absent dir -> caller's fail-closed try
        for entry in it:
            name = entry.name
            if name.startswith("gen_") and name.endswith(".py") and name[:-3] != SELF:
                stems.append(name[:-3])
    stems.sort()
    if not stems:  # a repo carrying the registry tool but no generators is malformed, never self-only
        raise ValueError("no generators found under {}: expected at least one tools/gen_*.py besides "
                         "{}.py (fail-closed)".format(tools_dir, SELF))
    declarations = []
    for stem in stems:
        decl = _read_declaration(tools_dir / (stem + ".py"), "{}.py".format(stem))
        declarations.append((stem, decl))
    return declarations


def _canonical_target(target, kind, where):
    """Return the canonical POSIX-relative spelling of `target`, raising ValueError (naming `where`) if
    it is not already in that clean form. Rejects a backslash (a Windows separator), an absolute path, a
    '.' or '..' segment, a './' prefix, and redundant separators ('//'). A trailing '/' is allowed ONLY
    on a tree target (it is the tree marker); on a file or block target it is rejected. A .working
    component is rejected too: the OPF store tree is owned by the OPF deny hook, so this registry never
    registers a store path. An OPF declared view lies outside .working (TODO.md, docs/STATUS.md) and is not
    checked here (this tool does not read the OPF manifests), so a generator declaring one is accepted. Callers
    deduplicate on the SLASH-STRIPPED canonical body, so a file 'X' and a tree 'X/', and 'X' and './X',
    all collide."""
    if "\\" in target:
        raise ValueError("{}: target {!r} must use POSIX '/' separators, not a backslash"
                         .format(where, target))
    if target.startswith("/"):
        raise ValueError("{}: target {!r} must be repo-relative, not absolute".format(where, target))
    has_trailing = target.endswith("/")
    if has_trailing and kind != "tree":
        raise ValueError("{}: a trailing '/' marks a tree target; {!r} (kind {!r}) must not end with "
                         "'/'".format(where, target, kind))
    body = target[:-1] if has_trailing else target  # a lone trailing '/' is the tree marker
    if any(seg in ("", ".", "..") for seg in body.split("/")):
        raise ValueError("{}: target {!r} must be a clean POSIX-relative path (no './' prefix, '..', "
                         "or redundant '/')".format(where, target))
    if ".working" in body.split("/"):
        raise ValueError("{}: target {!r} has a .working component: an OPF store path is owned by the OPF deny "
                         "hook (opf/enforcement/claude/pretooluse_deny.py), never by this registry"
                         .format(where, target))
    return target


def _validate_entry(raw, where, root, seen):
    """Validate one declaration entry against root and return the canonical dict {target, kind, sources
    (list), regenerate}. Raises ValueError on any malformed field (including a non-str key or kind, so a
    malformed literal fails closed rather than raising a bare TypeError), an unknown/absent source path,
    or a duplicate target (compared on its slash-stripped canonical body); the caller maps that to exit
    2. `where` names the declaring file for the message."""
    if not isinstance(raw, dict):
        raise ValueError("{}: every GENSRC_OUTPUTS entry must be a dict".format(where))
    # Type-check the keys BEFORE any set membership or .format: a non-str key would make sorted()/join
    # below raise a bare TypeError instead of the fail-closed ValueError the caller maps to exit 2.
    for key in raw:
        if not isinstance(key, str):
            raise ValueError("{}: entry keys must be strings, got {!r}".format(where, key))
    extra = set(raw) - ENTRY_KEYS
    if extra:
        raise ValueError("{}: entry has unknown key(s): {}".format(where, ", ".join(sorted(extra))))
    missing = ENTRY_KEYS - set(raw)
    if missing:
        raise ValueError("{}: entry missing key(s): {}".format(where, ", ".join(sorted(missing))))

    # Type-check kind BEFORE the set-membership test: an unhashable kind (a list/dict) would make
    # `kind not in KINDS` raise a bare TypeError rather than the fail-closed ValueError -> exit 2.
    kind = raw["kind"]
    if not isinstance(kind, str):
        raise ValueError("{}: kind must be a string, got {!r}".format(where, kind))
    if kind not in KINDS:
        raise ValueError("{}: kind {!r} must be one of {}".format(where, kind, "/".join(sorted(KINDS))))

    target = raw["target"]
    if not isinstance(target, str) or not target:
        raise ValueError("{}: target must be a non-empty string".format(where))
    canon = _canonical_target(target, kind, where)  # rejects a trailing '/' unless kind == "tree"
    if kind == "tree" and not canon.endswith("/"):
        raise ValueError("{}: a tree target ({!r}) must end with '/'".format(where, target))

    # `sources` are the CONTENT-BEARING inputs the output DERIVES from, not every path the generator
    # reads; validation-only reads are excluded by convention (see the module docstring). Here we check
    # only spelling sanity (no backslash, no '..', repo-relative) and EXISTENCE; a source's readability
    # is the generator's gen-time concern, not the registry's, so no readability probing is done.
    sources = raw["sources"]
    if not isinstance(sources, (tuple, list)) or not sources:
        raise ValueError("{}: sources must be a non-empty tuple of paths".format(where))
    for src in sources:
        if not isinstance(src, str) or not src:
            raise ValueError("{}: every source must be a non-empty string".format(where))
        if "\\" in src:
            raise ValueError("{}: source {!r} must use POSIX '/' separators, not a backslash"
                             .format(where, src))
        if src.startswith("/") or ".." in Path(src).parts:
            raise ValueError("{}: source {!r} must be repo-relative with no '..'".format(where, src))
        if not (root / src).exists():  # existence probe; an unreadable parent raises OSError -> exit 2
            raise ValueError("{}: source {!r} does not exist under {}".format(where, src, root))

    regenerate = raw["regenerate"]
    if not isinstance(regenerate, str) or not regenerate:
        raise ValueError("{}: regenerate must be a non-empty string".format(where))

    # Dedup on the SLASH-STRIPPED canonical body so a file 'X' and a tree 'X/' (and 'X' vs './X')
    # collide: one filesystem path is generated by exactly one entry, whatever its kind.
    dedup_key = canon[:-1] if canon.endswith("/") else canon
    if dedup_key in seen:
        raise ValueError("{}: duplicate target {!r} (a target is generated by exactly one entry)"
                         .format(where, target))
    seen.add(dedup_key)
    return {"target": canon, "kind": kind, "sources": list(sources), "regenerate": regenerate}


def read_authoring_roster(tools_dir):
    """The authoring-only roster, tools/gen_hooks.py AUTHORING_ONLY_REL, read STATICALLY (parsed, never
    imported or run, like every declaration here). Its value must be a tuple or list whose items are
    non-empty str literals, or names bound to one (gen_hooks spells it from AUTHORING_MARKER_REL and
    PROJECT_SETTINGS_REL), each a clean POSIX-relative path. Returns () when tools_dir holds no gen_hooks.py:
    a tree without that generator has no authoring-only target (the synthetic trees here and in
    check_gensrc_failclose). Fail-closed: a present gen_hooks.py that cannot be parsed, or whose items do not
    resolve to str literals, raises ValueError, and a read fault raises OSError; the caller maps both to exit
    2, so a renamed or computed roster can never silently drop the scope. AUTHORING_ONLY_REL and every name
    it uses must be bound exactly once, by one plain top-level assignment (a single Name target), and nowhere
    else: the _reject_rebinding walk _read_declaration uses refuses every other binding of those names
    anywhere in the module (tuple or star unpacking, augmented or annotated assignment, a for, with, import,
    def or class binding, a walrus, a del, a global or nonlocal declaration, an except-as, a match capture,
    a parameter, and any binding nested in a block or a function). Each name the roster uses must also be
    bound in a statement BEFORE the roster assignment (the module reads it at that line, so a later
    assignment is not the value the roster takes) and must not be dunder-shaped (begin and end with __): the
    interpreter binds some such names itself (a module docstring binds __doc__), so every name of that shape is
    refused, one the interpreter never binds included (failing safe). The file is parsed from its
    bytes (_parse_module), so a PEP 263 coding cookie is honoured as an import honours it. Within those rules
    the parsed value is the value the module binds. Bounded like _read_declaration: the walk is conservative
    (an unrelated local reuse of one of these names inside a function is refused too, failing safe), a
    dynamic rebinding (globals(), setattr, exec) is out of scope, and a PEP-695 type parameter is not
    inspected."""
    path = Path(tools_dir) / ROSTER_GEN
    try:
        tree = _parse_module(path)
    except FileNotFoundError:
        return ()
    except SyntaxError as exc:
        raise ValueError("{}: cannot parse for its {} roster ({})".format(ROSTER_GEN, ROSTER_NAME, exc))
    plain = {}
    position = {}
    for index, node in enumerate(tree.body):
        position[id(node)] = index
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            plain.setdefault(node.targets[0].id, []).append(node)

    def _bound_once(name):
        nodes = plain.get(name, [])
        # The one allowed binding is the Name target of a single plain top-level assignment; any other
        # binding of the name anywhere in the module fails closed (a second plain assignment included).
        _reject_rebinding(tree, name, {id(nodes[0].targets[0])} if len(nodes) == 1 else set(), ROSTER_GEN,
                          "plain")
        if len(nodes) != 1:
            raise ValueError("{}: {} must be bound exactly once, by a plain module-level assignment "
                             "(fail-closed)".format(ROSTER_GEN, name))
        return nodes[0]

    roster_node = _bound_once(ROSTER_NAME)
    value = roster_node.value
    if not isinstance(value, (ast.Tuple, ast.List)) or not value.elts:
        raise ValueError("{}: {} must be a non-empty tuple or list".format(ROSTER_GEN, ROSTER_NAME))
    roster = []
    for item in value.elts:
        if isinstance(item, ast.Name):
            if item.id.startswith("__") and item.id.endswith("__"):
                raise ValueError("{}: {} must not use {}, a dunder-shaped name (every name that begins and ends "
                                 "with __ is refused, since the interpreter binds some, such as __doc__ and "
                                 "__name__, itself) (fail-closed)".format(ROSTER_GEN, ROSTER_NAME, item.id))
            bound = _bound_once(item.id)
            if position[id(bound)] >= position[id(roster_node)]:
                raise ValueError("{}: {} must be bound before {} reads it (fail-closed)".format(
                    ROSTER_GEN, item.id, ROSTER_NAME))
            item = bound.value
        if not (isinstance(item, ast.Constant) and isinstance(item.value, str) and item.value):
            raise ValueError("{}: every {} item must be a str literal, or a name bound once to one "
                             "(fail-closed)".format(ROSTER_GEN, ROSTER_NAME))
        roster.append(_canonical_target(item.value, "file", "{} {}".format(ROSTER_GEN, ROSTER_NAME)))
    return tuple(roster)


def collect_entries(declarations, root):
    """Union and validate the declarations of every discovered generator plus this tool's own output,
    then return the entries sorted by target for a deterministic render. Raises ValueError on any
    malformed entry or duplicate target. Every kind=file entry whose target is on the authoring-only
    roster (read_authoring_roster over root/tools) gains "scope": "authoring"; a roster path declared as a
    tree or block, or lying under a tree entry, raises ValueError, because an unscoped entry over it would
    make gensrc_guard deny an adopter's edits to its own copy of that path."""
    entries = []
    seen = set()
    for stem, decl in declarations:
        for raw in decl:
            entries.append(_validate_entry(raw, "{}.py".format(stem), root, seen))
    for raw in OWN_OUTPUTS:
        entries.append(_validate_entry(raw, "gen_gensrc.py", root, seen))
    # Beyond the exact-body dedup in _validate_entry, reject a component-boundary ancestor/descendant
    # OVERLAP when either side is a tree: a tree 'X/' plus a file 'X/child', or a tree 'X/' plus a nested
    # tree 'X/Y/', name the same subtree, which exactly one entry must own. The boundary is a path
    # component (a trailing '/'), so a tree 'X/' never false-collides with a sibling like 'XYZ'. Compare
    # the slash-stripped canonical body and the kind of every pair.
    tracked = [((e["target"][:-1] if e["target"].endswith("/") else e["target"]), e["kind"], e["target"])
               for e in entries]
    for i in range(len(tracked)):
        a_body, a_kind, a_target = tracked[i]
        for j in range(i + 1, len(tracked)):
            b_body, b_kind, b_target = tracked[j]
            if a_kind != "tree" and b_kind != "tree":
                continue  # a tree-descendant overlap requires at least one tree
            if a_body.startswith(b_body + "/") or b_body.startswith(a_body + "/"):
                raise ValueError("tree-descendant overlap: {!r} and {!r} name the same subtree; a tree "
                                 "and any path beneath it cannot both be declared (fail-closed)"
                                 .format(a_target, b_target))
    roster = read_authoring_roster(root / "tools")
    for entry in entries:
        for rel in roster:
            if entry["kind"] == "file" and entry["target"] == rel:
                entry["scope"] = SCOPE_AUTHORING
            elif entry["target"].rstrip("/") == rel or (
                    entry["kind"] == "tree" and rel.startswith(entry["target"])):
                raise ValueError("the authoring-only path {!r} ({} {}) is covered by the {} entry {!r}; it "
                                 "must be a kind=file entry of its own so it can be scoped to the authoring "
                                 "tree (fail-closed)".format(rel, ROSTER_GEN, ROSTER_NAME, entry["kind"],
                                                             entry["target"]))
    entries.sort(key=lambda e: e["target"])
    return entries


def build_registry(root):
    """The full .aiqt/gensrc.json text: {"version": 1, "generated": [...sorted entries...]} rendered
    with sort_keys for determinism, plus a trailing newline. Raises ValueError/OSError on a bad
    declaration or an unreadable input; the caller maps those to exit 2."""
    declarations = discover_declarations(root / "tools")
    entries = collect_entries(declarations, root)
    obj = {"version": 1, "generated": entries}
    return json.dumps(obj, indent=2, sort_keys=True) + "\n"


def run(root, check):
    """Render the registry into root/.aiqt/gensrc.json, or (check mode) report drift. Fail-closed
    (exit 2) on a bad declaration or any unreadable input, mirroring the sibling generators."""
    try:
        text = build_registry(root)
    except (ValueError, OSError) as exc:
        print("error: cannot build {} ({}); fail-closed".format(REGISTRY_REL, exc), file=sys.stderr)
        return 2
    drifted = reconcile(root / REGISTRY_REL, text, check, root)  # shared reconcile fail-closes (exit 2) on an
                                                            # OSError or an invalid-UTF-8 target
    if drifted:
        print("drift: {} is out of date; run tools/gen_gensrc.py".format(REGISTRY_REL), file=sys.stderr)
        return 1
    if not check:
        print("wrote {} ({} generated-output entries)".format(REGISTRY_REL, text.count('"target":')))
    return 0


def main():
    args = sys.argv[1:]
    if "--self-test" in args:
        return self_test_main()
    return run(repo_root(), "--check" in args)


# --- self-test ----------------------------------------------------------------------------------------
# Proves the generator's own fail-closed invariants against synthetic trees (the gen_hooks.py pattern),
# so gen_gensrc never becomes an ungated generator:
#   (a) a conformant tree generates and re-checks drift-clean,
#   (b) a generator module missing GENSRC_OUTPUTS fails closed (exit 2), the structural staleness guard,
#   (c) a mutated .aiqt/gensrc.json is caught by --check (exit 1),
#   (d) an EMPTY GENSRC_OUTPUTS=() fails closed (exit 2), the old empty-declaration bypass,
#   (e) a NON-LITERAL RHS beside a module-level raise SystemExit(0) fails closed (exit 2) and is NEVER
#       executed, proving discovery parses statically and does not import,
#   (f) an unreadable tools/ dir fails closed (exit 2), os.scandir raising rather than a silent glob,
#   (g) two entries whose canonical bodies are equal (a file 'X' and a tree 'X/') fail closed (exit 2),
#       the slash-stripped canonical-dedup collision,
#   (h) a malformed literal (a non-str kind such as []) fails closed with ValueError -> exit 2, never a
#       bare TypeError from a set-membership test,
#   (i) each single-binding-invariant rebind of GENSRC_OUTPUTS beside a valid decoy literal fails closed
#       (exit 2), one case per binding mechanism (walrus/tuple-unpack/for/with/except/import-as/dotted-
#       import-root/wildcard-import/MatchMapping-rest/def/class), while the positive `import
#       GENSRC_OUTPUTS.child as y` (binds y only) generates cleanly (exit 0, no false reject),
#   (j) a non-str entry KEY fails closed with ValueError -> exit 2 (not a bare TypeError),
#   (k) a trailing '/' on a file or block target, and its absence on a tree target, each fail closed
#       (exit 2),
#   (l) a tree-descendant overlap (a tree 'X/' with a file 'X/c' in either order, or a nested tree 'X/Y/')
#       fails closed (exit 2), while a sibling on a component boundary ('X/' vs 'XYZ') does NOT collide,
#   (m) zero generators (a tools/ dir with no gen_*.py besides this tool) fails closed (exit 2),
#   (n) an invalid-UTF-8 existing .aiqt/gensrc.json target fails closed (exit 2) on --check, via the
#       shared _gen_common.reconcile guard rather than a raw traceback,
#   (o) the tools/gen_hooks.py AUTHORING_ONLY_REL roster marks exactly its entry "scope": "authoring",
#   (p) a roster path under a tree or declared as a block, an unbound, computed or doubly bound roster, and a
#       declaration carrying its own scope each fail closed (exit 2),
#   (q) every binding of the roster or of a name it uses other than its one plain top-level assignment (tuple
#       or star unpacking, augmented or annotated assignment, top-level or nested, a for/with/import/def/class
#       binding, a walrus, a del, a global declaration, a plain assignment nested in a block) fails closed
#       (exit 2),
#   (r) a source is read as the interpreter binds it: a roster name read through __doc__ or __name__, a name
#       bound after the roster, a dunder name bound before it, and a `# coding: utf-7` cookie hiding a
#       rebinding of a roster name or of GENSRC_OUTPUTS each fail closed (exit 2); a leading BOM and bytes that
#       are not UTF-8 stay refused (exit 2), and a utf-8 cookie on a valid generator generates (exit 0),
#   (s) a parser stack overflow reading a generator fails closed (exit 2) naming the cause "too deeply nested to
#       parse", never a traceback: a chain of 200,000 unary minus signs as tools/gen_hooks.py and as another
#       generator, a RecursionError injected into every parse of tools/gen_hooks.py, and a MemoryError injected into
#       its byte parse alone, each through run() and, for gen_hooks.py, through the roster read alone (discovery
#       reads that file's declaration first).
# A raised SystemExit anywhere in a run() call is caught by run_quiet and returned as a non-int sentinel
# string, so the self-test can never itself exit early (green or otherwise) on a raise: an UNEXPECTED
# raise mismatches its case's expected exit code and registers as a FAILURE, while case (n) asserts the
# sentinel because the shared reconcile guard fails closed by raising SystemExit(2).

_FAKE_GOOD = '''# self-test generator that declares its output
GENSRC_OUTPUTS = (
    {"target": "OUT.md", "kind": "file",
     "sources": ("src.txt",), "regenerate": "python3 tools/gen_selftestgood.py"},
)
'''

_FAKE_BAD = '''# self-test generator that FORGOT to declare its output
PLACEHOLDER = 1
'''

_FAKE_EMPTY = '''# self-test generator whose declaration is empty (the old empty-() bypass)
GENSRC_OUTPUTS = ()
'''

# A non-literal RHS beside a module-level SystemExit(0). Under the old import-based discovery the
# SystemExit(0) would have run at import and masked the fault as a clean exit; static AST parsing never
# executes it, so the non-literal RHS is caught and the tree fails closed (exit 2).
_FAKE_NONLITERAL = '''# self-test generator with a computed declaration and an import-time SystemExit
raise SystemExit(0)
def _make():
    return ({"target": "OUT.md", "kind": "file",
             "sources": ("src.txt",), "regenerate": "x"},)
GENSRC_OUTPUTS = _make()
'''

# Two entries whose SLASH-STRIPPED canonical bodies are equal: a file 'OUT.md' and a tree 'OUT.md/'.
# Each is individually well formed, but they name the same filesystem path, so dedup collides them and
# the tree fails closed (exit 2). (The old case used 'OUT.md' vs './OUT.md', which was mislabeled: the
# './OUT.md' spelling is rejected as a non-canonical path before dedup is ever reached.)
_FAKE_CANON_DUP = '''# self-test generator with a true canonical-duplicate pair (file 'X' vs tree 'X/')
GENSRC_OUTPUTS = (
    {"target": "OUT.md", "kind": "file",
     "sources": ("src.txt",), "regenerate": "x"},
    {"target": "OUT.md/", "kind": "tree",
     "sources": ("src.txt",), "regenerate": "x"},
)
'''

# A malformed literal: kind is a list (a non-str, and unhashable) rather than a string. Without the
# type-check this would raise a bare TypeError from `kind not in KINDS`; with it, a fail-closed
# ValueError -> exit 2.
_FAKE_BADTYPE = '''# self-test generator whose kind is a non-str list literal
GENSRC_OUTPUTS = (
    {"target": "OUT.md", "kind": [],
     "sources": ("src.txt",), "regenerate": "x"},
)
'''

# A valid single-literal declaration used as the DECOY beside each rebind: the_assign is found, yet the
# rebind is still rejected, proving the single-binding invariant is enforced by binding mechanism.
_DECOY = ('GENSRC_OUTPUTS = (\n'
          '    {"target": "OUT.md", "kind": "file",\n'
          '     "sources": ("src.txt",), "regenerate": "x"},\n'
          ')\n')

# One snippet per Python binding mechanism, each appended to the decoy. Every one rebinds GENSRC_OUTPUTS
# a way a node-type enumeration can miss, and every one must fail closed (exit 2). Parsed statically,
# never executed, so the snippets need no runnable names.
_REBINDS = {
    "walrus (GENSRC_OUTPUTS := [])": "(GENSRC_OUTPUTS := [])\n",
    "tuple-unpack a, GENSRC_OUTPUTS = ...": "a, GENSRC_OUTPUTS = 1, []\n",
    "for-target rebind": "for GENSRC_OUTPUTS in []:\n    pass\n",
    "with-as rebind": "with a as GENSRC_OUTPUTS:\n    pass\n",
    "except-as rebind": "try:\n    pass\nexcept Exception as GENSRC_OUTPUTS:\n    pass\n",
    "import-as rebind": "import os as GENSRC_OUTPUTS\n",
    "dotted-import root (import GENSRC_OUTPUTS.child)": "import GENSRC_OUTPUTS.child\n",
    "wildcard import (from x import *)": "from x import *\n",
    "MatchMapping rest (case {**GENSRC_OUTPUTS})": "match v:\n    case {**GENSRC_OUTPUTS}:\n        pass\n",
    "def rebind": "def GENSRC_OUTPUTS():\n    pass\n",
    "class rebind": "class GENSRC_OUTPUTS:\n    pass\n",
    "parameter rebind (def f(GENSRC_OUTPUTS))": "def _f(GENSRC_OUTPUTS):\n    pass\n",
}

# The positive counterpart to the dotted-import reject: `import GENSRC_OUTPUTS.child as y` binds only `y`,
# NOT GENSRC_OUTPUTS, so beside a valid decoy it must generate cleanly (exit 0) - the dotted-root reject
# must not false-positive on an aliased dotted import.
_FAKE_IMPORT_ALIAS_OK = _DECOY + "import GENSRC_OUTPUTS.child as y\n"

# A non-str entry KEY (int 1) must fail closed with a ValueError, never a bare TypeError from sorted()/join.
_FAKE_BADKEY = '''# self-test generator whose entry has a non-str key
GENSRC_OUTPUTS = (
    {1: "x", "target": "OUT.md", "kind": "file",
     "sources": ("src.txt",), "regenerate": "x"},
)
'''

# A trailing '/' is the tree marker; on a file or block it is rejected, and a tree without it is rejected.
_FAKE_FILE_SLASH = '''# self-test: a file target must not carry the trailing-'/' tree marker
GENSRC_OUTPUTS = (
    {"target": "OUT.md/", "kind": "file",
     "sources": ("src.txt",), "regenerate": "x"},
)
'''

_FAKE_BLOCK_SLASH = '''# self-test: a block target must not carry the trailing-'/' tree marker
GENSRC_OUTPUTS = (
    {"target": "OUT.md/", "kind": "block",
     "sources": ("src.txt",), "regenerate": "x"},
)
'''

_FAKE_TREE_NOSLASH = '''# self-test: a tree target must end with the trailing-'/' marker
GENSRC_OUTPUTS = (
    {"target": "OUT", "kind": "tree",
     "sources": ("src.txt",), "regenerate": "x"},
)
'''

# Tree-descendant overlap: a tree 'X/' and a file 'X/c' beneath it (both declaration orders), and a tree
# 'X/' with a nested tree 'X/Y/', name the same subtree and must fail closed (exit 2).
_FAKE_OVERLAP_TF = '''# self-test: tree 'X/' then file 'X/c' beneath it
GENSRC_OUTPUTS = (
    {"target": "X/", "kind": "tree",
     "sources": ("src.txt",), "regenerate": "x"},
    {"target": "X/c", "kind": "file",
     "sources": ("src.txt",), "regenerate": "x"},
)
'''

_FAKE_OVERLAP_FT = '''# self-test: file 'X/c' then tree 'X/' (opposite declaration order)
GENSRC_OUTPUTS = (
    {"target": "X/c", "kind": "file",
     "sources": ("src.txt",), "regenerate": "x"},
    {"target": "X/", "kind": "tree",
     "sources": ("src.txt",), "regenerate": "x"},
)
'''

_FAKE_OVERLAP_TT = '''# self-test: tree 'X/' plus nested tree 'X/Y/'
GENSRC_OUTPUTS = (
    {"target": "X/", "kind": "tree",
     "sources": ("src.txt",), "regenerate": "x"},
    {"target": "X/Y/", "kind": "tree",
     "sources": ("src.txt",), "regenerate": "x"},
)
'''

# The no-false-collision case: a tree 'X/' beside a sibling file 'XYZ' shares a string prefix but NOT a
# path component, so it must validate and generate cleanly (exit 0).
_FAKE_OVERLAP_OK = '''# self-test: tree 'X/' beside sibling file 'XYZ' (component boundary, no collision)
GENSRC_OUTPUTS = (
    {"target": "X/", "kind": "tree",
     "sources": ("src.txt",), "regenerate": "x"},
    {"target": "XYZ", "kind": "file",
     "sources": ("src.txt",), "regenerate": "x"},
)
'''


# The authoring-only roster fixtures (cases o, p): a tools/gen_hooks.py that spells AUTHORING_ONLY_REL
# through module-level names, as the real generator does. Case (o) spells the expected scope literally, never
# from SCOPE_AUTHORING, so a renamed value fails there.
_ROSTER_NAMES = ('AUTHORING_MARKER_REL = ".aiqt/authoring.toml"\n'
                 'PROJECT_SETTINGS_REL = ".claude/settings.json"\n')
_ROSTER = "AUTHORING_ONLY_REL = (AUTHORING_MARKER_REL, PROJECT_SETTINGS_REL)\n"


def _hooks_decl(*entries):
    """A GENSRC_OUTPUTS literal over (target, kind, extra) triples; extra is literal dict-item text."""
    body = "".join('    {{"target": "{}", "kind": "{}", "sources": ("src.txt",), '
                   '"regenerate": "python3 tools/gen_hooks.py"{}}},\n'.format(t, k, x) for t, k, x in entries)
    return "GENSRC_OUTPUTS = (\n" + body + ")\n"


_ROSTER_DECL = _hooks_decl((".claude/settings.json", "file", ""), ("OUT.md", "file", ""))
_FAKE_HOOKS_ROSTER = _ROSTER_NAMES + _ROSTER + _ROSTER_DECL
_NO_ROSTER_DECL = _hooks_decl(("OUT.md", "file", ""))
# Each must fail closed (exit 2): an unscoped entry over a roster path would make gensrc_guard deny an
# adopter's edits to its own copy, and a roster that cannot be read statically must never read as empty.
_ROSTER_FAULTS = {
    "a tree entry over a roster path": _ROSTER_NAMES + _ROSTER + _hooks_decl((".claude/", "tree", "")),
    "a block entry on a roster path": _ROSTER_NAMES + _ROSTER + _hooks_decl(
        (".claude/settings.json", "block", "")),
    "a gen_hooks.py with no roster": _NO_ROSTER_DECL,
    "a computed roster item": (_ROSTER_NAMES + 'AUTHORING_ONLY_REL = (".aiqt/" + "authoring.toml",)\n'
                               + _NO_ROSTER_DECL),
    "a roster name bound twice": _ROSTER_NAMES + 'PROJECT_SETTINGS_REL = "x"\n' + _ROSTER + _NO_ROSTER_DECL,
    "a declaration carrying its own scope": _ROSTER_NAMES + _ROSTER + _hooks_decl(
        (".claude/settings.json", "file", ', "scope": "authoring"')),
}
# Case (q): every binding of the roster, or of a name it uses, other than its one plain top-level assignment
# fails closed (exit 2), one vector per binding form, each appended to the valid case (o) generator (the
# augmented and annotated forms also nested in a block, which only the whole-module walk reaches). The first
# is the reported fixture: a tuple-unpack rebinds PROJECT_SETTINGS_REL before the roster reads it, so the
# module binds .claude/other.json while a parse of the first assignment reads .claude/settings.json.
_ROSTER_REBINDS = {
    "a tuple-unpack rebinding a used name before the roster (the reported fixture)": (
        "PROJECT_SETTINGS_REL = \".claude/settings.json\"\n"
        "PROJECT_SETTINGS_REL, = (\".claude/other.json\",)\n"
        "AUTHORING_ONLY_REL = (PROJECT_SETTINGS_REL,)\n"
        "GENSRC_OUTPUTS = (\n"
        "    {\"target\": \".claude/other.json\", \"kind\": \"file\",\n"
        "     \"sources\": (\"src.txt\",),\n"
        "     \"regenerate\": \"python3 tools/gen_hooks.py\"},\n"
        ")\n"),
    "a tuple unpack of the roster": "AUTHORING_ONLY_REL, _x = (\".claude/other.json\",), 1\n",
    "a star unpack of a used name": "*PROJECT_SETTINGS_REL, = \"ab\"\n",
    "an augmented assignment of the roster": "AUTHORING_ONLY_REL += (\".claude/other.json\",)\n",
    "an augmented assignment nested in an if": "if True:\n    AUTHORING_ONLY_REL += (\".claude/other.json\",)\n",
    "an annotated assignment of a used name": "PROJECT_SETTINGS_REL: str = \".claude/other.json\"\n",
    "an annotated assignment nested in a try": ("try:\n    PROJECT_SETTINGS_REL: str = \".claude/other.json\"\n"
                                                "except Exception:\n    pass\n"),
    "a for target": "for PROJECT_SETTINGS_REL in (\".claude/other.json\",):\n    pass\n",
    "a with target": "with x as AUTHORING_MARKER_REL:\n    pass\n",
    "an import alias": "from os import sep as PROJECT_SETTINGS_REL\n",
    "a plain import": "import AUTHORING_ONLY_REL\n",
    "a def": "def PROJECT_SETTINGS_REL():\n    pass\n",
    "a class": "class AUTHORING_ONLY_REL:\n    pass\n",
    "a walrus": "(PROJECT_SETTINGS_REL := \".claude/other.json\")\n",
    "a del": "del AUTHORING_MARKER_REL\n",
    "a global declaration": "def _f():\n    global AUTHORING_ONLY_REL\n",
    "a plain assignment nested in an if": "if True:\n    PROJECT_SETTINGS_REL = \".claude/other.json\"\n",
}
# Case (r): label -> (generator file name, its bytes or text, expected exit). The first three are the reproduced
# divergences, each exit 0 under a UTF-8 text parse with no order or dunder rule: it read .claude/settings.json
# where an import binds another value (the module docstring, the module name, or, under the utf-7 cookie, the
# hidden second assignment the ASCII comment text "+AAo-" decodes to a newline before). A name bound after the
# roster and a dunder name bound before it keep the order and dunder rules apart. The declaration reader shares
# the bytes parse, so the same cookie cannot hide GENSRC_OUTPUTS = () behind the literal. A leading BOM and
# latin-1 bytes under a latin-1 cookie were refused by the UTF-8 text read and stay refused; a utf-8 cookie on
# the valid case (o) generator is no false reject.
_MARKER_NAME = 'AUTHORING_MARKER_REL = ".aiqt/authoring.toml"\n'
_UTF7 = "# coding: utf-7\n"
_SOURCE_READS = {
    "a module docstring read through __doc__, rebound after the roster (reproduced)": (
        "gen_hooks.py", ('""".claude/other.json"""\n' + _MARKER_NAME
                         + "AUTHORING_ONLY_REL = (AUTHORING_MARKER_REL, __doc__)\n"
                         + '__doc__ = ".claude/settings.json"\n' + _ROSTER_DECL), 2),
    "__name__ read by the roster, rebound after it (reproduced)": (
        "gen_hooks.py", (_MARKER_NAME + "AUTHORING_ONLY_REL = (AUTHORING_MARKER_REL, __name__)\n"
                         + '__name__ = ".claude/settings.json"\n' + _ROSTER_DECL), 2),
    "a utf-7 coding cookie hiding a rebinding of a used name (reproduced)": (
        "gen_hooks.py", (_UTF7 + _ROSTER_NAMES + 'x = 1  # +AAo-PROJECT_SETTINGS_REL = ".claude/other.json"\n'
                         + _ROSTER + _ROSTER_DECL), 2),
    "a used name bound after the roster": (
        "gen_hooks.py", (_MARKER_NAME + _ROSTER + 'PROJECT_SETTINGS_REL = ".claude/settings.json"\n'
                         + _ROSTER_DECL), 2),
    "a dunder name bound before the roster": (
        "gen_hooks.py", (_MARKER_NAME + '__doc__ = ".claude/settings.json"\n'
                         + "AUTHORING_ONLY_REL = (AUTHORING_MARKER_REL, __doc__)\n" + _ROSTER_DECL), 2),
    "a utf-7 coding cookie hiding GENSRC_OUTPUTS = () behind the literal": (
        "gen_selftestcookie.py", _UTF7 + _DECOY + "x = 1  # +AAo-GENSRC_OUTPUTS = ()\n", 2),
    "a leading BOM": ("gen_hooks.py", b"\xef\xbb\xbf" + _FAKE_HOOKS_ROSTER.encode("utf-8"), 2),
    "latin-1 bytes under a latin-1 cookie": (
        "gen_hooks.py", b"# coding: latin-1\n# \xe9\n" + _FAKE_HOOKS_ROSTER.encode("utf-8"), 2),
    "a utf-8 coding cookie on the valid generator": (
        "gen_hooks.py", "# -*- coding: utf-8 -*-\n" + _FAKE_HOOKS_ROSTER, 0),
}
# Case (s): label -> (generator file name, its text, False or the overflow ast.parse raises on it: its class and
# whether only the byte parse raises it). The real vector overflows the parser (MemoryError on the CPython 3.14 this
# was written against, at the first parse, the UTF-8 text one); the injection into every parse pins the other class,
# and the injection into the byte parse alone fails if that parse leaves the handler. Each vector must name the
# cause, so an interpreter on which the chain parses, failing the vector for another reason, cannot pass it.
_DEEP_SOURCE = "x = " + "-" * 200000 + "1\n"
_DEEP_CAUSE = "too deeply nested to parse"
_DEEP_READS = {
    "a chain of 200,000 unary minus signs as tools/gen_hooks.py": ("gen_hooks.py", _DEEP_SOURCE, False),
    "a chain of 200,000 unary minus signs as another generator": ("gen_selftestdeep.py", _DEEP_SOURCE, False),
    "a RecursionError injected into every parse of tools/gen_hooks.py": (
        "gen_hooks.py", _FAKE_HOOKS_ROSTER, (RecursionError, False)),
    "a MemoryError injected into the byte parse alone of tools/gen_hooks.py": (
        "gen_hooks.py", _FAKE_HOOKS_ROSTER, (MemoryError, True)),
}


# The OPF boundary (case t): a target with a .working component is an OPF store path, owned by the OPF deny
# hook, and must fail closed (exit 2) so the registry never registers a store path.
_FAKE_OPF_WORKING = '''# self-test: a target inside an OPF .working store
GENSRC_OUTPUTS = (
    {"target": "x/.working/TODO.md", "kind": "file",
     "sources": ("src.txt",), "regenerate": "x"},
)
'''

def _build_from(base, filename, text):
    """A synthetic repo whose only generator is `filename` carrying `text`, with the source it names and
    an empty .aiqt/ so reconcile can write the registry."""
    (base / "tools").mkdir(parents=True)
    (base / "tools" / filename).write_text(text, encoding="utf-8")
    (base / "src.txt").write_text("source\n", encoding="utf-8")
    (base / ".aiqt").mkdir()


def self_test_main():
    import io
    import shutil
    import tempfile
    from contextlib import redirect_stdout, redirect_stderr

    def run_quiet(root, check):
        # A raised SystemExit (e.g. reconcile's OSError path) is caught and returned as a non-int
        # sentinel so it registers as a FAILURE against any expected exit code, rather than aborting
        # the self-test or letting it exit early green.
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            try:
                return run(root, check)
            except SystemExit as exc:
                return "raised SystemExit({!r})".format(exc.code)

    def run_stderr(root, check):
        # run_quiet that also returns the captured stderr, so a vector can assert the cause it names.
        err = io.StringIO()
        with redirect_stdout(io.StringIO()), redirect_stderr(err):
            try:
                rc = run(root, check)
            except SystemExit as exc:
                rc = "raised SystemExit({!r})".format(exc.code)
        return rc, err.getvalue()

    try:
        tmp = Path(tempfile.mkdtemp(prefix="aiqt-gen-gensrc-selftest-"))
    except OSError as exc:
        print("SELF-TEST ERROR: no writable temporary directory: {}".format(exc), file=sys.stderr)
        return 2
    failures = []
    skipped = []
    unread_tools = None
    try:
        # (a) A conformant tree generates, then re-checks drift-clean, and the registry exists.
        good = tmp / "good"
        _build_from(good, "gen_selftestgood.py", _FAKE_GOOD)
        if run_quiet(good, check=False) != 0:
            failures.append("conformant tree: generation expected exit 0")
        if run_quiet(good, check=True) != 0:
            failures.append("conformant tree: regeneration expected drift-clean exit 0")
        registry = good / REGISTRY_REL
        if not registry.is_file():
            failures.append("conformant tree: expected {} to be written".format(REGISTRY_REL))

        # (b) A generator missing GENSRC_OUTPUTS fails closed (exit 2): the structural staleness guard.
        bad = tmp / "bad"
        _build_from(bad, "gen_selftestbad.py", _FAKE_BAD)
        if run_quiet(bad, check=True) != 2:
            failures.append("missing GENSRC_OUTPUTS expected exit 2 (fail-closed)")

        # (c) A mutated registry is caught by --check (exit 1).
        if registry.is_file():
            registry.write_text(registry.read_text(encoding="utf-8") + "\n", encoding="utf-8")
            if run_quiet(good, check=True) != 1:
                failures.append("mutated {} expected exit 1 (drift)".format(REGISTRY_REL))

        # (d) An EMPTY GENSRC_OUTPUTS=() fails closed (exit 2): the old empty-declaration bypass.
        empty = tmp / "empty"
        _build_from(empty, "gen_selftestempty.py", _FAKE_EMPTY)
        if run_quiet(empty, check=True) != 2:
            failures.append("empty GENSRC_OUTPUTS=() expected exit 2 (fail-closed)")

        # (e) A non-literal RHS beside an import-time raise SystemExit(0) fails closed (exit 2) and is
        #     never executed: static AST parsing, no import. If discovery imported the module the
        #     SystemExit(0) would surface as exit 0 (or crash the run), not the fail-closed exit 2.
        nonlit = tmp / "nonlit"
        _build_from(nonlit, "gen_selftestnonlit.py", _FAKE_NONLITERAL)
        if run_quiet(nonlit, check=True) != 2:
            failures.append("non-literal GENSRC_OUTPUTS (with import-time SystemExit) expected exit 2 "
                            "(fail-closed, not executed)")

        # (f) An unreadable tools/ dir fails closed (exit 2): os.scandir raises where a glob would
        #     silently yield nothing. Skipped where the runner can still read a chmod-0 dir
        #     (root/DAC-bypass), observed via os.access, as gen_hooks does.
        unread = tmp / "unread"
        _build_from(unread, "gen_selftestgood.py", _FAKE_GOOD)
        unread_tools = unread / "tools"
        os.chmod(unread_tools, 0)
        if os.access(unread_tools, os.R_OK):
            skipped.append("f unreadable-tools-dir")
        elif run_quiet(unread, check=True) != 2:
            failures.append("unreadable tools/ dir expected exit 2 (fail-closed)")
        os.chmod(unread_tools, 0o755)  # restore so cleanup can remove it
        unread_tools = None

        # (g) A file 'X' and a tree 'X/' fail closed (exit 2): slash-stripped canonical-dedup collision.
        canondup = tmp / "canondup"
        _build_from(canondup, "gen_selftestcanondup.py", _FAKE_CANON_DUP)
        if run_quiet(canondup, check=True) != 2:
            failures.append("canonical-duplicate targets (file 'X', tree 'X/') expected exit 2 "
                            "(fail-closed)")

        # (h) A malformed literal (a non-str kind []) fails closed with ValueError -> exit 2, never a
        #     bare TypeError from the set-membership test.
        badtype = tmp / "badtype"
        _build_from(badtype, "gen_selftestbadtype.py", _FAKE_BADTYPE)
        if run_quiet(badtype, check=True) != 2:
            failures.append("malformed non-str kind ([]) expected exit 2 (fail-closed ValueError)")

        # (i) The single-binding invariant, one case per Python binding mechanism: each rebind of
        #     GENSRC_OUTPUTS beside a valid decoy literal fails closed (exit 2). Closes the whole rebind
        #     class an earlier node-type enumeration missed (walrus/tuple-unpack/for/with/except/
        #     import-as/def/class).
        for idx, (label, snippet) in enumerate(_REBINDS.items()):
            rbdir = tmp / "rebind{}".format(idx)
            _build_from(rbdir, "gen_selftestrebind.py", _DECOY + snippet)
            if run_quiet(rbdir, check=True) != 2:
                failures.append("{} expected exit 2 (single-binding invariant)".format(label))

        # (i+) The positive import case: `import GENSRC_OUTPUTS.child as y` binds only y, NOT
        #      GENSRC_OUTPUTS, so beside a valid decoy it must generate cleanly (exit 0) - the dotted-root
        #      reject must not false-positive on an aliased dotted import.
        aliasok = tmp / "aliasok"
        _build_from(aliasok, "gen_selftestaliasok.py", _FAKE_IMPORT_ALIAS_OK)
        if run_quiet(aliasok, check=False) != 0:
            failures.append("import GENSRC_OUTPUTS.child as y (binds y only) expected exit 0 (no false "
                            "reject)")

        # (j) A non-str entry KEY fails closed with ValueError -> exit 2 (not a bare TypeError).
        badkey = tmp / "badkey"
        _build_from(badkey, "gen_selftestbadkey.py", _FAKE_BADKEY)
        if run_quiet(badkey, check=True) != 2:
            failures.append("non-str entry key expected exit 2 (fail-closed ValueError)")

        # (k) Trailing-'/' by kind: a file or block target carrying it, and a tree target lacking it, all
        #     fail closed (exit 2).
        for idx, (label, text) in enumerate((("file with trailing '/'", _FAKE_FILE_SLASH),
                                            ("block with trailing '/'", _FAKE_BLOCK_SLASH),
                                            ("tree without trailing '/'", _FAKE_TREE_NOSLASH))):
            slashdir = tmp / "slash{}".format(idx)
            _build_from(slashdir, "gen_selftestslash.py", text)
            if run_quiet(slashdir, check=True) != 2:
                failures.append("{} expected exit 2 (trailing-'/' by kind)".format(label))

        # (l) Tree-descendant overlap fails closed (exit 2) in both declaration orders and for a nested
        #     tree, while a sibling on a component boundary ('X/' vs 'XYZ') does NOT collide (exit 0).
        for idx, (label, text) in enumerate((("tree 'X/' + file 'X/c'", _FAKE_OVERLAP_TF),
                                            ("file 'X/c' + tree 'X/'", _FAKE_OVERLAP_FT),
                                            ("tree 'X/' + tree 'X/Y/'", _FAKE_OVERLAP_TT))):
            ovdir = tmp / "overlap{}".format(idx)
            _build_from(ovdir, "gen_selftestoverlap.py", text)
            if run_quiet(ovdir, check=True) != 2:
                failures.append("tree-descendant overlap {} expected exit 2 (fail-closed)".format(label))
        okdir = tmp / "overlapok"
        _build_from(okdir, "gen_selftestoverlapok.py", _FAKE_OVERLAP_OK)
        if run_quiet(okdir, check=False) != 0:
            failures.append("tree 'X/' beside sibling file 'XYZ' expected exit 0 (no false collision)")

        # (m) Zero generators (a tools/ dir with no gen_*.py besides this tool) fails closed (exit 2): a
        #     repo carrying the registry tool but no generators is malformed, never a silent self-only run.
        zerogen = tmp / "zerogen"
        (zerogen / "tools").mkdir(parents=True)
        (zerogen / ".aiqt").mkdir()
        (zerogen / "tools" / "helper.py").write_text("x = 1\n", encoding="utf-8")  # not a gen_*.py
        if run_quiet(zerogen, check=True) != 2:
            failures.append("zero generators expected exit 2 (fail-closed)")

        # (n) An invalid-UTF-8 existing .aiqt/gensrc.json target fails closed (exit 2) on --check: reconcile
        #     reads the target as UTF-8, and the shared _gen_common.reconcile guard raises SystemExit(2) on
        #     the decode error rather than a raw traceback. run() makes the plain reconcile call, so that
        #     raise propagates and run_quiet records it as the "raised SystemExit(2)" sentinel (the same
        #     process exit 2 a real CLI run produces).
        unicode_tree = tmp / "unicode"
        _build_from(unicode_tree, "gen_selftestgood.py", _FAKE_GOOD)
        if run_quiet(unicode_tree, check=False) != 0:
            failures.append("unicode case: initial generation expected exit 0")
        (unicode_tree / REGISTRY_REL).write_bytes(b"\xff\xfe not valid utf-8 \x80\x81")
        if run_quiet(unicode_tree, check=True) != "raised SystemExit(2)":
            failures.append("invalid-UTF-8 registry target expected exit 2 (fail-closed)")

        # (o) The authoring-only roster: a tools/gen_hooks.py naming AUTHORING_ONLY_REL through module-level
        #     names marks exactly its roster entry "scope": "authoring"; the sibling entry and the registry's
        #     own entry carry no scope key at all.
        roster = tmp / "roster"
        _build_from(roster, "gen_hooks.py", _FAKE_HOOKS_ROSTER)
        if run_quiet(roster, check=False) != 0 or run_quiet(roster, check=True) != 0:
            failures.append("authoring-only roster: expected a clean generate and a drift-clean check")
        try:
            scopes = {e["target"]: e.get("scope", "no scope key") for e in
                      json.loads((roster / REGISTRY_REL).read_text(encoding="utf-8"))["generated"]}
        except (OSError, ValueError, KeyError, TypeError) as exc:
            scopes = "unreadable ({})".format(exc)
        want = {".claude/settings.json": "authoring", "OUT.md": "no scope key",
                ".aiqt/gensrc.json": "no scope key"}
        if scopes != want:
            failures.append("authoring-only roster: expected scopes {!r}, got {!r}".format(want, scopes))

        # (p) Every roster fault fails closed (exit 2): a roster path under a tree entry or declared as a block
        #     (unscopable), a gen_hooks.py binding no roster, a computed roster item, a roster name bound twice,
        #     and a declaration that carries its own scope (the roster is the one source of the scope).
        for idx, (label, text) in enumerate(_ROSTER_FAULTS.items()):
            rdir = tmp / "rosterfault{}".format(idx)
            _build_from(rdir, "gen_hooks.py", text)
            if run_quiet(rdir, check=True) != 2:
                failures.append("authoring-only roster: {} expected exit 2 (fail-closed)".format(label))

        # (q) Every other binding of the roster or of a name it uses fails closed (exit 2), so the parsed roster
        #     is the bound one. The reported fixture is a whole generator; each other snippet is appended to the
        #     valid case (o) generator, which alone generates cleanly there.
        for idx, (label, snippet) in enumerate(_ROSTER_REBINDS.items()):
            qdir = tmp / "rosterrebind{}".format(idx)
            text = snippet if "GENSRC_OUTPUTS" in snippet else _FAKE_HOOKS_ROSTER + snippet
            _build_from(qdir, "gen_hooks.py", text)
            if run_quiet(qdir, check=False) != 2:
                failures.append("authoring-only roster: {} expected exit 2 (single binding)".format(label))

        # (r) A source is read as the interpreter binds it, and the byte forms the UTF-8 text read refused stay
        #     refused; each generator is written as bytes, so a cookie or a BOM reaches the reader unchanged.
        for idx, (label, (filename, data, want)) in enumerate(_SOURCE_READS.items()):
            raw = data if isinstance(data, bytes) else data.encode("utf-8")
            try:
                ast.parse(raw)  # the interpreter compiles each vector, so only the reader can refuse it
            except (SyntaxError, ValueError) as exc:
                failures.append("source read: {} does not compile from its bytes ({})".format(label, exc))
            sdir = tmp / "sourceread{}".format(idx)
            _build_from(sdir, filename, "")
            (sdir / "tools" / filename).write_bytes(raw)
            if run_quiet(sdir, check=False) != want:
                failures.append("source read: {} expected exit {}".format(label, want))

        # (s) A parser stack overflow is exit 2, never a traceback; one that escapes run() is caught here and named.
        real_parse = ast.parse

        def overflow(source, *args, **kwargs):
            exc_class, bytes_only = inject
            if (str(kwargs.get("filename", "")).endswith("gen_hooks.py")
                    and (isinstance(source, bytes) or not bytes_only)):
                raise exc_class("injected parser overflow")
            return real_parse(source, *args, **kwargs)

        for idx, (label, (filename, text, inject)) in enumerate(_DEEP_READS.items()):
            ddir = tmp / "deep{}".format(idx)
            _build_from(ddir, filename, text)
            if inject:
                ast.parse = overflow
            rc, err = None, ""
            try:
                rc, err = run_stderr(ddir, check=False)
                # Discovery reads a gen_hooks.py declaration before its roster, so the roster read is also called
                # alone and must raise ValueError naming the cause (run maps it to exit 2).
                if filename == ROSTER_GEN:
                    read_authoring_roster(ddir / "tools")
                    rc = "a roster read that raised nothing"
            except ValueError as exc:
                if _DEEP_CAUSE not in str(exc):
                    rc = "a roster read that raised ValueError without the cause ({})".format(exc)
            except (MemoryError, RecursionError, SyntaxError) as exc:
                rc = "raised {}".format(type(exc).__name__)
            finally:
                ast.parse = real_parse
            if rc != 2 or _DEEP_CAUSE not in err:
                failures.append("parser overflow: {} expected exit 2 naming {!r}, got {}".format(
                    label, _DEEP_CAUSE, rc))

        # (t) The OPF boundary: a target with a .working component fails closed (exit 2) NAMING the component
        #     and the target, so an unrelated exit 2 cannot pass this case; the OPF deny hook owns the store
        #     tree, so the registry never registers a store path.
        opfdir = tmp / "opfworking"
        _build_from(opfdir, "gen_selftestopf.py", _FAKE_OPF_WORKING)
        rc, err = run_stderr(opfdir, check=True)
        if rc != 2 or ".working component" not in err or "x/.working/TODO.md" not in err:
            failures.append("a .working target (x/.working/TODO.md) expected exit 2 naming the .working "
                            "component (OPF boundary), got {} ({!r})".format(rc, err.strip()))
        # .aiqt/ above the registry symlinked outside the root fails closed (exit 2) in --check and write mode
        # with the registry there untouched; symlinked inside the root, it is read and the registry rewritten.
        failures.extend(linked_dir_failures(tmp, "aiqt", lambda base: _build_from(
            base, "gen_selftestgood.py", _FAKE_GOOD), run, ".aiqt", REGISTRY_REL))
    finally:
        if unread_tools is not None:
            os.chmod(unread_tools, 0o755)  # restore even on an unexpected early exit
        shutil.rmtree(tmp, ignore_errors=True)

    if failures:
        print("SELF-TEST FAIL:")
        for failure in failures:
            print("  - " + failure)
        return 1
    note = ("" if not skipped else
            " NOTE: skipped {} case(s) the runner cannot exercise (chmod-0 still readable): {}"
            .format(len(skipped), ", ".join(skipped)))
    print("SELF-TEST PASS: a conformant tree generates and regenerates drift-clean; a mutated "
          ".aiqt/gensrc.json fails --check (exit 1); a tree 'X/' beside a sibling file 'XYZ' generates "
          "cleanly (no false collision); and a generator that omits GENSRC_OUTPUTS, an empty "
          "GENSRC_OUTPUTS=(), a non-literal declaration beside an import-time SystemExit (proving static "
          "parse, no import), an unreadable tools/ dir, zero generators, canonical-duplicate targets (a "
          "file 'X' and a tree 'X/'), every single-binding-invariant rebind (walrus/tuple-unpack/for/with/"
          "except/import-as/dotted-import-root/wildcard-import/MatchMapping-rest/def/class; while an "
          "aliased dotted import binding only y generates cleanly), a non-str kind, a non-str key, each "
          "trailing-'/'-by-kind mismatch, "
          "a tree-descendant overlap (both orders and a nested tree), and an invalid-UTF-8 registry target "
          "all fail closed (exit 2); the gen_hooks AUTHORING_ONLY_REL roster scopes exactly its entry "
          "\"authoring\", and every roster fault (a roster path under a tree or as a block, an unbound, "
          "computed or doubly bound roster, a self-declared scope, and every other binding of the roster or a "
          "name it uses) fails closed (exit 2); a roster name read through __doc__ or __name__, bound after the "
          "roster, or a dunder, a utf-7 cookie hiding a rebinding, a leading BOM and non-UTF-8 bytes fail closed "
          "(exit 2), while a utf-8 cookie generates; a parser stack overflow (a chain of 200,000 unary minus "
          "signs as tools/gen_hooks.py or another generator, a RecursionError injected into every parse and a "
          "MemoryError into the byte parse alone) fails closed (exit 2) naming its cause; and a target with a "
          ".working component (an OPF store path) fails closed (exit 2)"
          + note)
    return 0


if __name__ == "__main__":
    sys.exit(main())
