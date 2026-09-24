#!/usr/bin/env python3
"""Generate the Claude Code enforcement-plugin surface from the hooks manifest (GD-24).

The pack's mechanical-enforcement controls are declared in .aiqt/core/hooks/manifest.toml (one
[[hook]] per control) with the handler implementations beside it under .aiqt/core/hooks/scripts/.
This tool renders them into the shipped, NESTED plugin surface under plugin/aiqt-guardrails-hooks/:
  plugin/aiqt-guardrails-hooks/hooks/hooks.json             the plugin hook config
  plugin/aiqt-guardrails-hooks/hooks/scripts/aiqt_hooks.py  the dispatcher, copied byte-identical
  plugin/aiqt-guardrails-hooks/.claude-plugin/plugin.json   the plugin manifest
The plugin is nested (not the whole repo) so an adopter installs only the plugin, never the authoring
repo. The .aiqt/core/hooks/ tree is the single edit point; the generated surface is always
regenerated, never hand-edited, and plugin/aiqt-guardrails-hooks/hooks/ is a RESERVED subtree (100%
generated, orphan-pruned) exactly like the .cursor/rules/aiqt-guardrails/ adapter. Validation is
fail-closed: every manifest `rules` entry must name a real corpus-id in .aiqt/core/rules/ (via
gen_rules.load_corpus), the event must be on the known-event whitelist, a tool event must carry a
compilable matcher and a non-tool event must not, `default` must be an allowed keyword with the
non-blocking `warn` legal only on a WARN_EVENTS event (PostToolUse, UserPromptSubmit, Stop,
SubagentStop, SessionStart) or under an explicit bake stage, the named handler must exist in the
source script, and `residue` must be non-empty, so a typo can never silently no-op an enforcement
control.
  gen_hooks.py             regenerate the plugin surface
  gen_hooks.py --check     fail (exit 1) on drift; exit 2 on a malformed source or a read/write failure
  gen_hooks.py --self-test build synthetic trees and assert the generator's own fail-closed invariants
"""
import ast
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _gen_common import repo_root, load_toml  # noqa: E402
from _standards import dir_present  # noqa: E402
from gen_rules import load_corpus  # noqa: E402

# The nested plugin surface, as repo-root-relative forward-slash paths. hooks/ under the plugin root is
# the reserved subtree (fully generated, orphan-scanned); .claude-plugin/ is NOT walked, only its
# plugin.json is managed, so a future hand-authored sibling stays out of reach of the prune.
PLUGIN_ROOT_PARTS = ("plugin", "aiqt-guardrails-hooks")
PLUGIN_ROOT_REL = "/".join(PLUGIN_ROOT_PARTS)
HOOKS_SUBTREE_PARTS = PLUGIN_ROOT_PARTS + ("hooks",)
HOOKS_JSON_REL = PLUGIN_ROOT_REL + "/hooks/hooks.json"
SCRIPT_REL = PLUGIN_ROOT_REL + "/hooks/scripts/aiqt_hooks.py"
PLUGIN_JSON_REL = PLUGIN_ROOT_REL + "/.claude-plugin/plugin.json"
SCRIPT_NAME = "aiqt_hooks.py"
# ${CLAUDE_PLUGIN_ROOT} resolves to the installed plugin dir, so the script path is relative to it
# regardless of where the plugin is nested in this authoring repo.
SCRIPT_PLUGIN_PATH = "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/" + SCRIPT_NAME

# The event whitelist, in the fixed render order (doc-confirmed event names, 2026-08-17; SessionStart
# and TeammateIdle re-confirmed against the hooks reference 2026-08-29). A typo'd event must fail
# closed here, never silently produce a hook no platform will fire.
KNOWN_EVENTS = ("PreToolUse", "PostToolUse", "UserPromptSubmit", "Stop", "SubagentStop",
                "SessionStart", "TeammateIdle")
TOOL_EVENTS = {"PreToolUse", "PostToolUse"}  # these require a matcher; the others must omit it
# A non-blocking "warn" default is legal on every event EXCEPT PreToolUse: on a PreToolUse control a
# silent non-decision would weaken an enforcement point, so warn there requires the explicit,
# recorded stage = "bake" posture below. SessionStart cannot block at the platform (exit 2 only shows
# stderr, doc-confirmed 2026-08-29), so a SessionStart control MUST declare warn: a block/ask claim
# there would be an overclaim the platform cannot honour.
WARN_EVENTS = {"PostToolUse", "UserPromptSubmit", "Stop", "SubagentStop", "SessionStart",
               "TeammateIdle"}
NONBLOCK_EVENTS = {"SessionStart"}  # the platform cannot block these: default must be "warn"
STAGES = {"bake", "enforce"}  # stage is optional authoring metadata; absent means "enforce"

HOOK_KEYS = {"id", "rules", "platform", "event", "matcher", "handler", "default", "class", "residue",
             "stage"}
REQUIRED_HOOK_KEYS = HOOK_KEYS - {"matcher", "stage"}
PLUGIN_KEYS = ("name", "version", "description", "author-name", "author-email", "homepage")
PLATFORMS = {"claude-code"}
DEFAULTS = {"block", "warn"}  # no-ask posture: "ask" is not a permitted default
CLASSES = {"a", "b", "c", "d"}
SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")
ID_RE = re.compile(r"^[a-z][a-z0-9-]*$")     # a stable kebab-case control id
CID_RE = re.compile(r"^[a-z0-9]{6,}$")        # a corpus-id (matches gen_rules' own shape)
HANDLER_RE = re.compile(r"^[a-z][a-z0-9_]*$")
TIMEOUT = 10  # seconds; every control is a lexical scan, far under this

# Declares this generator's outputs for the gensrc registry (tools/gen_gensrc.py); additive metadata
# only, it does not affect what this generator produces.
GENSRC_OUTPUTS = (
    {"target": "plugin/aiqt-guardrails-hooks/hooks/", "kind": "tree",
     "sources": (".aiqt/core/hooks/manifest.toml", ".aiqt/core/hooks/scripts/aiqt_hooks.py"),
     "regenerate": "python3 tools/gen_hooks.py"},
    {"target": "plugin/aiqt-guardrails-hooks/.claude-plugin/plugin.json", "kind": "file",
     "sources": (".aiqt/core/hooks/manifest.toml", ".aiqt/core/hooks/scripts/aiqt_hooks.py"),
     "regenerate": "python3 tools/gen_hooks.py"},
)


def _exists(path):
    """Fail-closed existence probe (gen_adapters idiom): Path.exists() swallows EACCES on an
    unreadable parent (the nested plugin dirs each have one), which would mask a present-but-unreadable
    target as absent. Path.stat() raises on EACCES, so absent -> False and an unreadable parent ->
    OSError, which the caller's fail-closed try turns into exit 2."""
    try:
        path.stat()
    except FileNotFoundError:
        return False
    return True


def _req_str(table, key, where):
    value = table.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError("{}: field '{}' must be a non-empty string".format(where, key))
    return value


