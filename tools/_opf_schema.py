#!/usr/bin/env python3
"""OPF (DevProcess) record envelope + baseline type schemas + status/transition grammar + counters (U2).

Offline, stdlib only, fail-closed. This is the CORE VALIDATOR the later OPF units (doctor, views,
import) build on: given a parsed record it decides whether the record is a well-formed instance of its
declared baseline type, and given a proposed status change it decides whether that transition is legal.
It extends U1's conventions (the VALID/INVALID/CANNOT-EVALUATE outcome model, the closed-keyset schema
discipline, the section-8.1 type->namespace taxonomy) rather than duplicating them; U1's roster and
namespace helpers are imported, not re-declared.

Four things live here, all from OPF-SPEC.md section 8 (with the release triad's worklog shape from 6.2):

  1. THE RECORD ENVELOPE (spec 8.3). Every record carries a closed envelope (id, type, status, title,
     created_at, updated_at, actor.kind, and optional actor.id, summary, links, refs); types add their
     own fields ON TOP. Schemas are CLOSED: an unknown key is a validation failure unless it sits under a
     registered `x-<vendor>` extension table (spec 8.7). The worklog entry uses a REDUCED envelope (id,
     date, actor, kind, summary, optional detail, links, refs) with a fixed status (spec 8.3, 8.5, 6.2).

  2. THE ID GRAMMAR + ONE-TO-ONE NAMESPACE BINDING (spec 8.2). An id is `<NS>-<n>`: a two-letter
     namespace, a hyphen, and a POSITIVE integer (no leading zero). The namespace MUST be the normative
     namespace bound to the record's type by U1's section-8.1 taxonomy; a namespace maps one-to-one to a
     type.

  3. THE STATUS + TRANSITION GRAMMAR (spec 8.4, 8.5). A status is `state(/proposed)?`. Each baseline
     type declares a closed state set (one initial, zero or more working, one or more terminal) and a
     legal transition table. A TERMINAL transition performed by an `assistant` or `automation` actor
     lands with the `/proposed` qualifier; only a MAINTAINER transition ratifies (drops the qualifier)
     or rejects (returns to a working state). An unqualified terminal state NEVER re-enters a working
     state (no resurrection); supersession is a link, not a state edit. A `block` created by an assistant
     or automation actor is `active/proposed` (a proposal, not a grant), the one non-terminal state this
     standard qualifies with `/proposed`.

  4. THE COUNTERS MODEL (spec 8.2). `counters.toml` holds one MONOTONIC high-water value per namespace.
     Allocation increments it as one atomic claim; counters are never reset and IDs are never reused,
     even when a record is superseded or reverted, and rotation/index-rewrite/relocation never touch
     them. This unit provides the schema check, monotonic allocation, a reset/regression check, an
     ids-within-high-water check, and a duplicate-id check the later doctor composes.

Every malformed, unknown, illegal, or unidentifiable input fails closed to an INVALID or CANNOT-EVALUATE
outcome, never a silent VALID (spec 3 "Fail closed"; the check-fails-closed-on-unreadable rule: a
present-but-unparseable record is a refusing failure that names the record, never silent absence).

Reference-tooling / spec ambiguities recorded for the finalizer (each resolved here the strict,
fail-closed way and named so the choice is reviewable, per disclose-guard-residuals). The spec's own
manifest is "illustrative (the schema release that follows this specification is normative)", so this
unit is that schema release for the record model and DEFINES the following where section 8 leaves a gap:
  - TIMESTAMPS are validated as RFC 3339 UTC STRINGS with a `Z` offset (the exact form of every spec
    example: Appendix A/B/C), optional fractional seconds, calendar-validated. A native TOML datetime
    (an UNQUOTED value tomllib parses to datetime) and a non-`Z` offset (e.g. `+00:00`) are REJECTED as
    a wrong type / non-canonical, fail-closed; the finalizer may widen if adopters need the native form.
  - PER-TYPE FIELD NAMES the spec describes but does not spell: `block.scopes` (spec 8.5 "scopes an
    enumerated list of record IDs"), `autonomous_decision.classification` and `.action` (spec 8.5 "the
    classification basis, the action, links"), and the worklog `detail` field (spec 6.2 "optional
    detail"). These names are DEFINED HERE; a store carrying a different name fails closed (the safe
    direction), and the finalizer can rename in one place.
  - The `finding.severity` VOCABULARY is not fixed by the spec (Appendix A shows "minor"); this unit
    accepts any non-empty string and enforces only the placement rule (severity graded at or after the
    fix decision, so it is FORBIDDEN while `open`, spec 8.5). The pending_decision bundle fields
    (`decision`, `decided_at`, `decided_by`) are enforced all-or-none: all present on `decided`, none on
    `open` or `withdrawn` (spec 8.5 names open and decided; `withdrawn` is a terminal-without-decision,
    so it carries none, defined here).
  - A single RECORD's status is validated only for WELL-FORMEDNESS against its type (a legal state, a
    `/proposed` only on a proposable state). The actor-vs-`/proposed` creation rule is a TRANSITION
    rule (validate_transition), not asserted from a lone record snapshot, because a record's `actor`
    need not name the actor of its last transition.
  - `counters.toml` shape is DEFINED HERE as an optional top-level `schema` int plus a `[counters]`
    table of `<NS> = <non-negative int>` (mirroring version.toml's `schema = 1` marker); the spec fixes
    the semantics (one monotonic high-water per namespace) but not the file layout.
"""
import datetime
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
# U1 supplies the outcome model, the section-8.1 type->namespace taxonomy, and the namespace/x-vendor
# shape helpers; reuse them rather than re-declaring (match-surrounding-code, single source of truth).
from _opf_store import (  # noqa: E402
    VALID, INVALID, CANNOT_EVALUATE, BASELINE_TYPES,
    _valid_extension_namespace,
)


# --- envelope vocabularies (spec 8.3, 8.6) -----------------------------------------------------------

