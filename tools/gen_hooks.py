#!/usr/bin/env python3
"""Generate the Claude Code enforcement-plugin surface from the hooks manifest (GD-24).

The pack's mechanical-enforcement controls are declared in .aiqt/core/hooks/manifest.toml (one
[[hook]] per control) with the handler implementations beside it under .aiqt/core/hooks/scripts/.
This tool renders them into the shipped, NESTED plugin surface under plugin/aiqt-guardrails-hooks/:
  plugin/aiqt-guardrails-hooks/hooks/hooks.json             the plugin hook config
  plugin/aiqt-guardrails-hooks/hooks/scripts/aiqt_hooks.py  the dispatcher, copied byte-identical
  plugin/aiqt-guardrails-hooks/.claude-plugin/plugin.json   the plugin manifest
  .claude/settings.json                                     this repository's own project hook registration
                                                            (the authoring tree only, never an adopter tree)
The plugin is nested (not the whole repo) so an adopter installs only the plugin, never the authoring
repo. The .aiqt/core/hooks/ tree is the single edit point; the generated surface is always
regenerated, never hand-edited, and plugin/aiqt-guardrails-hooks/hooks/ is a RESERVED subtree (100%
generated, orphan-pruned) exactly like the .cursor/rules/aiqt-guardrails/ adapter. The repository-local
.claude/settings.json is generated WHOLE (personal settings belong in .claude/settings.local.json): it
registers the PROJECT_HOOK_IDS controls for this repository's own Claude Code sessions, each matcher copied
from the manifest, so those controls run here without the plugin installed. run() renders it ONLY when the
generated tree carries the authoring marker .aiqt/authoring.toml (tracked here, never shipped), so an
adopter run never creates, overwrites or deletes an adopter's own .claude/settings.json; build_desired()
never renders it, because conformance.py C5 reuses build_desired() to judge an adopter install, which this
repository's project settings must not oblige. Validation is
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

import json
import os
import re
import stat
import tomllib
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
# The repository-local project registration (the dogfood wiring): this repository's own sessions load
# .claude/settings.json, so the controls listed here run without the plugin installed. Rendered by run()
# only, never by build_desired() (conformance.py C5 reuses that for an adopter install). $CLAUDE_PROJECT_DIR
# is the project root Claude Code sets when it spawns a hook command, double-quoted so a root holding a
# space stays one word; the script is the drift-gated plugin copy, which the plugin hooks/ tree entry of
# the gensrc registry protects (the source dispatcher is only a block entry there).
PROJECT_SETTINGS_REL = ".claude/settings.json"
PROJECT_HOOK_IDS = ("gensrc-edit-guard",)
PROJECT_SCRIPT_PATH = '"$CLAUDE_PROJECT_DIR"/' + SCRIPT_REL
# The authoring-tree test. Those project settings are this repository's dogfood wiring, but this tool ships to
# adopters, so run() renders them ONLY when the tree it generates carries this marker: a tracked file that the
# ownership map excludes from the release by an exact literal (gen_manifest.check_authoring_only refuses it a
# release class), so no release, pin or install puts it in an adopter tree. The test reads the generated tree
# itself, never an environment variable or the cwd, so the tree tested is the tree written. Without the
# marker (every adopter tree) .claude/settings.json is never created, overwritten or deleted. A present
# marker that is not a regular file of at most AUTHORING_MARKER_MAX_BYTES (the bound gensrc_guard applies,
# _GENSRC_MAX_BYTES, pinned by tools/selftest_aiqt_hooks.py) holding exactly AUTHORING_MARKER fails closed
# (exit 2), so damage to it here cannot silently drop the drift check. AUTHORING_ONLY_REL is the never-shipped
# roster: gen_gensrc and gen_manifest both read it from this file statically (gen_gensrc.read_authoring_roster),
# gen_gensrc scopes its registry entries "authoring", so gensrc_guard enforces them only where this marker is
# exact, and gen_manifest keeps them out of the release (with this roster as imported beside it, whatever a
# tree under check rosters). Keep it a tuple of literals, or of non-dunder names each bound exactly once, by
# one plain top-level assignment above it, to a str literal; any other binding fails closed.
AUTHORING_MARKER_REL = ".aiqt/authoring.toml"
AUTHORING_MARKER = {"format-version": 1, "role": "pack-authoring-repository"}
AUTHORING_MARKER_MAX_BYTES = 1_000_000
AUTHORING_ONLY_REL = (AUTHORING_MARKER_REL, PROJECT_SETTINGS_REL)

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
    {"target": ".claude/settings.json", "kind": "file",
     "sources": (".aiqt/core/hooks/manifest.toml",),
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


def render_project_settings(hooks, ids=PROJECT_HOOK_IDS):
    """The repository-local .claude/settings.json, deterministic and generated whole: the controls named in
    ids, in the render_hooks_json order (events in KNOWN_EVENTS order, entries sorted by id), each with its
    matcher COPIED from the manifest so the project wiring cannot drift from the declared scope. The
    settings form carries a shell-string command, not command + args, so the launcher is spelled
    `python3 -I <script> <handler>` (isolated, as check_python_launcher_isolation requires). Raises
    ValueError when an id names no manifest control, so a renamed control fails closed (exit 2) instead of
    silently dropping the project wiring."""
    known = {hook["id"]: hook for hook in hooks}
    missing = [hid for hid in ids if hid not in known]
    if missing:
        raise ValueError("{}: project hook id(s) not in the manifest: {}".format(
            PROJECT_SETTINGS_REL, ", ".join(missing)))
    events = {}
    for hook in sorted((known[hid] for hid in ids),
                       key=lambda h: (KNOWN_EVENTS.index(h["event"]), h["id"])):
        entry = {}
        if "matcher" in hook:
            entry["matcher"] = hook["matcher"]
        entry["hooks"] = [{"type": "command",
                           "command": "python3 -I {} {}".format(PROJECT_SCRIPT_PATH, hook["handler"]),
                           "timeout": TIMEOUT}]
        events.setdefault(hook["event"], []).append(entry)
    return json.dumps({"hooks": events}, indent=2) + "\n"


def authoring_tree(root):
    """True when root is the pack authoring repository: AUTHORING_MARKER_REL is a regular file of at most
    AUTHORING_MARKER_MAX_BYTES holding exactly AUTHORING_MARKER (format-version the integer 1, never the bool
    true). False when it is absent (an adopter tree). Raises ValueError on a marker that is present but is not
    a regular file (a symlink is not followed; a directory, FIFO, ...; lstat is probed before the open, so a
    stationary FIFO never blocks), is over the bound, is not UTF-8 TOML, or holds anything else, and OSError on
    an unreadable path; the caller maps both to exit 2. The bound and every refusal match gensrc_guard
    (_gensrc_authoring_marker), so the two never disagree on which tree is the authoring tree."""
    path = root / AUTHORING_MARKER_REL
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return False
    if not stat.S_ISREG(st.st_mode):
        raise ValueError("{}: the authoring marker must be a regular file".format(AUTHORING_MARKER_REL))
    with open(path, "rb") as handle:
        raw = handle.read(AUTHORING_MARKER_MAX_BYTES + 1)
    if len(raw) > AUTHORING_MARKER_MAX_BYTES:
        raise ValueError("{}: the authoring marker exceeds the {}-byte bound".format(
            AUTHORING_MARKER_REL, AUTHORING_MARKER_MAX_BYTES))
    try:
        data = tomllib.loads(raw.decode("utf-8"))  # UnicodeDecodeError and TOMLDecodeError are ValueErrors
    except RecursionError as exc:
        raise ValueError("{}: the authoring marker nests too deeply to parse ({})".format(
            AUTHORING_MARKER_REL, exc)) from exc
    # type() as well as equality: the TOML bool true == 1 in Python, and gensrc_guard (which honours an
    # authoring-scoped registry entry on this same marker) reads exactly the integer.
    if data != AUTHORING_MARKER or type(data.get("format-version")) is not int:
        raise ValueError("{}: the authoring marker must hold exactly {!r}".format(
            AUTHORING_MARKER_REL, AUTHORING_MARKER))
    return True


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


def run(root, check, project_ids=PROJECT_HOOK_IDS):
    """Reconcile the generated plugin surface under root; the gen_cursor.py reconcile idiom. With the hooks
    source present AND root the authoring tree (authoring_tree), the repository-local PROJECT_SETTINGS_REL
    registering project_ids is reconciled too, outside build_desired() so conformance.py C5 never requires
    it of an adopter. In any other tree it is not in the desired set, so it is never written or compared,
    and it is never pruned: .claude/ is not a reserved subtree. The self-test's synthetic manifests carry
    no project control, so its plugin-surface cases pass project_ids=()."""
    src_dir = root / ".aiqt" / "core" / "hooks"
    desired = {}
    try:
        # dir_present (not is_dir) inside the try: an unreadable .aiqt/ parent must fail closed as
        # exit 2, not read as an absent manifest (which would prune the whole surface as orphans).
        if dir_present(src_dir):
            desired = build_desired(root, src_dir)
            if project_ids and authoring_tree(root):
                _plugin, hooks = load_manifest(src_dir / "manifest.toml")
                desired[PROJECT_SETTINGS_REL] = render_project_settings(hooks, project_ids)
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
#  14. 'warn' on PreToolUse is accepted only under stage = "bake" (exit 2 without it),
#  15. a SessionStart control declaring 'block' fails closed (exit 2): the platform cannot block there.
#  16. the repository-local .claude/settings.json renders exactly one PreToolUse group for the project
#      control, its matcher copied from the manifest and its launcher the exact isolated shell string,
#  17. drift in (or deletion of) that file fails the check (exit 1): it is in run()'s desired set,
#  18. build_desired() never carries it (conformance.py C5 reuses build_desired for an adopter install),
#  19. a manifest without the project control fails closed (exit 2) under run()'s default project ids,
#  20. an adopter-shaped tree (no authoring marker) never creates, overwrites or deletes .claude/settings.json,
#      in write or check mode, whatever the cwd, while its plugin surface still generates,
#  21. a present marker that is not exactly the authoring marker (other content, a bool format-version,
#      unparseable, a directory, a symlink, a FIFO, empty, non-UTF-8, one byte over the size bound) fails
#      closed (exit 2) and renders no project settings, while a valid marker of exactly the bound renders them;
#      a marker fixture the loop cannot plant (os.symlink or os.mkfifo failing, or os.mkfifo absent) is
#      a NAMED NOT RUN failure (exit 1), never a skipped PASS, and the FIFO case runs in a bounded child
#      so a regression that blocks opening the FIFO is a reported failure, never a hang,
#  22. the NOT RUN discipline is red on revert: a child interpreter whose os.symlink refuses must still
#      exit nonzero naming the unplanted fixture; the child skips its own cases 22 and 23 through the
#      explicit self_test_main(_nested=True) argument, never through inherited environment,
#  23. the bad-marker handler stays narrow: an ordinary write failure inside a planter propagates as
#      itself, never as a NOT RUN unplantable-fixture diagnosis, red on widening the handler to OSError,
#  24. a timed-out bounded child is killed with its whole process group where one was created: a
#      shortened-bound parent starts a grandchild in the same group, the grandchild is confirmed
#      started, and after the timeout kill the grandchild is gone and the child TMPDIR removed
#      (red with group creation disabled or with the group kill removed; skipped, named in the
#      PASS NOTE, only where the platform has no os.killpg, and the PASS proof clause then drops
#      its group-kill sentence too),
#  25. an OSError raised by communicate while the child is alive still kills and REAPS the child
#      (its exit status collected into Popen.returncode: gone-or-zombie alone is not a reap),
#      removes the child TMPDIR and re-raises, never falling through to the unbounded wait in
#      Popen.__exit__,
#  26. the same discipline for a KeyboardInterrupt raised while the child is alive,
#  27. a SECONDARY failure inside the staged cleanup (a persistent drain error, a kill error, an
#      exhausted bounded reap, a close error on either pipe) never replaces the original
#      exception: the original still re-raises, the secondary failure is a NAMED failure entry,
#      and the child is reaped (or its unreaped state named) with the child TMPDIR removed,
#  28. _rmtree_verified never follows a symlink out of the tree it removes (a directory symlink
#      inside the tree pointing at an OUTSIDE directory leaves that directory's mode unchanged;
#      the symlink is unlinked, never chmodded through), and treats only a confirmed absence
#      (FileNotFoundError from lstat) as removed: any other verification error is a named
#      failure, never a silent success.

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


# A second control appended to the synthetic manifest for the project-settings cases. Its matcher differs
# from the real manifest's gensrc matcher, so a renderer that hard-codes the matcher fails case 16.
_PROJECT_HOOK = """
[[hook]]
id = "gensrc-edit-guard"
rules = ["tsthk1"]
platform = "claude-code"
event = "PreToolUse"
matcher = "Edit|Bash"
handler = "gensrc_guard"
default = "block"
class = "c"
residue = "A self-test project control catches nothing real."
"""

_PROJECT_HANDLER = """
def gensrc_guard(data):
    return (0, None, None)
