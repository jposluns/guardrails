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

Per-hook timeout and derived onFailure, OFF by default. A [[hook]] may author an optional integer
`timeout` (TIMEOUT_MIN to TIMEOUT_MAX seconds), and the [plugin] table may author
`claude-code-min-version` plus the render switch `on-failure-render`. While the switch is false or
absent (this repository's current setting), NEITHER an authored per-hook timeout NOR any onFailure key
is rendered, so the emitted hooks.json stays byte-identical to the pre-switch output. The switch ships
off because how a client older than 2.1.295 treats the unknown onFailure key in hooks.json is
unverified (it could ignore the key, warn, reject that entry, or reject the whole file, and the last
would silently disable every hook, a fail-open regression). With the switch on, every launcher renders
its configured timeout (the authored value, or TIMEOUT where none is authored), and exactly the
enforce-stage PreToolUse hooks whose default is "block" also render "onFailure": "block"; a warn hook
never carries onFailure, and no Stop, TeammateIdle or other non-PreToolUse hook ever carries it. With
the switch on, an authored timeout at or below the dispatcher's PreToolUse budget plus the margin its
deadline line reserves (PRETOOL_BUDGET_SECONDS + (HOOK_TIMEOUT_SECONDS - PRETOOL_BUDGET_SECONDS), both
read from the dispatcher, which is TIMEOUT because check_deadline holds the mirror to it) fails closed
(exit 2, naming the hook and the minimum), for a hook of any event, default or stage, so every
rendered AUTHORED timeout leaves the dispatcher more time past its budget than the margin its own
deadline design reserves. A hook that authors no timeout is the exception: it renders TIMEOUT, which is
exactly the budget plus the margin, the value an author may not write. Refusing an authored value above
the budget but not above budget plus margin (9 or 10 seconds today) is a chosen stricter policy, not a
mechanical need: today's 8-second budget is at least 1 second below those timeouts, no less than the
DEADLINE_MIN_MARGIN slack check_deadline accepts for the default timeout, so a PreToolUse hook can still
fail closed at the dispatcher's own deadline. While the switch is off an authored timeout is not
rendered, so only the TIMEOUT_MIN to TIMEOUT_MAX range applies.
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
import math
import os
import re
import stat
import tomllib
from fractions import Fraction
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
# Strict MAJOR.MINOR.PATCH, matched with .match so the string must hold the version and nothing else:
# [0-9] (never \d, which also matches other scripts' digits), no leading zero in a component, and \Z
# (never $, which also matches just before a trailing newline).
SEMVER_RE = re.compile(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\Z")
ID_RE = re.compile(r"^[a-z][a-z0-9-]*$")     # a stable kebab-case control id
CID_RE = re.compile(r"^[a-z0-9]{6,}$")        # a corpus-id (matches gen_rules' own shape)
HANDLER_RE = re.compile(r"^[a-z][a-z0-9_]*$")
TIMEOUT = 10  # default seconds the platform gives each hook command; while on-failure-render is off,
# the one source of every hook's deadline (an authored per-hook timeout renders only with the switch on)
TIMEOUT_MIN, TIMEOUT_MAX = 1, 600  # the accepted range of an authored per-hook timeout, in seconds
# (with on-failure-render on, check_authored_timeouts also refuses one at or below the dispatcher's
# PreToolUse budget plus its margin)
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
# either name in the script. It returns that margin, from which check_authored_timeouts derives the
# floor an authored timeout must clear with the switch on (no second literal for it here).
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
    another form, and no copy of the line inside a string, stands in for it. Returns the margin as an exact
    Fraction of its decimal text (the range test above keeps its float comparison unchanged), for
    check_authored_timeouts."""
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
    return Fraction(margins[0].group(1))


def check_authored_timeouts(hooks, margin, name):
    """Fail closed (ValueError) when, with the on-failure-render switch on, a hook authors a timeout at or
    below the dispatcher's PreToolUse budget plus its margin. margin is the dispatcher's own margin as
    check_deadline returns it, so its budget is PRETOOL_BUDGET_SECONDS = HOOK_TIMEOUT_SECONDS - margin
    (HOOK_TIMEOUT_SECONDS equals TIMEOUT, which check_deadline enforces). An authored timeout at or below
    that budget plus the same margin is refused, so an accepted AUTHORED timeout leaves the hook more time
    past its budget than the slack the dispatcher's own deadline line reserves. budget + margin is
    HOOK_TIMEOUT_SECONDS by construction, so authoring the default itself is refused too, while a hook
    that authors no timeout is not checked and renders TIMEOUT, exactly the budget plus the margin: the
    guarantee covers authored timeouts only. Refusing a value above the budget but not above budget plus
    margin (9 or 10 seconds today) is a chosen stricter policy, not a mechanical need: today's 8-second
    budget is at least 1 second below those timeouts, no less than the DEADLINE_MIN_MARGIN slack
    check_deadline accepts for the default timeout, so a PreToolUse hook can still fail closed at the
    dispatcher's own deadline. The rule covers a
    hook of any event, default or stage, the stricter reading: with the switch on every launcher renders
    its configured timeout (the authored value where one is authored), and a hook stopped by the platform
    loses any decision it has not yet written. The arithmetic is exact
    (Fraction), so no float rounding can move the boundary. The caller runs this only with the switch on;
    while it is off no authored timeout renders, and only load_manifest's TIMEOUT_MIN to TIMEOUT_MAX
    range applies."""
    budget = TIMEOUT - margin
    limit = budget + margin
    minimum = math.floor(limit) + 1
    for hook in hooks:
        if "timeout" in hook and hook["timeout"] <= limit:
            raise ValueError("{}: [[hook]] {}: timeout {} is at or below the dispatcher's PreToolUse budget "
                             "of {:g} seconds plus its {:g}-second margin ({:g} seconds); with "
                             "on-failure-render on, the minimum authored timeout is {}".format(
                                 name, hook["id"], hook["timeout"], float(budget), float(margin),
                                 float(limit), minimum))


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
    "onFailure": "block" (never a warn hook, never any other event). This function renders what it is
    given; build_desired runs check_authored_timeouts first, so the generator never renders an authored
    timeout at or below the dispatcher's budget plus its margin (a hook that authors none renders
    TIMEOUT, which equals that sum)."""
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


def render_project_settings(hooks, ids=PROJECT_HOOK_IDS):
    """The repository-local .claude/settings.json, deterministic and generated whole: the controls named in
    ids, in the render_hooks_json order (events in KNOWN_EVENTS order, entries sorted by id), each with its
    matcher COPIED from the manifest so the project wiring cannot drift from the declared scope. The
    settings form carries a shell-string command, not command + args, so the launcher is spelled
    `python3 -I <script> <handler>` (isolated, as check_python_launcher_isolation requires). Raises
    ValueError when an id names no manifest control, so a renamed control fails closed (exit 2) instead of
    silently dropping the project wiring. This render is OUTSIDE the [plugin] on-failure-render switch:
    every launcher carries the default TIMEOUT and never an onFailure key, whatever the switch or an
    authored per-hook timeout says (both apply only to the plugin hooks.json render)."""
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
        data = tomllib.loads(raw.decode("utf-8"))
    except ValueError as exc:  # UnicodeDecodeError, TOMLDecodeError and an oversized integer
        raise ValueError("{}: the authoring marker is not readable UTF-8 TOML ({})".format(
            AUTHORING_MARKER_REL, exc)) from exc
    except (RecursionError, MemoryError) as exc:
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
    margin = check_deadline(script_text)
    if plugin.get("on-failure-render", False):
        check_authored_timeouts(hooks, margin, manifest_path.name)
    return {HOOKS_JSON_REL: render_hooks_json(hooks, plugin.get("on-failure-render", False)),
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
#      exhausted bounded reap, a close error on either pipe, a non-timeout wait failure, a
#      non-timeout drain error FOLLOWED by a non-timeout wait failure, a close failure whose
#      own __str__ or __repr__ raises, whose render returns a raising str subclass, or whose
#      __str__ raises while its __repr__ returns such a subclass (the repr fallback is
#      normalized too), a leftover-removal error whose own render raises, and a SPAWN failure
#      whose render raises, driven through the real launch diagnostic) never replaces the
#      original exception: the original still re-raises, the secondary failure is a NAMED
#      failure entry (rendered through a guarded helper that returns an exact built-in str,
#      with a constant fallback), the child is reaped (or its unreaped state named) with the
#      child TMPDIR removed, a child whose exit status was already collected is not signalled
#      again by the staged cleanup (the returncode gate NARROWS, not eliminates, the reused-pid
#      hazard), and the case's own rescue of a deliberately-unreaped child is verified
#      STRUCTURALLY: the injected rescue faults are recorded apart from genuine rescue and
#      final-collection failures (recognized by exception identity, never by message text), a
#      genuine rescue or final-collection failure is never discarded by the expected-error
#      filter, each rescue and final-collection kill and wait runs under its own guard
#      against ANY exception (a RuntimeError or KeyboardInterrupt from the final kill
#      included), so the bounded final wait still runs when its kill raises, every rescue and
#      final-collection wait must carry timeout=10, and after the filter a recorded child
#      without a collected returncode is a named failure, so the case can never PASS with a
#      live child,
#  28. _rmtree_verified never follows a symlink out of the tree it removes (a directory symlink
#      inside the tree pointing at an OUTSIDE directory leaves that directory's mode unchanged;
#      the symlink is unlinked, never chmodded through), and treats only a confirmed absence
#      (FileNotFoundError from lstat) as removed: any other verification error is a named
#      failure, never a silent success. A ROOT that is a symlink is never walked, also
#      when its unlink fails: the retry walk requires an lstat-confirmed directory, and an
#      unremovable root link is a named failure with the outside tree untouched.
#      Confinement holds up to the disclosed replacement race between an entry's lstat
#      classification and the chmod or walk that follows (stated at the helper).
#  30. the PreToolUse deadline derives from TIMEOUT: a script whose HOOK_TIMEOUT_SECONDS differs from
#      TIMEOUT, that lacks either deadline line, or that binds either name a second time (in any form, or
#      with the budget line only inside a string) fails closed (exit 2), while reading the budget is
#      accepted, and the margin is accepted from DEADLINE_MIN_MARGIN up to just below TIMEOUT and refused
#      just outside either end. It runs in a nested suite too (outside the `if not _nested` block),
#  31. the optional per-hook `timeout` validates fail-closed: accepted only as an integer from
#      TIMEOUT_MIN to TIMEOUT_MAX (each end tested just inside, exactly at, and just outside), with a
#      bool, a string and a float each refused (exit 2); a non-boolean [plugin] on-failure-render is
#      refused, and the version strings are strict MAJOR.MINOR.PATCH: a claude-code-min-version that is
#      a non-string, misses a component, carries a trailing newline, a leading zero or non-ASCII digits
#      is refused while "0.0.0" is accepted, and a leading-zero `version` is refused through the same
#      regex,
#  32. with on-failure-render false, and separately with the key ABSENT (its own tree, so a regression
#      that treats only an explicit false as off is caught), an authored per-hook timeout and the
#      optional [plugin] keys change NOTHING: hooks.json renders byte-identical to a tree authoring
#      none of them, with no onFailure key anywhere, and that baseline render is itself pinned to
#      spelled-out expected bytes, so a shared rendering regression cannot hide in a both-sides
#      comparison,
#  33. with on-failure-render true, EVERY launcher renders its configured timeout (the authored value,
#      or TIMEOUT where none is authored) and exactly the enforce-stage PreToolUse block hooks also
#      render "onFailure": "block": an implicit-enforce and an explicit-enforce block hook carry it,
#      while a bake-stage PreToolUse block hook, a PreToolUse warn (bake) hook, a Stop block hook and a
#      TeammateIdle block hook each render their authored timeout WITHOUT it (so the stage clause is
#      discriminated independently of the default clause); the contract records key PRESENCE, never
#      get(), so an explicit JSON null cannot pass as an absent key, and it is shown discriminating
#      against a mutant output that drops the key, one that resets every timeout and one that writes
#      the explicit null; each clause of the render predicate (event, default, stage) is also
#      discriminated by direct render-level vectors, including PreToolUse warn + enforce, the
#      combination load_manifest refuses end-to-end,
#  34. the project .claude/settings.json render is OUTSIDE the switch: with on-failure-render true and
#      authored per-hook timeouts it stays byte-identical to a project tree authoring neither (always
#      the default timeout, never an onFailure key), while the same tree's hooks.json shows the switch
#      applied, so the case cannot pass with a switch that silently failed to apply,
#  35. with on-failure-render true, an authored timeout at or below the dispatcher's PreToolUse budget
#      plus the margin its deadline line reserves fails closed (exit 2, the refusal naming the hook and
#      the minimum), tested just below, exactly at and just above the boundary on a PreToolUse and a
#      Stop hook, and with a stub margin of 3.5 (the boundary holds and the refusal names the budget and
#      margin read from the stub); a warn hook, a bake-stage block hook and a second hook are each
#      refused just below it and generate just above it, so the rule cannot narrow to the onFailure
#      predicate or to the first hook; with the switch false or absent the below, at and above values
#      still generate and render the default timeout.
# Every vector that removes the entries its own faults caused (cases 20b, 21b, 27 and 28)
# does so by PROVENANCE, never by message text: each named entry from the staged cleanup,
# the removal and absence helpers and the launch diagnostic carries its stage and the
# exception object that originated it, each injected fault is recorded as it fires, and an
# entry is removed ONLY when it was recorded at a stage the vector declares AND its origin
# IS one of those recorded objects. A genuine failure whose text carries an injected token
# stays a failure (case 27's genuine-token vector and case 28's vector (e) hold this).

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
# An interruption of the suite DURING a spawn, before the child handle is stored, is the
# same residual shape, with one sharpening: ONLY where the platform has os.killpg does a
# TOP-LEVEL child run in its OWN session and process group, so a kill of this suite's
# process group, or a terminal interrupt, does NOT reach it; it survives until it exits by
# itself (a BLOCKED child need never exit) or until a supervisor signals its session or
# its pid. WITHOUT os.killpg a top-level child shares this suite's session and process
# group, so a group kill or terminal interrupt DOES reach it. A NESTED suite's children
# share their driver's process group, so an outer group kill reaches those. (Disclosed at
# _run_bounded; the earlier mask and handle-publication mechanisms for that window were
# removed as unprovable.)


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


def self_test_main(_nested=False):
    # _nested=True is passed ONLY by the code text of the case-22, case-23 and case-29 child
    # drivers, so a nested suite skips cases 22 to 29 instead of recursing; it is an explicit
    # internal argument, never an inherited environment variable.
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

    def _exc_text(exc):
        # Render an exception for a diagnostic WITHOUT trusting it: __str__ and
        # __repr__ are exception-authored code and either can raise, which inside a
        # cleanup handler would replace the original exception or skip the later
        # stages. A failing render falls back to the repr, a failing repr to the type
        # name, and a failing type-name render to a constant, so building a diagnostic
        # can never itself fail. The result is ALWAYS an exact built-in str:
        # str.__str__ copies a subclass's character data without calling any of the
        # subclass's own methods, so a render that legally returns a str SUBCLASS
        # whose own __str__ or __format__ raises cannot escape later, when the
        # diagnostic that embeds this text is itself formatted (case 27 holds this
        # with vectors whose __str__ and __repr__ raise, one whose __str__ returns
        # exactly such a subclass, and one whose __str__ raises while its __repr__
        # returns such a subclass, so the str fallback AND the repr fallback are
        # both normalized).
        try:
            return str.__str__(str(exc))
        except BaseException:
            pass
        try:
            return str.__str__(repr(exc))
        except BaseException:
            pass
        try:
            return str.__str__("<unrenderable {} instance>".format(type(exc).__name__))
        except BaseException:
            return "<unrenderable exception>"

    class _Observed(str):
        # A named failure entry that carries its PROVENANCE: the stage that recorded it
        # and the exception object that originated it (None where no exception did). A
        # vector that injects a fault removes an entry ONLY through that provenance
        # (_split_injected below), never by the entry's text, so a genuine failure whose
        # message happens to contain an injected token stays a failure.
        def __new__(cls, text, stage, origin):
            entry = str.__new__(cls, text)
            entry.stage = stage
            entry.origin = origin
            return entry

    def _observe(text, stage, origin=None):
        failures.append(_Observed(text, stage, origin))

    def _split_injected(added, injected, stages):
        # The ONLY way a vector removes entries it caused from the failure list: an entry
        # is SUPPRESSED only when it was recorded at one of the vector's declared stages
        # AND its originating exception IS (identity, never equality or message text) one
        # of the exception objects the vector itself created and recorded when each fired.
        # Every other entry stays a failure whatever its text. Returns (suppressed, kept).
        suppressed = []
        kept = []
        for entry in added:
            origin = getattr(entry, "origin", None)
            if (getattr(entry, "stage", None) in stages and origin is not None
                    and any(origin is fault for fault in injected)):
                suppressed.append(entry)
            else:
                kept.append(entry)
        return suppressed, kept

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
        # restore) and retries once; the repair never follows a symlink: the ROOT is
        # walked only when the verification lstat confirms it is a directory (os.walk
        # FOLLOWS a root symlink even though it does not descend symlinked
        # subdirectories, so a root that is a symlink is unlinked and never walked,
        # also when that unlink fails, which is a named failure), and every interior
        # entry is CLASSIFIED with os.lstat: a symlink is unlinked, only an entry lstat
        # confirms is a directory is chmodded, and an entry lstat CANNOT classify is a
        # NAMED failure left alone, never a chmod, which could follow a link planted
        # where a directory stood. So nothing OUTSIDE this tree is ever touched, up to
        # one stated residual race: between the lstat and the chmod (or the walk) the
        # entry could be REPLACED by a symlink; this suite does not close that window (a
        # directory-descriptor fchmod would, at the cost of platform-specific O_NOFOLLOW
        # and O_DIRECTORY semantics). A tree still present after the retry is a NAMED
        # failure.
        path = str(path)

        def _repair(entry):
            try:
                entry_stat = os.lstat(entry)
            except OSError as exc:
                _observe("{}: removal repair could not classify the entry {} ({}); "
                         "an unclassifiable entry is a named failure left alone, "
                         "never chmodded through a possible symlink"
                         .format(what, entry, _exc_text(exc)), "removal classify", exc)
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
                root_stat = os.lstat(path)
            except FileNotFoundError:
                return True
            except OSError as exc:
                _observe("{}: removal of the temporary tree {} could not be verified "
                         "({}); only a confirmed absence counts as removed, so an "
                         "unverifiable removal is a named failure, never a silent "
                         "success".format(what, path, _exc_text(exc)),
                         "removal verify", exc)
                return False
            if attempt == 0:
                # The retry repair walks ONLY a root this verification lstat confirms
                # is a directory: os.walk FOLLOWS a symlink given as its top argument
                # (it refuses only to descend into symlinked SUBdirectories), so
                # walking a root that is, or has become, a symlink would carry the
                # chmod repair into an OUTSIDE tree. A root classified as a symlink is
                # unlinked and NEVER walked, also when that unlink fails (a named
                # failure); any other non-directory root is left to the retry (nothing
                # to chmod, nothing to walk).
                if stat.S_ISLNK(root_stat.st_mode):
                    try:
                        os.unlink(path)
                    except OSError as exc:
                        _observe("{}: the root {} of the tree to remove is a "
                                 "symlink that could not be unlinked ({}); a "
                                 "root symlink is never walked (os.walk would "
                                 "follow it into an outside tree), so the "
                                 "leftover link is a named failure, never a "
                                 "traversal".format(what, path, _exc_text(exc)),
                                 "removal root unlink", exc)
                        return False
                    continue
                if not stat.S_ISDIR(root_stat.st_mode):
                    continue
                try:
                    os.chmod(path, 0o700)
                except OSError:
                    pass
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
            _observe("{}: could not verify that {!r} is gone ({}); an unverifiable "
                     "absence is a named failure, never a silent success"
                     .format(what, path, _exc_text(exc)), "absence confirm", exc)
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
            _observe("{}: could not verify that {!r} is absent ({}); an "
                     "unverifiable absence is a named failure, never a silent "
                     "success".format(what, path, _exc_text(exc)), "absence verify", exc)
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
        # later stages; only one raised inside a guarded stage is contained. DISCLOSED,
        # not covered, the spawn window too: an interruption delivered DURING the spawn,
        # before the child handle is stored in proc, can leave that child running with
        # no handle for the staged cleanup to kill. ONLY where the platform has
        # os.killpg is a TOP-LEVEL child started in its OWN session and process group
        # (start_new_session is gated on the same group flag below); there a kill of
        # this suite's process group, or a terminal interrupt, does NOT reach it: it
        # survives until it exits by itself (a BLOCKED child need never exit; no
        # lifetime bound is claimed) or until a supervisor signals its session or its
        # pid. WITHOUT os.killpg a top-level child shares this suite's session and
        # process group (start_new_session=False), so a group kill or terminal
        # interrupt DOES reach it. A NESTED
        # suite's children share their driver's process group, so an outer group kill
        # reaches those. Earlier rounds tried to close that window with
        # a signal mask around the spawn and a Popen subclass publishing the handle from
        # inside __init__; each mechanism left a narrower uncovered window of its own
        # (the mask was set before its restoring try was entered, the publication still
        # trailed the fork by a Python-level assignment), so both were REMOVED in favour
        # of this honest disclosure: the suite keeps only what it can prove. Stage 1
        # signals only a child whose returncode is still None, which narrows, not
        # eliminates, the hazard of signalling a pid the kernel may have reused (CPython
        # itself can reap a child and still leave returncode None in rare internal
        # races). Only the group LEADER (the direct
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

        def _cleanup_stages(kill):
            # The staged cleanup, shared by EVERY exit from the guarded region below. Each
            # stage is guarded on its own against ANY exception (an interruption raised
            # inside the stage included): a failing stage becomes a NAMED failure entry and
            # the remaining stages still run, so no secondary failure here can replace an
            # exception propagating through the caller. An interruption delivered BETWEEN
            # two guards still skips the later stages; that gap is disclosed above, not
            # covered. Returns True only when the group kill actually happened, so no
            # caller claims a group kill that did not. Every named entry carries its stage
            # and the exception that originated it (_observe), so case 27 can tell the
            # faults it injected from a genuine failure by identity, never by text.
            pid = getattr(proc, "pid", None)
            group_killed = False
            # Stage 1: kill the whole process group where one was created, otherwise the
            # direct child, and ONLY while the child is unreaped (returncode is None), which
            # NARROWS, not eliminates, the reused-pid hazard (CPython itself can reap a
            # child and still leave returncode None in rare internal races). A kill that itself
            # fails is a NAMED failure.
            if kill and proc is not None and pid is not None and proc.returncode is None:
                if group:
                    try:
                        os.killpg(pid, signal.SIGKILL)
                        group_killed = True
                    except OSError:
                        pass
                    except BaseException as exc:
                        _observe("{}: cleanup stage 1: the group kill of the child "
                                 "(pid {}) failed ({}); a kill failure is a named "
                                 "failure, never an exception replacing the original"
                                 .format(label, pid, _exc_text(exc)),
                                 "stage 1 group kill", exc)
                if not group_killed:
                    try:
                        proc.kill()
                    except BaseException as exc:
                        _observe("{}: cleanup stage 1: the direct kill of the child "
                                 "(pid {}) failed ({}); a kill failure is a named "
                                 "failure, never an exception replacing the original"
                                 .format(label, pid, _exc_text(exc)),
                                 "stage 1 direct kill", exc)
            # Stage 2: bounded reap: drain the pipes for at most _REAP_DRAIN_BOUND seconds,
            # then re-kill and wait at most _REAP_WAIT_BOUND more; a child still unreaped
            # after both bounds is a NAMED failure left to init, never an unbounded wait.
            # The unreaped entry's origin is the exception that ended the bounded wait
            # (None when the wait returned or never ran).
            if kill and proc is not None and pid is not None and proc.returncode is None:
                reap_origin = None
                try:
                    proc.communicate(timeout=_REAP_DRAIN_BOUND)
                except subprocess.TimeoutExpired:
                    try:
                        proc.kill()
                    except BaseException as exc:
                        _observe("{}: cleanup stage 2: the re-kill of the child "
                                 "(pid {}) failed ({}); a re-kill failure is a "
                                 "named failure, never a silent pass"
                                 .format(label, pid, _exc_text(exc)),
                                 "stage 2 re-kill", exc)
                    try:
                        proc.wait(timeout=_REAP_WAIT_BOUND)
                    except subprocess.TimeoutExpired as exc:
                        reap_origin = exc
                    except BaseException as exc:
                        reap_origin = exc
                        _observe("{}: cleanup stage 2: the bounded wait failed "
                                 "({}); a wait failure is a named failure, and "
                                 "the unreaped check below still runs"
                                 .format(label, _exc_text(exc)), "stage 2 wait", exc)
                except BaseException as exc:
                    _observe("{}: cleanup stage 2: the {}s pipe drain failed ({}); a "
                             "drain failure is a named failure, and the bounded wait "
                             "below still collects the child where it can"
                             .format(label, _REAP_DRAIN_BOUND, _exc_text(exc)),
                             "stage 2 drain", exc)
                    try:
                        proc.wait(timeout=_REAP_WAIT_BOUND)
                    except subprocess.TimeoutExpired as exc:
                        reap_origin = exc
                    except BaseException as exc:
                        reap_origin = exc
                        _observe("{}: cleanup stage 2: the bounded wait failed "
                                 "({}); a wait failure is a named failure, and "
                                 "the unreaped check below still runs"
                                 .format(label, _exc_text(exc)), "stage 2 wait", exc)
                if proc.returncode is None:
                    _observe("{}: cleanup stage 2: the child (pid {}) was not reaped "
                             "within the {}s drain and {}s wait bounds; an unreaped "
                             "child is a named failure left to init, never an "
                             "unbounded wait".format(label, pid,
                                                     _REAP_DRAIN_BOUND,
                                                     _REAP_WAIT_BOUND),
                             "stage 2 unreaped", reap_origin)
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
                            _observe("{}: cleanup stage 3: the {} close failed ({}); "
                                     "a close failure is a named failure and the "
                                     "other pipe is still closed"
                                     .format(label, stream_name, _exc_text(exc)),
                                     "stage 3 close", exc)
            # Stage 4: verified removal of the child TMPDIR; an OSError inside is named by
            # the helper itself, and ANY other exception out of it (an interruption
            # included) is named HERE, so the removal can neither replace the original
            # exception nor skip a caller that follows.
            try:
                _rmtree_verified(child_tmp, label)
            except BaseException as exc:
                _observe("{}: cleanup stage 4: the verified removal of the child "
                         "TMPDIR raised {}; a removal fault is a named failure, "
                         "never an exception replacing the original"
                         .format(label, _exc_text(exc)), "stage 4 removal", exc)
            return group_killed

        try:
            # The spawn window is DISCLOSED above, not covered: an interruption landing
            # inside Popen() or at this assignment, before proc holds the handle, leaves
            # a child the staged cleanup cannot kill. Only where the platform has
            # os.killpg is a top-level child in its OWN session and process group,
            # which a kill of this suite's group or a terminal interrupt does not
            # reach: it survives until it exits by itself (a BLOCKED child need never
            # exit) or until a supervisor signals its session or its pid; without
            # os.killpg, and in a nested suite, the child shares its driver's group.
            proc = subprocess.Popen(
                [sys.executable, "-I", "-B", "-c", code_text],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                env=child_env, start_new_session=group)
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
                # The spawn itself failed: proc is None, so the kill, reap and close
                # stages were all skipped (each is gated on a recorded handle; an
                # exec-failed child is reaped INSIDE Popen itself) and only the
                # removal stage ran. The diagnostic renders through _exc_text, so an
                # exception whose own render raises, or returns a raising str
                # subclass, still becomes a named failure.
                _observe("{}: could not launch the bounded child ({})"
                         .format(label, _exc_text(exc)), "launch", exc)
                return None, ""
            raise
        except BaseException:
            # A KeyboardInterrupt or any other non-timeout exception inside the guarded
            # region: the staged cleanup kills and reaps the recorded child (cases 26
            # and 27 exercise this path), then the ORIGINAL exception re-raises. With no
            # recorded handle (the DISCLOSED spawn window above) there is nothing to
            # kill or close (the kill, reap and close stages are all gated on the
            # recorded handle); only the removal stage still runs.
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

        def _check_bare_adopter_absence():
            # The REAL bare-adopter absence check: the armed pass below re-drives
            # the ONE call-site statement in this loop under an injected lstat
            # refusal WHILE the ordinary case runs, so replacing that call with a
            # bare os.path.lexists presence check goes red (the named cannot-verify
            # failure never appears), not only a helper revert.
            _require_absent(bare / ".claude", "bare adopter tree",
                            "an adopter run must never create .claude/ or "
                            ".claude/settings.json")

        real_lstat_abs = os.lstat
        # Every injected refusal is recorded as it fires, so the armed pass removes ONLY
        # the entries those exact exception objects originated (_split_injected).
        injected_abs = []

        def _refusing_bare_lstat(path, *args, **kwargs):
            if str(path) == str(bare / ".claude"):
                fault = PermissionError("injected absence-check refusal (self-test fault)")
                injected_abs.append(fault)
                raise fault
            return real_lstat_abs(path, *args, **kwargs)

        for _abs_armed in (True, False):
            before_abs = len(failures)
            if _abs_armed:
                os.lstat = _refusing_bare_lstat
            try:
                _check_bare_adopter_absence()
            finally:
                os.lstat = real_lstat_abs
            if _abs_armed:
                added_abs = failures[before_abs:]
                del failures[before_abs:]
                named_abs, kept_abs = _split_injected(
                    added_abs, injected_abs, ("absence verify",))
                if not any("could not verify" in entry
                           and "injected absence-check refusal" in entry
                           for entry in named_abs):
                    failures.append("the bare-adopter absence check: an lstat "
                                    "refusal must be a NAMED cannot-verify failure, "
                                    "never read as absence (a bare os.path.lexists "
                                    "does exactly that); got entries {!r}"
                                    .format(added_abs))
                failures.extend(kept_abs)
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

        def _check_marker_absence(label, badm):
            # The REAL bad-marker absence check (one call per planted marker): the
            # armed pass in the loop below re-drives the ONE call-site statement
            # under an injected lstat refusal WHILE the ordinary case runs, so
            # replacing that call with a bare os.path.lexists presence check goes
            # red, not only a helper revert.
            _require_absent(badm / ".claude" / "settings.json",
                            "a marker that is {}".format(label),
                            "a marker that is {} must not render project settings"
                            .format(label))

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

            def _refusing_marker_lstat(path, *args, _badm=badm, **kwargs):
                if str(path) == str(_badm / ".claude" / "settings.json"):
                    fault = PermissionError(
                        "injected absence-check refusal (self-test fault)")
                    injected_abs.append(fault)
                    raise fault
                return real_lstat_abs(path, *args, **kwargs)

            # The armed pass runs FIRST, and only for the first planted marker (one
            # injection proves the call site; every marker still gets the clean
            # pass), through the SAME call-site statement the clean pass runs.
            for _abs_armed in ((True, False) if i == 0 else (False,)):
                before_abs = len(failures)
                if _abs_armed:
                    os.lstat = _refusing_marker_lstat
                try:
                    _check_marker_absence(label, badm)
                finally:
                    os.lstat = real_lstat_abs
                if _abs_armed:
                    added_abs = failures[before_abs:]
                    del failures[before_abs:]
                    named_abs, kept_abs = _split_injected(
                        added_abs, injected_abs, ("absence verify",))
                    if not any("could not verify" in entry
                               and "injected absence-check refusal" in entry
                               for entry in named_abs):
                        failures.append("the bad-marker absence check: an lstat "
                                        "refusal must be a NAMED cannot-verify "
                                        "failure, never read as absence (a bare "
                                        "os.path.lexists does exactly that); got "
                                        "entries {!r}".format(added_abs))
                    failures.extend(kept_abs)
        at_bound = tmp / "markerbound"
        at_bound.mkdir()
        _build_project_tree(at_bound, authoring=False)
        _write_marker(at_bound, _padded_marker(_MARKER_BOUND))
        if run_project(at_bound, check=False) != 0 or not (at_bound / PROJECT_SETTINGS_REL).is_file():
            failures.append("a valid marker padded to exactly the bound expected exit 0 with project settings")

        # The 20b/21b lstat-refusal vectors live INSIDE cases 20 and 21 above: each
        # arms its refusal around the SAME call-site statement the ordinary case
        # runs, so the observation passes through the real absence invocations and
        # a bare os.path.lexists substituted at either call site goes red, not only
        # a helper revert inside _require_absent.

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
            #     PASS NOTE names it), never silently dropped. Cases 24 to 29 run only at top
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
                # The termination CAUSE is asserted too (the collected status must be
                # the cleanup's SIGKILL), so a retuned bound or a late injection can
                # never let a natural exit of the fixture pass as the kill.
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
                elif hasattr(signal, "SIGKILL") and \
                        state["proc"].returncode != -signal.SIGKILL:
                    failures.append("case {}: the collected exit status must be the "
                                    "cleanup's SIGKILL (returncode {}), got {}; a "
                                    "natural exit of the fixture must never pass as "
                                    "the kill".format(case_no, -signal.SIGKILL,
                                                      state["proc"].returncode))
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
            #     inject a drain exhaustion to reach their stage, and the kill-error,
            #     exhausted-reap, wait-error, drain-wait-error and rescue-error vectors
            #     alter kill and wait behavior as auxiliaries, so
            #     "one fault" names the TARGET, not the number of injections), then holds
            #     that the original re-raised, the targeted fault is a NAMED entry, the
            #     child was reaped (or its unreaped state named) and the child TMPDIR
            #     removed (the interrupt-removal and render-removal leftovers are BY
            #     DESIGN and the case removes them itself, requiring a NON-EMPTY
            #     recorded leftover path first: an empty path is a named failure,
            #     never a silent skip). The injected entries are
            #     then removed from the failure list BY PROVENANCE, never by message
            #     text: every injected fault is an exception object the vector creates
            #     and records as it fires, every staged-cleanup entry carries its stage
            #     and originating exception, and an entry is removed ONLY when it was
            #     recorded at a stage the vector declares AND its origin IS one of
            #     those recorded objects; every other entry stays a failure whatever
            #     its text (the genuine-token vector plants an UNRECORDED wait error
            #     whose message carries an injected token and holds that it is kept).
            #     The vector proves the injected entries were produced; they are not
            #     real failures. Every vector EXCEPT already-reaped (whose staged cleanup
            #     must never reach the drain or a wait once the status is collected,
            #     so it is EXEMPT from the two reap-bound checks) PINS both reap
            #     bounds against the literal
            #     contract (timeout=10 for the drain and for each wait, spelled as
            #     literals, never read from the implementation's constants), snapshots
            #     returncode and pipe state AT THE RE-RAISE (before the case's own
            #     rescue), and the close vectors hold that a failing close never skips
            #     the sibling close. The kill-error, exhausted-reap, wait-error,
            #     drain-wait-error and rescue-error vectors leave the real child alive
            #     or unreaped BY DESIGN; the case's RESCUE collects it before
            #     returning. The rescue bookkeeping is STRUCTURAL, never a message
            #     match: the rescue-error and final-kill vectors' injected rescue and
            #     final-collection faults are recognized by exception IDENTITY and
            #     recorded apart from genuine rescue and final-collection failures, a
            #     genuine entry is appended AFTER the expected-error filter (so it can
            #     never be discarded as an expected injection, whatever its text),
            #     each rescue and final-collection kill, wait and saved-pipe close
            #     runs under its OWN guard against ANY exception (an interruption
            #     included), so a kill failure of any type never skips the wait that
            #     follows, every rescue and final-collection wait records the bound it
            #     carried and must carry timeout=10 (a literal), and after the filter
            #     a recorded child without a collected returncode is a NAMED failure,
            #     so no vector can PASS with a live child (the rescue-error vector
            #     drives both rescue faults and then reaps the child through the
            #     final collection; the final-kill-runtime and final-kill-interrupt
            #     vectors make the FINAL kill raise a RuntimeError and a
            #     KeyboardInterrupt and hold that the bounded final wait still runs
            #     and collects the child).
            #     The wait-error and drain-wait-error vectors hold that a NON-timeout
            #     wait failure is a named entry on BOTH stage-2 branches (after a
            #     drain exhaustion and after a non-timeout drain failure); wait-error
            #     refuses the stage-2 re-kill, as kill-error does, so its not-reaped
            #     entry never depends on whether the re-kill's internal poll already
            #     collected the child. The render vectors hold that a secondary
            #     failure whose OWN __str__ or __repr__ raises, or whose render
            #     returns a str subclass that itself raises on __str__ or __format__
            #     (returned from the str fallback AND from the repr fallback), still
            #     becomes a named entry (the guarded render returns an exact built-in
            #     str with a constant fallback) without replacing the original or
            #     skipping the later stages, at the stage-3 close, at the leftover
            #     removal, and at the spawn launch diagnostic (the spawn-render
            #     vector after this case's loop); and the already-reaped vector holds
            #     that a child whose exit status was already collected is not
            #     signalled again by the staged cleanup (the returncode gate narrows,
            #     not eliminates, the reused-pid hazard).
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
                         "removal_interrupted": False, "kill_attempts": [],
                         "real_stdout": None, "real_stderr": None}
                # PROVENANCE: every fault this vector injects is recorded here as it
                # fires, and only entries those exact objects originated are removed
                # from the failure list below (_split_injected), whatever any entry's
                # text says. planted holds the genuine-token vector's UNRECORDED fault.
                injected = []
                planted = []
                # The final-kill vectors reuse the rescue-error vector's staged faults
                # (the child stays alive through the staged cleanup) and differ only
                # in which rescue and final-collection step is injected.
                rescue_like = kind in ("rescue-error", "final-kill-runtime",
                                       "final-kill-interrupt")

                def _inject(fault):
                    injected.append(fault)
                    return fault

                class _FailingClose:
                    def __init__(self, which, exc_factory=None):
                        self.which = which
                        self.exc_factory = exc_factory

                    def close(self):
                        state["close_attempts"].append(self.which)
                        if self.exc_factory is not None:
                            raise _inject(self.exc_factory())
                        raise _inject(OSError("injected close failure (self-test fault)"))

                class _RaisingStr(Exception):
                    # Its __str__ raises, so a diagnostic built from it must come from
                    # the guarded render's repr fallback, never from a crash inside the
                    # cleanup handler.
                    def __str__(self):
                        raise RuntimeError("str render failure (self-test fault)")

                class _RaisingBoth(Exception):
                    # Both renders raise, so only the constant fallback can name it.
                    def __str__(self):
                        raise RuntimeError("str render failure (self-test fault)")

                    def __repr__(self):
                        raise RuntimeError("repr render failure (self-test fault)")

                class _EvilStr(str):
                    # A str SUBCLASS whose own __str__ and __format__ raise: str(exc)
                    # may legally return it, and only a helper that normalizes to an
                    # exact built-in str keeps it out of the later .format of the
                    # diagnostic that embeds the rendered text.
                    def __str__(self):
                        raise RuntimeError("subclass str escape (self-test fault)")

                    def __format__(self, spec):
                        raise RuntimeError("subclass format escape (self-test fault)")

                class _SubclassRendered(Exception):
                    # Its __str__ SUCCEEDS but returns the raising subclass above.
                    def __str__(self):
                        return _EvilStr(
                            "subclass-rendered close failure (self-test fault)")

                class _SubclassRepr(Exception):
                    # Its __str__ RAISES while its __repr__ succeeds but returns the
                    # raising subclass above, so only a repr FALLBACK that normalizes
                    # to an exact built-in str keeps the subclass out of the later
                    # .format of the diagnostic that embeds the rendered text.
                    def __str__(self):
                        raise RuntimeError("str render failure (self-test fault)")

                    def __repr__(self):
                        return _EvilStr(
                            "subclass-repr close failure (self-test fault)")

                class _RaisingStrOSError(OSError):
                    # An OSError whose __str__ raises: the leftover-removal handler
                    # must render it through the guarded helper, never directly.
                    def __str__(self):
                        raise RuntimeError(
                            "leftover removal str render failure (self-test fault)")

                def _fault_communicate(self, *args, **kwargs):
                    if state["stage"] == "first":
                        state["stage"] = "drain"
                        state["proc"] = self
                        deadline = time.monotonic() + 20
                        while time.monotonic() < deadline and not beacon.is_file():
                            time.sleep(0.05)
                        if kind == "already-reaped":
                            # Collect the child's status BEFORE the fault fires: the
                            # staged cleanup must then never signal the collected (and
                            # so possibly reused) pid again.
                            real_kill(self)
                            real_wait(self, timeout=10)
                        if kind == "render-str":
                            state["real_stdout"], self.stdout = (
                                self.stdout, _FailingClose("stdout", lambda: _RaisingStr(
                                    "unrenderable close failure (self-test fault)")))
                        if kind == "render-repr":
                            state["real_stdout"], self.stdout = (
                                self.stdout, _FailingClose("stdout", _RaisingBoth))
                        if kind == "render-strsub":
                            state["real_stdout"], self.stdout = (
                                self.stdout,
                                _FailingClose("stdout", _SubclassRendered))
                        if kind == "render-reprsub":
                            state["real_stdout"], self.stdout = (
                                self.stdout,
                                _FailingClose("stdout", _SubclassRepr))
                        if kind in ("close-stdout", "genuine-token"):
                            state["real_stdout"], self.stdout = (
                                self.stdout, _FailingClose("stdout"))
                        if kind == "close-stderr":
                            state["real_stderr"], self.stderr = (
                                self.stderr, _FailingClose("stderr"))
                        raise original
                    state["drain_timeout"] = kwargs.get("timeout", "no-timeout-argument")
                    if kind in ("drain-error", "drain-wait-error"):
                        raise _inject(OSError("injected drain failure (self-test fault)"))
                    raise _inject(subprocess.TimeoutExpired(
                        cmd="injected drain exhaustion (self-test fault)", timeout=0))

                def _fault_kill(self):
                    if kind == "already-reaped" and self is state["proc"]:
                        # Recording, never killing: the vector holds that the staged
                        # cleanup never reaches a kill once the status is collected.
                        state["kill_attempts"].append("direct")
                        return None
                    # Refusing the kill (and the stage-2 re-kill for the exhausted-reap,
                    # wait-error and rescue-error vectors: a real kill would reap a dead
                    # child through its internal poll, so wait-error's not-reaped entry
                    # would otherwise depend on timing) keeps each vector pure.
                    if (kind in ("kill-error", "reap-exhausted", "wait-error")
                            or rescue_like) and self is state["proc"]:
                        raise _inject(OSError("injected kill failure (self-test fault)"))
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
                    if kind in ("wait-error", "drain-wait-error") and self is state["proc"]:
                        raise _inject(OSError("injected wait failure (self-test fault)"))
                    if (kind in ("kill-error", "reap-exhausted") or rescue_like) \
                            and self is state["proc"]:
                        raise _inject(subprocess.TimeoutExpired(
                            cmd="injected wait exhaustion (self-test fault)", timeout=0))
                    result = real_wait(self, *args, **kwargs)
                    if kind == "genuine-token" and self is state["proc"]:
                        # A GENUINE wait error, never recorded as injected, whose text
                        # carries the close vector's injected token: provenance must
                        # keep it a failure (a message-text filter discards it).
                        fault = OSError(
                            "genuine wait error: injected close failure (self-test fault)")
                        planted.append(fault)
                        raise fault
                    return result

                def _fault_killpg(pgid, sig):
                    if kind == "already-reaped" and state["proc"] is not None \
                            and pgid == state["proc"].pid:
                        state["kill_attempts"].append("group")
                        return None
                    if kind == "kill-error" or rescue_like:
                        raise _inject(OSError("injected group-kill failure (self-test fault)"))
                    if kind == "interrupt-group-kill":
                        raise _inject(KeyboardInterrupt(
                            "injected group-kill interrupt (self-test fault)"))
                    return real_killpg(pgid, sig)

                def _fault_rmtree(path, *args, **kwargs):
                    if (kind in ("interrupt-removal", "render-removal")
                            and not state["removal_interrupted"]
                            and os.path.basename(str(path)).startswith("bounded-child-")):
                        state["removal_interrupted"] = True
                        raise _inject(KeyboardInterrupt(
                            "injected removal interrupt (self-test fault)"))
                    return real_rmtree27(path, *args, **kwargs)

                before = len(failures)
                # Genuine rescue and final-collection failures are kept APART from
                # the injected staged-cleanup entries: rescue_named records ONLY the
                # rescue-error and final-kill vectors' injected rescue and final-
                # collection faults (recognized by exception IDENTITY, never by
                # message text), genuine records every real rescue or final-
                # collection failure, and genuine is appended AFTER the expected-error
                # filter below, so a real failure can never be discarded as an
                # expected injection, whatever its message contains.
                rescue_named = []
                genuine = []
                rescue_kill_fault = (
                    OSError("injected rescue kill failure (self-test fault)")
                    if kind == "rescue-error" else None)
                rescue_wait_fault = (
                    OSError("injected rescue wait failure (self-test fault)")
                    if rescue_like else None)
                final_kill_fault = {
                    "final-kill-runtime": RuntimeError(
                        "injected final kill runtime failure (self-test fault)"),
                    "final-kill-interrupt": KeyboardInterrupt(
                        "injected final kill interrupt (self-test fault)"),
                }.get(kind)
                # The bound EVERY rescue and final-collection wait carried, recorded
                # BEFORE the wait runs (an injected fault included), so a retuned or
                # unbounded collection wait goes red below exactly as a staged one
                # does.
                collection_waits = {"rescue": [], "final": []}

                def _collecting_wait(proc_, phase, fault, *args, **kwargs):
                    if "timeout" in kwargs:
                        collection_waits[phase].append(kwargs["timeout"])
                    elif args:
                        collection_waits[phase].append(args[0])
                    else:
                        collection_waits[phase].append("no-timeout-argument")
                    if fault is not None:
                        raise fault
                    return real_wait(proc_, *args, **kwargs)

                returned = None
                subprocess.Popen.communicate = _fault_communicate
                subprocess.Popen.kill = _fault_kill
                subprocess.Popen.wait = _fault_wait
                shutil.rmtree = _fault_rmtree
                if real_killpg is not None:
                    os.killpg = _fault_killpg
                try:
                    returned = _run_bounded(
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
                        # The rescue is itself VERIFIED: a rescue kill or wait error
                        # is a named entry; the rescue-error and final-kill vectors'
                        # INJECTED faults go to rescue_named (recognized by exception
                        # identity) while any REAL fault goes to genuine (never
                        # filtered), and a child whose exit status is still
                        # uncollected after the rescue is recorded the same way, then
                        # the FINAL collection below reaps it, so no vector can PASS
                        # while its child is alive. Every step is guarded against ANY
                        # exception (an interruption included), so no step can skip
                        # the next one or escape this finally.
                        rescue_wait_injected = False
                        try:
                            if rescue_kill_fault is not None:
                                raise rescue_kill_fault
                            real_kill(leftover)
                        except BaseException as rescue_exc:
                            (rescue_named if rescue_exc is rescue_kill_fault
                             else genuine).append(
                                "case 27 ({}): the rescue kill of the leftover "
                                "child (pid {}) failed ({}); a rescue failure is a "
                                "named entry, never a silent pass".format(
                                    kind, leftover.pid, _exc_text(rescue_exc)))
                        try:
                            _collecting_wait(leftover, "rescue", rescue_wait_fault,
                                             timeout=10)
                        except BaseException as rescue_exc:
                            rescue_wait_injected = rescue_exc is rescue_wait_fault
                            (rescue_named if rescue_wait_injected
                             else genuine).append(
                                "case 27 ({}): the rescue wait for the leftover "
                                "child (pid {}) failed ({}); a rescue failure is a "
                                "named entry, never a silent pass".format(
                                    kind, leftover.pid, _exc_text(rescue_exc)))
                        if leftover.returncode is None:
                            uncollected_entry = (
                                "case 27 ({}): the rescue could not collect the "
                                "leftover child (pid {}); the case must FAIL rather "
                                "than PASS with a live child".format(
                                    kind, leftover.pid))
                            if rescue_wait_injected:
                                # EXPECTED here: the injected rescue wait fault
                                # fired, so the uncollected state proves the
                                # naming; the final collection below reaps the
                                # child.
                                rescue_named.append(uncollected_entry)
                            else:
                                genuine.append(uncollected_entry)
                            # The FINAL collection, for EVERY vector whose child is
                            # still uncollected: the kill and the bounded wait run
                            # under SEPARATE guards against ANY exception, so a kill
                            # failure of any type never skips the wait, and a REAL
                            # failure of either is a GENUINE entry the expected-
                            # error filter can never discard.
                            try:
                                if final_kill_fault is not None:
                                    raise final_kill_fault
                                real_kill(leftover)
                            except BaseException as final_exc:
                                (rescue_named if final_exc is final_kill_fault
                                 else genuine).append(
                                    "case 27 ({}): the final collection kill of "
                                    "the leftover child (pid {}) failed ({})"
                                    .format(kind, leftover.pid,
                                            _exc_text(final_exc)))
                            try:
                                _collecting_wait(leftover, "final", None, timeout=10)
                            except BaseException as final_exc:
                                genuine.append(
                                    "case 27 ({}): the final collection wait for "
                                    "the leftover child (pid {}) failed ({})"
                                    .format(kind, leftover.pid,
                                            _exc_text(final_exc)))
                    for saved_name in ("real_stdout", "real_stderr"):
                        saved = state[saved_name]
                        if saved is not None:
                            try:
                                saved.close()
                            except BaseException as saved_exc:
                                genuine.append(
                                    "case 27 ({}): closing the saved {} pipe "
                                    "failed ({})".format(kind, saved_name,
                                                         _exc_text(saved_exc)))
                if returned is not None:
                    failures.append("case 27 ({}): the original injected OSError must "
                                    "re-raise out of _run_bounded, got exit {!r}"
                                    .format(kind, returned[0]))
                    failures.extend(genuine)
                    if state["proc"] is not None and state["proc"].returncode is None:
                        failures.append("case 27 ({}): the recorded child (pid {}) "
                                        "has no collected returncode; the case must "
                                        "FAIL rather than PASS with a live child"
                                        .format(kind, state["proc"].pid))
                    return
                added = failures[before:]
                del failures[before:]
                # Per vector: the staged-cleanup STAGES at which an injected fault
                # may be named, and the text each injected entry must render (the
                # render vectors prove the guarded render through that text). The
                # text is checked ONLY among entries the provenance split already
                # attributed to an injected fault; it never selects what is removed.
                killing = ("stage 1 direct kill", "stage 2 re-kill", "stage 2 unreaped")
                stages, expected = {
                    "drain-error": (("stage 2 drain",),
                                    ("injected drain failure (self-test fault)",)),
                    "kill-error": (killing,
                                   ("injected kill failure (self-test fault)",
                                    "was not reaped within")),
                    "reap-exhausted": (killing,
                                       ("was not reaped within",
                                        "injected kill failure (self-test fault)")),
                    "close-stdout": (("stage 3 close",),
                                     ("injected close failure (self-test fault)",)),
                    "close-stderr": (("stage 3 close",),
                                     ("injected close failure (self-test fault)",)),
                    "genuine-token": (("stage 3 close",),
                                      ("injected close failure (self-test fault)",)),
                    "interrupt-group-kill": (
                        ("stage 1 group kill",),
                        ("injected group-kill interrupt (self-test fault)",)),
                    "interrupt-removal": (
                        ("stage 4 removal",),
                        ("injected removal interrupt (self-test fault)",)),
                    "wait-error": (killing + ("stage 2 wait",),
                                   ("injected wait failure (self-test fault)",
                                    "injected kill failure (self-test fault)",
                                    "was not reaped within")),
                    "drain-wait-error": (
                        ("stage 2 drain", "stage 2 wait", "stage 2 unreaped"),
                        ("injected drain failure (self-test fault)",
                         "injected wait failure (self-test fault)",
                         "was not reaped within")),
                    "rescue-error": (killing,
                                     ("injected kill failure (self-test fault)",
                                      "was not reaped within")),
                    "final-kill-runtime": (killing,
                                           ("injected kill failure (self-test fault)",
                                            "was not reaped within")),
                    "final-kill-interrupt": (killing,
                                             ("injected kill failure (self-test fault)",
                                              "was not reaped within")),
                    "render-str": (("stage 3 close",),
                                   ("unrenderable close failure (self-test fault)",)),
                    "render-repr": (("stage 3 close",),
                                    ("<unrenderable _RaisingBoth instance>",)),
                    "render-strsub": (
                        ("stage 3 close",),
                        ("subclass-rendered close failure (self-test fault)",)),
                    "render-reprsub": (
                        ("stage 3 close",),
                        ("subclass-repr close failure (self-test fault)",)),
                    "render-removal": (
                        ("stage 4 removal",),
                        ("injected removal interrupt (self-test fault)",)),
                    "already-reaped": ((), ()),
                }[kind]
                named, kept = _split_injected(added, injected, stages)
                for want in expected:
                    if not any(want in entry for entry in named):
                        failures.append("case 27 ({}): the secondary cleanup failure was "
                                        "not NAMED (no entry originated by an injected "
                                        "fault containing {!r} among {!r})"
                                        .format(kind, want, added))
                if kind == "genuine-token":
                    # The planted GENUINE wait error must survive the split as a kept
                    # failure although its text carries the injected close token; the
                    # vector then drops exactly the entries that planted object
                    # originated (identity), so any OTHER kept entry still fails.
                    planted_kept = [entry for entry in kept
                                    if any(getattr(entry, "origin", None) is fault
                                           for fault in planted)]
                    if not planted or not planted_kept:
                        failures.append("case 27 (genuine-token): a genuine wait error "
                                        "whose text carries an injected token must stay "
                                        "a failure, never be discarded as an injection "
                                        "(planted {}, kept {!r}, all entries {!r})"
                                        .format(len(planted), kept, added))
                    kept = [entry for entry in kept
                            if not any(entry is planted_entry
                                       for planted_entry in planted_kept)]
                failures.extend(kept)
                # Genuine rescue and final-collection failures NEVER pass through the
                # expected-error filter above: they are real failures whatever their
                # text, appended verbatim here.
                failures.extend(genuine)
                rescue_wants = {
                    "rescue-error": (
                        "injected rescue kill failure (self-test fault)",
                        "injected rescue wait failure (self-test fault)",
                        "the rescue could not collect"),
                    "final-kill-runtime": (
                        "injected rescue wait failure (self-test fault)",
                        "the rescue could not collect",
                        "injected final kill runtime failure (self-test fault)"),
                    "final-kill-interrupt": (
                        "injected rescue wait failure (self-test fault)",
                        "the rescue could not collect",
                        "injected final kill interrupt (self-test fault)"),
                }.get(kind)
                if rescue_wants is not None:
                    # The vector proves its rescue and final-collection diagnostics
                    # were produced, in the structure that records ONLY injected
                    # faults.
                    for want_rescue in rescue_wants:
                        if not any(want_rescue in entry for entry in rescue_named):
                            failures.append("case 27 ({}): the injected rescue or "
                                            "final-collection fault was not NAMED (no "
                                            "recorded entry containing {!r} among {!r})"
                                            .format(kind, want_rescue, rescue_named))
                elif rescue_named:
                    failures.append("case 27 ({}): rescue entries were recorded as "
                                    "injected for a vector that injects no rescue "
                                    "fault: {!r}".format(kind, rescue_named))
                if state["proc"] is not None and state["proc"].returncode is None:
                    failures.append("case 27 ({}): the recorded child (pid {}) has "
                                    "no collected returncode after the expected-"
                                    "error filter; the case must FAIL rather than "
                                    "PASS with a live child".format(
                                        kind, state["proc"].pid))
                if kind in ("drain-error", "close-stdout", "close-stderr",
                            "genuine-token", "interrupt-group-kill", "interrupt-removal",
                            "render-str", "render-repr", "render-strsub",
                            "render-reprsub", "render-removal",
                            "already-reaped") and \
                        state["returncode_at_reraise"] is None:
                    failures.append("case 27 ({}): the exit status of the child was never "
                                    "collected at the re-raise (before this case's own "
                                    "rescue) despite a workable reap path".format(kind))
                # Independent literal expectations: 10 is the pinned CONTRACT for the
                # drain bound and for the wait bound, spelled as literals on purpose
                # (never read from _REAP_DRAIN_BOUND or _REAP_WAIT_BOUND), so retuning
                # either constant, or dropping a wait's timeout argument, goes red here
                # instead of silently retuning this test with it.
                if kind != "already-reaped" and state["drain_timeout"] != 10:
                    failures.append("case 27 ({}): the pipe drain must carry timeout=10 "
                                    "(the pinned drain bound), got {!r}"
                                    .format(kind, state["drain_timeout"]))
                if kind != "already-reaped" and (not state["wait_timeouts"] or any(
                        timeout_seen != 10 for timeout_seen in state["wait_timeouts"])):
                    failures.append("case 27 ({}): every bounded-reap wait must carry "
                                    "timeout=10 (the pinned wait bound) and at least one "
                                    "wait must run, got {!r}"
                                    .format(kind, state["wait_timeouts"]))
                # The same literal contract for the case's OWN rescue and final-
                # collection waits (they run after the staged-wait recorder above is
                # restored, so they record through _collecting_wait): every vector
                # whose staged cleanup leaves the child unreaped BY DESIGN must run a
                # rescue wait, every vector that defeats the rescue must run a final
                # wait (after its final kill raised, for the final-kill vectors), and
                # each must carry timeout=10, so an unbounded or retuned collection
                # wait goes red here.
                for phase, must_run in (
                        ("rescue", kind in ("kill-error", "reap-exhausted", "wait-error",
                                            "drain-wait-error") or rescue_like),
                        ("final", rescue_like)):
                    seen_waits = collection_waits[phase]
                    if (must_run and not seen_waits) or any(
                            timeout_seen != 10 for timeout_seen in seen_waits):
                        failures.append("case 27 ({}): every {} wait must carry "
                                        "timeout=10 (the pinned collection bound){}, "
                                        "got {!r}".format(
                                            kind, phase,
                                            " and at least one must run" if must_run
                                            else "", seen_waits))
                if kind == "already-reaped" and state["kill_attempts"]:
                    failures.append("case 27 (already-reaped): the staged cleanup "
                                    "signalled a child whose exit status was already "
                                    "collected ({!r}); a collected pid may be reused "
                                    "and is never a target"
                                    .format(state["kill_attempts"]))
                if kind in ("close-stdout", "close-stderr", "genuine-token", "render-str",
                            "render-repr", "render-strsub", "render-reprsub"):
                    failing = "stderr" if kind == "close-stderr" else "stdout"
                    sibling = "stdout" if kind == "close-stderr" else "stderr"
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
                if kind in ("interrupt-removal", "render-removal"):
                    # The injected interrupt left the child TMPDIR behind BY DESIGN (the
                    # stage-4 guard named it; no retry ran); this case removes the
                    # leftover itself so nothing outlives it. BOTH removal vectors
                    # REQUIRE a non-empty recorded leftover path before proceeding: an
                    # empty path is a named failure, never a silent skip of the local
                    # removal check. The render-removal vector
                    # drives THIS removal handler under an injected OSError whose own
                    # __str__ raises: the diagnostic below renders through _exc_text,
                    # so the entry is still NAMED instead of the render escaping (red
                    # with the exception formatted directly).
                    if not state["removal_interrupted"]:
                        failures.append("case 27 ({}): the injected removal interrupt "
                                        "never fired".format(kind))
                    if not child_tmpdir:
                        failures.append("case 27 ({}): the beacon recorded no child "
                                        "TMPDIR path, so the leftover-removal check "
                                        "could not proceed; a missing path is a named "
                                        "failure, never a silent skip".format(kind))
                        return
                    before_rm = len(failures)
                    try:
                        if kind == "render-removal":
                            raise _inject(_RaisingStrOSError(
                                "injected leftover-removal failure "
                                "(self-test fault)"))
                        real_rmtree27(child_tmpdir)
                    except FileNotFoundError:
                        pass
                    except OSError as exc:
                        _observe("case 27 ({}): could not remove the "
                                 "leftover child TMPDIR {!r} ({})"
                                 .format(kind, child_tmpdir,
                                         _exc_text(exc)), "leftover removal", exc)
                    if kind == "render-removal":
                        added_rm = failures[before_rm:]
                        del failures[before_rm:]
                        named_rm, kept_rm = _split_injected(
                            added_rm, injected, ("leftover removal",))
                        if not any(
                                "could not remove the leftover child TMPDIR" in entry
                                and "injected leftover-removal failure "
                                    "(self-test fault)" in entry
                                for entry in named_rm):
                            failures.append("case 27 (render-removal): a leftover-"
                                            "removal error whose own __str__ raises "
                                            "must still be a NAMED entry through the "
                                            "guarded render, never an escaping render "
                                            "exception; got entries {!r}"
                                            .format(added_rm))
                        failures.extend(kept_rm)
                        try:
                            real_rmtree27(child_tmpdir)
                        except FileNotFoundError:
                            pass
                        except OSError as exc:
                            failures.append("case 27 (render-removal): could not "
                                            "remove the leftover child TMPDIR "
                                            "{!r} ({})".format(child_tmpdir,
                                                               _exc_text(exc)))
                elif not child_tmpdir or not _confirmed_absent(child_tmpdir, label27):
                    failures.append("case 27 ({}): the child TMPDIR {!r} was not removed "
                                    "(or its absence could not be confirmed)"
                                    .format(kind, child_tmpdir))

            _vector_kinds = ["drain-error", "kill-error", "close-stdout", "close-stderr",
                             "interrupt-removal", "wait-error", "drain-wait-error",
                             "rescue-error", "final-kill-runtime",
                             "final-kill-interrupt", "genuine-token", "render-str",
                             "render-repr", "render-strsub", "render-reprsub",
                             "render-removal", "already-reaped"]
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

            # 27 (spawn-render): a SPAWN failure whose own render raises drives the
            # REAL launch handler in _run_bounded: the launch diagnostic must render
            # through the guarded helper and NAME the failure (red when the launch
            # diagnostic formats the exception directly: the raising __str__ then
            # escapes the handler, and this vector catches and names the escape).
            class _RaisingStrSpawnOSError(OSError):
                def __str__(self):
                    raise RuntimeError("spawn render failure (self-test fault)")

            real_popen27 = subprocess.Popen
            injected_spawn = []

            def _refusing_popen(*args, **kwargs):
                fault = _RaisingStrSpawnOSError("injected spawn failure (self-test fault)")
                injected_spawn.append(fault)
                raise fault

            before_spawn = len(failures)
            spawn_escape = None
            spawn_result = None
            subprocess.Popen = _refusing_popen
            try:
                spawn_result = _run_bounded("case 27 (spawn-render)",
                                            "raise SystemExit(0)\n", timeout=30)
            except BaseException as exc:
                spawn_escape = exc
            finally:
                subprocess.Popen = real_popen27
            added_spawn = failures[before_spawn:]
            del failures[before_spawn:]
            if spawn_escape is not None:
                failures.append("case 27 (spawn-render): the launch diagnostic must "
                                "render the spawn failure through the guarded "
                                "helper; a {} escaped _run_bounded instead"
                                .format(type(spawn_escape).__name__))
            elif spawn_result != (None, ""):
                failures.append("case 27 (spawn-render): a spawn failure must return "
                                "(None, '') with a named launch failure, got {!r}"
                                .format(spawn_result))
            named_spawn, kept_spawn = _split_injected(
                added_spawn, injected_spawn, ("launch",))
            if not any("could not launch the bounded child" in entry
                       and "injected spawn failure (self-test fault)" in entry
                       for entry in named_spawn):
                failures.append("case 27 (spawn-render): the launch failure was not "
                                "NAMED through the guarded render (no entry naming "
                                "the injected spawn failure); got entries {!r}"
                                .format(added_spawn))
            failures.extend(kept_spawn)

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
            #     DAC-bypassing root one included). Vector (e): a GENUINE verification
            #     error whose text carries vector (c)'s token stays a failure beside the
            #     removed injected entry. Each vector removes ONLY the entries its
            #     recorded faults originated (_split_injected), never a whole slice and
            #     never an entry selected by its text.
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
                    # Every injected fault is recorded as it fires; only entries those
                    # exact objects originated are removed below (_split_injected).
                    injected28c = []

                    def _refusing_once_rmtree_c(path, *args, **kwargs):
                        if state28c["refusals"] == 0:
                            state28c["refusals"] = 1
                            fault = OSError("injected rmtree refusal (self-test fault)")
                            injected28c.append(fault)
                            raise fault
                        return real_rmtree(path, *args, **kwargs)

                    def _refusing_entry_lstat(path, *args, **kwargs):
                        if str(path) == str(tree28c / "link"):
                            fault = PermissionError(
                                "injected entry lstat refusal (self-test fault)")
                            injected28c.append(fault)
                            raise fault
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
                    named28c, kept28c = _split_injected(
                        added28c, injected28c, ("removal classify",))
                    if not any("could not classify" in entry for entry in named28c):
                        failures.append("case 28: an entry the repair cannot classify "
                                        "(lstat refused) must be a NAMED failure, never "
                                        "a chmod and never a silent pass; got entries "
                                        "{!r}".format(added28c))
                    failures.extend(kept28c)
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
                injected28b = []

                def _refusing_lstat(path, *args, **kwargs):
                    if str(path) == str(tree28b):
                        fault = PermissionError("injected lstat refusal (self-test fault)")
                        injected28b.append(fault)
                        raise fault
                    return real_lstat(path, *args, **kwargs)

                def _refusing_rmtree(path, *args, **kwargs):
                    fault = OSError("injected rmtree refusal (self-test fault)")
                    injected28b.append(fault)
                    raise fault

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
                # Only the entries the injected refusals originated are removed; any
                # other entry stays a failure whatever its text.
                named28, kept28 = _split_injected(added28, injected28b, ("removal verify",))
                if ok28b or not any("could not be verified" in entry
                                    for entry in named28):
                    failures.append("case 28: an unverifiable removal (lstat refused) must "
                                    "be a NAMED failure, never a silent success; got "
                                    "result={!r} entries={!r}".format(ok28b, added28))
                failures.extend(kept28)
                real_rmtree(str(tree28b))

                # Vector (e): the provenance filter keeps a GENUINE failure whose text
                # carries an injected token. The first rmtree and the interior link's
                # lstat are refused (injected and recorded, as in vector (c)); after
                # the real retry removes the tree, the verification lstat of the root
                # raises a PLANTED genuine error, never recorded as injected, whose
                # text carries vector (c)'s token. The injected classification entry
                # is removed; the planted cannot-verify entry must stay a failure (a
                # message-text or whole-slice filter discards it), and the vector then
                # drops exactly the entries that planted object originated.
                base28e = base28 / "genuine-token"
                base28e.mkdir()
                tree28e = base28e / "tree"
                tree28e.mkdir()
                try:
                    os.symlink(str(base28e), str(tree28e / "link"))
                except OSError as exc:
                    failures.append("case 28: NOT RUN: the genuine-token symlink fixture "
                                    "could not be planted ({}); coverage that did not run "
                                    "is a named failure".format(exc))
                else:
                    injected28e = []
                    planted28e = []
                    state28e = {"refusals": 0}

                    def _refusing_once_rmtree_e(path, *args, **kwargs):
                        if state28e["refusals"] == 0:
                            state28e["refusals"] = 1
                            fault = OSError("injected rmtree refusal (self-test fault)")
                            injected28e.append(fault)
                            raise fault
                        return real_rmtree(path, *args, **kwargs)

                    def _planting_lstat(path, *args, **kwargs):
                        if str(path) == str(tree28e / "link"):
                            fault = PermissionError(
                                "injected entry lstat refusal (self-test fault)")
                            injected28e.append(fault)
                            raise fault
                        try:
                            return real_lstat(path, *args, **kwargs)
                        except FileNotFoundError:
                            if str(path) == str(tree28e) and not planted28e:
                                fault = PermissionError(
                                    "genuine root lstat error: could not classify "
                                    "(self-test fault)")
                                planted28e.append(fault)
                                raise fault
                            raise

                    before28e = len(failures)
                    shutil.rmtree = _refusing_once_rmtree_e
                    os.lstat = _planting_lstat
                    try:
                        ok28e = _rmtree_verified(tree28e, "case 28 genuine token")
                    finally:
                        shutil.rmtree = real_rmtree
                        os.lstat = real_lstat
                    added28e = failures[before28e:]
                    del failures[before28e:]
                    named28e, kept28e = _split_injected(
                        added28e, injected28e, ("removal classify",))
                    planted_kept28e = [entry for entry in kept28e
                                       if any(getattr(entry, "origin", None) is fault
                                              for fault in planted28e)]
                    if not any("could not classify" in entry for entry in named28e):
                        failures.append("case 28 (genuine token): the injected "
                                        "classification refusal was not NAMED; got "
                                        "entries {!r}".format(added28e))
                    if ok28e or not planted_kept28e:
                        failures.append("case 28 (genuine token): a genuine "
                                        "verification error whose text carries an "
                                        "injected token must stay a failure, never be "
                                        "discarded as an injection (result {!r}, "
                                        "planted {}, kept {!r}, all entries {!r})"
                                        .format(ok28e, len(planted28e), kept28e,
                                                added28e))
                    failures.extend(entry for entry in kept28e
                                    if not any(entry is planted_entry
                                               for planted_entry in planted_kept28e))
                    if not _confirmed_absent(str(tree28e), "case 28 genuine token"):
                        failures.append("case 28 (genuine token): the tree was not "
                                        "removed on the retry")

                # Vector (d): the ROOT itself is a symlink to an outside directory and
                # cannot be unlinked (its parent directory is read-only, mode 0o500):
                # _rmtree_verified must NEVER walk it (os.walk follows a root symlink
                # into the outside tree, where the repair would chmod outside entries),
                # must leave the outside subdirectory's mode unchanged, and must name
                # the failure. Skipped, named, where the runner's identity defeats the
                # 0o500 fixture (a DAC bypass such as root), as in cases 5 and 10.
                base28d = base28 / "root-link"
                base28d.mkdir()
                outside_d = base28d / "outside"
                outside_d.mkdir()
                inner_d = outside_d / "sub"
                inner_d.mkdir()
                os.chmod(str(inner_d), 0o750)
                holder_d = base28d / "holder"
                holder_d.mkdir()
                root_link = holder_d / "tree"
                try:
                    os.symlink(str(outside_d), str(root_link))
                except OSError as exc:
                    failures.append("case 28: NOT RUN: the root-symlink fixture could "
                                    "not be planted ({}); coverage that did not run is "
                                    "a named failure".format(exc))
                else:
                    os.chmod(str(holder_d), 0o500)
                    if os.access(str(holder_d), os.W_OK):
                        skipped.append("28 root-symlink-unremovable")
                        os.chmod(str(holder_d), 0o700)
                    else:
                        # This refusal comes from the fixture, not an injection, so the
                        # vector RECORDS the exact exception os.unlink raises for the
                        # root link as it fires; only the entry that object originated
                        # is removed below, never an entry selected by its text.
                        real_unlink_d = os.unlink
                        observed28d = []

                        def _observing_unlink(path, *args, **kwargs):
                            try:
                                return real_unlink_d(path, *args, **kwargs)
                            except OSError as exc:
                                if str(path) == str(root_link):
                                    observed28d.append(exc)
                                raise

                        before28d = len(failures)
                        os.unlink = _observing_unlink
                        try:
                            ok28d = _rmtree_verified(root_link, "case 28 root symlink")
                        finally:
                            os.unlink = real_unlink_d
                            os.chmod(str(holder_d), 0o700)
                        added28d = failures[before28d:]
                        del failures[before28d:]
                        named28d, kept28d = _split_injected(
                            added28d, observed28d, ("removal root unlink",))
                        if ok28d or not any(
                                "is a symlink that could not be unlinked" in entry
                                for entry in named28d):
                            failures.append("case 28: an unremovable ROOT symlink must "
                                            "be a NAMED failure and never a success; "
                                            "got result={!r} entries={!r}"
                                            .format(ok28d, added28d))
                        failures.extend(kept28d)
                        mode_after_d = os.lstat(str(inner_d)).st_mode & 0o7777
                        if mode_after_d != 0o750:
                            failures.append("case 28: an unremovable root symlink led "
                                            "to a walk of the OUTSIDE tree (the outside "
                                            "subdirectory's 0o750 became {:#o}); a root "
                                            "the helper cannot confirm as a directory "
                                            "is never walked".format(mode_after_d))
                        if not os.path.islink(str(root_link)):
                            failures.append("case 28: the unremovable-root fixture did "
                                            "not hold (the root symlink is gone), so "
                                            "the vector proved nothing")

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

        # 30. The deadline relation. Each case rewrites the stub's two deadline lines; a refused case
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

        # 31. The optional per-hook timeout: an integer from TIMEOUT_MIN to TIMEOUT_MAX seconds. Each end
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
        # The optional [plugin] keys validate fail-closed too. The version strings are STRICT
        # MAJOR.MINOR.PATCH: a non-string, a missing component, a trailing newline (which a $-anchored
        # regex accepts), a leading zero and non-ASCII digits (which \d accepts) are each refused,
        # while "0.0.0" is legal (zero itself is not a leading zero).
        plug_anchor = 'homepage = "https://example.invalid"'
        for label, addition, want in (
                ("switch-nonbool", 'on-failure-render = "yes"', 2),
                ("minver-shape", 'claude-code-min-version = "2.1"', 2),
                ("minver-nonstring", "claude-code-min-version = 2", 2),
                ("minver-trailing-newline", 'claude-code-min-version = "2.1.295\\n"', 2),
                ("minver-leading-zero", 'claude-code-min-version = "02.1.295"', 2),
                ("minver-nonascii-digits",
                 'claude-code-min-version = "\u0662.\u0661.\u0662\u0669\u0665"', 2),
                ("minver-zeros", 'claude-code-min-version = "0.0.0"', 0)):
            tree = tmp / ("plugkey-" + label)
            tree.mkdir()
            _build_tree(tree)
            _mutate(tree, plug_anchor, plug_anchor + "\n" + addition)
            rc = run_quiet(tree, check=(want != 0))
            if rc != want:
                failures.append("[plugin] {}: expected exit {}, got {}".format(label, want, rc))
        # The [plugin] version goes through the same regex; one strictness vector rides along.
        vzero = tmp / "plugkey-version-leading-zero"
        vzero.mkdir()
        _build_tree(vzero)
        _mutate(vzero, 'version = "0.1.0"', 'version = "01.1.0"')
        if run_quiet(vzero, check=True) != 2:
            failures.append("[plugin] version-leading-zero: expected exit 2 (fail-closed)")

        # 32. OFF is byte-identity, for an explicitly false switch AND for an ABSENT one (each its own
        #     tree, so a regression that treats only an explicit false as off is caught): an authored
        #     per-hook timeout and the optional [plugin] keys change NOTHING in the rendered hooks.json,
        #     which stays byte-identical to a tree authoring none of them, with no onFailure key
        #     anywhere. The baseline render itself is pinned to spelled-out expected bytes (the case-16
        #     idiom: never built from the module constants), so a rendering regression shared by both
        #     sides of the byte comparison cannot hide.
        ofr_base = tmp / "ofr-base"
        ofr_base.mkdir()
        _build_tree(ofr_base)
        ofr_off = tmp / "ofr-off"
        ofr_off.mkdir()
        _build_tree(ofr_off)
        _mutate(ofr_off, plug_anchor,
                plug_anchor + '\nclaude-code-min-version = "2.1.295"\non-failure-render = false')
        _mutate(ofr_off, 'default = "block"', 'default = "block"\ntimeout = 45')
        ofr_absent = tmp / "ofr-absent"
        ofr_absent.mkdir()
        _build_tree(ofr_absent)
        _mutate(ofr_absent, plug_anchor, plug_anchor + '\nclaude-code-min-version = "2.1.295"')
        _mutate(ofr_absent, 'default = "block"', 'default = "block"\ntimeout = 45')
        if (run_quiet(ofr_base, check=False) != 0 or run_quiet(ofr_off, check=False) != 0
                or run_quiet(ofr_absent, check=False) != 0):
            failures.append("off-switch byte-identity setup: all three trees expected a clean generate")
        else:
            base_json = (ofr_base / HOOKS_JSON_REL).read_text(encoding="utf-8")
            want_base = (
                '{\n'
                '  "description": "AIQT Guardrails mechanical-enforcement hooks, generated from'
                ' .aiqt/core/hooks/manifest.toml by tools/gen_hooks.py; do not hand-edit.",\n'
                '  "hooks": {\n'
                '    "PreToolUse": [\n'
                '      {\n'
                '        "matcher": "Bash",\n'
                '        "hooks": [\n'
                '          {\n'
                '            "type": "command",\n'
                '            "command": "python3",\n'
                '            "args": [\n'
                '              "-I",\n'
                '              "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/aiqt_hooks.py",\n'
                '              "test_handler"\n'
                '            ],\n'
                '            "timeout": 10\n'
                '          }\n'
                '        ]\n'
                '      }\n'
                '    ]\n'
                '  }\n'
                '}\n')
            if base_json != want_base:
                failures.append("the baseline hooks.json must match the spelled-out expected bytes")
            for label, tree in (("false", ofr_off), ("absent", ofr_absent)):
                other_json = (tree / HOOKS_JSON_REL).read_text(encoding="utf-8")
                if base_json != other_json:
                    failures.append("with on-failure-render {}, an authored timeout and the optional "
                                    "[plugin] keys must render byte-identical hooks.json".format(label))
                if "onFailure" in other_json:
                    failures.append("with on-failure-render {}, no onFailure key may render"
                                    .format(label))

        # 33. ON renders EVERY launcher with its configured timeout (the authored value, or TIMEOUT
        #     where none is authored), and exactly the enforce-stage PreToolUse block hooks also carry
        #     onFailure "block": an implicit-enforce and an explicit-enforce block hook carry it, while
        #     a bake-stage PreToolUse block hook, a PreToolUse warn (bake) hook, a Stop block hook and
        #     a TeammateIdle block hook each render their authored timeout WITHOUT it. The bake-stage
        #     BLOCK hook discriminates the stage clause on its own (the warn hook is excluded by its
        #     default already), and every excluded hook authors a distinct timeout, so "no onFailure"
        #     never collapses into "no authored timeout". The contract is then shown DISCRIMINATING
        #     against three mutant outputs: one dropping every onFailure key, one resetting every
        #     timeout to the default, and one writing an explicit JSON null where the key must be
        #     absent, each of which it must catch.
        ofr_on = tmp / "ofr-on"
        ofr_on.mkdir()
        _build_tree(ofr_on)
        _mutate(ofr_on, plug_anchor,
                plug_anchor + '\nclaude-code-min-version = "2.1.295"\non-failure-render = true')
        _mutate(ofr_on, 'default = "block"', 'default = "block"\ntimeout = 45')
        on_script = ofr_on / ".aiqt" / "core" / "hooks" / "scripts" / SCRIPT_NAME
        on_script.write_text(on_script.read_text(encoding="utf-8")
                             + "def enforce_handler(data):\n    return (0, None, None)\n"
                               "def bake_handler(data):\n    return (0, None, None)\n"
                               "def warn_handler(data):\n    return (0, None, None)\n"
                               "def stop_handler(data):\n    return (0, None, None)\n"
                               "def idle_handler(data):\n    return (0, None, None)\n", encoding="utf-8")
        on_manifest = ofr_on / ".aiqt" / "core" / "hooks" / "manifest.toml"
        on_manifest.write_text(on_manifest.read_text(encoding="utf-8") + (
            '\n[[hook]]\nid = "selftest-enforce-block"\nrules = ["tsthk1"]\nplatform = "claude-code"\n'
            'event = "PreToolUse"\nmatcher = "Bash"\nhandler = "enforce_handler"\ndefault = "block"\n'
            'stage = "enforce"\ntimeout = 20\nclass = "b"\n'
            'residue = "An explicit-enforce block self-test control."\n'
            '\n[[hook]]\nid = "selftest-bake-block"\nrules = ["tsthk1"]\nplatform = "claude-code"\n'
            'event = "PreToolUse"\nmatcher = "Bash"\nhandler = "bake_handler"\ndefault = "block"\n'
            'stage = "bake"\ntimeout = 25\nclass = "b"\n'
            'residue = "A bake block self-test control."\n'
            '\n[[hook]]\nid = "selftest-warn-bake"\nrules = ["tsthk1"]\nplatform = "claude-code"\n'
            'event = "PreToolUse"\nmatcher = "Bash"\nhandler = "warn_handler"\ndefault = "warn"\n'
            'stage = "bake"\ntimeout = 15\nclass = "b"\nresidue = "A warn bake self-test control."\n'
            '\n[[hook]]\nid = "selftest-stop-block"\nrules = ["tsthk1"]\nplatform = "claude-code"\n'
            'event = "Stop"\nhandler = "stop_handler"\ndefault = "block"\ntimeout = 30\nclass = "b"\n'
            'residue = "A Stop self-test control."\n'
            '\n[[hook]]\nid = "selftest-idle-block"\nrules = ["tsthk1"]\nplatform = "claude-code"\n'
            'event = "TeammateIdle"\nhandler = "idle_handler"\ndefault = "block"\ntimeout = 35\n'
            'class = "b"\nresidue = "A TeammateIdle self-test control."\n'), encoding="utf-8")

        def onfailure_findings(text):
            """The case-33 contract over one rendered hooks.json: per (event, handler), the onFailure
            key's PRESENCE, its value (None where absent, so a rendered JSON null is distinguished from
            an absent key by the presence flag, never conflated by get()) and the timeout, compared as
            a whole map."""
            try:
                rendered = json.loads(text)
            except ValueError as exc:
                return ["unparseable hooks.json: {}".format(exc)]
            got = {}
            for event, entries in rendered.get("hooks", {}).items():
                for e in entries:
                    for h in e.get("hooks", ()):
                        got[(event, h["args"][-1])] = ("onFailure" in h, h.get("onFailure"),
                                                       h.get("timeout"))
            want = {("PreToolUse", "test_handler"): (True, "block", 45),
                    ("PreToolUse", "enforce_handler"): (True, "block", 20),
                    ("PreToolUse", "bake_handler"): (False, None, 25),
                    ("PreToolUse", "warn_handler"): (False, None, 15),
                    ("Stop", "stop_handler"): (False, None, 30),
                    ("TeammateIdle", "idle_handler"): (False, None, 35)}
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
                failures.append("a mutant output dropping onFailure must fail the case-33 contract")
            mutant = json.loads(on_json)
            for entries in mutant["hooks"].values():
                for e in entries:
                    for h in e["hooks"]:
                        h["timeout"] = TIMEOUT
            if not onfailure_findings(json.dumps(mutant, indent=2) + "\n"):
                failures.append("a mutant output resetting the configured timeout must fail the "
                                "case-33 contract")
            mutant = json.loads(on_json)
            for entries in mutant["hooks"].values():
                for e in entries:
                    for h in e["hooks"]:
                        if "onFailure" not in h:
                            h["onFailure"] = None
            if not onfailure_findings(json.dumps(mutant, indent=2) + "\n"):
                failures.append("a mutant output writing an explicit null onFailure where the key "
                                "must be absent must fail the case-33 contract")

        # The onFailure render predicate itself, one clause at a time, with direct render_hooks_json
        # calls: the event, default and stage clauses are each discriminated, including the PreToolUse
        # warn + enforce combination, which load_manifest refuses end-to-end (the default clause is
        # defence in depth behind that refusal, so only a render-level vector can reach it).
        def rendered_onfailure(hook):
            launcher = json.loads(render_hooks_json([hook], on_failure_render=True))[
                "hooks"][hook["event"]][0]["hooks"][0]
            return "onFailure" in launcher
        for label, tweaks, want_key in (("enforce-block", {"stage": "enforce"}, True),
                                        ("implicit-enforce-block", {}, True),
                                        ("bake-block", {"stage": "bake"}, False),
                                        ("enforce-warn", {"default": "warn"}, False),
                                        ("stop-block", {"event": "Stop"}, False)):
            hook = {"id": "selftest-direct", "event": "PreToolUse", "matcher": "Bash",
                    "handler": "direct_handler", "default": "block"}
            hook.update(tweaks)
            if hook["event"] != "PreToolUse":
                del hook["matcher"]
            if rendered_onfailure(hook) != want_key:
                failures.append("render-level onFailure predicate ({}): the key was expected to be "
                                "{}".format(label, "present" if want_key else "absent"))

        # 34. The project settings render is OUTSIDE the switch: with on-failure-render true and
        #     authored per-hook timeouts, .claude/settings.json stays byte-identical to a project tree
        #     authoring neither (always the default timeout, never an onFailure key), while the same
        #     tree's hooks.json must show the switch applied to the SAME control, so this case cannot
        #     pass with a switch that silently failed to apply.
        proj_plain = tmp / "ofr-proj-plain"
        proj_plain.mkdir()
        _build_project_tree(proj_plain)
        proj_on = tmp / "ofr-proj-on"
        proj_on.mkdir()
        _build_project_tree(proj_on)
        _mutate(proj_on, plug_anchor,
                plug_anchor + '\nclaude-code-min-version = "2.1.295"\non-failure-render = true')
        _mutate(proj_on, 'default = "block"', 'default = "block"\ntimeout = 45')
        if run_project(proj_plain, check=False) != 0 or run_project(proj_on, check=False) != 0:
            failures.append("project-settings switch case: both trees expected a clean generate")
        else:
            plain_settings = (proj_plain / PROJECT_SETTINGS_REL).read_text(encoding="utf-8")
            on_settings = (proj_on / PROJECT_SETTINGS_REL).read_text(encoding="utf-8")
            if on_settings != plain_settings:
                failures.append("with on-failure-render true, .claude/settings.json must stay "
                                "byte-identical to a project tree authoring no optional keys")
            if "onFailure" in on_settings or '"timeout": 45' in on_settings:
                failures.append("the project settings must never render an onFailure key or an "
                                "authored timeout")
            on_hooks = (proj_on / HOOKS_JSON_REL).read_text(encoding="utf-8")
            if '"onFailure": "block"' not in on_hooks or '"timeout": 45' not in on_hooks:
                failures.append("project-settings switch case: hooks.json must show the switch "
                                "applied (the authored timeout and onFailure on the project control)")

        # 35. With on-failure-render true, an authored timeout at or below the stub's PreToolUse budget
        #     plus the margin its own deadline line reserves (10 seconds with the stub's lines) fails
        #     closed: it exits 2 with a message naming the hook and the minimum, and one second above it
        #     generates and renders. The boundary values and the minimum are spelled literally (never
        #     built from the module constants). Each is tested just below, exactly at and just above the
        #     boundary on a PreToolUse block hook and on a Stop hook (the rule covers every event); a
        #     stub whose margin is 3.5 moves the budget but not the boundary, and its refusal names the
        #     budget and margin read from that stub, so a boundary built from a literal margin is caught.
        #     The rule's scope is pinned by shape: a warn (bake) hook and a bake-stage block hook, which
        #     never carry onFailure, and a second hook behind a first that authors a fitting 11, are each
        #     refused at 9 and generate at 11, so a check narrowed to block defaults, to the enforce
        #     stage or to the first hook is caught. With the switch false or absent, 9, 10 and 11 still
        #     generate and render the default 10 (the off render is unchanged).
        def fit_case(label, switch_line, timeout, want, event="PreToolUse", margin="2.0", needles=(),
                     shape="block"):
            tree = tmp / ("fit-" + label)
            tree.mkdir()
            _build_tree(tree)
            if switch_line:
                _mutate(tree, plug_anchor, plug_anchor + "\n" + switch_line)
            first_lines = {"block": 'default = "block"', "warn": 'default = "warn"\nstage = "bake"',
                           "bake": 'default = "block"\nstage = "bake"', "second": 'default = "block"'}[shape]
            _mutate(tree, 'default = "block"', first_lines + "\ntimeout = {}".format(
                11 if shape == "second" else timeout))
            if shape == "second":
                hooks_dir = tree / ".aiqt" / "core" / "hooks"
                for path, tail in ((hooks_dir / "manifest.toml", (
                        '\n[[hook]]\nid = "selftest-second"\nrules = ["tsthk1"]\nplatform = "claude-code"\n'
                        'event = "PreToolUse"\nmatcher = "Bash"\nhandler = "second_handler"\n'
                        'default = "block"\ntimeout = {}\nclass = "b"\n'
                        'residue = "A second self-test control."\n'.format(timeout))),
                        (hooks_dir / "scripts" / SCRIPT_NAME,
                         "def second_handler(data):\n    return (0, None, None)\n")):
                    path.write_text(path.read_text(encoding="utf-8") + tail, encoding="utf-8")
            if event != "PreToolUse":
                _mutate(tree, 'event = "PreToolUse"\nmatcher = "Bash"', 'event = "{}"'.format(event))
            if margin != "2.0":
                script = tree / ".aiqt" / "core" / "hooks" / "scripts" / SCRIPT_NAME
                body = script.read_text(encoding="utf-8")
                default_lines = _SCRIPT_DEADLINE.format(timeout=TIMEOUT, margin="2.0")
                if default_lines not in body:
                    failures.append("self-test setup: the stub carries no deadline lines")
                script.write_text(body.replace(default_lines, _SCRIPT_DEADLINE.format(
                    timeout=TIMEOUT, margin=margin)), encoding="utf-8")
            out = io.StringIO()
            with redirect_stdout(out):
                rc = run(tree, False, project_ids=())
            if rc != want:
                failures.append("dispatcher-fit timeout ({}): expected exit {}, got {}".format(label, want, rc))
                return
            for needle in needles:
                if needle not in out.getvalue():
                    failures.append("dispatcher-fit timeout ({}): the refusal must name {!r}, got {!r}"
                                    .format(label, needle, out.getvalue().strip()))
            if want == 0:
                rendered = json.loads((tree / HOOKS_JSON_REL).read_text(encoding="utf-8"))
                handler = "second_handler" if shape == "second" else "test_handler"
                launchers = [h for e in rendered["hooks"][event] for h in e["hooks"] if h["args"][-1] == handler]
                got = launchers[0].get("timeout") if len(launchers) == 1 else launchers
                expected = timeout if switch_line == "on-failure-render = true" else 10
                if got != expected:
                    failures.append("dispatcher-fit timeout ({}): expected a rendered timeout of {}, got {!r}"
                                    .format(label, expected, got))
        switch_on = "on-failure-render = true"
        refusal = ("[[hook]] selftest-control", "minimum authored timeout is 11")
        for label, switch_line, timeout, want, event, margin, needles in (
                ("pre-below", switch_on, 9, 2, "PreToolUse", "2.0", refusal + ("timeout 9 ",)),
                ("pre-at", switch_on, 10, 2, "PreToolUse", "2.0",
                 refusal + ("timeout 10 ", "budget of 8 seconds plus its 2-second margin (10 seconds)")),
                ("pre-above", switch_on, 11, 0, "PreToolUse", "2.0", ()),
                ("stop-below", switch_on, 9, 2, "Stop", "2.0", refusal),
                ("stop-at", switch_on, 10, 2, "Stop", "2.0", refusal),
                ("stop-above", switch_on, 11, 0, "Stop", "2.0", ()),
                ("margin-3.5-below", switch_on, 9, 2, "PreToolUse", "3.5", refusal),
                ("margin-3.5-at", switch_on, 10, 2, "PreToolUse", "3.5",
                 refusal + ("budget of 6.5 seconds plus its 3.5-second margin (10 seconds)",)),
                ("margin-3.5-above", switch_on, 11, 0, "PreToolUse", "3.5", ()),
                ("off-below", "on-failure-render = false", 9, 0, "PreToolUse", "2.0", ()),
                ("off-at", "on-failure-render = false", 10, 0, "PreToolUse", "2.0", ()),
                ("off-above", "on-failure-render = false", 11, 0, "PreToolUse", "2.0", ()),
                ("absent-below", None, 9, 0, "PreToolUse", "2.0", ()),
                ("absent-at", None, 10, 0, "PreToolUse", "2.0", ()),
                ("absent-above", None, 11, 0, "PreToolUse", "2.0", ())):
            fit_case(label, switch_line, timeout, want, event, margin, needles)
        for label, shape, timeout, want, needles in (
                ("warn-below", "warn", 9, 2, refusal + ("timeout 9 ",)),
                ("warn-above", "warn", 11, 0, ()),
                ("bake-below", "bake", 9, 2, refusal + ("timeout 9 ",)),
                ("bake-above", "bake", 11, 0, ()),
                ("second-below", "second", 9, 2,
                 ("[[hook]] selftest-second", "timeout 9 ", "minimum authored timeout is 11")),
                ("second-above", "second", 11, 0, ())):
            fit_case(label, switch_on, timeout, want, needles=needles, shape=shape)
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
                            "failures".format(_exc_text(exc)))
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
    # Each proof clause is claimed ONLY when the checks behind it (cases 22 to 29) actually
    # ran: a nested suite skips them all, so its PASS line claims none of their proof, and a
    # runner with no os.killpg skips case 24, so the group-kill sentence is dropped there too
    # (the NOTE above names the skip). The wording states what the suite itself RAN; that
    # each guarded regression goes red was shown by mutation runs outside this suite, so the
    # PASS line claims no revert was executed here.
    group_proof = ("a timed-out child is killed with its whole process group (a started "
                   "grandchild included) and its TMPDIR verified removed, "
                   if _case24_ran else "")
    sigkill_proof = (", the cleanup's SIGKILL and never a natural exit"
                     if hasattr(signal, "SIGKILL") else "")
    rootlink_proof = ("" if "28 root-symlink-unremovable" in skipped else
                      " (a root symlink is never walked, also when its unlink fails)")
    proof = ("" if _nested else
             "; a child with symlink construction refused fails with the named NOT RUN entry, an "
             "ordinary write failure in a planter propagates as itself, " + group_proof +
             "an injected communicate OSError and KeyboardInterrupt each kill, REAP (exit "
             "status collected" + sigkill_proof + ") and clean up the live child before "
             "re-raising, a secondary cleanup failure is a named entry that never replaces "
             "the original exception, even one whose own __str__ or __repr__ raises or "
             "whose render returns a raising str subclass from the str fallback or the "
             "repr fallback (the guarded helper renders an "
             "exact built-in str with a constant fallback; its render vectors inject at "
             "the stage-3 close, at the leftover removal and at the spawn launch "
             "diagnostic), a non-timeout wait failure "
             "is a named entry on both stage-2 branches (alone and after a non-timeout "
             "drain failure), a child whose exit status was already collected is not "
             "signalled again by the staged cleanup, an injected fault's entry is removed "
             "only by the identity of the recorded fault that originated it at a declared "
             "stage, never by its text (a genuine failure carrying an injected token stays "
             "a failure in cases 27 and 28), a case-27 rescue or final-collection "
             "failure is a genuine named failure kept structurally apart from the injected "
             "entries and never discarded by the expected-error filter, each rescue and "
             "final-collection step is guarded against any exception (a RuntimeError or "
             "KeyboardInterrupt from the final kill still leaves the bounded final wait to "
             "collect the child) and every rescue and final-collection wait carries "
             "timeout=10, a recorded child "
             "without a collected returncode after that filter is a named failure (so no "
             "case passes "
             "with a live child), an absence check whose lstat is refused is a named "
             "cannot-verify failure driven through the real call sites while the ordinary "
             "case runs, a fault in the final "
             "suite removal is "
             "printed beside the escaping original instead of replacing it, "
             "and the removal helper neither follows a symlink out of its tree" +
             rootlink_proof + ", nor chmods "
             "an entry it cannot classify (a named failure instead), nor counts "
             "an unverifiable absence as removed, up to the disclosed replacement race "
             "between an entry's lstat classification and the chmod or walk that follows")
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
          "below TIMEOUT (each end refused just outside it), with no second binding of either name; "
          "the repository-local "
          ".claude/settings.json renders one isolated PreToolUse group with the manifest matcher, is "
          "drift-checked by run(), stays out of build_desired(), and fails closed without its control, "
          "in the authoring tree only: an adopter-shaped tree never gets, loses or changes a "
          ".claude/settings.json whatever the cwd, and a malformed authoring marker (a FIFO, an empty, a non-UTF-8 "
          "and a one-byte-oversize one included) fails closed while one of exactly the size bound is accepted; "
          "a marker fixture that cannot be planted is a named NOT RUN failure" + proof + ", and the "
          "FIFO case is time-bounded so a blocking regression is a reported failure, never a hang; an "
          "authored per-hook timeout is accepted only as an integer from TIMEOUT_MIN to TIMEOUT_MAX "
          "(each end refused just outside it, bool/string/float refused), with the optional [plugin] "
          "keys validated fail-closed and the version strings strict MAJOR.MINOR.PATCH (a non-string, "
          "a missing component, a trailing newline, a leading zero and non-ASCII digits each refused); "
          "with on-failure-render false, and separately with the key absent, nothing new renders "
          "(hooks.json stays byte-identical to a baseline pinned as spelled-out bytes, no onFailure "
          "anywhere); with it true every launcher carries its configured timeout and exactly the "
          "enforce-stage PreToolUse block hooks carry onFailure 'block' (a bake-stage block hook "
          "renders its authored timeout without it, so the stage clause is discriminated on its own), "
          "a presence-aware contract shown discriminating against a mutant that drops the key, one "
          "that resets the timeout and one that writes an explicit null, with each render-predicate "
          "clause (event, default, stage) also discriminated by direct render vectors; and the "
          "project .claude/settings.json stays byte-identical whatever the switch, while the same "
          "tree's hooks.json shows the switch applied; with the switch on, an authored timeout at or "
          "below the dispatcher's budget plus its own margin is refused naming the hook and the "
          "minimum (just below, at and just above the boundary, on a PreToolUse and a Stop hook and "
          "under a moved margin), a warn hook, a bake-stage hook and a second hook each refused at 9 "
          "and generating at 11, while with it off or absent 9, 10 and 11 still render the default"
          + note)
    return 0


if __name__ == "__main__":
    sys.exit(main())