ACTOR_KINDS = ("maintainer", "assistant", "automation", "importer")
# Actors whose TERMINAL transition lands with the /proposed qualifier (spec 8.4). A maintainer ratifies;
# an importer records imported history as settled fact, so neither proposes.
PROPOSER_KINDS = frozenset({"assistant", "automation"})

# The closed link-relation vocabulary (spec 8.6). Extending it is a spec version change.
LINK_RELS = ("supersedes", "resolves", "remediates", "receipt_of", "corrects", "follows", "relates")
# The closed reference-capture kinds (spec 8.6).
REF_KINDS = ("path", "url", "doc")

# The worklog change-kind vocabulary (spec 6.2); a manifest MAY register additional kinds, passed in.
WORKLOG_KINDS = ("added", "changed", "fixed", "removed", "security", "docs", "infra")

ACTOR_KEYS = frozenset({"kind", "id"})
LINK_KEYS = frozenset({"rel", "id"})
REF_KEYS = frozenset({"kind", "locator", "note"})

# The full (non-worklog) envelope keyset; types add their own extra keys on top (EXTRA_KEYS below).
ENVELOPE_KEYS = frozenset({"id", "type", "status", "title", "created_at", "updated_at",
                           "actor", "summary", "links", "refs"})
# The reduced worklog envelope keyset (spec 8.3, 6.2): no status/title/created_at/updated_at; `type` is
# implicit in worklog.toml (Appendix C omits it) so it is OPTIONAL here, and `date` replaces the
# creation/update timestamps.
WORKLOG_KEYS = frozenset({"id", "type", "date", "actor", "kind", "summary", "detail", "links", "refs"})

# `<NS>-<n>`: two uppercase letters, a hyphen, a positive integer with no leading zero (spec 8.2).
_ID_RE = re.compile(r"^([A-Z]{2})-([1-9][0-9]*)$")
# RFC 3339 UTC with a Z offset and optional fractional seconds (see the timestamp ambiguity note above).
_TS_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(\.\d+)?Z$")

# counters.toml layout (defined here; see the ambiguity note).
COUNTERS_TOP_KEYS = frozenset({"schema", "counters"})


# --- per-type state machines (spec 8.5) --------------------------------------------------------------

class TypeSpec:
    """One baseline record type's schema: its namespace (from U1's taxonomy), its closed state set and
    legal transitions, the states that may carry `/proposed`, its extra (non-envelope) field keys, and
    whether it uses the reduced worklog envelope. `states` is precomputed (initial + working + terminal).
    """
    __slots__ = ("name", "namespace", "initial", "working", "terminal", "transitions",
                 "proposable", "extra_keys", "reduced", "states")

    def __init__(self, name, initial, working, terminal, transitions, proposable,
                 extra_keys=frozenset(), reduced=False):
        self.name = name
        self.namespace = BASELINE_TYPES[name]     # single source of truth for the type->namespace bind
        self.initial = initial
        self.working = frozenset(working)
        self.terminal = frozenset(terminal)
        self.transitions = {k: frozenset(v) for k, v in transitions.items()}
        self.proposable = frozenset(proposable)
        self.extra_keys = frozenset(extra_keys)
        self.reduced = reduced
        self.states = frozenset({initial}) | self.working | self.terminal


# The nine baseline types (spec 8.5). Single-state types (done/autonomous_decision/reference/worklog)
# have `recorded` as both initial and terminal with no transitions (created terminal, immutable).
BASELINE_SPECS = {
    "backlog_item": TypeSpec(
        "backlog_item", initial="open", working={"active"}, terminal={"done", "dropped"},
        transitions={"open": {"active", "dropped"}, "active": {"done", "dropped"}},
        proposable={"done", "dropped"}),
    "done": TypeSpec(
        "done", initial="recorded", working=set(), terminal={"recorded"},
        transitions={}, proposable={"recorded"}),
    "worklog": TypeSpec(
        "worklog", initial="recorded", working=set(), terminal={"recorded"},
        transitions={}, proposable=set(), extra_keys={"date", "kind", "detail"}, reduced=True),
    "finding": TypeSpec(
        "finding", initial="open", working=set(),
        terminal={"fixed", "routed", "refuted", "accepted"},
        transitions={"open": {"fixed", "routed", "refuted", "accepted"}},
        proposable={"fixed", "routed", "refuted", "accepted"}, extra_keys={"severity"}),
    "pending_decision": TypeSpec(
        "pending_decision", initial="open", working=set(), terminal={"decided", "withdrawn"},
        transitions={"open": {"decided", "withdrawn"}},
        proposable={"decided", "withdrawn"}, extra_keys={"decision", "decided_at", "decided_by"}),
    "autonomous_decision": TypeSpec(
        "autonomous_decision", initial="recorded", working=set(), terminal={"recorded"},
        transitions={}, proposable={"recorded"}, extra_keys={"classification", "action"}),
    "block": TypeSpec(
        # The one type whose non-terminal initial state is proposable: an assistant/automation block is
        # `active/proposed`, a proposal a maintainer ratifies to `active` (spec 8.5).
        "block", initial="active", working=set(), terminal={"released", "expired"},
        transitions={"active": {"released", "expired"}},
        proposable={"active", "released", "expired"}, extra_keys={"scopes"}),
    "handoff": TypeSpec(
        "handoff", initial="current", working=set(), terminal={"superseded"},
        transitions={"current": {"superseded"}}, proposable={"superseded"}),
    "reference": TypeSpec(
        "reference", initial="recorded", working=set(), terminal={"recorded"},
        transitions={}, proposable={"recorded"}),
}


# --- result carriers (the U1 ManifestValidation idiom) -----------------------------------------------

class RecordValidation:
    __slots__ = ("status", "findings", "type", "id")

    def __init__(self, status, findings=None, rtype=None, rid=None):
        self.status = status              # VALID / INVALID / CANNOT_EVALUATE
        self.findings = findings or []
        self.type = rtype
        self.id = rid


