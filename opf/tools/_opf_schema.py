#!/usr/bin/env python3
"""OPFiles (OPF) record envelope + baseline type schemas + status/transition grammar + counters (U2).

Offline, stdlib only, fail-closed. This is the CORE VALIDATOR the later OPF units (doctor, views,
import) build on: given a parsed record it decides whether the record is a well-formed instance of its
declared baseline type, and given a proposed status change it decides whether that transition is legal.
It extends U1's conventions (the VALID/INVALID/CANNOT-EVALUATE outcome model, the closed-keyset schema
discipline, the section-8.1 type->namespace taxonomy) rather than duplicating them; U1's roster and
namespace helpers are imported, not re-declared.

Five things live here, all from OPF-SPEC.md section 8 (with the release triad's worklog shape from 6.2):

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
     The IMPORTED SERIES (spec 8.2) is opt-in: `imported:<NS>-<n>` ids and their quoted `"imported:<NS>"`
     counters are accepted only when a caller passes `imported_namespaces` (validate_counters) or
     `imported_series=True` (the allocation and checking helpers), each series on its own number line.
     With neither, every helper keeps its clean-only behaviour, so an imported id or counter is a finding.

  5. THE IMPORTED ENVELOPE (spec 8.3). validate_imported_record is a pure validator for one imported
     record: the closed imported envelope (an `imported:<NS>-<n>` id, no `/proposed`, an importer actor
     with an id), the closed `import` provenance table, and the `unrecorded` missingness contract (exactly
     one closed-reason row per omitted non-exempt field). The doctor wires it in as C-IMPORTED-SCHEMA,
     one validation per imported leaf row.

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
  - A single RECORD's status is validated for WELL-FORMEDNESS against its type (a legal state, a
    `/proposed` only on a proposable state) always, and for the actor-vs-`/proposed` snapshot rule only
    while the record is at its CREATION SNAPSHOT (created_at == updated_at as instants). Past that
    snapshot the rule does not fire, because a record's `actor` need not name the actor of its last
    transition (a maintainer ratification leaves the creator's `actor` in place); the write boundary
    (validate_transition) enforces gated entry and maintainer-only ratification there.
  - `counters.toml` shape is DEFINED HERE as an optional top-level `schema` int plus a `[counters]`
    table of `<NS> = <non-negative int>` (mirroring version.toml's `schema = 1` marker); the spec fixes
    the semantics (one monotonic high-water per namespace) but not the file layout.
  - The IMPORTED ENVELOPE (spec 8.3) leaves these gaps, DEFINED HERE: `import.span` is a byte range, so
    its two integers are non-negative and ordered; the contribution `delivery.sent_at`/`receipted_at`
    instants are bound by `imported_at` like the four named historical timestamps (the spec 8.8 refusal
    of a historical timestamp later than the run clock); a standalone imported `done` is legal history,
    so its `links` stay exempt from missingness while a reference's `refs` and a worklog's `summary` do
    not; an imported link must target the imported series (spec 8.6 refuses an imported-to-clean link);
    and supplied fields are held to their declared types and vocabularies only, so the current
    state-conditional placement rules (the pending_decision bundle, finding severity while open, the
    contribution delivery stages) are not applied to history. For the same reason the clean envelope's
    `updated_at`-not-before-`created_at` rule is NOT applied to an imported record: spec 8.3 bounds a
    historical timestamp only by its RFC 3339 UTC form and the import clock, the spec 8.8 import refusal
    list names no ordering, and a conforming imported series must reach doctor VALID, so a source whose
    own two timestamps disagree is preserved as recorded rather than refused. A caller-supplied `specs`
    roster is held to more than the shared name/namespace reconciliation: every member the imported
    validator reads must have its exact built-in shape, judged by its real type and never by an
    advertised `__class__`, a baseline entry must equal BASELINE_SPECS, and a module or importer entry
    may not take the reduced worklog envelope or claim an `import`, `unrecorded` or `x-<vendor>` field;
    the validator then reads only fresh copies of the checked entries, so a supplied spec cannot widen a
    baseline grammar, during or after the check. `import.source` must lie under
    `.working/imported/import/<run-id>/originals/` of the record's own `import.run` (spec 8.3, 8.8),
    matched exactly (anchored, case-sensitive, through the `/` after `originals`) on exact strings. A
    record must be plain throughout before any of it is read: exactly a dict whose every key and nested
    value is exactly a str, int, float, bool, date-time, list or dict, as a TOML parser yields, or None,
    a tuple, a set or a frozenset; a date-time's tzinfo is None or exactly a datetime.timezone whose name
    is exactly a str and whose offset is exactly a datetime.timedelta; and no non-empty container
    appears twice (shared or cyclic). Anything else is CANNOT-EVALUATE, since its own methods could
    answer a lookup, a hash, a comparison, a format or a repr with caller code. Depth alone is never
    refused, since tomllib nests tables to any depth through a dotted key or a table header; instead
    every finding renders a record value, and every name other than a section-8.1 type name that a
    caller's `specs` roster or `registered_kinds` control supplies (a module type's states and fields,
    a roster key, a registered kind), through a renderer that is iterative and stops at a fixed
    character budget, so no rendering overflows the stack or exceeds that budget plus the cut marker. A
    record also holds only TOML value
    types (spec 4.4): None, a tuple, a set, a frozenset or a non-str key anywhere in it, a registered
    `x-<vendor>` table and a module field no value rule reads included, is INVALID, and so is a
    date-time in a form no TOML date-time takes (a time with a tzinfo, a nonzero fold, or an offset that
    is not a whole number of minutes or carries a name other than the one its offset gives).
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: _opf_schema.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import datetime
import re
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
# U1 supplies the outcome model, the section-8.1 type->namespace taxonomy, and the namespace/x-vendor
# shape helpers; reuse them rather than re-declaring (match-surrounding-code, single source of truth).
from _opf_store import (  # noqa: E402
    VALID, INVALID, CANNOT_EVALUATE, BASELINE_TYPES, MODULE_TYPES, IMPORTER_TYPES,
    RESERVED_EXCLUDED_TYPES, _valid_extension_namespace,
    _str_token_set, _is_str_token_control, _is_item_collection, _safe_display,
    WORKING_DIRNAME, _HOME_RUN_PREFIXES, _HOME_RUN_SUFFIX, _home_file, evidence_run,
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

# The imported series (spec 8.2): an imported id is `imported:<NS>-<n>`, and its counter is the quoted TOML
# key `"imported:<NS>"`, a number line independent of the clean `<NS>` one.
IMPORTED_PREFIX = "imported:"
# The namespaces an imported id or counter may carry: imported history uses the same enabled types in a
# separate series (spec 8.1 roster notes), so the baseline and module types. The importer-only
# legacy_fragment (LF) is excluded, because spec 8.1 reserves LF unassigned, used by no record type in
# either series; the reserved TX and the unassigned CL are bound to no type and stay excluded (spec 8.1,
# 8.2).
IMPORTED_NAMESPACES = (
    frozenset(BASELINE_TYPES.values())
    | frozenset(ns for ns, _module in MODULE_TYPES.values())
)
# The namespace the section 8.1 roster reserves UNASSIGNED (bound to no type). U1 records it only in a
# comment, so it is named here for the imported-series refusal message.
_RESERVED_UNASSIGNED_NAMESPACES = frozenset({"CL"})


# The section-8.1 taxonomy as one name -> normative-namespace map (baseline + module-tier + importer). A
# caller-supplied `specs` roster is reconciled against this before either validator trusts it, so a MISBOUND
# roster cannot validate a record or transition under the wrong type's grammar (F-R17-A3).
_TAXONOMY_NAMESPACES = dict(BASELINE_TYPES)
_TAXONOMY_NAMESPACES.update({_name: _ns for _name, (_ns, _module) in MODULE_TYPES.items()})
_TAXONOMY_NAMESPACES.update(IMPORTER_TYPES)


def _specs_roster_error(specs, display=_safe_display):
    """Reconcile a caller-supplied `specs` roster against the section-8.1 taxonomy BEFORE either validator
    uses it. Each mapping KEY must be a string that equals its spec's own declared `name`, that name must be
    a real section-8.1 record type, and the spec's `namespace` must be exactly the one the taxonomy binds to
    it. A roster that misbinds a key to a spec of a different type ({"finding": <backlog_item spec>}) would
    otherwise let a type=finding / BI-1 record and a finding open->active transition validate under
    backlog_item's grammar: the roster is the guard's input, and a control that cannot answer for the type it
    is keyed under is a cannot-evaluate, not a silent pass (guard-input-soundness; F-R17-A3). Returns None
    when every entry reconciles, else a finding message naming the first contradiction. The default
    BASELINE_SPECS and the importer roster (legacy_fragment/LF) reconcile cleanly; only a hand-constructed
    misbound roster is newly rejected. `display` renders a key that names no section-8.1 type
    (_bounded_display for the imported validator, since that key is the caller's own text)."""
    if not isinstance(specs, dict):
        return "the type-spec roster is not a mapping (fail-closed)"
    for key, spec in specs.items():
        if type(key) is not str:
            return "the type-spec roster contains a non-string key (fail-closed)"
        if not isinstance(spec, TypeSpec):
            return "the type-spec roster contains a non-TypeSpec value (fail-closed)"
        expected_namespace = _TAXONOMY_NAMESPACES.get(key)
        if expected_namespace is None:
            return ("the type-spec roster names a type absent from the section-8.1 taxonomy: {} "
                    "(fail-closed)".format(display(key)))
        if getattr(spec, "name", None) != key:
            return "the type-spec roster key {!r} contradicts its TypeSpec name (fail-closed)".format(key)
        if getattr(spec, "namespace", None) != expected_namespace:
            return ("the type-spec roster namespace for {!r} contradicts the section-8.1 taxonomy "
                    "(fail-closed)".format(key))
    return None


# --- envelope vocabularies (spec 8.3, 8.6) -----------------------------------------------------------

ACTOR_KINDS = ("maintainer", "assistant", "automation", "importer")
# Actors whose TERMINAL transition lands with the /proposed qualifier (spec 8.4). A maintainer ratifies;
# an importer records imported history as settled fact, so neither proposes.
PROPOSER_KINDS = frozenset({"assistant", "automation"})

# The closed link-relation vocabulary (spec 8.6). Extending it is a spec version change. `exemplifies`
# (target constrained to a preference_pattern, spec 8.6) and `derives_from` (directional, any-type source)
# ride the 1.1.0 base-schema bump.
LINK_RELS = ("supersedes", "resolves", "remediates", "receipt_of", "corrects", "follows", "relates",
             "exemplifies", "derives_from")
# The relations a clean record MAY carry to an imported-series id (spec 8.6). `supersedes` is limited further
# to a target of the source record's own type, which _validate_links judges against the record's namespace.
CLEAN_TO_IMPORTED_RELS = frozenset({"relates", "derives_from", "follows", "supersedes"})
# The closed reference-capture kinds (spec 8.6).
REF_KINDS = ("path", "url", "doc")

# The worklog change-kind vocabulary (spec 6.2). A manifest MAY register additional kinds; callers pass
# them via the `registered_kinds` argument of validate_record/validate_worklog, and the effective set is
# these built-ins UNION the registered kinds (these remain the default when none are supplied).
WORKLOG_KINDS = ("added", "changed", "fixed", "removed", "security", "docs", "infra")

ACTOR_KEYS = frozenset({"kind", "id"})
LINK_KEYS = frozenset({"rel", "id"})
REF_KEYS = frozenset({"kind", "locator", "note"})
# The contribution delivery bundle (spec 8.5; the OUTWARD counterpart to a reference's receipt): the
# delivery pointer plus (when available) the recipient-side receipt. channel/ref/sent_at are required
# once a contribution has been sent; the receipt fields are legal only at acknowledged (see
# _validate_type_specific).
DELIVERY_KEYS = frozenset({"channel", "ref", "sent_at", "receipt_ref", "receipted_at"})

# The full (non-worklog) envelope keyset; types add their own extra keys on top (EXTRA_KEYS below).
# `proposed_from` is the writer-recorded pre-proposal state (spec 8.3/8.8): the authoring verb writes it
# when a transition lands a `/proposed` status, a maintainer rejection restores exactly it, and leaving
# the `/proposed` status removes it. It is validated by _validate_proposed_from: legal only on a
# `/proposed` status, and only naming a legal predecessor state for the type.
ENVELOPE_KEYS = frozenset({"id", "type", "status", "title", "created_at", "updated_at",
                           "actor", "summary", "links", "refs", "proposed_from"})
# The reduced worklog envelope keyset (spec 8.3, 6.2): no status/title/created_at/updated_at; `date`
# replaces the creation/update timestamps. Per spec 8.3 (the reduced-envelope list) `type` is NOT a
# member of the reduced envelope, so under the closed-schema rule a `type` key on a worklog entry is a
# validation failure (FORBIDDEN, my reading): a worklog entry is identified by the file it lives in, not
# by a `type` field.
WORKLOG_KEYS = frozenset({"id", "date", "actor", "kind", "summary", "detail", "links", "refs"})

# `<NS>-<n>`: two uppercase letters, a hyphen, a positive integer with no leading zero (spec 8.2).
_ID_RE = re.compile(r"^([A-Z]{2})-([1-9][0-9]*)\Z")
# The full id grammar over both series (spec 8.2): `^(?:imported:)?[A-Z]{2}-[1-9][0-9]*$`. _ID_RE above
# stays the clean-only grammar that every existing caller relies on.
_SERIES_ID_RE = re.compile(r"^(imported:)?([A-Z]{2})-([1-9][0-9]*)\Z")
# The id-number ceiling of the SERIES-AWARE path (parse_series_id, and every helper called with
# `imported_series=True` or `imported_namespaces`): the CPython default int-to-str limit (4300 digits), so
# the largest id number is _MAX_ID_NUMBER. An id is judged by its digit COUNT before any conversion, a
# counter by an int comparison with _MAX_ID_NUMBER, and both conversions run in chunks shorter than the
# smallest limit sys.set_int_max_str_digits allows (640), so parsing, counter validation and allocation
# share this one ceiling and never depend on that process-wide setting (test-hermeticity;
# guard-input-soundness). The clean-only default path keeps its original ambient-limit rule
# (_valid_id_shape, _genuine_high_water), so an existing caller's verdicts do not change.
_MAX_ID_DIGITS = 4300
_MAX_ID_NUMBER = 10 ** _MAX_ID_DIGITS - 1
_ID_DIGIT_CHUNK = 600
# RFC 3339 UTC with optional fractional seconds. UTC is expressible as `Z` OR the `+00:00` offset (both
# denote a zero offset); both are accepted, any other numeric offset (e.g. +05:30) is not UTC and is
# rejected (see the timestamp ambiguity note above).
_TS_RE = re.compile(r"^([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2}):([0-9]{2})(\.[0-9]+)?(?:Z|\+00:00)\Z")

# counters.toml layout (defined here; see the ambiguity note).
COUNTERS_TOP_KEYS = frozenset({"schema", "counters"})


# --- per-type state machines (spec 8.5) --------------------------------------------------------------

class TypeSpec:
    """One baseline record type's schema: its namespace (from U1's taxonomy), its closed state set and
    legal transitions, the states that may carry `/proposed`, the subset of NON-TERMINAL states whose
    entry by a proposer actor is gated (the generalized `gated` attribute, spec 8.4), its extra
    (non-envelope) field keys, and whether it uses the reduced worklog envelope. `states` is precomputed
    (initial + working + terminal).

    `gated` generalizes the one-off block `active/proposed` special case into a single mechanism: a
    gated NON-TERMINAL state entered by an assistant/automation actor takes the `/proposed` qualifier (a
    proposal a maintainer ratifies to the unqualified state), while a maintainer/importer lands it
    unqualified. Every gated state is a proposable non-terminal state (checked at construction).
    """
    __slots__ = ("name", "namespace", "initial", "working", "terminal", "transitions",
                 "proposable", "gated", "extra_keys", "reduced", "states")

    def __init__(self, name, initial, working, terminal, transitions, proposable,
                 gated=frozenset(), extra_keys=frozenset(), reduced=False):
        self.name = name
        self.namespace = BASELINE_TYPES[name]     # single source of truth for the type->namespace bind
        self.initial = initial
        self.working = frozenset(working)
        self.terminal = frozenset(terminal)
        self.transitions = {k: frozenset(v) for k, v in transitions.items()}
        self.proposable = frozenset(proposable)
        self.gated = frozenset(gated)
        self.extra_keys = frozenset(extra_keys)
        self.reduced = reduced
        self.states = frozenset({initial}) | self.working | self.terminal
        # A gated state is a NON-TERMINAL proposable state; a construction-time contradiction (a gated
        # terminal, or a gated state absent from `proposable`) is a fail-closed programming error here,
        # never a silently mis-specified grammar (guard-input-soundness; spec 8.4).
        for g in self.gated:
            if g in self.terminal or g not in self.proposable or g not in self.states:
                raise ValueError("TypeSpec {!r}: gated state {!r} must be a proposable non-terminal "
                                 "state (spec 8.4)".format(name, g))


# The twelve baseline types (spec 8.5). Single-state types (done/autonomous_decision/reference/worklog/
# maintainer_decision) have `recorded` as both initial and terminal with no transitions (created
# terminal, immutable).
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
        # A type whose non-terminal initial state is gated: an assistant/automation block is
        # `active/proposed`, a proposal a maintainer ratifies to `active` (spec 8.5). The gating rides the
        # generalized `gated` attribute (spec 8.4), the same mechanism contribution `sent` and
        # preference_pattern `active` use.
        "block", initial="active", working=set(), terminal={"released", "expired"},
        transitions={"active": {"released", "expired"}},
        proposable={"active", "released", "expired"}, gated={"active"}, extra_keys={"scopes"}),
    "handoff": TypeSpec(
        "handoff", initial="current", working=set(), terminal={"superseded"},
        transitions={"current": {"superseded"}}, proposable={"superseded"}),
    "reference": TypeSpec(
        # A created-terminal captured reference that awaits no ratification, so `recorded` is NOT
        # proposable (spec 8.4), mirroring worklog.
        "reference", initial="recorded", working=set(), terminal={"recorded"},
        transitions={}, proposable=set()),
    "contribution": TypeSpec(
        # The OUTWARD counterpart to `reference`: an artifact/fix/proposal this project proposes or
        # sends to a peer (spec 8.5). Proposed/withdrawn records need no delivery bundle; once sent,
        # channel/ref/sent_at are required, with optional receipt fields only at acknowledged.
        # `sent` is a gated non-terminal state: an
        # assistant/automation lands `sent/proposed`, a maintainer ratifies to `sent`; a valid standing
        # authorization for the declared recipient may deactivate that per-send gating (see
        # _validate_gated_snapshot). `acknowledged` is the single positive terminal (responded/adopted/
        # reshaped/declined all live in `summary`/`x-aiqt`, never as a state).
        "contribution", initial="proposed", working={"sent"},
        terminal={"acknowledged", "superseded", "withdrawn"},
        transitions={"proposed": {"sent", "withdrawn"}, "sent": {"acknowledged", "superseded"}},
        proposable={"sent", "acknowledged", "superseded", "withdrawn"}, gated={"sent"},
        extra_keys={"recipient", "dedup_class", "content_digest", "delivery"}),
    "maintainer_decision": TypeSpec(
        # A maintainer ruling: created-terminal `recorded` (immutable, the AD shape), never proposable (a
        # maintainer-authored recorded ruling awaits no ratification, spec 8.4). actor.kind is restricted
        # to {maintainer, importer} (D-7). It carries its decision text and MAY `exemplifies` the
        # preference_pattern it instantiates.
        "maintainer_decision", initial="recorded", working=set(), terminal={"recorded"},
        transitions={}, proposable=set(), extra_keys={"decision"}),
    "preference_pattern": TypeSpec(
        # A distilled preference pattern (active -> retired). `active` is a gated non-terminal state
        # (D-8): an assistant-distilled pattern lands `active/proposed` awaiting maintainer ratification;
        # a maintainer ratifies to unqualified `active`. `title` rides the envelope; `context` and
        # `rationale` are the type-specific fields.
        "preference_pattern", initial="active", working=set(), terminal={"retired"},
        transitions={"active": {"retired"}},
        proposable={"active", "retired"}, gated={"active"}, extra_keys={"context", "rationale"}),
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
    frac = m.group(7)[1:].rstrip("0") if m.group(7) else ""
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)),
            int(m.group(4)), int(m.group(5)), int(m.group(6)), frac)


def _is_single_line(value):
    """Non-blank single-line string under EVERY Unicode line boundary (splitlines catches \u2028/\u2029
    and \x85 a plain \n/\r scan misses; Fable-10). Non-blank, not merely non-empty: a whitespace-only
    title or worklog summary is rejected, harmonizing with the fix5b rejection-reason `.strip()` stance
    (Fable-M4)."""
    return isinstance(value, str) and bool(value.strip()) and value.splitlines() == [value]


def _genuine_high_water(value):
    """A non-negative int usable as a high-water: not a bool, and expressible as an id number under the
    runtime int-to-str limit, so an oversized counter is rejected not certified clean (Fable-8; spec 8.2)."""
    if type(value) is not int or value < 0:
        return False
    try:
        str(value)
    except ValueError:
        return False
    return True


def _series_high_water(value):
    """A non-negative int usable as a high-water on the series-aware path: not a bool, and at most
    _MAX_ID_NUMBER, the largest number parse_series_id accepts. Judged by an int comparison, never a
    str() conversion, so the verdict is the same under every sys.set_int_max_str_digits (spec 8.2)."""
    return type(value) is int and 0 <= value <= _MAX_ID_NUMBER


def _high_water_check(series_aware):
    """The high-water predicate a helper applies: the fixed-ceiling _series_high_water on the
    series-aware path, and the original ambient-limit _genuine_high_water on the clean-only default path
    (so an existing caller's verdicts are unchanged)."""
    return _series_high_water if series_aware else _genuine_high_water


def _id_digits(n):
    """The decimal digits of a positive id number of at most _MAX_ID_NUMBER, built in chunks shorter than
    the smallest int-to-str limit (640 digits), so rendering an allocated id never depends on
    sys.set_int_max_str_digits (test-hermeticity)."""
    base = 10 ** _ID_DIGIT_CHUNK
    chunks = []
    while n >= base:
        n, rest = divmod(n, base)
        chunks.append(str(rest).zfill(_ID_DIGIT_CHUNK))
    chunks.append(str(n))
    return "".join(reversed(chunks))


def parse_status(status, spec, display=_safe_display):
    """Parse a status string against a type's grammar (spec 8.4). Returns ((state, qualifier), None) on
    success, or (None, message) on a malformed or type-illegal status. `qualifier` is None or "proposed",
    and "proposed" is legal only on a state the type marks proposable. `display` renders the status, its
    parts and the type's states in a message (_bounded_display for an imported record, whose type may be
    a module type with caller-defined states)."""
    if not isinstance(status, str) or not status:
        return None, "status must be a non-empty string"
    if not isinstance(spec, TypeSpec):
        # `spec` is the type's grammar object; a missing or non-TypeSpec spec (e.g. None) would crash on
        # `spec.states` below. Fail closed with a clean message rather than an AttributeError
        # (guard-input-soundness; spec 8.4).
        return None, "status cannot be parsed: no valid type spec supplied (fail-closed)"
    parts = status.split("/")
    if len(parts) == 1:
        state, qual = parts[0], None
    elif len(parts) == 2:
        state, qual = parts[0], parts[1]
    else:
        return None, "status {} has more than one '/' qualifier".format(display(status))
    if state not in spec.states:
        return None, "status state {} is not a {} state {}".format(
            display(state), spec.name, display(sorted(spec.states)))
    if qual is not None:
        if qual != "proposed":
            return None, "status qualifier {} is not 'proposed' (the only qualifier, spec 8.4)".format(
                display(qual))
        if state not in spec.proposable:
            return None, "state {} may not carry '/proposed' for a {} (spec 8.4/8.5)".format(
                display(state), spec.name)
    return (state, qual), None


def _id_number(digits):
    """The int of an id's decimal suffix, or None when it has more than _MAX_ID_DIGITS digits. The length
    is checked BEFORE any int() call, and each int() call sees fewer than 640 digits, so neither the
    verdict nor the value depends on sys.set_int_max_str_digits (test-hermeticity)."""
    if len(digits) > _MAX_ID_DIGITS:
        return None
    n = 0
    for i in range(0, len(digits), _ID_DIGIT_CHUNK):
        chunk = digits[i:i + _ID_DIGIT_CHUNK]
        n = n * 10 ** len(chunk) + int(chunk)
    return n


def _valid_id_shape(value):
    """The (namespace, number) of a well-formed `<NS>-<n>` id, or None. An oversized numeric suffix
    (CPython refuses int() on a string of more than 4300 digits, a ValueError) is treated as MALFORMED
    input (None), never an uncontrolled crash: mirrors check_clauses.split_clause_id's >4300-digit
    ordinal guard (guard-input-soundness; the caller then reports the id as not well-formed)."""
    if not isinstance(value, str):
        return None
    m = _ID_RE.match(value)
    if not m:
        return None
    try:
        return (m.group(1), int(m.group(2)))  # an oversized numeric suffix raises ValueError -> None below
    except ValueError:
        return None


def parse_series_id(value):
    """The (counter_key, namespace, number) of an id in either series (spec 8.2), or None. A clean id
    `BI-7` yields ("BI", "BI", 7); an imported id `imported:BI-7` yields ("imported:BI", "BI", 7), so the
    counter key names the independent number line the id belongs to. The grammar is lexical: binding the
    namespace to the record's enabled type stays the caller's check, as for _valid_id_shape. An oversized
    numeric suffix (more than _MAX_ID_DIGITS digits, judged by its length before any int() call) is
    malformed input (None), never an uncontrolled crash, whatever sys.set_int_max_str_digits is."""
    if not isinstance(value, str):
        return None
    m = _SERIES_ID_RE.match(value)
    if not m:
        return None
    n = _id_number(m.group(3))
    if n is None:
        return None
    return ((m.group(1) or "") + m.group(2), m.group(2), n)


def _imported_series_error(flag):
    """None when `imported_series` is a genuine bool, else a fail-closed message. The flag widens what a
    counters helper accepts, so a truthy non-bool (the string "false", or 1) must never read as True
    (guard-input-soundness; spec 8.2)."""
    if isinstance(flag, bool):
        return None
    return ("imported_series must be a genuine bool, got {}; a non-bool must not widen the accepted "
            "series (spec 8.2)".format(_safe_display(flag)))


def _is_counter_key(key, imported_series):
    """True when `key` names a counters number line: a section-8.1 record namespace, or, only when
    `imported_series` is True, the quoted imported key `imported:<NS>` over IMPORTED_NAMESPACES (spec
    8.2). `key` is a mapping key, so it is hashable."""
    if key in RECORD_NAMESPACES:
        return True
    return (imported_series is True and isinstance(key, str) and key.startswith(IMPORTED_PREFIX)
            and key[len(IMPORTED_PREFIX):] in IMPORTED_NAMESPACES)


def _id_counter_shape(value, imported_series):
    """The (counter_key, number) of an id, or None when it is malformed: the clean grammar alone by
    default, and both series when `imported_series` is True, each id keyed to its own number line (spec
    8.2). An imported id is accepted only over IMPORTED_NAMESPACES, the same set every other imported
    helper enforces (_is_counter_key), so a reserved or non-imported namespace is never certified."""
    if imported_series is True:
        shape = parse_series_id(value)
        if shape is None or (shape[0] != shape[1] and shape[1] not in IMPORTED_NAMESPACES):
            return None
        return (shape[0], shape[2])
    return _valid_id_shape(value)


def _safe_key_names(keys, display=_safe_display):
    """Order surplus keys for a finding message, each rendered through _safe_display, so a control
    character embedded in an (untrusted) key name cannot forge an extra finding line and an oversized
    non-decimal int key cannot crash the render. It is the newline-safe, oversized-safe render for this
    validator's unknown-key joins (the module's own _safe_display convention); _safe_display yields a
    total-orderable, byte-stable string for every key so the sort itself cannot raise
    (guard-input-soundness; Fable-M2, gemini finding 3). The imported validator passes _bounded_display
    as `display`, so each of its keys renders within that renderer's budget."""
    return sorted(display(k) for k in keys)


def _validate_actor(record, findings, display=_safe_display):
    """Validate the `actor` table (spec 8.3). Returns the actor kind string, or None when it is absent or
    malformed (so the caller can apply the importer created_at exception on a KNOWN kind only). `display`
    renders each actor value in a finding (_bounded_display for an imported record)."""
    actor = record.get("actor")
    if "actor" not in record:
        findings.append("missing required field: actor")
        return None
    if not isinstance(actor, dict):
        findings.append("actor must be a table with a kind")
        return None
    extra = set(actor) - ACTOR_KEYS
    if extra:
        findings.append("actor unknown key(s): {}".format(", ".join(_safe_key_names(extra, display))))
    kind = actor.get("kind")
    if not isinstance(kind, str):
        # A non-string kind (e.g. a TOML-valid list/dict) is unhashable and would raise on the tuple
        # membership below: guard by type first and fail closed with a clean finding (spec 8.3).
        findings.append("actor.kind must be a string")
        kind = None
    elif kind not in ACTOR_KINDS:
        findings.append("actor.kind {} is not one of {}".format(display(kind), list(ACTOR_KINDS)))
        kind = None
    if "id" in actor and (not isinstance(actor.get("id"), str) or not actor.get("id").strip()):
        # A whitespace-only id is semantically blank, rejected like a blank title/summary/reason
        # (the Fable-M4 blank-string stance, harmonized across required payloads; Fable-F4).
        findings.append("actor.id must be a non-empty string when present")
    return kind


def _validate_links(record, findings, namespace):
    """Validate the optional `links` array of {rel, id} (spec 8.6): rel from the closed vocabulary, id a
    well-formed `<NS>-<n>` (any namespace, since a link may point at any type). A clean record MAY also
    link an imported-series id `imported:<NS>-<n>` over IMPORTED_NAMESPACES, but only by `relates`,
    `derives_from`, `follows`, or a `supersedes` whose target namespace is `namespace`, the record's own
    type's (spec 8.6); every other clean-to-imported relation is refused."""
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
            findings.append("{} unknown key(s): {}".format(where, ", ".join(_safe_key_names(extra))))
        if not isinstance(link.get("rel"), str):
            # A non-string rel (e.g. a TOML-valid list/dict) is unhashable and would raise on the set
            # membership below: guard by type first and fail closed with a clean finding (spec 8.6).
            findings.append("{}.rel must be a string".format(where))
        elif link.get("rel") not in LINK_RELS:
            findings.append("{}.rel {!r} is not one of {} (spec 8.6)".format(
                where, link.get("rel"), list(LINK_RELS)))
        series = parse_series_id(link.get("id"))
        if series is not None and series[0] != series[1]:
            # A clean-to-imported link: its namespace must be one the imported series carries, and a
            # vocabulary relation one of those spec 8.6 permits across series (a rel outside the vocabulary
            # is already a finding above). C-LINKS resolves the target over the union of both series.
            rel = link.get("rel")
            if series[1] not in IMPORTED_NAMESPACES:
                findings.append("{}.id {!r} uses namespace {!r}, which the imported series refuses: {}".format(
                    where, link.get("id"), series[1], _imported_namespace_refusal(series[1])))
            if isinstance(rel, str) and rel in LINK_RELS and (
                    rel not in CLEAN_TO_IMPORTED_RELS or (rel == "supersedes" and series[1] != namespace)):
                findings.append("{} {} -> {!r} is refused: a clean-to-imported link is limited to relates, "
                                "derives_from, follows, and same-type supersedes (spec 8.6)".format(
                                    where, rel, link.get("id")))
            continue
        shape = _valid_id_shape(link.get("id"))
        if shape is None:
            findings.append("{}.id {} is not a well-formed <NS>-<n> id".format(
                where, _safe_display(link.get("id"))))
        elif shape[0] not in RECORD_NAMESPACES:
            # A well-formed id whose namespace maps to no record type (e.g. ZZ-1) cannot point at any
            # record: reconcile against the section 8.1 taxonomy, not just the <NS>-<n> shape (spec 8.1/8.2).
            findings.append("{}.id {!r} uses namespace {!r} that is bound to no record type in the "
                            "section 8.1 taxonomy (spec 8.1/8.2)".format(where, link.get("id"), shape[0]))
        # `exemplifies` targets a preference_pattern (PP) specifically (spec 8.6): the link asserts the
        # source record instantiates the linked pattern, so a well-formed exemplifies id whose namespace
        # is not PP is invalid (the source side is unconstrained; modelled on the done receipt_of->BI
        # constraint). The rel is usable by any type; only its TARGET namespace is constrained.
        if link.get("rel") == "exemplifies" and shape is not None \
                and shape[0] != BASELINE_TYPES["preference_pattern"]:
            findings.append("{} exemplifies must target a preference_pattern ({} namespace), not {!r} "
                            "(spec 8.6)".format(where, BASELINE_TYPES["preference_pattern"],
                                                link.get("id")))


def _validate_refs(record, findings, display=_safe_display):
    """Validate the optional `refs` array of {kind, locator, note} (spec 8.6): kind from the closed set,
    locator a non-empty string, note an optional string. `display` renders each ref value in a finding
    (_bounded_display for an imported record)."""
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
            findings.append("{} unknown key(s): {}".format(where, ", ".join(_safe_key_names(extra, display))))
        if not isinstance(ref.get("kind"), str):
            # A non-string kind (e.g. a TOML-valid list/dict) is unhashable and would raise on the set
            # membership below: guard by type first and fail closed with a clean finding (spec 8.6).
            findings.append("{}.kind must be a string".format(where))
        elif ref.get("kind") not in REF_KINDS:
            findings.append("{}.kind {} is not one of {} (spec 8.6)".format(
                where, display(ref.get("kind")), list(REF_KINDS)))
        if not isinstance(ref.get("locator"), str) or not ref.get("locator").strip():
            # A whitespace-only locator is semantically blank (the Fable-M4 stance; Fable-F4).
            findings.append("{}.locator must be a non-empty string".format(where))
        # note is a required member of every ref on every record, not only a reference record (spec 8.6
        # {kind, locator, note}; m2).
        if "note" not in ref:
            findings.append("{}.note is required on every ref (spec 8.6 {{kind, locator, note}})".format(where))
        elif not isinstance(ref.get("note"), str):
            findings.append("{}.note must be a string".format(where))


def _check_keyset(record, allowed, registered_vendors, findings, display=_safe_display):
    """Enforce the closed schema (spec 8.3, 8.7): every top-level key is either in `allowed` or a
    registered `x-<vendor>` extension TABLE. An unregistered or malformed vendor prefix, or a vendor
    entry that is not a table, is a finding; any other unknown key is a finding. `display` renders each
    key in a finding (_bounded_display for an imported record)."""
    for key in record:
        if key in allowed:
            continue
        if isinstance(key, str) and key.startswith("x-"):
            if not _valid_extension_namespace(key):
                findings.append("extension key {} is not a valid x-<vendor> namespace (spec 8.7)".format(
                    display(key)))
            elif key not in registered_vendors:
                findings.append("extension {} is not registered in the manifest [vendors] "
                                "(spec 8.7)".format(display(key)))
            elif not isinstance(record.get(key), dict):
                findings.append("extension {} must be a table (spec 8.7)".format(display(key)))
        else:
            # `key` is an untrusted top-level key: an oversized non-decimal int key would crash a raw
            # {!r} render, so it goes through _safe_display like every other key render (Fable-F2).
            findings.append("unknown key {} (schemas are closed; spec 8.3)".format(display(key)))


