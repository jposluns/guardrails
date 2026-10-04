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
     the prior model plus the one inserted entry, under a TYPE-AWARE comparison (a number and a
     same-lexeme string never compare equal; the record writer's canonical-bytes discipline).
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
an oversize file or candidate, a non-bytes input or a foreign-typed model node refused at an
entry gate, a merged emission that would exceed the same size bound, nesting deep enough to
exhaust parser recursion, a CHANGED merge whose re-emission would exhaust emission recursion
(roughly the interpreter recursion limit; an already-merged file of the same depth still no-ops,
because the no-op path returns its own bytes without re-emitting)); INVALID for a decodable
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

Hostile in-process objects (fix 5's premise): the only callers are this repository's own code, and
rounds 4 to 6 showed that hardening every interior site against one more overridable dunder (str
subclasses, a _Number carrier, __bytes__, __repr__, a __class__ spoof) is unbounded. So each PUBLIC
function carries ONE ENTRY GATE instead: merge_registration accepts old_bytes only as EXACTLY bytes
and plugin_entry only as EXACTLY str (length-bounded before any whole-candidate scan);
validate_registration_model walks the model once, iteratively and cycle-safe, and refuses any
foreign-typed node, keys included; canonical_hook_group gates its token the same way. Every gate
refusal is a FIXED message that reads nothing off the refused object and never invokes one of its
methods (no repr, no str, no __bytes__, no type name off a metaclass). Past a gate only
built-in-backed types flow, so the interior exact-type checks CLASSIFY parsed data (str against the
_Number lexeme carrier), never defend against caller-run code.

Offline, stdlib only, fail-closed. It lives under `opf/tools/` and imports ONLY sibling `opf/tools/`
modules (the shared outcome model from `_opf_store`; `_opf_adopt` for the single-sourced token rule
and the op-row seam the self-test reconciles), so the standalone-closure property holds.

Exit convention (the repo's gates and the sibling OPF units): 0 clean, 1 a finding, 2 cannot-evaluate.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    sys.stderr.write(
        "error: _opf_adopt_hook.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
        "Nothing was run (cannot evaluate).\n"
        % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
    raise SystemExit(2)

import hashlib
import json
import math
import re
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _opf_adopt as schema  # noqa: E402  (single source: token rule, digest grammar, the op-row seam)
from _opf_store import VALID, INVALID, CANNOT_EVALUATE  # noqa: E402


# --- the closed v1 vocabulary of the one supported registration family --------------------------------

REGISTRATION_FAMILY = "claude-settings-json"
# An adopter registration file larger than this refuses (a representative resource bound, not a sandbox).
MAX_REGISTRATION_BYTES = 1024 * 1024
# A refusal returns at most this many findings plus one suppression marker (each finding already
# bounds what it repeats via _shown), so a pathological registration cannot amplify a refusal
# into megabytes of accumulated diagnostics. Construction cost stays linear in input size; only
# the returned list is capped.
MAX_FINDINGS = 64

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


# The fixed descriptors _shown returns INSTEAD of formatting a value it must never format, and
# the one placeholder it returns if formatting fails anyway. Each is constant and bounded. The
# malformed-lexeme descriptor names no provenance: _shown cannot tell a parsed _Number from a
# code-built one, so a parsed lexeme that ever reaches it is never labelled code-built.
_SHOWN_INT = "a code-built integer (not shown)"
_SHOWN_MALFORMED_NUMBER = "a malformed number lexeme (not shown)"
_SHOWN_TABLE = "a table (not shown)"
_SHOWN_ARRAY = "an array (not shown)"
_SHOWN_OTHER = "a value of a type this formatter does not show"
_SHOWN_UNFORMATTABLE = "a value that could not be shown"


def _shown(value):
    """Findings and refusal messages embed adopter content; bound what they repeat so a refusal
    never echoes a megabyte lexeme or key back to the caller, and NEVER RAISE, so no finding
    path can turn a refusal into an exception. Dispatch is by type IDENTITY, and only what
    cannot fail or grow is formatted. An exact str or _Number is bounded on the VALUE at EVERY
    length: a long one is truncated on the value before repr and reports the value's own
    length, and a short one comes back whole (its repr is never cut, so no cut can split a repr
    escape sequence and no reported count is ever the repr's); a str stays quoted, and a _Number
    shows as its bare lexeme ONLY when _NUMBER_LEXEME_RE FULLY matches it (every parsed one
    does: the parse hooks hold each lexeme to it), so a number never reads as a string and no
    raw control text reaches a finding; a _Number that does not fully match (only a code-built one
    can while the parse hooks hold) is NEVER formatted and gets the fixed _SHOWN_MALFORMED_NUMBER
    descriptor instead, which names no provenance. An exact float shows its repr, and True,
    False and None show their JSON
    spelling (true, false, null), a few dozen characters at most. An int, a table and an array
    are NEVER formatted and get a fixed descriptor instead: int-to-text conversion raises past
    the interpreter's digit limit, and a container's repr exhausts the stack on deep nesting
    (reachable from exact parsed bytes in the entry type field) and runs to megabytes on a wide
    one. Any other type gets a fixed descriptor too, with no method of it run (the public entry
    gates refuse foreign types before any interior check could hand one here). Any formatting
    failure at all returns the fixed _SHOWN_UNFORMATTABLE placeholder. Worst case stays near
    half a kilobyte (48 characters whose repr escapes are up to ten bytes each, plus the
    suffix), so every embedding message is bounded."""
    try:
        value_type = type(value)
        if value_type is _Number:
            if not _NUMBER_LEXEME_RE.fullmatch(value):
                return _SHOWN_MALFORMED_NUMBER
            if len(value) > 64:
                return "{}... ({} characters)".format(str.__str__(value)[:64], len(value))
            return str.__str__(value)
        if value_type is str:
            if len(value) > 48:
                return "{}... ({} characters)".format(repr(value[:48]), len(value))
            return repr(value)
        if value is None:
            return "null"
        if value is True:
            return "true"
        if value is False:
            return "false"
        if value_type is float:
            return repr(value)
        if value_type is int:
            return _SHOWN_INT
        if value_type is dict:
            return _SHOWN_TABLE
        if value_type is list:
            return _SHOWN_ARRAY
        return _SHOWN_OTHER
    except Exception:
        return _SHOWN_UNFORMATTABLE


# --- fail-closed parse and deterministic emission ------------------------------------------------------

class _ParseRefusal(ValueError):
    pass


class _EmitBoundRefusal(_ParseRefusal):
    """Raised by the emitter's RUNNING byte count the moment an emission would pass
    MAX_REGISTRATION_BYTES, before the over-bound output is built (memory and time stay near the
    bound, never the unbounded output size)."""
    pass


def _no_duplicate_pairs(pairs):
    """A duplicate JSON key is a smuggling vector (parsers keep the last silently, so a re-emission
    would drop the earlier one); refuse rather than pick."""
    table = dict()
    for key, value in pairs:
        if key in table:
            raise _ParseRefusal("duplicate key {}".format(_shown(key)))
        table[key] = value
    return table


def _no_constant(name):
    raise _ParseRefusal("non-finite JSON constant {!r}".format(name))


# The strict-JSON number grammar (RFC 8259), ASCII digits only: exactly what _parse can produce
# (its parse hooks hold every number lexeme to it, whichever json scanner ran), and what _emit
# holds every _Number lexeme to, so a code-built carrier cannot emit bytes _parse would refuse.
_NUMBER_LEXEME_RE = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?\Z")


def _is_finite_lexeme(lexeme):
    """The parser's own number rule over an exact _Number lexeme, single-sourced for the emitter
    and the public validator: the strict-JSON grammar FIRST (so float() only ever reads a
    grammatical lexeme and cannot raise), then the finite double range. Every parsed _Number
    passes; a code-built carrier is held to the same rule, never trusted by its type."""
    return bool(_NUMBER_LEXEME_RE.fullmatch(lexeme)) and math.isfinite(float(lexeme))


class _Number(str):
    """A JSON number carried as its exact source lexeme (a str subclass), so a changed merge
    re-emits the adopter's own number spellings verbatim (1E2 stays 1E2, 1.000 stays 1.000).
    Comparison is TYPE-AWARE: a _Number equals only another _Number with the same lexeme, NEVER a
    plain str, so the in-merge reparse verification sees an emitter that turns a number into a
    same-lexeme string or a digit string into a number (threat model 2). Every field check that
    needs a parsed STRING requires EXACTLY str by type identity (_is_json_string), which
    excludes this type, and the candidate plugin_entry check does the same. repr() shows the
    bare lexeme, never a quoted string, so a finding that embeds a value distinguishes a
    number from a string."""

    __slots__ = ()

    def __eq__(self, other):
        if isinstance(other, _Number):
            return str.__eq__(self, other)
        return False

    def __ne__(self, other):
        # explicit: without this, str.__ne__ inherited through the MRO would compare lexemes.
        return not self.__eq__(other)

    # equal _Numbers hash as their lexeme; colliding with the plain str's hash is harmless.
    __hash__ = str.__hash__

    def __repr__(self):
        # the bare lexeme, exactly how JSON spells the number, so 1 (a number) never displays
        # as '1' (a string) in a finding.
        return str.__str__(self)


def _parse_number(lexeme):
    """parse_float/parse_int hook. It holds the lexeme to the strict ASCII grammar FIRST, so the
    parser's number grammar never rests on which json scanner ran (the C _json scanner reads
    ASCII digits only, but a pure-Python scanner whose pattern spells digits as \\d would admit
    non-ASCII digits, which float() accepts). It then keeps the exact lexeme and refuses a value
    outside the finite double range (json.loads would otherwise read 1e400 as Infinity,
    sidestepping parse_constant), so no later path, merge or no-op, ever sees a malformed or
    non-finite parsed number. The out-of-range finding shows the lexeme bare, as a number."""
    if not _NUMBER_LEXEME_RE.fullmatch(lexeme):
        raise _ParseRefusal("JSON number lexeme is outside the strict ASCII number grammar "
                            "(not shown)")
    if not math.isfinite(float(lexeme)):
        raise _ParseRefusal("JSON number {} is outside the finite double range".format(
            _shown(_Number(lexeme))))
    return _Number(lexeme)


def _parse(raw):
    """Strict JSON parse of registration bytes. Raises _ParseRefusal (also for undecodable bytes, a
    number lexeme outside the strict ASCII grammar or the finite double range, whichever json
    scanner ran, and parser recursion exhaustion); callers map that to CANNOT-EVALUATE. Numbers
    come back as _Number lexemes (never a lossy float), and the merge and no-op paths share this
    one parse, so a parse-level refusal is shared by both."""
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=_no_duplicate_pairs,
                          parse_constant=_no_constant, parse_float=_parse_number,
                          parse_int=_parse_number)
    except (ValueError, RecursionError) as exc:  # ValueError covers JSONDecodeError/UnicodeDecodeError
        raise _ParseRefusal("registration bytes are not strict JSON: {}".format(exc)) from None


# json.dumps(value, ensure_ascii=False) escapes EXACTLY the backslash, the double quote, and the
# C0 controls: \b \t \n \f \r as two-character escapes, every other C0 control as a six-character
# \uXXXX escape; everything else passes through and costs its UTF-8 byte length (surrogatepass,
# matching _emit_piece's count, so a lone escaped surrogate is countable here and still refused
# by _emit's final strict encode).
_TWO_CHAR_ESCAPES = ('\\', '"', '\b', '\t', '\n', '\f', '\r')
_SIX_CHAR_ESCAPES = tuple(chr(c) for c in range(0x20)
                          if chr(c) not in ('\b', '\t', '\n', '\f', '\r'))


def _string_bytes(value):
    """The EXACT UTF-8 byte length json.dumps(value, ensure_ascii=False) serializes `value` to,
    computed WITHOUT serializing (C-speed counting passes, no allocation proportional to the
    escaped form): two quotes, plus the value's own UTF-8 bytes, plus one extra byte per
    two-character escape and five extra per \\uXXXX-escaped control. The emitter's
    pre-serialization budget checks use this, so they are exact, never merely a character-count
    lower bound: a string or key that fits is never refused, and one that cannot fit never
    reaches json.dumps."""
    total = 2 + (len(value) if value.isascii()
                 else len(value.encode("utf-8", "surrogatepass")))
    for ch in _TWO_CHAR_ESCAPES:
        total += value.count(ch)
    if value and min(value) < " ":
        # the 27 rare \uXXXX-escaped controls are counted only when a control is present at
        # all (min() is one C-speed pass; every control sorts below the space character).
        for ch in _SIX_CHAR_ESCAPES:
            total += 5 * value.count(ch)
    return total


def _emit(model):
    """Deterministic emission: sorted keys, two-space indent, one trailing newline, UTF-8 output.
    Strings are escaped by the json serializer with ensure_ascii=False, so the adopter's non-ASCII
    text and KEYS are preserved verbatim (never rewritten to \\uXXXX, which used to triple
    non-ASCII files and break the size-bound idempotence); _Number lexemes re-emit exactly as
    parsed. FIXED-POINT SCOPE, exactly: for every model produced by _parse or by
    merge_registration's own merge step (whose nodes are exact dict/list/str/_Number, True,
    False or None by construction), emission is a fixed point under _parse, enforced rather than
    assumed: every number lexeme is held to the parser's own number grammar and finite double
    range, a parseable model carrying an escaped unpaired surrogate refuses here (below), and
    keys, string values and lexeme carriers are classified by type IDENTITY (exactly str,
    exactly _Number), so a _Number key can never serialize into duplicate-key JSON. A CODE-BUILT
    model (the self-test's literals, a finite exact int/float) emits under the same number rule,
    but the container and numeric branches classify it by isinstance, so a SUBCLASS of
    int/float/dict/list, which only this repository's own code could pass (_emit is
    module-private and the public entry gates refuse foreign-typed models), is NOT covered by
    the fixed-point claim and may emit bytes _parse refuses. The output is held to
    MAX_REGISTRATION_BYTES by a RUNNING byte count (_emit_piece) that refuses the moment the
    bound would pass, so an over-bound emission is never built; a string or key is refused
    BEFORE json.dumps runs whenever its EXACT serialized UTF-8 byte length (_string_bytes:
    quotes plus base bytes plus escape growth, the serializer's own arithmetic) exceeds the
    remaining budget, so a huge or escape-heavy string, the caller's length-gated candidate
    included, never allocates a serialization past the budget, and a value that fits is never
    wrongly refused. Raises _ParseRefusal on all of those and on a value no parsed model can
    contain (a key or value of a foreign type), and RecursionError on nesting near the
    interpreter recursion limit (roughly 1000 levels, dependent on the caller's remaining
    stack); merge_registration maps every one of these to CANNOT-EVALUATE. The residual
    reformatting of a changed merge is disclosed in the module docstring."""
    out = []
    budget = [MAX_REGISTRATION_BYTES]
    _emit_value(model, 0, out, budget)
    _emit_piece("\n", out, budget)
    try:
        return "".join(out).encode("utf-8")
    except UnicodeEncodeError:
        # only reachable via an ESCAPED unpaired surrogate in the source (raw surrogate bytes are
        # not valid UTF-8 and already refuse at decode); refuse rather than emit undecodable bytes.
        raise _ParseRefusal("string contains an unpaired surrogate (not encodable as UTF-8)") from None


def _emit_piece(piece, out, budget):
    """Append one emission piece under the RUNNING output byte budget (the wide-and-deep resource
    fix: 2-byte input array elements each emitting about 2*depth indentation bytes used to build
    the whole over-bound output, hundreds of MiB, before a single length check). ASCII pieces
    count as their length; others by exact UTF-8 length (surrogatepass, so a lone escaped
    surrogate is countable here and still refused by _emit's final strict encode)."""
    budget[0] -= len(piece) if piece.isascii() else len(piece.encode("utf-8", "surrogatepass"))
    if budget[0] < 0:
        raise _EmitBoundRefusal("emission would exceed {} bytes".format(MAX_REGISTRATION_BYTES))
    out.append(piece)


def _emit_value(value, depth, out, budget):
    if type(value) is _Number:
        # held to the parser's own number rule (strict-JSON grammar, finite double range), so a
        # code-built lexeme carrier can never emit bytes _parse would refuse (the fixed point).
        # Exact type: a _Number SUBCLASS is caller-run code and falls through to the
        # unserializable refusal below, never into str(value) on an overridable __str__.
        if not _is_finite_lexeme(value):
            raise _ParseRefusal("number lexeme in the model is not a finite strict-JSON "
                                "number: {}".format(_shown(value)))
        _emit_piece(str(value), out, budget)
    elif type(value) is str:
        # exact type: classification against the _Number carrier (which must emit as a bare
        # lexeme, never quoted); a str subclass, which no parsed or merge-built model can
        # contain, falls through to the unserializable refusal below and never reaches
        # json.dumps. The budget pre-check is EXACT (_string_bytes), so a string that cannot
        # fit, the caller's length-gated candidate and an escape-heavy value included, is
        # refused BEFORE json.dumps materializes any part of its escaped form, and one that
        # fits is never wrongly refused.
        if budget[0] < _string_bytes(value):
            raise _EmitBoundRefusal(
                "emission would exceed {} bytes".format(MAX_REGISTRATION_BYTES))
        _emit_piece(json.dumps(value, ensure_ascii=False), out, budget)
    elif value is True:
        _emit_piece("true", out, budget)
    elif value is False:
        _emit_piece("false", out, budget)
    elif value is None:
        _emit_piece("null", out, budget)
    elif isinstance(value, (int, float)):
        # only code-built models (the self-test's) reach here; parsed numbers are _Number lexemes.
        # Held to the parser's finite double range (math.isfinite raises OverflowError for an int
        # outside it), so emission stays a fixed point under _parse for code-built numbers too.
        try:
            finite = math.isfinite(value)
        except OverflowError:
            finite = False
        if not finite:
            raise _ParseRefusal("number outside the finite double range in the model")
        _emit_piece(json.dumps(value), out, budget)
    elif isinstance(value, dict):
        if any(type(key) is not str for key in value):
            # exact type: a _Number key and a same-lexeme str key are DISTINCT model keys that
            # would both serialize as the same JSON string, emitting duplicate-key JSON _parse
            # then refuses. Both refuse here, so emission stays a fixed point for every
            # parsed or merge-built model (the docstring's fixed-point scope).
            raise _ParseRefusal("table key in the model is not exactly a string")
        if not value:
            _emit_piece("{}", out, budget)
            return
        pad = "  " * (depth + 1)
        _emit_piece("{\n", out, budget)
        for i, key in enumerate(sorted(value)):
            if budget[0] < len(pad) + _string_bytes(key) + 2:
                # the key's emitted piece is exactly pad + the key's serialized form + ": ";
                # the EXACT pre-check refuses before json.dumps materializes an over-budget
                # key and never refuses a key line that fits.
                raise _EmitBoundRefusal(
                    "emission would exceed {} bytes".format(MAX_REGISTRATION_BYTES))
            _emit_piece(pad + json.dumps(key, ensure_ascii=False) + ": ", out, budget)
            _emit_value(value[key], depth + 1, out, budget)
            _emit_piece(",\n" if i + 1 < len(value) else "\n", out, budget)
        _emit_piece("  " * depth + "}", out, budget)
    elif isinstance(value, list):
        if not value:
            _emit_piece("[]", out, budget)
            return
        pad = "  " * (depth + 1)
        _emit_piece("[\n", out, budget)
        for i, item in enumerate(value):
            _emit_piece(pad, out, budget)
            _emit_value(item, depth + 1, out, budget)
            _emit_piece(",\n" if i + 1 < len(value) else "\n", out, budget)
        _emit_piece("  " * depth + "]", out, budget)
    else:
        raise _ParseRefusal("unserializable value of type {!r}".format(type(value).__name__))


# --- the closed v1 shape validation --------------------------------------------------------------------

def _single_line(value):
    """A str with no control character in the whole code-point space; unlike the token rule it allows
    the empty string (a documented settings.json matcher form meaning match-all)."""
    return value == "" or schema._is_token(value)


def _is_json_string(value):
    """A parsed JSON string: EXACTLY str by type identity, never isinstance. Past the public
    entry gates only exact types flow, so this is pure CLASSIFICATION of parsed data: it keeps
    the _Number lexeme carrier out of every string-typed check (a number-typed field can never
    satisfy one). It stays type-identity so the classification cannot drift if a non-gated
    private path is ever added."""
    return type(value) is str


def _is_json_number(value):
    """A JSON number: an exact _Number whose lexeme passes the parser's own number rule
    (_is_finite_lexeme: strict-JSON grammar, then finite double range), or, for code-built
    models such as the self-test's, a finite non-bool int/float. A parsed _Number always
    passes; a code-built carrier the gate admits by type ("NaN", "1e400", "007", any
    non-number text) is refused here rather than trusted as finite by construction."""
    if type(value) is _Number:
        return _is_finite_lexeme(value)
    if isinstance(value, bool):
        return False
    if not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        # a code-built int outside the double range (10**400 say): refuse as not a v1 number,
        # never raise (the exposed-validator "refuse, never raise" discipline).
        return False


def _null_refusal(value, where):
    """JSON null in the recognized hooks surface: refused by NAME (CANNOT-EVALUATE), never read as
    absence and never crashed on; every check in this module keeps absent distinct from null by
    testing key membership, never .get(). Returns the refusal verdict, or None when not null."""
    if value is None:
        return schema.AdoptValidation(
            CANNOT_EVALUATE,
            ["{} is JSON null (null is refused, never read as absence)".format(where)])
    return None


# The ONE fixed finding a gate returns for a foreign-typed model node. Deliberately CONSTANT: it
# names no type and reads nothing off the refused object (no repr, no str, no type name off a
# metaclass), so no method of a hostile type ever runs on the refusal path.
_GATE_MODEL_FINDING = ("registration model carries a node of a type the entry gate does not "
                       "admit (exactly dict, list, str, the module's _Number, bool or None, "
                       "the parsed-JSON types, or an int or float in a code-built model; the "
                       "entry gate refuses foreign types unread)")


def _foreign_typed(model):
    """The public validator's ENTRY GATE (fix 5): ONE iterative pre-order walk, no recursion (so
    depth cannot crash it) and visited-id tracking on containers (so a self-referencing
    code-built model terminates instead of hanging), requiring every node, keys included, to be
    of an EXACT admitted type: the parsed-JSON types dict, list, str, the module's own _Number,
    bool and None, plus int and float, which only a code-built model carries (_parse returns
    numbers as _Number). The decision is by IDENTITY alone (`is` on type(node), which a
    __class__ spoof cannot fool and which never invokes a method, a metaclass __eq__ included;
    None, True and False by object identity, and bool cannot be subclassed). Past this gate only
    built-in-backed types flow, so every interior check classifies parsed data instead of
    defending against caller-run code. Returns True when a foreign-typed node is present."""
    stack = [model]
    seen = set()
    while stack:
        node = stack.pop()
        if node is None or node is True or node is False:
            continue
        node_type = type(node)
        if node_type is dict or node_type is list:
            if id(node) in seen:
                continue
            seen.add(id(node))
            if node_type is dict:
                for key, item in node.items():
                    stack.append(key)
                    stack.append(item)
            else:
                stack.extend(node)
        elif not (node_type is str or node_type is _Number
                  or node_type is int or node_type is float):
            return True
    return False


def validate_registration_model(model):
    """Validate an already-parsed registration model against the closed v1 shape. Returns an
    _opf_adopt.AdoptValidation and never raises on any model its gate admits (every finding
    formats caller content through _shown, which never raises). THE ENTRY GATE comes first
    (fix 5: one gate per public function, never per-dunder hardening): _foreign_typed walks the
    model once and refuses any foreign-typed node, keys included, with the one fixed
    _GATE_MODEL_FINDING and no method of the refused object ever run; past the gate every
    interior check classifies parsed data. Structural wrongness and out-of-vocabulary closed
    tokens short-circuit CANNOT-EVALUATE; schema violations accumulate to INVALID; only a fully
    recognized shape is VALID (never a best-effort read around an unrecognized part). SCOPE:
    this decides the closed v1 shape of the recognized hooks surface; an opaque subtree
    ($schema, env, model, permissions) is gated for type only, so a code-built opaque value
    the emitter refuses (a non-finite float, a key that is not exactly a string) validates
    VALID here and refuses at emission. _parse never produces such a value."""
    if _foreign_typed(model):
        return schema.AdoptValidation(CANNOT_EVALUATE, [_GATE_MODEL_FINDING])
    if not isinstance(model, dict):
        return schema.AdoptValidation(CANNOT_EVALUATE, ["registration top level is not a table"])
    findings = []
    if any(type(k) is not str for k in model):
        return schema.AdoptValidation(
            INVALID, ["registration carries a top-level key that is not exactly a string"])
    for key in sorted(model):
        if key not in TOP_LEVEL_KEYS:
            findings.append("unknown top-level key {} (the closed v1 recognized set; an "
                            "unrecognized registration refuses, never a best-effort merge)".format(
                                _shown(key)))
    if "hooks" in model:
        hooks = model["hooks"]
        verdict = _null_refusal(hooks, "hooks")
        if verdict is not None:
            return verdict
        if not isinstance(hooks, dict):
            return schema.AdoptValidation(CANNOT_EVALUATE, ["hooks is not a table"])
        if any(type(k) is not str for k in hooks):
            return schema.AdoptValidation(
                INVALID, ["hooks carries an event key that is not exactly a string"])
        for event in sorted(hooks):
            if event not in HOOK_EVENTS:
                return schema.AdoptValidation(
                    CANNOT_EVALUATE,
                    ["unknown hook event {} (closed v1 vocabulary)".format(_shown(event))])
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
        if len(findings) > MAX_FINDINGS:
            dropped = len(findings) - MAX_FINDINGS
            findings = findings[:MAX_FINDINGS]
            findings.append("... {} further findings suppressed (a refusal returns at most {} "
                            "findings; the registration refuses regardless)".format(
                                dropped, MAX_FINDINGS))
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
    if any(type(k) is not str for k in group):
        findings.append("{} carries a key that is not exactly a string".format(where))
        return None
    for key in sorted(group):
        if key not in _GROUP_REQUIRED + _GROUP_OPTIONAL:
            findings.append("{} unknown key {}".format(where, _shown(key)))
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
        if any(type(k) is not str for k in entry):
            findings.append("{} carries a key that is not exactly a string".format(ewhere))
            continue
        for key in sorted(entry):
            if key not in _ENTRY_REQUIRED + _ENTRY_OPTIONAL:
                findings.append("{} unknown key {}".format(ewhere, _shown(key)))
        missing = [key for key in _ENTRY_REQUIRED if key not in entry]
        if missing:
            findings.append("{} missing required key(s): {}".format(ewhere, ", ".join(missing)))
            continue
        verdict = _null_refusal(entry["type"], "{} type".format(ewhere))
        if verdict is not None:
            return verdict
        if not _is_json_string(entry["type"]) or entry["type"] not in HOOK_TYPES:
            # classification first: a number-typed field never satisfies a string-typed check.
            # Belt and braces, not a sole guard: _Number's type-aware __eq__ already keeps a
            # _Number("command") out of the HOOK_TYPES membership test, so no vector can tell
            # this exact-type test from an isinstance one; it keeps the classification explicit
            # and independent of _Number's equality.
            return schema.AdoptValidation(
                CANNOT_EVALUATE,
                ["{} type {} outside the closed v1 vocabulary".format(
                    ewhere, _shown(entry["type"]))])
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
                findings.append(
                    "{} timeout is not a number in the finite double range".format(ewhere))
    return None


# --- the merge ------------------------------------------------------------------------------------------

# Any surrogate code point. A str carrying one cannot encode as UTF-8, so no emitted registration
# can carry a candidate that does; both candidate gates refuse it up front, as a candidate defect.
_SURROGATE_RE = re.compile(r"[\ud800-\udfff]")


def canonical_hook_group(plugin_entry):
    """The exact matcher group v1 inserts: match-all, one command entry whose command is EXACTLY the
    pinned `plugin_entry` token (a name into the digest-verified installed pack), nothing else. The
    plan's new_digest pins the whole post-merge file, so the one approval binds these bytes.
    Public, so it carries the same entry gate as merge_registration's candidate half (exactly
    str by type identity, length-bounded, token-clean, free of surrogate code points), raising
    ValueError with a fixed message rather than building a group around a foreign object; on
    merge_registration's own path its gate has already run, so this raise is defense in depth
    for the wiring slice."""
    if (type(plugin_entry) is not str or len(plugin_entry) > MAX_REGISTRATION_BYTES
            or not schema._is_token(plugin_entry) or _SURROGATE_RE.search(plugin_entry)):
        raise ValueError("plugin_entry is not exactly a token string (the entry gate accepts "
                         "only an exact str that passes the no-control token rule and carries "
                         "no surrogate code point)")
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
    after the plan's approval-bound new_digest matches. THE ENTRY GATE comes first (fix 5: one
    gate per public function, never per-dunder hardening): old_bytes must be EXACTLY bytes and
    plugin_entry EXACTLY str, both by type identity, and anything else (a bytearray, a bytes or
    str subclass, a __bytes__ or __class__ spoof) refuses with a FIXED message that reads
    nothing off the refused object and never invokes one of its methods; a candidate longer
    than MAX_REGISTRATION_BYTES, which could never fit in an emitted registration, refuses
    before ANY whole-candidate scan, so candidate scanning is bounded by the registration
    size. Past the gate only built-in types flow. Merging an already-merged file is a verified
    no-op (changed False, new_bytes the file's OWN bytes verbatim, never a re-emission); an
    existing conflicting registration of the same entry, any unrecognized format or shape, JSON null
    in the recognized hooks surface, a number outside the finite double range, a plugin_entry
    failing the no-control token rule or carrying a surrogate code point (refused at the
    candidate gate, never later as an emission refusal), and a merged emission that would exceed
    MAX_REGISTRATION_BYTES (refused by the emitter's running byte count the moment the bound
    would pass, so the over-bound output is never built) all refuse fail-closed. The no-op
    path and the merge path share the same parse and validation, so parse-level and
    shape-level refusals are identical; content that only EMISSION refuses (an escaped
    unpaired surrogate, nesting past the emission recursion depth) still no-ops when the file
    is already merged, because the no-op returns the file's own bytes without re-emitting."""
    if type(old_bytes) is not bytes:
        # THE ENTRY GATE, input half: EXACTLY bytes by type identity, nothing else, and no
        # normalization that runs caller code (bytes(x) invokes an overridable __bytes__, and
        # CPython accepts a bytes-SUBCLASS return from it, so normalizing was an unbounded
        # per-dunder chase; a bytearray or bytes-subclass carrier is likewise refused unread).
        return _cannot("registration input is not exactly bytes (the entry gate accepts exact "
                       "bytes only and refuses anything else unread)")
    if len(old_bytes) > MAX_REGISTRATION_BYTES:
        return _cannot("registration exceeds {} bytes".format(MAX_REGISTRATION_BYTES),
                       old_bytes=old_bytes)
    if type(plugin_entry) is not str:
        # THE ENTRY GATE, candidate half: EXACTLY str by type identity, decided before ANY
        # read of the value (no len(), no scan), with the same fixed-message discipline. A
        # parsed-number lexeme carrier (_Number) would otherwise merge as a bare JSON number
        # this module's own next merge refuses.
        return _invalid(["plugin_entry is not exactly a string (the entry gate accepts exact "
                         "str only and refuses anything else unread)"], old_bytes=old_bytes)
    if len(plugin_entry) > MAX_REGISTRATION_BYTES:
        # a candidate longer than the whole registration bound can never fit in an emitted
        # registration; refusing it HERE bounds every later whole-candidate pass (the token
        # scan below and the emitter's exact byte count) by the registration size.
        return _cannot("plugin_entry exceeds {} bytes (the registration byte bound also "
                       "bounds candidate scanning)".format(MAX_REGISTRATION_BYTES),
                       old_bytes=old_bytes)
    if not schema._is_token(plugin_entry):
        return _invalid(["plugin_entry is not a JSON string passing the no-control token rule"],
                        old_bytes=old_bytes)
    if _SURROGATE_RE.search(plugin_entry):
        # a surrogate code point passes the token rule but cannot encode as UTF-8, so it is
        # refused HERE as a candidate defect, before the no-op test, never later by the
        # emitter, whose refusal would blame the registration.
        return _invalid(["plugin_entry carries a surrogate code point (not encodable as UTF-8, "
                         "so no emitted registration can carry it)"], old_bytes=old_bytes)
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
        return _invalid(["an existing conflicting registration of {} (present, but not exactly "
                         "the canonical v1 group under {!r})".format(
                             _shown(plugin_entry), ENTRY_EVENT)],
                        old_bytes=old_bytes)

    # model merge: reparse a fresh copy (never mutate the validated model), append the one group.
    merged = _parse(old_bytes)
    merged.setdefault("hooks", dict()).setdefault(ENTRY_EVENT, []).append(wanted)
    try:
        new_bytes = _emit(merged)
    except _EmitBoundRefusal:
        return _cannot("merged registration would exceed {} bytes (the same bound the input is "
                       "held to, refused by the emitter's running byte count so the over-bound "
                       "output is never built and an accepted output always no-ops on its next "
                       "merge)".format(MAX_REGISTRATION_BYTES), old_bytes=old_bytes)
    except (_ParseRefusal, RecursionError) as exc:
        # a refusal of the merged model's EMISSION (deep opaque nesting, an escaped unpaired
        # surrogate): no bytes were produced, so nothing was verified, and the message says so.
        return _cannot("merged registration cannot be emitted: {}".format(exc),
                       old_bytes=old_bytes)
    try:
        # verification, fail-closed (threat model 2): the emitted bytes reparse to exactly the prior
        # model plus the one inserted entry, and emission is a fixed point (byte-exact re-emission).
        # The re-emission runs INSIDE this try, so even a fault only a broken emitter could
        # raise on it maps to a refusal, never an uncaught exception.
        reparsed = _parse(new_bytes)
        stripped = _parse(new_bytes)
        re_emitted = _emit(reparsed)
    except (_ParseRefusal, RecursionError) as exc:
        # RecursionError here is defense in depth, with no vector: the reparse and re-emission
        # cover the nesting the first emission above just walked from this same frame without
        # exhausting recursion, so no self-test vector reaches this handler.
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
    # reparsed != merged and stripped != model overlap deliberately (given the tail guard, each
    # alone would refuse this corruption class); both stay as independent defenses, and the
    # re-emission clause pins byte-level canonicality on its own.
    if reparsed != merged or stripped != model or re_emitted != new_bytes:
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
# be removed once added), inert unless self_test arms it around a harnessed merge. The harness is a
# DOUBLE net, enforced exactly and nothing broader: this hook refuses the audited event named
# "open" plus every audited event under the namespaces below; a belt of os-level entry points
# (mostly ones CPython does not audit; open is additionally audit-refused) is denied by
# self_test's mock patches of stat, lstat, access, open, readlink, fstat, statvfs, write and pipe
# on BOTH os and the module os re-exports them from (posix on this platform), plus io.open,
# builtins.open, socket.socket and subprocess.Popen.
# Entry points the net does not cover are excluded by review of this module's imports, not by the
# harness: audited events OUTSIDE the refused namespaces (a ctypes foreign call raises the audited
# ctypes.call_function and ctypes.dlsym events; fcntl.* fires on an already-open descriptor) as
# well as unaudited, unmocked functions (an unlisted os function such as os.getcwd). self_test
# keeps every ENFORCED element honest: one canary per mocked entry point and per refused audit
# namespace (plus the bare open event), refused by exactly its own layer, and negative canaries
# pinning that the excluded events are not refused.
_PURITY_HARNESS = dict(installed=False, armed=False, effects=[])
_PURITY_EVENT_PREFIXES = ("os.", "socket.", "subprocess.", "shutil.", "glob.", "tempfile.")


def _purity_audit(event, args):
    if _PURITY_HARNESS["armed"] and (event == "open"
                                     or event.startswith(_PURITY_EVENT_PREFIXES)):
        _PURITY_HARNESS["effects"].append(event)
        raise RuntimeError(
            "purity harness refused audited event {} during merge_registration".format(event))


def self_test():
    """Fail-closed invariants over synthetic vectors, judged on returned statuses, findings and
    bytes, never by grepping output. The representative successful merge and no-op run under a
    REFUSING purity harness whose exact boundary is: the audit net above (_purity_audit) plus mock
    denials of stat, lstat, access, open, readlink, fstat, statvfs, write and pipe (patched on os
    AND on the module os re-exports them from), and of io.open, builtins.open, socket.socket and
    subprocess.Popen. That enumerated double net is what the harness enforces, and every element
    of it is canary-guarded: one canary per mocked entry point (refused by exactly its own deny),
    one per refused audit namespace plus the bare open event, and one real call per layer.
    Anything outside the net is excluded by review of this module's imports, not by the harness;
    the exclusion covers audited events outside the refused namespaces (a ctypes foreign call's
    audited ctypes.call_function, an fcntl.fcntl call on an already-open descriptor) as well as
    unaudited, unmocked entry points (an unlisted os function), and negative canaries pin that
    such events are indeed not refused."""
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
    import builtins
    import contextlib
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

    # the mock half of the double net: os-level entry points CPython does not audit, denied on
    # BOTH the os module and the module os re-exports them from (posix here), so a posix.stat
    # spelling cannot slip past an os.stat patch; plus the open/socket/subprocess seams.
    os_impl = sys.modules.get(os.name)
    denied = [(io, "open"), (builtins, "open"), (socket, "socket"), (subprocess, "Popen")]
    for fn in ("stat", "lstat", "access", "open", "readlink", "fstat",
               "statvfs", "write", "pipe"):
        denied.append((os, fn))
        if os_impl is not None and hasattr(os_impl, fn):
            denied.append((os_impl, fn))

    def merge_under_harness(raw, token):
        effects = []
        _PURITY_HARNESS["effects"] = effects
        with contextlib.ExitStack() as stack:
            for mod, fn in denied:
                stack.enter_context(mock.patch.object(
                    mod, fn, deny("{}.{}".format(mod.__name__, fn), effects)))
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

    # 1b: each layer of the double net is itself DISCRIMINATING: a deliberately impure merge (a
    # canary read smuggled into emission) must come back REFUSED with the effect on record. One
    # canary per layer: an AUDITED unmocked call (os.listdir) for the audit hook, an unaudited
    # os-module call (os.fstat) for the os mocks, and the re-export spelling for the os_impl
    # mocks. Disabling a layer turns exactly its canary red.
    real_emit = _emit
    canaries = (
        ("audit-hook", "os.listdir", lambda: os.listdir(".")),
        ("os-mock", "os.fstat", lambda: os.fstat(0)),
        ("os-impl-mock", "{}.stat".format(os.name),
         lambda: getattr(sys.modules[os.name], "stat")(__file__)),
    )
    for cname, recorded, impurity in canaries:
        def _impure_emit(value, _impurity=impurity):
            _impurity()
            return real_emit(value)
        try:
            globals()["_emit"] = _impure_emit
            impure, impure_effects = merge_under_harness(old, entry)
        finally:
            globals()["_emit"] = real_emit
        check("impure-canary-{}-refused".format(cname),
              impure.status is CANNOT_EVALUATE and impure.new_bytes is None)
        check("impure-canary-{}-recorded".format(cname), recorded in impure_effects)

    # 1c: EVERY element of the double net is individually canary-guarded, not just one per
    # layer. Each mocked entry point's canary must come back refused BY EXACTLY ITS OWN DENY (its
    # name recorded, its finding returned): reverting any single mock entry turns its canary red,
    # because the real call then either completes (the merge returns VALID) or is refused by the
    # AUDIT layer instead, whose recorded event name and finding text differ from the deny's.
    def _canary_probe(call):
        def _run():
            try:
                call()
            except (OSError, TypeError, ValueError):
                # with a REVERTED mock the real entry point runs; every probe is then harmless (a
                # read of ".", a 0-byte write to stderr, an immediately closed fd) or fails with
                # an ordinary usage error, swallowed so the canary reds on the missing DENIAL and
                # never on a crash; the deny stubs and the audit hook raise RuntimeError, which
                # always propagates.
                pass
        return _run

    entry_probes = {
        "stat": lambda mod: mod.stat("."),
        "lstat": lambda mod: mod.lstat("."),
        "access": lambda mod: mod.access(".", os.R_OK),
        "open": lambda mod: os.close(mod.open(os.devnull, os.O_RDONLY)),
        "readlink": lambda mod: mod.readlink("."),
        "fstat": lambda mod: mod.fstat(0),
        "statvfs": lambda mod: mod.statvfs("."),
        "write": lambda mod: mod.write(2, b""),
        "pipe": lambda mod: [os.close(fd) for fd in mod.pipe()],
    }
    seam_probes = {
        (io, "open"): lambda: io.open(os.devnull, "rb").close(),
        (builtins, "open"): lambda: builtins.open(os.devnull, "rb").close(),
        (socket, "socket"): lambda: socket.socket().close(),
        (subprocess, "Popen"): lambda: subprocess.Popen(None),
    }
    # the EXPECTED boundary is spelled here independently of the harness's own lists, so a
    # narrowed mock list or prefix tuple cannot silently narrow its guards with it.
    net_elements = list(seam_probes)
    for fn in ("stat", "lstat", "access", "open", "readlink", "fstat",
               "statvfs", "write", "pipe"):
        net_elements.append((os, fn))
        if os_impl is not None and hasattr(os_impl, fn):
            net_elements.append((os_impl, fn))
    check("mock-net-covers-exactly-the-stated-boundary", net_elements == denied)
    for mod, fn in net_elements:
        recorded = "{}.{}".format(mod.__name__, fn)
        if (mod, fn) in seam_probes:
            probe = _canary_probe(seam_probes[(mod, fn)])
        else:
            probe = _canary_probe(lambda mod=mod, fn=fn: entry_probes[fn](mod))

        def _entry_impure_emit(value, _probe=probe):
            _probe()
            return real_emit(value)

        try:
            globals()["_emit"] = _entry_impure_emit
            impure, impure_effects = merge_under_harness(old, entry)
        finally:
            globals()["_emit"] = real_emit
        check("mock-entry-canary-{}-refused".format(recorded),
              impure.status is CANNOT_EVALUATE and impure.new_bytes is None)
        check("mock-entry-canary-{}-own-deny".format(recorded),
              impure_effects == [recorded] and impure.findings ==
              ["purity harness refused {} during merge_registration".format(recorded)])

    # 1d: the audit layer's boundary is pinned FROM BOTH SIDES with synthetic events (sys.audit
    # reaches the hook exactly as a real call's event does, with no side effect when a clause
    # under test is reverted). The bare open event and one event per refused namespace must come
    # back refused and recorded (dropping the open clause or narrowing _PURITY_EVENT_PREFIXES
    # turns exactly its canary red); audited events OUTSIDE the boundary (a ctypes foreign call's
    # ctypes.call_function, an fcntl.fcntl call on an open descriptor) must complete VALID,
    # keeping the disclosed import-review exclusion honest.
    check("audit-prefixes-are-exactly-the-stated-boundary",
          _PURITY_EVENT_PREFIXES == ("os.", "socket.", "subprocess.", "shutil.",
                                     "glob.", "tempfile."))
    for event in ("open", "os.canary", "socket.canary", "subprocess.canary", "shutil.canary",
                  "glob.canary", "tempfile.canary"):

        def _audited_emit(value, _event=event):
            sys.audit(_event, "purity-canary")
            return real_emit(value)

        try:
            globals()["_emit"] = _audited_emit
            audited, audited_effects = merge_under_harness(old, entry)
        finally:
            globals()["_emit"] = real_emit
        check("audit-canary-{}-refused".format(event),
              audited.status is CANNOT_EVALUATE and audited.new_bytes is None
              and audited_effects == [event] and audited.findings ==
              ["purity harness refused audited event {} during merge_registration".format(
                  event)])
    for event in ("ctypes.call_function", "fcntl.fcntl"):

        def _outside_emit(value, _event=event):
            sys.audit(_event, "purity-canary")
            return real_emit(value)

        try:
            globals()["_emit"] = _outside_emit
            outside, outside_effects = merge_under_harness(old, entry)
        finally:
            globals()["_emit"] = real_emit
        check("audit-outside-{}-not-refused".format(event),
              outside.status is VALID and outside_effects == [])

    # 2: byte-exactness. Emission is a FIXED POINT: merge -> emit -> reparse -> re-emit is stable.
    check("emit-fixed-point", _emit(_parse(merged.new_bytes)) == merged.new_bytes)
    two = merge_registration(old, entry)
    check("merge-deterministic",
          (two.new_bytes, two.new_digest) == (merged.new_bytes, merged.new_digest))
    # sorted-key emission checked at the BYTE level with an insertion order that differs from
    # sorted order, so dropping the sort in _emit_value turns exactly this red.
    check("emit-sorted-keys-bytes",
          _emit(_parse(b'{"env":{"b":1,"a":2}}'))
          == b'{\n  "env": {\n    "a": 2,\n    "b": 1\n  }\n}\n')

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
    # non-ASCII KEYS are preserved as raw UTF-8 too (the docstring's no-\uXXXX claim covers
    # keys; re-adding ensure_ascii to the key emission turns exactly this red at the byte level).
    fancy_key = merge_registration(b'{"env":{"caf\xc3\xa9":"x"}}', entry)
    check("preserve-utf8-raw-keys", fancy_key.status is VALID and fancy_key.new_bytes is not None
          and b'"caf\xc3\xa9"' in fancy_key.new_bytes and b"\\u" not in fancy_key.new_bytes)
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

    # 5c: the verification is TYPE-AWARE. An emitter that rewrites value TYPES while keeping the
    # lexeme, numbers re-emitted as quoted strings or digit strings re-emitted as bare numbers,
    # must refuse: the reparse comparison sees the change because a _Number never equals a plain
    # str (reverting _Number to plain lexeme equality turns these red); the honest emitter keeps
    # the same inputs VALID.
    check("number-never-equals-string",
          _Number("1") != "1" and "1" != _Number("1") and not _Number("1") == "1"
          and _parse(b'{"env":{"x":"1"}}') != _parse(b'{"env":{"x":1}}'))
    # the comment on _Number.__hash__ is test-enforced (removing the assignment would leave the
    # class unhashable), and repr shows the bare lexeme so findings distinguish 1 from '1'.
    check("number-hashes-as-lexeme",
          _Number.__hash__ is not None and hash(_Number("1")) == hash("1")
          and len({_Number("1"), "1", _Number("1")}) == 2)
    check("number-repr-is-bare-lexeme",
          repr(_Number("1")) == "1" and repr("1") == "'1'"
          and repr(_Number("1")) != repr("1"))

    def _numbers_as_strings(value):
        if isinstance(value, _Number):
            return str(value)  # the exact lexeme, re-emitted as a QUOTED JSON string
        if isinstance(value, dict):
            return {key: _numbers_as_strings(item) for key, item in value.items()}
        if isinstance(value, list):
            return [_numbers_as_strings(item) for item in value]
        return value

    def _digit_strings_as_numbers(value):
        if _is_json_string(value) and value.isdigit():
            return _Number(value)  # the exact characters, re-emitted as a BARE number
        if isinstance(value, dict):
            return {key: _digit_strings_as_numbers(item) for key, item in value.items()}
        if isinstance(value, list):
            return [_digit_strings_as_numbers(item) for item in value]
        return value

    for name, raw, rewrite in (
            ("number-as-string", b'{"env":{"n":1,"f":2.5}}', _numbers_as_strings),
            ("digit-string-as-number", b'{"env":{"PORT":"8080"}}',
             _digit_strings_as_numbers)):
        honest = merge_registration(raw, entry)
        check("type-vector-{}-valid".format(name),
              honest.status is VALID and honest.changed is True)

        def _retyping_emit(value, _rewrite=rewrite):
            return real_emit(_rewrite(value))

        try:
            globals()["_emit"] = _retyping_emit
            retyped = merge_registration(raw, entry)
        finally:
            globals()["_emit"] = real_emit
        check("type-change-{}-refuses".format(name),
              retyped.status is CANNOT_EVALUATE and retyped.new_bytes is None)

    # 5d: the inserted-entry (tail) guard is discriminating on its own NAMED finding: an emitter
    # that silently DROPS the just-inserted entry must refuse with the tail guard's finding
    # (without the guard the combined verification clause still refuses, but with its own finding
    # instead, so this vector pins the guard itself).
    own_first = (b'{"hooks":{"PreToolUse":[{"hooks":[{"command":"adopter-own",'
                 b'"type":"command"}],"matcher":"Bash"}]}}')
    check("own-first-honest-valid", merge_registration(own_first, entry).status is VALID)

    def _drop_inserted_emit(value):
        if (isinstance(value, dict) and "hooks" in value and isinstance(value["hooks"], dict)
                and ENTRY_EVENT in value["hooks"] and value["hooks"][ENTRY_EVENT]):
            value = dict(value)
            value["hooks"] = dict(value["hooks"])
            value["hooks"][ENTRY_EVENT] = value["hooks"][ENTRY_EVENT][:-1]
        return real_emit(value)

    try:
        globals()["_emit"] = _drop_inserted_emit
        dropped = merge_registration(own_first, entry)
    finally:
        globals()["_emit"] = real_emit
    check("dropped-entry-refuses-at-tail-guard",
          dropped.status is CANNOT_EVALUATE and dropped.new_bytes is None
          and any("inserted entry not found" in f for f in dropped.findings))

    # 5e: the byte-level fixed-point clause is discriminating: an emitter whose FIRST emission
    # carries a trailing pad (same model, noncanonical bytes) must refuse; without the
    # _emit(reparsed) != new_bytes clause those noncanonical bytes would ship as VALID.
    pad_once = [b" "]

    def _padded_once_emit(value):
        data = real_emit(value)
        if pad_once:
            return data + pad_once.pop()
        return data

    try:
        globals()["_emit"] = _padded_once_emit
        padded = merge_registration(old, entry)
    finally:
        globals()["_emit"] = real_emit
    check("noncanonical-emission-refuses",
          padded.status is CANNOT_EVALUATE and padded.new_bytes is None)

    # 5f: the verification RE-EMISSION runs INSIDE the merge's try: a fault raised only on the
    # second emission (the re-emission itself) maps to a refusal, never an uncaught exception
    # (moving _emit(reparsed) back outside the try turns exactly this red).
    emit_call_count = [0]

    def _second_emit_faults(value):
        emit_call_count[0] += 1
        if emit_call_count[0] > 1:
            raise _ParseRefusal("re-emission fault (self-test shim)")
        return real_emit(value)

    faulted = None
    fault_escaped = False
    try:
        globals()["_emit"] = _second_emit_faults
        try:
            faulted = merge_registration(old, entry)
        except _ParseRefusal:
            fault_escaped = True
    finally:
        globals()["_emit"] = real_emit
    check("verification-fault-refused-never-raised",
          not fault_escaped and faulted is not None
          and faulted.status is CANNOT_EVALUATE and faulted.new_bytes is None)

    # 6: injection vectors: a control character anywhere in the candidate entry refuses (threat
    # model 5), across the whole code-point space; so does one in an EXISTING registered command.
    for bad in ("a\nb", "a\tb", "a\x7fb", "a\x85b", "a\N{LINE SEPARATOR}b", "", None, 7):
        check("entry-injection-{!r}-invalid".format(bad),
              merge_registration(old, bad).status is INVALID)
    # a parsed-number lexeme carrier (a str subclass) is NOT a genuine candidate string:
    # unchecked it would merge as a bare JSON number the module's own next merge refuses, so
    # the candidate GATE refuses it up front (reverting the gate's exact-type half turns
    # exactly this red).
    numeric_entry = merge_registration(old, _Number("5"))
    check("number-lexeme-entry-invalid",
          numeric_entry.status is INVALID and numeric_entry.new_bytes is None)
    # the candidate gate's refusal is the one FIXED message, decided before any read of the
    # value: no method of the refused object runs (the recorder pins that), and the pinned
    # text discriminates the gate from the per-site check it replaced.
    candidate_gate_finding = ("plugin_entry is not exactly a string (the entry gate accepts "
                              "exact str only and refuses anything else unread)")
    check("number-lexeme-entry-gate-finding",
          numeric_entry.findings == [candidate_gate_finding])
    repr_calls = []

    class _ReprRecordingStr(str):
        __slots__ = ()

        def __repr__(self):
            repr_calls.append("repr")
            return str.__repr__(self)

    hostile_candidate = merge_registration(old, _ReprRecordingStr(entry))
    check("candidate-gate-fixed-message-no-methods",
          hostile_candidate.status is INVALID and hostile_candidate.new_bytes is None
          and hostile_candidate.findings == [candidate_gate_finding] and repr_calls == [])
    gate_raised = False
    try:
        canonical_hook_group(_Number("5"))
    except ValueError:
        gate_raised = True
    check("canonical-group-gate-refuses", gate_raised)
    # the gate's other three guards, each pinned directly: an exact str one character over the
    # registration bound (a clean token, so only the length guard refuses it), a short exact
    # str carrying a control character (only the token guard refuses it) and a short exact str
    # carrying a surrogate code point (a clean token, so only the surrogate guard refuses it).
    # Removing any guard turns exactly its vector red, because the function then returns a
    # group around the value.
    for name, bad in (("overlength", "x" * (MAX_REGISTRATION_BYTES + 1)),
                      ("invalid-token", "a\nb"),
                      ("surrogate", "opf-\ud800")):
        gate_raised = False
        try:
            canonical_hook_group(bad)
        except ValueError:
            gate_raised = True
        check("canonical-group-{}-refuses".format(name), gate_raised)
    # merge_registration refuses the surrogate-carrying candidate at its candidate gate with a
    # candidate finding, on the merge path and on the already-merged path alike. Without that
    # gate alone, both paths reach canonical_hook_group, whose own surrogate guard raises
    # ValueError; the check catches it, so that regression fails this check by name, not by an
    # uncaught exception. Without both guards, the merge path reaches the emitter, whose refusal
    # blames the registration, and the already-merged path no-ops VALID.
    surrogate_finding = ["plugin_entry carries a surrogate code point (not encodable as UTF-8, "
                         "so no emitted registration can carry it)"]
    surrogate_merged = (b'{"hooks":{"PreToolUse":[{"hooks":[{"command":"opf-\\ud800",'
                        b'"type":"command"}],"matcher":"*"}]}}')
    surrogate_results = []
    for raw in (old, surrogate_merged):
        try:
            surrogate_results.append(merge_registration(raw, "opf-\ud800"))
        except ValueError:
            surrogate_results.append(None)
    check("surrogate-candidate-refused-at-gate",
          all(r is not None and r.status is INVALID and r.new_bytes is None
              and r.findings == surrogate_finding for r in surrogate_results))
    ctrl_cmd = canonical_registration()
    ctrl_cmd["hooks"]["PostToolUse"][0]["hooks"][0]["command"] = "run\N{PARAGRAPH SEPARATOR}it"
    check("existing-command-control-invalid",
          merge_registration(_emit(ctrl_cmd), entry).status is INVALID)

    # 6c: EXACT TYPES, one ENTRY GATE per public function (fix 5). A hostile str subclass is
    # caller-run code (an overridden __iter__ hides characters from a token scan, __eq__ fakes
    # an already-merged equality, __repr__ runs inside a refusal message), and rounds 4 to 6
    # showed per-site, per-dunder hardening against it is unbounded. The gates refuse every
    # foreign type up front, unread and with a fixed message; the interior exact-type checks
    # that remain (keys, string fields, the emitter) CLASSIFY parsed data, _Number against
    # str, and their vectors below use the parsed and _Number forms the gates admit.
    class _HidingIter(str):
        __slots__ = ()

        def __iter__(self):
            return iter("clean")

    class _AlwaysEqual(str):
        __slots__ = ()
        __hash__ = str.__hash__

        def __eq__(self, other):
            return True

        def __ne__(self, other):
            return False

    r = merge_registration(old, _HidingIter("opf-gov\nrm -rf tmp"))
    check("hostile-str-subclass-candidate-invalid",
          r.status is INVALID and r.new_bytes is None)
    other_entry = merge_registration(b"{}", "someone-else").new_bytes
    r = merge_registration(other_entry, _AlwaysEqual(entry))
    # under an isinstance revert this is a FALSE already-merged no-op (VALID, changed False):
    # the reflected __eq__ makes "someone-else" read as the candidate.
    check("always-equal-candidate-invalid", r.status is INVALID and r.new_bytes is None)
    exact_key = validate_registration_model({_Number("hooks"): dict()})
    check("exact-type-top-level-key-invalid",
          exact_key.status is INVALID and exact_key.findings ==
          ["registration carries a top-level key that is not exactly a string"])
    hostile_type = {"hooks": {"Stop": [{"hooks": [
        {"command": "x", "type": _AlwaysEqual("command")}]}]}}
    v = validate_registration_model(hostile_type)
    check("hostile-str-subclass-type-refused-at-gate",
          v.status is CANNOT_EVALUATE and v.findings == [_GATE_MODEL_FINDING])
    hostile_cmd = {"hooks": {"Stop": [{"hooks": [
        {"command": _HidingIter("clean\nevil"), "type": "command"}]}]}}
    v = validate_registration_model(hostile_cmd)
    check("hostile-str-subclass-command-refused-at-gate",
          v.status is CANNOT_EVALUATE and v.findings == [_GATE_MODEL_FINDING])
    hostile_matcher = {"hooks": {"Stop": [{"matcher": _HidingIter("a\nb"), "hooks": [
        {"command": "x", "type": "command"}]}]}}
    v = validate_registration_model(hostile_matcher)
    check("hostile-str-subclass-matcher-refused-at-gate",
          v.status is CANNOT_EVALUATE and v.findings == [_GATE_MODEL_FINDING])
    # keys are trust boundaries at EVERY level: a _Number key would be found by a plain-str
    # membership or dict lookup (reflected str equality), so only the exact-type key checks
    # keep a number-carrier key out of the recognized surface.
    hostile_event_key = {"hooks": {_Number("Stop"): []}}
    v = validate_registration_model(hostile_event_key)
    check("exact-type-event-key-invalid",
          v.status is INVALID and v.findings ==
          ["hooks carries an event key that is not exactly a string"])
    hostile_group_key = {"hooks": {"Stop": [{_Number("hooks"): [
        {"command": "x", "type": "command"}]}]}}
    v = validate_registration_model(hostile_group_key)
    # the finding text is pinned: an isinstance revert still refuses INVALID here, but with a
    # missing-required-key finding (the _Number key misses the plain-str dict lookup), so only
    # the exact-type check produces this finding.
    check("exact-type-group-key-invalid",
          v.status is INVALID and
          any("carries a key that is not exactly a string" in f for f in v.findings))
    hostile_entry_key = {"hooks": {"Stop": [{"hooks": [
        {_Number("command"): "x", "type": "command"}]}]}}
    v = validate_registration_model(hostile_entry_key)
    check("exact-type-entry-key-invalid",
          v.status is INVALID and
          any("carries a key that is not exactly a string" in f for f in v.findings))

    # 6d: THE ENTRY GATE on the exposed validator, pinned from both sides. A foreign-typed
    # node ANYWHERE (a recognized field, an opaque value, a container subclass) refuses with
    # exactly _GATE_MODEL_FINDING and NO method of the hostile object run: round 6 showed a
    # hostile __repr__ in the type field executing (and able to raise) inside the refusal
    # message, and a __class__-spoofing str subclass reading as a number and validating
    # VALID; type identity at the gate is immune to both. The walk is iterative with
    # visited-id tracking, so depth cannot crash it and a self-referencing model terminates.
    hostile_repr_calls = []

    class _HostileRepr(str):
        __slots__ = ()

        def __repr__(self):
            hostile_repr_calls.append("repr")
            raise RuntimeError("hostile repr executed")

    v = validate_registration_model({"hooks": {"Stop": [{"hooks": [
        {"command": "x", "type": _HostileRepr("command")}]}]}})
    check("model-gate-hostile-repr-refused-unrun",
          v.status is CANNOT_EVALUATE and v.findings == [_GATE_MODEL_FINDING]
          and hostile_repr_calls == [])

    class _StrAsInt(str):
        @property
        def __class__(self):
            return int

        def __float__(self):
            return 1.0

    v = validate_registration_model({"hooks": {"Stop": [{"hooks": [
        {"command": "x", "type": "command",
         "timeout": _StrAsInt("not-a-number")}]}]}})
    check("model-gate-class-spoof-refused",
          v.status is CANNOT_EVALUATE and v.findings == [_GATE_MODEL_FINDING])

    class _OpaqueForeign:
        __slots__ = ()

    v = validate_registration_model({"env": {"x": [_OpaqueForeign()]}})
    check("model-gate-opaque-foreign-refused",
          v.status is CANNOT_EVALUATE and v.findings == [_GATE_MODEL_FINDING])

    class _TableSubclass(dict):
        pass

    v = validate_registration_model({"hooks": _TableSubclass()})
    check("model-gate-container-subclass-refused",
          v.status is CANNOT_EVALUATE and v.findings == [_GATE_MODEL_FINDING])
    cyclic = {"env": dict()}
    cyclic["env"]["self"] = cyclic
    check("model-gate-cyclic-model-terminates",
          validate_registration_model(cyclic).status is VALID)
    # "keys included" is pinned: a foreign KEY inside an opaque subtree, which no interior
    # check reads, refuses at the gate unread (dropping the walk's key push lets it validate
    # VALID, so exactly this vector turns red).
    key_repr_calls = []

    class _RecordingKey(str):
        __slots__ = ()

        def __repr__(self):
            key_repr_calls.append("repr")
            return str.__repr__(self)

    opaque_key = dict()
    opaque_key[_RecordingKey("k")] = 1
    v = validate_registration_model(dict(env=opaque_key, hooks=dict()))
    check("model-gate-opaque-key-refused-unread",
          v.status is CANNOT_EVALUATE and v.findings == [_GATE_MODEL_FINDING]
          and key_repr_calls == [])

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

    # THE ENTRY GATE, input half (fix 5): EXACTLY bytes and nothing else, refused with the one
    # fixed message and NO method of the carrier run. bytes(x) would invoke an overridable
    # __bytes__, and CPython accepts a bytes-SUBCLASS return from it whose own decode() can
    # lie about the file content (round 6: a VALID merge that deleted the adopter's hooks), so
    # the gate never normalizes; a bytearray and a decode-overriding subclass refuse the same
    # way, unread.
    input_gate_finding = ("registration input is not exactly bytes (the entry gate accepts "
                          "exact bytes only and refuses anything else unread)")
    ba = merge_registration(bytearray(old), entry)
    check("bytearray-input-refused-at-gate",
          ba.status is CANNOT_EVALUATE and ba.new_bytes is None
          and ba.findings == [input_gate_finding])

    class _DecodeOverridingBytes(bytes):
        def decode(self, *args, **kwargs):
            raise AssertionError("caller-controlled decode must never run")

    subclassed = merge_registration(_DecodeOverridingBytes(old), entry)
    check("bytes-subclass-refused-at-gate",
          subclassed.status is CANNOT_EVALUATE and subclassed.new_bytes is None
          and subclassed.findings == [input_gate_finding])
    dunder_calls = []

    class _InnerLyingBytes(bytes):
        def decode(self, *args, **kwargs):
            dunder_calls.append("Inner.decode")
            return "{}"

        def __bytes__(self):
            dunder_calls.append("Inner.__bytes__")
            return self

    class _OuterBytesCarrier(bytes):
        def __bytes__(self):
            dunder_calls.append("Outer.__bytes__")
            return _InnerLyingBytes(old)

    carrier = merge_registration(_OuterBytesCarrier(old), entry)
    check("bytes-dunder-carrier-refused-at-gate",
          carrier.status is CANNOT_EVALUATE and carrier.new_bytes is None
          and carrier.findings == [input_gate_finding] and dunder_calls == [])
    # the oversize vector is VALID, ALREADY-MERGED JSON over the bound: without the input size
    # guard it would sail through parse and validation and return a VALID no-op of its own bytes
    # (the no-op path never re-emits, so the output bound cannot catch it), so removing that guard
    # turns exactly this red.
    big_merged = (b'{"hooks":{"PreToolUse":[{"hooks":[{"command":"opf-governance",'
                  b'"type":"command"}],"matcher":"*"}]},"env":{"x":"'
                  + b"a" * MAX_REGISTRATION_BYTES + b'"}}')
    check("oversize-vector-is-over-bound", len(big_merged) > MAX_REGISTRATION_BYTES)
    big = merge_registration(big_merged, entry)
    check("oversize-valid-json-cannot-eval",
          big.status is CANNOT_EVALUATE and big.new_bytes is None
          and any("exceeds" in f for f in big.findings))

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
    # the out-of-range finding shows the lexeme BARE, like every other number finding (a
    # quoted '1e400' would read as a string; showing the hook's plain str turns this red).
    r = merge_registration(b'{"env":{"x":1e400}}', entry)
    check("overflow-finding-shows-bare-lexeme",
          r.findings == ["registration bytes are not strict JSON: JSON number 1e400 is outside "
                         "the finite double range"])
    # 7b2: the parser's number grammar does not rest on the json scanner. The C _json scanner
    # reads ASCII digits only, but a pure-Python scanner whose number pattern spells digits as
    # \d (reproduced here by patching json.scanner) hands the parse hooks lexemes carrying
    # non-ASCII digits, which float() reads as finite numbers. The hooks refuse each one on the
    # strict ASCII grammar, on the merge path and the already-merged no-op path alike (without
    # the hooks' grammar check the no-op returns VALID over bytes the C scanner refuses). The
    # third lexeme ends in a grammatical tail (5), so an unanchored .search guard, which finds
    # that tail, turns this red too. The canary pins that the patched scanner really admits each
    # lexeme, so the vector exercises the hooks rather than a scanner refusal. A parsed lexeme
    # that escapes the grammar (the hooks' check bypassed here by a lax stand-in hook) is still
    # refused on the merge path by the emitter's own number rule, and the refusal does not call
    # it code-built (restoring a code-built label on the descriptor turns that check red).
    unicode_number_re = re.compile(r"(-?(?:0|[1-9]\d*))(\.\d+)?([eE][-+]?\d+)?",
                                   json.scanner.NUMBER_RE.flags)
    grammar_finding = ["registration bytes are not strict JSON: JSON number lexeme is outside "
                       "the strict ASCII number grammar (not shown)"]
    scanner_admits = []
    unicode_digit_results = []
    with mock.patch.object(json.scanner, "NUMBER_RE", unicode_number_re):
        with mock.patch.object(json.scanner, "make_scanner", json.scanner.py_make_scanner):
            for lexeme in ("1\u0663", "2.5e\u0663", "1\u06635"):
                scanner_admits.append(json.loads('{"x": ' + lexeme + '}', parse_int=str,
                                                 parse_float=str) == {"x": lexeme})
                for raw in (b'{"env":{"x":' + lexeme.encode("utf-8") + b'}}',
                            already_1e400.replace(b"1e400", lexeme.encode("utf-8"))):
                    unicode_digit_results.append(merge_registration(raw, entry))
            strict_parse_number = globals()["_parse_number"]
            try:
                globals()["_parse_number"] = _Number
                lax_merge = merge_registration(b'{"env":{"x":' + "1\u06635".encode("utf-8")
                                               + b'}}', entry)
            finally:
                globals()["_parse_number"] = strict_parse_number
    check("unicode-digit-scanner-canary", scanner_admits == [True, True, True])
    check("parse-number-grammar-scanner-independent",
          len(unicode_digit_results) == 6
          and all(r.status is CANNOT_EVALUATE and r.new_bytes is None
                  and r.findings == grammar_finding for r in unicode_digit_results))
    check("parsed-malformed-lexeme-refusal-not-code-built",
          lax_merge.status is CANNOT_EVALUATE and lax_merge.new_bytes is None
          and lax_merge.findings == ["merged registration cannot be emitted: number lexeme in the "
                                     "model is not a finite strict-JSON number: a malformed "
                                     "number lexeme (not shown)"])
    near = b'{"env":{"x":"' + b"a" * (MAX_REGISTRATION_BYTES - 60) + b'"}}'
    check("near-limit-input-inside-bound", len(near) <= MAX_REGISTRATION_BYTES)
    over = merge_registration(near, entry)
    # the bound refusal's own message is pinned (both it and the emission-refusal fallback
    # contain "exceed", so disabling the _EmitBoundRefusal branch, or ordering it after the
    # _ParseRefusal clause that would then catch it first, turns only this exact match red).
    check("merged-output-over-bound-refuses",
          over.status is CANNOT_EVALUATE and over.new_bytes is None
          and over.findings == ["merged registration would exceed {} bytes (the same bound the "
                                "input is held to, refused by the emitter's running byte "
                                "count so the over-bound output is never built and an "
                                "accepted output always no-ops on its next merge)".format(
                                    MAX_REGISTRATION_BYTES)])

    # 7d: the output byte bound is enforced INCREMENTALLY during emission: a small
    # wide-and-deep input (2-byte array elements whose every emitted line carries about 2*depth
    # bytes of indentation) must refuse the moment the running count would pass the bound. The
    # discriminator is DETERMINISTIC: a counting shim on the _emit_piece seam measures the
    # emission bytes actually BUILT, so the verdict depends on neither the interpreter build,
    # nor the allocator, nor ambient tracemalloc state (an absolute traced-peak ceiling here
    # was a false-red risk: reset_peak() keeps a caller's live allocations in the peak, and
    # object sizes are build-dependent; as build-dependent evidence only, the fixed shape
    # traced near 92 MiB on CPython 3.14 where the pre-fix shape traced near 332 MiB).
    # Reverting to build-the-whole-emission-then-length-check builds the whole multi-hundred-MB
    # output and turns exactly the built-bytes assertion red.
    wd_depth = 100
    wd_count = (MAX_REGISTRATION_BYTES - 2 * wd_depth - 20) // 2
    wide_deep = (b'{"env":' + b"[" * wd_depth + b"[" + b"0," * (wd_count - 1) + b"0]"
                 + b"]" * wd_depth + b"}")
    check("wide-deep-vector-inside-input-bound", len(wide_deep) <= MAX_REGISTRATION_BYTES)
    built = [0]
    real_emit_piece = _emit_piece

    def _counting_emit_piece(piece, out, budget):
        built[0] += (len(piece) if piece.isascii()
                     else len(piece.encode("utf-8", "surrogatepass")))
        return real_emit_piece(piece, out, budget)

    try:
        globals()["_emit_piece"] = _counting_emit_piece
        wide = merge_registration(wide_deep, entry)
    finally:
        globals()["_emit_piece"] = real_emit_piece
    check("wide-deep-emission-refuses",
          wide.status is CANNOT_EVALUATE and wide.new_bytes is None
          and any("exceed" in f for f in wide.findings))
    # an incremental refusal stops within one piece of the budget; pieces on this vector
    # reach about two hundred bytes (the deepest indentation runs), so 4096 bytes of slack is
    # generous and still two orders of magnitude below the pre-fix whole-output build.
    check("wide-deep-emission-built-bytes-bounded",
          0 < built[0] <= MAX_REGISTRATION_BYTES + 4096)

    # 7d2: the CANDIDATE is length-bounded at the ENTRY GATE, before any whole-candidate
    # scan or serialization: one longer than the registration byte bound could never fit, so
    # it refuses up front with json.dumps never run at all (the counting shim proves it) and
    # with the gate's own pinned finding (so a gate revert turns this red even though the
    # emitter's budget would also refuse, later and only after scanning).
    dumped = [0]
    real_json = json

    class _CountingJson:
        loads = staticmethod(real_json.loads)

        @staticmethod
        def dumps(value, **kwargs):
            text = real_json.dumps(value, **kwargs)
            dumped[0] += len(text)
            return text

    huge_candidate = "x" * (2 * MAX_REGISTRATION_BYTES)
    try:
        globals()["json"] = _CountingJson
        r = merge_registration(b"{}", huge_candidate)
    finally:
        globals()["json"] = real_json
    check("huge-candidate-refused-at-gate",
          r.status is CANNOT_EVALUATE and r.new_bytes is None
          and r.findings == ["plugin_entry exceeds {} bytes (the registration byte "
                             "bound also bounds candidate scanning)".format(
                                 MAX_REGISTRATION_BYTES)])
    check("huge-candidate-never-serialized", dumped[0] == 0)
    # an ESCAPE-HEAVY candidate UNDER the gate bound: its exact serialized form (two bytes
    # per quote) cannot fit the output budget, and the EXACT pre-serialization count
    # (_string_bytes) refuses it before json.dumps materializes the doubled form (under the
    # old len+2 lower bound a 1,200,002-byte serialization was built first and only the
    # running count refused it afterwards).
    dumped[0] = 0
    try:
        globals()["json"] = _CountingJson
        r = merge_registration(b"{}", '"' * 600000)
    finally:
        globals()["json"] = real_json
    check("escape-heavy-candidate-refuses",
          r.status is CANNOT_EVALUATE and r.new_bytes is None
          and any("exceed" in f for f in r.findings))
    check("escape-heavy-candidate-never-serialized", 0 < dumped[0] <= 4096)
    # a KEY that could never fit is likewise refused before serialization (a code-built
    # model only; parsed keys are already inside the input bound), and the EXACT count also
    # stops an escape-heavy key and a \uXXXX-control-heavy value that the old
    # character-count lower bound let through to json.dumps.
    dumped[0] = 0
    try:
        globals()["json"] = _CountingJson
        try:
            _emit({"k" * (2 * MAX_REGISTRATION_BYTES): 0})
            huge_key_refused = False
        except _EmitBoundRefusal:
            huge_key_refused = True
    finally:
        globals()["json"] = real_json
    check("huge-key-refuses", huge_key_refused)
    check("huge-key-never-serialized", dumped[0] == 0)
    dumped[0] = 0
    try:
        globals()["json"] = _CountingJson
        try:
            _emit({'"' * 600000: 0})
            quote_key_refused = False
        except _EmitBoundRefusal:
            quote_key_refused = True
    finally:
        globals()["json"] = real_json
    check("escape-heavy-key-refuses", quote_key_refused)
    check("escape-heavy-key-never-serialized", dumped[0] == 0)
    dumped[0] = 0
    try:
        globals()["json"] = _CountingJson
        try:
            _emit({"env": "\x01" * 400000})
            ctrl_value_refused = False
        except _EmitBoundRefusal:
            ctrl_value_refused = True
    finally:
        globals()["json"] = real_json
    check("control-heavy-value-refuses", ctrl_value_refused)
    # only the tiny "env" key is ever serialized; the 2,400,002-byte escaped value is not.
    check("control-heavy-value-never-serialized", 0 < dumped[0] <= 16)
    # the MULTIBYTE term of the exact count: a two-byte-UTF-8 candidate UNDER the gate bound
    # whose 1,200,002-byte serialization cannot fit refuses before json.dumps builds it (a
    # character-count revert of the UTF-8 term reads it as 600,002 bytes, lets json.dumps
    # build it, and turns exactly this red).
    dumped[0] = 0
    try:
        globals()["json"] = _CountingJson
        r = merge_registration(b"{}", "\u00e9" * 600000)
    finally:
        globals()["json"] = real_json
    check("multibyte-candidate-refused-never-serialized",
          r.status is CANNOT_EVALUATE and r.new_bytes is None
          and any("exceed" in f for f in r.findings) and 0 < dumped[0] <= 4096)

    # 7d3: the pre-serialization checks are EXACT (the serializer's own arithmetic), so they
    # can never refuse an emission that fits: a value or key landing the output exactly ON
    # the byte bound is accepted and one unit more refuses, for plain ASCII, escape-doubled,
    # \uXXXX-control and two-byte UTF-8 content alike (a pre-check stricter than exact, a
    # +300000 slack say, turns the at-bound acceptances red). These end-to-end vectors keep a
    # few bytes of closing structure after the string or key, so an overcount smaller than
    # that is pinned by the direct and zero-slack vectors after them, not by these.
    def _emit_bound_refuses(model):
        try:
            _emit(model)
        except _EmitBoundRefusal:
            return True
        return False

    tail_room = MAX_REGISTRATION_BYTES - len(_emit({"permissions": {"k": ""}}))
    check("late-string-at-exact-bound-accepted",
          len(_emit({"permissions": {"k": "a" * tail_room}}))
          == MAX_REGISTRATION_BYTES)
    check("late-string-one-over-refuses",
          _emit_bound_refuses({"permissions": {"k": "a" * (tail_room + 1)}}))
    esc_room = tail_room // 2
    check("late-escaped-string-at-bound-accepted",
          len(_emit({"permissions": {"k": '"' * esc_room}}))
          == MAX_REGISTRATION_BYTES - tail_room + 2 * esc_room)
    check("late-escaped-string-over-refuses",
          _emit_bound_refuses({"permissions": {"k": '"' * (esc_room + 1)}}))
    ctrl_room = tail_room // 6
    check("late-control-string-at-bound-accepted",
          len(_emit({"permissions": {"k": "\x01" * ctrl_room}}))
          == MAX_REGISTRATION_BYTES - tail_room + 6 * ctrl_room)
    utf8_room = tail_room // 2
    check("late-utf8-string-at-bound-accepted",
          len(_emit({"permissions": {"k": "\u00e9" * utf8_room}}))
          == MAX_REGISTRATION_BYTES - tail_room + 2 * utf8_room)
    key_room = MAX_REGISTRATION_BYTES - len(_emit({"permissions": {"": 0}}))
    check("late-key-at-exact-bound-accepted",
          len(_emit({"permissions": {"k" * key_room: 0}}))
          == MAX_REGISTRATION_BYTES)
    check("late-key-one-over-refuses",
          _emit_bound_refuses({"permissions": {"k" * (key_room + 1): 0}}))
    # the count ITSELF is exact: _string_bytes equals the serializer's own UTF-8 byte length
    # over an edge set (every escape class, DEL and C1, two-, three- and four-byte UTF-8, a lone
    # surrogate, and every code point below U+0080), so an overcount or undercount of even one
    # byte in any term turns this red.
    edge_strings = ["", "a", '"', "\\", "\b\t\n\r", "\x00\x01\x19", "\x7f\x80\x9f", "\u00e9",
                    "\N{LINE SEPARATOR}\N{PARAGRAPH SEPARATOR}", "\uffff",
                    "\U0010ffff", "\ud800", "caf\u00e9 \"q\"\n\x01",
                    "".join(chr(c) for c in range(0x80))]
    check("string-bytes-exact-over-edge-set",
          all(_string_bytes(s) == len(json.dumps(s, ensure_ascii=False).encode(
              "utf-8", "surrogatepass")) for s in edge_strings))

    # ZERO-SLACK boundary vectors: the string or key under test is the FINAL piece against a
    # budget of exactly the bytes up to and including it. The exact budget must admit it (an
    # overcount of even one byte, or a stricter-than-exact pre-check, refuses it and turns the
    # vector red), and a budget one byte short must refuse it with json.dumps never run (an
    # undercount lets the pre-check pass and json.dumps build the piece first, red too).
    def _final_piece_exact(value, as_key):
        if as_key:
            model, lead = {value: _Number("0")}, ["{\n"]
            piece = "  " + json.dumps(value, ensure_ascii=False) + ": "
        else:
            model, lead = value, []
            piece = json.dumps(value, ensure_ascii=False)
        need = sum(len(p.encode("utf-8", "surrogatepass")) for p in lead + [piece])
        fits = []
        try:
            _emit_value(model, 0, fits, [need])
        except _EmitBoundRefusal:
            pass  # a key's value cannot fit a zero-slack budget; only the key line is judged
        short = []
        short_refused = False
        dumped[0] = 0
        try:
            globals()["json"] = _CountingJson
            try:
                _emit_value(model, 0, short, [need - 1])
            except _EmitBoundRefusal:
                short_refused = True
        finally:
            globals()["json"] = real_json
        return fits == lead + [piece] and short_refused and short == lead and dumped[0] == 0

    for kind, text in (("ascii", "a" * 1000), ("escape", '"\\' * 500),
                       ("control", "\x01\x19\n" * 300), ("utf8", "\u00e9" * 700),
                       ("astral", "\U0001f600" * 300)):
        check("final-string-zero-slack-{}".format(kind), _final_piece_exact(text, False))
        check("final-key-zero-slack-{}".format(kind), _final_piece_exact(text, True))

    # 7e: the emitter itself holds numbers to the parser's number grammar and finite double
    # range, so a code-built exact int, float or _Number that _parse would refuse is refused
    # here instead of emitted (each vector below used to emit bytes _parse then refuses). The
    # fixed-point claim is exactly the _emit docstring's narrowed scope, parsed and merge-built
    # models; a subclass of int, float, dict or list is outside it.
    def _emit_refuses(model):
        try:
            _emit(model)
        except _ParseRefusal:
            return True
        return False

    check("emit-code-built-huge-int-refuses", _emit_refuses({"env": 10 ** 400}))
    check("emit-code-built-nonfinite-refuses", _emit_refuses({"env": float("inf")}))
    check("emit-out-of-range-lexeme-refuses", _emit_refuses({"env": _Number("1e400")}))
    check("emit-malformed-lexeme-refuses", _emit_refuses({"env": _Number("007")}))
    # exact-type keys and string values: a _Number key beside its same-lexeme str key would
    # emit duplicate-key JSON _parse then refuses (breaking the fixed-point claim above), and
    # a hostile str subclass must never reach json.dumps, which reads the real underlying
    # buffer.
    check("emit-nonexact-string-key-refuses",
          _emit_refuses({"env": {_Number("1"): 0, "1": 1}}))
    check("emit-nonexact-string-value-refuses", _emit_refuses({"env": _HidingIter("x")}))
    # the lexeme-carrier check is type IDENTITY: a _Number SUBCLASS is caller-built code
    # whose __str__ could emit arbitrary bytes AFTER the buffer passes the grammar check, so
    # it refuses as unserializable (an isinstance revert would run the override and emit its
    # output as JSON).

    class _EvilStrNumber(_Number):
        __slots__ = ()

        def __str__(self):
            return '0, "evil": 0'

    check("emit-number-subclass-refuses", _emit_refuses({"env": _EvilStrNumber("1")}))

    # 7c: recursion exhaustion refuses, never an uncaught exception, on BOTH paths: deep top-level
    # nesting exhausts the PARSER (mapped by _parse's RecursionError handler), and nesting that
    # parses but exceeds the Python-level emission depth (roughly the interpreter recursion limit)
    # exhausts EMISSION on the changed-merge path (mapped by the merged-emission handler, whose
    # message never claims a verification ran); dropping either RecursionError handler turns
    # its vector into an uncaught crash here.
    deep_parse = b"[" * 100000 + b"]" * 100000
    r = merge_registration(deep_parse, entry)
    check("deep-nesting-parse-cannot-eval", r.status is CANNOT_EVALUATE and r.new_bytes is None)
    deep_env = b'{"env":' + b"[" * 2000 + b"]" * 2000 + b"}"
    r = merge_registration(deep_env, entry)
    check("deep-nesting-emission-cannot-eval",
          r.status is CANNOT_EVALUATE and r.new_bytes is None)
    # the emission refusal is reported as an emission refusal, never as a failed verification
    # (catching it in the verification handler turns this red).
    check("deep-nesting-emission-not-reported-as-verification",
          len(r.findings) == 1
          and r.findings[0].startswith("merged registration cannot be emitted: ")
          and "verification" not in r.findings[0])
    # a FINDING never recurses over adopter content either: exact bytes nesting an array in
    # the entry type field just under the parser's own depth limit (found here by bisection,
    # since the limit depends on the build and its stack) parse, then refuse on the type with
    # the fixed array descriptor. A repr-based finding exhausts the stack on it on builds
    # whose repr overflows below the parser limit (round 7: from about 47,000 levels, against
    # a parser limit near 58,000, on CPython 3.14), an uncaught RecursionError.
    type_head = b'{"hooks":{"Stop":[{"hooks":[{"command":"x","type":'
    type_tail = b'}]}]}}'

    def _deep_type(depth):
        return type_head + b"[" * depth + b"]" * depth + type_tail

    def _parses(raw):
        try:
            _parse(raw)
        except _ParseRefusal:
            return False
        return True

    low, high = 0, 100000
    if _parses(_deep_type(high)):
        low = high
    while high - low > 1:
        mid = (low + high) // 2
        if _parses(_deep_type(mid)):
            low = mid
        else:
            high = mid
    deep_type = _deep_type(max(low - 64, 0))
    try:
        r = merge_registration(deep_type, entry)
        deep_type_raised = False
    except RecursionError:
        deep_type_raised = True
    check("deep-array-type-under-parser-limit-refuses-never-raises",
          not deep_type_raised and r.status is CANNOT_EVALUATE and r.new_bytes is None
          and r.findings == ["hooks['Stop'][0].hooks[0] type {} outside the closed v1 "
                             "vocabulary".format(_SHOWN_ARRAY)])

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
    # an integer outside the double range refuses (never an OverflowError), whether the model came
    # through stdlib json.loads (the wiring slice may parse that way) or was code-built.
    check("model-huge-int-timeout-refuses",
          validate_registration_model(json.loads(
              b'{"hooks":{"Stop":[{"hooks":[{"command":"x","type":"command",'
              b'"timeout":' + b"9" * 400 + b'}]}]}}')).status is INVALID)
    check("model-code-built-huge-int-refuses",
          validate_registration_model({"hooks": {"Stop": [{"hooks": [
              {"command": "x", "type": "command", "timeout": 10 ** 400}]}]}}).status is INVALID)
    # an EXACT _Number carrier the gate admits by type is held to the parser's own number
    # rule (grammar, then finite range) before it counts as a timeout, never trusted as finite
    # by construction: each malformed or non-finite lexeme refuses with the timeout finding,
    # while grammatical finite lexemes stay VALID (dropping the lexeme check lets all five
    # validate VALID; dropping only its grammar half makes float() raise on the non-numbers).
    timeout_finding = ["hooks['Stop'][0].hooks[0] timeout is not a number in the finite "
                       "double range"]

    def _timeout_model(lexeme):
        return {"hooks": {"Stop": [{"hooks": [
            {"command": "x", "type": "command", "timeout": _Number(lexeme)}]}]}}

    check("model-exact-number-carrier-lexeme-checked",
          all(validate_registration_model(_timeout_model(bad)).findings == timeout_finding
              for bad in ("not-a-number", "NaN", "1e400", "007", ""))
          and all(validate_registration_model(_timeout_model(good)).status is VALID
                  for good in ("60", "-0", "1.5E3", "1e-400")))
    # no FINDING path formats a caller value in a way that can raise: an exact int past the
    # interpreter's digit limit and a code-built array nested far past any repr depth, each
    # in the entry type field, refuse with a fixed descriptor (a repr-based finding raises
    # ValueError on the first and RecursionError on the second).
    deep_array = []
    for _ in range(200000):
        deep_array = [deep_array]
    for name, type_value, shown in (("huge-int", 10 ** 5000, _SHOWN_INT),
                                    ("int-in-array", [10 ** 5000], _SHOWN_ARRAY),
                                    ("deep-array", deep_array, _SHOWN_ARRAY)):
        try:
            v = validate_registration_model({"hooks": {"Stop": [{"hooks": [
                {"command": "x", "type": type_value}]}]}})
            refused = v.status is CANNOT_EVALUATE and v.findings == [
                "hooks['Stop'][0].hooks[0] type {} outside the closed v1 vocabulary".format(
                    shown)]
        except (ValueError, RecursionError):
            refused = False
        check("model-{}-type-refuses-never-raises".format(name), refused)
    del deep_array, type_value

    # 11: findings and refusal messages BOUND what they repeat (_shown): on every path that
    # embeds adopter content (a number lexeme, a key, a duplicate key, a hook event, an entry
    # type, and the caller's candidate), long content comes back as a bounded excerpt carrying
    # the value's own length, never echoed whole; reverting _shown to a plain repr turns each
    # of these red.
    finding_bound = 256
    big = 512 * 1024

    def findings_bounded(result, expect_status):
        return (result.status is expect_status and result.new_bytes is None
                and result.findings
                and all(len(f) <= finding_bound for f in result.findings))

    long_lexeme = merge_registration(b'{"env": {"x": ' + b"9" * big + b'}}', entry)
    check("bounded-finding-long-lexeme", findings_bounded(long_lexeme, CANNOT_EVALUATE))
    long_key = merge_registration(b'{"' + b"k" * big + b'": {}}', entry)
    check("bounded-finding-long-key", findings_bounded(long_key, INVALID))
    long_dup = merge_registration(
        b'{"' + b"d" * (big // 2) + b'": 1, "' + b"d" * (big // 2) + b'": 2}', entry)
    check("bounded-finding-long-duplicate-key", findings_bounded(long_dup, CANNOT_EVALUATE))
    long_event = merge_registration(b'{"hooks": {"' + b"e" * big + b'": []}}', entry)
    check("bounded-finding-long-event", findings_bounded(long_event, CANNOT_EVALUATE))
    long_type = merge_registration(
        b'{"hooks": {"Stop": [{"hooks": [{"command": "x", "type": "' + b"t" * big
        + b'"}]}]}}', entry)
    check("bounded-finding-long-type", findings_bounded(long_type, CANNOT_EVALUATE))
    long_entry = "c" * (256 * 1024)
    conflicted = _emit({"hooks": {ENTRY_EVENT: [
        {"hooks": [{"command": long_entry, "type": "command"}], "matcher": "Bash"}]}})
    check("bounded-finding-long-candidate",
          findings_bounded(merge_registration(conflicted, long_entry), INVALID))
    # the excerpt reports the VALUE's length and cuts the value before repr, so the cut can
    # never split a repr escape sequence (reverting to a repr-length count or a repr-text cut
    # turns exactly this red).
    check("shown-reports-value-length",
          _shown("a" * 100) == "{}... (100 characters)".format(repr("a" * 48))
          and _shown(_Number("1" * 100)) == "1" * 64 + "... (100 characters)")
    # a SHORT exact str or _Number whose repr is long (heavy escapes) comes back WHOLE: the
    # value, never the repr, is what gets cut at every length, so no cut can split an escape
    # and no genuine value is ever counted in repr characters (round 6: an
    # 18-control-character str came back cut mid-escape, labelled with the repr's length).
    check("shown-short-heavy-escape-uncut",
          _shown("\x01" * 18) == repr("\x01" * 18)
          and _shown("\U000f0000" * 20) == repr("\U000f0000" * 20)
          and _shown(_Number("1")) == "1")
    # an int, a table and an array are NEVER formatted, whatever their size: each comes back
    # as its fixed descriptor (restoring a repr for any of them turns this red), and a
    # formatting failure of any kind comes back as the fixed placeholder, never an exception
    # (a raising formatter injected at the module's repr seam; removing the guard lets it
    # raise and turns the second check red).
    check("shown-int-and-containers-fixed-descriptors",
          _shown(7) == _SHOWN_INT and _shown(10 ** 5000) == _SHOWN_INT
          and _shown(dict(a=1)) == _SHOWN_TABLE and _shown([1]) == _SHOWN_ARRAY)
    # a code-built _Number shows bare ONLY when the WHOLE of it is a grammatical number lexeme:
    # one carrying a quote and control characters comes back as its fixed descriptor on the
    # validator's type finding and on the emitter's lexeme refusal, so no raw control text
    # reaches a finding and a number never reads as a string (showing every _Number bare turns
    # this red). The second lexeme ends in a grammatical tail (31), so an unanchored .search
    # guard, which finds that tail, turns it red too. The emitter's refusal reads naturally
    # with the descriptor and with a grammatical out-of-range lexeme shown bare.
    def _emit_refusal(model):
        try:
            _emit(model)
        except _ParseRefusal as exc:
            return str(exc)
        return None

    hostile_ok = []
    for hostile_lexeme in (_Number("'command'\n\x1b[31m"), _Number("'command'\n\x1b[31")):
        hostile_type = validate_registration_model({"hooks": {"Stop": [{"hooks": [
            {"command": "x", "type": hostile_lexeme}]}]}})
        hostile_ok.append(
            _shown(hostile_lexeme) == _SHOWN_MALFORMED_NUMBER
            and hostile_type.status is CANNOT_EVALUATE
            and hostile_type.findings == ["hooks['Stop'][0].hooks[0] type {} outside the "
                                          "closed v1 vocabulary".format(_SHOWN_MALFORMED_NUMBER)]
            and _emit_refusal({"env": hostile_lexeme}) == (
                "number lexeme in the model is not a finite strict-JSON number: {}".format(
                    _SHOWN_MALFORMED_NUMBER)))
    check("shown-malformed-number-lexeme-fixed-descriptor",
          hostile_ok == [True, True] and _shown(_Number("-1.5E3")) == "-1.5E3"
          and _emit_refusal({"env": _Number("1e400")}) == (
              "number lexeme in the model is not a finite strict-JSON number: 1e400"))
    # a parsed JSON literal in a finding reads in JSON spelling (true, false, null), never in
    # Python's (True, False, None); restoring repr for the three literals turns this red.
    literal_type = merge_registration(
        b'{"hooks": {"Stop": [{"hooks": [{"command": "x", "type": true}]}]}}', entry)
    check("shown-json-literals-json-spelling",
          literal_type.status is CANNOT_EVALUATE
          and literal_type.findings == ["hooks['Stop'][0].hooks[0] type true outside the "
                                        "closed v1 vocabulary"]
          and _shown(True) == "true" and _shown(False) == "false" and _shown(None) == "null")

    def _raising_repr(value):
        raise ValueError("formatter fault (self-test shim)")

    try:
        globals()["repr"] = _raising_repr
        try:
            shown_fault = _shown("x")
        except ValueError:
            shown_fault = None
    finally:
        del globals()["repr"]
    check("shown-formatting-failure-placeholder", shown_fault == _SHOWN_UNFORMATTABLE)

    # 11b: the findings LIST is bounded too: at most MAX_FINDINGS findings plus one
    # suppression marker come back, so a registration with tens of thousands of violations
    # cannot amplify a refusal into megabytes of accumulated diagnostics.
    many = {"k" + str(i): 0 for i in range(MAX_FINDINGS + 200)}
    capped = validate_registration_model(many)
    check("findings-count-capped",
          capped.status is INVALID and len(capped.findings) == MAX_FINDINGS + 1
          and "further findings suppressed" in capped.findings[-1])

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
    if sys.argv[1:] == ["--self-test"]:
        sys.exit(self_test())
    sys.exit(main())
