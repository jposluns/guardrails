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
  - TIMESTAMPS are validated as RFC 3339 UTC STRINGS, optional fractional seconds, calendar-validated.
    The spec requires "RFC 3339 UTC" (spec 8.3, 6.1), and RFC 3339 expresses UTC either as the `Z`
    offset (the form of every spec example, Appendix A/B/C) OR as the `+00:00` numeric offset; BOTH are
    accepted, since `2026-06-14T00:00:00+00:00` is a valid RFC 3339 UTC instant that a Z-only check would
    wrongly reject. Any OTHER numeric offset (e.g. `+05:30`, or `-00:00` which RFC 3339 marks as an
    unknown local offset) is not UTC and is rejected, fail-closed. A native TOML datetime (an UNQUOTED
    value tomllib parses to datetime) is also REJECTED as a wrong type; the finalizer may widen if
    adopters need the native form.
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
    VALID, INVALID, CANNOT_EVALUATE, BASELINE_TYPES, MODULE_TYPES, IMPORTER_TYPES,
    _valid_extension_namespace,
    _str_token_set, _is_str_token_control, _is_item_collection,
)

# The one schema version this unit understands (mirrors version.toml / worklog.toml / counters.toml
# `schema = 1`). A ledger carrying any other schema is fail-closed, never parsed under v1 assumptions.
SUPPORTED_SCHEMA = 1

# Every namespace bound to a real record type in the section 8.1 taxonomy: the baseline types, the
# module-tier types (namespace is the first element of the (namespace, module) pair), and the importer-
# only quarantine type. A `links` id may point at ANY record type, so its namespace must be one of
# these; a namespace bound to no type (e.g. ZZ, the reserved-excluded `transaction`/TX, the unassigned
# CL) is not a real record-type namespace (spec 8.1, 8.2 one-to-one binding).
RECORD_NAMESPACES = (
    frozenset(BASELINE_TYPES.values())
    | frozenset(ns for ns, _module in MODULE_TYPES.values())
    | frozenset(IMPORTER_TYPES.values())
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

# The worklog change-kind vocabulary (spec 6.2). A manifest MAY register additional kinds; callers pass
# them via the `registered_kinds` argument of validate_record/validate_worklog, and the effective set is
# these built-ins UNION the registered kinds (these remain the default when none are supplied).
WORKLOG_KINDS = ("added", "changed", "fixed", "removed", "security", "docs", "infra")

ACTOR_KEYS = frozenset({"kind", "id"})
LINK_KEYS = frozenset({"rel", "id"})
REF_KEYS = frozenset({"kind", "locator", "note"})

# The full (non-worklog) envelope keyset; types add their own extra keys on top (EXTRA_KEYS below).
ENVELOPE_KEYS = frozenset({"id", "type", "status", "title", "created_at", "updated_at",
                           "actor", "summary", "links", "refs"})
# The reduced worklog envelope keyset (spec 8.3, 6.2): no status/title/created_at/updated_at; `date`
# replaces the creation/update timestamps. Per spec 8.3 (the reduced-envelope list) `type` is NOT a
# member of the reduced envelope, so under the closed-schema rule a `type` key on a worklog entry is a
# validation failure (FORBIDDEN, my reading): a worklog entry is identified by the file it lives in, not
# by a `type` field.
WORKLOG_KEYS = frozenset({"id", "date", "actor", "kind", "summary", "detail", "links", "refs"})

# `<NS>-<n>`: two uppercase letters, a hyphen, a positive integer with no leading zero (spec 8.2).
_ID_RE = re.compile(r"^([A-Z]{2})-([1-9][0-9]*)$")
# RFC 3339 UTC with optional fractional seconds. UTC is expressible as `Z` OR the `+00:00` offset (both
# denote a zero offset); both are accepted, any other numeric offset (e.g. +05:30) is not UTC and is
# rejected (see the timestamp ambiguity note above).
_TS_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(\.\d+)?(?:Z|\+00:00)$")

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
        # A created-terminal factual receipt that awaits no ratification, so `recorded` is NOT proposable
        # (spec 8.4: such a record carries no `/proposed` qualifier), mirroring worklog.
        "done", initial="recorded", working=set(), terminal={"recorded"},
        transitions={}, proposable=set()),
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
        # A created-terminal ACT record that awaits no ratification, so `recorded` is NOT proposable
        # (spec 8.4), mirroring worklog.
        "autonomous_decision", initial="recorded", working=set(), terminal={"recorded"},
        transitions={}, proposable=set(), extra_keys={"classification", "action"}),
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
        # A created-terminal captured reference that awaits no ratification, so `recorded` is NOT
        # proposable (spec 8.4), mirroring worklog.
        "reference", initial="recorded", working=set(), terminal={"recorded"},
        transitions={}, proposable=set()),
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
    """True when value is an RFC 3339 UTC string (a `Z` or `+00:00` zero offset) that is a real calendar
    instant (see the timestamp ambiguity note). A native TOML datetime or a non-UTC numeric offset is
    rejected."""
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