def load_manifest(path):
    """Parse and fully validate the hooks manifest. Returns (plugin_table, hook_tables). Raises
    ValueError on any malformed field (tomllib.TOMLDecodeError subclasses ValueError) and OSError on
    an unreadable file; the caller's fail-closed try maps both to exit 2.

    Control identity is the freeform `id` (unique, kebab-case), NOT the corpus-id: one rule may back
    more than one control (cnsdif has a Stop layer and a PreToolUse diff-source layer), so the corpus
    linkage lives in `rules` (cross-checked against load_corpus) and `id` keys the control."""
    data = load_toml(path)
    extra = set(data) - {"plugin", "hook"}
    if extra:
        raise ValueError("{}: unknown top-level key(s): {}".format(path.name, ", ".join(sorted(extra))))
    plugin = data.get("plugin")
    if not isinstance(plugin, dict):
        raise ValueError("{}: a [plugin] table is required".format(path.name))
    p_extra = set(plugin) - set(PLUGIN_KEYS)
    if p_extra:
        raise ValueError("{}: unknown [plugin] key(s): {}".format(path.name, ", ".join(sorted(p_extra))))
    for key in PLUGIN_KEYS:
        _req_str(plugin, key, "{}: [plugin]".format(path.name))
    if not SEMVER_RE.match(plugin["version"]):
        raise ValueError("{}: [plugin] version must be SemVer (MAJOR.MINOR.PATCH)".format(path.name))
    hooks = data.get("hook")
    if not isinstance(hooks, list) or not hooks:
        raise ValueError("{}: at least one [[hook]] entry is required".format(path.name))
    seen_ids = set()
    seen_handlers = set()
    for hook in hooks:
        if not isinstance(hook, dict):
            raise ValueError("{}: every [[hook]] must be a table".format(path.name))
        missing = REQUIRED_HOOK_KEYS - set(hook)
        if missing:
            raise ValueError("{}: [[hook]] missing key(s): {}".format(path.name, ", ".join(sorted(missing))))
        extra = set(hook) - HOOK_KEYS
        if extra:
            raise ValueError("{}: [[hook]] unknown key(s): {}".format(path.name, ", ".join(sorted(extra))))
        hid = _req_str(hook, "id", "{}: [[hook]]".format(path.name))
        where = "{}: [[hook]] {}".format(path.name, hid)
        if not ID_RE.match(hid):
            raise ValueError("{}: id must match ^[a-z][a-z0-9-]*$ (a kebab-case control id)".format(where))
        if hid in seen_ids:
            raise ValueError("{}: duplicate hook id".format(where))
        seen_ids.add(hid)
        rules = hook.get("rules")
        if not isinstance(rules, list) or not rules or not all(isinstance(r, str) and r for r in rules):
            raise ValueError("{}: rules must be a non-empty list of corpus-id strings".format(where))
        for rule in rules:
            if not CID_RE.match(rule):
                raise ValueError("{}: rules entry '{}' is not a corpus-id shape (^[a-z0-9]{{6,}}$)"
                                 .format(where, rule))
        if _req_str(hook, "platform", where) not in PLATFORMS:
            raise ValueError("{}: platform must be one of {}".format(where, "/".join(sorted(PLATFORMS))))
        event = _req_str(hook, "event", where)
        if event not in KNOWN_EVENTS:
            raise ValueError("{}: event '{}' is not a known hook event".format(where, event))
        if event in TOOL_EVENTS:
            matcher = _req_str(hook, "matcher", where)
            try:
                re.compile(matcher)
            except re.error as exc:
                raise ValueError("{}: matcher is not a valid regex: {}".format(where, exc))
        elif "matcher" in hook:
            raise ValueError("{}: matcher is forbidden on the non-tool event {}".format(where, event))
        handler = _req_str(hook, "handler", where)
        if not HANDLER_RE.match(handler):
            raise ValueError("{}: handler must be a python identifier".format(where))
        if handler in seen_handlers:
            raise ValueError("{}: duplicate handler '{}' (each control has its own)".format(where, handler))
        seen_handlers.add(handler)
        default = _req_str(hook, "default", where)
        if default not in DEFAULTS:
            raise ValueError("{}: default must be one of {}".format(where, "/".join(sorted(DEFAULTS))))
        stage = hook.get("stage", "enforce")
        if not isinstance(stage, str) or stage not in STAGES:
            raise ValueError("{}: stage must be one of {}".format(where, "/".join(sorted(STAGES))))
        if default == "warn" and event not in WARN_EVENTS and stage != "bake":
            raise ValueError("{}: default 'warn' is legal only on a {} event, or under an explicit "
                             "stage = \"bake\" posture on PreToolUse, not {}"
                             .format(where, "/".join(sorted(WARN_EVENTS)), event))
        if event in NONBLOCK_EVENTS and default != "warn":
            raise ValueError("{}: event {} cannot block at the platform, so default must be 'warn' "
                             "(a block/ask claim there is an overclaim)".format(where, event))
        if _req_str(hook, "class", where) not in CLASSES:
            raise ValueError("{}: class must be the EN-5 letter a/b/c/d".format(where))
        _req_str(hook, "residue", where)  # required, never empty: the control's honest coverage gap
    return plugin, hooks


# Read-only uses a dispatcher map may appear in besides its one binding: a method call that only reads, and
# a builtin call that only reads its argument (the dispatcher's own `sorted(HANDLERS)` usage line).
_DISPATCH_READ_METHODS = frozenset(("get", "keys", "values", "items"))
_DISPATCH_READ_BUILTINS = frozenset(("sorted", "len", "set", "frozenset", "list", "tuple"))


def _dispatch_load_is_read_only(node, parents):
    """Whether a Load of the dispatcher name is a PURE READ that cannot mutate or alias the map: the value
    of a Load-context subscript (HANDLERS[k]), the right side of an `in`/`not in` membership test, the
    receiver of a read-only method call (HANDLERS.get(k), .keys(), .values(), .items()), or the sole
    positional argument of a read-only builtin (sorted/len/set/frozenset/list/tuple). Every other Load (an
    alias `x = HANDLERS`, a mutating method `.update()`/`.clear()`/`.pop()`/`.setdefault()`, an attribute
    read, an argument to an arbitrary call, a return, a container element) can let the map change after
    its literal, so it is not read-only."""
    parent = parents.get(id(node))
    if isinstance(parent, ast.Subscript) and parent.value is node:
        return isinstance(parent.ctx, ast.Load)
    if isinstance(parent, ast.Compare) and node in parent.comparators:
        idx = parent.comparators.index(node)
        return isinstance(parent.ops[idx], (ast.In, ast.NotIn))
    if isinstance(parent, ast.Attribute) and parent.value is node:
        call = parents.get(id(parent))
        return (parent.attr in _DISPATCH_READ_METHODS and isinstance(call, ast.Call)
                and call.func is parent)
    if isinstance(parent, ast.Call) and parent.args == [node] and not parent.keywords:
        return isinstance(parent.func, ast.Name) and parent.func.id in _DISPATCH_READ_BUILTINS
    return False