def _standing_auth_permits(standing_auth, recipient):
    """True when `standing_auth` is a WELL-FORMED standing-authorization declaration that names
    `recipient` among its authorized recipients, deactivating per-send `sent` gating for that recipient
    (the contribution `sent` standing authorization, spec 8.4). A malformed or absent declaration fails
    CLOSED (returns False, so gating stays active).

    Declaration shape (defined here pending the adopter-config home; see the build note the finalizer
    resolves): a table `{"recipients": [<recipient-string>, ...]}` whose `recipients` is a non-empty list
    of non-empty strings. Any other shape (a non-table, a missing/empty/non-list `recipients`, a
    non-string or blank element) is malformed and denies the relief. The `recipient` matched against it is
    the contribution record's own `recipient` field."""
    if not isinstance(standing_auth, dict):
        return False
    recips = standing_auth.get("recipients")
    if not (isinstance(recips, list) and recips
            and all(isinstance(r, str) and r.strip() for r in recips)):
        return False
    if not (isinstance(recipient, str) and recipient.strip()):
        return False
    return recipient in recips


def _validate_gated_snapshot(record, spec, findings, standing_auth=None):
    """The creation-time snapshot rule for a type with gated non-terminal state(s) (the generalized
    `gated` TypeSpec attribute, spec 8.4). A record whose STATE is a gated state records its creator in
    `actor`, so the `/proposed` qualifier is a creation-time snapshot rule: an assistant/automation
    creator MUST land `state/proposed` (a proposal a maintainer ratifies); a maintainer/importer creator
    MUST land the bare `state` grant (only assistant/automation carry the proposal qualifier). This is
    the ONE mechanism block/contribution/preference_pattern all share, folded out of the former block-only
    special case, so it never weakens block's behaviour or the terminal-`/proposed` rule.

    SCOPED TO THE CREATION SNAPSHOT (spec 8.3/8.4). The rule is a creation-time observation, so it fires
    ONLY while the record is provably still at its creation snapshot: `created_at` and `updated_at` both
    present, both valid, and equal as instants (via `_instant_key`, never string equality). Spec 8.3
    makes `updated_at` the time of the last transition, so `created_at == updated_at` means no transition
    has occurred and the record's `actor` (its creator) IS the actor of its current status. When
    `updated_at` is later, the current status may be the product of a maintainer ratification whose actor
    the envelope does not carry (spec 8.4; the record's `actor` remains its creator): a legitimately
    ratified assistant-created record then rests at the bare gated grant, and the at-rest doctor must not
    flag it. Missing or invalid timestamps do not fire the rule either; those records are already INVALID
    from the required-field / timestamp findings (see validate_record), so no false-clean can result. The
    write/transition boundary (validate_transition) is the guard that catches a fresh proposer entering a
    gated state without `/proposed` and a non-maintainer ratification.

    Contribution `sent` additionally honours a valid STANDING AUTHORIZATION for the record's declared
    recipient: a well-formed declaration deactivates per-send gating for that recipient, letting an
    assistant/automation land the bare `sent` grant; a malformed or absent declaration fails closed to
    gated (spec 8.4).

    RESIDUALS (disclose-guard-residuals). (1) A nonconforming writer can evade the creation-snapshot rule
    by forging `updated_at > created_at`, exactly as it could already forge `actor.kind`; the at-rest
    doctor never claimed to catch a forging writer, and the write/transition boundary plus the prior-
    snapshot history check (C-HISTORY-RESURRECTION) are the guards for conforming toolchains. (2) A
    ratification recorded with `updated_at` equal to `created_at` to the timestamp's precision would
    false-positive; the remedy is a strictly later `updated_at` (fractional seconds are accepted), which
    spec 8.3's updated-at-is-last-transition-time semantics already implies."""
    if not spec.gated:
        return
    parsed, _ = parse_status(record.get("status"), spec)
    if parsed is None:
        return                                 # a malformed status is surfaced by parse_status elsewhere
    state, qual = parsed
    if state not in spec.gated:
        return
    # Creation-snapshot scoping: fire only while the record proves it is still at its creation snapshot
    # (both timestamps present, valid, and one instant). A later updated_at means a transition has
    # occurred and this snapshot rule no longer applies (see docstring; spec 8.3/8.4).
    created, updated = record.get("created_at"), record.get("updated_at")
    if not (_valid_timestamp(created) and _valid_timestamp(updated)
            and _instant_key(created) == _instant_key(updated)):
        return
    actor = record.get("actor")
    akind = actor.get("kind") if isinstance(actor, dict) else None
    # akind may be a TOML-valid non-string (list/dict) which is unhashable and would raise on the
    # membership tests below; guard by type first (a non-string actor kind is flagged by _validate_actor).
    if not isinstance(akind, str):
        akind = None
    # A valid standing authorization for the declared recipient relieves contribution `sent` of per-send
    # gating; every other gated state (and a malformed/absent declaration) stays gated (fail-closed).
    authorized = (spec.name == "contribution" and state == "sent"
                  and _standing_auth_permits(standing_auth, record.get("recipient")))
    if akind in PROPOSER_KINDS and qual is None and not authorized:
        findings.append("a {} created by an {} actor entering the gated {!r} state must be "
                        "'{}/proposed', not a bare '{}' grant (spec 8.4/8.5)".format(
                            spec.name, akind, state, state, state))
    elif akind in ("maintainer", "importer") and qual == "proposed":
        findings.append("a {} created by a {} actor must be a bare '{}' grant, not '{}/proposed' "
                        "(only assistant/automation carry the proposal qualifier, spec 8.4/8.5)".format(
                            spec.name, akind, state, state))


def _validate_type_specific(record, spec, findings, standing_auth=None):
    """Per-type field rules the envelope does not carry (spec 8.5). Reads the record's status STATE (the
    qualifier does not change these rules) to apply conditional bundles. `standing_auth` is the optional
    contribution standing-authorization declaration (spec 8.4), consulted only for the gated snapshot."""
    parsed, _ = parse_status(record.get("status"), spec) if not spec.reduced else (None, None)
    state = parsed[0] if parsed else None

    # The generalized gated-snapshot rule (block/contribution/preference_pattern share it, spec 8.4).
    _validate_gated_snapshot(record, spec, findings, standing_auth)

    if spec.name == "finding":
        # Severity is graded at or after the fix decision, never before: forbidden while open (spec 8.5).
        if "severity" in record:
            if state == "open":
                findings.append("finding.severity is graded at or after the fix decision, never while "
                                "open (spec 8.5)")
            if not isinstance(record.get("severity"), str) or not record.get("severity").strip():
                # A whitespace-only severity is semantically blank (the Fable-M4 stance; Fable-F4).
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
                                         or not record.get("decision").strip()):
                # A whitespace-only decision is semantically blank (the Fable-M4 stance; Fable-F4).
                findings.append("pending_decision.decision must be a non-empty string")
            if "decided_at" in record and not _valid_timestamp(record.get("decided_at")):
                findings.append("pending_decision.decided_at must be an RFC 3339 UTC timestamp")
            if "decided_by" in record and (not isinstance(record.get("decided_by"), str)
                                           or not record.get("decided_by").strip()):
                # A whitespace-only decided_by is semantically blank (the Fable-M4 stance; Fable-F4).
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
                    findings.append("block.scopes entry {} is not a well-formed <NS>-<n> id".format(
                        _safe_display(s)))
                elif sshape[0] not in RECORD_NAMESPACES:
                    # A scoped id must name a real record type in the section 8.1 taxonomy (spec 8.1/8.2);
                    # a namespace bound to no type (e.g. ZZ) scopes nothing (M3).
                    findings.append("block.scopes entry {!r} uses namespace {!r} bound to no record type "
                                    "in the section 8.1 taxonomy (spec 8.1/8.2)".format(s, sshape[0]))
        # The block `active/proposed` creation-time snapshot rule is enforced by the generalized
        # _validate_gated_snapshot (block.gated == {"active"}), the one mechanism this and the other gated
        # types share (spec 8.4/8.5).

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
            elif not isinstance(record.get(k), str) or not record.get(k).strip():
                # A whitespace-only classification/action is semantically blank (Fable-M4 stance; Fable-F4).
                findings.append("autonomous_decision.{} must be a non-empty string".format(k))

    elif spec.name == "reference":
        # A reference record IS a captured reference: it carries at least one ref (spec 8.6 reference
        # shape). Each ref's {kind, locator, note} completeness (note now required on every record's refs,
        # m2) is enforced by _validate_refs, so only the at-least-one requirement is reference-specific.
        refs = record.get("refs")
        if not isinstance(refs, list) or not refs:
            findings.append("a reference must carry at least one {kind, locator, note} ref (spec 8.6)")

    elif spec.name == "contribution":
        # The outward record of what this project proposes or sends to a peer (spec 8.5). Its identity
        # fields are required non-empty strings; proposed/withdrawn records need no delivery bundle and
        # forbid sent_at. Once sent, channel/ref/sent_at are required (modelled on the
        # pending_decision all-or-none idiom). The `sent` creation-gating snapshot is handled generically
        # by _validate_gated_snapshot above.
        for k in ("recipient", "dedup_class", "content_digest"):
            if k not in record:
                findings.append("a contribution must carry a non-empty {} (spec 8.5)".format(k))
            elif not isinstance(record.get(k), str) or not record.get(k).strip():
                findings.append("contribution.{} must be a non-empty string".format(k))
        delivery = record.get("delivery")
        if "delivery" in record and not isinstance(delivery, dict):
            findings.append("contribution.delivery must be a table {channel, ref, sent_at, ...} "
                            "(spec 8.5)")
            delivery = None
        d = delivery if isinstance(delivery, dict) else {}
        extra = set(d) - DELIVERY_KEYS
        if extra:
            findings.append("contribution.delivery unknown key(s): {}".format(
                ", ".join(_safe_key_names(extra))))
        # channel/ref/sent_at are ALL required once the contribution has been sent (sent/acknowledged/
        # superseded); channel/ref MAY be present earlier as a planned delivery; sent_at is FORBIDDEN
        # before the contribution is sent (proposed/withdrawn).
        if state in ("sent", "acknowledged", "superseded"):
            for k in ("channel", "ref", "sent_at"):
                if k not in d:
                    findings.append("a {} contribution must carry delivery.{} (all-or-none delivery "
                                    "bundle, spec 8.5)".format(state, k))
            if "sent_at" in d and not _valid_timestamp(d.get("sent_at")):
                findings.append("contribution.delivery.sent_at must be an RFC 3339 UTC timestamp")
        elif state in ("proposed", "withdrawn"):
            if "sent_at" in d:
                findings.append("contribution.delivery.sent_at is forbidden before the contribution is "
                                "sent (state {}, spec 8.5)".format(state))
        for k in ("channel", "ref"):
            if k in d and (not isinstance(d.get(k), str) or not d.get(k).strip()):
                findings.append("contribution.delivery.{} must be a non-empty string".format(k))
        # A recipient-side receipt (receipt_ref/receipted_at) is legal ONLY at acknowledged and is always
        # optional (a receipt may lag or never come).
        if state == "acknowledged":
            if "receipt_ref" in d and (not isinstance(d.get("receipt_ref"), str)
                                       or not d.get("receipt_ref").strip()):
                findings.append("contribution.delivery.receipt_ref must be a non-empty string when present")
            if "receipted_at" in d and not _valid_timestamp(d.get("receipted_at")):
                findings.append("contribution.delivery.receipted_at must be an RFC 3339 UTC timestamp")
        else:
            for k in ("receipt_ref", "receipted_at"):
                if k in d:
                    findings.append("contribution.delivery.{} is legal only at acknowledged "
                                    "(spec 8.5)".format(k))

    elif spec.name == "maintainer_decision":
        # A maintainer ruling, created-terminal `recorded` (spec 8.5). It carries its decision text (the
        # answer plus rationale), and actor.kind is restricted to {maintainer, importer} (D-7): a
        # maintainer ruling with assistant/automation attribution is a contradiction; importer covers
        # migrated history. An `exemplifies` link (target constrained to a PP) is validated in
        # _validate_links.
        if "decision" not in record:
            findings.append("a maintainer_decision must carry a non-empty decision (spec 8.5)")
        elif not isinstance(record.get("decision"), str) or not record.get("decision").strip():
            findings.append("maintainer_decision.decision must be a non-empty string")
        actor = record.get("actor")
        akind = actor.get("kind") if isinstance(actor, dict) else None
        if isinstance(akind, str) and akind not in ("maintainer", "importer"):
            findings.append("a maintainer_decision must be authored by a maintainer or importer actor, "
                            "not {!r} (a maintainer ruling with assistant attribution is a "
                            "contradiction; spec 8.5, D-7)".format(akind))

    elif spec.name == "preference_pattern":
        # A distilled preference pattern (active -> retired, spec 8.5). Its context and rationale are
        # required non-empty strings (title rides the envelope); the `active/proposed` creation-gating
        # snapshot is handled generically by _validate_gated_snapshot above (D-8).
        for k in ("context", "rationale"):
            if k not in record:
                findings.append("a preference_pattern must carry a non-empty {} (spec 8.5)".format(k))
            elif not isinstance(record.get(k), str) or not record.get(k).strip():
                findings.append("preference_pattern.{} must be a non-empty string".format(k))


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
                isinstance(k, str) and k for k in registered_kinds):
            allowed_kinds |= set(registered_kinds)
        else:
            findings.append("registered worklog kinds must be a list of non-empty strings")
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
    elif not _is_single_line(summ):
        # A worklog summary is one line (spec 6.2), like the envelope title (m1).
        findings.append("a worklog entry summary must be a single line (spec 6.2)")
    if "detail" in record and not isinstance(record.get("detail"), str):
        findings.append("worklog detail must be a string when present")


# --- the record validator (spec 8.3) -----------------------------------------------------------------

def _validate_proposed_from(record, spec, findings):
    """`proposed_from` (spec 8.3/8.8): the pre-proposal state the authoring verb recorded when it landed
    the current `/proposed` status, and the state a maintainer rejection restores. Legal ONLY on a
    `/proposed` status, and its value must be an unqualified state of this type from which the proposed
    state is reachable under the transition table (a legal predecessor); anything else is INVALID,
    fail-closed. An absent field is legal (a record proposed outside the transition subcommand carries
    none: one created at a `/proposed` initial state, or one proposed by a canonical hand edit; the
    absence establishes no provenance, and the verb then refuses its rejection rather than inferring or
    inventing a predecessor)."""
    if "proposed_from" not in record:
        return
    value = record.get("proposed_from")
    parsed, _err = parse_status(record.get("status"), spec)
    if parsed is None or parsed[1] != "proposed":
        findings.append("proposed_from is legal only on a '/proposed' status (spec 8.8)")
        return
    # isinstance first: a TOML-valid non-string (a list/dict) is unhashable and would raise on the
    # transitions dict lookup (guard-input-soundness).
    if not isinstance(value, str) or parsed[0] not in spec.transitions.get(value, frozenset()):
        findings.append("proposed_from {} is not a legal predecessor state of {!r} for a {} "
                        "(spec 8.5/8.8)".format(_safe_display(value), record.get("status"), spec.name))


def validate_record(record, expected_type=None, specs=None, registered_vendors=frozenset(),
                    registered_kinds=None, standing_auth=None):
    """Validate one parsed record against its baseline type schema. Returns a RecordValidation.

    The type is taken from the record's `type` field, or, for a typeless worklog entry (Appendix C omits
    it), from `expected_type`; when `expected_type` is also given for a typed record, a mismatch is a
    finding (spec 8.3 "type must match the file the record lives in"). `specs` defaults to the nine
    baseline types (U2M passes an extended roster). `registered_vendors` is the manifest's registered
    `x-<vendor>` set (U1's [vendors].registered) against which extension tables are checked.
    `registered_kinds` is the manifest's additional worklog change kinds (spec 6.2); the built-in
    WORKLOG_KINDS remain the accepted default when it is None. `standing_auth` is the optional
    contribution standing-authorization declaration (spec 8.4): a valid declaration for a contribution's
    declared recipient deactivates per-send `sent` gating for that recipient; absent or malformed, gating
    stays active (fail-closed). It is consulted only for the contribution gated snapshot.

    Outcome: CANNOT-EVALUATE when the input is not a table or its type cannot be identified; INVALID when
    an identified record violates its schema; VALID otherwise."""
    if specs is None:
        specs = BASELINE_SPECS
    else:
        # `specs` is a control (the type roster). The value-is-a-TypeSpec check alone confirmed each value
        # is a spec but not that it is the spec for the type it is keyed under; F-R17-A3 reconciles the
        # roster key/spec-name/spec-namespace against the section-8.1 taxonomy so a misbound roster
        # ({"finding": <backlog_item spec>}) is a cannot-evaluate, never a silent pass under the wrong type's
        # grammar (guard-input-soundness; spec 8.1/8.3/8.4). A non-mapping, a non-string key, or a
        # non-TypeSpec value is subsumed here and fails closed. BASELINE_SPECS reconciles cleanly.
        roster_error = _specs_roster_error(specs)
        if roster_error is not None:
            return RecordValidation(CANNOT_EVALUATE, [roster_error])
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
    findings = []
    if not _is_str_token_control(registered_vendors):
        # A malformed control (a bare string, an int, a non-string element) is SURFACED with a finding,
        # not silently normalized to the empty set and swallowed: a manifest whose [vendors].registered is
        # malformed must not read as "no vendors registered" without the operator learning the control was
        # unreadable, mirroring the malformed-known_namespaces surfacing in validate_counters (guard-input-
        # soundness; Fable-M5). The normalization below still fails closed and admits no extension.
        findings.append("registered_vendors must be a collection of x-<vendor> strings (fail-closed)")
    registered_vendors = _str_token_set(registered_vendors)
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
        # The roster's keys are rendered through _safe_display (the unknown-key idiom): a hand-constructed
        # specs roster carrying an oversized non-decimal int key would crash a raw repr sort/render, so
        # each key is coerced to a safe string before ordering (Fable-F2; matches _safe_key_names' stance).
        findings.append("{!r} is not a supported record type (baseline: {})".format(
            rtype, ", ".join(sorted(_safe_display(s) for s in specs))))
        return RecordValidation(INVALID, findings, rtype=rtype)

    rid = record.get("id")
    shape = _valid_id_shape(rid)
    if shape is None:
        findings.append("id {} is not a well-formed <NS>-<n> id (spec 8.2)".format(_safe_display(rid)))
    elif shape[0] != spec.namespace:
        findings.append("id namespace {!r} is not the {!r} namespace bound to type {!r} "
                        "(one-to-one binding, spec 8.2)".format(shape[0], spec.namespace, rtype))

    if spec.reduced:
        _validate_worklog(record, spec, registered_vendors, findings, registered_kinds)
        _validate_actor(record, findings)
        _validate_links(record, findings, spec.namespace)
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
    elif not _is_single_line(title):
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

    _validate_links(record, findings, spec.namespace)
    _validate_refs(record, findings)
    _validate_proposed_from(record, spec, findings)
    _validate_type_specific(record, spec, findings, standing_auth)

    return RecordValidation(INVALID if findings else VALID, findings, rtype, rid)


# --- the imported-record validator (spec 8.3, the imported envelope) ----------------------------------

# The closed missingness reasons of an `unrecorded` row (spec 8.3). _opf_adopt's import policy carries the
# same tuple (MISSINGNESS_REASONS); the self-test asserts the two stay equal.
IMPORTED_MISSINGNESS_REASONS = ("not_recorded_in_source", "unparsed", "ambiguous", "conflicting",
                                "not_applicable")
# The members every imported record MUST carry (spec 8.3). None of them may be waived through missingness.
IMPORTED_REQUIRED_KEYS = ("id", "type", "title", "status", "actor", "import")
# The optional envelope fields an imported record may omit without a missingness row (spec 8.3), each only
# where the type's own schema does not require it; registered `x-<vendor>` tables are exempt too.
IMPORTED_EXEMPT_FIELDS = frozenset({"proposed_from", "summary", "links", "refs"})
# The exempt fields a type's own schema requires, so omitting one there owes a row (spec 8.3): the worklog
# requires its summary (spec 6.2) and a reference its captured refs (spec 8.6). A standalone imported
# `done` is legal history (spec 8.3), so its `links` stay exempt.
_IMPORTED_TYPE_REQUIRED = {"worklog": frozenset({"summary"}), "reference": frozenset({"refs"})}
# The historical timestamps (spec 8.3): optional, RFC 3339 UTC when present, and no later than the import
# clock instant. The contribution delivery instants are held to the same clock (the spec 8.8 refusal of a
# historical timestamp later than the run clock).
IMPORTED_HISTORICAL_TIMESTAMPS = ("created_at", "updated_at", "date", "decided_at")
_IMPORTED_DELIVERY_TIMESTAMPS = ("sent_at", "receipted_at")
# The type fields a supplied value must keep as a non-blank string (their clean declared type, spec 8.3).
_IMPORTED_TEXT_FIELDS = ("severity", "decision", "decided_by", "classification", "action", "recipient",
                         "dedup_class", "content_digest", "context", "rationale")
# The closed `import` provenance table (spec 8.3) and its required members.
IMPORT_KEYS = frozenset({"source", "source_sha256", "run", "imported_at", "span", "history", "unparsed"})
IMPORT_REQUIRED_KEYS = ("source", "source_sha256", "run", "imported_at")
UNRECORDED_ROW_KEYS = frozenset({"field", "reason"})
HISTORY_ROW_KEYS = frozenset({"field", "value"})
# `source_sha256` is 64 lowercase hexadecimal digits (spec 8.3); an uppercase digit is refused.
_SOURCE_SHA256_RE = re.compile(r"^[0-9a-f]{64}\Z")
# The import run id `imp-<YYYYMMDD>T<HHMMSS>Z-<hash16>` (spec 8.3), composed from the store's import-home
# grammar so the two cannot drift. Like the store, this checks shape, not calendar validity.
_IMPORT_RUN_PATTERN = _HOME_RUN_PREFIXES["import"] + _HOME_RUN_SUFFIX


def _imported_schema_fields(spec):
    """The fields of a type's schema an imported record of that type may carry or account for (spec 8.3):
    the full envelope plus the type's own fields, or, for the worklog, the imported envelope's id, type,
    title, status and actor plus the worklog fields (spec 6.2)."""
    if spec.reduced:
        return frozenset({"id", "type", "title", "status", "actor"}) | WORKLOG_KEYS
    return ENVELOPE_KEYS | spec.extra_keys


def _check_closed_table(table, allowed, where, findings):
    """Report each key of an imported sub-table outside its closed keyset, in table order, each rendered
    through _bounded_display, so the report is deterministic and an oversized, deep or control-bearing
    key cannot crash it, forge a line or exceed the rendering budget."""
    for key in table:
        if key not in allowed:
            findings.append("{} unknown key {} (schemas are closed; spec 8.3)".format(
                where, _bounded_display(key)))


# The exact built-in types a plain value is made of: what a TOML parser yields (str, int, float, bool, the
# date-time types, list and dict), plus None, a tuple, a set and a frozenset, which no TOML document
# yields: a record holding one is plain, so it reaches its field rules and _is_toml_value and is INVALID,
# not CANNOT-EVALUATE. Each type is compared by identity, so no metaclass comparison runs.
_PLAIN_SCALAR_TYPES = (str, int, float, bool, type(None), datetime.datetime, datetime.date, datetime.time)
_PLAIN_COLLECTION_TYPES = (dict, list, tuple, set, frozenset)


def _plain_tzinfo(tz):
    """True when a date-time's tzinfo is None, or exactly a datetime.timezone whose two retained objects,
    its name and its offset, are exactly a str and exactly a datetime.timedelta. A timezone retains
    nothing else, and each is read through the exact timezone's own built-in accessor, which returns the
    retained object itself. A timezone built from a str or timedelta subclass keeps that subclass, and
    the date-time's repr would run the subclass's own __repr__."""
    if tz is None:
        return True
    return (type(tz) is datetime.timezone and type(datetime.timezone.tzname(tz, None)) is str
            and type(datetime.timezone.utcoffset(tz, None)) is datetime.timedelta)


def _plain_problem(value):
    """None when value is plain, else (reason, where): reason says why it is not, and where is () when
    value itself is the culprit or (key,) for the key of the top-level table entry under which the walk
    found it. Plain: value, and every key and member nested in it, is exactly one of the plain built-in
    types above; every date-time's tzinfo passes _plain_tzinfo; and no non-empty container is reached
    twice, so the value is a tree as a parsed one is. type() reads the real type, never an advertised
    `__class__`, and each container is read through its built-in type's own iterator, so the walk runs no
    caller code. A subclass or any other object is not plain: its own methods (a lookup, a hash, a
    comparison, a format, a repr, a startswith) could answer a later check with caller code. A shared
    container is not plain because a walk over its members (_is_toml_value) could repeat without bound,
    exponentially for a shared one and forever for a cyclic one. Depth is not bounded: tomllib builds
    nested tables from a dotted key or a table header without recursion, so a parsed record can be
    nested to any depth, and a plain value is rendered only by _bounded_display, which is bounded on its
    own. The walk is iterative and reads each non-empty container once, refusing a second visit (a cycle
    included), so it ends after a number of steps linear in the value's references. An empty container
    has no members, so sharing one (the empty tuple is shared by CPython itself) is harmless and
    allowed."""
    stack, seen = [(value, True, ())], set()
    while stack:
        v, top, where = stack.pop()
        t = type(v)
        if any(t is p for p in _PLAIN_SCALAR_TYPES):
            if (t is datetime.datetime or t is datetime.time) and not _plain_tzinfo(v.tzinfo):
                return ("holds a date-time whose tzinfo is neither None nor exactly a datetime.timezone "
                        "retaining an exact str name and an exact timedelta offset, so its own methods "
                        "could answer a check"), where
            continue
        if not any(t is p for p in _PLAIN_COLLECTION_TYPES):
            return ("holds a key or value that is not exactly a plain built-in type, so its own methods "
                    "could answer a check"), where
        if t.__len__(v) and id(v) in seen:
            return ("holds a non-empty container reached twice (shared or cyclic), so a walk over its "
                    "members could repeat without bound"), where
        seen.add(id(v))
        if t is dict:
            for key, member in dict.items(v):
                inner = (key,) if top else where
                stack.append((key, False, inner))
                stack.append((member, False, inner))
        else:
            for member in t.__iter__(v):
                stack.append((member, False, where))
    return None


def _is_plain_value(value):
    """True when value is plain (_plain_problem)."""
    return _plain_problem(value) is None


# The most characters one rendering of a record value in a finding takes (_bounded_display), the cut
# marker aside. It exceeds the rendering of any well-formed id (`imported:`, a two-letter namespace, a
# hyphen and up to _MAX_ID_DIGITS digits, in quotes), so no record's label is cut.
_DISPLAY_BUDGET = 8192
# What ends a rendering cut at _DISPLAY_BUDGET characters.
_DISPLAY_CUT = "...<cut at {} characters>".format(_DISPLAY_BUDGET)
# An int of more bits than this has more than _DISPLAY_BUDGET decimal digits, so it is named by the
# oversized marker without being converted.
_DISPLAY_INT_BITS = 4 * _DISPLAY_BUDGET
# The fixed text that stands for a value that is not plain, which is never rendered.
_NOT_PLAIN_MARK = "<not a plain built-in value>"
# Each container type's repr when empty, and its opening and closing text otherwise; a one-member tuple
# closes with ",)". Each type is compared by identity, never looked up by hash.
_DISPLAY_BRACKETS = ((list, "[]", "[", "]"), (dict, "{}", "{", "}"), (tuple, "()", "(", ")"),
                     (set, "set()", "{", "}"), (frozenset, "frozenset()", "frozenset({", "})"))
# The three kinds of step on _bounded_display's explicit stack, and the end of a container's members.
_DISPLAY_TEXT, _DISPLAY_VALUE, _DISPLAY_MEMBERS, _DISPLAY_END = object(), object(), object(), object()


def _display_int(n):
    """The repr of an exact int, or None when it is oversized: more than _DISPLAY_INT_BITS bits, or over
    the runtime int-to-str limit (the ValueError _safe_display catches)."""
    if int.bit_length(n) > _DISPLAY_INT_BITS:
        return None
    try:
        return int.__repr__(n)
    except ValueError:
        return None


def _display_scalar(v, room):
    """The repr of one scalar for _bounded_display, through its exact built-in type's own __repr__, with
    `room` characters left in the budget; None for an oversized int. A str longer than `room` is sliced to
    it first, so its repr exceeds the room and is cut, and a date-time whose timezone name is longer than
    the budget is named by a fixed text instead, so no single scalar's repr costs more than a constant
    multiple of the budget (an escape takes at most ten characters for one character). A sliced str is
    rendered in the quote the whole str's repr takes, chosen from the whole str, so the cut rendering is
    that repr's prefix: a quote of the other kind is appended to the slice, which makes its repr take
    the same quote, and is dropped with the closing quote, which the cut drops anyway. Anything that is
    not a plain scalar, a date-time whose tzinfo fails _plain_tzinfo included, is named by
    _NOT_PLAIN_MARK and never rendered."""
    t = type(v)
    if t is str:
        if str.__len__(v) <= room:
            return str.__repr__(v)
        double = str.__contains__(v, "'") and not str.__contains__(v, '"')
        return str.__repr__(v[:room] + ("'" if double else '"'))[:-2]
    if t is int:
        return _display_int(v)
    if t is bool or t is float or t is datetime.date or v is None:
        return t.__repr__(v)
    if (t is datetime.datetime or t is datetime.time) and _plain_tzinfo(v.tzinfo):
        if v.tzinfo is not None and str.__len__(datetime.timezone.tzname(v.tzinfo, None)) > _DISPLAY_BUDGET:
            return "<a date-time whose timezone name exceeds {} characters>".format(_DISPLAY_BUDGET)
        return t.__repr__(v)
    return _NOT_PLAIN_MARK


def _bounded_display(value):
    """Render a value for a finding message iteratively and within a budget: a rendering longer than
    _DISPLAY_BUDGET characters is cut there and ends in _DISPLAY_CUT. For a plain value (_is_plain_value)
    whose repr exists and fits the budget it is that repr, so _safe_display, byte for byte; for one whose
    repr exists and does not fit, it is that repr's first _DISPLAY_BUDGET characters and _DISPLAY_CUT.
    The repr does not exist for an oversized int, nor for a value nested deeper than the repr's recursion
    limit, which this walk renders up to the cut all the same. An oversized int alone is
    `<oversized-int: N bits>`, as from _safe_display, and a container holding one is `<oversized-value>`
    when the walk reaches the int before the cut; past the cut the int is never read, so that container
    renders as its cut prefix, where _safe_display gives `<oversized-value>`. Two cases differ from the
    repr: an int of more than _DISPLAY_INT_BITS bits, which the budget could not hold, is oversized even
    when the runtime int-to-str limit is lifted, and a date-time whose timezone name is longer than the
    budget is named by a fixed text (_display_scalar).
    It runs no caller code: each container is read through its exact built-in type's own iterator and
    each scalar rendered through its exact built-in type's own __repr__, and any other object, a subclass
    included, is named by _NOT_PLAIN_MARK in its place and never rendered. The walk is an explicit stack,
    so no depth overflows the stack. Each step emits at least one character, starts a member, or ends a
    container that emitted its opening, so the steps, the stack and the output all grow at most in
    proportion to the budget, whatever the value's depth, width or sharing (a shared or cyclic value
    renders as the tree it unfolds to, up to the cut). Every imported-record finding renders a record
    value, and every name other than a section-8.1 type name that a caller's `specs` roster or
    `registered_kinds` control supplies (a module type's states and fields, a roster key, a registered
    kind), through it, directly or through _plain_display. Only a value already confined to a fixed
    vocabulary is rendered raw: a two-letter namespace, a required or exempt imported envelope field
    name, a section-8.1 type name, an actor kind that ACTOR_KINDS holds, a well-formed series id
    (_SERIES_ID_RE, at most _MAX_ID_DIGITS digits), and a well-formed import run id (_IMPORT_RUN_PATTERN,
    of one fixed length)."""
    if type(value) is int and _display_int(value) is None:
        return "<oversized-int: {} bits>".format(int.bit_length(value))
    out, size, stack = [], 0, [(_DISPLAY_VALUE, value)]
    while stack:
        step = stack.pop()
        if step[0] is _DISPLAY_MEMBERS:
            _, members, pairs, first = step
            member = next(members, _DISPLAY_END)
            if member is _DISPLAY_END:
                continue
            stack.append((_DISPLAY_MEMBERS, members, pairs, False))
            if pairs:
                stack.append((_DISPLAY_VALUE, member[1]))
                stack.append((_DISPLAY_TEXT, ": "))
                stack.append((_DISPLAY_VALUE, member[0]))
            else:
                stack.append((_DISPLAY_VALUE, member))
            if not first:
                stack.append((_DISPLAY_TEXT, ", "))
            continue
        if step[0] is _DISPLAY_TEXT:
            text = step[1]
        else:
            v = step[1]
            t = type(v)
            brackets = next((b for b in _DISPLAY_BRACKETS if t is b[0]), None)
            if brackets is None:
                text = _display_scalar(v, _DISPLAY_BUDGET - size)
                if text is None:
                    return "<oversized-value>"
            elif not t.__len__(v):
                text = brackets[1]
            else:
                stack.append((_DISPLAY_TEXT, ",)" if t is tuple and tuple.__len__(v) == 1 else brackets[3]))
                stack.append((_DISPLAY_MEMBERS, iter(dict.items(v)) if t is dict else t.__iter__(v),
                              t is dict, True))
                text = brackets[2]
        if size + len(text) > _DISPLAY_BUDGET:
            out.append(text[:_DISPLAY_BUDGET - size])
            return "".join(out) + _DISPLAY_CUT
        out.append(text)
        size += len(text)
    return "".join(out)


def _plain_display(value):
    """_bounded_display of a plain value (_is_plain_value), so rendering it runs no caller code, cannot
    overflow the stack, and takes at most _DISPLAY_BUDGET characters and the cut marker; any other value is
    named by _NOT_PLAIN_MARK and never rendered, so its own __repr__ cannot run or forge the message. The
    plainness walk reads the whole value once, in steps linear in its references; the rendering itself
    is bounded by the budget."""
    return _bounded_display(value) if _is_plain_value(value) else _NOT_PLAIN_MARK


# The exact scalar types a TOML value takes (tomllib's output): _PLAIN_SCALAR_TYPES without None.
_TOML_SCALAR_TYPES = (str, int, float, bool, datetime.datetime, datetime.date, datetime.time)


def _is_toml_date_time(v):
    """True when a plain date-time (exactly a datetime or a time whose tzinfo passes _plain_tzinfo) takes a
    form a TOML date-time can, as tomllib builds it: fold 0, which no TOML form sets; a time without a
    tzinfo, since TOML has no offset time; and a datetime whose tzinfo is None (a local date-time) or a
    timezone whose offset is a whole number of minutes (TOML writes `Z` or `+HH:MM`) and whose name is
    the one that offset alone gives it. Fractional seconds are not limited: tomllib keeps them to the
    microsecond, the finest a date-time holds. Each read is a built-in accessor of an exact type."""
    if v.fold:
        return False
    tz = v.tzinfo
    if tz is None:
        return True
    if type(v) is datetime.time:
        return False
    offset = datetime.timezone.utcoffset(tz, None)
    return (offset.seconds % 60 == 0 and offset.microseconds == 0
            and datetime.timezone.tzname(tz, None) == datetime.timezone.tzname(datetime.timezone(offset), None))


def _is_toml_value(value):
    """True when a plain value (_is_plain_value) holds only TOML value types: exactly a TOML scalar, a list
    or a table, nested, every table key exactly a str, so never None, a tuple, a set or a frozenset, and
    every date-time in a form a TOML date-time takes (_is_toml_date_time). It checks types and date-time
    forms, not the content of a str or the size of an int: a str holding a lone surrogate, which no UTF-8
    TOML document carries, and an int outside TOML's 64-bit range both pass, since tomllib.loads yields
    each. A plain value is a tree, so the iterative walk reads each member once and ends."""
    stack = [value]
    while stack:
        v = stack.pop()
        t = type(v)
        if t is datetime.datetime or t is datetime.time:
            if not _is_toml_date_time(v):
                return False
            continue
        if any(t is p for p in _TOML_SCALAR_TYPES):
            continue
        if t is dict:
            for key, member in dict.items(v):
                if type(key) is not str:
                    return False
                stack.append(member)
        elif t is list:
            stack.extend(list.__iter__(v))
        else:
            return False
    return True