def _instant_key(value):
    """A comparable ordering key for a VALID RFC 3339 UTC timestamp string, or None when it is not one.
    Every accepted value is a zero UTC offset, so the calendar fields plus any fractional seconds order
    two instants directly (a string compare is unsafe: `...02Z` and `...02+00:00` are the same instant but
    differ lexically). Used to check timestamp chronology (MINOR 1)."""
    m = _TS_RE.match(value) if isinstance(value, str) else None
    if not m:
        return None
    frac = float(m.group(7)) if m.group(7) else 0.0
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)),
            int(m.group(4)), int(m.group(5)), int(m.group(6)), frac)


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
    if not isinstance(kind, str):
        # A non-string kind (e.g. a TOML-valid list/dict) is unhashable and would raise on the tuple
        # membership below: guard by type first and fail closed with a clean finding (spec 8.3).
        findings.append("actor.kind must be a string")
        kind = None
    elif kind not in ACTOR_KINDS:
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
        if not isinstance(link.get("rel"), str):
            # A non-string rel (e.g. a TOML-valid list/dict) is unhashable and would raise on the set
            # membership below: guard by type first and fail closed with a clean finding (spec 8.6).
            findings.append("{}.rel must be a string".format(where))
        elif link.get("rel") not in LINK_RELS:
            findings.append("{}.rel {!r} is not one of {} (spec 8.6)".format(
                where, link.get("rel"), list(LINK_RELS)))
        shape = _valid_id_shape(link.get("id"))
        if shape is None:
            findings.append("{}.id {!r} is not a well-formed <NS>-<n> id".format(where, link.get("id")))
        elif shape[0] not in RECORD_NAMESPACES:
            # A well-formed id whose namespace maps to no record type (e.g. ZZ-1) cannot point at any
            # record: reconcile against the section 8.1 taxonomy, not just the <NS>-<n> shape (spec 8.1/8.2).
            findings.append("{}.id {!r} uses namespace {!r} that is bound to no record type in the "
                            "section 8.1 taxonomy (spec 8.1/8.2)".format(where, link.get("id"), shape[0]))


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
        if not isinstance(ref.get("kind"), str):
            # A non-string kind (e.g. a TOML-valid list/dict) is unhashable and would raise on the set
            # membership below: guard by type first and fail closed with a clean finding (spec 8.6).
            findings.append("{}.kind must be a string".format(where))
        elif ref.get("kind") not in REF_KINDS:
            findings.append("{}.kind {!r} is not one of {} (spec 8.6)".format(
                where, ref.get("kind"), list(REF_KINDS)))
        if not isinstance(ref.get("locator"), str) or not ref.get("locator"):
            findings.append("{}.locator must be a non-empty string".format(where))
        # note is a required member of every ref on every record, not only a reference record (spec 8.6
        # {kind, locator, note}; m2).
        if "note" not in ref:
            findings.append("{}.note is required on every ref (spec 8.6 {{kind, locator, note}})".format(where))
        elif not isinstance(ref.get("note"), str):
            findings.append("{}.note must be a string".format(where))


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
                sshape = _valid_id_shape(s)
                if sshape is None:
                    findings.append("block.scopes entry {!r} is not a well-formed <NS>-<n> id".format(s))
                elif sshape[0] not in RECORD_NAMESPACES:
                    # A scoped id must name a real record type in the section 8.1 taxonomy (spec 8.1/8.2);
                    # a namespace bound to no type (e.g. ZZ) scopes nothing (M3).
                    findings.append("block.scopes entry {!r} uses namespace {!r} bound to no record type "
                                    "in the section 8.1 taxonomy (spec 8.1/8.2)".format(s, sshape[0]))
        # A block's actor names its creator, so the proposal qualifier is a creation-time rule on the
        # snapshot (spec 8.4/8.5): an assistant/automation block MUST be 'active/proposed' (a proposal,
        # not a grant); a maintainer/importer block MUST be a bare 'active' grant (only assistant and
        # automation carry the proposal qualifier) (M2).
        actor = record.get("actor")
        akind = actor.get("kind") if isinstance(actor, dict) else None
        # akind may be a TOML-valid non-string (list/dict) which is unhashable and would raise on the
        # membership tests below; guard by type first (a non-string actor kind is flagged by
        # _validate_actor). Only a string kind is compared against the proposer/maintainer sets.
        if not isinstance(akind, str):
            akind = None
        if akind in PROPOSER_KINDS and record.get("status") == "active":
            findings.append("a block created by an {} actor must be 'active/proposed', not a bare "
                            "'active' grant (spec 8.4/8.5)".format(akind))
        elif akind in ("maintainer", "importer") and record.get("status") == "active/proposed":
            findings.append("a block created by a {} actor must be a bare 'active' grant, not "
                            "'active/proposed' (only assistant/automation carry the proposal qualifier, "
                            "spec 8.4/8.5)".format(akind))

    elif spec.name == "done":
        # A done record is a one-to-one completion receipt: it MUST link EXACTLY ONE `receipt_of` to a
        # backlog_item (BI namespace) (spec 8.1/8.5, M3). A standalone receipt with no such link is legal
        # only for IMPORTED history (spec 8.1), and a standalone importer done MUST then carry at least one
        # provenance ref (spec 8.1/8.5, M4); any non-importer with no receipt_of is invalid.
        links = record.get("links")
        receipts = [l for l in links if isinstance(l, dict) and l.get("rel") == "receipt_of"] \
            if isinstance(links, list) else []
        actor = record.get("actor")
        is_importer = isinstance(actor, dict) and actor.get("kind") == "importer"
        refs = record.get("refs")
        has_provenance = isinstance(refs, list) and bool(refs)
        if not receipts:
            if not is_importer:
                findings.append("a done record must carry a receipt_of link to its backlog item; a "
                                "standalone receipt is legal only for imported history (spec 8.1/8.5)")
            elif not has_provenance:
                findings.append("a standalone importer done (no receipt_of) must carry at least one "
                                "provenance reference (spec 8.1/8.5)")
        if len(receipts) > 1:
            findings.append("a done record must carry exactly one receipt_of link (one-to-one completion "
                            "receipt, spec 8.5), has {}".format(len(receipts)))
        for l in receipts:
            lshape = _valid_id_shape(l.get("id"))
            if lshape is not None and lshape[0] != BASELINE_TYPES["backlog_item"]:
                findings.append("a done record's receipt_of must point to a backlog_item ({} namespace), "
                                "not {!r} (spec 8.1/8.5)".format(BASELINE_TYPES["backlog_item"], l.get("id")))

    elif spec.name == "autonomous_decision":
        # An immutable ACT record MUST carry its classification basis AND its action (spec 8.5); both are
        # required non-empty strings (field names defined here).
        for k in ("classification", "action"):
            if k not in record:
                findings.append("an autonomous_decision must carry a non-empty {} (spec 8.5)".format(k))
            elif not isinstance(record.get(k), str) or not record.get(k):
                findings.append("autonomous_decision.{} must be a non-empty string".format(k))

    elif spec.name == "reference":
        # A reference record IS a captured reference: it carries at least one ref (spec 8.6 reference
        # shape). Each ref's {kind, locator, note} completeness (note now required on every record's refs,
        # m2) is enforced by _validate_refs, so only the at-least-one requirement is reference-specific.
        refs = record.get("refs")
        if not isinstance(refs, list) or not refs:
            findings.append("a reference must carry at least one {kind, locator, note} ref (spec 8.6)")


def _validate_worklog(record, spec, registered_vendors, findings, registered_kinds=None):
    """The reduced worklog envelope (spec 8.3, 6.2): id, date, actor, kind, summary required; type
    (optional, must be `worklog`), detail, links, refs optional; NO status/title/created_at/updated_at.
    `registered_kinds` is the manifest's additional change kinds (spec 6.2); the accepted set is the
    built-in WORKLOG_KINDS UNION those, so the built-ins remain the default when none are supplied."""
    # `type` is not part of the reduced worklog envelope (spec 8.3); a `type` key is an unknown key under
    # the closed schema and is caught by _check_keyset, never tolerated here (M1).
    _check_keyset(record, WORKLOG_KEYS, registered_vendors, findings)
    if "date" not in record:
        findings.append("missing required field: date")
    elif not _valid_timestamp(record.get("date")):
        findings.append("date must be an RFC 3339 UTC timestamp")
    allowed_kinds = set(WORKLOG_KINDS)
    if registered_kinds is not None:
        # `registered_kinds` is a manifest-supplied control (spec 6.2). It MUST be a list/tuple of
        # strings. A bare string would splat into its characters under set() (so kind="p" from "perf"
        # would wrongly validate: a fail-open), and a non-string element (e.g. a list-of-list [["perf"]])
        # is unhashable and would raise on set(): validate by type first and fail CLOSED to the built-in
        # WORKLOG_KINDS only, appending a clean finding, never iterating a string into characters and
        # never crashing (guard-input-soundness; spec 6.2). The presence test is `is not None`, NOT
        # truthiness: a falsey-but-malformed control ("", {}, 0, False) is a wrong-shape control that is
        # REJECTED with a finding, never silently accepted as "no additional kinds" (M-round3). A genuinely
        # empty list/tuple is well-formed and adds no kinds without a finding.
        if isinstance(registered_kinds, (list, tuple)) and all(
                isinstance(k, str) for k in registered_kinds):
            allowed_kinds |= set(registered_kinds)
        else:
            findings.append("registered worklog kinds must be a list of strings")
    if "kind" not in record:
        findings.append("missing required field: kind")
    elif not isinstance(record.get("kind"), str):
        # A non-string kind (e.g. a TOML-valid list/dict) is unhashable and would raise on the set
        # membership below: guard by type first and fail closed with a clean finding (spec 6.2).
        findings.append("worklog kind must be a string")
    elif record.get("kind") not in allowed_kinds:
        findings.append("worklog kind {!r} is not one of {} (spec 6.2)".format(
            record.get("kind"), sorted(allowed_kinds)))
    summ = record.get("summary")
    if "summary" not in record or not isinstance(summ, str) or not summ:
        findings.append("a worklog entry must carry a non-empty one-line summary (spec 6.2)")
    elif "\n" in summ or "\r" in summ:
        # A worklog summary is one line (spec 6.2), like the envelope title (m1).
        findings.append("a worklog entry summary must be a single line (spec 6.2)")
    if "detail" in record and not isinstance(record.get("detail"), str):
        findings.append("worklog detail must be a string when present")