def _module_dict(script_text, dict_name):
    """The single module-level `<dict_name> = { ... }` assignment's ast.Dict node, extracted STATICALLY
    from the parsed script. ast.parse only builds the syntax tree; it does NOT execute or import the
    script, so this honours SECI-config-is-executable (the dispatcher is never run to read its keys).

    WHOLE-AST SINGLE-BINDING CHECK (round 12, the same shape as gen_gensrc._read_declaration). The ONLY
    accepted binding is ONE top-level plain `Name = {...}` (ast.Assign with a single Name target) or
    annotated `Name: T = {...}` (ast.AnnAssign) whose value is a dict LITERAL. The WHOLE tree is then
    walked, at every depth (module control flow, functions, classes, comprehensions), and the map fails
    closed (None, so the caller reads every handler as undispatchable) on ANY other binding or mutation of
    the name: a Store- or Del-context Name (a second, nested, or conditional assignment, an augmented or
    annotated assignment, a del, a walrus, a tuple or star unpack, a for, with, or comprehension target);
    an import alias, including a dotted-root `import HANDLERS.x` and a wildcard `from x import *`; a
    def or class; an except-as; a match capture, including a mapping rest; a parameter; a global or
    nonlocal declaration; and ANY Load of the name that is not a pure read (see
    _dispatch_load_is_read_only), which closes a subscript or attribute store (`HANDLERS[k] = v`), a
    mutating method call (`HANDLERS.update(..)`, `.clear()`, `.pop(..)`), and an alias that could be
    mutated later (`x = HANDLERS`). A chained `A = HANDLERS = {...}` is rejected (its sibling target is
    an alias), and a single binding whose value is not a dict literal (a call, comprehension, name, or
    merge) returns None.

    DISCLOSED RESIDUAL: the name is treated as RESERVED, so an unrelated local reuse of it inside a
    function is also rejected (an over-catch in the safe direction). Truly DYNAMIC binding
    (globals()["HANDLERS"] = .., exec, setattr on the module, a mutation through an object obtained
    without naming the map) is beyond static analysis and not claimed; the dispatch-path self-tests,
    which run the real dispatcher, are the compensating layer."""
    try:
        tree = ast.parse(script_text)
    except (SyntaxError, ValueError):
        return None
    literal_assign = [
        node for node in tree.body
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name) and node.targets[0].id == dict_name)
        or (isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
            and node.target.id == dict_name and node.value is not None)]
    if len(literal_assign) != 1 or not isinstance(literal_assign[0].value, ast.Dict):
        return None
    binding = literal_assign[0]
    allowed = {id(binding.targets[0] if isinstance(binding, ast.Assign) else binding.target)}
    parents = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[id(child)] = node
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == dict_name:
            if id(node) in allowed:
                continue
            if isinstance(node.ctx, (ast.Store, ast.Del)):
                return None
            if not _dispatch_load_is_read_only(node, parents):
                return None
        elif isinstance(node, ast.alias):
            bound = node.asname if node.asname is not None else node.name.partition(".")[0]
            if bound == dict_name or (node.asname is None and node.name == "*"):
                return None
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name == dict_name:
                return None
        elif isinstance(node, ast.ExceptHandler):
            if node.name == dict_name:
                return None
        elif isinstance(node, (ast.MatchAs, ast.MatchStar)):
            if node.name == dict_name:
                return None
        elif isinstance(node, ast.MatchMapping):
            if node.rest == dict_name:
                return None
        elif isinstance(node, ast.arg):
            if node.arg == dict_name:
                return None
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            if dict_name in node.names:
                return None
    return binding.value


def _dispatch_keys(script_text, dict_name):
    """The handler keys registered in a dispatcher dict literal (HANDLERS / HANDLER_EVENT), extracted
    STRUCTURALLY from the parsed AST rather than by regex, so a string-literal VALUE that merely LOOKS
    like a `"name": name,` entry can never contribute a phantom key (round 10: a regex over raw text read
    such a phantom and let cross_check accept a manifest handler the runtime HANDLERS lacks, fail-OPEN). A
    dict that cannot be located, is duplicated, or carries ANY non-string-literal key (a computed key or a
    `**` spread, whose key node is not a str Constant) yields the empty set, failing the membership check
    closed per check-fails-closed-on-unreadable. ast.parse is a static parse; it does not run the
    script."""
    node = _module_dict(script_text, dict_name)
    if node is None:
        return set()
    keys = set()
    for k in node.keys:
        if not (isinstance(k, ast.Constant) and isinstance(k.value, str)):
            return set()  # a non-string-literal key / ** spread: ambiguous dict form, fail whole closed
        keys.add(k.value)
    return keys


def _dispatch_event_map(script_text):
    """Map handler name -> the event STRING its HANDLER_EVENT entry resolves to, for the value-equality
    half of the dispatchability gate (A2), extracted STRUCTURALLY. A value is either the bare PRETOOL Name
    (-> 'PreToolUse') or a string literal (-> its value); a value in neither form contributes no mapping,
    so the caller's equality check fails closed. A non-string-literal key or an ambiguous dict form yields
    the empty map (fail closed). ast.parse does not execute the script."""
    node = _module_dict(script_text, "HANDLER_EVENT")
    if node is None:
        return {}
    mapping = {}
    for k, v in zip(node.keys, node.values):
        if not (isinstance(k, ast.Constant) and isinstance(k.value, str)):
            return {}
        if isinstance(v, ast.Name) and v.id == "PRETOOL":
            mapping[k.value] = "PreToolUse"
        elif isinstance(v, ast.Constant) and isinstance(v.value, str):
            mapping[k.value] = v.value
    return mapping


def cross_check(hooks, corpus_ids, script_text, name):
    """The manifest's outward references, fail-closed: every cited corpus-id exists in the loaded
    corpus (the check_mappings no-orphan pattern), and every named handler is defined in the source
    script, so a rename can never silently orphan an enforcement control."""
    dispatch = _dispatch_keys(script_text, "HANDLERS")
    dispatch_events = _dispatch_keys(script_text, "HANDLER_EVENT")
    event_map = _dispatch_event_map(script_text)
    for hook in hooks:
        where = "{}: [[hook]] {}".format(name, hook["id"])
        for rule in hook["rules"]:
            if rule not in corpus_ids:
                raise ValueError("{}: rules entry '{}' is not a corpus-id in .aiqt/core/rules/"
                                 .format(where, rule))
        if not re.search(r"^def {}\(".format(re.escape(hook["handler"])), script_text, re.M):
            raise ValueError("{}: handler '{}' is not defined in scripts/{}"
                             .format(where, hook["handler"], SCRIPT_NAME))
        # grdinp/observe-before-asserting-behaviour: a DEFINED handler is not a DISPATCHABLE one. The
        # manifest declares the platform's invocation (python3 -I aiqt_hooks.py <handler>), so the handler
        # MUST be a key in the dispatcher's HANDLERS and HANDLER_EVENT maps, or main() rejects the mode
        # (exit 2) and the control never runs though its `def` and its direct-call self-tests are green.
        if hook["handler"] not in dispatch:
            raise ValueError("{}: handler '{}' is defined but not registered in HANDLERS in scripts/{}; "
                             "the dispatcher cannot route to it".format(
                                 where, hook["handler"], SCRIPT_NAME))
        if hook["handler"] not in dispatch_events:
            raise ValueError("{}: handler '{}' is not registered in HANDLER_EVENT in scripts/{}".format(
                where, hook["handler"], SCRIPT_NAME))
        declared_event = event_map.get(hook["handler"])
        if declared_event != hook["event"]:
            # grdinp: a DEFINED, key-registered handler under the WRONG event still mis-routes. Compare
            # the registered event VALUE to the manifest's declared event; a value that cannot be resolved
            # (absent from the map) fails closed here too.
            raise ValueError(
                "{}: handler '{}' is registered in HANDLER_EVENT as {!r} but the manifest declares "
                "event {!r}; the dispatcher would route it under the wrong event".format(
                    where, hook["handler"], declared_event, hook["event"]))