class TransitionCheck:
    __slots__ = ("status", "findings")

    def __init__(self, status, findings=None):
        self.status = status              # VALID (legal) / INVALID (illegal) / CANNOT_EVALUATE
        self.findings = findings or []


# --- shared field validators -------------------------------------------------------------------------

def _valid_timestamp(value):
    """True when value is an RFC 3339 UTC string with a Z offset that is a real calendar instant (see
    the timestamp ambiguity note). A native TOML datetime or a non-Z offset is rejected."""
    if not isinstance(value, str):
        return False
    m = _TS_RE.match(value)
    if not m:
        return False
    try:
        datetime.datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)),
                          int(m.group(4)), int(m.group(5)), int(m.group(6)))
    except ValueError:
        return False   # e.g. month 13, day 32, hour 24, second 60 (a leap second is not a valid instant)
    return True


def parse_status(status, spec):
    """Parse a status string against a type's grammar (spec 8.4). Returns ((state, qualifier), None) on
    success, or (None, message) on a malformed or type-illegal status. `qualifier` is None or "proposed",
    and "proposed" is legal only on a state the type marks proposable."""
    if not isinstance(status, str) or not status:
        return None, "status must be a non-empty string"
    parts = status.split("/")
    if len(parts) == 1:
        state, qual = parts[0], None
    elif len(parts) == 2:
        state, qual = parts[0], parts[1]
    else:
        return None, "status {!r} has more than one '/' qualifier".format(status)
    if state not in spec.states:
        return None, "status state {!r} is not a {} state {}".format(
            state, spec.name, sorted(spec.states))
    if qual is not None:
        if qual != "proposed":
            return None, "status qualifier {!r} is not 'proposed' (the only qualifier, spec 8.4)".format(qual)
        if state not in spec.proposable:
            return None, "state {!r} may not carry '/proposed' for a {} (spec 8.4/8.5)".format(
                state, spec.name)
    return (state, qual), None


def _valid_id_shape(value):
    """The (namespace, number) of a well-formed `<NS>-<n>` id, or None."""
    if not isinstance(value, str):
        return None
    m = _ID_RE.match(value)
    return (m.group(1), int(m.group(2))) if m else None


def _validate_actor(record, findings):
    """Validate the `actor` table (spec 8.3). Returns the actor kind string, or None when it is absent or
    malformed (so the caller can apply the importer created_at exception on a KNOWN kind only)."""
    actor = record.get("actor")
    if "actor" not in record:
        findings.append("missing required field: actor")
        return None
    if not isinstance(actor, dict):
        findings.append("actor must be a table with a kind")
        return None
    extra = set(actor) - ACTOR_KEYS
    if extra:
        findings.append("actor unknown key(s): {}".format(", ".join(sorted(extra))))
    kind = actor.get("kind")
    if kind not in ACTOR_KINDS:
        findings.append("actor.kind {!r} is not one of {}".format(kind, list(ACTOR_KINDS)))
        kind = None
    if "id" in actor and (not isinstance(actor.get("id"), str) or not actor.get("id")):
        findings.append("actor.id must be a non-empty string when present")
    return kind


def _validate_links(record, findings):
    """Validate the optional `links` array of {rel, id} (spec 8.6): rel from the closed vocabulary, id a
    well-formed `<NS>-<n>` (any namespace, since a link may point at any type)."""
    if "links" not in record:
        return
    links = record.get("links")
    if not isinstance(links, list):
        findings.append("links must be an array of {rel, id} tables")
        return
    for i, link in enumerate(links):
        where = "links[{}]".format(i)
        if not isinstance(link, dict):
            findings.append("{} must be a {{rel, id}} table".format(where))
            continue
        extra = set(link) - LINK_KEYS
        if extra:
            findings.append("{} unknown key(s): {}".format(where, ", ".join(sorted(extra))))
        if link.get("rel") not in LINK_RELS:
            findings.append("{}.rel {!r} is not one of {} (spec 8.6)".format(
                where, link.get("rel"), list(LINK_RELS)))
        if _valid_id_shape(link.get("id")) is None:
            findings.append("{}.id {!r} is not a well-formed <NS>-<n> id".format(where, link.get("id")))


def _validate_refs(record, findings):
    """Validate the optional `refs` array of {kind, locator, note} (spec 8.6): kind from the closed set,
    locator a non-empty string, note an optional string."""
    if "refs" not in record:
        return
    refs = record.get("refs")
    if not isinstance(refs, list):
        findings.append("refs must be an array of {kind, locator, note} tables")
        return
    for i, ref in enumerate(refs):
        where = "refs[{}]".format(i)
        if not isinstance(ref, dict):
            findings.append("{} must be a {{kind, locator, note}} table".format(where))
            continue
        extra = set(ref) - REF_KEYS
        if extra:
            findings.append("{} unknown key(s): {}".format(where, ", ".join(sorted(extra))))
        if ref.get("kind") not in REF_KINDS:
            findings.append("{}.kind {!r} is not one of {} (spec 8.6)".format(
                where, ref.get("kind"), list(REF_KINDS)))
        if not isinstance(ref.get("locator"), str) or not ref.get("locator"):
            findings.append("{}.locator must be a non-empty string".format(where))
        if "note" in ref and not isinstance(ref.get("note"), str):
            findings.append("{}.note must be a string when present".format(where))