# --- the record validator (spec 8.3) -----------------------------------------------------------------

def validate_record(record, expected_type=None, specs=None, registered_vendors=frozenset(),
                    registered_kinds=None):
    """Validate one parsed record against its baseline type schema. Returns a RecordValidation.

    The type is taken from the record's `type` field, or, for a typeless worklog entry (Appendix C omits
    it), from `expected_type`; when `expected_type` is also given for a typed record, a mismatch is a
    finding (spec 8.3 "type must match the file the record lives in"). `specs` defaults to the nine
    baseline types (U2M passes an extended roster). `registered_vendors` is the manifest's registered
    `x-<vendor>` set (U1's [vendors].registered) against which extension tables are checked.
    `registered_kinds` is the manifest's additional worklog change kinds (spec 6.2); the built-in
    WORKLOG_KINDS remain the accepted default when it is None.

    Outcome: CANNOT-EVALUATE when the input is not a table or its type cannot be identified; INVALID when
    an identified record violates its schema; VALID otherwise."""
    if specs is None:
        specs = BASELINE_SPECS
    elif not isinstance(specs, dict):
        # `specs` is a control (the type roster); a non-mapping would crash on specs.get() below. Fail
        # closed rather than allocate against an unreadable roster (guard-input-soundness; spec 8.3).
        return RecordValidation(CANNOT_EVALUATE, ["specs roster is not a mapping (fail-closed)"])
    if not isinstance(record, dict):
        return RecordValidation(CANNOT_EVALUATE, ["record is not a table"])
    if expected_type is not None and not isinstance(expected_type, str):
        # expected_type names the type the record must match; a non-string (e.g. a list) is unhashable
        # and would crash on specs.get(expected_type), or later become `rtype` and crash on specs.get(rtype).
        # Guard by type first and fail closed, never a TypeError (guard-input-soundness; spec 8.3).
        return RecordValidation(CANNOT_EVALUATE, ["expected_type must be a string or None (fail-closed)"])
    # registered_vendors is the manifest's registered x-<vendor> allow-set. Normalize it to an
    # exact-token frozenset at the boundary so _check_keyset's `key not in registered_vendors` is always
    # an exact-token membership over a set, never a SUBSTRING match against a bare string (a fail-open
    # that admitted an unregistered x- extension) and never a crash on a non-collection (spec 8.7; M-round3).
    registered_vendors = _str_token_set(registered_vendors)

    findings = []
    tval = record.get("type")
    espec = specs.get(expected_type) if expected_type is not None else None
    if isinstance(tval, str) and tval:
        rtype = tval
        if expected_type is not None and tval != expected_type:
            findings.append("type {!r} does not match the expected type {!r} (spec 8.3)".format(
                tval, expected_type))
    elif espec is not None and espec.reduced:
        # Only the reduced worklog envelope omits `type` (spec 8.3); a typeless entry is identified by the
        # file it lives in via expected_type (M1).
        rtype = expected_type
    elif expected_type is not None:
        # A full-envelope record MUST carry a string `type` (spec 8.3, required field): do NOT substitute
        # expected_type for a missing or non-string `type`, so a finding without `type`, or type=7, is
        # INVALID rather than silently accepted (M1). Identify by expected_type so remaining findings surface.
        findings.append("missing or non-string required field: type (spec 8.3)")
        rtype = expected_type
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
        _validate_worklog(record, spec, registered_vendors, findings, registered_kinds)
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
    elif not isinstance(title, str) or not title or "\n" in title or "\r" in title:
        findings.append("title must be a non-empty single line")

    actor_kind = _validate_actor(record, findings)

    # created_at is required EXCEPT for an importer, which MAY omit it (spec 8.3); updated_at is always
    # required. Both, when present, are RFC 3339 UTC.
    if "created_at" not in record:
        if actor_kind != "importer":
            findings.append("missing required field: created_at")
        else:
            # An importer MAY omit created_at, but the omission is recorded as unknown via an import-
            # provenance reference, never guessed (spec 8.3): at least one ref is then required (M4).
            refs = record.get("refs")
            if not (isinstance(refs, list) and refs):
                findings.append("an importer that omits created_at must carry an import-provenance "
                                "reference (spec 8.3)")
    elif not _valid_timestamp(record.get("created_at")):
        findings.append("created_at must be an RFC 3339 UTC timestamp")
    if "updated_at" not in record:
        findings.append("missing required field: updated_at")
    elif not _valid_timestamp(record.get("updated_at")):
        findings.append("updated_at must be an RFC 3339 UTC timestamp")

    # When both timestamps are present and valid, updated_at (the time of the last transition) cannot
    # precede created_at (spec 8.3; MINOR 1).
    if ("created_at" in record and "updated_at" in record
            and _valid_timestamp(record.get("created_at"))
            and _valid_timestamp(record.get("updated_at"))
            and _instant_key(record.get("updated_at")) < _instant_key(record.get("created_at"))):
        findings.append("updated_at must not precede created_at (spec 8.3)")

    if "summary" in record and not isinstance(record.get("summary"), str):
        findings.append("summary must be a string when present")

    _validate_links(record, findings)
    _validate_refs(record, findings)
    _validate_type_specific(record, spec, findings)

    return RecordValidation(INVALID if findings else VALID, findings, rtype, rid)


# --- the transition validator (spec 8.4, 8.5) --------------------------------------------------------