def render_hooks_json(hooks):
    """The plugin hooks.json, deterministic: events in KNOWN_EVENTS order, entries sorted by id,
    json.dumps(indent=2) plus a trailing newline. Shape per the doc-confirmed plugin schema:
    {description, hooks: {<Event>: [{matcher?, hooks: [{type, command, args, timeout}]}]}}; the command
    is python3 with the script path and handler as args (no shell string, so nothing is shell-quoted);
    matcher is omitted for non-tool events. Every launcher runs isolated: args[0] is "-I" (Python's
    isolated mode), placed before the script path, so a file written beside the dispatcher cannot
    shadow a standard-library import and silently neuter the hook. The generator asserts this invariant
    in its --self-test, and the check_python_launcher_isolation gate re-checks the rendered surface."""
    events = {}
    for hook in sorted(hooks, key=lambda h: (KNOWN_EVENTS.index(h["event"]), h["id"])):
        entry = {}
        if "matcher" in hook:
            entry["matcher"] = hook["matcher"]
        entry["hooks"] = [{"type": "command",
                           "command": "python3",
                           "args": ["-I", SCRIPT_PLUGIN_PATH, hook["handler"]],
                           "timeout": TIMEOUT}]
        events.setdefault(hook["event"], []).append(entry)
    obj = {"description": "AIQT Guardrails mechanical-enforcement hooks, generated from "
                          ".aiqt/core/hooks/manifest.toml by tools/gen_hooks.py; do not hand-edit.",
           "hooks": events}
    return json.dumps(obj, indent=2) + "\n"


def render_plugin_json(plugin):
    obj = {"name": plugin["name"],
           "version": plugin["version"],
           "description": plugin["description"],
           "author": {"name": plugin["author-name"], "email": plugin["author-email"]},
           "homepage": plugin["homepage"]}
    return json.dumps(obj, indent=2) + "\n"


def build_desired(root, src_dir):
    """The desired generated surface for an install rooted at root: {rel: content}. Raises ValueError
    or OSError on any malformed or unreadable input (the caller maps both to exit 2). Parameterized
    on root so conformance.py can reuse it under --root (the C5 check) without calling main()."""
    manifest_path = src_dir / "manifest.toml"
    script_src = src_dir / "scripts" / SCRIPT_NAME
    if not _exists(manifest_path):
        raise ValueError("{} exists but holds no manifest.toml".format(src_dir))
    plugin, hooks = load_manifest(manifest_path)
    rules_dir = root / ".aiqt" / "core" / "rules"
    if not dir_present(rules_dir):
        raise ValueError("cannot validate manifest rules ids: no {} to load".format(rules_dir))
    corpus_ids = {str(fm["corpus-id"]) for _src, fm, _rel in load_corpus(rules_dir)}
    script_text = script_src.read_text(encoding="utf-8")  # FileNotFoundError -> OSError, fail-closed
    if not script_text.strip():
        raise ValueError("{} is empty".format(script_src))
    cross_check(hooks, corpus_ids, script_text, manifest_path.name)
    return {HOOKS_JSON_REL: render_hooks_json(hooks),
            SCRIPT_REL: script_text,
            PLUGIN_JSON_REL: render_plugin_json(plugin)}


def run(root, check):
    """Reconcile the generated plugin surface under root; the gen_cursor.py reconcile idiom."""
    src_dir = root / ".aiqt" / "core" / "hooks"
    desired = {}
    try:
        # dir_present (not is_dir) inside the try: an unreadable .aiqt/ parent must fail closed as
        # exit 2, not read as an absent manifest (which would prune the whole surface as orphans).
        if dir_present(src_dir):
            desired = build_desired(root, src_dir)
    except (ValueError, OSError) as exc:
        print("error: {}".format(exc))
        return 2
    # Reconcile even when src_dir is absent (desired empty) so orphaned generated files are never
    # concealed. Fail-closed throughout: an unreadable target ( _exists / read_text) or an unreadable
    # plugin hooks/ dir at any depth (os.walk(onerror=raise), never rglob, which silently skips an
    # unlistable subdir) becomes a clean exit 2, not a traceback or a concealed orphan.
    drift = []

    def _raise(exc):
        raise exc
    try:
        for rel, content in sorted(desired.items()):
            target = root / rel
            current = target.read_text(encoding="utf-8") if _exists(target) else None
            if current != content:
                drift.append(rel)
                if not check:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(content, encoding="utf-8")
        # Orphan scan, scoped to the RESERVED plugin hooks/ subtree (100% generated) plus the one
        # managed .claude-plugin/plugin.json; nothing else is ever walked, written, or deleted.
        hooks_dir = root.joinpath(*HOOKS_SUBTREE_PARTS)
        if dir_present(hooks_dir):
            for dirpath, _dirs, filenames in os.walk(hooks_dir, onerror=_raise):
                for fn in sorted(filenames):
                    f = Path(dirpath) / fn
                    # as_posix(), not str(): desired keys are forward-slash rel paths, so a backslash
                    # from str() on Windows would flag every generated file as an orphan.
                    rel = f.relative_to(root).as_posix()
                    if rel not in desired:
                        drift.append("orphan " + rel)
                        if not check:
                            f.unlink()
        plugin_json = root / PLUGIN_JSON_REL
        if PLUGIN_JSON_REL not in desired and _exists(plugin_json):
            drift.append("orphan " + PLUGIN_JSON_REL)
            if not check:
                plugin_json.unlink()
    except (OSError, ValueError) as exc:
        # ValueError catches UnicodeDecodeError from read_text on a non-utf-8 target during the drift
        # comparison; fail closed (exit 2), never a traceback.
        print("error: {}".format(exc))
        return 2
    if check and drift:
        print("drift: " + "; ".join(drift))
        print("run tools/gen_hooks.py to regenerate")
        return 1
    return 0


def main():
    args = sys.argv[1:]
    if "--self-test" in args:
        return self_test_main()
    return run(repo_root(), "--check" in args)


