#!/usr/bin/env python3
"""OPF adoption enable-hook merge core (PR3 slice 1): a PURE, NO-WRITE structured-merge library.

The `enable-hook` op row (the closed ADOPT_OPS vocabulary in _opf_adopt) writes executable-on-load
assistant-platform configuration, so its merge computation is built and reviewed BEFORE anything can
write one (the shipped pin at that op row: "a structured JSON merge, re-emitted byte-exact, never a
blind append"). This module carries exactly that computation and nothing that executes: it parses a
registration file as structured JSON, computes the merged model inserting exactly the pinned
`plugin_entry`, re-emits deterministically, and returns (old_bytes, new_bytes, new_digest) with NO
filesystem write, journal transaction, subprocess, or network use. The op wiring (verify old_digest
against live bytes, require the produced bytes to hash to the plan's approval-bound new_digest,
publish through the journal `write` primitive, reversal restoring the prior bytes) is the next slice.

v1 registration family: the Claude Code `settings.json`-family shape ONLY (the canonical operand in
_opf_adopt is `.claude/settings.json`). Any other format or shape refuses fail-closed; further
platform adapters land with the enforcement pack only after their denial semantics are verified
against official platform documentation at build time (the enforcement pack's build-time
denial-verification rule: a platform whose semantics cannot be so verified gets no enable-hook write
at all and falls to the instructions tier). The
recognized top-level keyset, the hook-event vocabulary, and the insertion shape below are therefore
v1 pins, re-verified at that build-time check before any live pack ships.

THREAT MODEL (carried here per the op row's pin; the gates-manifest residue repeats the residuals):
whatever the registration file registers executes with the adopter's FULL USER AUTHORITY on the
platform's next load, and the file usually already carries the adopter's own security controls.
  1. Malicious or wrong registration content (privilege escalation). A plan row must never register
     arbitrary command text. `plugin_entry` is a token naming a member of the digest-verified
     installed pack (the plant-governance trust gate); the merge inserts exactly that pinned token
     and nothing else; the exact post-merge bytes are pinned by the plan's `new_digest`, which the
     single approval binds transitively through `plan_digest`, so the adopter's one informed yes
     covers the precise executable registration byte-exact. The approval presentation must quote
     what will execute and when (on the platform's next load, not at apply).
  2. Corruption of the adopter's existing configuration. A blind append or regex splice can produce
     unparseable JSON, silently disabling the adopter's EXISTING hooks, including their own
     guardrails. Mitigation: structured parse -> model merge -> deterministic re-emit; anything
     unrecognized refuses rather than merges; the emitted bytes are verified to reparse to exactly
     the prior model plus the one inserted entry (the record writer's canonical-bytes discipline).
  3. TOCTOU / drifted preimage. The file changed between plan and apply. Mitigation is the op
     wiring's `old_digest` precondition on live bytes (drift refuses into a fresh plan, never a
     merge over unknown content); this library is pure over the bytes it is handed.
  4. Path substitution (traversal, symlink, special-file operands). Mitigation is upstream and
     downstream of this pure library: the contained-filepath grammar refuses traversal, absolute
     and control-character forms, and publication goes through the journal's fd-bound no-follow
     apply. This module never touches a path at all.
  5. Content-level injection. Control characters or line separators in an entry value as a
     lexer-splitting vector. Mitigation: `_opf_adopt._is_token`'s no-control rule over the whole
     code-point space applied to the candidate entry AND to every command already registered, plus
     structured JSON emission whose escaping is the serializer's (never string splicing); the
     no-control rule, not ASCII escaping, is what keeps separator code points out of commands.
  6. Execution at write time. This module and the op write configuration only; nothing here
     invokes a hook, spawns the platform, or tests activation in-process. Activation happens on the
     platform's next load, and the approval surfacing states that.
  7. Reversal limits. The op's reversal restores the prior registration bytes, but a platform that
     already loaded the merged configuration is not "un-executed" by restoring the file. Disclosed.
  8. Disclosed residuals: the registered hook runs with the same user's authority, so same-user
     tampering and OS isolation are outside this design (the _journal posture); platform
     hook-startup failures may fall through to normal permissions (the enforcement-pack residual
     table this unit inherits); per-clone installation and later hand edits to the registration
     file after adoption are outside the op; an unverifiable platform gets no enable-hook write
     (above); until the adoption trust gate lands, pack authenticity rests on `manifest_sha256`
     alone (no second, out-of-band digest is consulted), so a forged pack is the standing
     self-asserted-identity residual.

Outcome model (the _opf_adopt mapping, single-sourced from _opf_store): CANNOT-EVALUATE for input
this library cannot decide under v1 assumptions (undecodable/unparseable bytes, a duplicate JSON
key, a non-finite constant or a number outside the finite double range, a JSON null anywhere in the
recognized hooks surface (null is refused by name, never read as absence and never crashed on), a
wrong container type, an out-of-vocabulary closed token such as an unknown hook event or hook type,
an oversize file, a merged emission that would exceed the same size bound); INVALID for a decodable
table that violates the closed v1 schema (an unknown key, a wrong-typed or control-character-
carrying field, a malformed candidate entry, an existing conflicting entry). Both refuse; neither
yields bytes to write.

Reformatting disclosure (the "re-emitted byte-exact" pin, honestly bounded): the verified no-op
returns the adopter file's own bytes untouched, and a refusal returns no bytes at all. A CHANGED
merge re-emits the whole file deterministically, which preserves every string's TEXT verbatim
(UTF-8 output, no \\uXXXX rewriting of non-ASCII) and every number's exact source lexeme (1E2
stays 1E2, 1.000 stays 1.000), but normalizes what remains: key order (sorted), whitespace
(two-space indent, one trailing newline), and string escape SPELLINGS (an escaped "\\u00e9" in the
source re-emits as the raw character; control characters take the serializer's escape). The
emitted bytes are additionally held to the same MAX_REGISTRATION_BYTES bound as the input, so an
accepted output always no-ops, byte-identically, on its next merge. The plan's new_digest pins the
exact post-merge bytes, so the one approval covers this normalization byte-exactly.

Offline, stdlib only, fail-closed. It lives under `opf/tools/` and imports ONLY sibling `opf/tools/`
modules (the shared outcome model from `_opf_store`; `_opf_adopt` for the single-sourced token rule
and the op-row seam the self-test reconciles), so the standalone-closure property holds.

Exit convention (the repo's gates and the sibling OPF units): 0 clean, 1 a finding, 2 cannot-evaluate.
"""
import hashlib
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _opf_adopt as schema  # noqa: E402  (single source: token rule, digest grammar, the op-row seam)
from _opf_store import VALID, INVALID, CANNOT_EVALUATE  # noqa: E402


