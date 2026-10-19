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

Per-hook timeout and derived onFailure, OFF by default. A [[hook]] may author an optional integer
`timeout` (TIMEOUT_MIN to TIMEOUT_MAX seconds), and the [plugin] table may author
`claude-code-min-version` plus the render switch `on-failure-render`. While the switch is false or
absent (this repository's current setting), NEITHER an authored per-hook timeout NOR any onFailure key
is rendered, so the emitted hooks.json stays byte-identical to the pre-switch output. The switch ships
off because how a client older than 2.1.295 treats the unknown onFailure key in hooks.json is
unverified (it could ignore the key, warn, reject that entry, or reject the whole file, and the last
would silently disable every hook, a fail-open regression). With the switch on, exactly the
enforce-stage PreToolUse hooks whose default is "block" render "onFailure": "block" plus their
configured timeout; a warn hook never carries it, and no Stop, TeammateIdle or other non-PreToolUse
hook ever carries it.
  gen_hooks.py             regenerate the plugin surface
  gen_hooks.py --check     fail (exit 1) on drift; exit 2 on a malformed source or a read/write failure
  gen_hooks.py --self-test build synthetic trees and assert the generator's own fail-closed invariants
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: gen_hooks.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import ast
import json
import os
import re
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
             "stage", "timeout"}
REQUIRED_HOOK_KEYS = HOOK_KEYS - {"matcher", "stage", "timeout"}
PLUGIN_KEYS = ("name", "version", "description", "author-name", "author-email", "homepage")
# Optional [plugin] keys. claude-code-min-version records the oldest client version whose documented
# onFailure semantics the authored fields rely on; on-failure-render gates whether the per-hook timeout
# and the derived onFailure block are rendered at all (false or absent: the legacy byte-identical
# output, this repository's current posture; see the module docstring for why it ships off).
PLUGIN_OPTIONAL_KEYS = ("claude-code-min-version", "on-failure-render")
PLATFORMS = {"claude-code"}
DEFAULTS = {"block", "warn"}  # no-ask posture: "ask" is not a permitted default
CLASSES = {"a", "b", "c", "d"}
SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")
ID_RE = re.compile(r"^[a-z][a-z0-9-]*$")     # a stable kebab-case control id
CID_RE = re.compile(r"^[a-z0-9]{6,}$")        # a corpus-id (matches gen_rules' own shape)
HANDLER_RE = re.compile(r"^[a-z][a-z0-9_]*$")
TIMEOUT = 10  # default seconds the platform gives each hook command; while on-failure-render is off,
# the one source of every hook's deadline (an authored per-hook timeout renders only with the switch on)
TIMEOUT_MIN, TIMEOUT_MAX = 1, 600  # the accepted range of an authored per-hook timeout, in seconds
# onFailure is DERIVED, never authored: with the on-failure-render switch on, "onFailure": "block" is
# rendered exactly when the event is in ONFAILURE_EVENTS, the default is "block" and the stage is
# "enforce"; never for a warn hook, and never on Stop or TeammateIdle, where the documented behaviour
# does not apply. The platform hooks documentation (the source of the 2.1.295 floor): "an onFailure:
# block setting (client 2.1.295 and later) turns a timeout, crash, bad exit or invalid JSON into a
# block"; without it, a timed-out PreToolUse command hook does not block the tool call.
ONFAILURE_EVENTS = ("PreToolUse",)
# The dispatcher mirrors TIMEOUT as HOOK_TIMEOUT_SECONDS and derives its PreToolUse deadline from that
# mirror as PRETOOL_BUDGET_SECONDS = HOOK_TIMEOUT_SECONDS - <margin>, so main() fails closed (exit 2) with
# a named reason before the platform's timeout. check_deadline refuses a mirror that differs from TIMEOUT,
# a margin under DEADLINE_MIN_MARGIN or one that leaves no positive budget, and any other binding of
# either name in the script.
DEADLINE_MIN_MARGIN = 1
_HOOK_TIMEOUT_LINE_RE = re.compile(r"^HOOK_TIMEOUT_SECONDS = (\d+)$", re.M)
_PRETOOL_BUDGET_LINE_RE = re.compile(
    r"^PRETOOL_BUDGET_SECONDS = HOOK_TIMEOUT_SECONDS - (\d+(?:\.\d+)?)$", re.M)

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
    p_extra = set(plugin) - set(PLUGIN_KEYS) - set(PLUGIN_OPTIONAL_KEYS)
    if p_extra:
        raise ValueError("{}: unknown [plugin] key(s): {}".format(path.name, ", ".join(sorted(p_extra))))
    for key in PLUGIN_KEYS:
        _req_str(plugin, key, "{}: [plugin]".format(path.name))
    if not SEMVER_RE.match(plugin["version"]):
        raise ValueError("{}: [plugin] version must be SemVer (MAJOR.MINOR.PATCH)".format(path.name))
    if "claude-code-min-version" in plugin:
        minver = plugin["claude-code-min-version"]
        if not isinstance(minver, str) or not SEMVER_RE.match(minver):
            raise ValueError("{}: [plugin] claude-code-min-version must be a SemVer string "
                             "(MAJOR.MINOR.PATCH)".format(path.name))
    if "on-failure-render" in plugin and not isinstance(plugin["on-failure-render"], bool):
        raise ValueError("{}: [plugin] on-failure-render must be a TOML boolean".format(path.name))
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
        if "timeout" in hook:
            timeout = hook["timeout"]
            # bool is checked first: a TOML true parses as a Python bool, which is an int subclass and
            # would otherwise pass as 1, a silently wrong one-second deadline.
            if isinstance(timeout, bool) or not isinstance(timeout, int) or \
                    not TIMEOUT_MIN <= timeout <= TIMEOUT_MAX:
                raise ValueError("{}: timeout must be an integer from {} to {} seconds, got {!r}"
                                 .format(where, TIMEOUT_MIN, TIMEOUT_MAX, timeout))
        if _req_str(hook, "class", where) not in CLASSES:
            raise ValueError("{}: class must be the EN-5 letter a/b/c/d".format(where))
        _req_str(hook, "residue", where)  # required, never empty: the control's honest coverage gap
    return plugin, hooks