def _check_keyset(record, allowed, registered_vendors, findings):
    """Enforce the closed schema (spec 8.3, 8.7): every top-level key is either in `allowed` or a
    registered `x-<vendor>` extension TABLE. An unregistered or malformed vendor prefix, or a vendor
    entry that is not a table, is a finding; any other unknown key is a finding."""
    for key in record:
        if key in allowed:
            continue
        if isinstance(key, str) and key.startswith("x-"):
            if not _valid_extension_namespace(key):
                findings.append("extension key {!r} is not a valid x-<vendor> namespace (spec 8.7)".format(key))
            elif key not in registered_vendors:
                findings.append("extension {!r} is not registered in the manifest [vendors] "
                                "(spec 8.7)".format(key))
            elif not isinstance(record.get(key), dict):
                findings.append("extension {!r} must be a table (spec 8.7)".format(key))
        else:
            findings.append("unknown key {!r} (schemas are closed; spec 8.3)".format(key))


def _validate_type_specific(record, spec, findings):
    """Per-type field rules the envelope does not carry (spec 8.5). Reads the record's status STATE (the
    qualifier does not change these rules) to apply conditional bundles."""
    parsed, _ = parse_status(record.get("status"), spec) if not spec.reduced else (None, None)
    state = parsed[0] if parsed else None

    if spec.name == "finding":
        # Severity is graded at or after the fix decision, never before: forbidden while open (spec 8.5).
        if "severity" in record:
            if state == "open":
                findings.append("finding.severity is graded at or after the fix decision, never while "
                                "open (spec 8.5)")
            if not isinstance(record.get("severity"), str) or not record.get("severity"):
                findings.append("finding.severity must be a non-empty string when present")

    elif spec.name == "pending_decision":
        # All-or-none resolution bundle (spec 8.5): all three on `decided`, none on `open`/`withdrawn`.
        bundle = ("decision", "decided_at", "decided_by")
        present = [k for k in bundle if k in record]
        if state == "decided":
            missing = [k for k in bundle if k not in record]
            if missing:
                findings.append("a decided pending_decision must carry all of {}, missing {} "
                                "(all-or-none bundle, spec 8.5)".format(list(bundle), missing))
            if "decision" in record and (not isinstance(record.get("decision"), str)
                                         or not record.get("decision")):
                findings.append("pending_decision.decision must be a non-empty string")
            if "decided_at" in record and not _valid_timestamp(record.get("decided_at")):
                findings.append("pending_decision.decided_at must be an RFC 3339 UTC timestamp")
            if "decided_by" in record and (not isinstance(record.get("decided_by"), str)
                                           or not record.get("decided_by")):
                findings.append("pending_decision.decided_by must be a non-empty string")
        elif state in ("open", "withdrawn") and present:
            findings.append("an {} pending_decision must carry none of {}, has {} (all-or-none bundle, "
                            "spec 8.5)".format(state, list(bundle), present))

    elif spec.name == "block":
        # Scopes an enumerated list of record IDs the block covers (spec 8.5; field name defined here).
        scopes = record.get("scopes")
        if "scopes" not in record:
            findings.append("a block must carry `scopes`, a non-empty array of record IDs (spec 8.5)")
        elif not isinstance(scopes, list) or not scopes:
            findings.append("block.scopes must be a non-empty array of record IDs")
        else:
            for s in scopes:
                if _valid_id_shape(s) is None:
                    findings.append("block.scopes entry {!r} is not a well-formed <NS>-<n> id".format(s))

    elif spec.name == "autonomous_decision":
        # The classification basis and the action (spec 8.5; field names defined here). Optional strings.
        for k in ("classification", "action"):
            if k in record and (not isinstance(record.get(k), str) or not record.get(k)):
                findings.append("autonomous_decision.{} must be a non-empty string when present".format(k))


def _validate_worklog(record, spec, registered_vendors, findings):
    """The reduced worklog envelope (spec 8.3, 6.2): id, date, actor, kind, summary required; type
    (optional, must be `worklog`), detail, links, refs optional; NO status/title/created_at/updated_at."""
    _check_keyset(record, WORKLOG_KEYS, registered_vendors, findings)
    if "type" in record and record.get("type") != "worklog":
        findings.append("a worklog entry's type, when present, must be 'worklog'")
    if "date" not in record:
        findings.append("missing required field: date")
    elif not _valid_timestamp(record.get("date")):
        findings.append("date must be an RFC 3339 UTC timestamp")
    if "kind" not in record:
        findings.append("missing required field: kind")
    elif record.get("kind") not in WORKLOG_KINDS:
        findings.append("worklog kind {!r} is not one of {} (spec 6.2)".format(
            record.get("kind"), list(WORKLOG_KINDS)))
    if "summary" not in record or not isinstance(record.get("summary"), str) or not record.get("summary"):
        findings.append("a worklog entry must carry a non-empty one-line summary (spec 6.2)")
    if "detail" in record and not isinstance(record.get("detail"), str):
        findings.append("worklog detail must be a string when present")


# --- the record validator (spec 8.3) -----------------------------------------------------------------