# --- the closed v1 vocabulary of the one supported registration family --------------------------------

REGISTRATION_FAMILY = "claude-settings-json"
# An adopter registration file larger than this refuses (a representative resource bound, not a sandbox).
MAX_REGISTRATION_BYTES = 1024 * 1024

# The closed v1 hook-event vocabulary of the settings.json family. An event outside this set is an
# out-of-vocabulary closed token (CANNOT-EVALUATE), never a best-effort merge around it.
HOOK_EVENTS = ("PreToolUse", "PostToolUse", "UserPromptSubmit", "Notification",
               "Stop", "SubagentStop", "SessionStart", "SessionEnd", "PreCompact")
# The one v1 hook-entry type. The registered value is a command string the platform runs on load.
HOOK_TYPES = ("command",)
# Where v1 inserts the governance entry: a PreToolUse group matching every tool ("*"), so the
# registered pack member sees each tool call and carries the deny decision itself; a narrower matcher
# would silently exempt tools and is a PR6 (enforcement-pack) decision, not this library's.
ENTRY_EVENT = "PreToolUse"
ENTRY_MATCHER = "*"

# The closed v1 RECOGNIZED top-level keyset: `hooks` is the structured surface this library merges
# into; the rest are recognized-opaque (preserved through the model, never interpreted). The set is
# deliberately SMALL: an adopter file carrying any other key refuses fail-closed until the
# enforcement-pack build-time doc verification widens the recognized set additively (never here).
TOP_LEVEL_KEYS = ("$schema", "env", "hooks", "model", "permissions")
# Closed keysets inside the structured surface.
_GROUP_REQUIRED = ("hooks",)
_GROUP_OPTIONAL = ("matcher",)
_ENTRY_REQUIRED = ("command", "type")
_ENTRY_OPTIONAL = ("timeout",)


# --- result carrier (the _opf_adopt AdoptValidation idiom, plus the computed bytes) --------------------

class HookMergeResult:
    """status/findings as in _opf_adopt; on VALID, new_bytes/new_digest carry the exact bytes the op
    wiring publishes (and the plan pins as new_digest), and `changed` is False for the verified no-op
    of an already-merged file. On any refusal, new_bytes/new_digest/changed are None."""

    __slots__ = ("status", "findings", "old_bytes", "new_bytes", "new_digest", "changed")

    def __init__(self, status, findings=None, old_bytes=None, new_bytes=None,
                 new_digest=None, changed=None):
        self.status = status
        self.findings = findings or []
        self.old_bytes = old_bytes
        self.new_bytes = new_bytes
        self.new_digest = new_digest
        self.changed = changed


def _cannot(msg, old_bytes=None):
    return HookMergeResult(CANNOT_EVALUATE, [msg], old_bytes=old_bytes)


def _invalid(findings, old_bytes=None):
    return HookMergeResult(INVALID, list(findings), old_bytes=old_bytes)


def _digest(data):
    return "sha256:" + hashlib.sha256(data).hexdigest()


# --- fail-closed parse and deterministic emission ------------------------------------------------------

class _ParseRefusal(ValueError):
    pass


def _no_duplicate_pairs(pairs):
    """A duplicate JSON key is a smuggling vector (parsers keep the last silently, so a re-emission
    would drop the earlier one); refuse rather than pick."""
    table = dict()
    for key, value in pairs:
        if key in table:
            raise _ParseRefusal("duplicate key {!r}".format(key))
        table[key] = value
    return table


def _no_constant(name):
    raise _ParseRefusal("non-finite JSON constant {!r}".format(name))


class _Number(str):
    """A JSON number carried as its exact source lexeme (a str subclass), so a changed merge
    re-emits the adopter's own number spellings verbatim (1E2 stays 1E2, 1.000 stays 1.000). Every
    field check that needs a parsed STRING excludes this type (_is_json_string)."""

    __slots__ = ()


def _parse_number(lexeme):
    """parse_float/parse_int hook: keep the exact lexeme; refuse a value outside the finite double
    range (json.loads would otherwise read 1e400 as Infinity, sidestepping parse_constant), so no
    later path, merge or no-op, ever sees a non-finite parsed number."""
    if not math.isfinite(float(lexeme)):
        raise _ParseRefusal("JSON number {!r} is outside the finite double range".format(lexeme))
    return _Number(lexeme)


def _parse(raw):
    """Strict JSON parse of registration bytes. Raises _ParseRefusal (also for undecodable bytes, a
    number outside the finite double range, and parser recursion exhaustion); callers map that to
    CANNOT-EVALUATE. Numbers come back as _Number lexemes (never a lossy float), and the merge and
    no-op paths share this one parse, so both refuse the same inputs."""
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=_no_duplicate_pairs,
                          parse_constant=_no_constant, parse_float=_parse_number,
                          parse_int=_parse_number)
    except (ValueError, RecursionError) as exc:  # ValueError covers JSONDecodeError/UnicodeDecodeError
        raise _ParseRefusal("registration bytes are not strict JSON: {}".format(exc)) from None


def _emit(model):
    """Deterministic emission: sorted keys, two-space indent, one trailing newline, UTF-8 output.
    Strings are escaped by the json serializer with ensure_ascii=False, so the adopter's non-ASCII
    text is preserved verbatim (never rewritten to \\uXXXX, which used to triple non-ASCII files
    and break the size-bound idempotence); _Number lexemes re-emit exactly as parsed. Emission is a
    fixed point under _parse for any model _parse can produce. Raises _ParseRefusal on a value no
    parsed model can contain (a non-finite float, a non-string key, a foreign type); the residual
    reformatting of a changed merge is disclosed in the module docstring."""
    out = []
    _emit_value(model, 0, out)
    out.append("\n")
    try:
        return "".join(out).encode("utf-8")
    except UnicodeEncodeError:
        # only reachable via an ESCAPED unpaired surrogate in the source (raw surrogate bytes are
        # not valid UTF-8 and already refuse at decode); refuse rather than emit undecodable bytes.
        raise _ParseRefusal("string contains an unpaired surrogate (not encodable as UTF-8)") from None