# --- self-test ----------------------------------------------------------------------------------------
# Proves the generator's own fail-closed invariants against synthetic trees (the conformance.py
# pattern), so gen_hooks never becomes an ungated generator:
#   1. a conformant manifest generates, and regeneration is drift-clean,
#  1b. every rendered launcher runs isolated: args[0] == "-I", before the script path,
#   2. drift in hooks.json (and an orphan under the plugin hooks/) is caught (exit 1),
#   3. an unknown corpus-id in the manifest fails closed (exit 2),
#   4. an empty residue fails closed (exit 2),
#   5. an unreadable .aiqt/core/hooks/ fails closed (exit 2), never an all-absent clean,
#   6. a bad/unknown event fails closed (exit 2),
#   7. an uncompilable matcher regex fails closed (exit 2),
#   8. a named handler not defined in the source script fails closed (exit 2),
#   9. a missing handler script fails closed (exit 2),
#  10. an unreadable manifest fails closed (exit 2),
#  11. the FULL "warn" default policy, table-driven across every KNOWN_EVENTS event: warn is REJECTED
#      (exit 2) on every non-warn event and ACCEPTED (clean generate + drift-clean) on Stop and
#      SubagentStop, the only WARN_EVENTS,
#  12. render byte-identity: an otherwise-identical Stop hook renders the SAME hooks.json bytes whether
#      its default is "block" or "warn" (default is authoring metadata, never rendered into output),
#  13. a malformed/unknown default keyword fails closed (exit 2): the default whitelist.

_APEX = """---
corpus-id: apex01
origin: pack
family: aiqt
apex: true
slug: project-integrity
---
# Project integrity

The apex rule for the self-test corpus.
"""

_RULE = """---
corpus-id: tsthk1
origin: pack
family: aiqt
tier: 10
facet: TRUST
slug: selftest-hook-rule
---
# A self-test hook rule

A rule for the gen_hooks self-test corpus.
"""

_SCRIPT = """#!/usr/bin/env python3
# self-test dispatcher stub
def test_handler(data):
    return (0, None, None)


HANDLERS = {
    "test_handler": test_handler,
}

HANDLER_EVENT = {
    "test_handler": "PreToolUse",
}
"""

_MANIFEST = """[plugin]
name = "aiqt-selftest-hooks"
version = "0.1.0"
description = "Self-test plugin."
author-name = "Self Test"
author-email = "selftest@example.invalid"
homepage = "https://example.invalid"

[[hook]]
id = "selftest-control"
rules = ["{cid}"]
platform = "claude-code"
event = "PreToolUse"
matcher = "Bash"
handler = "test_handler"
default = "block"
class = "b"
residue = "{residue}"
"""


def _build_tree(base, cid="tsthk1", residue="A self-test control catches nothing real."):
    src = base / ".aiqt" / "core"
    (src / "rules" / "aiqt").mkdir(parents=True)
    (src / "rules" / "aiqt" / "00-project-integrity.md").write_text(_APEX, encoding="utf-8")
    (src / "rules" / "aiqt" / "selftest-hook-rule.md").write_text(_RULE, encoding="utf-8")
    (src / "hooks" / "scripts").mkdir(parents=True)
    (src / "hooks" / "scripts" / SCRIPT_NAME).write_text(_SCRIPT, encoding="utf-8")
    (src / "hooks" / "manifest.toml").write_text(
        _MANIFEST.format(cid=cid, residue=residue), encoding="utf-8")


def _set_script_event(tree, event):
    """Round 9 (A2): keep a fixture script's HANDLER_EVENT value in step with a retargeted manifest
    event. The pre-round-9 event-retargeting tests mutated only the manifest, leaving the script's
    HANDLER_EVENT at PreToolUse; the round-9 value-equality dispatchability gate correctly rejects that
    mismatch, so an event-retargeting fixture must set both surfaces to stay conformant (and so a masked
    exit-2 does not stand in for the behaviour the test means to prove)."""
    sp = tree / ".aiqt" / "core" / "hooks" / "scripts" / SCRIPT_NAME
    sp.write_text(sp.read_text(encoding="utf-8").replace(
        '"test_handler": "PreToolUse",', '"test_handler": "{}",'.format(event)), encoding="utf-8")