def validate_record(record, expected_type=None, specs=None, registered_vendors=frozenset()):
    """Validate one parsed record against its baseline type schema. Returns a RecordValidation.

    The type is taken from the record's `type` field, or, for a typeless worklog entry (Appendix C omits
    it), from `expected_type`; when `expected_type` is also given for a typed record, a mismatch is a
    finding (spec 8.3 "type must match the file the record lives in"). `specs` defaults to the nine
    baseline types (U2M passes an extended roster). `registered_vendors` is the manifest's registered
    `x-<vendor>` set (U1's [vendors].registered) against which extension tables are checked.

    Outcome: CANNOT-EVALUATE when the input is not a table or its type cannot be identified; INVALID when
    an identified record violates its schema; VALID otherwise."""
    if specs is None:
        specs = BASELINE_SPECS
    if not isinstance(record, dict):
        return RecordValidation(CANNOT_EVALUATE, ["record is not a table"])

    findings = []
    tval = record.get("type")
    if isinstance(tval, str) and tval:
        rtype = tval
        if expected_type is not None and tval != expected_type:
            findings.append("type {!r} does not match the expected type {!r} (spec 8.3)".format(
                tval, expected_type))
    elif expected_type is not None:
        rtype = expected_type          # a typeless worklog entry identified by the file it lives in
    else:
        return RecordValidation(CANNOT_EVALUATE,
                                ["record has no `type` and no expected type: cannot identify it"])

    spec = specs.get(rtype)
    if spec is None:
        return RecordValidation(INVALID, ["{!r} is not a supported record type (baseline: {})".format(
            rtype, sorted(specs))], rtype=rtype)

    rid = record.get("id")
    shape = _valid_id_shape(rid)
    if shape is None:
        findings.append("id {!r} is not a well-formed <NS>-<n> id (spec 8.2)".format(rid))
    elif shape[0] != spec.namespace:
        findings.append("id namespace {!r} is not the {!r} namespace bound to type {!r} "
                        "(one-to-one binding, spec 8.2)".format(shape[0], spec.namespace, rtype))

    if spec.reduced:
        _validate_worklog(record, spec, registered_vendors, findings)
        _validate_actor(record, findings)
        _validate_links(record, findings)
        _validate_refs(record, findings)
        return RecordValidation(INVALID if findings else VALID, findings, rtype, rid)

    # The full envelope. Allowed keyset = envelope + this type's extra fields (+ registered x-<vendor>).
    _check_keyset(record, ENVELOPE_KEYS | spec.extra_keys, registered_vendors, findings)

    if "status" not in record:
        findings.append("missing required field: status")
    else:
        _, err = parse_status(record.get("status"), spec)
        if err is not None:
            findings.append("status: {}".format(err))

    title = record.get("title")
    if "title" not in record:
        findings.append("missing required field: title")
    elif not isinstance(title, str) or not title or "\n" in title:
        findings.append("title must be a non-empty single line")

    actor_kind = _validate_actor(record, findings)

    # created_at is required EXCEPT for an importer, which MAY omit it (spec 8.3); updated_at is always
    # required. Both, when present, are RFC 3339 UTC.
    if "created_at" not in record:
        if actor_kind != "importer":
            findings.append("missing required field: created_at")
    elif not _valid_timestamp(record.get("created_at")):
        findings.append("created_at must be an RFC 3339 UTC timestamp")
    if "updated_at" not in record:
        findings.append("missing required field: updated_at")
    elif not _valid_timestamp(record.get("updated_at")):
        findings.append("updated_at must be an RFC 3339 UTC timestamp")

    if "summary" in record and not isinstance(record.get("summary"), str):
        findings.append("summary must be a string when present")

    _validate_links(record, findings)
    _validate_refs(record, findings)
    _validate_type_specific(record, spec, findings)

    return RecordValidation(INVALID if findings else VALID, findings, rtype, rid)


# --- the transition validator (spec 8.4, 8.5) --------------------------------------------------------

def validate_transition(type_name, from_status, to_status, actor_kind, specs=None):
    """Validate a status transition against the grammar (spec 8.4, 8.5). Returns a TransitionCheck whose
    status is VALID (legal), INVALID (illegal), or CANNOT-EVALUATE (the type or a status is unparseable).

    The rules:
      - A forward transition from an unqualified working/initial state follows the type's transition
        table. Landing on a TERMINAL state, an assistant/automation actor MUST land `/proposed`; a
        maintainer/importer MUST land unqualified.
      - From a `state/proposed`, only a maintainer may RATIFY (drop the qualifier, same state) or REJECT
        (return to a working state); no other move is legal.
      - An unqualified TERMINAL state does not transition at all (no resurrection, spec 8.4); a revived
        concern is a new record, and supersession is a link, not a state edit.
    """
    if specs is None:
        specs = BASELINE_SPECS
    spec = specs.get(type_name)
    if spec is None:
        return TransitionCheck(CANNOT_EVALUATE, ["{!r} is not a supported record type".format(type_name)])

    findings = []
    if actor_kind not in ACTOR_KINDS:
        findings.append("actor kind {!r} is not one of {}".format(actor_kind, list(ACTOR_KINDS)))

    fparsed, ferr = parse_status(from_status, spec)
    tparsed, terr = parse_status(to_status, spec)
    if ferr is not None:
        findings.append("from-status: {}".format(ferr))
    if terr is not None:
        findings.append("to-status: {}".format(terr))
    if fparsed is None or tparsed is None:
        # A status the grammar cannot parse is unparseable input: cannot-evaluate, fail-closed.
        return TransitionCheck(CANNOT_EVALUATE, findings)

    from_state, from_qual = fparsed
    to_state, to_qual = tparsed

    if from_status == to_status:
        return TransitionCheck(INVALID, findings + ["{!r} to {!r} is not a transition (no change)".format(
            from_status, to_status)])

    from_terminal = from_state in spec.terminal

    if from_state == to_state:
        # A qualifier-only change on the same state: the only legal form is ratification.
        if from_qual == "proposed" and to_qual is None:
            if actor_kind != "maintainer":
                findings.append("only a maintainer may ratify a '/proposed' record (spec 8.4)")
        else:
            findings.append("illegal qualifier change {!r} to {!r} (only maintainer ratification, "
                            "'/proposed' to unqualified, is legal in place, spec 8.4)".format(
                                from_status, to_status))
    elif from_qual == "proposed":
        # From a proposed record only a maintainer rejection back to a WORKING state is legal (spec 8.4).
        if to_state in spec.terminal or to_qual is not None:
            findings.append("from a '/proposed' record only a maintainer rejection to a working state "
                            "is legal, not {!r} (spec 8.4)".format(to_status))
        elif actor_kind != "maintainer":
            findings.append("only a maintainer may reject a '/proposed' record to a working state "
                            "(spec 8.4)")
    elif from_terminal:
        # An unqualified terminal state never re-enters a working state (spec 8.4): no resurrection.
        findings.append("no resurrection: an unqualified terminal {} state {!r} does not transition; a "
                        "revived concern is a new record (spec 8.4)".format(spec.name, from_state))
    else:
        # A forward transition from an unqualified working/initial state.
        if to_state not in spec.transitions.get(from_state, frozenset()):
            findings.append("illegal transition {!r} to {!r} for a {} (spec 8.5)".format(
                from_state, to_state, spec.name))
        elif to_state in spec.terminal:
            if actor_kind in PROPOSER_KINDS:
                if to_qual != "proposed":
                    findings.append("a terminal transition by an {} actor must land '/proposed', not "
                                    "{!r} (spec 8.4)".format(actor_kind, to_status))
            elif to_qual is not None:
                findings.append("a terminal transition by a {} actor lands unqualified, not {!r} "
                                "(only assistant/automation propose, spec 8.4)".format(actor_kind, to_status))
        elif to_qual is not None:
            findings.append("a non-terminal transition does not carry '/proposed', got {!r}".format(to_status))

    return TransitionCheck(INVALID if findings else VALID, findings)