def _emit_value(value, depth, out):
    if isinstance(value, _Number):
        out.append(str(value))
    elif isinstance(value, str):
        out.append(json.dumps(value, ensure_ascii=False))
    elif value is True:
        out.append("true")
    elif value is False:
        out.append("false")
    elif value is None:
        out.append("null")
    elif isinstance(value, (int, float)):
        # only code-built models (the self-test's) reach here; parsed numbers are _Number lexemes.
        if isinstance(value, float) and not math.isfinite(value):
            raise _ParseRefusal("non-finite number in the model")
        out.append(json.dumps(value))
    elif isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise _ParseRefusal("non-string table key in the model")
        if not value:
            out.append("{}")
            return
        pad = "  " * (depth + 1)
        out.append("{\n")
        for i, key in enumerate(sorted(value)):
            out.append(pad + json.dumps(key, ensure_ascii=False) + ": ")
            _emit_value(value[key], depth + 1, out)
            out.append(",\n" if i + 1 < len(value) else "\n")
        out.append("  " * depth + "}")
    elif isinstance(value, list):
        if not value:
            out.append("[]")
            return
        pad = "  " * (depth + 1)
        out.append("[\n")
        for i, item in enumerate(value):
            out.append(pad)
            _emit_value(item, depth + 1, out)
            out.append(",\n" if i + 1 < len(value) else "\n")
        out.append("  " * depth + "]")
    else:
        raise _ParseRefusal("unserializable value of type {!r}".format(type(value).__name__))


# --- the closed v1 shape validation --------------------------------------------------------------------

def _single_line(value):
    """A str with no control character in the whole code-point space; unlike the token rule it allows
    the empty string (a documented settings.json matcher form meaning match-all)."""
    return value == "" or schema._is_token(value)


def _is_json_string(value):
    """A parsed JSON string: a str that is not the _Number lexeme carrier (which subclasses str), so
    a number-typed field can never satisfy a string-typed check."""
    return isinstance(value, str) and not isinstance(value, _Number)


def _is_json_number(value):
    """A parsed JSON number (_Number, finite by construction) or, for code-built models such as the
    self-test's, a finite non-bool int/float."""
    if isinstance(value, _Number):
        return True
    if isinstance(value, bool):
        return False
    return isinstance(value, (int, float)) and math.isfinite(value)


def _null_refusal(value, where):
    """JSON null in the recognized hooks surface: refused by NAME (CANNOT-EVALUATE), never read as
    absence and never crashed on; every check in this module keeps absent distinct from null by
    testing key membership, never .get(). Returns the refusal verdict, or None when not null."""
    if value is None:
        return schema.AdoptValidation(
            CANNOT_EVALUATE,
            ["{} is JSON null (null is refused, never read as absence)".format(where)])
    return None


def validate_registration_model(model):
    """Validate an already-parsed registration model against the closed v1 shape. Returns an
    _opf_adopt.AdoptValidation. Structural wrongness and out-of-vocabulary closed tokens short-circuit
    CANNOT-EVALUATE; schema violations accumulate to INVALID; only a fully recognized shape is VALID
    (never a best-effort read around an unrecognized part)."""
    if not isinstance(model, dict):
        return schema.AdoptValidation(CANNOT_EVALUATE, ["registration top level is not a table"])
    findings = []
    if any(not isinstance(k, str) for k in model):
        return schema.AdoptValidation(INVALID, ["registration carries a non-string top-level key"])
    for key in sorted(model):
        if key not in TOP_LEVEL_KEYS:
            findings.append("unknown top-level key {!r} (the closed v1 recognized set; an "
                            "unrecognized registration refuses, never a best-effort merge)".format(key))
    if "hooks" in model:
        hooks = model["hooks"]
        verdict = _null_refusal(hooks, "hooks")
        if verdict is not None:
            return verdict
        if not isinstance(hooks, dict):
            return schema.AdoptValidation(CANNOT_EVALUATE, ["hooks is not a table"])
        if any(not isinstance(k, str) for k in hooks):
            return schema.AdoptValidation(INVALID, ["hooks carries a non-string event key"])
        for event in sorted(hooks):
            if event not in HOOK_EVENTS:
                return schema.AdoptValidation(
                    CANNOT_EVALUATE, ["unknown hook event {!r} (closed v1 vocabulary)".format(event)])
            groups = hooks[event]
            verdict = _null_refusal(groups, "hooks[{!r}]".format(event))
            if verdict is not None:
                return verdict
            if not isinstance(groups, list):
                return schema.AdoptValidation(
                    CANNOT_EVALUATE, ["hooks[{!r}] is not an array".format(event)])
            for i, group in enumerate(groups):
                where = "hooks[{!r}][{}]".format(event, i)
                verdict = _validate_group(group, where, findings)
                if verdict is not None:
                    return verdict
    if findings:
        return schema.AdoptValidation(INVALID, findings)
    return schema.AdoptValidation(VALID, [])