def _is_imported_source(value):
    """True when value is exactly a str and the canonical store-relative path of a preserved original,
    spelled from `.working/` (spec 8.3): the store's canonical file-operand grammar (no empty, `.` or `..`
    component, no absolute, drive or backslash form, no control character) beneath `.working/`. An aliased
    spelling is refused, never normalized, and so is a str subclass, whose own methods could answer these
    checks and the source binding."""
    if type(value) is not str or not value.startswith(WORKING_DIRNAME + "/"):
        return False
    try:
        _home_file(value)
    except ValueError:
        return False
    return True


def _validate_import_table(record, schema_fields, findings):
    """Validate the `import` provenance table (spec 8.3). Returns the ordering key of a valid
    `imported_at`, or None when it is absent or invalid (the record is then INVALID already, so the
    historical-timestamp bound is never silently skipped on a VALID record). The table must be exactly a
    dict. Its provenance operands `source` and `run` are read from it once and must each be exactly a
    str; every check below, the source binding included, reads only those captured values, through
    built-in operations, so no method of a caller-supplied value runs (a format that names another run
    or raises, a startswith that forges the match). An operand that is not exactly a str is refused, and
    its finding renders it only when it is plain (_plain_display), such as None or an int; an operand that
    is not plain is never rendered but named by a fixed marker."""
    if "import" not in record:
        findings.append("missing required field: import (the provenance table, spec 8.3)")
        return None
    imp = record.get("import")
    if type(imp) is not dict:
        findings.append("import must be a table {source, source_sha256, run, imported_at, ...} (spec 8.3)")
        return None
    _check_closed_table(imp, IMPORT_KEYS, "import", findings)
    for k in IMPORT_REQUIRED_KEYS:
        if k not in imp:
            findings.append("missing required field: import.{} (spec 8.3)".format(k))
    source, run = imp.get("source"), imp.get("run")
    source_ok = _is_imported_source(source)
    if "source" in imp and not source_ok:
        findings.append("import.source {} is not a canonical store-relative path spelled from {}/ "
                        "(spec 8.3)".format(_plain_display(source), WORKING_DIRNAME))
    if "source_sha256" in imp and not (isinstance(imp.get("source_sha256"), str)
                                       and _SOURCE_SHA256_RE.match(imp.get("source_sha256"))):
        findings.append("import.source_sha256 {} is not 64 lowercase hexadecimal digits (spec 8.3)".format(
            _plain_display(imp.get("source_sha256"))))
    run_ok = type(run) is str and re.fullmatch(_IMPORT_RUN_PATTERN, run) is not None
    if "run" in imp and not run_ok:
        findings.append("import.run {} is not an imp-<YYYYMMDD>T<HHMMSS>Z-<hash16> run id (spec 8.3)".format(
            _plain_display(run)))
    if source_ok and run_ok:
        # The preserved original is the exact file under its own import run's evidence bundle (spec 8.3,
        # 8.8), so a canonical path elsewhere under .working/, or under another run, is refused. The match
        # is exact: anchored at the start, case-sensitive, and through the `/` after originals, on the two
        # exact strs captured above. The canonical grammar above already refuses an empty <source-path>.
        originals = evidence_run("import", run) + "/originals/"
        if not str.startswith(source, originals):
            findings.append("import.source {} does not lie under {}, the preserved originals of the "
                            "record's own import.run (spec 8.3/8.8)".format(_bounded_display(source), originals))
    imported_at = None
    if "imported_at" in imp:
        if _valid_timestamp(imp.get("imported_at")):
            imported_at = _instant_key(imp.get("imported_at"))
        else:
            findings.append("import.imported_at must be an RFC 3339 UTC timestamp (spec 8.3)")
    if "span" in imp:
        span = imp.get("span")
        # An informational byte range (spec 8.3): two integers, defined here as non-negative and ordered.
        if not (isinstance(span, list) and len(span) == 2 and all(type(n) is int and n >= 0 for n in span)
                and span[0] <= span[1]):
            findings.append("import.span must be an array of two non-negative integers [start, end] with "
                            "start <= end (spec 8.3)")
    if "history" in imp:
        history = imp.get("history")
        if not isinstance(history, list):
            findings.append("import.history must be an array of {field, value} tables (spec 8.3)")
            history = []
        for i, row in enumerate(history):
            where = "import.history[{}]".format(i)
            if not isinstance(row, dict):
                findings.append("{} must be a {{field, value}} table (spec 8.3)".format(where))
                continue
            if set(row) != HISTORY_ROW_KEYS:
                findings.append("{} must hold exactly the two keys field and value, has {} key(s) "
                                "(spec 8.3)".format(where, len(row)))
            field = row.get("field")
            if not isinstance(field, str) or field not in schema_fields:
                findings.append("{}.field {} names no field of the type's schema (spec 8.3)".format(
                    where, _plain_display(field)))
            if not isinstance(row.get("value"), str):
                findings.append("{}.value must be the verbatim source value as a string (spec 8.3)".format(
                    where))
    if "unparsed" in imp:
        unparsed = imp.get("unparsed")
        if not (isinstance(unparsed, list) and all(isinstance(s, str) for s in unparsed)):
            findings.append("import.unparsed must be an array of strings (spec 8.3)")
    return imported_at


def _validate_unrecorded(record, spec, schema_fields, findings):
    """The missingness contract (spec 8.3): `unrecorded` holds exactly one closed-reason row for each
    omitted field of the type's schema that is not exempt, and no row that repeats a field, claims a
    supplied field absent, names a field outside the schema, names an exempt field, or waives a required
    imported envelope field. A field of the type's schema renders through _bounded_display, since a
    module type's fields are the caller's own text; only a required or exempt envelope field, a fixed
    vocabulary, renders raw."""
    exempt = IMPORTED_EXEMPT_FIELDS - _IMPORTED_TYPE_REQUIRED.get(spec.name, frozenset())
    rows = record.get("unrecorded", [])
    if not isinstance(rows, list):
        findings.append("unrecorded must be an array of {field, reason} tables (spec 8.3)")
        rows = []
    seen = set()
    for i, row in enumerate(rows):
        where = "unrecorded[{}]".format(i)
        if not isinstance(row, dict):
            findings.append("{} must be a {{field, reason}} table (spec 8.3)".format(where))
            continue
        _check_closed_table(row, UNRECORDED_ROW_KEYS, where, findings)
        reason = row.get("reason")
        if not isinstance(reason, str) or reason not in IMPORTED_MISSINGNESS_REASONS:
            findings.append("{}.reason {} is not one of {} (spec 8.3)".format(
                where, _bounded_display(reason), list(IMPORTED_MISSINGNESS_REASONS)))
        field = row.get("field")
        if not isinstance(field, str):
            findings.append("{}.field must be a string naming a field of the type's schema "
                            "(spec 8.3)".format(where))
            continue
        if field in seen:
            findings.append("{} repeats the field {}: unrecorded holds exactly one row per omitted "
                            "field (spec 8.3)".format(where, _bounded_display(field)))
            continue
        seen.add(field)
        if field.startswith("x-"):
            findings.append("{} names the x-<vendor> extension {}, which is exempt from missingness "
                            "and MUST be refused (spec 8.3)".format(where, _bounded_display(field)))
        elif field not in schema_fields:
            findings.append("{} names {}, which is no field of the {} schema (spec 8.3)".format(
                where, _bounded_display(field), spec.name))
        elif field in IMPORTED_REQUIRED_KEYS:
            findings.append("{} names the required imported envelope field {!r}, which missingness "
                            "cannot waive (spec 8.3)".format(where, field))
        elif field in exempt:
            findings.append("{} names the exempt optional envelope field {!r}, which MUST be refused "
                            "(spec 8.3)".format(where, field))
        elif field in record:
            findings.append("{} claims the supplied field {} absent (spec 8.3)".format(
                where, _bounded_display(field)))
    for field in sorted(schema_fields):
        if field in record or field in exempt or field in IMPORTED_REQUIRED_KEYS or field in seen:
            continue
        findings.append("the omitted field {} of the {} schema has no unrecorded row "
                        "(spec 8.3)".format(_bounded_display(field), spec.name))


def _validate_imported_links(record, spec, findings):
    """The optional imported `links` (spec 8.3, 8.6): each a closed {rel, id} table, rel from the closed
    vocabulary, and id an imported-series id over IMPORTED_NAMESPACES, since an imported-to-clean link MUST
    be refused (spec 8.6). The relation type constraints hold: `exemplifies` targets a preference_pattern,
    and a done record's `receipt_of` targets a backlog_item, at most once."""
    if "links" not in record:
        return
    links = record.get("links")
    if not isinstance(links, list):
        findings.append("links must be an array of {rel, id} tables")
        return
    receipts = 0
    for i, link in enumerate(links):
        where = "links[{}]".format(i)
        if not isinstance(link, dict):
            findings.append("{} must be a {{rel, id}} table".format(where))
            continue
        _check_closed_table(link, LINK_KEYS, where, findings)
        rel = link.get("rel")
        if not isinstance(rel, str) or rel not in LINK_RELS:
            findings.append("{}.rel {} is not one of {} (spec 8.6)".format(
                where, _bounded_display(rel), list(LINK_RELS)))
            rel = None
        shape = parse_series_id(link.get("id"))
        if shape is None:
            findings.append("{}.id {} is not a well-formed id (spec 8.2)".format(
                where, _bounded_display(link.get("id"))))
            continue
        if shape[0] == shape[1]:
            findings.append("{}.id {!r} is a clean-series id: an imported-to-clean link MUST be refused "
                            "(spec 8.6)".format(where, link.get("id")))
        elif shape[1] not in IMPORTED_NAMESPACES:
            findings.append("{}.id {!r} uses namespace {!r}, which the imported series refuses: {}".format(
                where, link.get("id"), shape[1], _imported_namespace_refusal(shape[1])))
        if rel == "exemplifies" and shape[1] != BASELINE_TYPES["preference_pattern"]:
            findings.append("{} exemplifies must target a preference_pattern ({} namespace) "
                            "(spec 8.6)".format(where, BASELINE_TYPES["preference_pattern"]))
        if rel == "receipt_of" and spec.name == "done":
            receipts += 1
            if shape[1] != BASELINE_TYPES["backlog_item"]:
                findings.append("{} a done record's receipt_of must target a backlog_item ({} "
                                "namespace) (spec 8.1/8.5)".format(where, BASELINE_TYPES["backlog_item"]))
    if receipts > 1:
        findings.append("a done record carries at most one receipt_of link (one-to-one receipt, spec 8.5), "
                        "has {}".format(receipts))


def _validate_imported_values(record, spec, findings, registered_kinds, imported_at):
    """Supplied fields keep their declared value types and vocabularies (spec 8.3), and every historical
    timestamp is RFC 3339 UTC and no later than `imported_at`. An omission is the missingness contract's
    business, so no bundle or required-field rule fires on an absence; the current state-conditional
    placement rules (resolution bundles, transition obligations) are not applied to history (spec 8.3)."""
    if "title" in record and not _is_single_line(record.get("title")):
        findings.append("title must be a non-empty single line (spec 8.3)")
    if "summary" in record:
        if spec.reduced:
            if not _is_single_line(record.get("summary")):
                findings.append("a worklog summary must be a non-empty single line (spec 6.2)")
        elif not isinstance(record.get("summary"), str):
            findings.append("summary must be a string when present")
    for k in IMPORTED_HISTORICAL_TIMESTAMPS:
        if k not in record:
            continue
        if not _valid_timestamp(record.get(k)):
            findings.append("{} must be an RFC 3339 UTC timestamp (spec 8.3)".format(k))
        elif imported_at is not None and _instant_key(record.get(k)) > imported_at:
            findings.append("{} is later than import.imported_at: a historical timestamp is never later "
                            "than the import clock instant (spec 8.3)".format(k))
    # No updated_at/created_at ordering is imposed on history (see the module docstring): spec 8.3 bounds
    # these only by form and the import clock, so a source's disagreeing timestamps are kept as recorded.
    for k in _IMPORTED_TEXT_FIELDS:
        if k in record and (not isinstance(record.get(k), str) or not record.get(k).strip()):
            findings.append("{}.{} must be a non-empty string when supplied (spec 8.3)".format(
                spec.name, k))
    if "detail" in record and not isinstance(record.get("detail"), str):
        findings.append("worklog detail must be a string when present")
    allowed_kinds = set(WORKLOG_KINDS)
    # `registered_kinds` arrives normalized by validate_imported_record: None (absent), a frozenset of
    # exact non-empty strings, or _MALFORMED_CONTROL. It is judged as _validate_worklog judges it, for
    # every worklog entry whether or not it supplies `kind`, and a malformed control fails closed to the
    # built-ins with a finding, never splatting a bare string or crashing on an unhashable element.
    if registered_kinds is _MALFORMED_CONTROL:
        if spec.reduced:
            findings.append("registered worklog kinds must be a list of non-empty strings")
    elif registered_kinds is not None:
        allowed_kinds |= registered_kinds
    if "kind" in record:
        if not isinstance(record.get("kind"), str) or record.get("kind") not in allowed_kinds:
            findings.append("worklog kind {} is not one of {} (spec 6.2)".format(
                _bounded_display(record.get("kind")), _bounded_display(sorted(allowed_kinds))))
    if "scopes" in record:
        scopes = record.get("scopes")
        if not isinstance(scopes, list) or not scopes:
            findings.append("block.scopes must be a non-empty array of record IDs (spec 8.5)")
            scopes = []
        for s in scopes:
            # Either series may be scoped; an imported id is held to IMPORTED_NAMESPACES (spec 8.1/8.2).
            shape = parse_series_id(s)
            if shape is None or shape[1] not in (IMPORTED_NAMESPACES if shape[0] != shape[1]
                                                 else RECORD_NAMESPACES):
                findings.append("block.scopes entry {} is not a record id of a section 8.1 type "
                                "(spec 8.1/8.2)".format(_bounded_display(s)))
    if "delivery" in record:
        delivery = record.get("delivery")
        if not isinstance(delivery, dict):
            findings.append("contribution.delivery must be a table {channel, ref, sent_at, ...} (spec 8.5)")
        else:
            _check_closed_table(delivery, DELIVERY_KEYS, "contribution.delivery", findings)
            for k in ("channel", "ref", "receipt_ref"):
                if k in delivery and (not isinstance(delivery.get(k), str) or not delivery.get(k).strip()):
                    findings.append("contribution.delivery.{} must be a non-empty string".format(k))
            for k in _IMPORTED_DELIVERY_TIMESTAMPS:
                if k not in delivery:
                    continue
                if not _valid_timestamp(delivery.get(k)):
                    findings.append("contribution.delivery.{} must be an RFC 3339 UTC timestamp".format(k))
                elif imported_at is not None and _instant_key(delivery.get(k)) > imported_at:
                    findings.append("contribution.delivery.{} is later than import.imported_at "
                                    "(spec 8.3/8.8)".format(k))
    if spec.name == "reference" and record.get("refs") == []:
        findings.append("a reference's supplied refs must hold at least one ref (spec 8.6); an absent "
                        "capture is an unrecorded row")
    _validate_imported_links(record, spec, findings)
    _validate_refs(record, findings, _bounded_display)


def _imported_namespace_refusal(ns):
    """Why the imported series refuses `ns`, a namespace outside IMPORTED_NAMESPACES, stated for that
    namespace: the importer-only LF, the reserved-excluded TX, the reserved-unassigned CL, or one bound to
    no type at all (spec 8.1/8.2)."""
    for name, bound in IMPORTER_TYPES.items():
        if ns == bound:
            return ("it is the namespace of the importer-only {} quarantine type, and the imported series "
                    "carries no LF (spec 8.1/8.2)".format(name))
    for name, bound in RESERVED_EXCLUDED_TYPES.items():
        if ns == bound:
            return ("it is reserved for the {} type, which is excluded from the adopter standard "
                    "(spec 8.1)".format(name))
    if ns in _RESERVED_UNASSIGNED_NAMESPACES:
        return "it is reserved unassigned and bound to no record type (spec 8.1)"
    return "it is bound to no record type in the section 8.1 taxonomy (spec 8.1/8.2)"


_NO_SLOT = object()
# A collection control that is present but malformed (see validate_imported_record).
_MALFORMED_CONTROL = object()
# The built-in collection types a caller-supplied token control may derive from.
_TOKEN_CONTROL_TYPES = (list, tuple, set, frozenset)


def _control_members(value, bases):
    """The members of a caller-supplied collection control as a fresh list, or None when its real type
    derives from none of `bases`. type() reads the real type, never an advertised `__class__` (which
    isinstance consults), and the members are read through that built-in base's own iterator, so a
    subclass's `__iter__` never runs: a faked control cannot raise here, and a subclass that rewrites
    itself, or something else, when iterated never gets the chance."""
    for base in bases:
        if issubclass(type(value), base):
            return list(base.__iter__(value))
    return None


def _is_str_frozenset(value):
    """True when value is exactly a frozenset whose members are all exact non-empty strs, so membership and
    equality over it run no caller-supplied code."""
    return type(value) is frozenset and all(type(m) is str and m for m in value)


def _imported_spec_copy(key, spec):
    """Check one roster entry as the imported validator would read it. Returns (None, copy), where copy is
    a fresh TypeSpec built from the checked slot values, else (message, None) naming the first structural
    defect. The entry must be exactly a TypeSpec, judged by type(), which reads the real type and never an
    advertised `__class__` (a subclass could answer attribute reads with its own code). Each slot is read
    once, and every slot must hold its exact built-in shape: `name`, `namespace` and `initial` non-empty
    strings; `working`, `terminal`, `proposable`, `gated` and `extra_keys` frozensets of non-empty strings;
    `states` the union of initial, working and terminal, no state holding the `/` qualifier separator;
    `transitions` a dict from a state to a frozenset of states; `proposable` within `states`, `gated`
    within the non-terminal proposable states (the TypeSpec construction rule); and `reduced` a bool. The
    copy holds those immutable values and its own transitions dict, so no caller code that runs later (a
    control's or a record value's own method) can change the grammar that was checked. The key is not yet
    reconciled with the taxonomy here, so a refusal renders it through _bounded_display."""
    if type(spec) is not TypeSpec:
        return ("the type-spec roster entry {} is not exactly a TypeSpec (fail-closed)".format(
            _bounded_display(key))), None
    v = {slot: getattr(spec, slot, _NO_SLOT) for slot in TypeSpec.__slots__}

    def bad(slot):
        return ("the type-spec roster entry {} has a malformed or missing {} (fail-closed)".format(
            _bounded_display(key), slot)), None

    for slot in ("name", "namespace", "initial"):
        if type(v[slot]) is not str or not v[slot]:
            return bad(slot)
    for slot in ("working", "terminal", "proposable", "gated", "extra_keys", "states"):
        if not _is_str_frozenset(v[slot]):
            return bad(slot)
    states = v["states"]
    if states != frozenset({v["initial"]}) | v["working"] | v["terminal"] or any("/" in st for st in states):
        return bad("states")
    if type(v["transitions"]) is not dict or not all(
            type(src) is str and src in states and _is_str_frozenset(dst) and dst <= states
            for src, dst in dict.items(v["transitions"])):
        return bad("transitions")
    if not v["proposable"] <= states:
        return bad("proposable")
    if not v["gated"] <= v["proposable"] - v["terminal"]:
        return bad("gated")
    if type(v["reduced"]) is not bool:
        return bad("reduced")
    copy = object.__new__(TypeSpec)
    for slot in TypeSpec.__slots__:
        setattr(copy, slot, v[slot])
    copy.transitions = dict(dict.items(v["transitions"]))
    return None, copy


def _imported_specs_control(specs):
    """Validate a caller-supplied `specs` roster fully before validate_imported_record reads it. Returns
    (None, roster) on success, where roster is a plain dict from each checked key to a fresh copy of its
    checked entry (_imported_spec_copy), so neither a mapping subclass's later lookup nor a later write to
    a caller's TypeSpec or transitions dict can change what was checked; else (message, None) naming the
    first defect. The roster must really derive from dict: its real type is read by type(), never an
    advertised `__class__`, and its entries through dict's own items(), never an override.

    The shared _specs_roster_error reconciles each key, name and namespace against the section-8.1
    taxonomy; this adds what that leaves open. Every entry is first held to its exact structural shape
    (_imported_spec_copy), so no malformed member reaches a membership test or a comparison and
    raises. A BASELINE type's entry must then equal its authoritative BASELINE_SPECS definition in every
    slot (states, transitions, proposable and gated states, fields, envelope), so a supplied spec cannot
    admit a state, a field or an envelope the spec does not give that type. A module or importer entry
    has no authoritative definition in this unit, so beyond its shape it may not take the reduced worklog
    envelope (spec 8.3 gives it to the worklog alone) and its fields may not name `import`, `unrecorded`
    or an `x-<vendor>` table, each of which would bypass the imported envelope's own rules."""
    if not issubclass(type(specs), dict):
        return "the type-spec roster is not a mapping (fail-closed)", None
    roster = {}
    for key, spec in list(dict.items(specs)):
        if type(key) is not str:
            return "the type-spec roster contains a non-string key (fail-closed)", None
        err, copy = _imported_spec_copy(key, spec)
        if err is not None:
            return err, None
        roster[key] = copy
    err = _specs_roster_error(roster, _bounded_display)
    if err is not None:
        return err, None
    for key, spec in roster.items():
        base = BASELINE_SPECS.get(key)
        if base is not None:
            for slot in TypeSpec.__slots__:
                if getattr(spec, slot) != getattr(base, slot):
                    return ("the type-spec roster entry {!r} contradicts the baseline {} definition's {} "
                            "(spec 8.5; fail-closed)".format(key, key, slot)), None
            continue
        if spec.reduced:
            return ("the type-spec roster entry {!r} takes the reduced envelope, which spec 8.3 gives the "
                    "worklog alone (fail-closed)".format(key)), None
        for field in sorted(spec.extra_keys):
            if field in ("import", "unrecorded") or field.startswith("x-"):
                return ("the type-spec roster entry {!r} declares the field {}, which the imported "
                        "envelope reserves (spec 8.3/8.7; fail-closed)".format(key, _bounded_display(field))), None
    return None, roster


def _imported_not_plain(record):
    """The CANNOT-EVALUATE result for a table that is not plain (_is_plain_value). Nothing is looked up in
    it: its entries are read through dict's own items(), the id is the value under the exact-str key
    `id`, and the label shows that id, through _bounded_display, only when the id is plain itself, else
    calls it unreadable, so neither the label nor the finding runs caller code or exceeds the rendering
    budget. For an exact dict, the finding names the field under which the walk (_plain_problem) found
    the first value that is not plain, and says why; for a dict subclass it names no field, since the
    record itself is the culprit."""
    ids = [v for k, v in dict.items(record) if type(k) is str and k == "id"]
    if not ids:
        label, rid = "imported record (no id)", None
    elif _is_plain_value(ids[0]):
        label, rid = "imported record {}".format(_bounded_display(ids[0])), ids[0]
    else:
        label, rid = "imported record (unreadable id)", None
    if type(record) is not dict:
        why = "the record is a subclass of a table, not exactly one, so its own methods could answer each read"
    else:
        reason, where = _plain_problem(record)
        why = "field {} {}".format(_plain_display(where[0]) if where else "<the record>", reason)
    return RecordValidation(CANNOT_EVALUATE, ["{}: {} (a parsed record holds none; fail-closed)".format(
        label, why)], rid=rid)