"""


# The authoring marker, spelled literally (never built from AUTHORING_MARKER), so a renamed key or role fails.
_MARKER_TEXT = 'format-version = 1\nrole = "pack-authoring-repository"\n'
# The marker size bound, spelled literally like _MARKER_TEXT (gensrc_guard _GENSRC_MAX_BYTES, which
# tools/selftest_aiqt_hooks.py pins AUTHORING_MARKER_MAX_BYTES to), and the valid marker padded by a TOML comment
# to exactly `size` bytes.
_MARKER_BOUND = 1_000_000


def _padded_marker(size):
    return _MARKER_TEXT + "#" + "x" * (size - len(_MARKER_TEXT) - 2) + "\n"


def _write_marker(base, text=_MARKER_TEXT):
    (base / ".aiqt" / "authoring.toml").write_text(text, encoding="utf-8")


def _build_project_tree(base, authoring=True):
    _build_tree(base)
    src = base / ".aiqt" / "core" / "hooks"
    for path, tail in ((src / "manifest.toml", _PROJECT_HOOK), (src / "scripts" / SCRIPT_NAME, _PROJECT_HANDLER)):
        path.write_text(path.read_text(encoding="utf-8") + tail, encoding="utf-8")
    if authoring:
        _write_marker(base)


# The bad-marker loop's NOT RUN discipline (#535's rule: coverage that did not run FAILS the self-test,
# never a silent PASS). Raised ONLY by a planter's fixture-CONSTRUCTION step (an os.symlink or os.mkfifo
# call, or a platform with no os.mkfifo at all), the steps whose failure can mean the runner lacks that
# filesystem support; an ordinary write failure in a planter propagates as itself, so it is never dressed
# as unavailable support (case 23 pins that narrowness red-on-widening). The loop converts this into a
# named NOT RUN entry in `failures`, so an unplantable fixture exits 1, never a skip note beside PASS.
class _FixtureUnavailable(Exception):
    pass


# Termination of the SUITE ITSELF is disclosed, not handled: an outer SIGTERM or SIGKILL aimed
# at this suite ends it WITHOUT the in-suite kill, reap and verified-removal cleanup (no SIGTERM
# handler is installed; the earlier SIGTERM-to-exception translation could itself be interrupted
# by a second SIGTERM during the final cleanup, before its handler restore and its re-delivery,
# so it was REMOVED rather than hardened), and a bounded child running in its own session
# survives such an outer kill. The child's time bound is enforced by THIS suite's communicate
# call, never by anything in the child itself, so once the suite is gone the orphan is NOT
# limited to that bound: it runs until its own code exits (the fixtures sleep 40s, but a
# BLOCKED child need never exit) or until another supervisor kills it. A parent-death signal
# for the child (the Linux prctl PR_SET_PDEATHSIG) is a possible LATER layer for that residue;
# it is not built here.


# The DEFAULT hard bound on a self-test child interpreter: a healthy child finishes in well under a
# second; the nested-suite children of cases 22 and 23 pass a longer bound derived from this one,
# because each runs this whole suite again. A child that exceeds its bound is killed together with its
# whole process group where one was created (the platform has os.killpg and the suite is top level;
# otherwise only the direct child is killed), only the group LEADER is reaped by the suite, and the
# kill is reported as a failure attributed to the BOUND: the child may be
# blocked (as a regression reopening the FIFO marker read would be) or merely slower than the bound.
# Recursion suppression for the nested suites is the explicit self_test_main(_nested=True) argument
# their code text passes; it is never read from inherited environment, so no ambient variable can
# silently remove coverage while the PASS line still claims it ran.
_CHILD_TIMEOUT = 120

# The bounded reap inside the staged cleanup of _run_bounded: the pipes of a killed child are
# drained for at most _REAP_DRAIN_BOUND seconds, then the child is re-killed and waited for at
# most _REAP_WAIT_BOUND more; a child still unreaped after both bounds is a NAMED failure left
# to init, never an unbounded wait. Case 27 PINS the drain bound (the exhausted-reap vector
# records the timeout argument the drain carried), so an unbounded drain cannot come back
# silently.
_REAP_DRAIN_BOUND = 10
_REAP_WAIT_BOUND = 10

# The spawn-to-store signal block of _run_bounded (SIGINT and SIGTERM, blocked with
# signal.pthread_sigmask) is INHERITED by a spawned child on POSIX, so every bounded child
# first unblocks exactly those two signals again; a platform without pthread_sigmask never
# blocked them, and the reset degrades to a no-op there.
_CHILD_MASK_RESET = ("import signal as _signal_reset\n"
                     "if hasattr(_signal_reset, \"pthread_sigmask\"):\n"
                     "    _signal_reset.pthread_sigmask(\n"
                     "        _signal_reset.SIG_UNBLOCK,\n"
                     "        {_signal_reset.SIGINT, _signal_reset.SIGTERM})\n"
                     "del _signal_reset\n")


def self_test_main(_nested=False):
    # _nested=True is passed ONLY by the code text of the case-22 and case-23 child drivers, so a
    # nested suite skips cases 22 to 30 instead of recursing; it is an explicit internal argument,
    # never an inherited environment variable.
    import io
    import shutil
    import signal
    import stat
    import subprocess
    import tempfile
    import time
    from contextlib import redirect_stdout

    def run_quiet(root, check):
        # The plugin-surface cases build manifests with no project control, so they pass project_ids=().
        with redirect_stdout(io.StringIO()):
            return run(root, check, project_ids=())

    def run_project(root, check):
        # The project-settings cases run with run()'s OWN default project ids, as main() does.
        with redirect_stdout(io.StringIO()):
            return run(root, check)

    try:
        tmp = Path(tempfile.mkdtemp(prefix="aiqt-gen-hooks-selftest-"))
    except OSError as exc:
        print("SELF-TEST ERROR: no writable temporary directory: {}".format(exc), file=sys.stderr)
        return 2
    failures = []
    skipped = []
    _case24_ran = False

    def _mutate(root, old, new):
        """Rewrite the built manifest in place to inject one fault, then the run must fail closed."""
        mpath = root / ".aiqt" / "core" / "hooks" / "manifest.toml"
        text = mpath.read_text(encoding="utf-8")
        if old not in text:
            failures.append("self-test setup: {!r} not found in manifest".format(old))
        mpath.write_text(text.replace(old, new), encoding="utf-8")

    def _rmtree_verified(path, what):
        # Removal is VERIFIED, never best effort: after shutil.rmtree, absence is proven by
        # os.lstat raising FileNotFoundError, and ANY other lstat error is a named failure
        # (an unverifiable removal is never a silent success). A failed first attempt
        # restores owner permissions on the entries this suite itself created (a chmod-0
        # fixture from case 5 or 10 can survive an exception raised between plant and
        # restore) and retries once; the repair never follows a symlink (os.walk does not
        # descend one, and every entry is CLASSIFIED with os.lstat: a symlink is unlinked,
        # only an entry lstat confirms is a directory is chmodded, and an entry lstat
        # CANNOT classify is a NAMED failure left alone, never a chmod, which could follow
        # a link planted where a directory stood), so nothing OUTSIDE this tree is ever
        # touched, up to one stated residual race: between the lstat and the chmod the
        # entry could be REPLACED by a symlink; this suite does not close that window (a
        # directory-descriptor fchmod would, at the cost of platform-specific O_NOFOLLOW
        # and O_DIRECTORY semantics). A tree still present after the retry is a NAMED
        # failure.
        path = str(path)

        def _repair(entry):
            try:
                entry_stat = os.lstat(entry)
            except OSError as exc:
                failures.append("{}: removal repair could not classify the entry {} ({}); "
                                "an unclassifiable entry is a named failure left alone, "
                                "never chmodded through a possible symlink"
                                .format(what, entry, exc))
                return
            try:
                if stat.S_ISLNK(entry_stat.st_mode):
                    os.unlink(entry)
                elif stat.S_ISDIR(entry_stat.st_mode):
                    os.chmod(entry, 0o700)
            except OSError:
                pass

        for attempt in (0, 1):
            try:
                shutil.rmtree(path)
            except OSError:
                pass
            try:
                os.lstat(path)
            except FileNotFoundError:
                return True
            except OSError as exc:
                failures.append("{}: removal of the temporary tree {} could not be verified "
                                "({}); only a confirmed absence counts as removed, so an "
                                "unverifiable removal is a named failure, never a silent "
                                "success".format(what, path, exc))
                return False
            if attempt == 0:
                _repair(path)
                for dirpath, dirnames, _filenames in os.walk(path):
                    for name in dirnames:
                        _repair(os.path.join(dirpath, name))
        failures.append("{}: the temporary tree {} was not removed; a tree left behind is a "
                        "named failure, never a silent success".format(what, path))
        return False

    def _confirmed_absent(path, what):
        # Only a CONFIRMED absence (FileNotFoundError from lstat) counts as removed; any
        # other lstat error is a cannot-verify, recorded as a NAMED failure.
        try:
            os.lstat(str(path))
        except FileNotFoundError:
            return True
        except OSError as exc:
            failures.append("{}: could not verify that {!r} is gone ({}); an unverifiable "
                            "absence is a named failure, never a silent success"
                            .format(what, path, exc))
        return False

    def _require_absent(path, what, message):
        # Required ABSENCE is proven the same way: only FileNotFoundError from lstat is
        # absence. Presence appends the caller's message, and an lstat error that leaves
        # the absence UNVERIFIABLE is its own named failure, never a silent pass (a bare
        # lexists would treat exactly that error as absence).
        try:
            os.lstat(str(path))
        except FileNotFoundError:
            return
        except OSError as exc:
            failures.append("{}: could not verify that {!r} is absent ({}); an "
                            "unverifiable absence is a named failure, never a silent "
                            "success".format(what, path, exc))
            return
        failures.append(message)

    tools_dir = str(Path(__file__).resolve().parent)

    def _run_bounded(label, code_text, timeout=_CHILD_TIMEOUT, timeout_is_failure=True):
        # Run a small driver in a fresh isolated interpreter under a hard bound, so a case that
        # could BLOCK (the FIFO marker open) or must be observed from outside (cases 22 to 27)
        # is a reported failure on regression, never a hang. A top-level driver starts its own
        # session where the platform has os.killpg (otherwise only the direct child can be
        # killed), and its TMPDIR is a private directory under the tmp dir of this suite. On
        # timeout, and on ANY other exception out of the guarded region below (an OSError or a
        # KeyboardInterrupt from communicate included), the STAGED cleanup runs: group or
        # direct kill, bounded reap, the close of each pipe, verified removal. Each stage is
        # guarded on its own against ANY exception (an interruption raised INSIDE a stage
        # included), so a failing stage becomes a NAMED failure entry and every later stage
        # still runs; the ORIGINAL exception is then re-raised, never replaced by a
        # secondary cleanup failure (cases 25 to 27 hold this, and nothing falls through to
        # the UNBOUNDED wait inside Popen.__exit__). Disclosed, not covered: an interruption
        # delivered BETWEEN two stage guards (in the glue between them) still skips the
        # later stages; only one raised inside a guarded stage is contained. The
        # spawn-to-store window is covered by TWO mechanisms (case 30 holds both). First,
        # SIGINT and SIGTERM are BLOCKED (signal.pthread_sigmask) from immediately before
        # the spawn until the handle store completes, and the previous mask is restored in
        # a finally, so a real interrupting signal landing in that window is DELIVERED
        # right after the restore, when the staged cleanup already owns the recorded child;
        # each child's code text starts with _CHILD_MASK_RESET, so the inherited block is
        # removed again inside the child. Second, the handle is PUBLISHED where the cleanup
        # reads it BEFORE Popen.__init__ runs (CPython presets pid and returncode early in
        # __init__ and sets pid right after the fork), so even a synchronously RAISED
        # interruption, which no signal mask can block, inside the spawn after the fork or
        # at the handle store leaves the cleanup owning a killable child; that interruption
        # is additionally a NAMED entry, because the case being run did not complete.
        # Stage 1 signals only a child whose returncode is still None: a child CPython
        # already reaped (an exec failure is reaped inside Popen, its returncode set) is
        # never signalled, so a reused pid is never a target. Only the group LEADER (the direct
        # child) is reaped here; other group members are reparented to and reaped by init or
        # the nearest subreaper. A NESTED suite keeps its drivers in its own process group,
        # so a top-level group kill reaches them too. Residuals, disclosed: a descendant that
        # starts its OWN session or moves itself to another process group (os.setpgid,
        # start_new_session) leaves the group and survives the group kill (no driver here
        # does either); and a NORMAL child exit kills nothing beyond the child itself, so a
        # same-group descendant left behind by a cleanly exiting child would outlive
        # _run_bounded (no driver here leaves one). Returns (exit code, combined output); a
        # child-TMPDIR or spawn failure appends its own named failure and returns (None, "");
        # a timeout appends one too and returns (None, "") unless timeout_is_failure=False
        # (case 24 expects its timeout), where it returns (None, "timeout: " + how the kill
        # went).
        try:
            child_tmp = tempfile.mkdtemp(prefix="bounded-child-", dir=str(tmp))
        except OSError as exc:
            failures.append("{}: could not create the child TMPDIR ({}); a creation failure "
                            "is a named failure, never a traceback".format(label, exc))
            return None, ""
        child_env = dict(os.environ)
        child_env["TMPDIR"] = child_tmp
        group = hasattr(os, "killpg") and not _nested
        proc = None
        _spawned = {"handle": None}

        class _PublishedPopen(subprocess.Popen):
            # The handle is published where _cleanup_stages reads it BEFORE __init__ runs
            # (CPython presets pid and returncode to None early in __init__ and sets pid
            # right after the fork), so an interruption raised inside the spawn or at the
            # outer handle store leaves the cleanup owning the child.
            def __init__(self, *args, **kwargs):
                _spawned["handle"] = self
                super().__init__(*args, **kwargs)

        def _cleanup_stages(kill):
            # The staged cleanup, shared by EVERY exit from the guarded region below. Each
            # stage is guarded on its own against ANY exception (an interruption raised
            # inside the stage included): a failing stage becomes a NAMED failure entry and
            # the remaining stages still run, so no secondary failure here can replace an
            # exception propagating through the caller. An interruption delivered BETWEEN
            # two guards still skips the later stages; that gap is disclosed above, not
            # covered. Returns True only when the group kill actually happened, so no
            # caller claims a group kill that did not.
            proc = _spawned["handle"]
            pid = getattr(proc, "pid", None)
            group_killed = False
            # Stage 1: kill the whole process group where one was created, otherwise the
            # direct child, and ONLY while the child is unreaped (returncode is None): a
            # reaped pid may already be reused and is never signalled. A kill that itself
            # fails is a NAMED failure.
            if kill and proc is not None and pid is not None and proc.returncode is None:
                if group:
                    try:
                        os.killpg(pid, signal.SIGKILL)
                        group_killed = True
                    except OSError:
                        pass
                    except BaseException as exc:
                        failures.append("{}: cleanup stage 1: the group kill of the child "
                                        "(pid {}) failed ({!r}); a kill failure is a named "
                                        "failure, never an exception replacing the original"
                                        .format(label, pid, exc))
                if not group_killed:
                    try:
                        proc.kill()
                    except BaseException as exc:
                        failures.append("{}: cleanup stage 1: the direct kill of the child "
                                        "(pid {}) failed ({}); a kill failure is a named "
                                        "failure, never an exception replacing the original"
                                        .format(label, pid, exc))
            # Stage 2: bounded reap: drain the pipes for at most _REAP_DRAIN_BOUND seconds,
            # then re-kill and wait at most _REAP_WAIT_BOUND more; a child still unreaped
            # after both bounds is a NAMED failure left to init, never an unbounded wait.
            if kill and proc is not None and pid is not None and proc.returncode is None:
                try:
                    proc.communicate(timeout=_REAP_DRAIN_BOUND)
                except subprocess.TimeoutExpired:
                    try:
                        proc.kill()
                    except BaseException as exc:
                        failures.append("{}: cleanup stage 2: the re-kill of the child "
                                        "(pid {}) failed ({}); a re-kill failure is a "
                                        "named failure, never a silent pass"
                                        .format(label, pid, exc))
                    try:
                        proc.wait(timeout=_REAP_WAIT_BOUND)
                    except subprocess.TimeoutExpired:
                        pass
                    except BaseException as exc:
                        failures.append("{}: cleanup stage 2: the bounded wait failed "
                                        "({}); a wait failure is a named failure, and "
                                        "the unreaped check below still runs"
                                        .format(label, exc))
                except BaseException as exc:
                    failures.append("{}: cleanup stage 2: the {}s pipe drain failed ({}); a "
                                    "drain failure is a named failure, and the bounded wait "
                                    "below still collects the child where it can"
                                    .format(label, _REAP_DRAIN_BOUND, exc))
                    try:
                        proc.wait(timeout=_REAP_WAIT_BOUND)
                    except subprocess.TimeoutExpired:
                        pass
                    except BaseException as exc:
                        failures.append("{}: cleanup stage 2: the bounded wait failed "
                                        "({}); a wait failure is a named failure, and "
                                        "the unreaped check below still runs"
                                        .format(label, exc))
                if proc.returncode is None:
                    failures.append("{}: cleanup stage 2: the child (pid {}) was not reaped "
                                    "within the {}s drain and {}s wait bounds; an unreaped "
                                    "child is a named failure left to init, never an "
                                    "unbounded wait".format(label, pid,
                                                            _REAP_DRAIN_BOUND,
                                                            _REAP_WAIT_BOUND))
            # Stage 3: close each pipe on its OWN guard, so a failing stdout close never
            # skips the stderr close (communicate already closed both on the healthy path,
            # where closing again is a no-op).
            if proc is not None:
                for stream_name in ("stdout", "stderr"):
                    stream = getattr(proc, stream_name, None)
                    if stream is not None:
                        try:
                            stream.close()
                        except BaseException as exc:
                            failures.append("{}: cleanup stage 3: the {} close failed ({}); "
                                            "a close failure is a named failure and the "
                                            "other pipe is still closed"
                                            .format(label, stream_name, exc))
            # Stage 4: verified removal of the child TMPDIR; an OSError inside is named by
            # the helper itself, and ANY other exception out of it (an interruption
            # included) is named HERE, so the removal can neither replace the original
            # exception nor skip a caller that follows.
            try:
                _rmtree_verified(child_tmp, label)
            except BaseException as exc:
                failures.append("{}: cleanup stage 4: the verified removal of the child "
                                "TMPDIR raised {!r}; a removal fault is a named failure, "
                                "never an exception replacing the original"
                                .format(label, exc))
            return group_killed

        try:
            # SIGINT and SIGTERM are blocked from immediately before the spawn until the
            # handle store below completes; the inner finally restores the previous mask,
            # so a real interrupting signal landing in that window is delivered right
            # after the restore, when _cleanup_stages already owns the recorded child.
            previous_mask = None
            if hasattr(signal, "pthread_sigmask"):
                previous_mask = signal.pthread_sigmask(
                    signal.SIG_BLOCK, {signal.SIGINT, signal.SIGTERM})
            try:
                proc = _PublishedPopen(
                    [sys.executable, "-I", "-B", "-c", _CHILD_MASK_RESET + code_text],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                    env=child_env, start_new_session=group)
            finally:
                if previous_mask is not None:
                    signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)
            out, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            group_killed = _cleanup_stages(kill=True)
            how = ("killed with its whole process group" if group_killed else
                   "killed (the direct child only: no process group was created, or the "
                   "group kill failed)")
            if timeout_is_failure:
                failures.append("{}: the bounded child exceeded its {}s bound and was {}; "
                                "exceeding the bound is a reported failure, never a hang"
                                .format(label, timeout, how))
                return None, ""
            return None, "timeout: " + how
        except OSError as exc:
            _cleanup_stages(kill=True)
            if proc is None:
                # The spawn itself failed: an exec-failed child is reaped INSIDE Popen
                # with its returncode set, so the gated stages above never signal it (a
                # reaped pid may be reused); the close and removal stages still ran.
                failures.append("{}: could not launch the bounded child ({})".format(label, exc))
                return None, ""
            raise
        except BaseException:
            # A KeyboardInterrupt or any other non-timeout exception inside the guarded
            # region, the spawn included: the published handle lets the staged cleanup
            # kill and reap a child that was already forked (cases 26, 27 and 30 exercise
            # this path), then the ORIGINAL exception re-raises. An interruption that
            # arrived before the handle store completed is additionally a NAMED entry:
            # the case being run did not complete.
            if proc is None and _spawned["handle"] is not None:
                failures.append("{}: the spawn was interrupted between the fork and the "
                                "handle store (pid {}); the published handle lets the "
                                "staged cleanup below kill and reap the child"
                                .format(label, getattr(_spawned["handle"], "pid", None)))
            _cleanup_stages(kill=True)
            raise
        _cleanup_stages(kill=False)
        return proc.returncode, out + err

    def _pid_gone(pid):
        # A SIGKILLed process may stay visible as a ZOMBIE until its reaper collects it (the
        # case-24 grandchild is reparented to init or to the nearest subreaper), so a zombie
        # counts as killed: it runs no code and holds no descriptor.
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        except OSError:
            return False
        try:
            with open("/proc/{}/stat".format(pid), "rb") as fh:
                return fh.read().rsplit(b")", 1)[1].split()[:1] == [b"Z"]
        except (OSError, IndexError):
            return False

    def _wait_pid_gone(pid, bound):
        deadline = time.monotonic() + bound
        while time.monotonic() < deadline:
            if _pid_gone(pid):
                return True
            time.sleep(0.1)
        return _pid_gone(pid)

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
        #    chmod-0 dir (root/DAC-bypass), observed via os.access, as in conformance.py. This skip is NOT
        #    the bad-marker loop's NOT RUN failure: there the fixture's CONSTRUCTION fails, an anomaly on
        #    every supported runner; here the fixture is PLANTED (chmod 0 succeeded) and the runner's own
        #    identity defeats its semantics: no chmod-based fixture can make this path unreadable to a
        #    DAC-bypassing runner (root is one such identity), and simulating the refusal in-process would
        #    exercise the simulation, not the generator's own filesystem handling, so a failure here
        #    would turn every DAC-bypassing runner permanently red (conformance.py, gen_gensrc.py and
        #    gen_first_pin_demo.py keep the same deliberate skip).
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
        #     file (root/DAC-bypass), observed via os.access, as in case 5, and kept a skip (not a NOT RUN
        #     failure) for case 5's reason: the fixture is planted; the runner's identity defeats it.
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

        # 16. The repository-local .claude/settings.json: exactly one PreToolUse group, for the project
        #     control only, its matcher copied from the manifest, and the exact isolated launcher. The
        #     expected value is spelled literally, never built from the module constants.
        proj = tmp / "project"
        proj.mkdir()
        _build_project_tree(proj)
        if run_project(proj, check=False) != 0:
            failures.append("project tree: generation expected exit 0")
        want_cmd = ('python3 -I "$CLAUDE_PROJECT_DIR"/plugin/aiqt-guardrails-hooks/hooks/scripts/'
                    'aiqt_hooks.py gensrc_guard')
        want = {"hooks": {"PreToolUse": [{"matcher": "Edit|Bash", "hooks": [
            {"type": "command", "command": want_cmd, "timeout": TIMEOUT}]}]}}
        settings = proj / ".claude" / "settings.json"
        try:
            got = json.loads(settings.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            got = "unreadable ({})".format(exc)
        if got != want:
            failures.append("project settings: expected {!r}, got {!r}".format(want, got))

        # 17. Drift in, or deletion of, the project settings fails the check (exit 1), and regeneration
        #     reconciles it: the file is in run()'s desired set, not a write-only side output.
        if run_project(proj, check=True) != 0:
            failures.append("project tree: regeneration expected drift-clean exit 0")
        try:
            text = settings.read_text(encoding="utf-8")
            if "python3 -I " not in text:
                failures.append("project settings setup: no isolated launcher to strip")
            settings.write_text(text.replace("python3 -I ", "python3 "), encoding="utf-8")
            if run_project(proj, check=True) != 1:
                failures.append("drifted project settings expected exit 1")
            if run_project(proj, check=False) != 0 or run_project(proj, check=True) != 0:
                failures.append("regeneration after project settings drift expected a clean reconcile")
            settings.unlink()
            if run_project(proj, check=True) != 1:
                failures.append("a deleted project settings file expected exit 1")
        except OSError as exc:
            failures.append("project settings drift case: {}".format(exc))

        # 18. build_desired() never carries the project settings: conformance.py C5 reuses it to judge an
        #     adopter install, which this repository's project wiring must not oblige.
        try:
            keys = set(build_desired(proj, proj / ".aiqt" / "core" / "hooks"))
        except (ValueError, OSError) as exc:
            keys = set()
            failures.append("project tree: build_desired expected to succeed: {}".format(exc))
        if ".claude/settings.json" in keys or PROJECT_SETTINGS_REL in keys:
            failures.append("build_desired() must not carry the project settings (adopter C5)")

        # 19. A manifest without the project control fails closed (exit 2) under run()'s default, so a
        #     renamed control cannot silently drop the project wiring.
        noproj = tmp / "noproj"
        noproj.mkdir()
        _build_tree(noproj)
        _write_marker(noproj)
        if run_project(noproj, check=True) != 2:
            failures.append("a manifest without the project control expected exit 2 (fail-closed)")

        # 20. An adopter-shaped tree (the shipped hooks source, no authoring marker): a write run and a check
        #     run leave an existing adopter .claude/settings.json byte-identical and exit 0, and a tree with
        #     none gets none (not even .claude/). The cwd is an authoring-shaped tree throughout, so a test
        #     that read the cwd instead of the generated tree fails here.
        adopter_bytes = b'{\n  "permissions": {"allow": ["Bash(make test)"]}\n}\n'
        adopter = tmp / "adopter"
        adopter.mkdir()
        _build_project_tree(adopter, authoring=False)
        (adopter / ".claude").mkdir()
        (adopter / ".claude" / "settings.json").write_bytes(adopter_bytes)
        bare = tmp / "adopter-bare"
        bare.mkdir()
        _build_project_tree(bare, authoring=False)
        saved_cwd = os.getcwd()
        try:
            os.chdir(proj)
            for check in (False, True):
                if run_project(adopter, check=check) != 0:
                    failures.append("adopter tree (check={}): expected exit 0".format(check))
                if run_project(bare, check=check) != 0:
                    failures.append("bare adopter tree (check={}): expected exit 0".format(check))
        finally:
            os.chdir(saved_cwd)
        try:
            if (adopter / ".claude" / "settings.json").read_bytes() != adopter_bytes:
                failures.append("an adopter run must leave the adopter .claude/settings.json byte-identical")
        except OSError as exc:
            failures.append("an adopter run must never delete the adopter .claude/settings.json ({})".format(
                exc))
        _require_absent(bare / ".claude", "bare adopter tree",
                        "an adopter run must never create .claude/ or .claude/settings.json")
        if not (adopter / HOOKS_JSON_REL).is_file():
            failures.append("adopter tree: the plugin surface must still generate")

        # 21. A present marker that is not exactly the authoring marker fails closed (exit 2) and renders no
        #     settings: other content, unparseable text, a directory, a symlink to a valid marker (lstat, never
        #     followed), a FIFO (lstat first, so no open and no hang), an empty file, non-UTF-8 bytes, and a valid
        #     marker padded one byte over the bound. 21b. The same marker padded to exactly the bound is the
        #     authoring marker (exit 0, settings rendered), so the bound is not off by one. A fixture this loop
        #     cannot plant is a NAMED NOT RUN failure (exit 1), never a skip: _FixtureUnavailable comes only
        #     from a planter's construction step (an os.symlink or os.mkfifo call, or a platform with no
        #     os.mkfifo at all), so an ordinary write failure
        #     propagates as itself, never dressed as unavailable support. The FIFO case runs through
        #     _run_bounded (write mode, run()'s default project ids, exactly what run_project calls), so a
        #     regression that reopens the blocking FIFO read (dropping authoring_tree's regular-file refusal)
        #     is a reported failure within its bound plus a bounded kill, reap and cleanup, never
        #     a hang of this suite.
        bad_markers = [("other content", lambda b: _write_marker(b, 'format-version = 1\nrole = "adopter"\n')),
                       ("a bool format-version", lambda b: _write_marker(
                           b, 'format-version = true\nrole = "pack-authoring-repository"\n')),
                       ("unparseable", lambda b: _write_marker(b, "not toml [\n")),
                       ("a directory", lambda b: (b / ".aiqt" / "authoring.toml").mkdir()),
                       ("empty", lambda b: _write_marker(b, "")),
                       ("non-UTF-8", lambda b: (b / ".aiqt" / "authoring.toml").write_bytes(
                           b"\xff\xfe format-version = 1\n")),
                       ("one byte over the bound", lambda b: _write_marker(b, _padded_marker(_MARKER_BOUND + 1)))]

        def _plant_link(b):
            # The ordinary write stays OUTSIDE the construction guard: its failure is a plain error,
            # never "unavailable symlink support".
            (b / "real-marker.toml").write_text(_MARKER_TEXT, encoding="utf-8")
            try:
                os.symlink(b / "real-marker.toml", b / ".aiqt" / "authoring.toml")
            except (OSError, NotImplementedError) as exc:
                raise _FixtureUnavailable("os.symlink: {}".format(exc)) from exc
        bad_markers.append(("a symlink", _plant_link))

        def _plant_fifo(b):
            if not hasattr(os, "mkfifo"):
                raise _FixtureUnavailable("no os.mkfifo on this platform")
            try:
                os.mkfifo(str(b / ".aiqt" / "authoring.toml"))
            except OSError as exc:
                raise _FixtureUnavailable("os.mkfifo: {}".format(exc)) from exc
        bad_markers.append(("a FIFO", _plant_fifo))
        for i, (label, plant) in enumerate(bad_markers):
            badm = tmp / "badmarker{}".format(i)
            badm.mkdir()
            _build_project_tree(badm, authoring=False)
            try:
                plant(badm)
            except _FixtureUnavailable as exc:
                failures.append("NOT RUN: a marker that is {}: the fixture could not be planted ({}); "
                                "coverage that did not run fails the self-test".format(label, exc))
                continue
            if label == "a FIFO":
                code, _out = _run_bounded(
                    "a marker that is a FIFO",
                    "import sys\n"
                    "sys.path.insert(0, {tools!r})\n"
                    "from pathlib import Path\n"
                    "import gen_hooks\n"
                    "sys.exit(gen_hooks.run(Path({root!r}), False))\n".format(
                        tools=tools_dir, root=str(badm)))
                if code is None:
                    continue
            else:
                code = run_project(badm, check=False)
            if code != 2:
                failures.append("a marker that is {} expected exit 2 (fail-closed)".format(label))
            _require_absent(badm / ".claude" / "settings.json",
                            "a marker that is {}".format(label),
                            "a marker that is {} must not render project settings"
                            .format(label))
        at_bound = tmp / "markerbound"
        at_bound.mkdir()
        _build_project_tree(at_bound, authoring=False)
        _write_marker(at_bound, _padded_marker(_MARKER_BOUND))
        if run_project(at_bound, check=False) != 0 or not (at_bound / PROJECT_SETTINGS_REL).is_file():
            failures.append("a valid marker padded to exactly the bound expected exit 0 with project settings")

        # Cases 22 and 23 each re-run this whole suite in a child interpreter whose code text calls
        # self_test_main(_nested=True): recursion suppression is that explicit argument, NEVER inherited
        # environment, so no ambient variable can remove these cases silently while PASS still claims
        # their proof (the earlier environment gate did exactly that when GEN_HOOKS_SELFTEST_NESTED=1
        # leaked into the suite's environment).
        if not _nested:
            # 22. The NOT RUN discipline itself, red on revert (the train #550 finding on #526): with
            #     the symlink fixture unplantable (os.symlink refuses), this self-test must still exit
            #     nonzero and NAME the unplanted fixture; a skip note beside PASS would let a
            #     symlink-accepting authoring_tree regression ship unexercised on exactly such a
            #     runner. No faulty authoring_tree is planted in the child: with the fixture
            #     unplantable it could never execute, so the child proves the discipline, not the
            #     fault.
            code, out = _run_bounded(
                "the NOT RUN discipline (case 22)",
                "import os\n"
                "import sys\n"
                "sys.path.insert(0, {tools!r})\n"
                "import gen_hooks\n"
                "\n"
                "\n"
                "def _refuse_symlink(*args, **kwargs):\n"
                "    raise OSError('symlink construction unavailable (self-test fault)')\n"
                "\n"
                "\n"
                "os.symlink = _refuse_symlink\n"
                "sys.exit(gen_hooks.self_test_main(_nested=True))\n".format(tools=tools_dir),
                # Under a regression the child may itself wait out a FIFO grandchild's full
                # _CHILD_TIMEOUT, so its own bound is longer: it then REPORTS that timeout and exits;
                # if the child instead overruns this longer bound, _run_bounded kills its whole
                # process group (where one was created), so a blocked grandchild dies with it
                # instead of being orphaned.
                timeout=_CHILD_TIMEOUT * 2 + 30)
            if code is not None and (code == 0 or "NOT RUN" not in out or "a symlink" not in out):
                failures.append("an unplantable symlink fixture must fail the self-test with a named "
                                "NOT RUN failure, got exit {!r}".format(code))

            # 23. The bad-marker handler's NARROWNESS, red on widening: an ORDINARY write failure
            #     inside a planter (the symlink planter's real-marker.toml write) must propagate as
            #     itself, never be dressed as a NOT RUN unplantable-fixture diagnosis, because only a
            #     construction step may claim the runner lacks filesystem support. Proven in a child
            #     that makes exactly that write raise OSError, then calls self_test_main(_nested=True):
            #     the fault must surface verbatim and the unplantable-fixture wording must be absent,
            #     so widening the handler back to OSError goes red here.
            code, out = _run_bounded(
                "planter write-failure propagation (case 23)",
                "import pathlib\n"
                "import sys\n"
                "sys.path.insert(0, {tools!r})\n"
                "import gen_hooks\n"
                "_real_write_text = pathlib.Path.write_text\n"
                "\n"
                "\n"
                "def _failing_write_text(self, *args, **kwargs):\n"
                "    if self.name == 'real-marker.toml':\n"
                "        raise OSError('planter write failure (self-test fault)')\n"
                "    return _real_write_text(self, *args, **kwargs)\n"
                "\n"
                "\n"
                "pathlib.Path.write_text = _failing_write_text\n"
                "sys.exit(gen_hooks.self_test_main(_nested=True))\n".format(tools=tools_dir),
                timeout=_CHILD_TIMEOUT * 2 + 30)
            if code is not None and (code == 0 or "planter write failure (self-test fault)" not in out
                                     or "could not be planted" in out):
                failures.append("an ordinary write failure in a planter must propagate as itself, "
                                "never as an unplantable-fixture NOT RUN diagnosis, got exit {!r}"
                                .format(code))

            # 24. Process-group containment, red on losing it: a shortened-bound parent starts a
            #     grandchild in the SAME process group; the grandchild is confirmed started (its
            #     beacon exists before the parent reports ready, and the parent then outlives
            #     its bound); the timeout kill fires, and the grandchild must be gone (killed
            #     with the group; a not-yet-collected zombie counts) and the child TMPDIR
            #     removed. This goes red with group creation disabled (group = False) and with
            #     the os.killpg call removed: both leave the grandchild alive and the kill
            #     message naming only the direct child. Where the platform has no os.killpg at
            #     all there is no group kill to hold, so the case is recorded as skipped (the
            #     PASS NOTE names it), never silently dropped. Cases 24 to 30 run only at top
            #     level, like cases 22 and 23.
            if not hasattr(os, "killpg"):
                skipped.append("24 no-os.killpg-on-this-platform")
            else:
                # Timing bounds, stated and separated: startup admission is the parent
                # fixture waiting up to 20s for the grandchild beacon, cut short by the 6s
                # child bound (a slow startup is the named never-confirmed-started failure
                # below, never a false pass); the kill cleanup is bounded by the
                # _REAP_DRAIN_BOUND drain plus the _REAP_WAIT_BOUND wait; and both fixtures
                # naturally sleep 40s, far above every bound here, so a natural expiry can
                # never be mistaken for the kill.
                _case24_ran = True
                base24 = tmp / "case24"
                base24.mkdir()
                gc_code = ("import os\n"
                           "import sys\n"
                           "import time\n"
                           "tmp_path = sys.argv[1] + \"/grandchild.pid.tmp\"\n"
                           "with open(tmp_path, \"w\") as fh:\n"
                           "    fh.write(str(os.getpid()))\n"
                           "os.rename(tmp_path, sys.argv[1] + \"/grandchild.pid\")\n"
                           "time.sleep(40)\n")
                code, out = _run_bounded(
                    "process-group containment (case 24)",
                    "import os\n"
                    "import subprocess\n"
                    "import sys\n"
                    "import time\n"
                    "base = {base!r}\n"
                    "subprocess.Popen([sys.executable, \"-I\", \"-c\", {gc!r}, base])\n"
                    "deadline = time.monotonic() + 20\n"
                    "while time.monotonic() < deadline and not os.path.exists(base + \"/grandchild.pid\"):\n"
                    "    time.sleep(0.05)\n"
                    "tmp_path = base + \"/parent-ready.tmp\"\n"
                    "with open(tmp_path, \"w\") as fh:\n"
                    "    fh.write(os.environ.get(\"TMPDIR\", \"\"))\n"
                    "os.rename(tmp_path, base + \"/parent-ready\")\n"
                    "time.sleep(40)\n".format(base=str(base24), gc=gc_code),
                    timeout=6, timeout_is_failure=False)
                if code is not None or "killed with its whole process group" not in out:
                    failures.append("case 24: expected a timeout kill of the whole process "
                                    "group, got exit {!r} ({!r})".format(code, out))
                gc_pid_file = base24 / "grandchild.pid"
                if not gc_pid_file.is_file() or not (base24 / "parent-ready").is_file():
                    failures.append("case 24: the grandchild was never confirmed started "
                                    "before the bound fired")
                else:
                    gc_pid = int(gc_pid_file.read_text(encoding="utf-8"))
                    if not _wait_pid_gone(gc_pid, 10):
                        failures.append("case 24: the grandchild (pid {}) survived the timeout "
                                        "kill; the whole process group must die with the "
                                        "bounded child".format(gc_pid))
                    child_tmpdir = (base24 / "parent-ready").read_text(encoding="utf-8")
                    if not child_tmpdir or not _confirmed_absent(child_tmpdir, "case 24"):
                        failures.append("case 24: the child TMPDIR {!r} was not removed (or "
                                        "its absence could not be confirmed) after the "
                                        "timeout kill".format(child_tmpdir))

            def _communicate_fault_case(case_no, injected):
                # Inject a fault into the FIRST communicate call of one bounded child, once
                # the child has written its beacon AND is confirmed alive at the injection
                # point, then hold that _run_bounded killed and REAPED the child (its exit
                # status collected into Popen.returncode: gone-or-zombie alone is not a
                # reap), removed the child TMPDIR and re-raised the SAME exception. Timing
                # bounds, stated and separated: startup admission is the beacon wait (at
                # most 20s, inside the injection hook, BEFORE the fault fires, so a slow
                # start never eats the cleanup budget); the cleanup is measured FROM THE
                # INJECTION to the re-raise and must finish within _REAP_DRAIN_BOUND plus
                # _REAP_WAIT_BOUND plus 5 seconds; and the fixture naturally sleeps 40s,
                # above every bound here, so a natural expiry can never pass as a kill.
                fixture_sleep = 40
                base = tmp / "case{}".format(case_no)
                base.mkdir()
                beacon = base / "child.pid"
                real_communicate = subprocess.Popen.communicate
                state = {"armed": True, "proc": None, "injected_at": None,
                         "alive_at_injection": False}

                def _faulting_communicate(self, *args, **kwargs):
                    if state["armed"]:
                        state["armed"] = False
                        state["proc"] = self
                        deadline = time.monotonic() + 20
                        while time.monotonic() < deadline and not beacon.is_file():
                            time.sleep(0.05)
                        state["alive_at_injection"] = not _pid_gone(self.pid)
                        state["injected_at"] = time.monotonic()
                        raise injected
                    return real_communicate(self, *args, **kwargs)

                subprocess.Popen.communicate = _faulting_communicate
                try:
                    code, _out = _run_bounded(
                        "communicate fault (case {})".format(case_no),
                        "import os\n"
                        "import sys\n"
                        "import time\n"
                        "base = {base!r}\n"
                        "tmp_path = base + \"/child.pid.tmp\"\n"
                        "with open(tmp_path, \"w\") as fh:\n"
                        "    fh.write(str(os.getpid()) + \" \" + os.environ.get(\"TMPDIR\", \"\"))\n"
                        "os.rename(tmp_path, base + \"/child.pid\")\n"
                        "time.sleep({sleep})\n".format(base=str(base),
                                                        sleep=fixture_sleep),
                        timeout=30)
                    failures.append("case {}: the injected {} must re-raise out of "
                                    "_run_bounded, got exit {!r}".format(
                                        case_no, type(injected).__name__, code))
                    return
                except BaseException as exc:
                    if exc is not injected:
                        raise
                finally:
                    subprocess.Popen.communicate = real_communicate
                if state["injected_at"] is None:
                    failures.append("case {}: the fault was never injected".format(case_no))
                    return
                cleanup_bound = _REAP_DRAIN_BOUND + _REAP_WAIT_BOUND + 5
                if cleanup_bound >= fixture_sleep:
                    failures.append("case {}: the {}s cleanup allowance must stay UNDER "
                                    "the fixture's {}s sleep, or a natural expiry could "
                                    "pass as the kill".format(case_no, cleanup_bound,
                                                              fixture_sleep))
                elapsed = time.monotonic() - state["injected_at"]
                if elapsed > cleanup_bound:
                    failures.append("case {}: kill, reap and cleanup after the injected {} "
                                    "took {:.1f}s measured from the injection, over the "
                                    "stated {}s cleanup bound; the exception path must stay "
                                    "bounded, never an unbounded wait".format(
                                        case_no, type(injected).__name__, elapsed,
                                        cleanup_bound))
                if not state["alive_at_injection"]:
                    failures.append("case {}: the child was not alive at the injection "
                                    "point, so the fault did not fire while the child was "
                                    "alive".format(case_no))
                if state["proc"] is None or state["proc"].returncode is None:
                    failures.append("case {}: the exit status of the child was never "
                                    "collected (Popen.returncode is still None); gone or "
                                    "zombie alone is not a reap".format(case_no))
                if not beacon.is_file():
                    failures.append("case {}: the child was never confirmed alive before the "
                                    "injected fault".format(case_no))
                    return
                pid_text, _, child_tmpdir = beacon.read_text(
                    encoding="utf-8").partition(" ")
                if not _wait_pid_gone(int(pid_text), 10):
                    failures.append("case {}: the child (pid {}) survived the injected {}; "
                                    "the exception path must kill and reap it".format(
                                        case_no, pid_text, type(injected).__name__))
                if not child_tmpdir or not _confirmed_absent(
                        child_tmpdir, "case {}".format(case_no)):
                    failures.append("case {}: the child TMPDIR {!r} was not removed (or its "
                                    "absence could not be confirmed) on the injected-{} path"
                                    .format(case_no, child_tmpdir,
                                            type(injected).__name__))

            # 25. An injected communicate OSError while the child is alive: _run_bounded must
            #     kill and reap the child, remove its TMPDIR and re-raise, within seconds.
            _communicate_fault_case(
                25, OSError("injected communicate failure (self-test fault)"))
            # 26. The same discipline for an injected KeyboardInterrupt while the child is
            #     alive (the old Popen context manager left the child and its TMPDIR behind).
            _communicate_fault_case(
                26, KeyboardInterrupt("injected interrupt (self-test fault)"))

            # 27. Secondary failures inside the staged cleanup never replace the original
            #     exception: each vector injects the ORIGINAL communicate OSError plus
            #     secondary faults TARGETING ONE failure mode (the non-drain vectors also
            #     inject a drain exhaustion to reach their stage, and the kill-error and
            #     exhausted-reap vectors alter kill and wait behavior as auxiliaries, so
            #     "one fault" names the TARGET, not the number of injections), then holds
            #     that the original re-raised, the targeted fault is a NAMED entry, the
            #     child was reaped (or its unreaped state named) and the child TMPDIR
            #     removed (the interrupt-removal vector's leftover is BY DESIGN and the
            #     case removes it itself). The injected entries are then removed from the
            #     failure list: the vector proves they were produced; they are not real
            #     failures. Every vector PINS both reap bounds against the literal
            #     contract (timeout=10 for the drain and for each wait, spelled as
            #     literals, never read from the implementation's constants), snapshots
            #     returncode and pipe state AT THE RE-RAISE (before the case's own
            #     rescue), and the close vectors hold that a failing close never skips
            #     the sibling close. The kill-error and exhausted-reap vectors leave the
            #     real child alive or unreaped BY DESIGN; the case collects it before
            #     returning, so nothing outlives the case.
            def _secondary_fault_case(kind):
                label27 = "case 27 ({})".format(kind)
                base = tmp / "case27-{}".format(kind)
                base.mkdir()
                beacon = base / "child.pid"
                original = OSError("injected communicate failure (self-test fault)")
                real_communicate = subprocess.Popen.communicate
                real_kill = subprocess.Popen.kill
                real_wait = subprocess.Popen.wait
                real_killpg = getattr(os, "killpg", None)
                real_rmtree27 = shutil.rmtree
                state = {"stage": "first", "proc": None,
                         "drain_timeout": "drain-never-called",
                         "wait_timeouts": [], "close_attempts": [],
                         "returncode_at_reraise": "never-snapshotted",
                         "stdout_closed_at_reraise": None,
                         "stderr_closed_at_reraise": None,
                         "removal_interrupted": False,
                         "real_stdout": None, "real_stderr": None}

                class _FailingClose:
                    def __init__(self, which):
                        self.which = which

                    def close(self):
                        state["close_attempts"].append(self.which)
                        raise OSError("injected close failure (self-test fault)")

                def _fault_communicate(self, *args, **kwargs):
                    if state["stage"] == "first":
                        state["stage"] = "drain"
                        state["proc"] = self
                        deadline = time.monotonic() + 20
                        while time.monotonic() < deadline and not beacon.is_file():
                            time.sleep(0.05)
                        if kind == "close-stdout":
                            state["real_stdout"], self.stdout = (
                                self.stdout, _FailingClose("stdout"))
                        if kind == "close-stderr":
                            state["real_stderr"], self.stderr = (
                                self.stderr, _FailingClose("stderr"))
                        raise original
                    state["drain_timeout"] = kwargs.get("timeout", "no-timeout-argument")
                    if kind == "drain-error":
                        raise OSError("injected drain failure (self-test fault)")
                    raise subprocess.TimeoutExpired(
                        cmd="injected drain exhaustion (self-test fault)", timeout=0)

                def _fault_kill(self):
                    # Refusing the kill (and, for the exhausted reap, the re-kill: a real
                    # kill would reap the zombie through its poll) keeps the vector pure.
                    if kind in ("kill-error", "reap-exhausted") and self is state["proc"]:
                        raise OSError("injected kill failure (self-test fault)")
                    return real_kill(self)

                def _fault_wait(self, *args, **kwargs):
                    if self is state["proc"]:
                        # Record the bound EVERY wait carries, so an unbounded wait (no
                        # timeout argument at all) goes red below.
                        if "timeout" in kwargs:
                            state["wait_timeouts"].append(kwargs["timeout"])
                        elif args:
                            state["wait_timeouts"].append(args[0])
                        else:
                            state["wait_timeouts"].append("no-timeout-argument")
                    if kind in ("kill-error", "reap-exhausted") and self is state["proc"]:
                        raise subprocess.TimeoutExpired(
                            cmd="injected wait exhaustion (self-test fault)", timeout=0)
                    return real_wait(self, *args, **kwargs)

                def _fault_killpg(pgid, sig):
                    if kind == "kill-error":
                        raise OSError("injected group-kill failure (self-test fault)")
                    if kind == "interrupt-group-kill":
                        raise KeyboardInterrupt(
                            "injected group-kill interrupt (self-test fault)")
                    return real_killpg(pgid, sig)

                def _fault_rmtree(path, *args, **kwargs):
                    if (kind == "interrupt-removal"
                            and not state["removal_interrupted"]
                            and os.path.basename(str(path)).startswith("bounded-child-")):
                        state["removal_interrupted"] = True
                        raise KeyboardInterrupt(
                            "injected removal interrupt (self-test fault)")
                    return real_rmtree27(path, *args, **kwargs)

                before = len(failures)
                subprocess.Popen.communicate = _fault_communicate
                subprocess.Popen.kill = _fault_kill
                subprocess.Popen.wait = _fault_wait
                shutil.rmtree = _fault_rmtree
                if real_killpg is not None:
                    os.killpg = _fault_killpg
                try:
                    code, _out = _run_bounded(
                        label27,
                        "import os\n"
                        "import sys\n"
                        "import time\n"
                        "base = {base!r}\n"
                        "tmp_path = base + \"/child.pid.tmp\"\n"
                        "with open(tmp_path, \"w\") as fh:\n"
                        "    fh.write(str(os.getpid()) + \" \" + os.environ.get(\"TMPDIR\", \"\"))\n"
                        "os.rename(tmp_path, base + \"/child.pid\")\n"
                        "time.sleep(40)\n".format(base=str(base)),
                        timeout=30)
                    failures.append("case 27 ({}): the original injected OSError must "
                                    "re-raise out of _run_bounded, got exit {!r}"
                                    .format(kind, code))
                    return
                except BaseException as exc:
                    # Snapshots taken AT THE RE-RAISE, before this case's own rescue in
                    # the finally below, so the assertions observe _run_bounded's work,
                    # never the rescue's.
                    snap = state["proc"]
                    state["returncode_at_reraise"] = (
                        None if snap is None else snap.returncode)
                    for pipe_name in ("stdout", "stderr"):
                        pipe = getattr(snap, pipe_name, None) if snap is not None else None
                        state[pipe_name + "_closed_at_reraise"] = getattr(
                            pipe, "closed", None)
                    if exc is not original:
                        raise
                finally:
                    subprocess.Popen.communicate = real_communicate
                    subprocess.Popen.kill = real_kill
                    subprocess.Popen.wait = real_wait
                    shutil.rmtree = real_rmtree27
                    if real_killpg is not None:
                        os.killpg = real_killpg
                    leftover = state["proc"]
                    if leftover is not None and leftover.returncode is None:
                        try:
                            real_kill(leftover)
                        except OSError:
                            pass
                        try:
                            real_wait(leftover, timeout=10)
                        except (subprocess.TimeoutExpired, OSError):
                            pass
                    for saved in (state["real_stdout"], state["real_stderr"]):
                        if saved is not None:
                            saved.close()
                added = failures[before:]
                del failures[before:]
                expected = {
                    "drain-error": ("injected drain failure (self-test fault)",),
                    "kill-error": ("injected kill failure (self-test fault)",
                                   "was not reaped within"),
                    "reap-exhausted": ("was not reaped within",
                                       "injected kill failure (self-test fault)"),
                    "close-stdout": ("injected close failure (self-test fault)",),
                    "close-stderr": ("injected close failure (self-test fault)",),
                    "interrupt-group-kill": (
                        "injected group-kill interrupt (self-test fault)",),
                    "interrupt-removal": (
                        "injected removal interrupt (self-test fault)",),
                }[kind]
                for want in expected:
                    if not any(want in entry for entry in added):
                        failures.append("case 27 ({}): the secondary cleanup failure was "
                                        "not NAMED (no entry containing {!r} among {!r})"
                                        .format(kind, want, added))
                for entry in added:
                    if not any(want in entry for want in expected):
                        failures.append(entry)
                if kind in ("drain-error", "close-stdout", "close-stderr",
                            "interrupt-group-kill", "interrupt-removal") and \
                        state["returncode_at_reraise"] is None:
                    failures.append("case 27 ({}): the exit status of the child was never "
                                    "collected at the re-raise (before this case's own "
                                    "rescue) despite a workable reap path".format(kind))
                # Independent literal expectations: 10 is the pinned CONTRACT for the
                # drain bound and for the wait bound, spelled as literals on purpose
                # (never read from _REAP_DRAIN_BOUND or _REAP_WAIT_BOUND), so retuning
                # either constant, or dropping a wait's timeout argument, goes red here
                # instead of silently retuning this test with it.
                if state["drain_timeout"] != 10:
                    failures.append("case 27 ({}): the pipe drain must carry timeout=10 "
                                    "(the pinned drain bound), got {!r}"
                                    .format(kind, state["drain_timeout"]))
                if not state["wait_timeouts"] or any(
                        timeout_seen != 10 for timeout_seen in state["wait_timeouts"]):
                    failures.append("case 27 ({}): every bounded-reap wait must carry "
                                    "timeout=10 (the pinned wait bound) and at least one "
                                    "wait must run, got {!r}"
                                    .format(kind, state["wait_timeouts"]))
                if kind in ("close-stdout", "close-stderr"):
                    failing = "stdout" if kind == "close-stdout" else "stderr"
                    sibling = "stderr" if kind == "close-stdout" else "stdout"
                    if failing not in state["close_attempts"]:
                        failures.append("case 27 ({}): the failing {} close was never "
                                        "attempted".format(kind, failing))
                    if state[sibling + "_closed_at_reraise"] is not True:
                        failures.append("case 27 ({}): the {} pipe was not closed after "
                                        "the {} close failed; a close failure must never "
                                        "skip the sibling close, got {!r}"
                                        .format(kind, sibling, failing,
                                                state[sibling + "_closed_at_reraise"]))
                if kind == "drain-error" and (
                        state["stdout_closed_at_reraise"] is not True
                        or state["stderr_closed_at_reraise"] is not True):
                    failures.append("case 27 (drain-error): stage 3 must still close both "
                                    "pipes after a stage-2 drain failure, got stdout={!r} "
                                    "stderr={!r}".format(
                                        state["stdout_closed_at_reraise"],
                                        state["stderr_closed_at_reraise"]))
                if not beacon.is_file():
                    failures.append("case 27 ({}): the child was never confirmed alive "
                                    "before the injected fault".format(kind))
                    return
                child_tmpdir = beacon.read_text(encoding="utf-8").partition(" ")[2]
                if kind == "interrupt-removal":
                    # The injected interrupt left the child TMPDIR behind BY DESIGN (the
                    # stage-4 guard named it; no retry ran); this case removes the
                    # leftover itself so nothing outlives it.
                    if not state["removal_interrupted"]:
                        failures.append("case 27 (interrupt-removal): the injected "
                                        "removal interrupt never fired")
                    if child_tmpdir:
                        try:
                            real_rmtree27(child_tmpdir)
                        except FileNotFoundError:
                            pass
                        except OSError as exc:
                            failures.append("case 27 (interrupt-removal): could not "
                                            "remove the leftover child TMPDIR {!r} ({})"
                                            .format(child_tmpdir, exc))
                elif not child_tmpdir or not _confirmed_absent(child_tmpdir, label27):
                    failures.append("case 27 ({}): the child TMPDIR {!r} was not removed "
                                    "(or its absence could not be confirmed)"
                                    .format(kind, child_tmpdir))

            _vector_kinds = ["drain-error", "kill-error", "close-stdout", "close-stderr",
                             "interrupt-removal"]
            if hasattr(os, "killpg"):
                # The exhausted-reap and group-kill-interrupt vectors need the group
                # kill: the first so its injected kill refusal never has to block a REAL
                # stage-1 direct kill, the second because the interruption is injected
                # INTO the group kill itself.
                _vector_kinds.append("reap-exhausted")
                _vector_kinds.append("interrupt-group-kill")
            else:
                skipped.append("27 reap-exhausted no-os.killpg-on-this-platform")
                skipped.append("27 interrupt-group-kill no-os.killpg-on-this-platform")
            for _kind in _vector_kinds:
                _secondary_fault_case(_kind)

            # 28. _rmtree_verified stays inside its own tree and proves absence. Vector (a):
            #     with the FIRST rmtree attempt refused (injected), a directory symlink
            #     inside the tree pointing at an OUTSIDE directory leaves the mode of that
            #     directory unchanged (the repair lstat-checks every entry and unlinks a
            #     symlink instead of chmodding through it) while the tree is still removed
            #     on the retry. Vector (b): with rmtree refused and lstat on the root
            #     refused (injected), the result is a NAMED cannot-verify failure, never a
            #     silent success. Vector (c): with the first rmtree refused and lstat
            #     refused for one INTERIOR entry (the directory symlink), the repair
            #     records a NAMED classification failure and never chmods the entry it
            #     could not classify (a chmod could follow the link): the OUTSIDE mode
            #     stays unchanged and the tree is still removed on the retry. Every fault
            #     is injected, so the vectors run the same under any runner (a
            #     DAC-bypassing root one included).
            base28 = tmp / "case28"
            base28.mkdir()
            outside = base28 / "outside"
            outside.mkdir()
            os.chmod(str(outside), 0o750)
            tree28 = base28 / "tree"
            tree28.mkdir()
            try:
                os.symlink(str(outside), str(tree28 / "link"))
            except OSError as exc:
                failures.append("case 28: NOT RUN: the directory-symlink fixture could not "
                                "be planted ({}); coverage that did not run is a named "
                                "failure".format(exc))
            else:
                real_rmtree = shutil.rmtree
                state28 = {"refusals": 0}

                def _refusing_once_rmtree(path, *args, **kwargs):
                    if state28["refusals"] == 0:
                        state28["refusals"] = 1
                        raise OSError("injected rmtree refusal (self-test fault)")
                    return real_rmtree(path, *args, **kwargs)

                shutil.rmtree = _refusing_once_rmtree
                try:
                    ok28 = _rmtree_verified(tree28, "case 28 symlink confinement")
                finally:
                    shutil.rmtree = real_rmtree
                mode_after = os.lstat(str(outside)).st_mode & 0o7777
                if mode_after != 0o750:
                    failures.append("case 28: the removal repair changed the mode of an "
                                    "OUTSIDE directory through a symlink (0o750 became "
                                    "{:#o}); nothing outside the removed tree may ever be "
                                    "touched".format(mode_after))
                if state28["refusals"] == 0:
                    failures.append("case 28: the injected rmtree refusal never fired")
                if not ok28 or not _confirmed_absent(str(tree28), "case 28"):
                    failures.append("case 28: the tree with an interior symlink was not "
                                    "removed on the retry")

                # Vector (c): entry-level classification refusal.
                base28c = base28 / "entry-refusal"
                base28c.mkdir()
                outside_c = base28c / "outside"
                outside_c.mkdir()
                os.chmod(str(outside_c), 0o750)
                tree28c = base28c / "tree"
                tree28c.mkdir()
                try:
                    os.symlink(str(outside_c), str(tree28c / "link"))
                except OSError as exc:
                    failures.append("case 28: NOT RUN: the entry-refusal symlink fixture "
                                    "could not be planted ({}); coverage that did not run "
                                    "is a named failure".format(exc))
                else:
                    real_lstat_c = os.lstat
                    state28c = {"refusals": 0}

                    def _refusing_once_rmtree_c(path, *args, **kwargs):
                        if state28c["refusals"] == 0:
                            state28c["refusals"] = 1
                            raise OSError("injected rmtree refusal (self-test fault)")
                        return real_rmtree(path, *args, **kwargs)

                    def _refusing_entry_lstat(path, *args, **kwargs):
                        if str(path) == str(tree28c / "link"):
                            raise PermissionError(
                                "injected entry lstat refusal (self-test fault)")
                        return real_lstat_c(path, *args, **kwargs)

                    before28c = len(failures)
                    shutil.rmtree = _refusing_once_rmtree_c
                    os.lstat = _refusing_entry_lstat
                    try:
                        ok28c = _rmtree_verified(tree28c, "case 28 entry refusal")
                    finally:
                        shutil.rmtree = real_rmtree
                        os.lstat = real_lstat_c
                    added28c = failures[before28c:]
                    del failures[before28c:]
                    if not any("could not classify" in entry for entry in added28c):
                        failures.append("case 28: an entry the repair cannot classify "
                                        "(lstat refused) must be a NAMED failure, never "
                                        "a chmod and never a silent pass; got entries "
                                        "{!r}".format(added28c))
                    for entry in added28c:
                        if "could not classify" not in entry:
                            failures.append(entry)
                    mode_after_c = os.lstat(str(outside_c)).st_mode & 0o7777
                    if mode_after_c != 0o750:
                        failures.append("case 28: an entry-level lstat refusal led to a "
                                        "chmod through the symlink (0o750 became {:#o}); "
                                        "an unclassifiable entry must be left alone"
                                        .format(mode_after_c))
                    if state28c["refusals"] == 0:
                        failures.append("case 28: the injected rmtree refusal (entry "
                                        "vector) never fired")
                    if not ok28c or not _confirmed_absent(
                            str(tree28c), "case 28 entry refusal"):
                        failures.append("case 28: the tree was not removed on the retry "
                                        "after the entry-level lstat refusal")

                tree28b = base28 / "tree-b"
                tree28b.mkdir()
                real_lstat = os.lstat

                def _refusing_lstat(path, *args, **kwargs):
                    if str(path) == str(tree28b):
                        raise PermissionError("injected lstat refusal (self-test fault)")
                    return real_lstat(path, *args, **kwargs)

                def _refusing_rmtree(path, *args, **kwargs):
                    raise OSError("injected rmtree refusal (self-test fault)")

                before28 = len(failures)
                shutil.rmtree = _refusing_rmtree
                os.lstat = _refusing_lstat
                try:
                    ok28b = _rmtree_verified(tree28b, "case 28 verification")
                finally:
                    shutil.rmtree = real_rmtree
                    os.lstat = real_lstat
                added28 = failures[before28:]
                del failures[before28:]
                if ok28b or not any("could not be verified" in entry
                                    for entry in added28):
                    failures.append("case 28: an unverifiable removal (lstat refused) must "
                                    "be a NAMED failure, never a silent success; got "
                                    "result={!r} entries={!r}".format(ok28b, added28))
                real_rmtree(str(tree28b))

            # 29. The suite's OWN final cleanup: a fault in the final removal of the
            #     suite temporary tree never replaces an escaping ORIGINAL exception and
            #     never skips the printing of the named failures. A nested child suite is
            #     given an escaping original (run itself raises) plus a final removal
            #     that raises; it must still print the escaping-failure block with the
            #     removal fault NAMED inside it, and the last traceback line must be the
            #     ORIGINAL, never the removal fault.
            code, out = _run_bounded(
                "final-removal fault (case 29)",
                "import os\n"
                "import shutil\n"
                "import sys\n"
                "sys.path.insert(0, {tools!r})\n"
                "import gen_hooks\n"
                "_real_rmtree = shutil.rmtree\n"
                "\n"
                "\n"
                "def _faulting_rmtree(path, *args, **kwargs):\n"
                "    if os.path.basename(str(path)).startswith(\"aiqt-gen-hooks-selftest-\"):\n"
                "        raise RuntimeError(\"injected final-removal fault (self-test fault)\")\n"
                "    return _real_rmtree(path, *args, **kwargs)\n"
                "\n"
                "\n"
                "def _raising_run(*args, **kwargs):\n"
                "    raise RuntimeError(\"injected escaping original (self-test fault)\")\n"
                "\n"
                "\n"
                "shutil.rmtree = _faulting_rmtree\n"
                "gen_hooks.run = _raising_run\n"
                "sys.exit(gen_hooks.self_test_main(_nested=True))\n".format(
                    tools=tools_dir))
            if code is not None and (
                    code == 0
                    or "SELF-TEST FAIL (an exception is escaping" not in out
                    or "injected final-removal fault (self-test fault)" not in out
                    or not out.rstrip().endswith(
                        "injected escaping original (self-test fault)")):
                failures.append("case 29: a fault in the final suite removal must be a "
                                "named failure printed beside the escaping ORIGINAL "
                                "exception, never a replacement of it, got exit {!r} "
                                "(tail {!r})".format(code, out[-400:]))

            # 30. The spawn-to-store window of _run_bounded: an interruption raised at
            #     the HANDLE STORE (after Popen.__init__ returned, before the store
            #     completed) and one raised INSIDE the spawn (at the post-fork errpipe
            #     read) each find the PUBLISHED handle: the staged cleanup kills and
            #     reaps the child, removes its TMPDIR, appends the NAMED
            #     interrupted-spawn entry, re-raises the ORIGINAL interruption, and the
            #     signal mask is restored. Both vectors RAISE the interruption
            #     synchronously, which is exactly what no signal mask can block, so they
            #     hold the publication mechanism; the mask itself is held by recording it
            #     INSIDE the spawn (SIGINT and SIGTERM must be blocked there) and after
            #     the re-raise (restored to the before-spawn mask).
            def _spawn_window_case(kind):
                label30 = "case 30 ({})".format(kind)
                injected30 = KeyboardInterrupt(
                    "injected spawn interruption (self-test fault)")
                real_init = subprocess.Popen.__init__
                real_read = os.read
                state30 = {"armed": True, "proc": None, "mask_in_spawn": None,
                           "read_fired": False, "gone_after": None}

                def _interrupting_read(fd, size):
                    # The first os.read after the fork is the spawn's errpipe read.
                    os.read = real_read
                    state30["read_fired"] = True
                    raise injected30

                def _recording_init(self, *args, **kwargs):
                    if not state30["armed"]:
                        return real_init(self, *args, **kwargs)
                    state30["armed"] = False
                    state30["proc"] = self
                    if hasattr(signal, "pthread_sigmask"):
                        state30["mask_in_spawn"] = signal.pthread_sigmask(
                            signal.SIG_BLOCK, set())
                    if kind == "post-fork-read":
                        os.read = _interrupting_read
                        try:
                            return real_init(self, *args, **kwargs)
                        finally:
                            os.read = real_read
                    real_init(self, *args, **kwargs)
                    raise injected30

                mask_before = (signal.pthread_sigmask(signal.SIG_BLOCK, set())
                               if hasattr(signal, "pthread_sigmask") else None)
                entries_before = set(os.listdir(str(tmp)))
                before = len(failures)
                subprocess.Popen.__init__ = _recording_init
                try:
                    code, _out = _run_bounded(
                        label30,
                        "import time\n"
                        "time.sleep(40)\n",
                        timeout=30)
                    failures.append("case 30 ({}): the injected interruption must "
                                    "re-raise out of _run_bounded, got exit {!r}"
                                    .format(kind, code))
                    return
                except BaseException as exc:
                    if exc is not injected30:
                        raise
                finally:
                    subprocess.Popen.__init__ = real_init
                    os.read = real_read
                    leftover = state30["proc"]
                    leftover_pid = (getattr(leftover, "pid", None)
                                    if leftover is not None else None)
                    if leftover_pid is not None:
                        # Observed BEFORE the rescue below, so the survival assertion
                        # sees _run_bounded's work, never the rescue's.
                        state30["gone_after"] = _wait_pid_gone(leftover_pid, 10)
                        if not state30["gone_after"]:
                            # The rescue of this case: on regression the child survives;
                            # it is collected HERE so nothing outlives the case.
                            try:
                                os.kill(leftover_pid, signal.SIGKILL)
                            except OSError:
                                pass
                            try:
                                os.waitpid(leftover_pid, 0)
                            except (ChildProcessError, OSError):
                                pass
                added = failures[before:]
                del failures[before:]
                expected30 = "interrupted between the fork and the handle store"
                if not any(expected30 in entry for entry in added):
                    failures.append("case 30 ({}): the interrupted spawn was not NAMED "
                                    "(no entry containing {!r} among {!r})"
                                    .format(kind, expected30, added))
                for entry in added:
                    if expected30 not in entry and "cleanup stage 2" not in entry:
                        failures.append(entry)
                if state30["proc"] is None:
                    failures.append("case 30 ({}): the spawn was never observed"
                                    .format(kind))
                    return
                if kind == "post-fork-read" and not state30["read_fired"]:
                    failures.append("case 30 (post-fork-read): the injected read "
                                    "interruption never fired")
                if getattr(state30["proc"], "pid", None) is None:
                    failures.append("case 30 ({}): the published handle never recorded "
                                    "the pid".format(kind))
                elif state30["gone_after"] is not True:
                    failures.append("case 30 ({}): the child (pid {}) survived the "
                                    "interruption inside the spawn window; the published "
                                    "handle must let the staged cleanup kill and reap it"
                                    .format(kind, state30["proc"].pid))
                leftover_dirs = sorted(
                    entry for entry in set(os.listdir(str(tmp))) - entries_before
                    if entry.startswith("bounded-child-"))
                if leftover_dirs:
                    failures.append("case 30 ({}): the child TMPDIR was not removed on "
                                    "the interrupted-spawn path ({!r})"
                                    .format(kind, leftover_dirs))
                if hasattr(signal, "pthread_sigmask"):
                    mask = state30["mask_in_spawn"]
                    if (mask is None or signal.SIGINT not in mask
                            or signal.SIGTERM not in mask):
                        failures.append("case 30 ({}): SIGINT and SIGTERM must be "
                                        "blocked inside the spawn-to-store window, "
                                        "observed {!r}".format(kind, mask))
                    mask_after = signal.pthread_sigmask(signal.SIG_BLOCK, set())
                    if mask_after != mask_before:
                        failures.append("case 30 ({}): the signal mask was not restored "
                                        "after the interrupted spawn (before {!r}, "
                                        "after {!r})".format(kind, mask_before,
                                                             mask_after))

            if not hasattr(signal, "pthread_sigmask"):
                # The publication vectors still run; only the mask observations are
                # unavailable, and the PASS line drops the mask clause below.
                skipped.append("30 signal-mask no-pthread_sigmask-on-this-platform")
            for _kind30 in ("handle-store", "post-fork-read"):
                _spawn_window_case(_kind30)
    finally:
        try:
            _rmtree_verified(tmp, "suite temp cleanup")
        except BaseException as exc:
            # An OSError inside is named by the helper itself; ANYTHING else out of it
            # (an interruption included) is named here, so a fault in the final removal
            # never replaces an escaping original exception and never skips the printing
            # below (case 29 holds this).
            failures.append("suite temp cleanup: the final removal raised {!r}; a "
                            "removal fault is a named failure that never replaces the "
                            "escaping original or skips the printing of the named "
                            "failures".format(exc))
        try:
            if failures and sys.exc_info()[0] is not None:
                # An exception is escaping the suite (a re-raised original out of
                # _run_bounded that no case expected, for example): the normal printing
                # below would never run, so the NAMED failure entries (the staged-cleanup
                # and suite-cleanup ones included) are printed here, to stderr beside the
                # traceback, before it propagates. Named cleanup failures survive the
                # exit paths THIS SUITE controls; they do NOT survive an outer SIGTERM or
                # SIGKILL of the suite itself, nor a failure of this printing itself
                # (both guards in this finally keep the ORIGINAL exception propagating
                # instead of replacing it).
                print("SELF-TEST FAIL (an exception is escaping; the named failures so far):",
                      file=sys.stderr)
                for failure in failures:
                    print("  - " + failure, file=sys.stderr)
        except BaseException:
            pass

    if failures:
        print("SELF-TEST FAIL:")
        for failure in failures:
            print("  - " + failure)
        return 1
    note = ("" if not skipped else
            " NOTE: skipped {} case(s) the runner cannot exercise (a chmod-0 path the runner can still "
            "read, a DAC bypass such as root, or a platform with no os.killpg, which no fixture can "
            "construct around): {}"
            .format(len(skipped), ", ".join(skipped)))
    # Each proof clause is claimed ONLY when the checks behind it (cases 22 to 30) actually
    # ran: a nested suite skips them all, so its PASS line claims none of their proof, and a
    # runner with no os.killpg skips case 24, so the group-kill sentence is dropped there too
    # (the NOTE above names the skip). The wording states what the suite itself RAN; that
    # each guarded regression goes red was shown by mutation runs outside this suite, so the
    # PASS line claims no revert was executed here.
    group_proof = ("a timed-out child is killed with its whole process group (a started "
                   "grandchild included) and its TMPDIR verified removed, "
                   if _case24_ran else "")
    mask_proof = ("" if _nested or not hasattr(signal, "pthread_sigmask") else
                  " with the spawn-window signal mask restored")
    proof = ("" if _nested else
             "; a child with symlink construction refused fails with the named NOT RUN entry, an "
             "ordinary write failure in a planter propagates as itself, " + group_proof +
             "an injected communicate OSError and KeyboardInterrupt each kill, REAP (exit "
             "status collected) and clean up the live child before re-raising, a secondary "
             "cleanup failure is a named entry that never replaces the original exception, "
             "an interruption injected inside the spawn or at the handle store finds the "
             "published handle (child killed and reaped, TMPDIR removed, the interruption "
             "re-raised" + mask_proof + "), a fault in the final suite removal is printed "
             "beside the escaping original instead of replacing it, "
             "and the removal helper neither follows a symlink out of its tree, nor chmods "
             "an entry it cannot classify (a named failure instead), nor counts "
             "an unverifiable absence as removed")
    print("SELF-TEST PASS: a conformant manifest generates and regenerates drift-clean; every rendered "
          "launcher runs isolated (args[0] == '-I'); hooks.json "
          "drift and a plugin hooks/ orphan fail the check; an unknown corpus-id, an empty residue, "
          "an unreadable source tree, a bad/unknown event, an uncompilable matcher, a handler not in "
          "the script, a missing handler script, an unreadable manifest, and a malformed default keyword "
          "all fail closed; the full 'warn' default policy holds across every known event (rejected on "
          "every non-warn event, accepted on every WARN_EVENTS event, plus warn+stage=bake on "
          "PreToolUse and the SessionStart-cannot-block overclaim); a Stop hook renders "
          "byte-identical hooks.json whether its default is 'block' or 'warn'; and the repository-local "
          ".claude/settings.json renders one isolated PreToolUse group with the manifest matcher, is "
          "drift-checked by run(), stays out of build_desired(), and fails closed without its control, "
          "in the authoring tree only: an adopter-shaped tree never gets, loses or changes a "
          ".claude/settings.json whatever the cwd, and a malformed authoring marker (a FIFO, an empty, a non-UTF-8 "
          "and a one-byte-oversize one included) fails closed while one of exactly the size bound is accepted; "
          "a marker fixture that cannot be planted is a named NOT RUN failure" + proof + ", and the "
          "FIFO case is time-bounded so a blocking regression is a reported failure, never a hang" + note)
    return 0


if __name__ == "__main__":
    sys.exit(main())