# --- counters (spec 8.2) -----------------------------------------------------------------------------

def validate_counters(data, known_namespaces=None):
    """Validate a parsed counters.toml (see the counters layout ambiguity note). Returns (high_water,
    findings): high_water is {namespace: int}. Each namespace is two uppercase letters (optionally
    restricted to `known_namespaces`); each value is a non-negative int (a bool is rejected)."""
    findings = []
    high_water = {}
    if not isinstance(data, dict):
        return high_water, ["counters.toml is not a table"]
    extra = set(data) - COUNTERS_TOP_KEYS
    if extra:
        findings.append("counters.toml unknown top-level key(s): {}".format(", ".join(sorted(extra))))
    if "schema" in data and not (type(data.get("schema")) is int):
        findings.append("counters.toml schema must be an integer")
    counters = data.get("counters")
    if counters is None:
        return high_water, findings
    if not isinstance(counters, dict):
        findings.append("[counters] is not a table")
        return high_water, findings
    for ns, val in counters.items():
        if not (isinstance(ns, str) and len(ns) == 2 and ns.isupper() and ns.isalpha()):
            findings.append("[counters] key {!r} is not a two-letter uppercase namespace".format(ns))
            continue
        if known_namespaces is not None and ns not in known_namespaces:
            findings.append("[counters] namespace {!r} is not a known namespace".format(ns))
        # A bool is an int subclass; a high-water is a genuine non-negative int, never True/False.
        if type(val) is not int or val < 0:
            findings.append("[counters].{} must be a non-negative integer high-water mark".format(ns))
            continue
        high_water[ns] = val
    return high_water, findings


def high_water(high, ns):
    """The current high-water for a namespace, 0 when it has allocated nothing yet."""
    return high.get(ns, 0)


def next_id(high, ns):
    """Allocate the next id for a namespace: returns (id_string, new_high_water). The high-water only
    ever increases by one, so an id is never reused (spec 8.2). The caller records new_high_water back to
    counters.toml under the store lock as one atomic claim (spec 8.2, the atomic-claim-from-pool rule)."""
    n = high_water(high, ns) + 1
    return "{}-{}".format(ns, n), n


def check_monotonic(old_high, new_high):
    """Confirm counters only ever advance (spec 8.2: never reset, IDs never reused). Every namespace in
    `old_high` must be present in `new_high` with a value greater than or equal to its old high-water; a
    regression or a dropped namespace (which would let a later allocation reuse an id) is a finding."""
    findings = []
    for ns, old in old_high.items():
        if ns not in new_high:
            findings.append("namespace {!r} vanished from counters (would allow id reuse; spec 8.2)".format(ns))
        elif new_high[ns] < old:
            findings.append("namespace {!r} high-water regressed {} to {} (counters never reset; "
                            "spec 8.2)".format(ns, old, new_high[ns]))
    return findings


def check_ids_within_counters(ids, high):
    """Confirm every allocated id's number is within its namespace's high-water (spec 8.2): an id above
    the high-water means the counter never reserved it, the reuse hazard the atomic claim prevents. `ids`
    is an iterable of id strings; a malformed id, or an unknown namespace, is itself a finding."""
    findings = []
    for rid in ids:
        shape = _valid_id_shape(rid)
        if shape is None:
            findings.append("id {!r} is not a well-formed <NS>-<n> id".format(rid))
            continue
        ns, n = shape
        if ns not in high:
            findings.append("id {!r} uses namespace {!r} that counters.toml does not track".format(rid, ns))
        elif n > high[ns]:
            findings.append("id {!r} exceeds the {} high-water {} (unreserved id; spec 8.2)".format(
                rid, ns, high[ns]))
    return findings


def check_unique_ids(ids):
    """Confirm no id is reused across a set of records (spec 8.2: IDs are never reused). Returns a finding
    per duplicated id."""
    findings = []
    seen = set()
    for rid in ids:
        if rid in seen:
            findings.append("duplicate id {!r}: IDs are never reused (spec 8.2)".format(rid))
        else:
            seen.add(rid)
    return findings


# --- self-test ---------------------------------------------------------------------------------------