def validate_imported_record(record, expected_type=None, specs=None, registered_vendors=frozenset(),
                             registered_kinds=None):
    """Validate one parsed imported record against the closed imported envelope (spec 8.3). Pure: it reads
    no file and no clock. Returns a RecordValidation; every finding, a control refusal included, begins
    `imported record <id>: ` and so names the record by its id, with exactly three exceptions, the inputs
    that have no readable id to give: a table without an `id` key is labelled `imported record (no id): `,
    a table that is not plain and whose own `id` is not plain is labelled `imported record (unreadable
    id): `, and a non-table input yields the single finding `imported record is not a table
    (fail-closed)`.

    The imported envelope: `id` an `imported:<NS>-<n>` id bound to the record's type, `type` (required for
    every type, the worklog included), a one-line `title`, `status` from the type's legal state set
    without `/proposed`, `actor.kind = "importer"` with an `actor.id` naming the importing assistant, and
    the closed `import` provenance table (`source` the canonical path, spelled from `.working/`, of a
    preserved original under `.working/imported/import/<run-id>/originals/` of the record's own `run`,
    `source_sha256` as 64 lowercase hex digits, an `imp-` `run` id, an RFC 3339 UTC `imported_at`, and
    optional `span`, `history` rows of exactly {field, value}, and `unparsed`). Every omitted non-exempt
    field of the type's schema carries exactly one closed-reason `unrecorded` row; supplied fields keep
    their declared types and vocabularies; historical timestamps are no later than `imported_at`;
    `proposed_from` is never carried; unknown keys fail. The importer-only legacy_fragment (LF) is
    refused (spec 8.1/8.2). Before any of the record is read, it must be plain (_plain_problem): exactly
    a dict whose every key and nested value is exactly a plain built-in type, a tree as a parsed record
    is, so no method of a record value (a lookup, a hash, a comparison, a format, a repr, a startswith)
    runs during validation. Its depth is not bounded, since tomllib nests tables to any depth through a
    dotted key or a table header; instead every finding renders a record value, and every name other
    than a section-8.1 type name that a caller's `specs` roster or `registered_kinds` control supplies,
    through _bounded_display, so no rendering
    overflows the stack and each takes at most _DISPLAY_BUDGET characters and the cut marker (a finding
    may render more than one value, and every finding repeats the label, which renders the id). Every
    field then holds only TOML value types, each date-time in a TOML form (_is_toml_value, spec 4.4), a
    registered `x-<vendor>` table and a module field no value rule reads included. A
    supplied `specs` roster is
    checked in full before use, and only fresh copies of its checked entries are read
    (_imported_specs_control). Every control is judged by its
    real type, never by an advertised `__class__`, and a collection control is read once, through its
    built-in base's own iterator (_control_members). A worklog record judges the `registered_kinds`
    control whether or not it supplies `kind`.

    Outcome: CANNOT-EVALUATE when the input is not a table or is not plain, the `specs` or
    `expected_type` control is malformed (a `specs` entry also when it contradicts its authoritative
    definition), or the record's type cannot be identified; INVALID when an identified record violates
    the envelope, or the `registered_vendors` control (or, on a worklog, the `registered_kinds` control)
    is malformed, as validate_record judges them; VALID otherwise.

    Not checked here. Each item but the last three needs evidence beyond this one record (other records,
    the store, the preserved original, the source or the import run); the next two need a definition
    this unit does not hold, and the last is left open so that no record tomllib parses is refused:
      - the cross-record checks: C-IMPORTED-IDS counters and no-deletion, C-LINKS resolution over both
        series, and the C-IMPORTED-SEGREGATION firewall beyond the per-record imported-to-clean link
        refusal (spec 8.3, 8.6);
      - C-IMPORTED-PROVENANCE, which re-reads the preserved original and verifies `source_sha256`
        (spec 8.3): that the file under `import.source` exists and holds those bytes needs the store;
      - that import time did not stand in for event time (spec 8.3): a historical timestamp equal to
        `imported_at` is accepted, since only the source shows whether it was copied from the clock;
      - that no UTC midnight was fabricated from a date-only value (spec 8.3): a `T00:00:00Z` value is
        indistinguishable from a recorded midnight without the original;
      - that unmappable source text was retained in `import.unparsed` rather than dropped (spec 8.3), and
        any byte-level coverage or semantic fidelity, which spec 8.3 says are not machine-proven;
      - that `imported_at` was read from the writer's clock (spec 8.3), and the run-level import
        refusals of spec 8.8 (adoption receipt and plan scope, bound-item drift, the recomputed source
        size and digest, replay identity, the type being enabled in the manifest);
      - immutability after publication and correction only by a fresh run that keeps the old evidence
        and links `supersedes` or `corrects` (spec 8.3, 8.8), which needs the record's history;
      - C-CONTAINMENT's recognition of the imported managed leaves (spec 8.3), a store-layout check;
      - that `actor.id` names the assistant that actually ran the import (spec 8.3): only its presence
        and its form (a non-empty string) are checked, since which assistant ran the run is run
        evidence, not record content;
      - what a registered `x-<vendor>` table means, including that it overrides none of identity, state,
        transitions or the other spec 8.7 concerns: only its registration, that it is a table, that it is
        plain, and that it holds only TOML value types with each date-time in a TOML form
        (_is_toml_value) are checked, since its meaning is the vendor's definition;
      - the field semantics of a module or importer roster entry. Its shape and its reserved field names
        are checked, but this unit holds no authoritative definition for it (the module schemas ship
        with the module schemas release, spec 8.5), and the value rules here key on a field's name, not
        its type: a module entry that declares a baseline field name such as `kind`, `detail`,
        `delivery` or `severity` inherits that field's baseline rule, which may not be the module's;
      - that every str and int is one a TOML document can carry: a str holding a lone surrogate, which
        no UTF-8 document carries, and an int outside TOML's 64-bit range are accepted wherever no value
        rule refuses them, since tomllib.loads yields both."""
    if not issubclass(type(record), dict):
        # No id is available to label a non-table input: the one finding without an id (see above). type()
        # reads the real type, so an object that only advertises dict as its __class__ is not a table.
        return RecordValidation(CANNOT_EVALUATE, ["imported record is not a table (fail-closed)"])
    if not _is_plain_value(record):
        # Before any lookup: a dict subclass's own get, or a value's own hash, format or repr, could raise
        # or answer a check with a value it does not hold.
        return _imported_not_plain(record)
    rid = record.get("id")
    label = "imported record {}".format(_bounded_display(rid) if "id" in record else "(no id)")

    def named(findings):
        return ["{}: {}".format(label, f) for f in findings]

    # The two collection controls are read once, before the roster check and through their real built-in
    # types (_control_members), so no control's own code runs during or after validation. Their members
    # must be exact strings: a str subclass could answer a later hash or comparison with its own code.
    vendors = _control_members(registered_vendors, _TOKEN_CONTROL_TYPES)
    vendors_ok = vendors is not None and all(type(m) is str for m in vendors)
    registered_vendors = frozenset(vendors) if vendors_ok else frozenset()
    if registered_kinds is not None:
        kinds = _control_members(registered_kinds, (list, tuple))
        registered_kinds = (frozenset(kinds) if kinds is not None and all(type(k) is str and k for k in kinds)
                            else _MALFORMED_CONTROL)
    if specs is None:
        specs = BASELINE_SPECS
    else:
        roster_error, specs = _imported_specs_control(specs)
        if roster_error is not None:
            return RecordValidation(CANNOT_EVALUATE, named([roster_error]), rid=rid)
    if expected_type is not None and type(expected_type) is not str:
        # Exactly a str: a subclass, or an object advertising str as its __class__, could answer the type
        # comparison and the roster lookup below with its own code.
        return RecordValidation(CANNOT_EVALUATE, named(
            ["expected_type must be a string or None (fail-closed)"]), rid=rid)
    findings = []
    if not vendors_ok:
        findings.append("registered_vendors must be a collection of x-<vendor> strings (fail-closed)")
    tval = record.get("type")
    if isinstance(tval, str) and tval:
        rtype = tval
        if expected_type is not None and tval != expected_type:
            findings.append("type {} does not match the expected type {} (spec 8.3)".format(
                _bounded_display(tval), _bounded_display(expected_type)))
    elif expected_type is not None:
        # Every imported record carries `type`, the worklog included (spec 8.3): never substitute it.
        findings.append("missing or non-string required field: type (spec 8.3)")
        rtype = expected_type
    else:
        return RecordValidation(CANNOT_EVALUATE, named(
            ["the record has no `type` and no expected type: cannot identify it"]), rid=rid)
    if rtype in IMPORTER_TYPES:
        # New imports use the imported series and verbatim `unparsed` text, never LF quarantine.
        findings.append("type {!r} is the importer-only LF quarantine type, which the imported series "
                        "refuses (spec 8.1/8.2)".format(rtype))
        return RecordValidation(INVALID, named(findings), rtype, rid)
    spec = specs.get(rtype)
    if spec is None:
        findings.append("{} is not a supported record type (baseline: {})".format(
            _bounded_display(rtype), ", ".join(sorted(_bounded_display(s) for s in specs))))
        return RecordValidation(INVALID, named(findings), rtype, rid)

    shape = parse_series_id(rid)
    if "id" not in record:
        findings.append("missing required field: id")
    elif shape is None or shape[0] == shape[1]:
        findings.append("id {} is not an imported-series imported:<NS>-<n> id (spec 8.2/8.3)".format(
            _bounded_display(rid)))
    elif shape[1] not in IMPORTED_NAMESPACES:
        findings.append("id namespace {!r} is refused by the imported series: {}".format(
            shape[1], _imported_namespace_refusal(shape[1])))
    elif shape[1] != spec.namespace:
        findings.append("id namespace {!r} is not the {!r} namespace bound to type {!r} "
                        "(spec 8.2)".format(shape[1], spec.namespace, rtype))

    schema_fields = _imported_schema_fields(spec)
    _check_keyset(record, schema_fields | {"import", "unrecorded"}, registered_vendors, findings,
                  _bounded_display)
    for key, value in dict.items(record):
        # A record is TOML (spec 4.4), so no field holds a value a TOML document cannot, whether or not a
        # value rule reads it: a registered x-<vendor> table, or a module field with no baseline rule, is
        # held to this too. A top-level key that is not a str is an unknown key above.
        if type(key) is str and not _is_toml_value(value):
            findings.append("field {} holds a key or value no TOML document can (a non-string key, None, a "
                            "tuple, a set, a frozenset, or a date-time in no TOML form); a record is TOML "
                            "(spec 4.4)".format(_bounded_display(key)))
    if "proposed_from" in record:
        findings.append("an imported record never carries proposed_from, since its status never takes "
                        "/proposed (spec 8.3)")
    for k in ("title", "status"):
        if k not in record:
            findings.append("missing required field: {}".format(k))
    if "status" in record:
        parsed, err = parse_status(record.get("status"), spec, _bounded_display)
        if err is not None:
            findings.append("status: {}".format(err))
        elif parsed[1] is not None:
            findings.append("imported status {} takes /proposed; an imported status is a legal state "
                            "without it (spec 8.3)".format(_bounded_display(record.get("status"))))
    actor_kind = _validate_actor(record, findings, _bounded_display)
    if actor_kind is not None and actor_kind != "importer":
        findings.append("actor.kind {!r} is not 'importer': an imported record is importer-authored "
                        "(spec 8.3)".format(actor_kind))
    if isinstance(record.get("actor"), dict) and "id" not in record.get("actor"):
        findings.append("missing required field: actor.id, naming the importing assistant (spec 8.3)")

    imported_at = _validate_import_table(record, schema_fields, findings)
    _validate_unrecorded(record, spec, schema_fields, findings)
    _validate_imported_values(record, spec, findings, registered_kinds, imported_at)
    return RecordValidation(INVALID if findings else VALID, named(findings), rtype, rid)


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
        returns the record to a working state "with a recorded reason"). This validator sees statuses
        only, so the caller supplies `pre_proposal_state` and `reason` as evidence it holds: the
        authoring verb reads `pre_proposal_state` from the record's own `proposed_from` envelope field
        (spec 8.3/8.8), written by the proposing transition; when the pre-proposal state is not supplied
        the rejection target cannot be verified and the result is CANNOT-EVALUATE, never a permissive
        VALID (M1); a rejection with no recorded reason is INVALID.
      - An unqualified TERMINAL state does not transition at all (no resurrection, spec 8.4); a revived
        concern is a new record, and supersession is a link, not a state edit.
    """
    if specs is None:
        specs = BASELINE_SPECS
    else:
        # F-R17-A3: reconcile a caller-supplied roster against the section-8.1 taxonomy before it is used, so
        # a misbound roster ({"finding": <backlog_item spec>}) cannot make a finding open->active transition
        # validate under backlog_item's transition table (guard-input-soundness). A non-mapping, a non-string
        # key, or a non-TypeSpec value is subsumed here and fails closed.
        roster_error = _specs_roster_error(specs)
        if roster_error is not None:
            return TransitionCheck(CANNOT_EVALUATE, [roster_error])
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
        findings.append("actor kind {} is not one of {}".format(_safe_display(actor_kind), list(ACTOR_KINDS)))

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
        # supplied by the caller (the authoring verb reads it from the record's own proposed_from field,
        # spec 8.3/8.8; this validator sees statuses only).
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
                "record's own proposed_from field (spec 8.4/8.8)"])
        elif to_state != pre_proposal_state:
            findings.append("a rejection must return to the record's actual pre-proposal state {}, not "
                            "{!r} (a rejection may not restore a state the record was never in, spec "
                            "8.4/8.5)".format(_safe_display(pre_proposal_state), to_state))
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
        elif to_state in spec.terminal or to_state in spec.gated:
            # Landing on a TERMINAL state, or ENTERING a gated non-terminal state (the generalized
            # `gated` attribute, spec 8.4): an assistant/automation actor MUST land '/proposed' (a
            # proposal a maintainer ratifies); a maintainer/importer lands unqualified. Contribution
            # `sent` may be relieved of per-send gating by a valid standing authorization, but that is a
            # record-level policy overlay (validate_record), not part of the base transition grammar,
            # which stays fail-closed here (spec 8.4/8.5).
            kind_label = "terminal" if to_state in spec.terminal else "gated"
            if isinstance(actor_kind, str) and actor_kind in PROPOSER_KINDS:
                if to_qual != "proposed":
                    findings.append("a {} transition into the {!r} state by an {} actor must land "
                                    "'/proposed', not {!r} (spec 8.4)".format(
                                        kind_label, to_state, actor_kind, to_status))
            elif to_qual is not None:
                findings.append("a {} transition into the {!r} state by a {} actor lands unqualified, "
                                "not {!r} (only assistant/automation propose, spec 8.4)".format(
                                    kind_label, to_state, _safe_display(actor_kind), to_status))
        elif to_qual is not None:
            findings.append("a non-terminal transition does not carry '/proposed', got {!r}".format(to_status))

    return TransitionCheck(INVALID if findings else VALID, findings)


# --- counters (spec 8.2) -----------------------------------------------------------------------------

def validate_counters(data, known_namespaces=None, optional_namespaces=None, imported_namespaces=None):
    """Validate a parsed counters.toml (see the counters layout ambiguity note). Returns (high_water,
    findings): high_water is {namespace: int}. Each namespace is two uppercase letters (optionally
    restricted to `known_namespaces`); each value is a non-negative int (a bool is rejected).

    `optional_namespaces` names namespaces that are ACCEPTED-if-present but NOT REQUIRED (importer/
    quarantine namespaces such as LF): a counter namespace is accepted (never flagged "not a known
    namespace") when it is in known_namespaces OR in optional_namespaces, but the completeness check
    (every known namespace must carry a high-water) applies ONLY to known_namespaces, so an optional
    namespace absent from [counters] is not a finding. The section-8.1 taxonomy-validity check still
    applies to every counter including optional ones (an optional namespace bound to no record type is
    still flagged). It is validated fail-closed exactly like known_namespaces (a malformed control is a
    finding and treated as empty), and when None/omitted every existing behaviour is unchanged.

    `imported_namespaces` opts into the imported series (spec 8.2). None (the default) keeps every
    behaviour above, so an `imported:<NS>` key is still a finding. A collection of namespaces accepts the
    quoted key `"imported:<NS>"` as an independent number line, requires one for each listed namespace
    (completeness per series, as for known_namespaces), and flags an imported key whose namespace is not in
    IMPORTED_NAMESPACES or not listed, or whose value is not a genuine high-water. The returned map then
    holds both series under their own keys. A malformed control is a finding and treated as empty."""
    findings = []
    high_water = {}
    # The series-aware path (imported_namespaces given) judges every counter, clean or imported, against
    # the one fixed id ceiling; the default path keeps the original ambient-limit rule.
    hw_ok = _high_water_check(imported_namespaces is not None)
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
    # optional_namespaces (accepted-if-present, not required) is validated the same fail-closed way: a
    # malformed control is a finding and treated as EMPTY, so it can never widen what is accepted beyond
    # what a well-formed control names (guard-input-soundness; spec 8.2).
    if optional_namespaces is not None and not _is_str_token_control(optional_namespaces):
        findings.append("optional_namespaces must be a collection of namespace strings (fail-closed)")
        optional_namespaces = frozenset()
    if imported_namespaces is not None and not _is_str_token_control(imported_namespaces):
        findings.append("imported_namespaces must be a collection of namespace strings (fail-closed)")
        imported_namespaces = frozenset()
    extra = set(data) - COUNTERS_TOP_KEYS
    if extra:
        findings.append("counters.toml unknown top-level key(s): {}".format(
            ", ".join(_safe_key_names(extra))))
    # F-R17-D1: counters.toml is a schema-bearing machine document and MUST carry the exact integer marker,
    # mirroring the index rule (absent -> finding, wrong -> fail-closed). A missing marker is no longer a
    # silent clean pass.
    if "schema" not in data:
        findings.append("counters.toml is missing the required schema marker (spec 8.2)")
    elif type(data.get("schema")) is not int:
        findings.append("counters.toml schema must be an integer")
    elif data.get("schema") != SUPPORTED_SCHEMA:
        findings.append("counters.toml schema {} is not the supported schema version {} (fail-closed; "
                        "do not parse under v{} assumptions)".format(
                            _safe_display(data.get("schema")), SUPPORTED_SCHEMA, SUPPORTED_SCHEMA))
    counters = data.get("counters")
    if counters is None:
        # An absent [counters] table is a finding when namespaces are known to require a high-water each
        # (spec 8.2: one monotonic high-water per namespace); with no known namespaces it is simply empty.
        if known_namespaces:
            findings.append("counters.toml has no [counters] table but known namespace(s) {} each require "
                            "a high-water (spec 8.2)".format(sorted(known_namespaces)))
        if imported_namespaces:
            findings.append("counters.toml has no [counters] table but imported namespace(s) {} each "
                            "require a high-water (spec 8.2)".format(sorted(imported_namespaces)))
        return high_water, findings
    if not isinstance(counters, dict):
        findings.append("[counters] is not a table")
        return high_water, findings
    for ns, val in counters.items():
        if imported_namespaces is not None and isinstance(ns, str) and ns.startswith(IMPORTED_PREFIX):
            # An imported counter (spec 8.2): its namespace must be one the imported series may carry and
            # one the caller listed; it is recorded under its own quoted key, never the clean line's.
            if ns[len(IMPORTED_PREFIX):] not in IMPORTED_NAMESPACES:
                findings.append("[counters] key {} is not an imported counter of a type the imported series "
                                "may carry (spec 8.1/8.2)".format(_safe_display(ns)))
                continue
            if ns[len(IMPORTED_PREFIX):] not in imported_namespaces:
                findings.append("[counters] imported counter {} is not a known imported namespace".format(
                    _safe_display(ns)))
            if not hw_ok(val):
                findings.append("[counters].{} must be a non-negative integer high-water mark of at most "
                                "{} digits".format(_safe_display(ns), _MAX_ID_DIGITS))
                continue
            high_water[ns] = val
            continue
        if not (isinstance(ns, str) and len(ns) == 2 and ns.isupper() and ns.isalpha()):
            # `ns` is an untrusted inner key: an oversized non-decimal int key would crash a raw {!r}
            # render, so it goes through _safe_display like every other key render (Fable-F2).
            findings.append("[counters] key {} is not a two-letter uppercase namespace".format(
                _safe_display(ns)))
            continue
        # A namespace must be bound to a real record type in the section 8.1 taxonomy, checked against that
        # authoritative set EVEN WHEN known_namespaces is omitted, so an unknown 2-letter namespace like ZZ
        # is never silently clean (spec 8.1/8.2, guard-input-soundness; M5).
        if ns not in RECORD_NAMESPACES:
            findings.append("[counters] namespace {!r} is bound to no record type in the section 8.1 "
                            "taxonomy (spec 8.1/8.2)".format(ns))
            continue
        # A counter namespace is ACCEPTED when it is a known (required) namespace OR an optional
        # (accepted-if-present) one; only a namespace that is neither, under a known-namespaces
        # restriction, is flagged unknown. The taxonomy-validity check above still applies to all.
        accepted_ns = (known_namespaces is not None and ns in known_namespaces) or \
                      (optional_namespaces is not None and ns in optional_namespaces)
        if known_namespaces is not None and not accepted_ns:
            findings.append("[counters] namespace {!r} is not a known namespace".format(ns))
        # A bool is an int subclass; a high-water is a genuine non-negative int, never True/False.
        if not hw_ok(val):
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
    if imported_namespaces is not None:
        for ns in sorted(imported_namespaces):
            if IMPORTED_PREFIX + ns not in high_water:
                findings.append("[counters] is missing a high-water for imported namespace {} "
                                "(spec 8.2)".format(_safe_display(IMPORTED_PREFIX + ns)))
    return high_water, findings


def high_water(high, ns):
    """The current high-water for a namespace, 0 when it has allocated nothing yet. Fails closed
    (ValueError) on a counters map that is not a table, rather than reading a malformed map as
    high-water 0 (which could let an existing id be reused; guard-input-soundness, spec 8.2)."""
    if not isinstance(high, dict):
        raise ValueError("counters map is not a table (spec 8.2)")
    if not isinstance(ns, str):
        # ns is the namespace key; a non-string (e.g. an unhashable list) would crash on the dict lookup
        # below. next_id already guards ns, but guard it here too so the shipped helper is robust
        # standalone, failing closed rather than a TypeError (guard-input-soundness; spec 8.2).
        raise ValueError("namespace must be a string (spec 8.2)")
    return high.get(ns, 0)


def _validated_counter_map(high, where, imported_series=False):
    """Fail closed (ValueError) on a counters map that is not a mapping of section-8.1 taxonomy namespaces
    to genuine non-negative ints. next_id and check_monotonic both TRUST the map they are handed, so a
    corrupt map (a non-taxonomy namespace, or a value that is not a genuine int such as a bool, which is
    an int subclass with True == 1) is a fail-closed error rather than a silent zero or a bypassed guard
    (spec 8.2, guard-input-soundness; M7). With `imported_series` True an `imported:<NS>` key is a number
    line too (spec 8.2)."""
    if not isinstance(high, dict):
        raise ValueError("{}: counters map is not a table (spec 8.2)".format(where))
    for ns, val in high.items():
        if not _is_counter_key(ns, imported_series):
            # `ns` is an untrusted map key: an oversized non-decimal int key would crash a raw {!r} render
            # and lose this module's named refusal, so it renders through _safe_display (Fable-F3).
            raise ValueError("{}: namespace {} is bound to no record type in the section 8.1 taxonomy "
                             "(spec 8.1/8.2)".format(where, _safe_display(ns)))
        if not _high_water_check(imported_series is True)(val):
            raise ValueError("{}: high-water for {!r} must be a genuine non-negative int, got {} "
                             "(a bool is not a high-water; spec 8.2)".format(where, ns, _safe_display(val)))


def next_id(high, ns, known_complete=False, imported_series=False):
    """Allocate the next id for a namespace: returns (id_string, new_high_water). The high-water only
    ever increases by one, so an id is never reused (spec 8.2). The caller records new_high_water back to
    counters.toml under the store lock as one atomic claim (spec 8.2, the atomic-claim-from-pool rule).

    Fails closed (ValueError) rather than allocating an unsound id (guard-input-soundness; M7):
      - `ns` is bound to no record type in the section 8.1 taxonomy (an out-of-taxonomy namespace like ZZ);
      - the counters map is corrupt (not a table of taxonomy namespaces to genuine non-negative ints);
      - `ns` has NO recorded high-water AND the map is not known to be complete. Allocating from a missing
        counter would read its high-water as 0 and could reuse an id that already exists; that is sound
        only when the map has been proved complete (validate_counters with known_namespaces). The caller
        asserts that proof with `known_complete=True`; without it, an absent counter is refused.

    With `imported_series=True` (a genuine bool), `ns` may also be an imported counter key
    `imported:<NS>`, which allocates `imported:<NS>-<n>` from that key's own high-water and never reads the
    clean `<NS>` line (spec 8.2); the map may then hold both series. Without it, an imported key is refused
    exactly as before. An absent imported key is refused whatever `known_complete` says: spec 8.2
    requires the imported series' counter rows, one for each enabled type, and lets only a genuinely first
    adoption start at zero, written as an explicit zero row, and a clean-only known_namespaces proof says
    nothing about the imported line, so reading an absent row as high-water 0 would zero-seed prior
    ancestry and could reuse an imported id. On this series-aware path the
    map's values and the allocated number share parse_series_id's fixed ceiling (_MAX_ID_NUMBER): an
    allocation past it is refused, never emitted as an id the parser or check_ids_within_counters would
    reject, whatever sys.set_int_max_str_digits is."""
    flag_error = _imported_series_error(imported_series)
    if flag_error is not None:
        raise ValueError("cannot allocate an id: {}".format(flag_error))
    if (imported_series and isinstance(ns, str) and ns.startswith(IMPORTED_PREFIX)
            and not _is_counter_key(ns, imported_series)):
        raise ValueError("cannot allocate an id for {}: its namespace is not one the imported series may "
                         "carry (spec 8.1/8.2)".format(_safe_display(ns)))
    if not isinstance(ns, str) or not _is_counter_key(ns, imported_series):
        # Guard by type first: a non-string ns (e.g. a list) is unhashable and would crash on the
        # RECORD_NAMESPACES frozenset membership; it is also bound to no record type. Fail closed either
        # way, never a TypeError (guard-input-soundness; spec 8.1/8.2).
        raise ValueError("cannot allocate an id for namespace {}: it is bound to no record type in the "
                         "section 8.1 taxonomy (spec 8.1/8.2)".format(_safe_display(ns)))
    if not isinstance(known_complete, bool):
        # known_complete is a genuine-bool PROOF flag, not a truthiness test: a non-bool (e.g. the truthy
        # string "false", or 1) must NOT be read as True and allow allocation from a counters map that is
        # not actually known complete, which could reuse an existing id. Fail closed (spec 8.2,
        # guard-input-soundness).
        raise ValueError("cannot allocate an id: known_complete must be a genuine bool proof flag, got "
                         "{}; a non-bool must not be read as True (spec 8.2)".format(
                             _safe_display(known_complete)))
    _validated_counter_map(high, "next_id", imported_series)
    if imported_series and ns.startswith(IMPORTED_PREFIX) and ns not in high:
        # The imported line accepts NO completeness proof for an absent key, known_complete=True included:
        # spec 8.2 requires the imported series' counter rows, one for each enabled type, and lets only a
        # genuinely first adoption start at zero, written as an explicit zero row, so reading an absent
        # row as high-water 0 would zero-seed prior ancestry and could reuse an imported id. Fail closed;
        # the remedy is recording the counter row, never a proof flag.
        raise ValueError("cannot allocate an id for namespace {!r}: the imported series has no recorded "
                         "high-water row for it, and an absent imported counter is refused whatever "
                         "known_complete says; record the explicit counter row (zero only on a genuinely "
                         "first adoption), so prior ancestry is never zero-seeded (spec 8.2)".format(ns))
    if ns not in high and not known_complete:
        raise ValueError("cannot allocate an id for namespace {!r}: it has no recorded high-water and the "
                         "counters map is not known complete; validate it with validate_counters(..., "
                         "{}=...) and pass known_complete=True, so an absent counter cannot "
                         "read as high-water 0 and reuse an existing id (spec 8.2)".format(
                             ns, "imported_namespaces" if ns.startswith(IMPORTED_PREFIX)
                             else "known_namespaces"))
    n = high_water(high, ns) + 1
    if imported_series:
        # The series-aware path: one fixed ceiling, shared with parse_series_id and the counter checks, and
        # a chunked rendering, so neither the verdict nor the id depends on the ambient int limit.
        if n > _MAX_ID_NUMBER:
            raise ValueError("cannot allocate an id for namespace {!r}: the next id number exceeds the "
                             "{}-digit id ceiling ({}; spec 8.2)".format(ns, _MAX_ID_DIGITS, _safe_display(n)))
        return "{}-{}".format(ns, _id_digits(n)), n
    if not _genuine_high_water(n):
        # The high-water passed _genuine_high_water (within the 4300-digit int-to-str limit), but the id it
        # allocates is high-water + 1, which at exactly the limit overflows one digit past it; refuse with
        # the module's OWN named message rather than let str(n) in the format below raise the raw CPython
        # int-to-str ValueError (range-bounds closed at its root; guard-input-soundness; Fable-M3).
        raise ValueError("cannot allocate an id for namespace {!r}: the next id number is not expressible "
                         "under the runtime int-to-str limit ({}; spec 8.2)".format(ns, _safe_display(n)))
    return "{}-{}".format(ns, n), n


def check_monotonic(old_high, new_high, imported_series=False):
    """Confirm counters only ever advance (spec 8.2: never reset, IDs never reused). Every namespace in
    `old_high` must be present in `new_high` with a value greater than or equal to its old high-water; a
    regression or a dropped namespace (which would let a later allocation reuse an id) is a finding. Both
    maps are validated first: a non-taxonomy namespace or a value that is not a genuine non-negative int
    (a bool is not, though True == 1) is a fail-closed finding, never a silent pass that lets a corrupt
    counter read as a valid high-water (guard-input-soundness; M7). With `imported_series` True an
    `imported:<NS>` key is compared on its own number line, independently of the clean one (spec 8.2)."""
    flag_error = _imported_series_error(imported_series)
    if flag_error is not None:
        return [flag_error]
    findings = []
    for label, m in (("old", old_high), ("new", new_high)):
        if not isinstance(m, dict):
            findings.append("the {} counters map is not a table (spec 8.2)".format(label))
            continue
        for ns, val in m.items():
            if not _is_counter_key(ns, imported_series):
                findings.append("{} counters namespace {} is bound to no record type in the section 8.1 "
                                "taxonomy (spec 8.1/8.2)".format(label, _safe_display(ns)))
            if not _high_water_check(imported_series)(val):
                findings.append("{} counters high-water for {} must be a genuine non-negative int, got "
                                "{} (a bool is not a high-water; spec 8.2)".format(label, _safe_display(ns), _safe_display(val)))
    if findings:
        return findings                  # a corrupt map is not compared for monotonicity (fail-closed)
    for ns, old in old_high.items():
        if ns not in new_high:
            findings.append("namespace {!r} vanished from counters (would allow id reuse; spec 8.2)".format(ns))
        elif new_high[ns] < old:
            findings.append("namespace {!r} high-water regressed {} to {} (counters never reset; "
                            "spec 8.2)".format(ns, _safe_display(old), _safe_display(new_high[ns])))
    return findings


def check_ids_within_counters(ids, high, imported_series=False):
    """Confirm every allocated id's number is within its namespace's high-water (spec 8.2): an id above
    the high-water means the counter never reserved it, the reuse hazard the atomic claim prevents. `ids`
    is an iterable of id strings; a malformed id, or an unknown namespace, is itself a finding. With
    `imported_series` True an `imported:<NS>-<n>` id is checked against its own `imported:<NS>` high-water,
    never the clean one (spec 8.2: each series is evaluated independently)."""
    flag_error = _imported_series_error(imported_series)
    if flag_error is not None:
        return [flag_error]
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
        shape = _id_counter_shape(rid, imported_series)
        if shape is None:
            findings.append(("id {} is not a well-formed <NS>-<n> or imported:<NS>-<n> id over a namespace "
                             "its series may carry (spec 8.1/8.2)" if imported_series else
                             "id {} is not a well-formed <NS>-<n> id").format(_safe_display(rid)))
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
        if not _high_water_check(imported_series)(hv):
            findings.append("namespace {!r} high-water {} is not a non-negative integer (spec 8.2)".format(
                ns, _safe_display(hv)))
            continue
        if n > hv:
            findings.append("id {!r} exceeds the {} high-water {} (unreserved id; spec 8.2)".format(
                rid, ns, _safe_display(hv)))
    return findings


def check_unique_ids(ids, imported_series=False):
    """Confirm no id is reused across a set of records (spec 8.2: IDs are never reused). Returns a finding
    per duplicated id. With `imported_series` True an `imported:<NS>-<n>` id is well-formed too; it never
    collides with the clean `<NS>-<n>`, since the two series are independent (spec 8.2)."""
    flag_error = _imported_series_error(imported_series)
    if flag_error is not None:
        return [flag_error]
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
        if _id_counter_shape(rid, imported_series) is None:
            findings.append(("malformed id {}: an id must be a well-formed '<NS>-<n>' or 'imported:<NS>-<n>' "
                             "string over a namespace its series may carry (spec 8.1/8.2)" if imported_series
                             else "malformed id {}: an id must be a well-formed '<NS>-<n>' string (spec 8.2)"
                             ).format(_safe_display(rid)))
            continue
        if rid in seen:
            findings.append("duplicate id {!r}: IDs are never reused (spec 8.2)".format(rid))
        else:
            seen.add(rid)
    return findings


# --- self-test ---------------------------------------------------------------------------------------

# True only inside the bounded child of self_test's caller-timer vector, which runs the self-test again
# in-process there; that nested run skips the vector, so it never recurses.
_SELF_TEST_NESTED = False


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

    # 8b: proposed_from (spec 8.8): the writer-recorded pre-proposal state a rejection restores. Legal
    # only on a '/proposed' status, and only naming a legal predecessor state under the type's table.
    proposer = dict(kind="assistant")
    check("proposed-from-on-proposed-ok",
          validate_record(envelope("backlog_item", 3, "done/proposed", actor=proposer,
                                   proposed_from="active")).status == VALID)
    check("proposed-from-on-unqualified-invalid",
          validate_record(envelope("backlog_item", 3, "active", proposed_from="open")).status == INVALID)
    check("proposed-from-illegal-predecessor-invalid",   # open -> done is not in the backlog table
          validate_record(envelope("backlog_item", 3, "done/proposed", actor=proposer,
                                   proposed_from="open")).status == INVALID)
    check("proposed-from-qualified-value-invalid",
          validate_record(envelope("finding", 2, "fixed/proposed", actor=proposer,
                                   proposed_from="open/proposed")).status == INVALID)
    check("proposed-from-non-string-clean-invalid",   # unhashable value: clean INVALID, never a TypeError
          validate_record(envelope("finding", 2, "fixed/proposed", actor=proposer,
                                   proposed_from=["open"])).status == INVALID)
    check("proposed-from-legal-predecessor-ok",
          validate_record(envelope("finding", 2, "fixed/proposed", actor=proposer,
                                   proposed_from="open")).status == VALID)
    check("proposed-from-on-worklog-invalid",   # not in the reduced envelope (closed keyset)
          validate_record(dict(wl, proposed_from="open"), expected_type="worklog").status == INVALID)

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
    _, bad_cf = validate_counters({"schema": 1, "counters": {"BI": -1}})
    check("counters-negative-invalid", bad_cf)
    _, bool_cf = validate_counters({"schema": 1, "counters": {"BI": True}})   # bool is not a high-water int
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

    # 13b (M5): the gated actor-vs-`/proposed` rule is a CREATION-SNAPSHOT rule (created_at == updated_at
    # as instants), not an at-rest rule. Over block/preference_pattern/contribution, the six-way matrix per
    # type: at the creation snapshot the four actor x qualifier combinations grade exactly as before; PAST
    # the snapshot (updated_at > created_at, a transition has occurred) the rule does not fire, so a
    # legitimately ratified assistant-created record at a bare gated grant is VALID and a maintainer-created
    # record left at gated `/proposed` after an assistant transition is VALID. The two post-transition
    # vectors FAIL without the fix (old code flagged them from the lone snapshot). Extra per-type fields are
    # supplied so the qualifier/timestamp is the sole variable; TS is the creation instant, TS2 is later.
    TS2 = "2026-08-13T09:14:02Z"
    check("m5-instant-key-sanity", _instant_key(TS) < _instant_key(TS2))
    # gated-type -> the extra fields that otherwise make each state VALID (block: scopes; PP: context +
    # rationale; contribution `sent`: the identity trio plus a full delivery bundle).
    _DLV = {"channel": "email", "ref": "peer-inbox/msg-1", "sent_at": TS}
    gated_extras = {
        "block": ({"scopes": ["BI-1"]}, "active"),
        "preference_pattern": ({"context": "a synthetic context", "rationale": "a synthetic rationale"},
                               "active"),
        "contribution": ({"recipient": "peer-project", "dedup_class": "cls-1",
                          "content_digest": "sha256:00", "delivery": _DLV}, "sent"),
    }
    n = 30
    for tname, (extra, gstate) in gated_extras.items():
        n += 1
        # (a) creation snapshot: assistant bare grant -> INVALID (must propose).
        check("m5-{}-snap-assistant-bare-invalid".format(tname),
              validate_record(envelope(tname, n, gstate, actor={"kind": "assistant"}, **extra))
              .status == INVALID)
        # (b) creation snapshot: assistant `/proposed` -> VALID.
        check("m5-{}-snap-assistant-proposed-ok".format(tname),
              validate_record(envelope(tname, n, gstate + "/proposed", actor={"kind": "assistant"},
                                       **extra)).status == VALID)
        # (c) creation snapshot: maintainer bare grant -> VALID.
        check("m5-{}-snap-maintainer-bare-ok".format(tname),
              validate_record(envelope(tname, n, gstate, actor={"kind": "maintainer"}, **extra))
              .status == VALID)
        # (d) creation snapshot: maintainer `/proposed` -> INVALID (only proposers carry the qualifier).
        check("m5-{}-snap-maintainer-proposed-invalid".format(tname),
              validate_record(envelope(tname, n, gstate + "/proposed", actor={"kind": "maintainer"},
                                       **extra)).status == INVALID)
        # (e) POST-TRANSITION: assistant-creator at the bare gated grant -> VALID (ratified). FAILS without
        # the fix (old at-rest rule flagged it INVALID regardless of the transition).
        check("m5-{}-posttx-assistant-bare-ok".format(tname),
              validate_record(envelope(tname, n, gstate, actor={"kind": "assistant"}, updated_at=TS2,
                                       **extra)).status == VALID)
        # (f) POST-TRANSITION: maintainer-creator left at gated `/proposed` -> VALID. FAILS without the fix.
        check("m5-{}-posttx-maintainer-proposed-ok".format(tname),
              validate_record(envelope(tname, n, gstate + "/proposed", actor={"kind": "maintainer"},
                                       updated_at=TS2, **extra)).status == VALID)

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
          any("registered worklog kinds must be a list of non-empty strings" in f for f in b_failopen.findings))
    b_listoflist = validate_record(dict(wl, kind="fixed"), expected_type="worklog",
                                   registered_kinds=[["perf"]])
    check("r3b-listoflist-kinds-finding-no-crash",
          any("registered worklog kinds must be a list of non-empty strings" in f for f in b_listoflist.findings))
    check("r3b-legit-list-kinds-still-ok",
          validate_record(dict(wl, kind="perf"), expected_type="worklog",
                          registered_kinds=["perf"]).status == VALID)

    # 20 (M3): counter completeness. An absent [counters] with known namespaces is a finding; a known
    # namespace with no high-water is a MISSING finding; a complete table is clean.
    _, m3a = validate_counters({"schema": 1}, known_namespaces={"BI"})
    check("counters-absent-table-with-known-ns-invalid", bool(m3a))
    _, m3b = validate_counters({"schema": 1, "counters": {"BI": 5}}, known_namespaces={"BI", "FN"})
    check("counters-missing-known-ns-invalid", any("missing a high-water" in f for f in m3b))
    _, m3c = validate_counters({"schema": 1, "counters": {"BI": 5, "FN": 2}}, known_namespaces={"BI", "FN"})
    check("counters-complete-known-ns-ok", not m3c)
    _, m3d = validate_counters({"schema": 1}, known_namespaces=None)   # no known ns: an empty file is not a finding
    check("counters-empty-no-known-ns-ok", not m3d)

    # 20b (optional/importer namespaces, accepted-if-present, NOT required; the LF fresh-init case). An
    # optional namespace present alongside a complete known baseline is CLEAN; the SAME optional namespace
    # ABSENT is still clean (not required); an optional namespace that is NOT taxonomy-valid is still
    # flagged; and a REQUIRED (known) namespace missing is still flagged even when an optional set is given.
    _, o1 = validate_counters({"schema": 1, "counters": {"BI": 5, "LF": 0}},
                              known_namespaces={"BI"}, optional_namespaces={"LF"})
    check("counters-optional-ns-present-ok", not o1)
    _, o2 = validate_counters({"schema": 1, "counters": {"BI": 5}},
                              known_namespaces={"BI"}, optional_namespaces={"LF"})
    check("counters-optional-ns-absent-ok", not o2)
    _, o3 = validate_counters({"schema": 1, "counters": {"BI": 5, "ZZ": 1}},
                              known_namespaces={"BI"}, optional_namespaces={"ZZ"})
    check("counters-optional-ns-not-taxonomy-valid-flagged",
          any("bound to no record type" in f for f in o3))
    _, o4 = validate_counters({"schema": 1, "counters": {"LF": 0}},
                              known_namespaces={"BI"}, optional_namespaces={"LF"})
    check("counters-required-ns-missing-flagged-with-optional",
          any("missing a high-water" in f for f in o4))
    # a malformed optional_namespaces control is a finding and treated as empty (fail-closed).
    _, o5 = validate_counters({"schema": 1, "counters": {"BI": 5}},
                              known_namespaces={"BI"}, optional_namespaces="LF")
    check("counters-optional-ns-malformed-control-flagged",
          any("optional_namespaces must be a collection" in f for f in o5))

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
    _, m5a = validate_counters({"schema": 1, "counters": {"ZZ": 1}})
    check("m5-counters-nontaxonomy-ns-invalid", any("section 8.1 taxonomy" in f for f in m5a))
    _, m5b = validate_counters({"schema": 1, "counters": {"BI": 1}})
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

    # M1: a rejection restores the ACTUAL pre-proposal state (spec 8.4), supplied by the caller (the
    # authoring verb reads it from the record's own proposed_from field, spec 8.8). The
    # matrix-predecessor heuristic is REMOVED: it blessed active -> dropped/proposed
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

    # ----- reconcile-draft fixes (U2 retro: Fable + codex). The fixN1/fixA3/fixA4-subepsilon/fix10/fix8/
    # fixB1 vectors discriminate a fix landed in THIS commit (they fail or crash pre-fix); the remaining
    # fix2/fix3/fix4/fix5*/fix6/fixC6/fixD1 vectors are retained regression guards, not fail-to-pass. -----
    # ROUND-6 codex: PIN the int-str-conversion limit to the default (4300) around the oversized-int
    # fixtures so their verdicts come from the CODE under test, not the ambient sys.set_int_max_str_digits
    # (a hostile caller could set 0/5001/640, breaking fix8-*/r2fix3-*); restored in finally so this
    # self-test leaves the process limit UNCHANGED (test-hermeticity). At the pinned 4300, _nines (4300
    # digits) passes counter-map validation but the allocated next id (4301 digits) trips the
    # _genuine_high_water(n) guard, so removing that guard reaches the raw str(n) 'Exceeds the limit'
    # and r2fix3-next-id-boundary-refused catches it regardless of the ambient limit.
    _prev_idlimit = sys.get_int_max_str_digits()
    sys.set_int_max_str_digits(4300)
    try:
        _big = 10 ** 5000
        nl_env = envelope("backlog_item", 1, "open", id="BI-1" + chr(10), created_at=TS + chr(10), updated_at=TS + chr(10))
        check("fixN1-newline-record-invalid", validate_record(nl_env).status == INVALID)
        check("fixN1-newline-id-shape-none", _valid_id_shape("BI-1" + chr(10)) is None)
        check("fixN1-newline-ts-false", not _valid_timestamp(TS + chr(10)))
        check("fixN1-newline-unique-distinct", bool(check_unique_ids(["BI-1", "BI-1" + chr(10)])))
        check("fixA3-nonascii-digit-ts-false", not _valid_timestamp("202" + chr(0x666) + "-01-02T03:04:05Z"))
        # fixA4 replaced a FLOAT fractional-seconds key with a decimal-string one. The distinct-key (.11/.12)
        # and trailing-zero-equal (.5/.50) vectors were removed as false pins: a float key ALSO distinguishes
        # .11/.12 and equates .5/.50, so they passed with or without the fix and discriminated nothing. Only the
        # sub-epsilon vector below discriminates: two fractions differing beyond double precision collapse to one
        # float (updated_at == created_at, a clean chronology) but stay distinct as strings (updated_at precedes
        # created_at, INVALID), so it fails pre-fix and passes post-fix.
        check("fixA4-subepsilon-regression-invalid",
              validate_record(envelope("backlog_item", 1, "open",
                                       created_at="2026-08-12T09:14:02.12345678901234567892Z",
                                       updated_at="2026-08-12T09:14:02.12345678901234567891Z")).status == INVALID)
        check("fix10-u2028-title-invalid",
              validate_record(envelope("finding", 1, "open", title="a" + chr(0x2028) + "b")).status == INVALID)
        check("fix10-u2028-summary-invalid",
              validate_record(dict(wl, summary="a" + chr(0x2028) + "b"), expected_type="worklog").status == INVALID)
        _hw8, f8 = validate_counters(dict(schema=1, counters=dict(BI=_big)))
        check("fix8-oversized-counter-flagged", bool(f8) and "BI" not in _hw8)
        try:
            next_id(dict(BI=_big), "BI", known_complete=True)
            check("fix8-next-id-oversized-refused", False)
        except ValueError as _e8:
            # self-test-discrimination: assert the module's OWN named counter-validation diagnostic, not merely
            # "some ValueError". Pre-fix, next_id let str(n) trip CPython's raw "Exceeds the limit ... integer
            # string conversion" ValueError, which a bare `except ValueError` would ALSO satisfy; requiring the
            # named reason means the uncontrolled CPython crash no longer passes as a clean refusal.
            check("fix8-next-id-oversized-refused",
                  "must be a genuine non-negative int" in str(_e8))
        check("fix2-unique-oversized-clean", bool(check_unique_ids([_big])))
        check("fix2-within-oversized-clean", bool(check_ids_within_counters([_big], dict())))
        check("fix2-monotonic-oversized-key-clean", bool(check_monotonic(dict(), {_big: 1})))
        check("fix2-transition-oversized-preproposal-clean",
              validate_transition("backlog_item", "dropped/proposed", "open", "maintainer",
                                  pre_proposal_state=_big, reason="x").status == INVALID)
        check("fixB1-empty-registered-kind-invalid",
              validate_record(dict(wl, kind=""), expected_type="worklog", registered_kinds=[""]).status == INVALID)
        _rvbad = envelope("finding", 8, "open")
        _rvbad["x-aiqt"] = dict(a=1)
        check("fix3-malformed-registered-vendors-invalid",
              validate_record(_rvbad, registered_vendors="x-aiqt,x-evil").status == INVALID)
        check("fix5c-counters-bool-schema-flagged",
              any("schema must be an integer" in f
                  for f in validate_counters(dict(schema=True, counters=dict(BI=1)))[1]))
        try:
            next_id(dict(BI=5), "FN", known_complete="no")
            check("fix4-known-complete-nonbool-refused", False)
        except ValueError:
            check("fix4-known-complete-nonbool-refused", True)
        try:
            next_id(dict(BI=-1), "BI", known_complete=True)
            check("fix6-negative-map-next-id-refused", False)
        except ValueError:
            check("fix6-negative-map-next-id-refused", True)
        check("fix5b-reject-whitespace-reason-invalid",
              validate_transition("finding", "fixed/proposed", "open", "maintainer",
                                  pre_proposal_state="open", reason="   ").status == INVALID)
        check("fixC6-ad-missing-action-only-invalid",
              validate_record(envelope("autonomous_decision", 1, "recorded",
                                       actor=dict(kind="assistant"), classification="ACT")).status == INVALID)
        check("fixC6-ad-missing-classification-only-invalid",
              validate_record(envelope("autonomous_decision", 1, "recorded",
                                       actor=dict(kind="assistant"), action="merged")).status == INVALID)
        _d1 = validate_transition("backlog_item", "done/proposed", "dropped", "maintainer",
                                  pre_proposal_state="dropped", reason="isolate terminal-target")
        check("fixD1-reject-to-terminal-isolated-invalid", _d1.status == INVALID)
        check("fixD1-reject-to-terminal-isolated-named",
              any("rejection to a working state" in f for f in _d1.findings))
        # ----- round-2 reconcile additions (Fable ff-QA MINOR 1-5 + gemini finding 3 at this module's
        # shared-helper call sites). Each fix has a discriminating vector that fails or crashes pre-fix;
        # r2fix4-normal-title-still-ok and r2fix5-wellformed-vendors-no-finding are over-reach guards. -------
        # test-hermeticity: build the 4300-digit boundary fixture by ARITHMETIC (10**4300 - 1), not int("9"*4300).
        # int() of a 4300-digit string trips CPython's int-to-str conversion limit if a caller lowered
        # sys.set_int_max_str_digits below 4300, crashing this suite before its summary; pow/subtraction is never
        # subject to that limit, so the fixture is constructible regardless of the ambient limit (value identical).
        _nines = 10 ** 4300 - 1
        _a_nl = dict(kind="maintainer"); _a_nl["x" + chr(10) + "y"] = 1
        _r2f1 = validate_record(envelope("finding", 50, "open", actor=_a_nl))
        check("r2fix1-unknownkey-newline-escaped",
              _r2f1.status == INVALID and all(chr(10) not in f for f in _r2f1.findings))
        _a_big = dict(kind="maintainer"); _a_big[_big] = 1
        check("r2fix1-oversized-int-key-no-crash",
              validate_record(envelope("finding", 51, "open", actor=_a_big)).status == INVALID)
        _d_big = dict(); _d_big[_big] = 1
        check("r2fix1-counters-oversized-int-key-no-crash", bool(validate_counters(_d_big)[1]))
        _tc_nl = validate_transition("finding", "open", "fixed/proposed", "m" + chr(10) + "FORGED")
        check("r2fix2-txn-actorkind-newline-escaped",
              _tc_nl.status == INVALID and all(chr(10) not in f for f in _tc_nl.findings))
        try:
            next_id(dict(BI=_nines), "BI", known_complete=True)
            check("r2fix3-next-id-boundary-refused", False)
        except ValueError as _e:
            check("r2fix3-next-id-boundary-refused",
                  "Exceeds the limit" not in str(_e) and "integer string conversion" not in str(_e))
        check("r2fix4-blank-title-invalid",
              validate_record(envelope("finding", 52, "open", title="   ")).status == INVALID)
        check("r2fix4-blank-summary-invalid",
              validate_record(dict(wl, summary="   "), expected_type="worklog").status == INVALID)
        check("r2fix4-normal-title-still-ok",
              validate_record(envelope("finding", 53, "open", title="A real title")).status == VALID)
        _r2f5 = validate_record(envelope("finding", 54, "open"), registered_vendors="x-aiqt")
        check("r2fix5-malformed-vendors-surfaced",
              _r2f5.status == INVALID and any("registered_vendors must be a collection" in f
                                              for f in _r2f5.findings))
        _r2f5b = envelope("finding", 55, "open")
        _r2f5b["x-aiqt"] = dict(v="ok")
        check("r2fix5-wellformed-vendors-no-finding",
              not any("registered_vendors must be a collection" in f
                      for f in validate_record(_r2f5b, registered_vendors=REG).findings))

        # ----- round-3 residual sweep (Fable ff-QA F2 crash-class siblings, F3 message-integrity siblings,
        # F4 blank-string siblings). Each vector discriminates a fix landed HERE: the F2/F3 vectors CRASH the
        # validator with the raw CPython int-to-str ValueError pre-fix (aborting self_test), and the F4 vectors
        # validate VALID pre-fix; each is INVALID/refused post-fix, with the *-still-ok guards catching over-reach.
        # F2 (crash-class): an oversized non-decimal int key renders through _safe_display, never a raw {!r},
        # at the three unknown-key/roster render sites the r2fix1 sweep left open. Each returns a structured
        # INVALID/finding rather than aborting self_test with an uncontrolled ValueError.
        _f2a = envelope("finding", 60, "open"); _f2a[_big] = 1
        check("f2a-toplevel-oversized-int-key-no-crash", validate_record(_f2a).status == INVALID)
        check("f2b-counters-inner-oversized-int-key-no-crash",
              bool(validate_counters({"counters": {_big: 1}})[1]))
        # An oversized non-decimal int roster key is now a non-string key: F-R17-A3 reconciles the roster
        # first, so this is a fail-closed CANNOT-EVALUATE (updating the old INVALID expectation), still with
        # no raw CPython int-to-str crash.
        check("f2c-unsupported-type-oversized-specs-key-no-crash",
              validate_record({"type": "artifact", "id": "AR-1"},
                              specs={_big: BASELINE_SPECS["finding"]}).status == CANNOT_EVALUATE)
        # F3 (message-integrity): a refusal handed an oversized non-decimal int control keeps this module's OWN
        # named ValueError, never the raw CPython "Exceeds the limit"/"integer string conversion" text. Pre-fix
        # the {!r} render raises that CPython ValueError while building the message; assert the module's message.
        try:
            next_id({}, _big, known_complete=True)
            check("f3-next-id-oversized-ns-named", False)
        except ValueError as _e:
            check("f3-next-id-oversized-ns-named",
                  "Exceeds the limit" not in str(_e) and "integer string conversion" not in str(_e))
        try:
            next_id({}, "BI", known_complete=_big)
            check("f3-known-complete-oversized-named", False)
        except ValueError as _e:
            check("f3-known-complete-oversized-named",
                  "Exceeds the limit" not in str(_e) and "integer string conversion" not in str(_e))
        try:
            next_id({_big: 1}, "BI", known_complete=True)
            check("f3-counter-map-oversized-ns-named", False)
        except ValueError as _e:
            check("f3-counter-map-oversized-ns-named",
                  "Exceeds the limit" not in str(_e) and "integer string conversion" not in str(_e))
    finally:
        sys.set_int_max_str_digits(_prev_idlimit)
    # F4 (blank-string): a whitespace-only required payload is semantically blank and INVALID, harmonizing
    # the Fable-M4 stance across actor.id, ref.locator, finding.severity, pending_decision.decision/
    # decided_by, and autonomous_decision.classification/action. A real value still validates VALID.
    check("f4-actor-id-whitespace-invalid",
          validate_record(envelope("backlog_item", 61, "open",
                                   actor={"kind": "maintainer", "id": "   "})).status == INVALID)
    check("f4-actor-id-real-still-ok",
          validate_record(envelope("backlog_item", 62, "open",
                                   actor={"kind": "maintainer", "id": "orchestrator"})).status == VALID)
    check("f4-ref-locator-whitespace-invalid",
          validate_record(envelope("backlog_item", 63, "open",
                                   refs=[{"kind": "path", "locator": "   ", "note": "n"}])).status == INVALID)
    check("f4-severity-whitespace-invalid",
          validate_record(envelope("finding", 64, "fixed/proposed", actor={"kind": "assistant"},
                                   severity="   ")).status == INVALID)
    check("f4-decision-whitespace-invalid",
          validate_record(envelope("pending_decision", 65, "decided", decision="   ",
                                   decided_at=TS, decided_by="maintainer")).status == INVALID)
    check("f4-decided-by-whitespace-invalid",
          validate_record(envelope("pending_decision", 66, "decided", decision="chose X",
                                   decided_at=TS, decided_by="   ")).status == INVALID)
    check("f4-ad-classification-whitespace-invalid",
          validate_record(envelope("autonomous_decision", 67, "recorded", actor={"kind": "assistant"},
                                   classification="   ", action="merged")).status == INVALID)
    check("f4-ad-action-whitespace-invalid",
          validate_record(envelope("autonomous_decision", 68, "recorded", actor={"kind": "assistant"},
                                   classification="ACT", action="   ")).status == INVALID)

    # ----- F-R17-A3: a MISBOUND caller-supplied roster is a cannot-evaluate, never a silent pass under the
    # wrong type's grammar. The reconciliation runs in BOTH validate_record and validate_transition and
    # admits the baseline and importer/module taxonomy extensions unchanged.
    def _a3_spec(name, namespace):
        s = object.__new__(TypeSpec)
        s.name = name
        s.namespace = namespace
        return s

    _a3_misbound = {"finding": BASELINE_SPECS["backlog_item"]}
    check("a3-misbound-roster-record-cannot-eval",
          validate_record({"type": "finding", "id": "FN-1", "status": "open"},
                          specs=_a3_misbound).status == CANNOT_EVALUATE)
    # F-R18-A3TEST: use open->active, NOT open->fixed. Under the misbound backlog_item grammar `open->active`
    # is a LEGAL transition, so the OLD impl (roster check reverted) returns VALID here; the roster
    # reconciliation is the ONLY layer that turns it into CANNOT-EVALUATE, so reverting that guard reds this.
    # `open->fixed` did NOT discriminate: `fixed` is not a backlog_item state, so the OLD impl already
    # CANNOT-EVALUATEs the unparseable target regardless of the roster check.
    check("a3-misbound-roster-transition-cannot-eval",
          validate_transition("finding", "open", "active", "maintainer",
                              specs=_a3_misbound).status == CANNOT_EVALUATE)
    check("a3-contradicting-namespace-cannot-eval",
          validate_record({"type": "finding", "id": "FN-1", "status": "open"},
                          specs={"finding": _a3_spec("finding", "BI")}).status == CANNOT_EVALUATE)
    check("a3-absent-taxonomy-type-cannot-eval",
          _specs_roster_error({"not_a_type": _a3_spec("not_a_type", "ZZ")}) is not None)
    check("a3-baseline-roster-reconciles", _specs_roster_error(BASELINE_SPECS) is None)
    _a3_extended = dict(BASELINE_SPECS)
    _a3_extended["legacy_fragment"] = _a3_spec("legacy_fragment", IMPORTER_TYPES["legacy_fragment"])
    _a3_extended["artifact"] = _a3_spec("artifact", MODULE_TYPES["artifact"][0])
    check("a3-taxonomy-extension-reconciles", _specs_roster_error(_a3_extended) is None)
    check("a3-baseline-record-still-ok",
          validate_record(envelope("backlog_item", 90, "open")).status == VALID)

    # ----- F-R17-D1: counters.toml requires the exact integer schema marker; an absent marker is a finding,
    # not a silent clean pass; a present marker still validates clean.
    check("d1-counters-absent-schema-finding", bool(validate_counters({"counters": {}})[1]))
    check("d1-counters-present-schema-ok", not validate_counters({"schema": 1, "counters": {}})[1])

    # ----- 1.1.0 base-schema additions: contribution (CN), maintainer_decision (MD), preference_pattern
    # (PP), the generalized `gated` attribute, and the exemplifies/derives_from link rels. Each new state/
    # transition/bundle/link constraint carries a discriminating valid+invalid pair (change-carries-check).
    contrib_base = dict(recipient="peer-project", dedup_class="rule-fix", content_digest="sha256:abc")
    sent_delivery = {"channel": "inbox", "ref": "msg-1", "sent_at": TS}

    # contribution: a maintainer-authored proposed contribution is VALID (proposed is not gated); a
    # maintainer sent contribution lands the bare `sent` grant with a complete delivery bundle.
    check("contribution-proposed-ok",
          validate_record(envelope("contribution", 1, "proposed", **contrib_base)).status == VALID)
    check("contribution-sent-maintainer-ok",
          validate_record(envelope("contribution", 2, "sent", delivery=dict(sent_delivery),
                                   **contrib_base)).status == VALID)
    # `sent` gating: an assistant lands `sent/proposed` (legal); a bare `sent` by an assistant is INVALID
    # absent a standing authorization; a maintainer ratification of `sent` is a transition (below).
    check("contribution-sent-proposed-assistant-ok",
          validate_record(envelope("contribution", 3, "sent/proposed", actor={"kind": "assistant"},
                                   delivery=dict(sent_delivery), **contrib_base)).status == VALID)
    c_unauth = validate_record(envelope("contribution", 4, "sent", actor={"kind": "assistant"},
                                        delivery=dict(sent_delivery), **contrib_base))
    check("contribution-sent-assistant-unqualified-invalid", c_unauth.status == INVALID)
    check("contribution-sent-assistant-unqualified-named",
          any("gated 'sent' state must be 'sent/proposed'" in f for f in c_unauth.findings))
    # a VALID standing authorization for the declared recipient relieves per-send gating; a MALFORMED one,
    # or one naming a different recipient, fails closed to gated (bare `sent` by assistant stays INVALID).
    check("contribution-sent-assistant-standing-auth-ok",
          validate_record(envelope("contribution", 5, "sent", actor={"kind": "assistant"},
                                   delivery=dict(sent_delivery), **contrib_base),
                          standing_auth={"recipients": ["peer-project"]}).status == VALID)
    check("contribution-sent-assistant-malformed-auth-invalid",
          validate_record(envelope("contribution", 6, "sent", actor={"kind": "assistant"},
                                   delivery=dict(sent_delivery), **contrib_base),
                          standing_auth={"recipients": "peer-project"}).status == INVALID)
    check("contribution-sent-assistant-auth-other-recipient-invalid",
          validate_record(envelope("contribution", 7, "sent", actor={"kind": "assistant"},
                                   delivery=dict(sent_delivery), **contrib_base),
                          standing_auth={"recipients": ["someone-else"]}).status == INVALID)
    # delivery bundle: sent_at is forbidden at proposed; a sent contribution missing the bundle is INVALID;
    # receipt fields are legal only at acknowledged.
    check("contribution-sent-at-at-proposed-invalid",
          validate_record(envelope("contribution", 8, "proposed", delivery={"sent_at": TS},
                                   **contrib_base)).status == INVALID)
    check("contribution-sent-missing-delivery-invalid",
          validate_record(envelope("contribution", 9, "sent", **contrib_base)).status == INVALID)
    check("contribution-receipt-at-sent-invalid",
          validate_record(envelope("contribution", 10, "sent",
                                   delivery=dict(sent_delivery, receipt_ref="r-1"),
                                   **contrib_base)).status == INVALID)
    check("contribution-acknowledged-with-receipt-ok",
          validate_record(envelope("contribution", 11, "acknowledged",
                                   delivery=dict(sent_delivery, receipt_ref="r-1", receipted_at=TS),
                                   **contrib_base)).status == VALID)
    check("contribution-missing-recipient-invalid",
          validate_record(envelope("contribution", 12, "proposed",
                                   dedup_class="rule-fix", content_digest="sha256:abc")).status == INVALID)
    # contribution transitions: proposed -> sent/proposed by assistant legal; proposed -> acknowledged
    # illegal; maintainer ratifies sent/proposed -> sent; a bare `sent` entry by an assistant is INVALID
    # at the base grammar (standing-auth relief is a record-level overlay), a maintainer lands it bare.
    check("txn-contribution-proposed-to-sent-proposed-legal",
          validate_transition("contribution", "proposed", "sent/proposed", "assistant").status == VALID)
    check("txn-contribution-proposed-to-acknowledged-illegal",
          validate_transition("contribution", "proposed", "acknowledged", "maintainer").status == INVALID)
    check("txn-contribution-ratify-sent-legal",
          validate_transition("contribution", "sent/proposed", "sent", "maintainer").status == VALID)
    check("txn-contribution-assistant-sent-bare-invalid",
          validate_transition("contribution", "proposed", "sent", "assistant").status == INVALID)
    check("txn-contribution-maintainer-sent-bare-legal",
          validate_transition("contribution", "proposed", "sent", "maintainer").status == VALID)
    check("txn-contribution-sent-to-acknowledged-proposed-legal",
          validate_transition("contribution", "sent", "acknowledged/proposed", "assistant").status == VALID)

    # maintainer_decision: created-terminal `recorded` carrying its decision; actor.kind restricted to
    # {maintainer, importer}; never proposable; exemplifies must target a PP.
    check("maintainer-decision-ok",
          validate_record(envelope("maintainer_decision", 1, "recorded",
                                   decision="Chose option 1 for its reversibility")).status == VALID)
    check("maintainer-decision-importer-actor-ok",
          validate_record(envelope("maintainer_decision", 2, "recorded", actor={"kind": "importer"},
                                   decision="migrated ruling")).status == VALID)
    check("maintainer-decision-assistant-actor-invalid",
          validate_record(envelope("maintainer_decision", 3, "recorded", actor={"kind": "assistant"},
                                   decision="x")).status == INVALID)
    check("maintainer-decision-missing-decision-invalid",
          validate_record(envelope("maintainer_decision", 4, "recorded")).status == INVALID)
    check("maintainer-decision-proposed-invalid",
          validate_record(envelope("maintainer_decision", 5, "recorded/proposed",
                                   decision="x")).status == INVALID)
    check("maintainer-decision-exemplifies-pp-ok",
          validate_record(envelope("maintainer_decision", 6, "recorded", decision="x",
                                   links=[{"rel": "exemplifies", "id": "PP-1"}])).status == VALID)
    md_ex = validate_record(envelope("maintainer_decision", 7, "recorded", decision="x",
                                     links=[{"rel": "exemplifies", "id": "FN-1"}]))
    check("maintainer-decision-exemplifies-non-pp-invalid", md_ex.status == INVALID)
    check("maintainer-decision-exemplifies-non-pp-named",
          any("exemplifies must target a preference_pattern" in f for f in md_ex.findings))

    # preference_pattern: active -> retired; `active` is gated (assistant lands active/proposed, a
    # maintainer lands bare active); context and rationale are required.
    check("preference-pattern-active-maintainer-ok",
          validate_record(envelope("preference_pattern", 1, "active",
                                   context="when X", rationale="because Y")).status == VALID)
    check("preference-pattern-active-proposed-assistant-ok",
          validate_record(envelope("preference_pattern", 2, "active/proposed", actor={"kind": "assistant"},
                                   context="when X", rationale="because Y")).status == VALID)
    check("preference-pattern-active-assistant-bare-invalid",
          validate_record(envelope("preference_pattern", 3, "active", actor={"kind": "assistant"},
                                   context="when X", rationale="because Y")).status == INVALID)
    check("preference-pattern-active-proposed-maintainer-invalid",
          validate_record(envelope("preference_pattern", 4, "active/proposed",
                                   context="when X", rationale="because Y")).status == INVALID)
    check("preference-pattern-missing-fields-invalid",
          validate_record(envelope("preference_pattern", 5, "active")).status == INVALID)
    check("txn-preference-pattern-retire-proposed-legal",
          validate_transition("preference_pattern", "active", "retired/proposed", "assistant").status == VALID)
    check("txn-preference-pattern-ratify-legal",
          validate_transition("preference_pattern", "active/proposed", "active", "maintainer").status == VALID)

    # link rels: derives_from is now a valid rel (any-type source); exemplifies is validated above.
    check("derives-from-link-ok",
          validate_record(envelope("contribution", 30, "proposed",
                                   links=[{"rel": "derives_from", "id": "FN-1"}],
                                   **contrib_base)).status == VALID)

    # Clean-to-imported links (spec 8.6): relates, derives_from, follows and same-type supersedes are the
    # only relations a clean record may carry to an imported-series id, judged on the full envelope and on
    # the reduced worklog alike. Every other relation, a supersedes of another type, and an imported
    # namespace the series refuses are each INVALID with exactly one named finding.
    _xs_refused = "a clean-to-imported link is limited to relates, derives_from, follows, and same-type"

    def _xs_one(v, text):
        return v.status == INVALID and len(v.findings) == 1 and text in v.findings[0]

    for _rel in ("relates", "derives_from", "follows"):
        _xs = validate_record(envelope("finding", 40, "open", links=[{"rel": _rel, "id": "imported:BI-3"}]))
        check("clean-to-imported-{}-ok".format(_rel), _xs.status == VALID and not _xs.findings)
    _xs = validate_record(envelope("finding", 41, "open", links=[{"rel": "supersedes", "id": "imported:FN-3"}]))
    check("clean-to-imported-same-type-supersedes-ok", _xs.status == VALID and not _xs.findings)
    _xs = validate_record(dict(wl, links=[{"rel": "supersedes", "id": "imported:WL-2"}]), expected_type="worklog")
    check("clean-to-imported-worklog-same-type-supersedes-ok", _xs.status == VALID and not _xs.findings)
    for _rel, _tid in (("resolves", "imported:BI-3"), ("remediates", "imported:BI-3"),
                       ("receipt_of", "imported:BI-3"), ("corrects", "imported:BI-3"),
                       ("exemplifies", "imported:PP-3")):
        _xs = validate_record(envelope("finding", 42, "open", links=[{"rel": _rel, "id": _tid}]))
        check("clean-to-imported-{}-refused".format(_rel), _xs_one(_xs, _xs_refused))
    _xs = validate_record(envelope("finding", 43, "open", links=[{"rel": "supersedes", "id": "imported:BI-3"}]))
    check("clean-to-imported-cross-type-supersedes-refused", _xs_one(_xs, _xs_refused))
    _xs = validate_record(dict(wl, links=[{"rel": "supersedes", "id": "imported:BI-2"}]), expected_type="worklog")
    check("clean-to-imported-worklog-cross-type-supersedes-refused", _xs_one(_xs, _xs_refused))
    # A clean done's receipt_of MUST target a clean backlog item (spec 8.6), so an imported one is refused.
    _xs = validate_record(envelope("done", 40, "recorded", links=[{"rel": "receipt_of", "id": "imported:BI-3"}]))
    check("clean-to-imported-done-receipt-refused", _xs_one(_xs, _xs_refused))
    _xs = validate_record(envelope("finding", 44, "open", links=[{"rel": "relates", "id": "imported:LF-1"}]))
    check("clean-to-imported-refused-namespace-invalid", _xs_one(_xs, "which the imported series refuses"))
    # A relation outside the vocabulary is named once, by the vocabulary rule, and never again as a
    # cross-series refusal.
    _xs = validate_record(envelope("finding", 45, "open", links=[{"rel": "blesses", "id": "imported:BI-3"}]))
    check("clean-to-imported-unknown-rel-one-finding", _xs_one(_xs, ".rel 'blesses' is not one of"))

    # The imported series (spec 8.2): `imported:<NS>-<n>` ids and `"imported:<NS>"` counters, opt-in, each
    # series on its own number line; with no opt-in every helper keeps its clean-only behaviour.
    def _alloc_refused(thunk):
        try:
            thunk()
        except ValueError:
            return True
        return False

    check("series-id-imported-parses", parse_series_id("imported:BI-7") == ("imported:BI", "BI", 7))
    check("series-id-clean-parses", parse_series_id("BI-7") == ("BI", "BI", 7))
    for bad in ("imported:BI-07", "imported:BI-0", "Imported:BI-1", "imported:bi-1", "imported:BIX-1",
                "imported:imported:BI-1", "importedBI-1", "imported: BI-1", " imported:BI-1",
                "imported:BI-1" + chr(10), "imported:", None, 7, ["imported:BI-1"]):
        check("series-id-malformed-{}".format(_safe_display(bad)[:40]), parse_series_id(bad) is None)
    # test-hermeticity: the oversized-number verdict is judged by digit count, never by the ambient
    # sys.set_int_max_str_digits, so run each boundary vector under several ambient limits (unlimited, the
    # 640 floor, the 4300 default, and a high one) and restore the caller's limit afterward.
    _series_prev_idlimit = sys.get_int_max_str_digits()
    try:
        for _limit in (0, 640, 4300, 271828):
            sys.set_int_max_str_digits(_limit)
            check("series-id-malformed-oversized-limit-{}".format(_limit),
                  parse_series_id("imported:BI-" + "9" * (_MAX_ID_DIGITS + 1)) is None
                  and parse_series_id("BI-" + "9" * 5000) is None)
            check("series-id-max-digits-parses-limit-{}".format(_limit),
                  parse_series_id("imported:BI-" + "9" * _MAX_ID_DIGITS) == ("imported:BI", "BI", _MAX_ID_NUMBER))
            # The clean-only default path keeps its original rule: an id is accepted exactly when int()
            # of its suffix succeeds under the ambient limit, so every existing caller's verdict is
            # unchanged, and an accepted id's number always renders back under that limit (a caller that
            # formats it, such as a duplicate worklog id message, never crashes).
            for _len in (641, _MAX_ID_DIGITS + 1):
                _cid = "WL-" + "9" * _len
                _shape = _valid_id_shape(_cid)
                check("clean-id-default-rule-{}-limit-{}".format(_len, _limit),
                      (_shape is not None) == (_limit == 0 or _len <= _limit)
                      and (_shape is None or str(_shape[1]) == _cid[3:]))
            # One ceiling for counter validation, allocation and parsing on the series-aware path: a
            # counter past it is refused, a counter at it is valid but allocates nothing, and every id the
            # allocator emits parses and passes the counter-bound and uniqueness checks.
            _ceil_ctr = dict([("BI", _MAX_ID_NUMBER), ("imported:BI", _MAX_ID_NUMBER)])
            _, _ceil_f = validate_counters(dict(schema=1, counters=dict(_ceil_ctr)), known_namespaces=["BI"],
                                           imported_namespaces=["BI"])
            check("series-counter-at-ceiling-ok-limit-{}".format(_limit), not _ceil_f)
            for _key in ("BI", "imported:BI"):
                _over = dict(_ceil_ctr)
                _over[_key] = _MAX_ID_NUMBER + 1
                _, _ceil_f = validate_counters(dict(schema=1, counters=_over), known_namespaces=["BI"],
                                               imported_namespaces=["BI"])
                check("series-counter-past-ceiling-flagged-{}-limit-{}".format(_key, _limit),
                      any("[counters].{} must be".format(_key if _key == "BI" else _safe_display(_key)) in f
                          for f in _ceil_f))
                check("series-next-id-at-ceiling-refused-{}-limit-{}".format(_key, _limit),
                      _alloc_refused(lambda: next_id(_ceil_ctr, _key, imported_series=True)))
                check("series-next-id-past-ceiling-refused-{}-limit-{}".format(_key, _limit),
                      _alloc_refused(lambda: next_id(_over, _key, imported_series=True)))
                check("series-monotonic-past-ceiling-flagged-{}-limit-{}".format(_key, _limit),
                      bool(check_monotonic(_ceil_ctr, _over, imported_series=True)))
                check("series-ids-within-past-ceiling-high-flagged-{}-limit-{}".format(_key, _limit),
                      bool(check_ids_within_counters(["BI-1", "imported:BI-1"], _over, imported_series=True)))
                for _hw in (_MAX_ID_NUMBER - 1, 10 ** 700, 7):
                    try:
                        _rid, _n = next_id({_key: _hw}, _key, imported_series=True)
                    except ValueError:
                        _rid = _n = None
                    check("series-next-id-parses-{}-{}-limit-{}".format(_key, _hw.bit_length(), _limit),
                          _n == _hw + 1 and parse_series_id(_rid) == (_key, "BI", _n)
                          and not check_ids_within_counters([_rid], {_key: _n}, imported_series=True)
                          and not check_unique_ids([_rid], imported_series=True))
    finally:
        sys.set_int_max_str_digits(_series_prev_idlimit)
    check("series-id-clean-grammar-unchanged", _valid_id_shape("imported:BI-7") is None)
    check("imported-namespaces-exclude-lf-tx-cl",
          not ({"LF", "TX", "CL"} & IMPORTED_NAMESPACES) and {"BI", "WL", "SL"} <= IMPORTED_NAMESPACES)

    imp_ctr = {"schema": 1, "counters": {"BI": 3, "imported:BI": 5}}
    imp_hw, imp_f = validate_counters(imp_ctr, known_namespaces={"BI"}, imported_namespaces={"BI"})
    check("imported-counters-ok", not imp_f and imp_hw == {"BI": 3, "imported:BI": 5})
    _, imp_f = validate_counters(imp_ctr, known_namespaces={"BI"})
    check("imported-counters-default-still-flagged", any("'imported:BI'" in f for f in imp_f))
    _, imp_f = validate_counters({"schema": 1, "counters": {"BI": 3}}, known_namespaces={"BI"},
                                 imported_namespaces={"BI"})
    check("imported-counters-missing-flagged",
          any("missing a high-water for imported namespace 'imported:BI'" in f for f in imp_f))
    _, imp_f = validate_counters({"schema": 1}, imported_namespaces={"BI"})
    check("imported-counters-absent-table-flagged", any("imported namespace(s)" in f for f in imp_f))
    for key in ("imported:LF", "imported:TX", "imported:CL", "imported:ZZ", "imported:", "imported:bi"):
        _, imp_f = validate_counters({"schema": 1, "counters": {"BI": 3, "imported:BI": 1, key: 1}},
                                     known_namespaces={"BI"}, imported_namespaces={"BI"})
        check("imported-counters-bad-key-flagged-{}".format(key),
              any(_safe_display(key) in f for f in imp_f))
    _, imp_f = validate_counters({"schema": 1, "counters": {"BI": 3, "imported:BI": 1, "imported:FN": 1}},
                                 known_namespaces={"BI"}, imported_namespaces={"BI"})
    check("imported-counters-unlisted-flagged", any("'imported:FN'" in f for f in imp_f))
    for badval in (True, -1, 1.5, "1"):
        _, imp_f = validate_counters({"schema": 1, "counters": {"BI": 3, "imported:BI": badval}},
                                     known_namespaces={"BI"}, imported_namespaces={"BI"})
        check("imported-counters-bad-value-flagged-{}".format(_safe_display(badval)),
              any("[counters].'imported:BI' must be" in f for f in imp_f))
    _, imp_f = validate_counters(imp_ctr, known_namespaces={"BI"}, imported_namespaces="BI")
    check("imported-counters-malformed-control-flagged",
          any("imported_namespaces must be" in f for f in imp_f))

    def _alloc_error(thunk):
        try:
            thunk()
        except ValueError as e:
            return e
        return None

    both = {"BI": 3, "imported:BI": 5}
    check("imported-next-id-own-line", next_id(both, "imported:BI", imported_series=True) == ("imported:BI-6", 6))
    check("next-id-imported-absent-refused-even-known-complete",
          _alloc_refused(lambda: next_id({"BI": 3}, "imported:BI", known_complete=True, imported_series=True)))
    check("next-id-imported-zero-row-allocates",
          next_id({"imported:BI": 0}, "imported:BI", imported_series=True) == ("imported:BI-1", 1))
    check("next-id-clean-absent-known-complete-unchanged",
          next_id({}, "BI", known_complete=True) == ("BI-1", 1)
          and next_id({"imported:BI": 5}, "BI", known_complete=True, imported_series=True) == ("BI-1", 1))
    check("imported-next-id-clean-line-unchanged", next_id(both, "BI", imported_series=True) == ("BI-4", 4))
    check("imported-next-id-default-refused", _alloc_refused(lambda: next_id(both, "imported:BI")))
    check("imported-next-id-default-mixed-map-refused", _alloc_refused(lambda: next_id(both, "BI")))
    check("imported-next-id-absent-not-complete-refused",
          _alloc_refused(lambda: next_id({"BI": 3}, "imported:BI", imported_series=True)))
    check("imported-next-id-lf-refused", _alloc_refused(
        lambda: next_id({}, "imported:LF", known_complete=True, imported_series=True)))
    check("imported-next-id-nonbool-flag-refused", _alloc_refused(
        lambda: next_id(both, "imported:BI", imported_series="false")))

    check("imported-monotonic-ok", not check_monotonic(both, {"BI": 3, "imported:BI": 6}, imported_series=True))
    check("imported-monotonic-regression-flagged", any("regressed" in f for f in check_monotonic(
        both, {"BI": 3, "imported:BI": 4}, imported_series=True)))
    check("imported-monotonic-vanished-flagged", any("vanished" in f for f in check_monotonic(
        both, {"BI": 3}, imported_series=True)))
    check("imported-monotonic-default-flagged", bool(check_monotonic(both, both)))
    check("imported-monotonic-nonbool-flag-flagged", bool(check_monotonic({"BI": 1}, {"BI": 1}, imported_series=1)))

    check("imported-ids-within-ok",
          not check_ids_within_counters(["BI-3", "imported:BI-5"], both, imported_series=True))
    check("imported-id-exceeds-own-line", any("exceeds" in f for f in check_ids_within_counters(
        ["imported:BI-4"], {"BI": 9, "imported:BI": 3}, imported_series=True)))
    check("clean-id-exceeds-own-line", any("exceeds" in f for f in check_ids_within_counters(
        ["BI-4"], {"BI": 3, "imported:BI": 9}, imported_series=True)))
    check("imported-ids-default-malformed", bool(check_ids_within_counters(["imported:BI-1"], both)))
    check("imported-ids-nonbool-flag-flagged",
          bool(check_ids_within_counters(["BI-1"], both, imported_series="yes")))

    check("imported-unique-distinct-series", not check_unique_ids(["BI-1", "imported:BI-1"], imported_series=True))
    check("imported-unique-duplicate-flagged", any("duplicate" in f for f in check_unique_ids(
        ["imported:BI-1", "imported:BI-1"], imported_series=True)))
    check("imported-unique-default-malformed", bool(check_unique_ids(["imported:BI-1"])))
    check("imported-unique-nonbool-flag-flagged", bool(check_unique_ids(["BI-1"], imported_series=0)))
    # Every helper that accepts imported ids enforces the one IMPORTED_NAMESPACES set (spec 8.1/8.2): the
    # importer-only LF, the reserved TX, the unassigned CL and an unknown ZZ are refused even when the
    # caller's map tracks them.
    for _ns in ("LF", "TX", "CL", "ZZ"):
        _ikey = "imported:" + _ns
        _iid = _ikey + "-1"
        check("imported-ids-within-namespace-refused-{}".format(_ns),
              any("not a well-formed" in f for f in check_ids_within_counters(
                  [_iid], {_ikey: 5}, imported_series=True)))
        check("imported-unique-namespace-refused-{}".format(_ns),
              any("malformed id" in f for f in check_unique_ids([_iid], imported_series=True)))
        check("imported-monotonic-namespace-refused-{}".format(_ns),
              bool(check_monotonic({_ikey: 1}, {_ikey: 1}, imported_series=True)))
        check("imported-counters-namespace-refused-{}".format(_ns), bool(validate_counters(
            dict(schema=1, counters={_ikey: 1}), imported_namespaces=[_ns])[1]))
        check("imported-next-id-namespace-refused-{}".format(_ns), _alloc_refused(
            lambda: next_id({_ikey: 1}, _ikey, imported_series=True)))
    check("imported-next-id-absent-refusal-names-no-proof-escape",
          "whatever known_complete" in str(_alloc_error(
              lambda: next_id(dict(BI=3), "imported:BI", imported_series=True)))
          and "whatever known_complete" in str(_alloc_error(
              lambda: next_id(dict(BI=3), "imported:BI", known_complete=True, imported_series=True))))

    # ----- The imported-record validator (spec 8.3, the imported envelope). Valid imported PD and MD
    # records pass; each refused vector differs from its valid base by the one deviation its rule refuses,
    # and the finding that rule raises is named, so a vector refused for an unrelated reason does not count.
    _IMP_AT = "2026-08-12T09:14:02Z"
    _IMP_RUN = "imp-20260812T091402Z-0123456789abcdef"
    _IMP_SHA = "0123456789abcdef" * 4
    _IMP_ACTOR = {"kind": "importer", "id": "import-assistant"}

    def _imp_table(**over):
        t = {"source": ".working/imported/import/" + _IMP_RUN + "/originals/DECISIONS.md",
             "source_sha256": _IMP_SHA, "run": _IMP_RUN, "imported_at": _IMP_AT}
        t.update(over)
        return t

    def _imp_pd(**over):
        # decided_at survives only as a date, so it is an `unparsed` omission whose verbatim source value
        # rides import.history (spec 8.3: a UTC midnight is never fabricated); summary is an exempt omission.
        r = {"id": "imported:PD-1", "type": "pending_decision", "status": "decided",
             "title": "Keep the legacy ledger read-only", "created_at": "2025-03-01T10:00:00Z",
             "actor": dict(_IMP_ACTOR), "decision": "The legacy ledger stays read-only",
             "decided_by": "maintainer",
             "unrecorded": [{"field": "updated_at", "reason": "not_recorded_in_source"},
                            {"field": "decided_at", "reason": "unparsed"}],
             "import": _imp_table(span=[0, 120], history=[{"field": "decided_at", "value": "2025-03-02"}],
                                  unparsed=["Decided at the March sync; no time recorded."])}
        r.update(over)
        return r

    def _imp_md(**over):
        r = {"id": "imported:MD-4", "type": "maintainer_decision", "status": "recorded",
             "title": "Prefer additive migrations", "created_at": "2025-01-05T08:00:00Z",
             "updated_at": "2025-01-05T08:00:00+00:00", "actor": dict(_IMP_ACTOR),
             "decision": "Additive migrations only", "summary": "Recorded in the old decision log",
             "links": [{"rel": "exemplifies", "id": "imported:PP-2"}],
             "refs": [{"kind": "path", "locator": "DECISIONS.md", "note": "original entry"}],
             "x-aiqt": {"lane": "history"}, "import": _imp_table()}
        r.update(over)
        return r

    # An uncontrolled exception fails the vector rather than aborting the run, so a mutant that turns a
    # structured refusal into a crash reds the named check.
    def _imp_refused(rec, needle, **kw):
        try:
            res = validate_imported_record(rec, **kw)
        except Exception:
            return False
        return res.status == INVALID and any(needle in f for f in res.findings)

    def _imp_ok(rec, **kw):
        try:
            res = validate_imported_record(rec, **kw)
        except Exception:
            return False
        return res.status == VALID and not res.findings

    _pd_rows = _imp_pd()["unrecorded"]
    _pd_res = validate_imported_record(_imp_pd())
    check("imported-valid-pd", _pd_res.status == VALID and not _pd_res.findings
          and _pd_res.id == "imported:PD-1" and _pd_res.type == "pending_decision")
    check("imported-valid-md", _imp_ok(_imp_md(), registered_vendors=REG))
    check("imported-missing-unrecorded-row-refused", _imp_refused(
        _imp_pd(unrecorded=_pd_rows[1:]), "'updated_at' of the pending_decision schema has no unrecorded row"))
    check("imported-absent-unrecorded-refused", _imp_refused(
        {k: v for k, v in _imp_pd().items() if k != "unrecorded"}, "has no unrecorded row"))
    check("imported-exempt-summary-row-refused", _imp_refused(
        _imp_pd(unrecorded=_pd_rows + [{"field": "summary", "reason": "not_recorded_in_source"}]),
        "exempt optional envelope field 'summary'"))
    check("imported-duplicate-row-refused", _imp_refused(
        _imp_pd(unrecorded=_pd_rows + [{"field": "updated_at", "reason": "ambiguous"}]),
        "repeats the field 'updated_at'"))
    check("imported-proposed-status-refused", _imp_refused(_imp_pd(status="decided/proposed"), "takes /proposed"))
    check("imported-maintainer-actor-refused", _imp_refused(
        _imp_pd(actor={"kind": "maintainer", "id": "import-assistant"}), "is not 'importer'"))
    check("imported-uppercase-sha-refused", _imp_refused(
        _imp_pd(**{"import": _imp_table(source_sha256=_IMP_SHA.upper())}), "not 64 lowercase"))
    check("imported-timestamp-after-import-refused", _imp_refused(
        _imp_pd(created_at="2026-08-12T09:14:03Z"), "created_at is later than import.imported_at"))
    check("imported-timestamp-at-import-instant-ok", _imp_ok(_imp_pd(created_at="2026-08-12T09:14:02+00:00")))
    check("imported-unknown-key-refused", _imp_refused(_imp_pd(colour="teal"), "unknown key 'colour'"))
    check("imported-three-key-history-row-refused", _imp_refused(
        _imp_pd(**{"import": _imp_table(history=[{"field": "decided_at", "value": "2025-03-02",
                                                    "note": "date only"}])}),
        "exactly the two keys field and value"))

    # LF: refused under the baseline roster and under a roster that registers a legacy_fragment spec, so
    # the refusal is the rule's own and not an unsupported-type side effect (spec 8.1/8.2).
    _lf_spec = object.__new__(TypeSpec)
    for _slot, _val in (("name", "legacy_fragment"), ("namespace", IMPORTER_TYPES["legacy_fragment"]),
                        ("initial", "quarantined"), ("working", frozenset()),
                        ("terminal", frozenset(("quarantined",))), ("transitions", dict()),
                        ("proposable", frozenset()), ("gated", frozenset()), ("extra_keys", frozenset()),
                        ("reduced", False), ("states", frozenset(("quarantined",)))):
        setattr(_lf_spec, _slot, _val)
    _lf_roster = dict(BASELINE_SPECS, legacy_fragment=_lf_spec)
    _lf_rec = {"id": "imported:LF-1", "type": "legacy_fragment", "status": "quarantined",
               "title": "A legacy fragment", "created_at": TS, "updated_at": TS, "actor": dict(_IMP_ACTOR),
               "import": _imp_table()}
    check("imported-lf-roster-reconciles", _specs_roster_error(_lf_roster) is None)
    check("imported-lf-type-refused-extended-roster", _imp_refused(_lf_rec, "importer-only LF", specs=_lf_roster))
    check("imported-lf-type-refused-baseline", _imp_refused(_lf_rec, "importer-only LF"))
    check("imported-lf-namespace-refused", _imp_refused(_imp_pd(id="imported:LF-1"), "carries no LF"))
    # Each refused namespace is refused for its own reason, on an id and on a link: only LF's names LF.
    for _ns, _needle in (("TX", "reserved for the transaction type"), ("CL", "reserved unassigned"),
                         ("ZZ", "bound to no record type in the section 8.1 taxonomy")):
        _ns_id = "imported:" + _ns + "-1"
        _ns_res = validate_imported_record(_imp_pd(id=_ns_id))
        check("imported-namespace-refusal-reason-" + _ns, _ns_res.status == INVALID
              and any(("id namespace " + repr(_ns)) in f and _needle in f and "LF" not in f
                      for f in _ns_res.findings)
              and _imp_refused(_imp_md(links=[{"rel": "relates", "id": _ns_id}]), _needle,
                               registered_vendors=REG))

    # Every finding names the record; malformed input is a refusing failure, never a silent absence.
    _named = validate_imported_record(_imp_pd(colour="teal", status="decided/proposed"))
    check("imported-findings-name-record", _named.status == INVALID and len(_named.findings) == 2
          and all(f.startswith("imported record 'imported:PD-1': ") for f in _named.findings))
    _noid = validate_imported_record({k: v for k, v in _imp_pd().items() if k != "id"})
    check("imported-missing-id-refused", _noid.status == INVALID
          and any(f.startswith("imported record (no id): missing required field: id") for f in _noid.findings))
    for _bad in (None, [], "imported:PD-1", 7):
        check("imported-non-table-cannot-eval-" + type(_bad).__name__,
              validate_imported_record(_bad).status == CANNOT_EVALUATE)
    _untyped = validate_imported_record({k: v for k, v in _imp_pd().items() if k != "type"})
    check("imported-untyped-cannot-eval", _untyped.status == CANNOT_EVALUATE and _untyped.id == "imported:PD-1"
          and _untyped.findings == ["imported record 'imported:PD-1': the record has no `type` and no "
                                    "expected type: cannot identify it"])
    check("imported-untyped-with-expected-refused", _imp_refused(
        {k: v for k, v in _imp_pd().items() if k != "type"}, "required field: type",
        expected_type="pending_decision"))
    check("imported-expected-type-mismatch-refused", _imp_refused(
        _imp_pd(), "does not match the expected type", expected_type="finding"))
    _et_bad = validate_imported_record(_imp_pd(), expected_type=["pending_decision"])
    check("imported-expected-type-nonstring-cannot-eval", _et_bad.status == CANNOT_EVALUATE
          and _et_bad.id == "imported:PD-1"
          and _et_bad.findings == ["imported record 'imported:PD-1': expected_type must be a string or None "
                                   "(fail-closed)"])
    # A control refusal names the record too: the label and the result's id survive the early return.
    def _imp_ctrl(rec, specs, needle, **kw):
        try:
            res = validate_imported_record(rec, specs=specs, **kw)
        except Exception:
            return False
        return (res.status == CANNOT_EVALUATE and len(res.findings) == 1 and res.id == rec["id"]
                and res.findings[0].startswith("imported record " + repr(rec["id"]) + ": ")
                and needle in res.findings[0])

    check("imported-misbound-roster-cannot-eval", _imp_ctrl(
        _imp_pd(), {"pending_decision": BASELINE_SPECS["finding"]}, "contradicts its TypeSpec name"))
    for _ctl in ([], [1], "pending_decision", 7):
        check("imported-roster-not-mapping-named-" + repr(_ctl),
              _imp_ctrl(_imp_pd(), _ctl, "roster is not a mapping"))
    check("imported-roster-refusal-no-id-label", validate_imported_record(
        {k: v for k, v in _imp_pd().items() if k != "id"}, specs=[]).findings
        == ["imported record (no id): the type-spec roster is not a mapping (fail-closed)"])

    # The roster is validated in full: a malformed member never reaches a membership test, and a baseline
    # entry may not change its grammar (states, transitions, fields, envelope). The two review
    # reproductions: `states = None` (formerly a TypeError) and a bogus state set (formerly VALID).
    import copy as _copy
    _MD = BASELINE_SPECS["maintainer_decision"]

    def _md_roster(**slots):
        sp = _copy.copy(_MD)
        for _slot, _val in slots.items():
            setattr(sp, _slot, _val)
        return dict(maintainer_decision=sp)

    class _Raises:
        def __eq__(self, other):
            raise RuntimeError("a control member ran its own comparison")
        __ne__ = __eq__
        __hash__ = object.__hash__

    check("imported-roster-copy-of-baseline-ok", _imp_ok(_imp_md(), specs=_md_roster(), registered_vendors=REG))
    check("imported-roster-states-none-cannot-eval", _imp_ctrl(
        _imp_md(), _md_roster(states=None), "malformed or missing states", registered_vendors=REG))
    check("imported-roster-bogus-state-cannot-eval", _imp_ctrl(
        _imp_md(status="bogus"), _md_roster(states=frozenset(["bogus"])), "malformed or missing states",
        registered_vendors=REG))
    check("imported-roster-consistent-bogus-grammar-cannot-eval", _imp_ctrl(
        _imp_md(status="bogus"), _md_roster(initial="bogus", terminal=frozenset(["bogus"]),
                                            states=frozenset(["bogus"])),
        "contradicts the baseline maintainer_decision definition's initial", registered_vendors=REG))
    for _slot, _val in (("extra_keys", _MD.extra_keys | frozenset(["colour"])),
                        ("proposable", frozenset(["recorded"])), ("reduced", True),
                        ("transitions", dict(recorded=frozenset(["recorded"])))):
        check("imported-roster-baseline-" + _slot + "-contradiction-cannot-eval", _imp_ctrl(
            _imp_md(colour="teal"), _md_roster(**dict([(_slot, _val)])), "definition's " + _slot,
            registered_vendors=REG))
    for _j, (_slot, _val) in enumerate((
            ("name", _Raises()), ("namespace", None), ("initial", ""), ("working", ["x"]),
            ("terminal", None), ("proposable", None), ("gated", set(["recorded"])),
            ("extra_keys", ("decision",)), ("extra_keys", frozenset([_Raises()])),
            ("states", frozenset(["recorded", "a/b"])), ("transitions", []),
            ("transitions", dict(recorded=set(["recorded"]))), ("transitions", dict([(_Raises(), frozenset())])),
            ("proposable", frozenset(["elsewhere"])), ("reduced", 0), ("reduced", None))):
        check("imported-roster-malformed-{}-{}-cannot-eval".format(_slot, _j), _imp_ctrl(
            _imp_md(), _md_roster(**dict([(_slot, _val)])), "malformed or missing " + _slot,
            registered_vendors=REG))
    # A state carrying the `/` qualifier separator could never parse; refused even in a self-consistent set.
    check("imported-roster-slash-state-cannot-eval", _imp_ctrl(
        _imp_md(), _md_roster(initial="a/b", terminal=frozenset(["a/b"]), states=frozenset(["a/b"])),
        "malformed or missing states", registered_vendors=REG))
    _partial = object.__new__(TypeSpec)
    _partial.name, _partial.namespace = "maintainer_decision", "MD"
    check("imported-roster-missing-slot-cannot-eval", _imp_ctrl(
        _imp_md(), dict(maintainer_decision=_partial), "malformed or missing initial", registered_vendors=REG))

    class _SubSpec(TypeSpec):
        __slots__ = ()

    _sub = object.__new__(_SubSpec)
    for _slot in TypeSpec.__slots__:
        setattr(_sub, _slot, getattr(_MD, _slot))
    check("imported-roster-subclass-cannot-eval", _imp_ctrl(
        _imp_md(), dict(maintainer_decision=_sub), "not exactly a TypeSpec", registered_vendors=REG))
    check("imported-roster-nonstring-key-cannot-eval", _imp_ctrl(
        _imp_md(), dict([(1, _MD)]), "non-string key", registered_vendors=REG))

    class _LyingRoster(dict):
        def get(self, key, default=None):
            return BASELINE_SPECS["finding"]

    check("imported-roster-checked-snapshot-is-used",
          _imp_ok(_imp_pd(), specs=_LyingRoster(pending_decision=BASELINE_SPECS["pending_decision"])))

    # Every control is judged by its real type: isinstance consults an advertised __class__, so an object
    # that only claims a built-in class must be refused, never reach that class's own methods and raise.
    # The first vector is the review reproduction (formerly a TypeError at dict.items).
    def _pretend(cls):
        class _Pretend:
            @property
            def __class__(self):
                return cls
        return _Pretend()

    def _imp_try(rec, **kw):
        try:
            return validate_imported_record(rec, **kw)
        except Exception:
            return None

    # A record that is not plain is one labelled CANNOT-EVALUATE finding, refused before any read.
    def _unplain(res, prefix, rid):
        return (res is not None and res.status == CANNOT_EVALUATE and res.id == rid and len(res.findings) == 1
                and res.findings[0].startswith(prefix)
                and res.findings[0].endswith("(a parsed record holds none; fail-closed)"))

    class _LyingStr(str):
        def __hash__(self):
            raise RuntimeError("a control member ran its own hash")

        def __eq__(self, other):
            raise RuntimeError("a control member ran its own comparison")
        __ne__ = __eq__

    class _IterRaisesList(list):
        def __iter__(self):
            raise RuntimeError("a control ran its own __iter__")

    class _IterRaisesSet(frozenset):
        def __iter__(self):
            raise RuntimeError("a control ran its own __iter__")

    # Equal-valued subclasses of the built-in types: each meets a condition only isinstance would accept.
    class _Str(str):
        __slots__ = ()

    class _FSet(frozenset):
        __slots__ = ()

    class _Dict(dict):
        __slots__ = ()

    class _ItemsRaises(dict):
        def items(self):
            raise RuntimeError("a roster ran its own items()")

    check("imported-roster-dict-subclass-read-through-dict-ok",
          _imp_ok(_imp_pd(), specs=_ItemsRaises(pending_decision=BASELINE_SPECS["pending_decision"])))
    check("imported-roster-str-subclass-key-cannot-eval", _imp_ctrl(
        _imp_md(), {_Str("maintainer_decision"): _MD}, "non-string key", registered_vendors=REG))

    class _ReprRaisesStr(str):
        def __repr__(self):
            raise RuntimeError("a roster key ran its own __repr__")

    # Refused before any message renders it, so a malformed entry under such a key cannot raise either.
    check("imported-roster-str-subclass-key-malformed-entry-cannot-eval", _imp_ctrl(
        _imp_md(), {_ReprRaisesStr("maintainer_decision"): _md_roster(states=None)["maintainer_decision"]},
        "non-string key", registered_vendors=REG))
    for _ctl in (["x-aiqt"], ("x-aiqt",), {"x-aiqt"}, frozenset(["x-aiqt"])):
        check("imported-vendors-container-ok-" + type(_ctl).__name__, _imp_ok(_imp_md(), registered_vendors=_ctl))
    check("imported-roster-pretend-dict-cannot-eval", isinstance(_pretend(dict), dict) and _imp_ctrl(
        _imp_md(), _pretend(dict), "roster is not a mapping", registered_vendors=REG))
    check("imported-roster-pretend-typespec-cannot-eval", isinstance(_pretend(TypeSpec), TypeSpec) and _imp_ctrl(
        _imp_md(), dict(maintainer_decision=_pretend(TypeSpec)), "not exactly a TypeSpec", registered_vendors=REG))
    for _j, _et in enumerate((_pretend(str), _LyingStr("pending_decision"))):
        check("imported-expected-type-faked-cannot-eval-" + str(_j), _imp_ctrl(
            _imp_pd(), None, "expected_type must be a string or None", expected_type=_et))
    _pretend_rec = _imp_try(_pretend(dict))
    check("imported-pretend-dict-record-cannot-eval", _pretend_rec is not None
          and _pretend_rec.status == CANNOT_EVALUATE
          and _pretend_rec.findings == ["imported record is not a table (fail-closed)"])
    for _j, _ctl in enumerate((_pretend(list), _pretend(frozenset), [_LyingStr("x-aiqt")])):
        check("imported-vendors-faked-refused-" + str(_j), _imp_refused(
            _imp_md(), "registered_vendors must be", registered_vendors=_ctl))
    # A genuine built-in subclass is read through its base's own iterator, never its __iter__ override.
    for _j, _ctl in enumerate((_IterRaisesList(["x-aiqt"]), _IterRaisesSet(["x-aiqt"]))):
        check("imported-vendors-subclass-base-iterated-ok-" + str(_j), _imp_ok(_imp_md(), registered_vendors=_ctl))

    # The checked roster cannot be widened after the check: only fresh copies of the checked entries are
    # read. The review reproduction, a vendors control whose own __iter__ rebinds the caller's spec,
    # never runs that code; a record value whose own __hash__ would rebind the caller's spec (its `type`
    # is hashed at the roster lookup) never runs either, since a record that is not plain is refused
    # before any read; and the copy owns its own transitions dict, so a later write to the caller's dict
    # changes nothing either.
    _mut_md = _copy.copy(_MD)

    class _Mutator(list):
        def __iter__(self):
            _mut_md.states = frozenset(["recorded", "bogus"])
            return list.__iter__(self)

    _mut_res = _imp_try(_imp_md(status="bogus"), specs=dict(maintainer_decision=_mut_md),
                        registered_vendors=_Mutator(["x-aiqt"]))
    check("imported-roster-not-widened-by-control-code", _mut_res is not None and _mut_res.status == INVALID
          and any("status state 'bogus'" in f for f in _mut_res.findings))
    _hook_md = _copy.copy(_MD)

    class _HookType(str):
        def __hash__(self):
            _hook_md.states = frozenset(["recorded", "bogus"])
            return str.__hash__(self)

    _hook_res = _imp_try(_imp_md(status="bogus", type=_HookType("maintainer_decision")),
                         specs=dict(maintainer_decision=_hook_md), registered_vendors=REG)
    check("imported-roster-not-widened-by-record-code", _hook_md.states == _MD.states and _unplain(
        _hook_res, "imported record 'imported:MD-4': field 'type' holds", "imported:MD-4"))
    _cp_md = _copy.copy(_MD)
    _cp_md.transitions = {}
    _cp_err, _cp_roster = _imported_specs_control(dict(maintainer_decision=_cp_md))
    _cp = (_cp_roster or {}).get("maintainer_decision")
    check("imported-roster-entries-are-fresh-copies", _cp_err is None and type(_cp) is TypeSpec
          and _cp is not _cp_md and _cp.transitions is not _cp_md.transitions
          and all(getattr(_cp, _slot) == getattr(_MD, _slot) for _slot in TypeSpec.__slots__))

    # A module-tier entry (no authoritative definition here) is held to its shape and may not take the
    # reduced envelope or declare a field the imported envelope reserves; a well-formed one validates.
    def _module_roster(**slots):
        sp = object.__new__(TypeSpec)
        base = dict(name="artifact", namespace=MODULE_TYPES["artifact"][0], initial="recorded",
                    working=frozenset(), terminal=frozenset(["recorded"]), transitions=dict(),
                    proposable=frozenset(), gated=frozenset(), extra_keys=frozenset(["digest"]),
                    reduced=False, states=frozenset(["recorded"]))
        base.update(slots)
        for _slot, _val in base.items():
            setattr(sp, _slot, _val)
        return dict(BASELINE_SPECS, artifact=sp)

    _ar = dict(id="imported:AR-1", type="artifact", status="recorded", title="A legacy artifact",
               created_at=TS, updated_at=TS, actor=dict(_IMP_ACTOR), digest="abc")
    _ar["import"] = _imp_table()
    check("imported-module-spec-ok", _imp_ok(_ar, specs=_module_roster()))
    check("imported-module-spec-reduced-cannot-eval", _imp_ctrl(
        _ar, _module_roster(reduced=True), "reduced envelope"))
    for _field in ("x-aiqt", "import", "unrecorded"):
        check("imported-module-spec-reserved-field-cannot-eval-" + _field, _imp_ctrl(
            _ar, _module_roster(extra_keys=frozenset(["digest", _field])), "declares the field " + repr(_field)))
    # Every shape condition on a module entry, which has no authoritative definition, so the baseline
    # equality rule cannot mask a removed condition: a staged > review > promoted|rejected grammar
    # validates, and each deviation from it, a type condition met by an equal-valued subclass, is a
    # labelled refusal naming its slot.
    _ART = dict(name="artifact", namespace=MODULE_TYPES["artifact"][0], initial="staged",
                working=frozenset(["review"]), terminal=frozenset(["promoted", "rejected"]),
                transitions={"staged": frozenset(["review", "rejected"]),
                             "review": frozenset(["promoted", "rejected"])},
                proposable=frozenset(["review", "promoted", "rejected"]), gated=frozenset(["review"]),
                extra_keys=frozenset(["digest"]), reduced=False,
                states=frozenset(["staged", "review", "promoted", "rejected"]))
    _ar_rich = dict(_ar, status="promoted")
    check("imported-module-rich-spec-ok", _imp_ok(_ar_rich, specs=_module_roster(**_ART)))

    class _LeRaises(frozenset):
        def __le__(self, other):
            raise RuntimeError("a roster member ran its own __le__")

    _art_bad = [("name", dict(name=_Str("artifact"))), ("namespace", dict(namespace=_Str(_ART["namespace"]))),
                ("initial", dict(initial=_Str("staged")))]
    _art_bad += [(_slot, {_slot: _FSet(_ART[_slot])})
                 for _slot in ("working", "terminal", "proposable", "gated", "extra_keys", "states")]
    _art_bad += [
        ("working", dict(working=frozenset([_Str("review")]))),
        ("working", dict(working=frozenset(["review", ""]), states=_ART["states"] | {""})),
        ("transitions", dict(transitions=_Dict(_ART["transitions"]))),
        ("transitions", dict(transitions=dict(_ART["transitions"], bogus=frozenset()))),
        ("transitions", dict(transitions={_Str("staged"): _ART["transitions"]["staged"],
                                          "review": _ART["transitions"]["review"]})),
        ("transitions", dict(transitions=dict(_ART["transitions"], staged=set(["review"])))),
        ("transitions", dict(transitions=dict(_ART["transitions"], staged=frozenset(["review", "bogus"])))),
        ("proposable", dict(proposable=_ART["proposable"] | {"elsewhere"})),
        ("gated", dict(gated=frozenset(["staged"]))),
        ("gated", dict(gated=frozenset(["promoted"]))),
        ("reduced", dict(reduced=0)),
        # A transitions destination is an exact frozenset of exact strs, like every other set slot: an
        # equal-valued frozenset subclass, one whose own __le__ raises, and a str-subclass member.
        ("transitions", dict(transitions=dict(_ART["transitions"], staged=_FSet(["review", "rejected"])))),
        ("transitions", dict(transitions=dict(_ART["transitions"], staged=_LeRaises(["review", "rejected"])))),
        ("transitions", dict(transitions=dict(_ART["transitions"],
                                              staged=frozenset([_Str("review"), "rejected"]))))]
    for _j, (_slot, _over) in enumerate(_art_bad):
        check("imported-module-spec-malformed-{}-{}-cannot-eval".format(_slot, _j), _imp_ctrl(
            _ar_rich, _module_roster(**dict(_ART, **_over)), "malformed or missing " + _slot))

    # A baseline entry differing from its definition in one slot alone is refused for that slot, also
    # where the state set is unchanged (a state moved between working and terminal) or the slot is empty.
    def _base_roster(name, **slots):
        sp = _copy.copy(BASELINE_SPECS[name])
        for _slot, _val in slots.items():
            setattr(sp, _slot, _val)
        return {name: sp}

    _bi = {"id": "imported:BI-1", "type": "backlog_item", "status": "done", "title": "Retire the old ledger",
           "created_at": TS, "updated_at": TS, "actor": dict(_IMP_ACTOR), "import": _imp_table()}
    _BI = BASELINE_SPECS["backlog_item"]
    check("imported-roster-backlog-copy-ok", _imp_ok(_bi, specs=_base_roster("backlog_item")))
    for _name, _rec, _slot, _val in (
            ("backlog_item", _bi, "working", _BI.working | {"done"}),
            ("backlog_item", _bi, "terminal", _BI.terminal | {"active"})):
        check("imported-roster-baseline-" + _name + "-" + _slot + "-only-cannot-eval", _imp_ctrl(
            _rec, _base_roster(_name, **dict([(_slot, _val)])), "definition's " + _slot))
    check("imported-unsupported-type-refused", _imp_refused(_imp_pd(type="artifact"), "not a supported"))
    check("imported-bad-vendors-control-refused", _imp_refused(_imp_pd(), "registered_vendors must be",
                                                               registered_vendors="x-aiqt"))

    # The envelope: imported-series id bound to the type, no proposed_from, importer actor with an id.
    check("imported-clean-id-refused", _imp_refused(_imp_pd(id="PD-1"), "not an imported-series"))
    check("imported-id-namespace-mismatch-refused", _imp_refused(_imp_pd(id="imported:BI-1"),
                                                                 "not the 'PD' namespace"))
    check("imported-proposed-from-refused", _imp_refused(_imp_pd(proposed_from="open"), "never carries proposed_from"))
    check("imported-illegal-state-refused", _imp_refused(_imp_pd(status="active"), "status: status state"))
    check("imported-missing-title-refused", _imp_refused(
        {k: v for k, v in _imp_pd().items() if k != "title"}, "missing required field: title"))
    check("imported-multiline-title-refused", _imp_refused(_imp_pd(title="one\ntwo"), "single line"))
    check("imported-missing-actor-id-refused", _imp_refused(_imp_pd(actor={"kind": "importer"}),
                                                            "actor.id, naming the importing assistant"))
    check("imported-blank-actor-id-refused", _imp_refused(_imp_pd(actor={"kind": "importer", "id": " "}),
                                                          "actor.id must be a non-empty string"))

    # The import provenance table (spec 8.3): closed, its four members required, each in its grammar.
    check("imported-missing-import-refused", _imp_refused(
        {k: v for k, v in _imp_pd().items() if k != "import"}, "missing required field: import"))
    check("imported-import-not-table-refused", _imp_refused(_imp_pd(**{"import": ["x"]}), "import must be a table"))
    check("imported-import-unknown-key-refused", _imp_refused(
        _imp_pd(**{"import": _imp_table(digest="x")}), "import unknown key 'digest'"))
    for _k in ("source", "source_sha256", "run", "imported_at"):
        check("imported-import-missing-" + _k, _imp_refused(
            _imp_pd(**{"import": {k: v for k, v in _imp_table().items() if k != _k}}),
            "missing required field: import." + _k))
    for _src in ("DECISIONS.md", "/.working/x.md", ".working/", ".working", ".working/a/../b.md",
                 ".working/./b.md", ".working//b.md", ".working\\b.md", ".working/b\n.md", "working/b.md", 7):
        check("imported-source-refused-" + repr(_src), _imp_refused(
            _imp_pd(**{"import": _imp_table(source=_src)}), "import.source"))
    # The preserved original lies under its own run's originals (spec 8.3, 8.8): a canonical path anywhere
    # else under .working/, or under another run, is refused for that reason; a nested original under the
    # record's own run, and an original under another run that the record itself names, validate.
    _own = ".working/imported/import/" + _IMP_RUN
    _OTHER_RUN = "imp-20260812T091402Z-fedcba9876543210"
    for _src in (".working/imported/adoption/legacy/NOTES.md", ".working/DECISIONS.md",
                 ".working/imported/import/" + _OTHER_RUN + "/originals/DECISIONS.md",
                 ".working/imported/ingest/" + _IMP_RUN + "/originals/DECISIONS.md",
                 _own + "0/originals/DECISIONS.md", _own + "/DECISIONS.md", _own + "/inventory.toml",
                 _own + "/originalsX/DECISIONS.md"):
        check("imported-source-unbound-refused-" + _src, _imp_refused(
            _imp_pd(**{"import": _imp_table(source=_src)}), "the preserved originals of the record's own import.run"))
    check("imported-source-nested-original-ok", _imp_ok(_imp_pd(**{"import": _imp_table(
        source=_own + "/originals/docs/2024/DECISIONS.md")})))
    check("imported-source-own-other-run-ok", _imp_ok(_imp_pd(**{"import": _imp_table(
        run=_OTHER_RUN, source=".working/imported/import/" + _OTHER_RUN + "/originals/DECISIONS.md")})))
    # The binding is exact: anchored at the start, case-sensitive, and through the `/` after originals.
    # The record's own prefix later in a path (also inside another run's originals), a run directory that
    # merely contains the record's run, a case-changed spelling, and the prefix without its separator are
    # each refused for that reason; a nested original that names another run's prefix inside the record's
    # own originals validates.
    for _src in (".working/x/" + _own + "/originals/DECISIONS.md",
                 ".working/imported/import/" + _OTHER_RUN + "/originals/" + _own + "/originals/DECISIONS.md",
                 ".working/imported/import/" + _OTHER_RUN + "/originals/" + _IMP_RUN + "/originals/DECISIONS.md",
                 ".working/imported/import/x" + _IMP_RUN + "/originals/DECISIONS.md",
                 ".working/IMPORTED/import/" + _IMP_RUN + "/originals/DECISIONS.md",
                 ".working/imported/IMPORT/" + _IMP_RUN + "/originals/DECISIONS.md",
                 ".working/imported/import/" + _IMP_RUN.upper() + "/originals/DECISIONS.md",
                 _own + "/ORIGINALS/DECISIONS.md", _own + "/Originals/DECISIONS.md",
                 _own + "/originals", _own + "/originalsDECISIONS.md"):
        check("imported-source-inexact-binding-refused-" + _src, _imp_refused(
            _imp_pd(**dict([("import", _imp_table(source=_src))])),
            "the preserved originals of the record's own import.run"))
    check("imported-source-nested-foreign-prefix-ok", _imp_ok(_imp_pd(**dict([("import", _imp_table(
        source=_own + "/originals/.working/imported/import/" + _OTHER_RUN + "/originals/DECISIONS.md"))]))))

    # The review reproductions on the binding's operands: a run whose own __format__ names another run
    # (formerly VALID) or raises (formerly a raw RuntimeError), and a source whose own startswith forges
    # the match (formerly VALID). A record holding one is not plain and is refused before any read; the
    # import-table rule itself, handed one directly, refuses it by its exact type, never rendering it,
    # running its code or binding it.
    class _RunRedirect(str):
        def __format__(self, spec):
            return _OTHER_RUN

    class _RunRaises(str):
        def __format__(self, spec):
            raise RuntimeError("a record value ran its own __format__")

    class _SourceRedirect(str):
        def startswith(self, prefix, *args):
            return prefix != "/"

    _under_other = ".working/imported/import/" + _OTHER_RUN + "/originals/DECISIONS.md"
    for _name, _op, _over in (
            ("run-redirect", "import.run", dict(run=_RunRedirect(_IMP_RUN), source=_under_other)),
            ("run-raises", "import.run", dict(run=_RunRaises(_IMP_RUN))),
            ("source-redirect", "import.source", dict(source=_SourceRedirect(".working/outside.md")))):
        check("imported-source-binding-" + _name + "-cannot-eval", _unplain(
            _imp_try(_imp_md(**dict([("import", _imp_table(**_over))])), registered_vendors=REG),
            "imported record 'imported:MD-4': field 'import' holds", "imported:MD-4"))
        _found = []
        try:
            _validate_import_table(dict([("import", _imp_table(**_over))]), _imported_schema_fields(_MD), _found)
        except Exception:
            _found = None
        check("imported-import-table-" + _name + "-refused", _found is not None
              and any(f.startswith(_op + " <not a plain built-in value> is not ") for f in _found)
              and not any("does not lie under" in f for f in _found))
    _found = []
    _validate_import_table(dict([("import", _Dict(_imp_table()))]), _imported_schema_fields(_MD), _found)
    check("imported-import-table-dict-subclass-refused", any(f.startswith("import must be a table") for f in _found))

    # A hostile record, as opposed to a hostile control. Each review reproduction (a dict subclass whose
    # own get raises, or reports a status it does not hold; a `type` whose own __hash__ raises; an `id`
    # whose own __repr__ raises) is CANNOT-EVALUATE before any read, and so is an equal-valued subclass
    # at the top, nested in a value or as a key, and a date-time with a foreign tzinfo. The label shows
    # the id only when the id is plain.
    class _GetRaises(dict):
        def get(self, *args):
            raise RuntimeError("a record ran its own get")

    class _GetLies(dict):
        def get(self, key, default=None):
            return "recorded" if key == "status" else dict.get(self, key, default)

    class _HashRaisesStr(str):
        def __hash__(self):
            raise RuntimeError("a record value ran its own hash")

    class _Zone(datetime.tzinfo):
        def utcoffset(self, dt):
            return datetime.timedelta(0)

    _md4 = "imported record 'imported:MD-4': "
    _key_rec = _imp_md()
    _key_rec[_Str("summary")] = _key_rec.pop("summary")
    _ref = dict(kind="path", locator=_Str("DECISIONS.md"), note="original entry")
    for _name, _rec, _prefix, _rid in (
            ("get-raises", _GetRaises(_imp_md()), _md4 + "the record is a subclass", "imported:MD-4"),
            ("get-lies", _GetLies(_imp_md(status="bogus")), _md4 + "the record is a subclass", "imported:MD-4"),
            ("equal-valued-record", _Dict(_imp_md()), _md4 + "the record is a subclass", "imported:MD-4"),
            ("type-hash-raises", _imp_md(type=_HashRaisesStr("maintainer_decision")), _md4 + "field 'type' holds",
             "imported:MD-4"),
            ("id-repr-raises", _imp_md(id=_ReprRaisesStr("imported:MD-4")),
             "imported record (unreadable id): field 'id' holds", None),
            ("equal-valued-title", _imp_md(title=_Str("Prefer additive migrations")), _md4 + "field 'title' holds",
             "imported:MD-4"),
            ("nested-value", _imp_md(refs=[_ref]), _md4 + "field 'refs' holds", "imported:MD-4"),
            ("key", _key_rec, _md4 + "field <not a plain built-in value> holds", "imported:MD-4"),
            ("foreign-tzinfo", _imp_md(created_at=datetime.datetime(2025, 1, 5, tzinfo=_Zone())),
             _md4 + "field 'created_at' holds", "imported:MD-4"),
            ("no-id", dict((k, v) for k, v in _imp_md(title=_Str("x")).items() if k != "id"),
             "imported record (no id): field 'title' holds", None)):
        check("imported-record-not-plain-" + _name + "-cannot-eval", _unplain(
            _imp_try(_rec, registered_vendors=REG), _prefix, _rid))
    # A non-empty container reached twice, a cycle included, is not plain: no parsed record shares one, and
    # a walk over a shared one could repeat without bound. The walk has no depth bound, so with its visited
    # set removed it would never end on a cycle: each vector runs in a bounded child process
    # (_opf_emit.run_bounded) and returns a short token from there, and the shared values, which are
    # finite, reach their field rules, so each named check fails rather than hanging the run. run_bounded
    # arms its timer, resets and unblocks SIGALRM, and sets its memory limit in the child alone. The
    # caller-timer vector at the end checks that the self-test leaves a caller's timer, SIGALRM handler and
    # signal mask as they were only within the coverage boundary stated there: a borrow its spies do not
    # see passes it exactly when the run reaches the clauses stated there within that vector's run_bounded
    # deadline and the other six all hold, the caller's handler never having run among them.
    import _opf_emit

    def _in_child(thunk):
        # "OK" when the vector holds in the child, else "MISMATCH" or a run_bounded sentinel (TIMEOUT, OOM,
        # CHILD-DIED, ERROR:<type>, SETUP-ERROR:...), none of which is "OK".
        return _opf_emit.run_bounded(lambda: "OK" if thunk() else "MISMATCH", timeout_s=30)

    _twice = "holds a non-empty container reached twice (shared or cyclic)"
    _cyc = []
    _cyc.append(_cyc)
    _cyc_dict = {}
    _cyc_dict["self"] = _cyc_dict
    _shared = ["Decided at the March sync"]
    _dag = ["leaf"]
    for _ in range(12):
        _dag = [_dag, _dag]
    for _name, _rec, _field in (
            ("cyclic-list", _imp_md(**dict([("import", _imp_table(unparsed=_cyc))])), "'import'"),
            ("cyclic-vendor-dict", _imp_md(**{"x-aiqt": _cyc_dict}), "'x-aiqt'"),
            ("shared-in-field", _imp_md(**dict([("import", _imp_table(unparsed=[_shared, _shared]))])), "'import'"),
            ("shared-doubling", _imp_md(**{"x-aiqt": {"lane": _dag}}), "'x-aiqt'"),
            ("shared-across-fields", _imp_md(**dict([("import", _imp_table(unparsed=_shared)),
                                                    ("x-aiqt", {"lane": _shared})])), None)):
        def _shared_refused():
            res = _imp_try(_rec, registered_vendors=REG)
            return (_unplain(res, _md4 + "field ", "imported:MD-4") and _twice in res.findings[0]
                    and (_field is None or res.findings[0].startswith(_md4 + "field " + _field + " " + _twice)))

        check("imported-record-" + _name + "-cannot-eval", _in_child(_shared_refused) == "OK")
    _self_rec = _imp_md()
    _self_rec["self"] = _self_rec
    check("imported-record-contains-itself-cannot-eval", _in_child(lambda: _unplain(
        _imp_try(_self_rec, registered_vendors=REG), _md4 + "field 'self' " + _twice, "imported:MD-4")) == "OK")
    # An empty container may be shared (CPython shares the empty tuple itself); it reaches its field rule.
    _empty = []
    check("imported-record-shared-empty-container-ok", _imp_ok(_imp_md(**{"x-aiqt": {"a": _empty, "b": _empty}}),
                                                               registered_vendors=REG))

    # Depth alone is never refused: tomllib nests tables through a dotted key or a table header, so a deep
    # value is plain and reaches its field rules (VALID where it is TOML and no rule reads it). Every
    # finding renders a record value through _bounded_display, which is iterative and cut at
    # _DISPLAY_BUDGET characters, so no vector raises (a RecursionError included) and no rendering in a
    # finding outgrows the budget: on the normal label, the refusal label, a key, and the import table.
    # Some CPython patch levels refuse a TOML key of more than 1000 dotted parts (a RecursionError from
    # tomllib), so a table nest deeper than that is built directly in the shape tomllib gives a table
    # header, and every parsed vector keeps each key under 1000 parts; the parsed id takes wide parts, so
    # its rendering still outgrows the budget.
    import tomllib

    def _nest(levels, inner=None):
        v = [] if inner is None else inner
        for _ in range(levels - 1):
            v = [v]
        return v

    def _nest_table(levels, part="a"):
        # What tomllib gives for a table header of that many parts, each named part, holding v = 1.
        v = {"v": 1}
        for _ in range(levels):
            v = {part: v}
        return v

    _cut_len = _DISPLAY_BUDGET + len(_DISPLAY_CUT)

    def _bounded_findings(res, rendered):
        # Each finding renders at most `rendered` record values, each cut at the budget, beside fixed text.
        return res is not None and all(len(f) <= rendered * _cut_len + 1000 for f in res.findings)

    _very_deep = _nest(100000)
    _deep_list = "[" * _DISPLAY_BUDGET + _DISPLAY_CUT
    _deep_label = "imported record " + _deep_list + ": "
    _deep_key = ()
    for _ in range(100000):
        _deep_key = (_deep_key,)
    _deep_tuple = "(" * _DISPLAY_BUDGET + _DISPLAY_CUT
    _deep_key_rec = _imp_md()
    _deep_key_rec[_deep_key] = "x"
    _deep_key_unplain = _imp_md()
    _deep_key_unplain[_deep_key] = _Str("x")
    _path600 = ".".join(["a"] * 600)
    _deep_doc_id = _nest_table(100000)
    # 999 parts: id and 998 wide ones, whose openings alone take more than the budget.
    _wide = "a" * 16
    _wide_open = "{'" + _wide + "': "
    _parsed_deep_id = tomllib.loads("[id." + ".".join([_wide] * 998) + "]\nv = 1")["id"]
    check("imported-record-deep-table-is-tomllib-shape", _parsed_deep_id == _nest_table(998, _wide)
          and 998 * len(_wide_open) > _DISPLAY_BUDGET)
    for _name, _rec, _needle in (
            ("id", _imp_md(id=_very_deep), _deep_label + "id " + _deep_list + " is not an imported-series"),
            ("table-id", _imp_md(id=_deep_doc_id), "imported record {'a': {'a': "),
            ("parsed-id", _imp_md(id=_parsed_deep_id), "imported record " + _wide_open + _wide_open),
            ("source", _imp_md(**dict([("import", _imp_table(source=_very_deep))])),
             _md4 + "import.source " + _deep_list + " is not a canonical"),
            ("key", _deep_key_rec, _md4 + "unknown key " + _deep_tuple + " (schemas are closed")):
        _res = _imp_try(_rec, registered_vendors=REG)
        check("imported-record-deep-" + _name + "-invalid-bounded", _res is not None and _res.status == INVALID
              and any(f.startswith(_needle) for f in _res.findings) and _bounded_findings(_res, 2))
    for _name, _rec, _prefix, _rid in (
            ("id-refusal-label", _imp_md(id=_very_deep, title=_Str("x")), _deep_label + "field 'title' holds",
             _very_deep),
            ("key", _deep_key_unplain, _md4 + "field " + _deep_tuple + " holds a key or value that is not",
             "imported:MD-4")):
        _res = _imp_try(_rec, registered_vendors=REG)
        check("imported-record-deep-" + _name + "-cannot-eval-bounded", _unplain(_res, _prefix, _rid)
              and _bounded_findings(_res, 2))
    # Deep TOML values are VALID: a constructed nest, and tomllib's own dotted assignments, table headers
    # and arrays, alone and combined, in a registered vendor table and in a whole parsed record.
    for _name, _val in (
            ("list-100000", {"lane": _very_deep}),
            ("dotted-600", tomllib.loads("lane." + _path600 + " = 1")),
            ("header-600", tomllib.loads("[lane." + _path600 + "]\nv = 1")),
            ("table-100000", {"lane": _nest_table(100000)}),
            ("header-999", tomllib.loads("[lane." + ".".join(["a"] * 998) + "]\nv = 1")),
            ("header-and-array", tomllib.loads("[lane." + ".".join(["a"] * 300) + "]\nv = " + "[" * 300
                                               + "]" * 300)),
            ("arrays-400", tomllib.loads("lane = " + "[" * 400 + "]" * 400))):
        check("imported-record-deep-" + _name + "-ok", _imp_ok(_imp_md(**{"x-aiqt": _val}), registered_vendors=REG))
    _toml_md = "\n".join([
        'id = "imported:MD-4"', 'type = "maintainer_decision"', 'status = "recorded"',
        'title = "Prefer additive migrations"', 'created_at = "2025-01-05T08:00:00Z"',
        'updated_at = "2025-01-05T08:00:00Z"', 'decision = "Additive migrations only"',
        'summary = "Recorded in the old decision log"', 'links = [{rel = "exemplifies", id = "imported:PP-2"}]',
        'refs = [{kind = "path", locator = "DECISIONS.md", note = "original entry"}]',
        'actor = {kind = "importer", id = "import-assistant"}', "x-aiqt.dotted." + _path600 + " = 1",
        "[import]", 'source = "' + _imp_table()["source"] + '"', 'source_sha256 = "' + _IMP_SHA + '"',
        'run = "' + _IMP_RUN + '"', 'imported_at = "' + _IMP_AT + '"', "[x-aiqt.header." + _path600 + "]",
        "v = 1", ""])
    check("imported-record-deep-parsed-document-ok", _imp_ok(tomllib.loads(_toml_md), registered_vendors=REG))
    _found = []
    try:
        _validate_import_table(dict([("import", _imp_table(source=_very_deep, run=_very_deep))]),
                               _imported_schema_fields(_MD), _found)
    except Exception:
        _found = None
    check("imported-import-table-deep-operands-rendered-bounded", _found is not None
          and any(f.startswith("import.source " + _deep_list + " is not ") for f in _found)
          and any(f.startswith("import.run " + _deep_list + " is not ") for f in _found))

    # _bounded_display is _safe_display, byte for byte, for a plain value whose repr exists and fits the
    # budget, and for the oversized ints below, alone or reached before the cut (each renders as an
    # oversized marker under the default int-to-str limit); a value whose repr exists and does not fit is
    # cut at exactly the budget. Two exceptions, each pinned below: a date-time whose timezone name is
    # longer than the budget, alone or reached before the cut, renders as a fixed text in its place, and
    # with the int-to-str limit lifted an int of more than _DISPLAY_INT_BITS bits renders as an oversized
    # marker.
    _cet = datetime.timezone(datetime.timedelta(hours=1), "CET")
    _render_same = (
        "plain", "it's", 'say "hi"', "a\nb\u2028\U000e0001", "", 0, -7, 1 << 64, 1.5, float("nan"), float("-inf"),
        True, None, datetime.date(2025, 1, 5), datetime.datetime(2025, 1, 5, 8, tzinfo=datetime.timezone.utc),
        datetime.datetime(2025, 1, 5, 8, tzinfo=datetime.timezone(datetime.timedelta(hours=-7, minutes=-30))),
        datetime.datetime(2025, 1, 5, 8, 0, 0, 7, tzinfo=_cet), datetime.time(8, 0, 0, 500000, fold=1),
        [], {}, (), set(), frozenset(), ("a",), ("a", 1), [1, [2, ["x"]]], {"k": {"n": [1, {"o": None}]}, 1: ("t",)},
        {frozenset({1}): set(["a"])}, frozenset({("a", 1)}), [[], {}, (), set(), frozenset()],
        1 << 20000, [1 << 20000], {"k": [1, (1 << 20000,)]}, "x" * (_DISPLAY_BUDGET - 2),
        ["x" * (_DISPLAY_BUDGET - 4)])
    check("imported-bounded-display-is-safe-display", all(_bounded_display(v) == _safe_display(v)
                                                          for v in _render_same))
    for _name, _val, _want in (
            ("str-one-over", "x" * (_DISPLAY_BUDGET - 1), repr("x" * (_DISPLAY_BUDGET - 1))[:_DISPLAY_BUDGET]),
            ("list-one-over", ["x" * (_DISPLAY_BUDGET - 3)], repr(["x" * (_DISPLAY_BUDGET - 3)])[:_DISPLAY_BUDGET]),
            ("deep-list", _very_deep, "[" * _DISPLAY_BUDGET),
            ("deep-tuple", _deep_key, "(" * _DISPLAY_BUDGET),
            ("table", _deep_doc_id, ("{'a': " * (_DISPLAY_BUDGET // 6 + 1))[:_DISPLAY_BUDGET]),
            ("parsed-header", _parsed_deep_id,
             (_wide_open * (_DISPLAY_BUDGET // len(_wide_open) + 1))[:_DISPLAY_BUDGET])):
        check("imported-bounded-display-cut-" + _name, _bounded_display(_val) == _want + _DISPLAY_CUT)
    # A str cut short keeps the quote its whole repr takes, chosen from the whole str, so the cut rendering
    # is the repr's prefix also when the quote that decides it lies past the cut. An oversized int past the
    # cut is never read, so its container renders as the cut prefix where _safe_display gives the marker;
    # one before the cut makes the container the marker, as _safe_display does.
    for _name, _val in (
            ("both-quotes", "'" + "a" * 9000 + '"'), ("single-quote-past-cut", "a" * 9000 + "'"),
            ("single-quote-before-cut", "'" + "a" * 9000), ("nested-both-quotes", ["'" + "a" * 9000 + '"'])):
        check("imported-bounded-display-cut-quote-" + _name,
              _bounded_display(_val) == repr(_val)[:_DISPLAY_BUDGET] + _DISPLAY_CUT)
    _old_digits = sys.get_int_max_str_digits()
    try:
        sys.set_int_max_str_digits(4300)
        _past_cut = ["x" * _DISPLAY_BUDGET, 1 << 20000]
        _past = (_bounded_display(_past_cut), _safe_display(_past_cut), _bounded_display([1 << 20000, "x"]))
    finally:
        sys.set_int_max_str_digits(_old_digits)
    check("imported-bounded-display-oversized-int-past-cut",
          _past == (repr(_past_cut[:1])[:_DISPLAY_BUDGET] + _DISPLAY_CUT, "<oversized-value>", "<oversized-value>"))
    # A value that repeats one string renders far beyond its own size under a bare repr; here every
    # rendering, whatever the width, the sharing or a cycle, stops at the budget, and so does a record's.
    _wide = ["a" * 4096] * 1024
    _dag = ["leaf"]
    for _ in range(60):
        _dag = [_dag, _dag]
    _cyc_render = []
    _cyc_render.append(_cyc_render)
    for _name, _val, _want in (
            ("wide-shared-str", _wide, repr(_wide[:3])[:_DISPLAY_BUDGET]),
            ("huge-str", "x" * 1000000, "'" + "x" * (_DISPLAY_BUDGET - 1)),
            ("doubling-dag", _dag, "[" * 61 + "'leaf'], ['leaf']], "),
            ("cycle", _cyc_render, "[" * _DISPLAY_BUDGET)):
        # The doubling DAG and the cycle unfold without end, so each rendering runs in a bounded child.
        def _render_bounded():
            r = _bounded_display(_val)
            return len(r) == _cut_len and r.endswith(_DISPLAY_CUT) and r.startswith(_want)

        check("imported-bounded-display-bounded-" + _name, _in_child(_render_bounded) == "OK")
    check("imported-plain-display-wide-shared-str", _plain_display(_wide) == repr(_wide[:3])[:_DISPLAY_BUDGET]
          + _DISPLAY_CUT)
    _res = _imp_try(_imp_md(id=["x" * 100000] * 200), registered_vendors=REG)
    check("imported-record-wide-shared-id-bounded", _res is not None and _res.status == INVALID
          and _bounded_findings(_res, 2) and _res.findings[0].startswith("imported record [" + repr("x" * 100)[:-1]))
    # An int too large for the budget is named, unconverted, even with the int-to-str limit lifted; an object
    # that is not plain, a date-time with a foreign tzinfo included, is named by the fixed marker and never
    # rendered; a timezone name longer than the budget is never rendered.
    _old_digits = sys.get_int_max_str_digits()
    try:
        sys.set_int_max_str_digits(0)
        _big = _bounded_display(1 << (_DISPLAY_INT_BITS + 1))
        _big_nested = _bounded_display([1 << (_DISPLAY_INT_BITS + 1)])
    finally:
        sys.set_int_max_str_digits(_old_digits)
    check("imported-bounded-display-int-over-bits", _big == "<oversized-int: {} bits>".format(_DISPLAY_INT_BITS + 2)
          and _big_nested == "<oversized-value>")
    _ran = []

    class _ReprZoneRan(datetime.tzinfo):
        def utcoffset(self, dt):
            return datetime.timedelta(0)

        def __repr__(self):
            _ran.append("zone")
            raise RuntimeError("a foreign tzinfo ran its own __repr__")

    class _ReprListRan(list):
        def __repr__(self):
            _ran.append("list")
            raise RuntimeError("a list subclass ran its own __repr__")

    check("imported-bounded-display-not-plain-marked", _bounded_display(
        [_ReprRaisesStr("x"), datetime.datetime(2025, 1, 5, tzinfo=_ReprZoneRan()), {"k": _ReprListRan([1])}])
        == "[{0}, {0}, {{'k': {0}}}]".format(_NOT_PLAIN_MARK) and not _ran)
    _long_zone = datetime.datetime(
        2025, 1, 5, tzinfo=datetime.timezone(datetime.timedelta(hours=1), "Z" * (_DISPLAY_BUDGET + 1)))
    _zone_mark = "<a date-time whose timezone name exceeds {} characters>".format(_DISPLAY_BUDGET)
    check("imported-bounded-display-long-zone-name", _bounded_display(_long_zone) == _zone_mark
          and _bounded_display([_long_zone, 1]) == "[" + _zone_mark + ", 1]")
    # A str is sliced to the budget before its repr is taken, so rendering a huge one costs the budget, not
    # its length.
    import tracemalloc
    _huge = "x" * 10000000
    _was_tracing = tracemalloc.is_tracing()
    if not _was_tracing:
        tracemalloc.start()
    tracemalloc.reset_peak()
    _base = tracemalloc.get_traced_memory()[0]
    _bounded_display([_huge])
    _peak = tracemalloc.get_traced_memory()[1] - _base
    if not _was_tracing:
        tracemalloc.stop()
    check("imported-bounded-display-huge-str-cost", _peak < 1000000)
    del _huge
    # Every site that renders a record value in an imported-record finding renders it through
    # _bounded_display: a deep value or a 100000-character str there is cut at the budget, never raised on,
    # never rendered whole.
    _z = "z" * 100000
    _long = "'" + "z" * (_DISPLAY_BUDGET - 1) + _DISPLAY_CUT
    _dl, _dt = _deep_list, _deep_tuple
    _tkey = {_deep_key: "v"}
    for _name, _rec, _kw, _needle in (
            ("source-sha256", _imp_md(**dict([("import", _imp_table(source_sha256=_very_deep))])), {},
             "import.source_sha256 " + _dl),
            ("source-not-canonical", _imp_md(**dict([("import", _imp_table(source="/" + _z))])), {},
             "import.source '/" + "z" * (_DISPLAY_BUDGET - 2) + _DISPLAY_CUT + " is not a canonical"),
            ("source-elsewhere", _imp_md(**dict([("import", _imp_table(source=".working/" + _z))])), {},
             "import.source '.working/" + "z" * (_DISPLAY_BUDGET - 10) + _DISPLAY_CUT + " does not lie"),
            ("run", _imp_md(**dict([("import", _imp_table(run=_z))])), {}, "import.run " + _long),
            ("history-field", _imp_md(**dict([("import", _imp_table(history=[{"field": _very_deep, "value": "v"}]))])),
             {}, "import.history[0].field " + _dl),
            ("import-key", _imp_md(**dict([("import", dict(_imp_table(), **{}) | _tkey)])), {},
             "import unknown key " + _dt),
            ("unrecorded-reason", _imp_md(unrecorded=[{"field": "decided_at", "reason": _very_deep}]), {},
             "unrecorded[0].reason " + _dl),
            ("unrecorded-field", _imp_md(unrecorded=[{"field": _z, "reason": "unparsed"}]), {},
             "unrecorded[0] names " + _long),
            ("unrecorded-repeat", _imp_md(unrecorded=[{"field": _z, "reason": "unparsed"},
                                                      {"field": _z, "reason": "unparsed"}]), {},
             "unrecorded[1] repeats the field " + _long),
            ("unrecorded-vendor", _imp_md(unrecorded=[{"field": "x-" + _z, "reason": "unparsed"}]), {},
             "unrecorded[0] names the x-<vendor> extension '" + "x-" + "z" * (_DISPLAY_BUDGET - 3) + _DISPLAY_CUT),
            ("unrecorded-key", _imp_md(unrecorded=[dict({"field": "decided_at", "reason": "unparsed"}, **{}) | _tkey]),
             {}, "unrecorded[0] unknown key " + _dt),
            ("link-rel", _imp_md(links=[{"rel": _very_deep, "id": "imported:PP-2"}]), {}, "links[0].rel " + _dl),
            ("link-id", _imp_md(links=[{"rel": "exemplifies", "id": _very_deep}]), {}, "links[0].id " + _dl),
            ("link-key", _imp_md(links=[dict({"rel": "exemplifies", "id": "imported:PP-2"}) | _tkey]), {},
             "links[0] unknown key " + _dt),
            ("kind", _imp_md(kind=_very_deep), {}, "worklog kind " + _dl),
            ("scopes", _imp_md(scopes=[_very_deep]), {}, "block.scopes entry " + _dl),
            ("ref-kind", _imp_md(refs=[{"kind": _z, "locator": "x", "note": "n"}]), {}, "refs[0].kind " + _long),
            ("ref-key", _imp_md(refs=[dict({"kind": "path", "locator": "x", "note": "n"}) | _tkey]), {},
             "refs[0] unknown key(s): " + _dt),
            ("actor-kind", _imp_md(actor={"kind": _z, "id": "a"}), {}, "actor.kind " + _long),
            ("actor-key", _imp_md(actor=dict(_IMP_ACTOR) | _tkey), {}, "actor unknown key(s): " + _dt),
            ("status-state", _imp_md(status=_z), {}, "status: status state " + _long),
            ("status-slashes", _imp_md(status=_z + "/a/b"), {}, "status: status " + _long),
            ("status-qualifier", _imp_md(status="recorded/" + _z), {}, "status: status qualifier " + _long),
            ("type-mismatch", _imp_md(type=_z), {"expected_type": "maintainer_decision"}, "type " + _long),
            ("type-unsupported", _imp_md(type=_z), {}, _long + " is not a supported record type"),
            ("record-key", _imp_md(**{_z: None}), {}, "unknown key " + _long),
            ("toml-key", _imp_md(**{_z: None}), {}, "field " + _long + " holds a key or value no TOML"),
            ("vendor-not-registered", _imp_md(**{"x-" + _z: {}}), {},
             "extension 'x-" + "z" * (_DISPLAY_BUDGET - 3) + _DISPLAY_CUT + " is not registered"),
            ("vendor-malformed", _imp_md(**{"x-" + _z.upper(): {}}), {},
             "extension key 'x-" + "Z" * (_DISPLAY_BUDGET - 3) + _DISPLAY_CUT + " is not a valid"),
            ("vendor-not-table", _imp_md(**{"x-" + _z: 1}), {"registered_vendors": REG | {"x-" + _z}},
             "extension 'x-" + "z" * (_DISPLAY_BUDGET - 3) + _DISPLAY_CUT + " must be a table"),
            ("id", _imp_md(id=_z), {}, "imported record " + _long + ": id " + _long)):
        _res = _imp_try(_rec, **dict(dict(registered_vendors=REG), **_kw))
        check("imported-render-site-bounded-" + _name, _res is not None and _res.status == INVALID
              and any(_needle in f and _DISPLAY_CUT in f for f in _res.findings) and _bounded_findings(_res, 2))
    # A name a caller's `specs` roster or `registered_kinds` control supplies is the caller's own text, not a
    # fixed vocabulary: a 100000-character module field or state, roster key or registered kind is cut at
    # the budget, in a refusal and in a control refusal alike.
    def _cut(v):
        return repr(v)[:_DISPLAY_BUDGET] + _DISPLAY_CUT

    _zf = _module_roster(extra_keys=frozenset(["digest", _z]))
    _zs = _module_roster(initial=_z, terminal=frozenset([_z]), states=frozenset([_z]))
    for _name, _rec, _kw, _status, _needle in (
            ("module-field-claimed-absent", dict(_ar, **{_z: "v", "unrecorded": [{"field": _z, "reason": "unparsed"}]}),
             dict(specs=_zf), INVALID, "unrecorded[0] claims the supplied field " + _cut(_z) + " absent"),
            ("module-field-omitted", _ar, dict(specs=_zf), INVALID,
             "the omitted field " + _cut(_z) + " of the artifact schema has no unrecorded row"),
            ("module-state-proposed", dict(_ar, status=_z + "/proposed"), dict(specs=_zs), INVALID,
             "status: state " + _cut(_z) + " may not carry '/proposed' for a artifact"),
            ("module-states-listed", _ar, dict(specs=_zs), INVALID,
             "status: status state 'recorded' is not a artifact state " + _cut([_z])),
            ("module-reserved-field", _ar, dict(specs=_module_roster(extra_keys=frozenset(["digest", "x-" + _z]))),
             CANNOT_EVALUATE, "declares the field " + _cut("x-" + _z) + ", which the imported envelope reserves"),
            ("roster-key-not-typespec", _ar, dict(specs={_z: 1}), CANNOT_EVALUATE,
             "the type-spec roster entry " + _cut(_z) + " is not exactly a TypeSpec"),
            ("roster-key-missing-slot", _ar, dict(specs={_z: object.__new__(TypeSpec)}), CANNOT_EVALUATE,
             "the type-spec roster entry " + _cut(_z) + " has a malformed or missing name"),
            ("roster-key-untyped", _ar, dict(specs={_z: BASELINE_SPECS["backlog_item"]}), CANNOT_EVALUATE,
             "absent from the section-8.1 taxonomy: " + _cut(_z) + " (fail-closed)"),
            ("registered-kind", dict(_ar, kind="bogus"), dict(specs=_module_roster(), registered_kinds=[_z]),
             INVALID, "worklog kind 'bogus' is not one of " + _cut(sorted(set(WORKLOG_KINDS) | {_z})))):
        _res = _imp_try(_rec, **_kw)
        check("imported-module-name-bounded-" + _name, _res is not None and _res.status == _status
              and any(_needle in f for f in _res.findings) and _bounded_findings(_res, 2))
    # The clean validators keep their messages byte for byte: their renderer is _safe_display, the repr (whole
    # up to 65,536 characters, _opf_store._DISPLAY_FULL_MAX_CHARS, within its depth and value bounds).
    _bi_spec = BASELINE_SPECS["backlog_item"]
    check("clean-status-and-roster-messages-unchanged",
          parse_status("bogus", _bi_spec) == (
              None, "status state 'bogus' is not a backlog_item state ['active', 'done', 'dropped', 'open']")
          and parse_status("open/proposed", _bi_spec) == (
              None, "state 'open' may not carry '/proposed' for a backlog_item (spec 8.4/8.5)")
          and _specs_roster_error({"not_a_type": _bi_spec}) == (
              "the type-spec roster names a type absent from the section-8.1 taxonomy: 'not_a_type' (fail-closed)"))
    # A name over the display budget appears there uncut, as its repr, up to _safe_display's 65,536-character
    # limit (_opf_store._DISPLAY_FULL_MAX_CHARS; a longer repr is cut), so a switch to the bounded renderer
    # (which would cut it) shows.
    _over = "x" * (_DISPLAY_BUDGET + 808)
    check("clean-status-and-roster-long-name-uncut",
          parse_status(_over, _bi_spec) == (None, "status state " + repr(_over) + " is not a backlog_item state "
                                            "['active', 'done', 'dropped', 'open']")
          and _specs_roster_error({_over: _bi_spec}) == (
              "the type-spec roster names a type absent from the section-8.1 taxonomy: " + repr(_over)
              + " (fail-closed)"))
    # The same names reach the clean validators' findings uncut, up to the same 65,536-character limit,
    # through each call site whose message a finding carries (the gated-snapshot, type-specific and
    # proposed-from sites drop theirs), so a switch to the bounded renderer at those call sites shows too.
    _over_state = ("status state " + repr(_over) + " is not a backlog_item state "
                   "['active', 'done', 'dropped', 'open']")
    _over_roster = ("the type-spec roster names a type absent from the section-8.1 taxonomy: " + repr(_over)
                    + " (fail-closed)")
    check("clean-validators-long-name-uncut",
          ("status: " + _over_state) in validate_record(envelope("backlog_item", 1, _over)).findings
          and validate_record(envelope("backlog_item", 1, "open"), specs={_over: _bi_spec}).findings
          == [_over_roster]
          and ("from-status: " + _over_state) in validate_transition(
              "backlog_item", _over, "open", "maintainer").findings
          and ("to-status: " + _over_state) in validate_transition(
              "backlog_item", "open", _over, "maintainer").findings
          and validate_transition("backlog_item", "open", "active", "maintainer",
                                  specs={_over: _bi_spec}).findings == [_over_roster])
    # A plain operand that is not a str is refused and rendered, as the import-table docstring says.
    _found = []
    _validate_import_table({"import": {"run": 42, "source": None}}, _imported_schema_fields(_MD), _found)
    check("imported-import-table-plain-operands-rendered",
          any(f.startswith("import.source None is not ") for f in _found)
          and any(f.startswith("import.run 42 is not ") for f in _found))

    # A timezone is plain only when the two objects it retains are: an exact datetime.timezone whose name is
    # a str subclass, or whose offset is a timedelta subclass, is not plain, on a datetime or a time, as
    # the id (the normal label), beside another non-plain field (the refusal label), or as import.source.
    # Each subclass's own __repr__ would run if the date-time were rendered; it never does.
    _tz_ran = []

    class _TzName(str):
        def __repr__(self):
            _tz_ran.append("name")
            raise RuntimeError("a timezone name ran its own __repr__")

    class _TzOffset(datetime.timedelta):
        def __repr__(self):
            _tz_ran.append("offset")
            raise RuntimeError("a timezone offset ran its own __repr__")

    _tzwhy = "holds a date-time whose tzinfo is neither None nor exactly a datetime.timezone retaining"
    for _zname, _zone in (("name", datetime.timezone(datetime.timedelta(hours=1), _TzName("CET"))),
                          ("offset", datetime.timezone(_TzOffset(hours=1)))):
        for _kind, _val in (("datetime", datetime.datetime(2025, 1, 5, 8, tzinfo=_zone)),
                            ("time", datetime.time(8, tzinfo=_zone))):
            for _where, _rec, _prefix, _rid in (
                    ("id", _imp_md(id=_val), "imported record (unreadable id): field 'id' " + _tzwhy, None),
                    ("id-refusal-label", _imp_md(id=_val, title=_Str("x")), "imported record (unreadable id): ",
                     None),
                    ("source", _imp_md(**dict([("import", _imp_table(source=_val))])),
                     _md4 + "field 'import' " + _tzwhy, "imported:MD-4")):
                check("imported-record-tz-" + _zname + "-" + _kind + "-" + _where + "-cannot-eval", _unplain(
                    _imp_try(_rec, registered_vendors=REG), _prefix, _rid))
    check("imported-record-tz-subclass-repr-never-ran", not _tz_ran)
    # An exact timezone holding an exact name, and tomllib's own offset date-times, stay plain: each reaches
    # its field rule (a TOML date-time is not the RFC 3339 string the field takes).
    for _name, _val in (("named", datetime.datetime(2025, 1, 5, 8, tzinfo=datetime.timezone(
            datetime.timedelta(hours=1), "CET"))),
                        ("parsed-offset", tomllib.loads("t = 2025-01-05T08:00:00-07:00")["t"]),
                        ("parsed-utc", tomllib.loads("t = 2025-01-05T08:00:00Z")["t"])):
        check("imported-record-tz-" + _name + "-plain", _imp_refused(
            _imp_md(created_at=_val), "created_at must be an RFC 3339", registered_vendors=REG))

    # A foreign tzinfo on a time (not only on a datetime) is not plain, and its own __repr__ never runs.
    class _ReprZone(datetime.tzinfo):
        def utcoffset(self, dt):
            return datetime.timedelta(0)

        def __repr__(self):
            raise RuntimeError("a foreign tzinfo ran its own __repr__")

    check("imported-record-not-plain-foreign-tzinfo-time-cannot-eval", _unplain(_imp_try(
        _imp_md(**dict([("import", _imp_table(source=datetime.time(8, tzinfo=_ReprZone())))])),
        registered_vendors=REG), _md4 + "field 'import' " + _tzwhy, "imported:MD-4"))

    # A str-subclass key is never compared with `id`: one whose own __eq__ raises, and one whose __eq__
    # claims to be `id` (placed first, with a forged id), each leave the label on the record's real id.
    class _EqRaisesKey(str):
        __hash__ = str.__hash__

        def __eq__(self, other):
            raise RuntimeError("a record key ran its own __eq__")

    class _EqLiesKey(str):
        __hash__ = str.__hash__

        def __eq__(self, other):
            return True

    _eq_raises_rec = _imp_md()
    _eq_raises_rec[_EqRaisesKey("note")] = "x"
    _eq_lies_rec = dict([(_EqLiesKey("zz"), "imported:MD-99")] + list(_imp_md().items()))
    for _name, _rec in (("eq-raises", _eq_raises_rec), ("eq-lies", _eq_lies_rec)):
        check("imported-record-not-plain-key-" + _name + "-cannot-eval", _unplain(
            _imp_try(_rec, registered_vendors=REG), _md4 + "field <not a plain built-in value> holds a key",
            "imported:MD-4"))

    # Every admitted plain type is plain: each reaches the title rule (INVALID), never CANNOT-EVALUATE.
    for _name, _val in (("date", datetime.date(2025, 1, 5)), ("time", datetime.time(8, 0)),
                        ("set", set(["a"])), ("frozenset", frozenset(["a"])), ("none", None), ("tuple", ("a",)),
                        ("float", 1.5), ("bool", True), ("list", ["a"]), ("dict", {"a": "b"})):
        check("imported-record-plain-" + _name + "-reaches-field-rule", _imp_refused(
            _imp_md(title=_val), "title must be a non-empty single line", registered_vendors=REG))

    # A record holds only what a TOML document can: None, a tuple, a set, a frozenset or a non-str key in a
    # registered x-<vendor> table, at any depth, or in a module field that no value rule reads, is INVALID;
    # every TOML value type is VALID.
    _no_toml = "field 'x-aiqt' holds a key or value no TOML document can"
    for _name, _table in (("none", {"v": None}), ("tuple", {"v": ("t",)}), ("set", {"v": set(["a"])}),
                          ("frozenset", {"v": frozenset()}), ("int-key", {1: "a"}), ("none-key", {None: "a"}),
                          ("nested-none", {"a": [{"b": None}]}), ("nested-tuple-key", {"a": {("k",): "v"}})):
        check("imported-vendor-table-" + _name + "-refused", _imp_refused(
            _imp_md(**{"x-aiqt": _table}), _no_toml, registered_vendors=REG))
    for _name, _val in (("none", None), ("tuple", ("t",)), ("set", set(["a"])), ("frozenset", frozenset()),
                        ("int-key", {1: "a"}), ("nested-none", [None])):
        check("imported-module-field-" + _name + "-refused", _imp_refused(
            dict(_ar, digest=_val), "field 'digest' holds a key or value no TOML document can",
            specs=_module_roster()))
    check("imported-vendor-table-toml-values-ok", _imp_ok(_imp_md(**{"x-aiqt": tomllib.loads(
        'a = "s"\nb = 1\nc = 1.5\nd = true\ne = 2025-01-05T08:00:00Z\nf = 2025-01-05T08:00:00\ng = 2025-01-05\n'
        'h = 08:00:00\ni = [1, [2, "x"]]\nj = nan\nk = inf\n[[l]]\nm = {n = [{o = 1}]}\n')}), registered_vendors=REG))
    # A date-time in a form no TOML date-time takes is INVALID: an offset time, a nonzero fold, an offset that
    # is not a whole number of minutes, or a timezone with a name of its own. Every form tomllib yields, a
    # timezone named as its offset alone names it, and microseconds stay VALID.
    _utc = datetime.timezone.utc
    for _name, _val in (
            ("time-offset", datetime.time(8, tzinfo=_utc)),
            ("named-zone", datetime.datetime(2025, 1, 5, 8, tzinfo=datetime.timezone(datetime.timedelta(hours=1), "CET"))),
            ("named-utc-zone", datetime.datetime(2025, 1, 5, 8, tzinfo=datetime.timezone(datetime.timedelta(0), "Z"))),
            ("second-offset", datetime.datetime(2025, 1, 5, 8, tzinfo=datetime.timezone(datetime.timedelta(seconds=30)))),
            ("negative-second-offset", datetime.datetime(2025, 1, 5, 8, tzinfo=datetime.timezone(
                -datetime.timedelta(hours=7, seconds=30)))),
            ("microsecond-offset", datetime.datetime(2025, 1, 5, 8, tzinfo=datetime.timezone(
                datetime.timedelta(minutes=1, microseconds=1)))),
            ("fold-datetime", datetime.datetime(2025, 1, 5, 8, fold=1)),
            ("fold-time", datetime.time(8, fold=1)),
            ("nested-time-offset", [{"t": datetime.time(8, tzinfo=_utc)}])):
        check("imported-vendor-table-date-time-" + _name + "-refused", _imp_refused(
            _imp_md(**{"x-aiqt": {"v": _val}}), _no_toml, registered_vendors=REG))
    check("imported-vendor-table-toml-date-times-ok", _imp_ok(_imp_md(**{"x-aiqt": dict(tomllib.loads(
        'a = 2025-01-05T08:00:00Z\nb = 2025-01-05T08:00:00+00:00\nc = 2025-01-05T08:00:00-00:00\n'
        'd = 2025-01-05T08:00:00+23:59\ne = 2025-01-05T08:00:00-07:30\nf = 2025-01-05T08:00:00.123456789Z\n'
        'g = 2025-01-05T08:00:00.5\nh = 08:00:00.999999\ni = 0001-01-01\nj = 9999-12-31T23:59:59.999999-23:59\n'),
        k=datetime.datetime(2025, 1, 5, 8, tzinfo=datetime.timezone(datetime.timedelta(hours=1), "UTC+01:00")),
        l=datetime.datetime(2025, 1, 5, 8, 0, 0, 1, tzinfo=datetime.timezone(-datetime.timedelta(hours=7))))}),
        registered_vendors=REG))

    for _sha in (_IMP_SHA[:-1], _IMP_SHA + "0", "sha256:" + _IMP_SHA, _IMP_SHA[:-1] + "g", 0):
        check("imported-sha-refused-" + repr(_sha)[:24], _imp_refused(
            _imp_pd(**{"import": _imp_table(source_sha256=_sha)}), "import.source_sha256"))
    for _run in ("adopt-20260812T091402Z-0123456789abcdef", "imp-20260812T091402-0123456789abcdef",
                 "imp-20260812T091402Z-0123456789ABCDEF", "imp-20260812T091402Z-0123456789abcde",
                 _IMP_RUN + "\n", "../" + _IMP_RUN, None):
        check("imported-run-refused-" + repr(_run)[:28], _imp_refused(
            _imp_pd(**{"import": _imp_table(run=_run)}), "import.run"))
    import datetime as _dt
    for _at in ("2026-08-12T09:14:02+05:30", "2026-08-12 09:14:02Z", "2026-02-30T00:00:00Z",
                _dt.datetime(2026, 8, 12, tzinfo=_dt.timezone.utc)):
        check("imported-imported-at-refused-" + repr(_at)[:28], _imp_refused(
            _imp_pd(**{"import": _imp_table(imported_at=_at)}), "import.imported_at must be"))
    for _span in ([1], [1, 2, 3], [5, 2], [-1, 3], [True, 3], [0, 1.5], "0-3", (0, 3)):
        check("imported-span-refused-" + repr(_span), _imp_refused(
            _imp_pd(**{"import": _imp_table(span=_span)}), "import.span"))
    check("imported-span-empty-range-ok", _imp_ok(_imp_pd(**{"import": _imp_table(span=[4, 4])})))
    for _hist, _needle in (
            ({"field": "decided_at"}, "import.history must be an array"),
            (["decided_at"], "must be a {field, value} table"),
            ([{"field": "decided_at"}], "exactly the two keys"),
            ([{"field": "colour", "value": "teal"}], "names no field"),
            ([{"field": "decided_at", "value": 20250302}], "value must be the verbatim")):
        check("imported-history-refused-" + _needle, _imp_refused(
            _imp_pd(**{"import": _imp_table(history=_hist)}), _needle))
    for _unp in ("text", ["ok", 3], [["nested"]]):
        check("imported-unparsed-refused-" + repr(_unp), _imp_refused(
            _imp_pd(**{"import": _imp_table(unparsed=_unp)}), "import.unparsed"))
    check("imported-empty-history-unparsed-ok", _imp_ok(
        _imp_pd(**{"import": _imp_table(history=[], unparsed=[])})))

    # Missingness (spec 8.3): closed rows, closed reasons, schema fields only, never a supplied, required,
    # exempt or extension field; every closed reason is accepted.
    for _reason in IMPORTED_MISSINGNESS_REASONS:
        check("imported-reason-ok-" + _reason, _imp_ok(_imp_pd(unrecorded=[
            {"field": "updated_at", "reason": _reason}, _pd_rows[1]])))
    for _rows, _needle in (
            ({"field": "updated_at"}, "unrecorded must be an array"),
            (_pd_rows + ["updated_at"], "must be a {field, reason} table"),
            ([{"field": "updated_at", "reason": "lost"}, _pd_rows[1]], ".reason 'lost' is not one of"),
            ([{"field": "updated_at"}, _pd_rows[1]], ".reason None is not one of"),
            ([dict(_pd_rows[0], note="x"), _pd_rows[1]], "unknown key 'note'"),
            (_pd_rows + [{"field": 3, "reason": "unparsed"}], ".field must be a string"),
            (_pd_rows + [{"field": "colour", "reason": "unparsed"}], "no field of the pending_decision"),
            (_pd_rows + [{"field": "severity", "reason": "unparsed"}], "no field of the pending_decision"),
            (_pd_rows + [{"field": "title", "reason": "unparsed"}], "missingness cannot waive"),
            (_pd_rows + [{"field": "proposed_from", "reason": "not_applicable"}], "exempt optional"),
            (_pd_rows + [{"field": "links", "reason": "not_applicable"}], "exempt optional"),
            (_pd_rows + [{"field": "x-aiqt", "reason": "not_applicable"}], "x-<vendor> extension"),
            (_pd_rows + [{"field": "created_at", "reason": "ambiguous"}], "claims the supplied field")):
        check("imported-unrecorded-refused-" + _needle, _imp_refused(_imp_pd(unrecorded=_rows), _needle))

    # Historical timestamps and supplied values keep their declared types (spec 8.3).
    check("imported-decided-at-after-import-refused", _imp_refused(
        _imp_pd(decided_at="2026-09-01T00:00:00Z", unrecorded=_pd_rows[:1]), "decided_at is later than"))
    check("imported-decided-at-supplied-ok", _imp_ok(
        _imp_pd(decided_at="2025-03-02T09:00:00Z", unrecorded=_pd_rows[:1])))
    check("imported-date-only-timestamp-refused", _imp_refused(
        _imp_pd(decided_at="2025-03-02", unrecorded=_pd_rows[:1]), "decided_at must be an RFC 3339"))
    # A source whose updated_at precedes its created_at is kept as recorded: no ordering is imposed on
    # history (see the module docstring), though the clean validator still refuses the same pair.
    check("imported-updated-before-created-retained", _imp_ok(
        _imp_pd(updated_at="2025-02-01T00:00:00Z", unrecorded=_pd_rows[1:])))
    check("imported-blank-decision-refused", _imp_refused(_imp_pd(decision=" "), "decision must be a non-empty"))
    check("imported-summary-nonstring-refused", _imp_refused(_imp_md(summary=3), "summary must be a string",
                                                             registered_vendors=REG))
    check("imported-unregistered-vendor-refused", _imp_refused(_imp_md(), "not registered"))
    # Open-state history keeps its supplied decision text (no current bundle rule applies to history).
    check("imported-history-bundle-not-applied", _imp_ok(_imp_pd(status="open")))

    # Links (spec 8.6): imported-to-imported only, closed vocabulary and the relation type constraints.
    for _links, _needle in (
            ([{"rel": "relates", "id": "MD-1"}], "imported-to-clean link"),
            ([{"rel": "relates", "id": "imported:LF-1"}], "imported series refuses"),
            ([{"rel": "blesses", "id": "imported:MD-1"}], ".rel 'blesses' is not one of"),
            ([{"rel": "exemplifies", "id": "imported:MD-1"}], "exemplifies must target"),
            ([{"rel": "relates", "id": "imported:MD-1", "why": "x"}], "unknown key 'why'"),
            ([{"rel": "relates", "id": "imported:MD-01"}], "is not a well-formed id"),
            (["imported:MD-1"], "links[0] must be a {rel, id} table"),
            ([{"rel": "relates", "id": "imported:MD-1"}, 7], "links[1] must be a {rel, id} table"),
            ("imported:MD-1", "links must be an array")):
        check("imported-links-refused-" + _needle, _imp_refused(_imp_md(links=_links), _needle,
                                                                registered_vendors=REG))

    # Per-type exemptions (spec 8.3): an imported worklog row carries the full imported envelope and owes
    # a row for an omitted summary; a reference owes one for omitted refs; a standalone done is legal.
    _wl = {"id": "imported:WL-3", "type": "worklog", "status": "recorded", "title": "Legacy fix entry",
           "actor": dict(_IMP_ACTOR), "date": "2024-11-02T00:00:00Z", "kind": "fixed",
           "summary": "Fixed the stale view", "unrecorded": [{"field": "detail", "reason": "not_applicable"}],
           "import": _imp_table()}
    _wl_nosum = {k: v for k, v in _wl.items() if k != "summary"}
    check("imported-valid-worklog", _imp_ok(_wl, expected_type="worklog"))
    check("imported-worklog-summary-row-ok", _imp_ok(dict(_wl_nosum, unrecorded=_wl["unrecorded"] + [
        {"field": "summary", "reason": "not_recorded_in_source"}])))
    check("imported-worklog-summary-omitted-refused", _imp_refused(_wl_nosum, "'summary' of the worklog schema"))
    check("imported-worklog-proposed-refused", _imp_refused(dict(_wl, status="recorded/proposed"), "status:"))
    check("imported-worklog-created-at-unknown", _imp_refused(dict(_wl, created_at=TS), "unknown key 'created_at'"))
    check("imported-worklog-bad-kind-refused", _imp_refused(dict(_wl, kind="merged"), "worklog kind 'merged'"))
    check("imported-worklog-registered-kind-ok", _imp_ok(dict(_wl, kind="perf"), registered_kinds=["perf"]))
    check("imported-worklog-bad-kinds-control-refused", _imp_refused(dict(_wl, kind="p"), "registered worklog kinds",
                                                                     registered_kinds="perf"))
    check("imported-worklog-multiline-summary-refused", _imp_refused(dict(_wl, summary="a\nb"), "single line"))
    check("imported-worklog-detail-ok", _imp_ok(dict(_wl, detail="Root cause: a stale cache", unrecorded=[])))
    for _det in (7, ["a"], None):
        check("imported-worklog-detail-type-refused-" + type(_det).__name__, _imp_refused(
            dict(_wl, detail=_det, unrecorded=[]), "worklog detail must be a string"))
    # The kinds control is judged for every worklog entry, as _validate_worklog judges it, so a malformed
    # control is surfaced even when the entry omits `kind` under a missingness row.
    _wl_nokind = dict({k: v for k, v in _wl.items() if k != "kind"},
                      unrecorded=_wl["unrecorded"] + [dict(field="kind", reason="not_recorded_in_source")])
    check("imported-worklog-kind-omitted-ok", _imp_ok(_wl_nokind, registered_kinds=["perf"]))
    check("imported-worklog-kind-omitted-bad-kinds-control-refused", _imp_refused(
        _wl_nokind, "registered worklog kinds", registered_kinds="perf"))
    for _j, _ctl in enumerate((_pretend(list), [_LyingStr("perf")], ["perf", ""], frozenset(["perf"]), ["perf", 7])):
        check("imported-worklog-kinds-faked-refused-" + str(_j), _imp_refused(
            dict(_wl, kind="perf"), "registered worklog kinds", registered_kinds=_ctl))
    for _ctl in (_IterRaisesList(["perf"]), ("perf",)):
        check("imported-worklog-kinds-base-iterated-ok-" + type(_ctl).__name__, _imp_ok(
            dict(_wl, kind="perf"), registered_kinds=_ctl))
    # The kinds control is a worklog control: a non-worklog record is not judged by it, as validate_record.
    for _j, _ctl in enumerate(("perf", [""], _pretend(list))):
        check("imported-non-worklog-bad-kinds-control-ok-" + str(_j), _imp_ok(
            _imp_md(), registered_kinds=_ctl, registered_vendors=REG))
    _rf = {"id": "imported:RF-2", "type": "reference", "status": "recorded", "title": "Old design note",
           "created_at": TS, "updated_at": TS, "actor": dict(_IMP_ACTOR), "import": _imp_table()}
    check("imported-reference-refs-omitted-refused", _imp_refused(_rf, "'refs' of the reference schema"))
    check("imported-reference-refs-row-ok", _imp_ok(dict(_rf, unrecorded=[
        {"field": "refs", "reason": "not_recorded_in_source"}])))
    check("imported-reference-empty-refs-refused", _imp_refused(dict(_rf, refs=[]), "at least one ref"))
    _dn = {"id": "imported:DN-5", "type": "done", "status": "recorded", "title": "Shipped the importer",
           "created_at": TS, "updated_at": TS, "actor": dict(_IMP_ACTOR), "import": _imp_table()}
    check("imported-standalone-done-ok", _imp_ok(_dn))
    check("imported-done-receipt-ok", _imp_ok(dict(_dn, links=[{"rel": "receipt_of", "id": "imported:BI-5"}])))
    check("imported-done-receipt-target-refused", _imp_refused(
        dict(_dn, links=[{"rel": "receipt_of", "id": "imported:FN-5"}]), "must target a backlog_item"))
    check("imported-done-two-receipts-refused", _imp_refused(dict(_dn, links=[
        {"rel": "receipt_of", "id": "imported:BI-5"}, {"rel": "receipt_of", "id": "imported:BI-6"}]),
        "at most one receipt_of"))
    _cn = {"id": "imported:CN-1", "type": "contribution", "status": "sent", "title": "Upstream patch",
           "created_at": TS, "updated_at": TS, "actor": dict(_IMP_ACTOR), "recipient": "peer",
           "dedup_class": "patch", "content_digest": "abc",
           "delivery": {"channel": "mail", "ref": "msg-1", "sent_at": TS}, "import": _imp_table()}
    check("imported-valid-contribution", _imp_ok(_cn))
    check("imported-delivery-after-import-refused", _imp_refused(dict(_cn, delivery=dict(
        _cn["delivery"], sent_at="2026-08-13T00:00:00Z")), "delivery.sent_at is later than"))
    check("imported-delivery-unknown-key-refused", _imp_refused(dict(_cn, delivery=dict(
        _cn["delivery"], via="x")), "contribution.delivery unknown key 'via'"))
    check("imported-delivery-receipt-ok", _imp_ok(dict(_cn, delivery=dict(
        _cn["delivery"], receipt_ref="ack-1", receipted_at=TS))))
    for _k in ("sent_at", "receipted_at"):
        for _bad in ("yesterday", "2026-08-01", 20260801):
            check("imported-delivery-" + _k + "-format-refused-" + repr(_bad), _imp_refused(
                dict(_cn, delivery=dict(_cn["delivery"], **dict([(_k, _bad)]))),
                "contribution.delivery." + _k + " must be an RFC 3339"))
    for _k in ("channel", "ref", "receipt_ref"):
        for _bad in (" ", 7):
            check("imported-delivery-" + _k + "-refused-" + repr(_bad), _imp_refused(
                dict(_cn, delivery=dict(_cn["delivery"], **dict([(_k, _bad)]))),
                "contribution.delivery." + _k + " must be a non-empty string"))
    check("imported-delivery-omitted-row-owed", _imp_refused(
        {k: v for k, v in _cn.items() if k != "delivery"}, "'delivery' of the contribution schema"))
    _bl = {"id": "imported:BL-1", "type": "block", "status": "active", "title": "Hold the release",
           "created_at": TS, "updated_at": TS, "actor": dict(_IMP_ACTOR),
           "scopes": ["imported:BI-1", "BI-2"], "import": _imp_table()}
    check("imported-valid-block", _imp_ok(_bl))
    check("imported-roster-block-copy-ok", _imp_ok(_bl, specs=_base_roster("block")))
    check("imported-roster-baseline-block-gated-only-cannot-eval", _imp_ctrl(
        _bl, _base_roster("block", gated=frozenset()), "definition's gated"))
    for _sc in ([], ["imported:LF-1"], ["ZZ-1"], "BI-1", [7]):
        check("imported-block-scopes-refused-" + repr(_sc), _imp_refused(dict(_bl, scopes=_sc), "block.scopes"))

    # The missingness reasons equal the adopt import policy's (one vocabulary). The adopt layer is imported
    # here, lazily, so this core module takes no load-time edge on it.
    import _opf_adopt
    check("imported-reasons-match-adopt-policy",
          tuple(_opf_adopt.MISSINGNESS_REASONS) == IMPORTED_MISSINGNESS_REASONS)

    # Adversarial sweep: a non-string, non-table, unhashable or oversized value (or an oversized-int key) at
    # every record, import, row and actor position is a structured refusal, never a crash and never VALID.
    _huge = 10 ** 5000
    _adv = (None, True, _huge, {"k": [1]}, [[]])

    def _imp_adv(name, rec):
        try:
            res = validate_imported_record(rec, registered_vendors=REG)
        except Exception as exc:   # an uncontrolled exception is a fail-open crash: record it, never raise
            check("imported-adv-no-crash-" + name + "-" + type(exc).__name__, False)
            return
        check("imported-adv-refused-" + name, res.status in (INVALID, CANNOT_EVALUATE) and bool(res.findings))

    _adv_paths = [(k,) for k in _imp_pd()] + [("import", k) for k in _imp_pd()["import"]] + [
        ("unrecorded", 0, "field"), ("unrecorded", 0, "reason"), ("import", "history", 0, "field"),
        ("import", "history", 0, "value"), ("import", "unparsed", 0), ("import", "span", 0),
        ("actor", "kind"), ("actor", "id")]
    for _path in _adv_paths:
        for _j, _val in enumerate(_adv):
            _rec = _imp_pd()
            _node = _rec
            for _step in _path[:-1]:
                _node = _node[_step]
            _node[_path[-1]] = _val
            _imp_adv("/".join(str(s) for s in _path) + "#" + str(_j), _rec)
    for _where in ("record", "import", "row", "history", "actor"):
        _rec = _imp_pd()
        _target = {"record": _rec, "import": _rec["import"], "row": _rec["unrecorded"][0],
                   "history": _rec["import"]["history"][0], "actor": _rec["actor"]}[_where]
        _target[_huge] = 1
        _imp_adv("huge-key-" + _where, _rec)
    _imp_adv("huge-id", _imp_pd(id=_huge))
    _imp_adv("huge-link", _imp_md(links=[{"rel": _huge, "id": _huge}]))

    # The caller-timer vector. A caller arms ITIMER_REAL with an interval and installs a SIGALRM handler;
    # the timer must keep counting down through the whole self-test with its interval kept, and the handler
    # and the signal mask must stay as they were, never cancelled, restarted, delivered early or replaced.
    # The caller is a bounded child that runs this self-test in-process (that nested run skips this vector)
    # and returns KEPT only when every clause holds, else LOST and the failed clauses by name. The clauses:
    # handler (the caller's handler is still installed), mask (the signal mask is unchanged), fired (the
    # caller's handler never ran), calls (the call leg), interval (still 7), and the elapsed leg's floor and
    # ceiling (the countdown left is the armed one less the time elapsed since, within a 0.05-second
    # reading tolerance either way). The call leg: while spies replace the signal, setitimer and alarm
    # attributes of both the signal module and _signal, the run makes no call through any of them
    # (run_bounded makes those calls in its own child alone, which sets that child's timer and records
    # nothing here). Arming in the child replaces its own run_bounded timer; the parent-owned run_bounded
    # deadline still bounds it.
    # Coverage boundary: the call leg sees a call only when it looks one of those six attributes up while
    # the spies are in place. The signal module's signal function is a wrapper that looks _signal.signal up
    # at each call, so a call through it is seen however it was bound, an alias or a name imported before
    # the spies included. A call through any other of the six functions bound before the spies (an alias,
    # or a name imported from either module) is invisible to the call leg, and so is a change made by
    # another route (pthread_sigmask, ctypes, an extension module). The other clauses catch such a borrow
    # only by the trace it leaves: a handler or mask left changed, the caller's handler run, the interval
    # changed, or the countdown off by more than the tolerance. So a borrow through an alias that restores
    # the timer verbatim within about 0.05 seconds passes, and so does one that takes its own window off the
    # countdown it restores, of any length within the run_bounded deadline, while no SIGALRM reaches the
    # caller's handler in that window. The two residual vectors below assert that both pass: a check that
    # closes them turns them red, and this boundary is then restated. Each clause, and each of the six spies,
    # has a vector below that goes red without it: the every-spy vector requires each of the six calls it
    # makes by name, and each other clause has a borrow that fails it and no other clause.
    if not _SELF_TEST_NESTED:
        import _signal
        import contextlib
        import io
        import signal
        import time

        def _caller_timer_kept(workload):
            global _SELF_TEST_NESTED
            _SELF_TEST_NESTED = True
            fired = []

            def _caller_alarm(signum, frame):
                fired.append(signum)

            def _mask():
                return signal.pthread_sigmask(signal.SIG_BLOCK, ()) if hasattr(signal, "pthread_sigmask") else None

            calls = []
            real = {(module, name): getattr(module, name) for module in (signal, _signal)
                    for name in ("signal", "setitimer", "alarm") if hasattr(module, name)}

            def _spy(key):
                def call(*args, **kwargs):
                    calls.append(key[0].__name__ + "." + key[1])
                    return real[key](*args, **kwargs)
                return call

            signal.signal(signal.SIGALRM, _caller_alarm)
            mask = _mask()
            start = time.monotonic()
            signal.setitimer(signal.ITIMER_REAL, 3600, 7)
            armed = time.monotonic()
            for key in real:
                setattr(key[0], key[1], _spy(key))
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    code = workload()
            finally:
                for key, fn in real.items():
                    setattr(key[0], key[1], fn)
            before = time.monotonic()
            remaining, interval = signal.getitimer(signal.ITIMER_REAL)
            after = time.monotonic()
            clauses = (("handler", signal.getsignal(signal.SIGALRM) is _caller_alarm),
                       ("mask", _mask() == mask), ("fired", not fired), ("calls", not calls),
                       ("interval", interval == 7),
                       ("floor", 3600 - (after - start) - 0.05 <= remaining),
                       ("ceiling", remaining <= 3600 - (before - armed) + 0.05))
            failed = [name for name, held in clauses if not held]
            if not failed:
                return "KEPT"
            return "LOST code={} failed={} remaining={:.3f} interval={} fired={} calls={}".format(
                code, ",".join(failed), remaining, interval, len(fired), ",".join(sorted(set(calls))))

        def _timer_vector(workload, usr2=None):
            # usr2 (SIG_BLOCK or SIG_UNBLOCK) sets SIGUSR2's membership in the child's mask before the
            # caller samples it, so a mask vector starts from a stated mask, not the one the child inherits.
            def _caller():
                if usr2 is not None:
                    signal.pthread_sigmask(usr2, {signal.SIGUSR2})
                return _caller_timer_kept(workload)
            return _opf_emit.run_bounded(_caller, timeout_s=300, mem_bytes=2 * 1024 ** 3)

        def _lost_on(workload, failed, calls="", usr2=None):
            # True when one of up to three runs is LOST with exactly these failed clauses and these calls: a
            # host stall inside a borrow can cost the countdown more than the tolerance, which adds an
            # elapsed clause (the elapsed leg working as stated). The retries make a stall-caused failure
            # less likely, not impossible: a stall in all three runs still fails the vector.
            for _ in range(3):
                seen = _timer_vector(workload, usr2)
                if seen.startswith("LOST code=0 failed=" + failed + " ") and seen.endswith(" calls=" + calls):
                    return True
            return False

        check("self-test-keeps-caller-timer", _timer_vector(self_test) == "KEPT")

        def _module_borrow():
            prior = _signal.signal(_signal.SIGALRM, _signal.SIG_IGN)
            _signal.setitimer(_signal.ITIMER_REAL, *_signal.setitimer(_signal.ITIMER_REAL, 10))
            _signal.signal(_signal.SIGALRM, prior)
            return 0

        _seen = _timer_vector(_module_borrow)
        check("self-test-caller-timer-sees-_signal-borrow", _seen.startswith("LOST code=0 ")
              and _seen.endswith(" calls=_signal.setitimer,_signal.signal"))
        # The residual, recorded, not a detection: a borrow through aliases bound before the spies passes
        # every clause when it restores the timer verbatim at once, or when it restores the countdown less its
        # own window. Each vector takes up to three attempts, since a host stall inside the borrow can cost
        # the countdown more than the tolerance, which is the elapsed leg working as stated.
        _alias_handler, _alias_timer, _alias_wrapper = _signal.signal, _signal.setitimer, signal.signal

        def _alias_quick_restore():
            prior = _alias_handler(_signal.SIGALRM, _signal.SIG_IGN)
            _alias_timer(_signal.ITIMER_REAL, *_alias_timer(_signal.ITIMER_REAL, 10))
            _alias_handler(_signal.SIGALRM, prior)
            return 0

        def _alias_compensated_restore():
            began = time.monotonic()
            saved = _alias_timer(_signal.ITIMER_REAL, 10)
            time.sleep(0.2)
            _alias_timer(_signal.ITIMER_REAL, saved[0] - (time.monotonic() - began), saved[1])
            return 0

        for _name, _borrow in (("alias-quick-restore", _alias_quick_restore),
                               ("alias-compensated-restore", _alias_compensated_restore)):
            check("self-test-caller-timer-residual-unseen-" + _name,
                  any(_timer_vector(_borrow) == "KEPT" for _ in range(3)))

        def _alias_slow_restore():
            saved = _alias_timer(_signal.ITIMER_REAL, 10)
            time.sleep(0.3)
            _alias_timer(_signal.ITIMER_REAL, *saved)
            return 0

        check("self-test-caller-timer-elapsed-leg-sees-alias-slow-restore",
              _lost_on(_alias_slow_restore, "ceiling"))

        # A run through all six spied attributes, each required by name; a borrow through the signal.signal
        # wrapper bound before the spies, seen through _signal.signal; and a borrow for each clause the
        # vectors above leave unpinned, each failing that clause and no other. The mask borrow runs once from
        # SIGUSR2 unblocked and once from it blocked, so its verdict does not rest on the caller's mask.
        # What the two elapsed-leg borrows pin: the slow-restore borrow leaves the countdown over the
        # ceiling's reference by 0.3 seconds plus any stall between its two timer calls, and the shortening
        # borrow leaves it under the floor's by 0.3 seconds plus any stall between its began reading and its
        # first timer call, each less the short gaps between adjacent clock and timer readings in the run. A
        # tolerance widened to T turns a vector red exactly when that offset is at most T in all three of its
        # runs: a tolerance at or below about 0.3 seconds is not pinned at all (0.25 passes), and one above
        # that is pinned only when no run stalls its borrow by more than about T less 0.3 seconds.
        def _every_spied_call():
            saved = _alias_timer(_signal.ITIMER_REAL, 10)
            signal.alarm(0)
            _signal.alarm(0)
            _signal.setitimer(_signal.ITIMER_REAL, *signal.setitimer(_signal.ITIMER_REAL, 10))
            _alias_timer(_signal.ITIMER_REAL, *saved)
            _signal.signal(_signal.SIGALRM, signal.signal(_signal.SIGALRM, _signal.SIG_IGN))
            return 0

        def _alias_wrapper_swap():
            _alias_wrapper(signal.SIGALRM, _alias_wrapper(signal.SIGALRM, signal.SIG_IGN))
            return 0

        def _alias_handler_left():
            _alias_handler(_signal.SIGALRM, _signal.SIG_IGN)
            return 0

        def _mask_left(start):
            # Toggles SIGUSR2's membership in the mask it finds, so it leaves the mask changed whatever the
            # mask was (a fork starts with none pending, so the unblock has none to deliver). Its code is 0
            # only when it finds the start its vector set, so each vector runs the branch it names.
            def _borrow():
                held = signal.SIGUSR2 in signal.pthread_sigmask(signal.SIG_BLOCK, ())
                signal.pthread_sigmask(signal.SIG_UNBLOCK if held else signal.SIG_BLOCK, {signal.SIGUSR2})
                return 0 if held == (start == signal.SIG_BLOCK) else 1
            return _borrow

        def _alias_fires_caller_handler():
            began = time.monotonic()
            saved = _alias_timer(_signal.ITIMER_REAL, 0.001)
            time.sleep(0.1)
            _alias_timer(_signal.ITIMER_REAL, saved[0] - (time.monotonic() - began), saved[1])
            return 0

        def _alias_interval_changed():
            began = time.monotonic()
            saved = _alias_timer(_signal.ITIMER_REAL, 10)
            _alias_timer(_signal.ITIMER_REAL, saved[0] - (time.monotonic() - began), 5)
            return 0

        def _alias_shortened():
            began = time.monotonic()
            saved = _alias_timer(_signal.ITIMER_REAL, 10)
            _alias_timer(_signal.ITIMER_REAL, saved[0] - (time.monotonic() - began) - 0.3, saved[1])
            return 0

        _all_six = "_signal.alarm,_signal.setitimer,_signal.signal,signal.alarm,signal.setitimer,signal.signal"
        for _name, _borrow, _failed, _calls in (
                ("every-spied-call", _every_spied_call, "calls", _all_six),
                ("prebound-signal-wrapper", _alias_wrapper_swap, "calls", "_signal.signal"),
                ("alias-handler-left", _alias_handler_left, "handler", ""),
                ("alias-fires-caller-handler", _alias_fires_caller_handler, "fired", ""),
                ("alias-interval-changed", _alias_interval_changed, "interval", ""),
                ("alias-shortened", _alias_shortened, "floor", "")):
            check("self-test-caller-timer-" + _failed + "-clause-sees-" + _name,
                  _lost_on(_borrow, _failed, _calls))
        for _name, _start in (("mask-left", signal.SIG_UNBLOCK),
                              ("mask-left-sigusr2-blocked", signal.SIG_BLOCK)):
            check("self-test-caller-timer-mask-clause-sees-" + _name,
                  _lost_on(_mask_left(_start), "mask", "", _start))

    # ----- Deep values: a finding message renders a parsed value through the bounded
    # _safe_display. tomllib builds a dotted table header iteratively, so a 100,000-part header parses into a
    # 100,000-level nested table whose plain repr() overflowed the stack pre-fix (RecursionError, "Stack
    # overflow ... while getting the repr of an object", escaping validate_record). CPython 3.14.8 and later
    # refuse a TOML key of more than 1,000 dotted parts, so the deep vectors build that table directly and
    # iteratively, in the shape tomllib gives the header, and run on every supported interpreter: no vector
    # parses a longer key. Each deep check also compares the direct build with tomllib's own parse of the
    # same header at 999 parts, under that cap. The deep vectors run in a bounded child (run_bounded), so a
    # regression is the ERROR:RecursionError sentinel rather than a crash of this suite. Each refusal still
    # stands: the record is INVALID with its finding. The wide vector rendered a megabyte-scale finding
    # pre-fix; the small vector pins the byte-identical render. -----
    import tomllib
    from _opf_emit import run_bounded

    def _dr_nest(parts):
        # What tomllib gives for a table header [<name>.a.a ... .a] of `parts` a-parts holding b = 1.
        nest = {"b": 1}
        for _ in range(parts):
            nest = {"a": nest}
        return nest

    _dr_id_shape = tomllib.loads("[id" + ".a" * 998 + "]\nb = 1\n")["id"] == _dr_nest(998)
    _dr_link_shape = (tomllib.loads("[[links]]\nrel = 'relates'\n[links.id" + ".a" * 997 + "]\nb = 1\n")["links"]
                      == [{"rel": "relates", "id": _dr_nest(997)}])

    def _dr_outcome(rec, prefix):
        res = validate_record(rec)
        hit = [f for f in res.findings if f.startswith(prefix) and "is not a well-formed" in f]
        return "{}|{}|{}".format(res.status, len(hit) == 1 and "nested deeper than" in hit[0],
                                 all(len(f) <= 2048 for f in res.findings))

    def _dr_deep_id():
        rec = envelope("backlog_item", 90, "open")
        rec["id"] = _dr_nest(100000)
        return _dr_outcome(rec, "id ")

    def _dr_deep_link():
        rec = envelope("backlog_item", 91, "open")
        rec["links"] = [{"rel": "relates", "id": _dr_nest(100000)}]
        return _dr_outcome(rec, "links[0].id ")

    check("deeprender-deep-id-refused-as-finding",
          _dr_id_shape and run_bounded(_dr_deep_id, timeout_s=60) == "INVALID|True|True")
    check("deeprender-deep-link-id-refused-as-finding",
          _dr_link_shape and run_bounded(_dr_deep_link, timeout_s=60) == "INVALID|True|True")
    _dr_wide = envelope("backlog_item", 92, "open")
    _dr_wide["id"] = tomllib.loads("id = [" + "1, " * 200000 + "]\n")["id"]
    _dr_wide_res = validate_record(_dr_wide)
    check("deeprender-wide-id-finding-bounded",
          _dr_wide_res.status == INVALID and all(len(f) <= 2048 for f in _dr_wide_res.findings)
          and any(f.startswith("id [1, 1, ") and "...<truncated>" in f for f in _dr_wide_res.findings))
    _dr_small = envelope("backlog_item", 93, "open")
    _dr_small["id"] = tomllib.loads("id = [1, 'x', {a = 2}]\n")["id"]
    check("deeprender-small-id-byte-identical",
          "id {!r} is not a well-formed <NS>-<n> id (spec 8.2)".format([1, "x", {"a": 2}])
          in validate_record(_dr_small).findings)

    if failures:
        print("OPF-SCHEMA SELF-TEST: FAIL ({} of {} checks failed)".format(len(failures), checked))
        for f in failures:
            print("  FAILED: {}".format(f))
        return 1
    print("OPF-SCHEMA SELF-TEST: PASS ({} envelope, type-schema, transition, and counters checks)".format(
        checked))
    return 0


if __name__ == "__main__":
    if sys.argv[1:] == ["--self-test"]:
        sys.exit(self_test())
    sys.exit(self_test())