def _validate_group(group, where, findings):
    """Validate one matcher group in place; returns a short-circuit AdoptValidation for structural /
    out-of-vocabulary / null wrongness, else None with schema findings appended to `findings`."""
    verdict = _null_refusal(group, where)
    if verdict is not None:
        return verdict
    if not isinstance(group, dict):
        return schema.AdoptValidation(CANNOT_EVALUATE, ["{} is not a table".format(where)])
    if any(not isinstance(k, str) for k in group):
        findings.append("{} carries a non-string key".format(where))
        return None
    for key in sorted(group):
        if key not in _GROUP_REQUIRED + _GROUP_OPTIONAL:
            findings.append("{} unknown key {!r}".format(where, key))
    for key in _GROUP_REQUIRED:
        if key not in group:
            findings.append("{} missing required key {!r}".format(where, key))
            return None
    if "matcher" in group:
        matcher = group["matcher"]
        verdict = _null_refusal(matcher, "{} matcher".format(where))
        if verdict is not None:
            return verdict
        if not (_is_json_string(matcher) and _single_line(matcher)):
            findings.append("{} matcher is not a single-line string".format(where))
    entries = group["hooks"]
    verdict = _null_refusal(entries, "{} hooks".format(where))
    if verdict is not None:
        return verdict
    if not isinstance(entries, list):
        return schema.AdoptValidation(CANNOT_EVALUATE, ["{} hooks is not an array".format(where)])
    if not entries:
        findings.append("{} hooks is empty".format(where))
    for j, entry in enumerate(entries):
        ewhere = "{}.hooks[{}]".format(where, j)
        verdict = _null_refusal(entry, ewhere)
        if verdict is not None:
            return verdict
        if not isinstance(entry, dict):
            return schema.AdoptValidation(CANNOT_EVALUATE, ["{} is not a table".format(ewhere)])
        if any(not isinstance(k, str) for k in entry):
            findings.append("{} carries a non-string key".format(ewhere))
            continue
        for key in sorted(entry):
            if key not in _ENTRY_REQUIRED + _ENTRY_OPTIONAL:
                findings.append("{} unknown key {!r}".format(ewhere, key))
        missing = [key for key in _ENTRY_REQUIRED if key not in entry]
        if missing:
            findings.append("{} missing required key(s): {}".format(ewhere, ", ".join(missing)))
            continue
        verdict = _null_refusal(entry["type"], "{} type".format(ewhere))
        if verdict is not None:
            return verdict
        if entry["type"] not in HOOK_TYPES:
            return schema.AdoptValidation(
                CANNOT_EVALUATE,
                ["{} type {!r} outside the closed v1 vocabulary".format(ewhere, entry["type"])])
        # the command executes on load: the candidate-entry no-control rule applies to EVERY
        # registered command, so a control character can never ride a re-emission (threat model 5).
        verdict = _null_refusal(entry["command"], "{} command".format(ewhere))
        if verdict is not None:
            return verdict
        if not (_is_json_string(entry["command"]) and schema._is_token(entry["command"])):
            findings.append("{} command fails the no-control token rule".format(ewhere))
        if "timeout" in entry:
            timeout = entry["timeout"]
            verdict = _null_refusal(timeout, "{} timeout".format(ewhere))
            if verdict is not None:
                return verdict
            if not _is_json_number(timeout):
                findings.append("{} timeout is not a number".format(ewhere))
    return None


# --- the merge ------------------------------------------------------------------------------------------

def canonical_hook_group(plugin_entry):
    """The exact matcher group v1 inserts: match-all, one command entry whose command is EXACTLY the
    pinned `plugin_entry` token (a name into the digest-verified installed pack), nothing else. The
    plan's new_digest pins the whole post-merge file, so the one approval binds these bytes."""
    return {"hooks": [{"command": plugin_entry, "type": "command"}], "matcher": ENTRY_MATCHER}


def _entry_occurrences(model, plugin_entry):
    """Every (event, group) in the recognized hooks surface whose entries register plugin_entry.
    Runs only on a VALIDATED model, so `hooks` is a table when present (never null) and every entry
    carries `command`; absent stays distinct from null throughout."""
    occurrences = []
    hooks = model["hooks"] if "hooks" in model else dict()
    for event, groups in hooks.items():
        for group in groups:
            if any(entry["command"] == plugin_entry for entry in group["hooks"]):
                occurrences.append((event, group))
    return occurrences


def merge_registration(old_bytes, plugin_entry):
    """Compute the merged registration for `old_bytes` + `plugin_entry`, purely. No filesystem write:
    the caller (the enable-hook op wiring, next slice) publishes new_bytes through the journal only
    after the plan's approval-bound new_digest matches. Merging an already-merged file is a verified
    no-op (changed False, new_bytes the file's OWN bytes verbatim, never a re-emission); an
    existing conflicting registration of the same entry, any unrecognized format or shape, JSON null
    in the recognized hooks surface, a number outside the finite double range, and a merged emission
    that would exceed MAX_REGISTRATION_BYTES all refuse fail-closed. The no-op path and the merge
    path share the same parse and validation, so they refuse the same inputs."""
    if isinstance(old_bytes, bytearray):
        old_bytes = bytes(old_bytes)
    if not isinstance(old_bytes, bytes):
        return _cannot("registration input is not bytes")
    if len(old_bytes) > MAX_REGISTRATION_BYTES:
        return _cannot("registration exceeds {} bytes".format(MAX_REGISTRATION_BYTES),
                       old_bytes=old_bytes)
    if not schema._is_token(plugin_entry):
        return _invalid(["plugin_entry fails the no-control token rule"], old_bytes=old_bytes)
    try:
        model = _parse(old_bytes)
    except _ParseRefusal as exc:
        return _cannot(str(exc), old_bytes=old_bytes)
    verdict = validate_registration_model(model)
    if verdict.status is not VALID:
        return HookMergeResult(verdict.status, verdict.findings, old_bytes=old_bytes)

    wanted = canonical_hook_group(plugin_entry)
    occurrences = _entry_occurrences(model, plugin_entry)
    if occurrences:
        if occurrences == [(ENTRY_EVENT, wanted)]:
            # the verified no-op: the exact canonical group is already registered, once; nothing to
            # write, so the file's own bytes (not a re-emission) are the result.
            return HookMergeResult(VALID, [], old_bytes=old_bytes, new_bytes=old_bytes,
                                   new_digest=_digest(old_bytes), changed=False)
        return _invalid(["an existing conflicting registration of {!r} (present, but not exactly "
                         "the canonical v1 group under {!r})".format(plugin_entry, ENTRY_EVENT)],
                        old_bytes=old_bytes)

    # model merge: reparse a fresh copy (never mutate the validated model), append the one group.
    merged = _parse(old_bytes)
    merged.setdefault("hooks", dict()).setdefault(ENTRY_EVENT, []).append(wanted)
    try:
        new_bytes = _emit(merged)
        if len(new_bytes) > MAX_REGISTRATION_BYTES:
            return _cannot("merged registration would exceed {} bytes (the same bound the input is "
                           "held to, so an accepted output always no-ops on its next merge)".format(
                               MAX_REGISTRATION_BYTES), old_bytes=old_bytes)
        # verification, fail-closed (threat model 2): the emitted bytes reparse to exactly the prior
        # model plus the one inserted entry, and emission is a fixed point (byte-exact re-emission).
        reparsed = _parse(new_bytes)
        stripped = _parse(new_bytes)
    except (_ParseRefusal, RecursionError) as exc:
        return _cannot("merge verification failed: {}".format(exc), old_bytes=old_bytes)
    tail = stripped["hooks"][ENTRY_EVENT]
    if not tail or tail[-1] != wanted:
        return _cannot("merge verification failed: inserted entry not found", old_bytes=old_bytes)
    tail.pop()
    prior_hooks = model["hooks"] if "hooks" in model else dict()
    if not tail and ENTRY_EVENT not in prior_hooks:
        # the event list exists only because the merge created it. An ORIGINALLY PRESENT empty
        # event list is adopter content: it stays, so stripping reproduces the prior model.
        del stripped["hooks"][ENTRY_EVENT]
        if stripped["hooks"] == dict() and "hooks" not in model:
            del stripped["hooks"]
    if reparsed != merged or stripped != model or _emit(reparsed) != new_bytes:
        return _cannot("merge verification failed: emission is not the prior model plus exactly "
                       "the one entry", old_bytes=old_bytes)
    return HookMergeResult(VALID, [], old_bytes=old_bytes, new_bytes=new_bytes,
                           new_digest=_digest(new_bytes), changed=True)