def self_test():
    """Envelope + type-schema + status/transition + counters invariants over synthetic vectors. Judged on
    the returned status/finding values, never by grepping output (spec 8; the isolate-verifiers rule)."""
    failures = []
    checked = 0

    def check(name, cond):
        nonlocal checked
        checked += 1
        if not cond:
            failures.append(name)

    TS = "2026-08-12T09:14:02Z"
    REG = frozenset({"x-aiqt"})

    def envelope(rtype, ns_num, status, **over):
        r = {"id": "{}-{}".format(BASELINE_TYPES[rtype], ns_num), "type": rtype, "status": status,
             "title": "A synthetic {} record".format(rtype), "created_at": TS, "updated_at": TS,
             "actor": {"kind": "maintainer"}}
        r.update(over)
        return r

    # 1: a valid record for every baseline type validates VALID with no findings.
    valid_vectors = {
        "backlog_item": envelope("backlog_item", 1, "open"),
        "done": envelope("done", 1, "recorded", links=[{"rel": "receipt_of", "id": "BI-1"}]),
        "finding": envelope("finding", 7, "fixed/proposed", actor={"kind": "assistant"},
                            severity="minor", links=[{"rel": "remediates", "id": "BI-42"}],
                            refs=[{"kind": "path", "locator": ".working/BACKLOG.md", "note": "drift"}]),
        "pending_decision": envelope("pending_decision", 1, "open"),
        "autonomous_decision": envelope("autonomous_decision", 1, "recorded",
                                        actor={"kind": "assistant"}, classification="ACT", action="merged"),
        "block": envelope("block", 1, "active", scopes=["BI-1", "BI-2"]),
        "handoff": envelope("handoff", 1, "current"),
        "reference": envelope("reference", 1, "recorded",
                              refs=[{"kind": "doc", "locator": "OPF-SPEC.md 8", "note": "envelope"}]),
    }
    for name, rec in valid_vectors.items():
        res = validate_record(rec, registered_vendors=REG)
        check("valid-envelope-{}".format(name), res.status == VALID and not res.findings)

    # 1b: a valid worklog entry (reduced envelope, typeless, identified by expected_type).
    wl = {"id": "WL-131", "date": TS, "actor": {"kind": "maintainer"}, "kind": "fixed",
          "summary": "Close the stale-view gap", "links": [{"rel": "resolves", "id": "BI-42"}]}
    check("valid-worklog", validate_record(wl, expected_type="worklog").status == VALID)
    # a worklog with an unknown change kind, a missing summary, and a bad date each fail closed.
    check("worklog-bad-kind", validate_record(dict(wl, kind="merged"),
                                              expected_type="worklog").status == INVALID)
    check("worklog-no-summary", validate_record({k: v for k, v in wl.items() if k != "summary"},
                                                expected_type="worklog").status == INVALID)
    check("worklog-status-key-rejected",   # status is not in the reduced envelope (closed keyset)
          validate_record(dict(wl, status="recorded"), expected_type="worklog").status == INVALID)

    # 2: a valid vendor extension passes only when registered; unregistered and malformed prefixes fail.
    rec_x = envelope("finding", 8, "open")
    rec_x["x-aiqt"] = {"verification": "triple-family"}
    check("vendor-registered-ok", validate_record(rec_x, registered_vendors=REG).status == VALID)
    check("vendor-unregistered-invalid", validate_record(rec_x, registered_vendors=frozenset()).status == INVALID)
    rec_badx = envelope("finding", 9, "open")
    rec_badx["x-"] = {}
    check("vendor-bad-shape-invalid", validate_record(rec_badx, registered_vendors=REG).status == INVALID)
    rec_xnotable = envelope("finding", 10, "open")
    rec_xnotable["x-aiqt"] = "not-a-table"
    check("vendor-not-table-invalid", validate_record(rec_xnotable, registered_vendors=REG).status == INVALID)

    # 3: an unknown top-level key is a finding (closed schema).
    check("unknown-key-invalid", validate_record(envelope("finding", 11, "open", mystery=1)).status == INVALID)

    # 4: a bad id and a namespace/type mismatch each fail closed.
    check("bad-id-invalid", validate_record(dict(envelope("finding", 1, "open"), id="FN1")).status == INVALID)
    check("bad-id-leading-zero", validate_record(dict(envelope("finding", 1, "open"), id="FN-01")).status == INVALID)
    mism = validate_record(dict(envelope("finding", 1, "open"), id="ZZ-1"))
    check("namespace-mismatch-invalid", mism.status == INVALID)
    check("namespace-mismatch-named", any("one-to-one binding" in f for f in mism.findings))

    # 5: required / optional / conditional presence.
    check("missing-title-invalid",
          validate_record({k: v for k, v in envelope("finding", 1, "open").items() if k != "title"}).status == INVALID)
    check("missing-updated-at-invalid",
          validate_record({k: v for k, v in envelope("finding", 1, "open").items() if k != "updated_at"}).status == INVALID)
    # an importer MAY omit created_at (spec 8.3); a maintainer may not.
    imp = {k: v for k, v in envelope("done", 1, "recorded").items() if k != "created_at"}
    imp["actor"] = {"kind": "importer"}
    check("importer-omits-created-at-ok", validate_record(imp).status == VALID)
    maint_missing = {k: v for k, v in envelope("done", 1, "recorded").items() if k != "created_at"}
    check("maintainer-missing-created-at-invalid", validate_record(maint_missing).status == INVALID)
    # severity is forbidden while a finding is open (conditional-before rule).
    check("severity-while-open-invalid",
          validate_record(envelope("finding", 1, "open", severity="minor")).status == INVALID)

    # 6: the pending_decision all-or-none bundle.
    dec_ok = envelope("pending_decision", 2, "decided", decision="chose X", decided_at=TS, decided_by="maintainer")
    check("decision-bundle-complete-ok", validate_record(dec_ok).status == VALID)
    dec_half = envelope("pending_decision", 3, "decided", decision="chose X", decided_at=TS)  # no decided_by
    hv = validate_record(dec_half)
    check("decision-bundle-half-invalid", hv.status == INVALID)
    check("decision-bundle-half-named", any("all-or-none" in f for f in hv.findings))
    dec_open_field = envelope("pending_decision", 4, "open", decision="premature")
    check("decision-open-with-field-invalid", validate_record(dec_open_field).status == INVALID)

    # 7: bad link shape / rel, and bad ref kind.
    check("bad-link-rel-invalid",
          validate_record(envelope("finding", 1, "open", links=[{"rel": "blocks", "id": "BI-1"}])).status == INVALID)
    check("bad-link-shape-invalid",
          validate_record(envelope("finding", 1, "open", links=[{"rel": "relates"}])).status == INVALID)
    check("link-not-table-invalid",
          validate_record(envelope("finding", 1, "open", links=["BI-1"])).status == INVALID)
    check("bad-ref-kind-invalid",
          validate_record(envelope("finding", 1, "open",
                                   refs=[{"kind": "ftp", "locator": "x", "note": "y"}])).status == INVALID)
    # a bad block scope id fails closed.
    check("bad-block-scope-invalid",
          validate_record(envelope("block", 2, "active", scopes=["not-an-id"])).status == INVALID)
    check("block-missing-scopes-invalid",
          validate_record(envelope("block", 3, "active")).status == INVALID)

    # 8: status well-formedness (a proposed on a non-proposable state, an unknown state).
    check("proposed-on-nonproposable-invalid",
          validate_record(envelope("backlog_item", 2, "open/proposed")).status == INVALID)
    check("block-active-proposed-ok",   # the one non-terminal proposable state
          validate_record(envelope("block", 4, "active/proposed", scopes=["BI-1"])).status == VALID)
    check("unknown-state-invalid",
          validate_record(envelope("finding", 1, "frozen")).status == INVALID)

    # 9: transitions. Legal forward, illegal target, and the /proposed landing rules.
    check("txn-forward-legal",
          validate_transition("backlog_item", "open", "active", "assistant").status == VALID)
    check("txn-illegal-target",
          validate_transition("backlog_item", "open", "done", "maintainer").status == INVALID)
    check("txn-assistant-terminal-proposed-legal",
          validate_transition("finding", "open", "fixed/proposed", "assistant").status == VALID)
    missing_prop = validate_transition("finding", "open", "fixed", "assistant")
    check("txn-assistant-terminal-missing-proposed-invalid", missing_prop.status == INVALID)
    check("txn-assistant-terminal-missing-proposed-named",
          any("must land '/proposed'" in f for f in missing_prop.findings))
    check("txn-maintainer-terminal-unqualified-legal",
          validate_transition("finding", "open", "fixed", "maintainer").status == VALID)
    check("txn-maintainer-terminal-proposed-invalid",
          validate_transition("finding", "open", "fixed/proposed", "maintainer").status == INVALID)

    # 9b: ratification and rejection are maintainer-only; no resurrection from an unqualified terminal.
    check("txn-ratify-maintainer-legal",
          validate_transition("finding", "fixed/proposed", "fixed", "maintainer").status == VALID)
    check("txn-ratify-assistant-invalid",
          validate_transition("finding", "fixed/proposed", "fixed", "assistant").status == INVALID)
    check("txn-reject-to-working-legal",
          validate_transition("finding", "fixed/proposed", "open", "maintainer").status == VALID)
    resurrect = validate_transition("finding", "fixed", "open", "maintainer")
    check("txn-resurrection-invalid", resurrect.status == INVALID)
    check("txn-resurrection-named", any("no resurrection" in f for f in resurrect.findings))
    check("txn-block-ratify-legal",
          validate_transition("block", "active/proposed", "active", "maintainer").status == VALID)
    check("txn-noop-invalid", validate_transition("finding", "open", "open", "maintainer").status == INVALID)
    check("txn-unknown-type-cannot-eval",
          validate_transition("nope", "open", "active", "maintainer").status == CANNOT_EVALUATE)
    check("txn-unparseable-status-cannot-eval",
          validate_transition("finding", "bogus", "fixed", "maintainer").status == CANNOT_EVALUATE)

    # 10: counters. Schema, monotonic allocation, no reset, ids-within-high-water, uniqueness.
    hw, cfindings = validate_counters({"schema": 1, "counters": {"BI": 42, "FN": 7, "WL": 131}})
    check("counters-schema-ok", not cfindings and hw == {"BI": 42, "FN": 7, "WL": 131})
    _, bad_cf = validate_counters({"counters": {"BI": -1}})
    check("counters-negative-invalid", bad_cf)
    _, bool_cf = validate_counters({"counters": {"BI": True}})   # bool is not a high-water int
    check("counters-bool-invalid", bool_cf)
    nid, newhw = next_id({"BI": 42}, "BI")
    check("counters-allocate-increments", nid == "BI-43" and newhw == 43)
    check("counters-allocate-from-empty", next_id({}, "FN") == ("FN-1", 1))
    check("counters-monotonic-ok", not check_monotonic({"BI": 42}, {"BI": 43}))
    check("counters-regression-invalid", check_monotonic({"BI": 42}, {"BI": 41}))
    check("counters-dropped-ns-invalid", check_monotonic({"BI": 42, "FN": 7}, {"BI": 42}))
    check("counters-id-within-hw-ok", not check_ids_within_counters(["BI-1", "BI-42"], {"BI": 42}))
    check("counters-id-exceeds-hw-invalid", check_ids_within_counters(["BI-43"], {"BI": 42}))
    check("counters-unknown-ns-invalid", check_ids_within_counters(["ZZ-1"], {"BI": 42}))
    check("ids-unique-ok", not check_unique_ids(["BI-1", "BI-2", "FN-1"]))
    check("ids-duplicate-invalid", check_unique_ids(["BI-1", "BI-1"]))

    # 11: non-table / unidentifiable inputs are CANNOT-EVALUATE, never a silent pass.
    check("not-a-table-cannot-eval", validate_record([]).status == CANNOT_EVALUATE)
    check("no-type-no-expected-cannot-eval", validate_record({"id": "BI-1"}).status == CANNOT_EVALUATE)
    check("unsupported-type-invalid", validate_record({"type": "artifact", "id": "AR-1"}).status == INVALID)

    if failures:
        print("OPF-SCHEMA SELF-TEST: FAIL ({} of {} checks failed)".format(len(failures), checked))
        for f in failures:
            print("  FAILED: {}".format(f))
        return 1
    print("OPF-SCHEMA SELF-TEST: PASS ({} envelope, type-schema, transition, and counters checks)".format(
        checked))
    return 0


if __name__ == "__main__":
    sys.exit(self_test())