def validate_transition(type_name, from_status, to_status, actor_kind, pre_proposal_state=None,
                        reason=None, specs=None):
    """Validate a status transition against the grammar (spec 8.4, 8.5). Returns a TransitionCheck whose
    status is VALID (legal), INVALID (illegal), or CANNOT-EVALUATE (the type or a status is unparseable, or
    a rejection whose pre-proposal state was not supplied).

    The rules:
      - A forward transition from an unqualified working/initial state follows the type's transition
        table. Landing on a TERMINAL state, an assistant/automation actor MUST land `/proposed`; a
        maintainer/importer MUST land unqualified.
      - From a `state/proposed`, only a maintainer may RATIFY (drop the qualifier, same state) or REJECT.
        A rejection returns the record to its ACTUAL pre-proposal state, so it is legal iff `to_state ==
        pre_proposal_state`, `to_qual is None`, `to_state` is a non-terminal (working/initial) state, the
        actor is a maintainer, AND a recorded rejection `reason` is supplied (spec 8.4: a rejection
        returns the record to a working state "with a recorded reason"). The envelope records no prior
        state (spec 8.3), so the caller supplies `pre_proposal_state` and `reason` from the record's
        history; when the pre-proposal state is not supplied the rejection target cannot be verified and
        the result is CANNOT-EVALUATE, never a permissive VALID (M1); a rejection with no recorded reason
        is INVALID.
      - An unqualified TERMINAL state does not transition at all (no resurrection, spec 8.4); a revived
        concern is a new record, and supersession is a link, not a state edit.
    """
    if specs is None:
        specs = BASELINE_SPECS
    elif not isinstance(specs, dict):
        # `specs` is a control (the type roster); a non-mapping would crash on specs.get() below. Fail
        # closed rather than evaluate a transition against an unreadable roster (guard-input-soundness).
        return TransitionCheck(CANNOT_EVALUATE, ["specs roster is not a mapping (fail-closed)"])
    if not isinstance(type_name, str):
        # A non-string type_name (e.g. a TOML-valid list/dict) is unhashable and would raise on the
        # specs.get() dict lookup below: guard by type first and fail closed with a clean finding, never
        # a TypeError (guard-input-soundness; spec 8.1).
        return TransitionCheck(CANNOT_EVALUATE, ["record type must be a string"])
    spec = specs.get(type_name)
    if spec is None:
        return TransitionCheck(CANNOT_EVALUATE, ["{!r} is not a supported record type".format(type_name)])

    findings = []
    # A non-string actor_kind (e.g. a TOML-valid list/dict) is unhashable and would raise on the
    # PROPOSER_KINDS frozenset membership below (line ~782): guard by type first here so that membership
    # is never reached with a non-str, and fail closed with a clean finding (guard-input-soundness; the
    # ACTOR_KINDS tuple compares element-wise and so does not raise, but PROPOSER_KINDS is a frozenset).
    if not isinstance(actor_kind, str) or actor_kind not in ACTOR_KINDS:
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
        # From a proposed record only a maintainer rejection back to a non-terminal state is legal (spec
        # 8.4). A rejection restores the ACTUAL pre-proposal state, so it must land on exactly that state,
        # supplied by the caller from the record's history (the envelope carries no prior state, spec 8.3).
        # The removed matrix-predecessor heuristic (land on any state whence the proposed state was
        # reachable) let active -> dropped/proposed -> open slip through, an effective active -> open that
        # is absent from the 8.5 matrix; without the pre-proposal state the target cannot be verified, so
        # the result is CANNOT-EVALUATE, never a permissive VALID (guard-input-soundness; M1).
        if to_state in spec.terminal or to_qual is not None:
            findings.append("from a '/proposed' record only a maintainer rejection to a working state "
                            "is legal, not {!r} (spec 8.4)".format(to_status))
        elif actor_kind != "maintainer":
            findings.append("only a maintainer may reject a '/proposed' record to a working state "
                            "(spec 8.4)")
        elif pre_proposal_state is None:
            return TransitionCheck(CANNOT_EVALUATE, findings + [
                "a rejection target cannot be verified without the pre-proposal state; supply it from the "
                "record's history (spec 8.4)"])
        elif to_state != pre_proposal_state:
            findings.append("a rejection must return to the record's actual pre-proposal state {!r}, not "
                            "{!r} (a rejection may not restore a state the record was never in, spec "
                            "8.4/8.5)".format(pre_proposal_state, to_state))
        elif not (isinstance(reason, str) and reason.strip()):
            # Spec 8.4: a rejection returns the record to a working state "with a recorded reason". An
            # otherwise-legal rejection carrying no recorded reason is INVALID (fail-closed).
            findings.append("a rejection transition must carry a recorded reason (spec 8.4)")
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
            if isinstance(actor_kind, str) and actor_kind in PROPOSER_KINDS:
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
    # known_namespaces is a control naming the namespaces that MUST each carry a high-water. None means
    # NO restriction (fail-safe). Any malformed shape (a bare string, whose `ns not in known_namespaces`
    # would SUBSTRING-match and whose sorted() would splat into characters, or a non-string element) is
    # rejected with a finding and treated as an EMPTY known set: every declared namespace is then flagged
    # unknown, strictly MORE findings than the permissive no-restriction default, so a malformed control
    # never reads as more permissive than an omitted one (guard-input-soundness; spec 8.2).
    if known_namespaces is not None and not _is_str_token_control(known_namespaces):
        findings.append("known_namespaces must be a collection of namespace strings (fail-closed)")
        known_namespaces = frozenset()
    extra = set(data) - COUNTERS_TOP_KEYS
    if extra:
        findings.append("counters.toml unknown top-level key(s): {}".format(", ".join(sorted(extra))))
    if "schema" in data:
        if type(data.get("schema")) is not int:
            findings.append("counters.toml schema must be an integer")
        elif data.get("schema") != SUPPORTED_SCHEMA:
            findings.append("counters.toml schema {} is not the supported schema version {} (fail-closed; "
                            "do not parse under v{} assumptions)".format(
                                data.get("schema"), SUPPORTED_SCHEMA, SUPPORTED_SCHEMA))
    counters = data.get("counters")
    if counters is None:
        # An absent [counters] table is a finding when namespaces are known to require a high-water each
        # (spec 8.2: one monotonic high-water per namespace); with no known namespaces it is simply empty.
        if known_namespaces:
            findings.append("counters.toml has no [counters] table but known namespace(s) {} each require "
                            "a high-water (spec 8.2)".format(sorted(known_namespaces)))
        return high_water, findings
    if not isinstance(counters, dict):
        findings.append("[counters] is not a table")
        return high_water, findings
    for ns, val in counters.items():
        if not (isinstance(ns, str) and len(ns) == 2 and ns.isupper() and ns.isalpha()):
            findings.append("[counters] key {!r} is not a two-letter uppercase namespace".format(ns))
            continue
        # A namespace must be bound to a real record type in the section 8.1 taxonomy, checked against that
        # authoritative set EVEN WHEN known_namespaces is omitted, so an unknown 2-letter namespace like ZZ
        # is never silently clean (spec 8.1/8.2, guard-input-soundness; M5).
        if ns not in RECORD_NAMESPACES:
            findings.append("[counters] namespace {!r} is bound to no record type in the section 8.1 "
                            "taxonomy (spec 8.1/8.2)".format(ns))
            continue
        if known_namespaces is not None and ns not in known_namespaces:
            findings.append("[counters] namespace {!r} is not a known namespace".format(ns))
        # A bool is an int subclass; a high-water is a genuine non-negative int, never True/False.
        if type(val) is not int or val < 0:
            findings.append("[counters].{} must be a non-negative integer high-water mark".format(ns))
            continue
        high_water[ns] = val
    # Completeness: every known namespace must carry a high-water (spec 8.2, one per namespace); a known
    # namespace absent from [counters] is a MISSING finding, not only an unexpected one reported above.
    if known_namespaces is not None:
        for ns in sorted(known_namespaces):
            if ns not in high_water:
                findings.append("[counters] is missing a high-water for known namespace {!r} "
                                "(spec 8.2)".format(ns))
    return high_water, findings


def high_water(high, ns):
    """The current high-water for a namespace, 0 when it has allocated nothing yet. Fails closed
    (ValueError) on a counters map that is not a table, rather than reading a malformed map as
    high-water 0 (which could let an existing id be reused; guard-input-soundness, spec 8.2)."""
    if not isinstance(high, dict):
        raise ValueError("counters map is not a table (spec 8.2)")
    return high.get(ns, 0)


def _validated_counter_map(high, where):
    """Fail closed (ValueError) on a counters map that is not a mapping of section-8.1 taxonomy namespaces
    to genuine non-negative ints. next_id and check_monotonic both TRUST the map they are handed, so a
    corrupt map (a non-taxonomy namespace, or a value that is not a genuine int such as a bool, which is
    an int subclass with True == 1) is a fail-closed error rather than a silent zero or a bypassed guard
    (spec 8.2, guard-input-soundness; M7)."""
    if not isinstance(high, dict):
        raise ValueError("{}: counters map is not a table (spec 8.2)".format(where))
    for ns, val in high.items():
        if ns not in RECORD_NAMESPACES:
            raise ValueError("{}: namespace {!r} is bound to no record type in the section 8.1 taxonomy "
                             "(spec 8.1/8.2)".format(where, ns))
        if type(val) is not int or val < 0:
            raise ValueError("{}: high-water for {!r} must be a genuine non-negative int, got {!r} "
                             "(a bool is not a high-water; spec 8.2)".format(where, ns, val))