# --- self-test -------------------------------------------------------------------------------------------

def canonical_registration():
    """A minimal VALID adopter registration model of the v1 family: an opaque schema pointer and
    permissions table, plus one existing hook of the adopter's own (which every merge must preserve)."""
    return {
        "$schema": "https://json.schemastore.org/claude-code-settings.json",
        "permissions": {"deny": ["Read(./secrets/**)"]},
        "hooks": {
            "PostToolUse": [
                {"hooks": [{"command": "tools/fmt-check.sh", "type": "command", "timeout": 60}],
                 "matcher": "Write|Edit"},
            ],
        },
    }


# The self-test purity harness's module-level half: ONE audit hook per process (audit hooks cannot
# be removed once added), inert unless self_test arms it around a harnessed merge. The enforced net
# is exactly: the audited event named "open" plus every audited event under the namespaces below;
# the os-level entry points CPython does NOT audit (os.stat among them) are denied by mock patches
# inside self_test instead. Nothing broader is claimed or enforced by the harness.
_PURITY_HARNESS = dict(installed=False, armed=False, effects=[])
_PURITY_EVENT_PREFIXES = ("os.", "socket.", "subprocess.", "shutil.", "glob.", "tempfile.")


def _purity_audit(event, args):
    if _PURITY_HARNESS["armed"] and (event == "open"
                                     or event.startswith(_PURITY_EVENT_PREFIXES)):
        _PURITY_HARNESS["effects"].append(event)
        raise RuntimeError(
            "purity harness refused audited event {} during merge_registration".format(event))