def cross_check(hooks, corpus_ids, script_text, name):
    """The manifest's outward references, fail-closed: every cited corpus-id exists in the loaded
    corpus (the check_mappings no-orphan pattern), and every named handler is defined in the source
    script, so a rename can never silently orphan an enforcement control."""
    for hook in hooks:
        where = "{}: [[hook]] {}".format(name, hook["id"])
        for rule in hook["rules"]:
            if rule not in corpus_ids:
                raise ValueError("{}: rules entry '{}' is not a corpus-id in .aiqt/core/rules/"
                                 .format(where, rule))
        if not re.search(r"^def {}\(".format(re.escape(hook["handler"])), script_text, re.M):
            raise ValueError("{}: handler '{}' is not defined in scripts/{}"
                             .format(where, hook["handler"], SCRIPT_NAME))


def _name_bindings(tree, name):
    """The line of every binding of `name` the parse shows, wherever it sits: an assignment target of any
    form (plain, augmented, annotated, walrus, for, with, a tuple or starred element), a del, an import, an
    except or match capture, a def or class, a parameter of a function or lambda of any kind (positional,
    positional-only, keyword-only, *args or **kwargs), which shadows the module's name inside it, a type
    parameter (a TypeVar, ParamSpec or TypeVarTuple of a def, class or type alias), and a global or
    nonlocal declaration, which lets a function rebind the module's name. A write the parse cannot see (a
    star import, globals() or setattr) is outside it."""
    lines = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            bound = node.id == name and not isinstance(node.ctx, ast.Load)
        elif isinstance(node, ast.alias):
            bound = (node.asname or node.name.partition(".")[0]) == name
        elif isinstance(node, ast.arg):
            bound = node.arg == name
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            bound = name in node.names
        elif isinstance(node, (ast.ExceptHandler, ast.MatchAs, ast.MatchStar, ast.FunctionDef,
                               ast.AsyncFunctionDef, ast.ClassDef, ast.TypeVar, ast.ParamSpec,
                               ast.TypeVarTuple)):
            bound = node.name == name
        elif isinstance(node, ast.MatchMapping):
            bound = node.rest == name
        else:
            continue
        if bound:
            lines.append(node.lineno)
    return sorted(lines)