def next_id(high, ns, known_complete=False):
    """Allocate the next id for a namespace: returns (id_string, new_high_water). The high-water only
    ever increases by one, so an id is never reused (spec 8.2). The caller records new_high_water back to
    counters.toml under the store lock as one atomic claim (spec 8.2, the atomic-claim-from-pool rule).

    Fails closed (ValueError) rather than allocating an unsound id (guard-input-soundness; M7):
      - `ns` is bound to no record type in the section 8.1 taxonomy (an out-of-taxonomy namespace like ZZ);
      - the counters map is corrupt (not a table of taxonomy namespaces to genuine non-negative ints);
      - `ns` has NO recorded high-water AND the map is not known to be complete. Allocating from a missing
        counter would read its high-water as 0 and could reuse an id that already exists; that is sound
        only when the map has been proved complete (validate_counters with known_namespaces). The caller
        asserts that proof with `known_complete=True`; without it, an absent counter is refused."""
    if not isinstance(ns, str) or ns not in RECORD_NAMESPACES:
        # Guard by type first: a non-string ns (e.g. a list) is unhashable and would crash on the
        # RECORD_NAMESPACES frozenset membership; it is also bound to no record type. Fail closed either
        # way, never a TypeError (guard-input-soundness; spec 8.1/8.2).
        raise ValueError("cannot allocate an id for namespace {!r}: it is bound to no record type in the "
                         "section 8.1 taxonomy (spec 8.1/8.2)".format(ns))
    _validated_counter_map(high, "next_id")
    if ns not in high and not known_complete:
        raise ValueError("cannot allocate an id for namespace {!r}: it has no recorded high-water and the "
                         "counters map is not known complete; validate it with validate_counters(..., "
                         "known_namespaces=...) and pass known_complete=True, so an absent counter cannot "
                         "read as high-water 0 and reuse an existing id (spec 8.2)".format(ns))
    n = high_water(high, ns) + 1
    return "{}-{}".format(ns, n), n


def check_monotonic(old_high, new_high):
    """Confirm counters only ever advance (spec 8.2: never reset, IDs never reused). Every namespace in
    `old_high` must be present in `new_high` with a value greater than or equal to its old high-water; a
    regression or a dropped namespace (which would let a later allocation reuse an id) is a finding. Both
    maps are validated first: a non-taxonomy namespace or a value that is not a genuine non-negative int
    (a bool is not, though True == 1) is a fail-closed finding, never a silent pass that lets a corrupt
    counter read as a valid high-water (guard-input-soundness; M7)."""
    findings = []
    for label, m in (("old", old_high), ("new", new_high)):
        if not isinstance(m, dict):
            findings.append("the {} counters map is not a table (spec 8.2)".format(label))
            continue
        for ns, val in m.items():
            if ns not in RECORD_NAMESPACES:
                findings.append("{} counters namespace {!r} is bound to no record type in the section 8.1 "
                                "taxonomy (spec 8.1/8.2)".format(label, ns))
            if type(val) is not int or val < 0:
                findings.append("{} counters high-water for {!r} must be a genuine non-negative int, got "
                                "{!r} (a bool is not a high-water; spec 8.2)".format(label, ns, val))
    if findings:
        return findings                  # a corrupt map is not compared for monotonicity (fail-closed)
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
    if not isinstance(high, dict):
        # `high` is a control (the counters map). A non-table (e.g. a bare string) would SUBSTRING-match
        # `ns not in high` and then crash on high[ns]: fail closed, certifying no id against an unreadable
        # map, rather than a TypeError or a silent pass (guard-input-soundness; spec 8.2).
        return ["counters map is not a table, so no id can be certified within it (spec 8.2)"]
    if not _is_item_collection(ids):
        # A non-iterable would crash the `for`; a bare string would splat into characters. Fail closed.
        return ["id-collection must be an iterable of id strings, not {} (spec 8.2)".format(
            type(ids).__name__)]
    for rid in ids:
        shape = _valid_id_shape(rid)
        if shape is None:
            findings.append("id {!r} is not a well-formed <NS>-<n> id".format(rid))
            continue
        ns, n = shape
        if ns not in high:
            findings.append("id {!r} uses namespace {!r} that counters.toml does not track".format(rid, ns))
            continue
        hv = high[ns]
        # A malformed high-water fails CLOSED: a bool (True == 1), a float, or a negative int is not a
        # genuine non-negative high-water, so do NOT certify the id against it (the comparison would
        # silently accept True/1.5, or reject against a negative). type(hv) is not int rejects bool, whose
        # type is bool not int (spec 8.2).
        if type(hv) is not int or hv < 0:
            findings.append("namespace {!r} high-water {!r} is not a non-negative integer (spec 8.2)".format(
                ns, hv))
            continue
        if n > hv:
            findings.append("id {!r} exceeds the {} high-water {} (unreserved id; spec 8.2)".format(
                rid, ns, hv))
    return findings