def self_test():
    """Fail-closed invariants over synthetic vectors, judged on returned statuses and bytes, never by
    grepping output. The representative successful merge and no-op run under a REFUSING purity
    harness whose exact boundary is: the audit net above (_purity_audit) plus mock denials for
    os.stat/os.lstat/os.access/os.open/io.open/builtins.open/socket.socket/subprocess.Popen. That
    enumerated double net is what the harness enforces; anything outside it (a raw ctypes syscall,
    say) is excluded by review of this module's imports, not by the harness."""
    failures = []
    checked = [0]

    def check(name, cond):
        checked[0] += 1
        if not cond:
            failures.append(name)

    entry = "opf-governance"
    old = _emit(canonical_registration())

    # 0: vocabulary and seam consistency. The insertion pins are inside their own closed sets, and the
    # op row this library serves still declares exactly the inputs the wiring slice will bind.
    check("entry-event-in-vocab", ENTRY_EVENT in HOOK_EVENTS)
    check("events-unique", len(set(HOOK_EVENTS)) == len(HOOK_EVENTS))
    check("top-keys-unique-and-recognize-hooks",
          len(set(TOP_LEVEL_KEYS)) == len(TOP_LEVEL_KEYS) and "hooks" in TOP_LEVEL_KEYS)
    op = schema.ADOPT_OPS_BY_NAME["enable-hook"]
    check("op-seam-inputs", set(op.required_inputs) ==
          {"registration_path", "plugin_entry", "old_digest", "new_digest"})
    check("op-seam-journal-write", op.journal == ("write",))
    check("op-seam-canonical-operand",
          schema.canonical_op("enable-hook")["registration_path"] == ".claude/settings.json")

    # 1: the successful merge, computed under the refusing purity harness (the docstring above and
    # the module-level comment state its exact boundary; a denial is BOTH recorded and raised, so a
    # harnessed merge that touches the boundary comes back refused with the effect on record).
    import io
    import os
    import socket
    import subprocess
    from unittest import mock

    if not _PURITY_HARNESS["installed"]:
        sys.addaudithook(_purity_audit)
        _PURITY_HARNESS["installed"] = True

    def deny(name, record):
        def _deny(*args, **kwargs):
            record.append(name)
            raise RuntimeError("purity harness refused {} during merge_registration".format(name))
        return _deny

    def merge_under_harness(raw, token):
        effects = []
        _PURITY_HARNESS["effects"] = effects
        with mock.patch.object(os, "stat", deny("os.stat", effects)), \
                mock.patch.object(os, "lstat", deny("os.lstat", effects)), \
                mock.patch.object(os, "access", deny("os.access", effects)), \
                mock.patch.object(os, "open", deny("os.open", effects)), \
                mock.patch.object(io, "open", deny("io.open", effects)), \
                mock.patch("builtins.open", deny("open", effects)), \
                mock.patch.object(socket, "socket", deny("socket.socket", effects)), \
                mock.patch.object(subprocess, "Popen", deny("subprocess.Popen", effects)):
            _PURITY_HARNESS["armed"] = True
            try:
                result = merge_registration(raw, token)
            except RuntimeError as exc:
                result = HookMergeResult(CANNOT_EVALUATE, [str(exc)])
            finally:
                _PURITY_HARNESS["armed"] = False
        return result, effects

    merged, effects = merge_under_harness(old, entry)
    check("merge-valid", merged.status is VALID and not merged.findings)
    check("merge-no-effects", effects == [])
    if merged.new_bytes is None:
        # the representative merge itself refused (an impure or broken merge): every later section
        # depends on its bytes, so report red NOW, still by status and exit code, never by raising.
        print("OPF-ADOPT-HOOK SELF-TEST: FAIL (representative merge refused; "
              "{} checks run)".format(checked[0]))
        for f in failures:
            print("  FAILED: {}".format(f))
        return 1
    check("merge-changed", merged.changed is True and merged.new_bytes != old)
    check("merge-echoes-old-bytes", merged.old_bytes == old)
    check("merge-digest-grammar",
          isinstance(merged.new_digest, str) and bool(schema._DIGEST_RE.match(merged.new_digest))
          and merged.new_digest == _digest(merged.new_bytes))
    model = _parse(merged.new_bytes)
    check("merge-inserts-exactly-the-entry",
          model["hooks"][ENTRY_EVENT] == [canonical_hook_group(entry)])
    # the adopter's own configuration is preserved value-for-value (threat model 2); the reference
    # is the PARSED form of the input, whose numbers are _Number lexemes like the merged model's.
    del model["hooks"][ENTRY_EVENT]
    check("merge-preserves-prior-model", model == _parse(old))

    # 2: byte-exactness. Emission is a FIXED POINT: merge -> emit -> reparse -> re-emit is stable.
    check("emit-fixed-point", _emit(_parse(merged.new_bytes)) == merged.new_bytes)
    two = merge_registration(old, entry)
    check("merge-deterministic",
          (two.new_bytes, two.new_digest) == (merged.new_bytes, merged.new_digest))

    # 2b: preservation of untouched adopter content (threat model 2). Non-ASCII text re-emits as
    # raw UTF-8 (never \uXXXX-rewritten) and number lexemes re-emit exactly as spelled; only
    # whitespace, key order, and escape spellings normalize (the module-docstring disclosure).
    fancy_raw = b'{"env":{"note":"caf\xc3\xa9 \xce\xbb","n":1E2,"f":1.000,"tiny":1e-10,"neg":-0}}'
    fancy = merge_registration(fancy_raw, entry)
    check("preserve-valid", fancy.status is VALID and fancy.changed is True)
    check("preserve-utf8-raw", fancy.new_bytes is not None
          and b"caf\xc3\xa9 \xce\xbb" in fancy.new_bytes and b"\\u" not in fancy.new_bytes)
    check("preserve-number-lexemes", fancy.new_bytes is not None
          and b"1E2" in fancy.new_bytes and b"1.000" in fancy.new_bytes
          and b"1e-10" in fancy.new_bytes and b"-0" in fancy.new_bytes
          and b"100.0" not in fancy.new_bytes)
    check("preserve-fixed-point", fancy.new_bytes is not None
          and _emit(_parse(fancy.new_bytes)) == fancy.new_bytes)
    # an ESCAPED unpaired surrogate parses as JSON but cannot re-emit as UTF-8: refused, not raised.
    surrogate = merge_registration(b'{"env":{"x":"\\ud800"}}', entry)
    check("unpaired-surrogate-refuses",
          surrogate.status is CANNOT_EVALUATE and surrogate.new_bytes is None
          and any("surrogate" in f for f in surrogate.findings))
    # merge(merge(x)) is a VERIFIED no-op even for non-ASCII-heavy input: without ASCII escaping
    # the output stays near the input's size (inside the same byte bound), so the second merge
    # returns the first merge's bytes verbatim instead of refusing an oversize re-emission.
    heavy = _emit(dict(env=dict(x="\u00e9" * 180000)))
    grown_heavy = merge_registration(heavy, entry)
    check("heavy-utf8-first-merge-valid", grown_heavy.status is VALID
          and grown_heavy.new_bytes is not None
          and len(grown_heavy.new_bytes) <= MAX_REGISTRATION_BYTES
          and len(grown_heavy.new_bytes) < len(heavy) + 4096)
    twice = merge_registration(grown_heavy.new_bytes, entry)
    check("heavy-utf8-second-merge-no-op", twice.status is VALID and twice.changed is False
          and twice.new_bytes == grown_heavy.new_bytes)

    # 3: idempotence. Merging an already-merged file is a VERIFIED NO-OP: the file's own bytes come
    # back unchanged (no re-emission), changed is False, and the digest is of those same bytes.
    again = merge_registration(merged.new_bytes, entry)
    check("idempotent-valid", again.status is VALID)
    check("idempotent-no-op",
          again.changed is False and again.new_bytes == merged.new_bytes
          and again.new_digest == merged.new_digest)

    # 3b: the no-op also holds under the purity harness, and for NONCANONICAL already-merged bytes
    # (unsorted keys, raw UTF-8, spelled lexemes) the file's OWN bytes come back verbatim: the
    # no-op path never re-emits, so it can never reformat adopter content.
    noop_h, noop_effects = merge_under_harness(merged.new_bytes, entry)
    check("no-op-under-harness", noop_h.status is VALID and noop_h.changed is False
          and noop_effects == [])
    noncanon = (b'{"model":"caf\xc3\xa9","hooks":{"PreToolUse":[{"hooks":'
                b'[{"command":"opf-governance","type":"command"}],"matcher":"*"}]},'
                b'"env":{"n":1E2}}')
    kept = merge_registration(noncanon, entry)
    check("noncanonical-no-op-verbatim",
          kept.status is VALID and kept.changed is False and kept.new_bytes == noncanon)

    # 4: a registration with no hooks table at all gains exactly the one entry.
    bare = _emit({"permissions": dict()})
    grown = merge_registration(bare, entry)
    check("merge-creates-hooks", grown.status is VALID and
          _parse(grown.new_bytes)["hooks"] == {ENTRY_EVENT: [canonical_hook_group(entry)]})

    # 4b: an ORIGINALLY PRESENT empty event list is recognized adopter content: a pre-existing
    # empty ENTRY_EVENT list gains the one entry in place (this used to refuse as a false
    # verification failure), and another event's empty-list spelling survives the merge.
    had_empty = merge_registration(_emit(dict(hooks={ENTRY_EVENT: []})), entry)
    check("empty-entry-event-merges", had_empty.status is VALID and had_empty.changed is True
          and had_empty.new_bytes is not None
          and _parse(had_empty.new_bytes)["hooks"] == {ENTRY_EVENT: [canonical_hook_group(entry)]})
    other_empty = merge_registration(_emit(dict(hooks=dict(Stop=[]))), entry)
    expected_hooks = dict()
    expected_hooks["Stop"] = []
    expected_hooks[ENTRY_EVENT] = [canonical_hook_group(entry)]
    check("other-empty-event-preserved", other_empty.status is VALID
          and other_empty.new_bytes is not None
          and _parse(other_empty.new_bytes)["hooks"] == expected_hooks)

    # 5: THE foreign-registration flip: an unrecognized top-level key refuses INVALID, never a
    # best-effort merge; an unknown hook event and an unknown entry type are out-of-vocabulary closed
    # tokens, CANNOT-EVALUATE. Each refusal returns no bytes to write.
    foreign = canonical_registration()
    foreign["mcpServers"] = {"x": {"command": "server"}}
    r = merge_registration(_emit(foreign), entry)
    check("foreign-top-key-invalid", r.status is INVALID and r.new_bytes is None)
    alien_event = canonical_registration()
    alien_event["hooks"]["OnBoot"] = []
    check("unknown-event-cannot-eval",
          merge_registration(_emit(alien_event), entry).status is CANNOT_EVALUATE)
    alien_type = canonical_registration()
    alien_type["hooks"]["PostToolUse"][0]["hooks"][0]["type"] = "script"
    check("unknown-type-cannot-eval",
          merge_registration(_emit(alien_type), entry).status is CANNOT_EVALUATE)
    alien_group_key = canonical_registration()
    alien_group_key["hooks"]["PostToolUse"][0]["run"] = "x"
    check("unknown-group-key-invalid",
          merge_registration(_emit(alien_group_key), entry).status is INVALID)
    alien_entry_key = canonical_registration()
    alien_entry_key["hooks"]["PostToolUse"][0]["hooks"][0]["env"] = dict()
    check("unknown-entry-key-invalid",
          merge_registration(_emit(alien_entry_key), entry).status is INVALID)

    # 5b: the in-merge preservation/fixed-point verification is DISCRIMINATING. Under an emitter
    # that silently drops an untouched adopter key (a corruption the inserted-entry check alone
    # cannot see), the merge must refuse and return no bytes; removing the verification guard in
    # merge_registration turns exactly this red.
    real_emit = _emit

    def _corrupt_emit(value):
        if isinstance(value, dict) and "permissions" in value:
            value = dict(value)
            del value["permissions"]
        return real_emit(value)

    try:
        globals()["_emit"] = _corrupt_emit
        broken = merge_registration(old, entry)
    finally:
        globals()["_emit"] = real_emit
    check("corrupt-emission-refuses",
          broken.status is CANNOT_EVALUATE and broken.new_bytes is None)

    # 6: injection vectors: a control character anywhere in the candidate entry refuses (threat
    # model 5), across the whole code-point space; so does one in an EXISTING registered command.
    for bad in ("a\nb", "a\tb", "a\x7fb", "a\x85b", "a\N{LINE SEPARATOR}b", "", None, 7):
        check("entry-injection-{!r}-invalid".format(bad),
              merge_registration(old, bad).status is INVALID)
    ctrl_cmd = canonical_registration()
    ctrl_cmd["hooks"]["PostToolUse"][0]["hooks"][0]["command"] = "run\N{PARAGRAPH SEPARATOR}it"
    check("existing-command-control-invalid",
          merge_registration(_emit(ctrl_cmd), entry).status is INVALID)

    # 6b: JSON null anywhere in the recognized hooks surface refuses CANNOT-EVALUATE with a NAMED
    # finding; null is never read as absence and never crashes, while genuinely ABSENT optional
    # keys stay valid (the absent-vs-null distinction) and number-typed strings stay refused.
    null_vectors = (
        ("hooks", b'{"hooks":null}'),
        ("event", b'{"hooks":{"Stop":null}}'),
        ("group", b'{"hooks":{"Stop":[null]}}'),
        ("matcher", b'{"hooks":{"Stop":[{"matcher":null,"hooks":'
                    b'[{"command":"x","type":"command"}]}]}}'),
        ("entries", b'{"hooks":{"Stop":[{"hooks":null}]}}'),
        ("entry", b'{"hooks":{"Stop":[{"hooks":[null]}]}}'),
        ("command", b'{"hooks":{"Stop":[{"hooks":'
                    b'[{"command":null,"type":"command"}]}]}}'),
        ("type", b'{"hooks":{"Stop":[{"hooks":'
                 b'[{"command":"x","type":null}]}]}}'),
        ("timeout", b'{"hooks":{"Stop":[{"hooks":[{"command":"x","type":"command",'
                    b'"timeout":null}]}]}}'))
    for name, raw in null_vectors:
        r = merge_registration(raw, entry)
        check("null-{}-cannot-eval".format(name),
              r.status is CANNOT_EVALUATE and r.new_bytes is None
              and any("null" in f for f in r.findings))
    bare_group = merge_registration(
        b'{"hooks":{"Stop":[{"hooks":[{"command":"x","type":"command"}]}]}}', entry)
    check("absent-optional-keys-valid", bare_group.status is VALID and bare_group.changed is True)
    num_cmd = merge_registration(
        b'{"hooks":{"Stop":[{"hooks":[{"command":5,"type":"command"}]}]}}', entry)
    check("number-command-invalid", num_cmd.status is INVALID)
    num_matcher = merge_registration(
        b'{"hooks":{"Stop":[{"matcher":5,"hooks":'
        b'[{"command":"x","type":"command"}]}]}}', entry)
    check("number-matcher-invalid", num_matcher.status is INVALID)
    str_timeout = merge_registration(
        b'{"hooks":{"Stop":[{"hooks":[{"command":"x","type":"command",'
        b'"timeout":"60"}]}]}}', entry)
    check("string-timeout-invalid", str_timeout.status is INVALID)

    # 7: unparseable / structurally undecidable registrations are CANNOT-EVALUATE, bytes-typed input
    # is required, and the representative resource bound refuses an oversize file.
    for name, raw in (("not-json", b"hooks = []\n"), ("top-level-array", b"[]\n"),
                      ("top-level-string", b'"x"\n'), ("not-utf8", b"\xff{}"),
                      ("duplicate-key", b'{"hooks": {}, "hooks": {}}\n'),
                      ("non-finite", b'{"env": NaN}\n'), ("empty", b"")):
        check("unparseable-{}-cannot-eval".format(name),
              merge_registration(raw, entry).status is CANNOT_EVALUATE)
    check("nonbytes-input-cannot-eval", merge_registration("{}", entry).status is CANNOT_EVALUATE)
    check("bytearray-input-accepted", merge_registration(bytearray(old), entry).status is VALID)
    check("oversize-cannot-eval",
          merge_registration(b" " * (MAX_REGISTRATION_BYTES + 1), entry).status is CANNOT_EVALUATE)

    # 7b: numeric overflow refuses at parse on BOTH paths (they share the one parse), never an
    # uncaught exception and never VALID; and a merged emission that would exceed the input's own
    # byte bound refuses up front instead of emitting a file the next merge would refuse.
    overflow_vectors = (
        ("env-1e400", b'{"env":{"x":1e400}}'),
        ("timeout-neg-1e999", b'{"hooks":{"Stop":[{"hooks":[{"command":"x",'
                              b'"type":"command","timeout":-1e999}]}]}}'),
        ("huge-int", b'{"env":{"x":' + b"9" * 400 + b'}}'))
    for name, raw in overflow_vectors:
        r = merge_registration(raw, entry)
        check("overflow-{}-cannot-eval".format(name),
              r.status is CANNOT_EVALUATE and r.new_bytes is None)
    already_1e400 = (b'{"hooks":{"PreToolUse":[{"hooks":[{"command":"opf-governance",'
                     b'"type":"command"}],"matcher":"*"}]},"env":{"x":1e400}}')
    r = merge_registration(already_1e400, entry)
    check("overflow-no-op-path-cannot-eval", r.status is CANNOT_EVALUATE and r.new_bytes is None)
    near = b'{"env":{"x":"' + b"a" * (MAX_REGISTRATION_BYTES - 60) + b'"}}'
    check("near-limit-input-inside-bound", len(near) <= MAX_REGISTRATION_BYTES)
    over = merge_registration(near, entry)
    check("merged-output-over-bound-refuses",
          over.status is CANNOT_EVALUATE and over.new_bytes is None
          and any("exceed" in f for f in over.findings))

    # 8: an existing CONFLICTING registration of the same entry refuses: same command under another
    # matcher, under another event, or beside extra entries is never treated as already-merged.
    conflict_matcher = canonical_registration()
    conflict_matcher["hooks"][ENTRY_EVENT] = [
        {"hooks": [{"command": entry, "type": "command"}], "matcher": "Bash"}]
    check("conflict-other-matcher-invalid",
          merge_registration(_emit(conflict_matcher), entry).status is INVALID)
    conflict_event = canonical_registration()
    conflict_event["hooks"]["Stop"] = [canonical_hook_group(entry)]
    check("conflict-other-event-invalid",
          merge_registration(_emit(conflict_event), entry).status is INVALID)
    conflict_extra = canonical_registration()
    conflict_extra["hooks"][ENTRY_EVENT] = [
        {"hooks": [{"command": entry, "type": "command"},
                   {"command": "other", "type": "command"}], "matcher": ENTRY_MATCHER}]
    check("conflict-extra-entry-invalid",
          merge_registration(_emit(conflict_extra), entry).status is INVALID)

    # 9: shape violations inside the recognized surface are INVALID; a matcher may be the documented
    # empty (match-all) string; a bool timeout is not a number.
    no_cmd = canonical_registration()
    del no_cmd["hooks"]["PostToolUse"][0]["hooks"][0]["command"]
    check("missing-command-invalid", merge_registration(_emit(no_cmd), entry).status is INVALID)
    no_hooks_key = canonical_registration()
    del no_hooks_key["hooks"]["PostToolUse"][0]["hooks"]
    check("missing-group-hooks-invalid",
          merge_registration(_emit(no_hooks_key), entry).status is INVALID)
    empty_entries = canonical_registration()
    empty_entries["hooks"]["PostToolUse"][0]["hooks"] = []
    check("empty-group-hooks-invalid",
          merge_registration(_emit(empty_entries), entry).status is INVALID)
    bad_matcher = canonical_registration()
    bad_matcher["hooks"]["PostToolUse"][0]["matcher"] = "a\nb"
    check("control-matcher-invalid",
          merge_registration(_emit(bad_matcher), entry).status is INVALID)
    empty_matcher = canonical_registration()
    empty_matcher["hooks"]["PostToolUse"][0]["matcher"] = ""
    check("empty-matcher-valid", merge_registration(_emit(empty_matcher), entry).status is VALID)
    bool_timeout = canonical_registration()
    bool_timeout["hooks"]["PostToolUse"][0]["hooks"][0]["timeout"] = True
    check("bool-timeout-invalid",
          merge_registration(_emit(bool_timeout), entry).status is INVALID)
    wrong_hooks = _emit({"hooks": []})
    check("hooks-not-a-table-cannot-eval",
          merge_registration(wrong_hooks, entry).status is CANNOT_EVALUATE)
    wrong_groups = _emit({"hooks": {"PreToolUse": dict()}})
    check("groups-not-an-array-cannot-eval",
          merge_registration(wrong_groups, entry).status is CANNOT_EVALUATE)

    # 9b: non-table groups/entries and a non-array entries value are structural CANNOT-EVALUATE;
    # each vector turns red if its isinstance guard in _validate_group is removed (the remaining
    # closed-keyset walk would misread the value and refuse INVALID instead).
    group_not_table = _emit(dict(hooks=dict(PostToolUse=["x"])))
    check("group-not-a-table-cannot-eval",
          merge_registration(group_not_table, entry).status is CANNOT_EVALUATE)
    entry_not_table = _emit(dict(hooks=dict(PostToolUse=[dict(hooks=["x"], matcher="*")])))
    check("entry-not-a-table-cannot-eval",
          merge_registration(entry_not_table, entry).status is CANNOT_EVALUATE)
    entries_not_array = _emit(dict(hooks=dict(PostToolUse=[dict(hooks="", matcher="*")])))
    check("entries-not-an-array-cannot-eval",
          merge_registration(entries_not_array, entry).status is CANNOT_EVALUATE)

    # 10: validate_registration_model is exposed for the wiring slice; adversarial already-parsed
    # models refuse, never raise (the sibling 9a discipline).
    check("model-nonstring-key-invalid",
          validate_registration_model({0: "x", "hooks": dict()}).status is INVALID)
    check("model-not-a-table-cannot-eval", validate_registration_model([]).status is CANNOT_EVALUATE)
    check("model-hooks-null-cannot-eval",
          validate_registration_model(dict(hooks=None)).status is CANNOT_EVALUATE)
    check("model-canonical-valid",
          validate_registration_model(canonical_registration()).status is VALID)

    if failures:
        print("OPF-ADOPT-HOOK SELF-TEST: FAIL ({} of {} checks failed)".format(
            len(failures), checked[0]))
        for f in failures:
            print("  FAILED: {}".format(f))
        return 1
    print("OPF-ADOPT-HOOK SELF-TEST: PASS ({} merge-core checks)".format(checked[0]))
    return 0


def main():
    args = sys.argv[1:]
    if "--self-test" in args or "--selftest" in args:
        return self_test()
    print("usage: _opf_adopt_hook.py --self-test (a pure library module; the enable-hook op wiring "
          "is a later slice)", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