def check_deadline(script_text):
    """Fail closed (ValueError) unless the dispatcher derives its PreToolUse deadline from TIMEOUT: exactly
    one `HOOK_TIMEOUT_SECONDS = <int>` line equal to TIMEOUT, and exactly one `PRETOOL_BUDGET_SECONDS =
    HOOK_TIMEOUT_SECONDS - <margin>` line whose margin is at least DEADLINE_MIN_MARGIN and below TIMEOUT,
    so the budget is positive and the refusal is written before the platform's timeout. Each of those lines
    must also be its name's only binding in the parsed script (_name_bindings), so no later assignment of
    another form, and no copy of the line inside a string, stands in for it."""
    timeouts = list(_HOOK_TIMEOUT_LINE_RE.finditer(script_text))
    margins = list(_PRETOOL_BUDGET_LINE_RE.finditer(script_text))
    if len(timeouts) != 1 or int(timeouts[0].group(1)) != TIMEOUT:
        raise ValueError("scripts/{}: expected one line 'HOOK_TIMEOUT_SECONDS = {}' mirroring the hooks.json "
                         "timeout, found {}".format(SCRIPT_NAME, TIMEOUT,
                                                    [m.group(1) for m in timeouts] or "none"))
    if len(margins) != 1:
        raise ValueError("scripts/{}: expected one line 'PRETOOL_BUDGET_SECONDS = HOOK_TIMEOUT_SECONDS - "
                         "<margin>', found {}".format(SCRIPT_NAME, len(margins)))
    margin = float(margins[0].group(1))
    if not DEADLINE_MIN_MARGIN <= margin < TIMEOUT:
        raise ValueError("scripts/{}: the PreToolUse budget margin {:g} must be at least {} and below the "
                         "{}-second timeout".format(SCRIPT_NAME, margin, DEADLINE_MIN_MARGIN, TIMEOUT))
    try:
        tree = ast.parse(script_text)
    except (SyntaxError, ValueError, RecursionError, MemoryError) as exc:
        raise ValueError("scripts/{}: cannot parse the dispatcher to check its deadline bindings ({}: {})"
                         .format(SCRIPT_NAME, type(exc).__name__, exc))
    for name, match in (("HOOK_TIMEOUT_SECONDS", timeouts[0]), ("PRETOOL_BUDGET_SECONDS", margins[0])):
        line = script_text.count("\n", 0, match.start()) + 1
        bindings = _name_bindings(tree, name)
        if bindings != [line]:
            raise ValueError("scripts/{}: {} must be bound only by its line {}, found bindings on lines {}"
                             .format(SCRIPT_NAME, name, line, bindings or "none"))