def check_unique_ids(ids):
    """Confirm no id is reused across a set of records (spec 8.2: IDs are never reused). Returns a finding
    per duplicated id."""
    findings = []
    if not _is_item_collection(ids):
        # A non-iterable would crash the `for`; a bare string would splat into characters. Fail closed
        # with a finding rather than a TypeError or a splat (guard-input-soundness; spec 8.2).
        return ["id-collection must be an iterable of id strings, not {} (spec 8.2)".format(
            type(ids).__name__)]
    seen = set()
    for rid in ids:
        # A non-string / malformed id (e.g. a TOML-valid list) is unhashable and would raise on the set
        # membership below: guard by shape first and fail closed with a clean finding, never a TypeError
        # (guard-input-soundness; spec 8.2). Uniqueness cannot be judged for a malformed id, so it is
        # surfaced and not added to `seen`.
        if _valid_id_shape(rid) is None:
            findings.append("malformed id {!r}: an id must be a well-formed '<NS>-<n>' string (spec 8.2)".format(rid))
            continue
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
    # FIX 3 / R3: a TOML-valid but non-string (unhashable list/dict), or a string-that-should-be-a-list, at
    # a set/frozenset/dict-membership or set()-construction site yields a clean INVALID / CANNOT-EVALUATE
    # finding, never a TypeError crash and never a fail-open. Every record-field or manifest-control site
    # in this module that feeds such a test is guarded and exercised: the validate_record-reachable sites
    # covered by FIX 3 here (worklog kind, actor.kind, link.rel, ref.kind, and the block actor-kind (akind)
    # proposal-qualifier check), and the R3 additions covered by their own vectors below (validate_transition
    # type_name and actor_kind; check_unique_ids rid; and the _validate_worklog registered_kinds control).
    # If the guard were absent any of these would raise, aborting self_test with a fail-closed exit rather
    # than returning a structured finding.
    check("fix3-worklog-kind-list-clean-invalid",
          validate_record(dict(wl, kind=["fixed"]), expected_type="worklog").status == INVALID)
    check("fix3-actor-kind-list-clean-invalid",
          validate_record(dict(wl, actor={"kind": ["maintainer"]}), expected_type="worklog").status == INVALID)
    check("fix3-link-rel-list-clean-invalid",
          validate_record(dict(wl, links=[{"rel": ["resolves"], "id": "BI-42"}]),
                          expected_type="worklog").status == INVALID)
    check("fix3-ref-kind-list-clean-invalid",
          validate_record(dict(wl, refs=[{"kind": ["doc"], "locator": "x", "note": "n"}]),
                          expected_type="worklog").status == INVALID)
    check("fix3-block-akind-list-clean-invalid",
          validate_record(envelope("block", 77, "active", actor={"kind": ["assistant"]},
                                    scopes=["BI-1"])).status == INVALID)

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
    imp["refs"] = [{"kind": "doc", "locator": "legacy/DONE.md", "note": "imported"}]  # provenance (M4)
    imp["links"] = [{"rel": "receipt_of", "id": "BI-1"}]
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
    check("block-active-proposed-ok",   # the one non-terminal proposable state (assistant proposer, M2)
          validate_record(envelope("block", 4, "active/proposed", actor={"kind": "assistant"},
                                   scopes=["BI-1"])).status == VALID)
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
          validate_transition("finding", "fixed/proposed", "open", "maintainer",
                              pre_proposal_state="open", reason="fix did not hold").status == VALID)
    # FIX 5 (spec 8.4): a rejection returns the record to a working state WITH A RECORDED REASON. An
    # otherwise-legal maintainer rejection carrying no reason is INVALID; the same rejection with a
    # recorded reason is VALID.
    txn_reject_noreason = validate_transition("finding", "fixed/proposed", "open", "maintainer",
                                              pre_proposal_state="open")
    check("fix5-reject-no-reason-invalid", txn_reject_noreason.status == INVALID)
    check("fix5-reject-no-reason-named",
          any("recorded reason" in f for f in txn_reject_noreason.findings))
    check("fix5-reject-with-reason-valid",
          validate_transition("finding", "fixed/proposed", "open", "maintainer",
                              pre_proposal_state="open", reason="fix did not hold").status == VALID)
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
    # R3 FIX A (guard-input-soundness): a non-string type_name (a TOML-valid list/dict) is unhashable and
    # would raise on the specs.get() dict lookup; it now fails closed to CANNOT-EVALUATE with no exception.
    # A non-string actor_kind (a list) is unhashable and would raise on the PROPOSER_KINDS frozenset
    # membership at a terminal transition; it now records the invalid-actor finding and cuts INVALID with
    # no exception. Both repros must complete cleanly (never a TypeError).
    txn_a_listtype = validate_transition([], "a", "b", "maintainer")
    check("r3a-list-type-name-cannot-eval", txn_a_listtype.status == CANNOT_EVALUATE)
    check("r3a-list-type-name-named",
          any("record type must be a string" in f for f in txn_a_listtype.findings))
    txn_a_listactor = validate_transition("backlog_item", "active", "done/proposed", ["assistant"])
    check("r3a-list-actor-kind-invalid", txn_a_listactor.status == INVALID)
    check("r3a-list-actor-kind-named",
          any("actor kind" in f for f in txn_a_listactor.findings))

    # 10: counters. Schema, monotonic allocation, no reset, ids-within-high-water, uniqueness.
    hw, cfindings = validate_counters({"schema": 1, "counters": {"BI": 42, "FN": 7, "WL": 131}})
    check("counters-schema-ok", not cfindings and hw == {"BI": 42, "FN": 7, "WL": 131})
    _, bad_cf = validate_counters({"counters": {"BI": -1}})
    check("counters-negative-invalid", bad_cf)
    _, bool_cf = validate_counters({"counters": {"BI": True}})   # bool is not a high-water int
    check("counters-bool-invalid", bool_cf)
    nid, newhw = next_id({"BI": 42}, "BI")
    check("counters-allocate-increments", nid == "BI-43" and newhw == 43)
    check("counters-allocate-from-empty", next_id({}, "FN", known_complete=True) == ("FN-1", 1))
    check("counters-monotonic-ok", not check_monotonic({"BI": 42}, {"BI": 43}))
    check("counters-regression-invalid", check_monotonic({"BI": 42}, {"BI": 41}))
    check("counters-dropped-ns-invalid", check_monotonic({"BI": 42, "FN": 7}, {"BI": 42}))
    check("counters-id-within-hw-ok", not check_ids_within_counters(["BI-1", "BI-42"], {"BI": 42}))
    check("counters-id-exceeds-hw-invalid", check_ids_within_counters(["BI-43"], {"BI": 42}))
    check("counters-unknown-ns-invalid", check_ids_within_counters(["ZZ-1"], {"BI": 42}))
    # FIX 2: a malformed high-water fails CLOSED (the id is never silently certified). A bool (True == 1),
    # a float, and a negative int are each flagged for BI-1 rather than accepted; a genuine non-negative
    # int still behaves as before.
    check("fix2-high-water-bool-flagged", bool(check_ids_within_counters(["BI-1"], {"BI": True})))
    check("fix2-high-water-float-flagged", bool(check_ids_within_counters(["BI-1"], {"BI": 1.5})))
    check("fix2-high-water-negative-flagged", bool(check_ids_within_counters(["BI-1"], {"BI": -1})))
    check("fix2-high-water-valid-ok", not check_ids_within_counters(["BI-1"], {"BI": 5}))
    check("ids-unique-ok", not check_unique_ids(["BI-1", "BI-2", "FN-1"]))
    check("ids-duplicate-invalid", check_unique_ids(["BI-1", "BI-1"]))
    # R3 FIX C (guard-input-soundness): a non-string / malformed id (a TOML-valid list) is unhashable and
    # would raise on the `rid in seen` set membership; it now surfaces a "malformed id" finding and is not
    # added to `seen`, never a TypeError.
    ids_malformed = check_unique_ids([["BI-1"]])
    check("r3c-malformed-id-flagged", any("malformed id" in f for f in ids_malformed))

    # 11: non-table / unidentifiable inputs are CANNOT-EVALUATE, never a silent pass.
    check("not-a-table-cannot-eval", validate_record([]).status == CANNOT_EVALUATE)
    check("no-type-no-expected-cannot-eval", validate_record({"id": "BI-1"}).status == CANNOT_EVALUATE)
    check("unsupported-type-invalid", validate_record({"type": "artifact", "id": "AR-1"}).status == INVALID)

    # 12 (M1): a created-terminal factual/ACT/reference record is NOT proposable; `recorded/proposed`
    # fails closed (mirrors worklog). Each vector is otherwise valid so the qualifier is the sole fault.
    check("done-proposed-invalid",
          validate_record(envelope("done", 6, "recorded/proposed",
                                   links=[{"rel": "receipt_of", "id": "BI-1"}])).status == INVALID)
    check("ad-proposed-invalid",
          validate_record(envelope("autonomous_decision", 6, "recorded/proposed",
                                   actor={"kind": "assistant"}, classification="ACT",
                                   action="merged")).status == INVALID)
    check("reference-proposed-invalid",
          validate_record(envelope("reference", 6, "recorded/proposed",
                                   refs=[{"kind": "doc", "locator": "x", "note": "y"}])).status == INVALID)

    # 13 (M2): an assistant/automation block must be `active/proposed`, never a bare `active` grant; a
    # maintainer block may be unqualified.
    check("assistant-block-bare-active-invalid",
          validate_record(envelope("block", 5, "active", actor={"kind": "assistant"},
                                   scopes=["BI-1"])).status == INVALID)
    check("assistant-block-active-proposed-ok",
          validate_record(envelope("block", 6, "active/proposed", actor={"kind": "automation"},
                                   scopes=["BI-1"])).status == VALID)
    check("maintainer-block-bare-active-ok",
          validate_record(envelope("block", 7, "active", scopes=["BI-1"])).status == VALID)

    # 14 (M5): a done receipt MUST link receipt_of (import history exempt); an autonomous_decision MUST
    # carry classification AND action.
    check("done-missing-receipt-invalid",
          validate_record(envelope("done", 7, "recorded")).status == INVALID)
    imp_done = {k: v for k, v in envelope("done", 8, "recorded").items()}
    imp_done["actor"] = {"kind": "importer"}
    imp_done["refs"] = [{"kind": "doc", "locator": "legacy/DONE.md", "note": "imported"}]  # provenance (M4)
    check("done-importer-standalone-ok", validate_record(imp_done).status == VALID)
    check("ad-empty-payload-invalid",
          validate_record(envelope("autonomous_decision", 7, "recorded",
                                   actor={"kind": "assistant"})).status == INVALID)

    # 15 (M6): a link id namespace must map to a real record type; ZZ-1 (no type) fails, a module-tier
    # namespace (AR) passes. The generic <NS>-<n> shape check still stands (bad-link-shape above).
    check("link-unknown-namespace-invalid",
          validate_record(envelope("finding", 12, "open",
                                   links=[{"rel": "relates", "id": "ZZ-1"}])).status == INVALID)
    check("link-module-namespace-ok",
          validate_record(envelope("finding", 13, "open",
                                   links=[{"rel": "relates", "id": "AR-1"}])).status == VALID)

    # 16 (m1): a reference record requires each ref to carry a note; a missing note or no refs fails.
    check("reference-missing-note-invalid",
          validate_record(envelope("reference", 7, "recorded",
                                   refs=[{"kind": "doc", "locator": "x"}])).status == INVALID)
    check("reference-no-refs-invalid",
          validate_record(envelope("reference", 8, "recorded")).status == INVALID)

    # 17 (m2): a carriage return in a one-line title fails closed (not only a newline).
    check("title-carriage-return-invalid",
          validate_record(envelope("finding", 14, "open", title="line1\rline2")).status == INVALID)
    check("title-crlf-invalid",
          validate_record(envelope("finding", 15, "open", title="line1\r\nline2")).status == INVALID)

    # 18 (m3): RFC 3339 UTC accepts both `Z` and `+00:00`; any other numeric offset and a naive value
    # are rejected. A record carrying a `+00:00` timestamp validates VALID.
    check("timestamp-z-ok", _valid_timestamp("2026-08-12T09:14:02Z"))
    check("timestamp-plus-zero-utc-ok", _valid_timestamp("2026-08-12T09:14:02+00:00"))
    check("timestamp-fractional-plus-zero-ok", _valid_timestamp("2026-08-12T09:14:02.5+00:00"))
    check("timestamp-nonzero-offset-rejected", not _valid_timestamp("2026-08-12T09:14:02+05:30"))
    check("timestamp-minus-zero-rejected", not _valid_timestamp("2026-08-12T09:14:02-00:00"))
    check("timestamp-naive-rejected", not _valid_timestamp("2026-08-12T09:14:02"))
    check("record-plus-zero-timestamp-ok",
          validate_record(envelope("finding", 16, "open", created_at="2026-08-12T09:14:02+00:00",
                                   updated_at="2026-08-12T09:14:02+00:00")).status == VALID)

    # 19 (M4): a manifest-registered additional worklog kind validates VALID when registered, INVALID
    # when not; the built-in kinds remain the default.
    check("worklog-manifest-kind-ok",
          validate_record(dict(wl, kind="perf"), expected_type="worklog",
                          registered_kinds=["perf"]).status == VALID)
    check("worklog-manifest-kind-unregistered-invalid",
          validate_record(dict(wl, kind="perf"), expected_type="worklog").status == INVALID)
    check("worklog-builtin-kind-still-ok",
          validate_record(dict(wl, kind="fixed"), expected_type="worklog").status == VALID)
    # R3 FIX B (guard-input-soundness): `registered_kinds` is a manifest control that MUST be a list of
    # strings. A bare STRING would splat into its characters under set() (a fail-open: kind="p" from
    # "perf" would wrongly validate); a list-of-list ([["perf"]]) is unhashable and would raise. Both now
    # fail closed to the built-in WORKLOG_KINDS with a clean finding, never a fail-open and never a crash;
    # a genuine list-of-strings still registers its kinds.
    b_failopen = validate_record(dict(wl, kind="p"), expected_type="worklog", registered_kinds="perf")
    check("r3b-string-kinds-no-failopen", b_failopen.status == INVALID)
    check("r3b-string-kinds-named",
          any("registered worklog kinds must be a list of strings" in f for f in b_failopen.findings))
    b_listoflist = validate_record(dict(wl, kind="fixed"), expected_type="worklog",
                                   registered_kinds=[["perf"]])
    check("r3b-listoflist-kinds-finding-no-crash",
          any("registered worklog kinds must be a list of strings" in f for f in b_listoflist.findings))
    check("r3b-legit-list-kinds-still-ok",
          validate_record(dict(wl, kind="perf"), expected_type="worklog",
                          registered_kinds=["perf"]).status == VALID)

    # 20 (M3): counter completeness. An absent [counters] with known namespaces is a finding; a known
    # namespace with no high-water is a MISSING finding; a complete table is clean.
    _, m3a = validate_counters({"schema": 1}, known_namespaces={"BI"})
    check("counters-absent-table-with-known-ns-invalid", bool(m3a))
    _, m3b = validate_counters({"counters": {"BI": 5}}, known_namespaces={"BI", "FN"})
    check("counters-missing-known-ns-invalid", any("missing a high-water" in f for f in m3b))
    _, m3c = validate_counters({"counters": {"BI": 5, "FN": 2}}, known_namespaces={"BI", "FN"})
    check("counters-complete-known-ns-ok", not m3c)
    _, m3d = validate_counters({}, known_namespaces=None)   # no known ns: an empty file is not a finding
    check("counters-empty-no-known-ns-ok", not m3d)

    # 21 (M8): a schema field other than the supported version fails closed, not parsed under v1.
    _, m8a = validate_counters({"schema": 999, "counters": {}})
    check("counters-bad-schema-invalid", any("supported schema version" in f for f in m8a))
    _, m8b = validate_counters({"schema": 1, "counters": {}})
    check("counters-good-schema-ok", not m8b)

    # ----- round-2 spec-conformance hardening -------------------------------------------------------
    # M1: a full-envelope record MUST carry a string `type`; expected_type is NOT substituted for a
    # missing/non-string type (a finding without type, or type=7, is INVALID). Worklog is the exception.
    fn_no_type = {k: v for k, v in envelope("finding", 20, "open").items() if k != "type"}
    check("m1-nonworklog-missing-type-invalid",
          validate_record(fn_no_type, expected_type="finding").status == INVALID)
    check("m1-nonworklog-nonstring-type-invalid",
          validate_record(dict(envelope("finding", 21, "open"), type=7), expected_type="finding").status == INVALID)
    check("m1-worklog-omits-type-ok", validate_record(wl, expected_type="worklog").status == VALID)
    # a worklog entry carrying a `type` key is FORBIDDEN (reduced envelope omits it; closed schema).
    check("m1-worklog-type-key-forbidden",
          validate_record(dict(wl, type="worklog"), expected_type="worklog").status == INVALID)

    # M2: a maintainer/importer block must be bare 'active', never 'active/proposed'.
    check("m2-maintainer-block-active-proposed-invalid",
          validate_record(envelope("block", 20, "active/proposed", scopes=["BI-1"])).status == INVALID)
    check("m2-importer-block-active-proposed-invalid",
          validate_record(envelope("block", 21, "active/proposed", actor={"kind": "importer"},
                                   scopes=["BI-1"])).status == INVALID)

    # M3: block.scopes ids use a section-8.1 namespace; a done receipt_of points to exactly one BI id.
    check("m3-block-scope-nonrecord-namespace-invalid",
          validate_record(envelope("block", 22, "active", scopes=["ZZ-1"])).status == INVALID)
    check("m3-done-receipt-wrong-namespace-invalid",
          validate_record(envelope("done", 20, "recorded",
                                   links=[{"rel": "receipt_of", "id": "FN-1"}])).status == INVALID)
    check("m3-done-two-receipts-invalid",
          validate_record(envelope("done", 21, "recorded",
                                   links=[{"rel": "receipt_of", "id": "BI-1"},
                                          {"rel": "receipt_of", "id": "BI-2"}])).status == INVALID)
    check("m3-done-single-bi-receipt-ok",
          validate_record(envelope("done", 22, "recorded",
                                   links=[{"rel": "receipt_of", "id": "BI-1"}])).status == VALID)

    # M4: an importer omitting created_at needs a provenance ref; a standalone importer done needs one too;
    # neither created_at, refs, nor receipt_of is INVALID.
    imp_noprov = {k: v for k, v in envelope("done", 23, "recorded").items() if k != "created_at"}
    imp_noprov["actor"] = {"kind": "importer"}
    imp_noprov["links"] = [{"rel": "receipt_of", "id": "BI-1"}]   # isolate the created_at-provenance rule
    check("m4-importer-omit-created-at-no-provenance-invalid", validate_record(imp_noprov).status == INVALID)
    imp_standalone_noprov = {k: v for k, v in envelope("done", 24, "recorded").items()}
    imp_standalone_noprov["actor"] = {"kind": "importer"}         # created_at present, no receipt_of, no refs
    check("m4-importer-standalone-no-provenance-invalid",
          validate_record(imp_standalone_noprov).status == INVALID)
    imp_none = {k: v for k, v in envelope("done", 25, "recorded").items() if k != "created_at"}
    imp_none["actor"] = {"kind": "importer"}                       # neither created_at, refs, nor receipt_of
    check("m4-importer-done-neither-invalid", validate_record(imp_none).status == INVALID)

    # M5: counters validate namespaces against the taxonomy even without known_namespaces; next_id refuses
    # a non-taxonomy namespace.
    _, m5a = validate_counters({"counters": {"ZZ": 1}})
    check("m5-counters-nontaxonomy-ns-invalid", any("section 8.1 taxonomy" in f for f in m5a))
    _, m5b = validate_counters({"counters": {"BI": 1}})
    check("m5-counters-taxonomy-ns-ok", not m5b)
    try:
        next_id({}, "ZZ")
        check("m5-next-id-nontaxonomy-refused", False)
    except ValueError:
        check("m5-next-id-nontaxonomy-refused", True)
    check("m5-next-id-taxonomy-ok", next_id({}, "FN", known_complete=True) == ("FN-1", 1))

    # --- M7: next_id and check_monotonic validate their counters-map input (guard-input-soundness) -----
    # next_id refuses to allocate from a namespace ABSENT from an unvalidated (not-known-complete) map,
    # where an absent counter would read as high-water 0 and could reuse an id that already exists.
    try:
        next_id({"BI": 5}, "FN")
        check("m7-next-id-missing-ns-refused", False)
    except ValueError:
        check("m7-next-id-missing-ns-refused", True)
    # a known-complete map allocates the first id for a namespace with no high-water yet.
    check("m7-next-id-known-complete-ok",
          next_id({"BI": 5}, "FN", known_complete=True) == ("FN-1", 1))
    # a corrupt map (a bool high-water, though True == 1) fails closed rather than allocating BI-2.
    try:
        next_id({"BI": True}, "BI", known_complete=True)
        check("m7-next-id-corrupt-map-refused", False)
    except ValueError:
        check("m7-next-id-corrupt-map-refused", True)
    # check_monotonic reports a non-genuine-int high-water (True must not pass as 1) and a non-taxonomy ns.
    check("m7-check-monotonic-bool-value-finding", bool(check_monotonic({"BI": 1}, {"BI": True})))
    check("m7-check-monotonic-nontaxonomy-finding", bool(check_monotonic({}, {"ZZ": 1})))

    # m1: a worklog summary must be a single line.
    check("m1-worklog-multiline-summary-invalid",
          validate_record(dict(wl, summary="line1\nline2"), expected_type="worklog").status == INVALID)

    # m2: refs[].note is required on every record's refs, not only a reference.
    check("m2-finding-ref-missing-note-invalid",
          validate_record(envelope("finding", 26, "open",
                                   refs=[{"kind": "path", "locator": "x"}])).status == INVALID)
    check("m2-finding-ref-with-note-ok",
          validate_record(envelope("finding", 27, "open",
                                   refs=[{"kind": "path", "locator": "x", "note": "n"}])).status == VALID)

    # M1: a rejection restores the ACTUAL pre-proposal state (spec 8.4), supplied by the caller from the
    # record's history. The matrix-predecessor heuristic is REMOVED: it blessed active -> dropped/proposed
    # -> open (an effective active -> open, absent from the 8.5 matrix). dropped/proposed -> open is VALID
    # only when the record was in `open` before it was proposed, and INVALID when it was in `active` (the
    # exact codex M1 vector).
    check("m1-reject-to-pre-proposal-state-ok",
          validate_transition("backlog_item", "dropped/proposed", "open", "maintainer",
                              pre_proposal_state="open", reason="withdrawn on review").status == VALID)
    check("m1-reject-to-wrong-state-invalid",
          validate_transition("backlog_item", "dropped/proposed", "open", "maintainer",
                              pre_proposal_state="active").status == INVALID)
    # without the pre-proposal state a rejection target cannot be verified: CANNOT-EVALUATE, never VALID.
    m1_noprior = validate_transition("backlog_item", "dropped/proposed", "open", "maintainer")
    check("m1-reject-no-pre-proposal-cannot-eval", m1_noprior.status == CANNOT_EVALUATE)
    check("m1-reject-no-pre-proposal-named",
          any("pre-proposal state" in f for f in m1_noprior.findings))
    # a rejection is still maintainer-only and still cannot land on a terminal or qualified state,
    # independent of the pre-proposal state (structural, so INVALID even without needing it).
    check("m1-reject-nonmaintainer-invalid",
          validate_transition("backlog_item", "dropped/proposed", "open", "assistant",
                              pre_proposal_state="open").status == INVALID)
    check("m1-reject-to-terminal-invalid",
          validate_transition("backlog_item", "done/proposed", "dropped", "maintainer",
                              pre_proposal_state="active").status == INVALID)

    # MINOR 1: updated_at (the time of the last transition) cannot precede created_at (spec 8.3).
    check("minor1-updated-before-created-invalid",
          validate_record(envelope("backlog_item", 40, "open",
                                   created_at="2026-08-12T09:14:02Z",
                                   updated_at="2026-08-11T09:14:02Z")).status == INVALID)
    check("minor1-updated-equals-created-ok",
          validate_record(envelope("backlog_item", 41, "open", created_at=TS, updated_at=TS)).status == VALID)
    check("minor1-updated-after-created-ok",
          validate_record(envelope("backlog_item", 42, "open",
                                   created_at="2026-08-12T09:14:02Z",
                                   updated_at="2026-08-13T09:14:02Z")).status == VALID)

    # G1 (adjudicated NOT a defect): the spec defines no maintainer "reject a proposed block" transition
    # (rejection returns to a working state, spec 8.4; a block has no working state, spec 8.5). A proposed
    # block is disposed of by ratifying then releasing it, or by leaving it inert; a direct
    # active/proposed -> released "rejection" is not a defined transition.
    check("g1-proposed-block-direct-reject-to-terminal-invalid",
          validate_transition("block", "active/proposed", "released", "maintainer").status == INVALID)
    check("g1-proposed-block-ratify-ok",
          validate_transition("block", "active/proposed", "active", "maintainer").status == VALID)
    check("g1-ratified-block-release-ok",
          validate_transition("block", "active", "released", "maintainer").status == VALID)

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