def self_test_main():
    import io
    import shutil
    import tempfile
    from contextlib import redirect_stdout

    def run_quiet(root, check):
        with redirect_stdout(io.StringIO()):
            return run(root, check)

    try:
        tmp = Path(tempfile.mkdtemp(prefix="aiqt-gen-hooks-selftest-"))
    except OSError as exc:
        print("SELF-TEST ERROR: no writable temporary directory: {}".format(exc), file=sys.stderr)
        return 2
    failures = []
    skipped = []

    def _mutate(root, old, new):
        """Rewrite the built manifest in place to inject one fault, then the run must fail closed."""
        mpath = root / ".aiqt" / "core" / "hooks" / "manifest.toml"
        text = mpath.read_text(encoding="utf-8")
        if old not in text:
            failures.append("self-test setup: {!r} not found in manifest".format(old))
        mpath.write_text(text.replace(old, new), encoding="utf-8")

    try:
        # 1. Conformant manifest: generate, then --check is drift-clean.
        good = tmp / "good"
        good.mkdir()
        _build_tree(good)
        if run_quiet(good, check=False) != 0:
            failures.append("conformant tree: generation expected exit 0")
        if run_quiet(good, check=True) != 0:
            failures.append("conformant tree: regeneration expected drift-clean exit 0")
        for rel in (HOOKS_JSON_REL, SCRIPT_REL, PLUGIN_JSON_REL):
            if not (good / rel).is_file():
                failures.append("conformant tree: expected generated file {}".format(rel))

        # 1b. Every rendered launcher runs isolated: args[0] is "-I", before the script path, so a
        #     sibling file cannot shadow a stdlib import and neuter the hook. A regression in the
        #     generator (dropping the flag or misplacing it) fails the generator's own self-test.
        try:
            rendered = json.loads((good / HOOKS_JSON_REL).read_text(encoding="utf-8"))
            entries = [h for event in rendered["hooks"].values() for e in event for h in e["hooks"]]
            if not entries:
                failures.append("isolation invariant: no rendered hook entries to check")
            for h in entries:
                if h.get("args", [None])[0] != "-I":
                    failures.append("isolation invariant: launcher args[0] must be '-I', got {!r}"
                                    .format(h.get("args")))
        except (OSError, ValueError, KeyError, IndexError) as exc:
            failures.append("isolation invariant: could not read rendered hooks.json: {}".format(exc))

        # 2. Drift in hooks.json, and an orphan under the plugin hooks/, are caught.
        target = good / HOOKS_JSON_REL
        target.write_text(target.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        if run_quiet(good, check=True) != 1:
            failures.append("drifted hooks.json expected exit 1")
        if run_quiet(good, check=False) != 0 or run_quiet(good, check=True) != 0:
            failures.append("regeneration after drift expected a clean reconcile")
        stray = good.joinpath(*HOOKS_SUBTREE_PARTS) / "stray.txt"
        stray.write_text("stray\n", encoding="utf-8")
        if run_quiet(good, check=True) != 1:
            failures.append("orphan under the plugin hooks/ expected exit 1")
        stray.unlink()

        # N. Dispatchability gate: a handler DEFINED but not registered in the script's HANDLERS fails
        #    closed (round 8, F-339). The conformant fixture wires test_handler; unwiring it must exit 2.
        undispatch = tmp / "undispatch"
        undispatch.mkdir()
        _build_tree(undispatch)
        _spath = undispatch / ".aiqt" / "core" / "hooks" / "scripts" / SCRIPT_NAME
        _stext = _spath.read_text(encoding="utf-8")
        if '"test_handler": test_handler,' not in _stext:
            failures.append("self-test setup: fixture HANDLERS entry not found to unwire")
        _spath.write_text(_stext.replace('"test_handler": test_handler,', ""), encoding="utf-8")
        if run_quiet(undispatch, check=False) != 2:
            failures.append("a handler defined but absent from HANDLERS must fail closed (exit 2)")

        # N2 (round 9, A2): a COMMENTED-OUT HANDLERS entry must NOT read as a live handler. A regex over
        #     raw source counted '# "x": x,' as a key (the control then never dispatches though --check is
        #     green); stripping comments before key extraction fails it closed (exit 2).
        commented = tmp / "commented"
        commented.mkdir()
        _build_tree(commented)
        _cpath = commented / ".aiqt" / "core" / "hooks" / "scripts" / SCRIPT_NAME
        _ctext = _cpath.read_text(encoding="utf-8")
        _cpath.write_text(_ctext.replace('"test_handler": test_handler,',
                                         '# "test_handler": test_handler,'), encoding="utf-8")
        if run_quiet(commented, check=False) != 2:
            failures.append("a commented-out HANDLERS entry must fail closed (exit 2)")

        # N3 (round 9, A2): a HANDLER_EVENT VALUE disagreeing with the manifest event must fail closed.
        #     Key-membership alone let a PreToolUse hook register under PostToolUse (a mis-triggered
        #     control); comparing the registered event to the manifest event catches it.
        wrongevent = tmp / "wrongevent"
        wrongevent.mkdir()
        _build_tree(wrongevent)
        _wpath = wrongevent / ".aiqt" / "core" / "hooks" / "scripts" / SCRIPT_NAME
        _wtext = _wpath.read_text(encoding="utf-8")
        _wpath.write_text(_wtext.replace('"test_handler": "PreToolUse",',
                                         '"test_handler": "PostToolUse",'), encoding="utf-8")
        if run_quiet(wrongevent, check=False) != 2:
            failures.append("a HANDLER_EVENT value disagreeing with the manifest event must fail closed "
                            "(exit 2)")

        # N4 (round 10): a dict VALUE that is a STRING LITERAL resembling a '"name": name,' entry must NOT
        #     contribute a PHANTOM key. The round-9 regex _dispatch_keys scanned raw text and extracted
        #     "test_handler" from inside the string value below (fail-OPEN: cross_check then accepted a
        #     manifest handler the runtime HANDLERS lacks). ast.parse reads dict KEY NODES structurally and
        #     does NOT execute the script, so a string-literal value contributes no key.
        _decoy = 'HANDLERS = {\n    "decoy": ' + repr('"test_handler": test_handler,') + ',\n}\n'
        if "test_handler" in _dispatch_keys(_decoy, "HANDLERS"):
            failures.append("a string-literal dict VALUE must not contribute a phantom HANDLERS key")
        if _dispatch_keys(_decoy, "HANDLERS") != {"decoy"}:
            failures.append("ast _dispatch_keys must extract only the real string-literal key 'decoy'")

        # N4b (round 10): the SAME decoy in a BUILT tree fails closed end-to-end. The fixture's real
        #     HANDLERS entry is replaced by the decoy, so test_handler is genuinely undispatchable and
        #     cross_check must exit 2 (the regex read the phantom key and let it pass, exit 0).
        phantom = tmp / "phantom"
        phantom.mkdir()
        _build_tree(phantom)
        _ppath = phantom / ".aiqt" / "core" / "hooks" / "scripts" / SCRIPT_NAME
        _ptext = _ppath.read_text(encoding="utf-8")
        _ppath.write_text(_ptext.replace(
            '"test_handler": test_handler,',
            '"decoy": ' + repr('"test_handler": test_handler,') + ','), encoding="utf-8")
        if run_quiet(phantom, check=False) != 2:
            failures.append("a string-literal decoy embedding a phantom HANDLERS key must fail closed "
                            "(exit 2)")

        # N5 (round 11, FIX 2): a LATER non-dict rebinding of the dispatcher name fails closed. The runtime
        #     dict is the rebinding's value (empty/undispatchable), but the r10 _module_dict filtered for an
        #     ast.Dict value BEFORE counting bindings, so it found the sole literal and read its keys as
        #     dispatchable (fail-OPEN). Counting EVERY binding first fails closed.
        _rebind = ('HANDLERS = {\n    "test_handler": test_handler,\n}\n'
                   'HANDLERS = make_handlers()\n')
        if _dispatch_keys(_rebind, "HANDLERS") != set():
            failures.append("a later non-dict rebinding of HANDLERS must fail closed (empty key set)")
        _rebind_ev = ('HANDLER_EVENT = {\n    "test_handler": "PreToolUse",\n}\n'
                      'HANDLER_EVENT = build_events()\n')
        if _dispatch_event_map(_rebind_ev) != {}:
            failures.append("a later non-dict rebinding of HANDLER_EVENT must fail closed (empty map)")

        # N6 (round 11, FIX 2): a subscript or augmented MUTATION alongside the literal is a second binding of
        #     the name, so the dispatcher is no longer a single immutable literal and fails closed.
        _subscript = ('HANDLERS = {\n    "test_handler": test_handler,\n}\n'
                      'HANDLERS["late"] = late_handler\n')
        if _dispatch_keys(_subscript, "HANDLERS") != set():
            failures.append("a subscript mutation of HANDLERS must fail closed (empty key set)")
        _augmented = ('HANDLERS = {\n    "test_handler": test_handler,\n}\n'
                      'HANDLERS += extra\n')
        if _dispatch_keys(_augmented, "HANDLERS") != set():
            failures.append("an augmented rebinding of HANDLERS must fail closed (empty key set)")

        # N7 (round 11, FIX 2): a TUPLE-UNPACK binding (HANDLERS, HANDLER_EVENT = ..) is intentionally
        #     unsupported (a dispatcher is bound once as its own dict literal). r10 ignored a non-Name target,
        #     so the accompanying literal still read as dispatchable (fail-OPEN); counting every binding of
        #     the name fails it closed.
        _unpack = ('HANDLERS = {\n    "test_handler": test_handler,\n}\n'
                   'HANDLERS, HANDLER_EVENT = _pair()\n')
        if _dispatch_keys(_unpack, "HANDLERS") != set():
            failures.append("a tuple-unpack rebinding of HANDLERS (alongside its literal) must fail closed")

        # N8 (round 12, whole-AST single binding): EVERY other binding or mutation of the dispatcher name,
        #     at any depth, fails the map closed, not only a top-level statement. Each form below slipped
        #     the round-11 top-level scan (a nested rebinding, a walrus, an import binder, a for/with/
        #     except/match/comprehension target, a global rebinding in a function, a def, a parameter, a
        #     mutating method call, an alias that is mutated later, a chained assignment, an argument to an
        #     arbitrary call). The literal line is shared; each case appends its own binding.
        _lit = 'HANDLERS = {\n    "test_handler": test_handler,\n}\n'
        for _label, _tail in (
                ("nested-if", "if True:\n    HANDLERS = {'x': x}\n"),
                ("nested-try", "try:\n    HANDLERS = {}\nexcept E:\n    pass\n"),
                ("walrus", "(HANDLERS := {})\n"),
                ("from-import", "from elsewhere import HANDLERS\n"),
                ("dotted-import", "import HANDLERS.child\n"),
                ("wildcard-import", "from elsewhere import *\n"),
                ("for-target", "for HANDLERS in rows:\n    pass\n"),
                ("with-target", "with ctx() as HANDLERS:\n    pass\n"),
                ("except-as", "try:\n    pass\nexcept E as HANDLERS:\n    pass\n"),
                ("match-capture", "match v:\n    case HANDLERS:\n        pass\n"),
                ("match-rest", "match v:\n    case {**HANDLERS}:\n        pass\n"),
                ("comprehension", "[1 for HANDLERS in rows]\n"),
                ("global-rebind", "def f():\n    global HANDLERS\n    HANDLERS = {}\n"),
                ("def", "def HANDLERS():\n    pass\n"),
                ("parameter", "def f(HANDLERS):\n    pass\n"),
                ("del", "del HANDLERS\n"),
                ("update-method", "HANDLERS.update(late=late)\n"),
                ("clear-in-function", "def f():\n    HANDLERS.clear()\n"),
                ("alias-mutation", "alias = HANDLERS\nalias['late'] = late\n"),
                ("call-argument", "register(HANDLERS)\n"),
                ("attribute-store", "HANDLERS.extra = 1\n")):
            if _dispatch_keys(_lit + _tail, "HANDLERS") != set():
                failures.append("a {} binding or mutation of HANDLERS must fail closed (empty key set)"
                                .format(_label))
        if _dispatch_keys("X = " + _lit, "HANDLERS") != set():
            failures.append("a chained X = HANDLERS = {...} (an alias target) must fail closed")
        # the dispatcher's own pure reads stay accepted (no over-fire on the real usage shape)
        _reads = (_lit + "def main(mode):\n    if mode not in HANDLERS:\n        print(sorted(HANDLERS))\n"
                  "    return HANDLERS[mode](None), HANDLERS.get(mode)\n")
        if _dispatch_keys(_reads, "HANDLERS") != {"test_handler"}:
            failures.append("the dispatcher's pure reads (in, sorted, subscript, get) must stay accepted")
        # N8b: the same mutation in a BUILT tree fails the generator closed end-to-end (exit 2).
        mutated = tmp / "mutated"
        mutated.mkdir()
        _build_tree(mutated)
        _mpath = mutated / ".aiqt" / "core" / "hooks" / "scripts" / SCRIPT_NAME
        _mpath.write_text(_mpath.read_text(encoding="utf-8") + "\nHANDLERS.clear()\n", encoding="utf-8")
        if run_quiet(mutated, check=False) != 2:
            failures.append("a HANDLERS.clear() mutation in the built script must fail closed (exit 2)")

        # 3. An unknown corpus-id in the manifest fails closed.
        badid = tmp / "badid"
        badid.mkdir()
        _build_tree(badid, cid="nosuch1")
        if run_quiet(badid, check=True) != 2:
            failures.append("unknown corpus-id expected exit 2 (fail-closed)")

        # 4. An empty residue fails closed.
        badres = tmp / "badres"
        badres.mkdir()
        _build_tree(badres, residue="")
        if run_quiet(badres, check=True) != 2:
            failures.append("empty residue expected exit 2 (fail-closed)")

        # 5. An unreadable .aiqt/core/hooks/ fails closed. Skipped where the runner can still read a
        #    chmod-0 dir (root/DAC-bypass), observed via os.access, as in conformance.py.
        unread = tmp / "unread"
        unread.mkdir()
        _build_tree(unread)
        hooks_src = unread / ".aiqt" / "core" / "hooks"
        os.chmod(hooks_src, 0)
        if os.access(hooks_src, os.R_OK):
            skipped.append("5 unreadable-hooks-src")
        else:
            if run_quiet(unread, check=True) != 2:
                failures.append("unreadable .aiqt/core/hooks/ expected exit 2 (fail-closed)")
        os.chmod(hooks_src, 0o755)  # restore so cleanup can remove it

        # 6. A bad/unknown hook event fails closed (the event whitelist).
        badevent = tmp / "badevent"
        badevent.mkdir()
        _build_tree(badevent)
        _mutate(badevent, 'event = "PreToolUse"', 'event = "Nonesuch"')
        if run_quiet(badevent, check=True) != 2:
            failures.append("unknown event expected exit 2 (fail-closed)")

        # 7. An uncompilable matcher regex fails closed.
        badmatcher = tmp / "badmatcher"
        badmatcher.mkdir()
        _build_tree(badmatcher)
        _mutate(badmatcher, 'matcher = "Bash"', 'matcher = "([unterminated"')
        if run_quiet(badmatcher, check=True) != 2:
            failures.append("uncompilable matcher expected exit 2 (fail-closed)")

        # 8. A named handler not defined in the source script fails closed.
        badhandler = tmp / "badhandler"
        badhandler.mkdir()
        _build_tree(badhandler)
        _mutate(badhandler, 'handler = "test_handler"', 'handler = "missing_handler"')
        if run_quiet(badhandler, check=True) != 2:
            failures.append("missing handler expected exit 2 (fail-closed)")

        # 9. A missing handler SCRIPT (the dispatcher file absent) fails closed.
        noscript = tmp / "noscript"
        noscript.mkdir()
        _build_tree(noscript)
        (noscript / ".aiqt" / "core" / "hooks" / "scripts" / SCRIPT_NAME).unlink()
        if run_quiet(noscript, check=True) != 2:
            failures.append("missing handler script expected exit 2 (fail-closed)")

        # 10. An unreadable manifest fails closed. Skipped where the runner can still read the chmod-0
        #     file (root/DAC-bypass), observed via os.access, as in case 5.
        unreadman = tmp / "unreadman"
        unreadman.mkdir()
        _build_tree(unreadman)
        manifest = unreadman / ".aiqt" / "core" / "hooks" / "manifest.toml"
        os.chmod(manifest, 0)
        if os.access(manifest, os.R_OK):
            skipped.append("10 unreadable-manifest")
        elif run_quiet(unreadman, check=True) != 2:
            failures.append("unreadable manifest expected exit 2 (fail-closed)")
        os.chmod(manifest, 0o644)  # restore so cleanup can remove it

        # 11. The FULL "warn" default policy, table-driven across every known event. warn is a
        #     non-blocking default legal on the WARN_EVENTS (PostToolUse, UserPromptSubmit, Stop,
        #     SubagentStop, SessionStart), so the generator must REJECT it (exit 2) on every other event
        #     outside a bake stage and ACCEPT it on each WARN_EVENTS event, regenerating drift-clean.
        #     Converting the base tool hook to a non-tool event also drops the now-forbidden matcher,
        #     matching a real Stop/UserPromptSubmit control.
        tool_block = 'event = "PreToolUse"\nmatcher = "Bash"\nhandler = "test_handler"\ndefault = "block"'
        for event in KNOWN_EVENTS:
            warntree = tmp / ("warn-" + event.lower())
            warntree.mkdir()
            _build_tree(warntree)
            if event in TOOL_EVENTS:
                # a tool event keeps its matcher; retarget the event and flip the default to warn
                _mutate(warntree, 'event = "PreToolUse"', 'event = "{}"'.format(event))
                _mutate(warntree, 'default = "block"', 'default = "warn"')
            else:
                # a non-tool event must drop the now-forbidden matcher as it flips to warn
                _mutate(warntree, tool_block,
                        'event = "{}"\nhandler = "test_handler"\ndefault = "warn"'.format(event))
            _set_script_event(warntree, event)
            if event in WARN_EVENTS:
                if run_quiet(warntree, check=False) != 0 or run_quiet(warntree, check=True) != 0:
                    failures.append("'warn' default on WARN event {} expected a clean generate + "
                                    "drift-clean check".format(event))
            elif run_quiet(warntree, check=True) != 2:
                failures.append("'warn' default on non-warn event {} expected exit 2 (fail-closed)"
                                .format(event))

        # 12. Render byte-identity: the default is authoring metadata, never rendered into hooks.json, so
        #     an otherwise-identical Stop hook must render the SAME bytes whether its default is "block"
        #     or "warn". Proven by generating both and comparing the emitted hooks.json.
        stop_block = tmp / "stop-block"
        stop_block.mkdir()
        _build_tree(stop_block)
        _mutate(stop_block, tool_block,
                'event = "Stop"\nhandler = "test_handler"\ndefault = "block"')
        _set_script_event(stop_block, "Stop")
        stop_warn = tmp / "stop-warn"
        stop_warn.mkdir()
        _build_tree(stop_warn)
        _mutate(stop_warn, tool_block,
                'event = "Stop"\nhandler = "test_handler"\ndefault = "warn"')
        _set_script_event(stop_warn, "Stop")
        if run_quiet(stop_block, check=False) != 0 or run_quiet(stop_warn, check=False) != 0:
            failures.append("byte-identity setup: both Stop trees expected a clean generate (exit 0)")
        else:
            block_json = (stop_block / HOOKS_JSON_REL).read_text(encoding="utf-8")
            warn_json = (stop_warn / HOOKS_JSON_REL).read_text(encoding="utf-8")
            if block_json != warn_json:
                failures.append("Stop hooks.json must be byte-identical for default 'block' vs 'warn' "
                                "(default is authoring metadata, not rendered output)")

        # 13. A malformed/unknown default keyword fails closed (the default whitelist), so a typo can
        #     never silently select an unintended enforcement posture. Under the no-ask posture "ask" is
        #     no longer a permitted default and is rejected exactly like any other unknown keyword.
        #     Asserting both that "ask" is absent from DEFAULTS and that a manifest default = "ask"
        #     fails closed makes a revert of the no-ask removal fail this self-test (discrimination).
        if "ask" in DEFAULTS:
            failures.append("no-ask posture: 'ask' must not be a permitted default (DEFAULTS whitelist)")
        baddefault = tmp / "baddefault"
        baddefault.mkdir()
        _build_tree(baddefault)
        _mutate(baddefault, 'default = "block"', 'default = "nonesuch"')
        if run_quiet(baddefault, check=True) != 2:
            failures.append("unknown default keyword expected exit 2 (fail-closed)")
        askdefault = tmp / "askdefault"
        askdefault.mkdir()
        _build_tree(askdefault)
        _mutate(askdefault, 'default = "block"', 'default = "ask"')
        if run_quiet(askdefault, check=True) != 2:
            failures.append("no-ask posture: manifest default = \"ask\" expected exit 2 (fail-closed)")

        # 14. 'warn' on PreToolUse is legal ONLY under the explicit recorded stage = "bake" posture.
        bake = tmp / "bake"
        bake.mkdir()
        _build_tree(bake)
        _mutate(bake, 'default = "block"', 'default = "warn"\nstage = "bake"')
        if run_quiet(bake, check=False) != 0 or run_quiet(bake, check=True) != 0:
            failures.append("'warn' + stage=bake on PreToolUse expected a clean generate")
        nostage = tmp / "nostage"
        nostage.mkdir()
        _build_tree(nostage)
        _mutate(nostage, 'default = "block"', 'default = "warn"')
        if run_quiet(nostage, check=True) != 2:
            failures.append("'warn' on PreToolUse without stage=bake expected exit 2 (fail-closed)")

        # 15. A SessionStart control cannot claim block/ask: the platform cannot block that event.
        ssblock = tmp / "ssblock"
        ssblock.mkdir()
        _build_tree(ssblock)
        _mutate(ssblock, tool_block,
                'event = "SessionStart"\nhandler = "test_handler"\ndefault = "block"')
        _set_script_event(ssblock, "SessionStart")
        if run_quiet(ssblock, check=True) != 2:
            failures.append("default 'block' on SessionStart expected exit 2 (an overclaim)")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if failures:
        print("SELF-TEST FAIL:")
        for failure in failures:
            print("  - " + failure)
        return 1
    note = ("" if not skipped else
            " NOTE: skipped {} case(s) the runner cannot exercise (chmod-0 still readable): {}"
            .format(len(skipped), ", ".join(skipped)))
    print("SELF-TEST PASS: a conformant manifest generates and regenerates drift-clean; every rendered "
          "launcher runs isolated (args[0] == '-I'); hooks.json "
          "drift and a plugin hooks/ orphan fail the check; an unknown corpus-id, an empty residue, "
          "an unreadable source tree, a bad/unknown event, an uncompilable matcher, a handler not in "
          "the script, a missing handler script, an unreadable manifest, and a malformed default keyword "
          "all fail closed; the full 'warn' default policy holds across every known event (rejected on "
          "every non-warn event, accepted on every WARN_EVENTS event, plus warn+stage=bake on "
          "PreToolUse and the SessionStart-cannot-block overclaim); and a Stop hook renders "
          "byte-identical hooks.json whether its default is 'block' or 'warn'" + note)
    return 0


if __name__ == "__main__":
    sys.exit(main())