def render_hooks_json(hooks, on_failure_render=False):
    """The plugin hooks.json, deterministic: events in KNOWN_EVENTS order, entries sorted by id,
    json.dumps(indent=2) plus a trailing newline. Shape per the doc-confirmed plugin schema:
    {description, hooks: {<Event>: [{matcher?, hooks: [{type, command, args, timeout}]}]}}; the command
    is python3 with the script path and handler as args (no shell string, so nothing is shell-quoted);
    matcher is omitted for non-tool events. Every launcher runs isolated: args[0] is "-I" (Python's
    isolated mode), placed before the script path, so a file written beside the dispatcher cannot
    shadow a standard-library import and silently neuter the hook. The generator asserts this invariant
    in its --self-test, and the check_python_launcher_isolation gate re-checks the rendered surface.
    With on_failure_render false (the default, and this repository's current [plugin] setting) the
    output is byte-identical to the pre-switch render: every launcher carries the default TIMEOUT and
    no onFailure key, whatever a hook authors. With it true, each launcher carries its configured
    timeout, and exactly the enforce-stage PreToolUse hooks whose default is "block" also carry
    "onFailure": "block" (never a warn hook, never any other event)."""
    events = {}
    for hook in sorted(hooks, key=lambda h: (KNOWN_EVENTS.index(h["event"]), h["id"])):
        entry = {}
        if "matcher" in hook:
            entry["matcher"] = hook["matcher"]
        launcher = {"type": "command",
                    "command": "python3",
                    "args": ["-I", SCRIPT_PLUGIN_PATH, hook["handler"]],
                    "timeout": TIMEOUT}
        if on_failure_render:
            launcher["timeout"] = hook.get("timeout", TIMEOUT)
            if (hook["event"] in ONFAILURE_EVENTS and hook["default"] == "block"
                    and hook.get("stage", "enforce") == "enforce"):
                launcher["onFailure"] = "block"
        entry["hooks"] = [launcher]
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
    check_deadline(script_text)
    return {HOOKS_JSON_REL: render_hooks_json(hooks, plugin.get("on-failure-render", False)),
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
#  16. the PreToolUse deadline derives from TIMEOUT: a script whose HOOK_TIMEOUT_SECONDS differs from
#      TIMEOUT, that lacks either deadline line, or that binds either name a second time (in any form, or
#      with the budget line only inside a string) fails closed (exit 2), while reading the budget is
#      accepted, and the margin is accepted from DEADLINE_MIN_MARGIN up to just below TIMEOUT and refused
#      just outside either end,
#  17. the optional per-hook `timeout` validates fail-closed: accepted only as an integer from
#      TIMEOUT_MIN to TIMEOUT_MAX (each end tested just inside, exactly at, and just outside), with a
#      bool, a string and a float each refused (exit 2); a non-boolean [plugin] on-failure-render and a
#      non-SemVer claude-code-min-version are refused too,
#  18. with on-failure-render false or absent, an authored per-hook timeout and the optional [plugin]
#      keys change NOTHING: hooks.json renders byte-identical to a tree authoring none of them, with no
#      onFailure key anywhere,
#  19. with on-failure-render true, exactly the enforce-stage PreToolUse block hooks render
#      "onFailure": "block" plus their configured timeout, while a PreToolUse warn (bake) hook, a Stop
#      block hook and a TeammateIdle block hook render neither; a mutant output that drops the
#      onFailure key, and one that resets the configured timeout, each fail the contract
#      (discrimination).

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

_SCRIPT_DEADLINE = "HOOK_TIMEOUT_SECONDS = {timeout}\nPRETOOL_BUDGET_SECONDS = HOOK_TIMEOUT_SECONDS - {margin}\n"
_SCRIPT = """#!/usr/bin/env python3
# self-test dispatcher stub
""" + _SCRIPT_DEADLINE.format(timeout=TIMEOUT, margin="2.0") + """def test_handler(data):
    return (0, None, None)
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
        stop_warn = tmp / "stop-warn"
        stop_warn.mkdir()
        _build_tree(stop_warn)
        _mutate(stop_warn, tool_block,
                'event = "Stop"\nhandler = "test_handler"\ndefault = "warn"')
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
        if run_quiet(ssblock, check=True) != 2:
            failures.append("default 'block' on SessionStart expected exit 2 (an overclaim)")

        # 16. The deadline relation. Each case rewrites the stub's two deadline lines; a refused case
        #     exits 2 (fail closed) and an accepted one generates (exit 0).
        def deadline_case(label, text, want):
            tree = tmp / ("deadline-" + label)
            tree.mkdir()
            _build_tree(tree)
            script = tree / ".aiqt" / "core" / "hooks" / "scripts" / SCRIPT_NAME
            body = script.read_text(encoding="utf-8")
            default_lines = _SCRIPT_DEADLINE.format(timeout=TIMEOUT, margin="2.0")
            if default_lines not in body:
                failures.append("self-test setup: the stub carries no deadline lines")
            script.write_text(body.replace(default_lines, text), encoding="utf-8")
            if run_quiet(tree, check=False) != want:
                failures.append("deadline relation ({}): expected exit {}".format(label, want))
        deadline_case("mirror-differs", _SCRIPT_DEADLINE.format(timeout=TIMEOUT + 1, margin="2.0"), 2)
        deadline_case("no-mirror", "PRETOOL_BUDGET_SECONDS = HOOK_TIMEOUT_SECONDS - 2.0\n", 2)
        deadline_case("no-budget", "HOOK_TIMEOUT_SECONDS = {}\n".format(TIMEOUT), 2)
        deadline_case("literal-budget", "HOOK_TIMEOUT_SECONDS = {}\nPRETOOL_BUDGET_SECONDS = 8.0\n"
                      .format(TIMEOUT), 2)
        # A second binding of either name, in any form, fails closed even beside the one accepted line.
        accepted = _SCRIPT_DEADLINE.format(timeout=TIMEOUT, margin="2.0")
        for label, extra in (("budget-rebound", "PRETOOL_BUDGET_SECONDS = 30\n"),
                             ("budget-augmented", "PRETOOL_BUDGET_SECONDS += 30\n"),
                             ("budget-tuple", "PRETOOL_BUDGET_SECONDS, _other = 30, 0\n"),
                             ("budget-global", "def _rebind():\n    global PRETOOL_BUDGET_SECONDS\n"
                                               "    PRETOOL_BUDGET_SECONDS = 30\n"),
                             ("timeout-augmented", "HOOK_TIMEOUT_SECONDS += 50\n"),
                             ("budget-param", "def _probe(argv, PRETOOL_BUDGET_SECONDS=30):\n    return argv\n"),
                             ("budget-posonly-param", "def _probe(PRETOOL_BUDGET_SECONDS, /):\n    return 0\n"),
                             ("budget-kwonly-param", "def _probe(*, PRETOOL_BUDGET_SECONDS=30):\n    return 0\n"),
                             ("budget-vararg", "def _probe(*PRETOOL_BUDGET_SECONDS):\n    return 0\n"),
                             ("budget-kwarg", "def _probe(**PRETOOL_BUDGET_SECONDS):\n    return 0\n"),
                             ("budget-async-param",
                              "async def _probe(PRETOOL_BUDGET_SECONDS=30):\n    return 0\n"),
                             ("budget-lambda-param", "_PROBE = lambda PRETOOL_BUDGET_SECONDS=30: 0\n"),
                             ("budget-typevar", "def _probe[PRETOOL_BUDGET_SECONDS]():\n    return 0\n"),
                             ("budget-paramspec", "class _Probe[**PRETOOL_BUDGET_SECONDS]:\n    pass\n"),
                             ("budget-typevartuple", "type _Probe[*PRETOOL_BUDGET_SECONDS] = tuple\n"),
                             ("timeout-param", "def _probe(HOOK_TIMEOUT_SECONDS=50):\n    return 0\n")):
            deadline_case(label, accepted + extra, 2)
        # A parameter whose default only reads the budget is no binding of it, so it generates.
        deadline_case("budget-param-default-read",
                      accepted + "def _probe(budget=PRETOOL_BUDGET_SECONDS):\n    return budget\n", 0)
        deadline_case("budget-line-in-string", "HOOK_TIMEOUT_SECONDS = {}\n_TEXT = '''\nPRETOOL_BUDGET_SECONDS = "
                      "HOOK_TIMEOUT_SECONDS - 2.0\n'''\nPRETOOL_BUDGET_SECONDS = 30\n".format(TIMEOUT), 2)
        deadline_case("budget-read-only", accepted + "_BUDGET_COPY = PRETOOL_BUDGET_SECONDS\n", 0)
        for margin, want in ((DEADLINE_MIN_MARGIN - 0.1, 2), (DEADLINE_MIN_MARGIN, 0),
                             (DEADLINE_MIN_MARGIN + 0.1, 0), (TIMEOUT - 0.1, 0), (TIMEOUT, 2),
                             (TIMEOUT + 0.1, 2)):
            text = "{:g}".format(margin)
            deadline_case("margin-" + text, _SCRIPT_DEADLINE.format(timeout=TIMEOUT, margin=text), want)

        # 17. The optional per-hook timeout: an integer from TIMEOUT_MIN to TIMEOUT_MAX seconds. Each end
        #     is tested just inside, exactly at, and just outside it; a bool is refused explicitly (a TOML
        #     true parses as a Python bool, an int subclass that would otherwise pass as 1), and a string
        #     and a float are refused as non-integers.
        def timeout_case(label, toml_value, want):
            tree = tmp / ("timeout-" + label)
            tree.mkdir()
            _build_tree(tree)
            _mutate(tree, 'default = "block"', 'default = "block"\ntimeout = {}'.format(toml_value))
            if run_quiet(tree, check=False) != want:
                failures.append("per-hook timeout ({}): expected exit {}".format(label, want))
        for label, toml_value, want in (("below-min", TIMEOUT_MIN - 1, 2), ("at-min", TIMEOUT_MIN, 0),
                                        ("inside-min", TIMEOUT_MIN + 1, 0),
                                        ("inside-max", TIMEOUT_MAX - 1, 0), ("at-max", TIMEOUT_MAX, 0),
                                        ("above-max", TIMEOUT_MAX + 1, 2),
                                        ("bool", "true", 2), ("string", '"10"', 2), ("float", 1.5, 2)):
            timeout_case(label, toml_value, want)
        # The optional [plugin] keys validate fail-closed too.
        plug_anchor = 'homepage = "https://example.invalid"'
        for label, addition in (("switch-nonbool", 'on-failure-render = "yes"'),
                                ("minver-shape", 'claude-code-min-version = "2.1"')):
            tree = tmp / ("plugkey-" + label)
            tree.mkdir()
            _build_tree(tree)
            _mutate(tree, plug_anchor, plug_anchor + "\n" + addition)
            if run_quiet(tree, check=True) != 2:
                failures.append("[plugin] {}: expected exit 2 (fail-closed)".format(label))

        # 18. OFF is byte-identity: with on-failure-render false (or absent), an authored per-hook
        #     timeout and the optional [plugin] keys change NOTHING in the rendered hooks.json, which
        #     stays byte-identical to a tree authoring none of them, with no onFailure key anywhere.
        ofr_base = tmp / "ofr-base"
        ofr_base.mkdir()
        _build_tree(ofr_base)
        ofr_off = tmp / "ofr-off"
        ofr_off.mkdir()
        _build_tree(ofr_off)
        _mutate(ofr_off, plug_anchor,
                plug_anchor + '\nclaude-code-min-version = "2.1.295"\non-failure-render = false')
        _mutate(ofr_off, 'default = "block"', 'default = "block"\ntimeout = 45')
        if run_quiet(ofr_base, check=False) != 0 or run_quiet(ofr_off, check=False) != 0:
            failures.append("off-switch byte-identity setup: both trees expected a clean generate")
        else:
            base_json = (ofr_base / HOOKS_JSON_REL).read_text(encoding="utf-8")
            off_json = (ofr_off / HOOKS_JSON_REL).read_text(encoding="utf-8")
            if base_json != off_json:
                failures.append("with on-failure-render false, an authored timeout and the optional "
                                "[plugin] keys must render byte-identical hooks.json")
            if "onFailure" in off_json:
                failures.append("with on-failure-render false, no onFailure key may render")

        # 19. ON renders exactly the enforce-stage PreToolUse block hooks with onFailure "block" and
        #     their configured timeout; a PreToolUse warn (bake) hook, a Stop block hook and a
        #     TeammateIdle block hook carry neither. The contract is then shown DISCRIMINATING against
        #     two mutant outputs, one dropping every onFailure key and one resetting every timeout to
        #     the default, each of which it must catch.
        ofr_on = tmp / "ofr-on"
        ofr_on.mkdir()
        _build_tree(ofr_on)
        _mutate(ofr_on, plug_anchor,
                plug_anchor + '\nclaude-code-min-version = "2.1.295"\non-failure-render = true')
        _mutate(ofr_on, 'default = "block"', 'default = "block"\ntimeout = 45')
        on_script = ofr_on / ".aiqt" / "core" / "hooks" / "scripts" / SCRIPT_NAME
        on_script.write_text(on_script.read_text(encoding="utf-8")
                             + "def warn_handler(data):\n    return (0, None, None)\n"
                               "def stop_handler(data):\n    return (0, None, None)\n"
                               "def idle_handler(data):\n    return (0, None, None)\n", encoding="utf-8")
        on_manifest = ofr_on / ".aiqt" / "core" / "hooks" / "manifest.toml"
        on_manifest.write_text(on_manifest.read_text(encoding="utf-8") + (
            '\n[[hook]]\nid = "selftest-warn-bake"\nrules = ["tsthk1"]\nplatform = "claude-code"\n'
            'event = "PreToolUse"\nmatcher = "Bash"\nhandler = "warn_handler"\ndefault = "warn"\n'
            'stage = "bake"\nclass = "b"\nresidue = "A warn bake self-test control."\n'
            '\n[[hook]]\nid = "selftest-stop-block"\nrules = ["tsthk1"]\nplatform = "claude-code"\n'
            'event = "Stop"\nhandler = "stop_handler"\ndefault = "block"\nclass = "b"\n'
            'residue = "A Stop self-test control."\n'
            '\n[[hook]]\nid = "selftest-idle-block"\nrules = ["tsthk1"]\nplatform = "claude-code"\n'
            'event = "TeammateIdle"\nhandler = "idle_handler"\ndefault = "block"\nclass = "b"\n'
            'residue = "A TeammateIdle self-test control."\n'), encoding="utf-8")

        def onfailure_findings(text):
            """The case-19 contract over one rendered hooks.json: per (event, handler), the exact
            onFailure value (None where absent) and timeout, compared as a whole map."""
            try:
                rendered = json.loads(text)
            except ValueError as exc:
                return ["unparseable hooks.json: {}".format(exc)]
            got = {}
            for event, entries in rendered.get("hooks", {}).items():
                for e in entries:
                    for h in e.get("hooks", ()):
                        got[(event, h["args"][-1])] = (h.get("onFailure"), h.get("timeout"))
            want = {("PreToolUse", "test_handler"): ("block", 45),
                    ("PreToolUse", "warn_handler"): (None, TIMEOUT),
                    ("Stop", "stop_handler"): (None, TIMEOUT),
                    ("TeammateIdle", "idle_handler"): (None, TIMEOUT)}
            if got != want:
                return ["rendered onFailure/timeout map {} does not equal the expected {}"
                        .format(got, want)]
            return []

        if run_quiet(ofr_on, check=False) != 0:
            failures.append("on-failure-render true: generation expected exit 0")
        else:
            on_json = (ofr_on / HOOKS_JSON_REL).read_text(encoding="utf-8")
            probs = onfailure_findings(on_json)
            if probs:
                failures.append("on-failure-render true: " + "; ".join(probs))
            mutant = json.loads(on_json)
            for e in mutant["hooks"]["PreToolUse"]:
                for h in e["hooks"]:
                    h.pop("onFailure", None)
            if not onfailure_findings(json.dumps(mutant, indent=2) + "\n"):
                failures.append("a mutant output dropping onFailure must fail the case-19 contract")
            mutant = json.loads(on_json)
            for entries in mutant["hooks"].values():
                for e in entries:
                    for h in e["hooks"]:
                        h["timeout"] = TIMEOUT
            if not onfailure_findings(json.dumps(mutant, indent=2) + "\n"):
                failures.append("a mutant output resetting the configured timeout must fail the "
                                "case-19 contract")
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
          "PreToolUse and the SessionStart-cannot-block overclaim); a Stop hook renders "
          "byte-identical hooks.json whether its default is 'block' or 'warn'; the dispatcher's "
          "PreToolUse deadline must mirror TIMEOUT and leave a margin from DEADLINE_MIN_MARGIN up to just "
          "below TIMEOUT (each end refused just outside it), with no second binding of either name; an "
          "authored per-hook timeout is accepted only as an integer from TIMEOUT_MIN to TIMEOUT_MAX "
          "(each end refused just outside it, bool/string/float refused), with the optional [plugin] "
          "keys validated fail-closed; with on-failure-render false nothing new renders (hooks.json "
          "stays byte-identical, no onFailure anywhere); and with it true exactly the enforce-stage "
          "PreToolUse block hooks carry onFailure 'block' plus their configured timeout, a contract "
          "shown discriminating against a mutant that drops the key and one that resets the timeout"
          + note)
    return 0


if __name__ == "__main__":
    sys.exit(main())
